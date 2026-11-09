"""Verify the owned execution behind one designed-Jobs Run.

Reads only the isolated owned root: the execution document carries the original
receipt, the cleanup proof and the supervisor identity. Every process check is
about the identities recorded by that document, never a fresh PID guess.
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def alive(pid):
    """Liveness of one recorded PID; stdout decoding is locale-independent."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if sys.platform == "win32":
        import ctypes
        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--expect", choices=("cancelled", "completed"), required=True)
    parser.add_argument("--require", action="append", default=[],
                        help="text that must appear in the original log")
    parser.add_argument("--forbid", action="append", default=[],
                        help="text that must not appear in the original log")
    args = parser.parse_args()

    store = args.root / "canonical" / "executions" / "executions.sqlite3"
    if not store.is_file():
        raise SystemExit(f"execution store missing: {store}")
    with sqlite3.connect(store) as db:
        documents = [json.loads(row[0]) for row in db.execute("SELECT document FROM executions")]
    matches = [doc for doc in documents
               if (doc.get("attempt") or {}).get("runId") == args.run
               or (doc.get("effectKey") or {}).get("runId") == args.run]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one execution for {args.run}, found {len(matches)}")
    doc = matches[0]
    result = doc.get("result")
    if not isinstance(result, dict):
        raise SystemExit("execution has no receipt yet")
    proof = result.get("cleanupProof") or {}
    tree = proof.get("ownedTree") or {}
    receipt = result.get("receipt") or {}
    if not tree.get("confirmed") or tree.get("remaining"):
        raise SystemExit(f"owned tree not confirmed: {json.dumps(tree)[:400]}")
    if args.expect == "cancelled":
        if result.get("outcome") == "succeeded":
            raise SystemExit("cancelled Run still reports a successful effect")
        if receipt.get("effectStarted") is True and result.get("outcome") == "succeeded":
            raise SystemExit("cancelled Run completed its effect")
    else:
        if result.get("outcome") != "succeeded":
            raise SystemExit(f"completed Run has no successful receipt: {result.get('outcome')}")
    from packages.core.platform.execution_host import supervisor_exit_evidence
    supervisor = doc.get("supervisorIdentity") or {}
    supervisor_proof = supervisor_exit_evidence(supervisor) if supervisor else {"confirmed": True}
    if supervisor and not supervisor_proof.get("confirmed"):
        raise SystemExit(f"original supervisor exit is not proven: {json.dumps(supervisor_proof)[:300]}")
    pids = {"supervisor": supervisor.get("pid"), "effect": (receipt.get("processIdentity") or {}).get("pid")}
    for pid in tree.get("owned") or []:
        pids[f"owned-{pid}"] = pid
    living = {name: pid for name, pid in pids.items() if alive(pid)}
    if living:
        raise SystemExit(f"owned process tree still alive: {living}")
    text = None
    if args.require or args.forbid:
        from packages.jobs.independent_executor import ExecutionStore
        store_object = ExecutionStore(args.root / "canonical" / "executions")
        page = store_object.read_execution_log(doc["effectId"], doc["attempt"], offset=0, limit=65536)
        text = page.get("text") or ""
        missing = [marker for marker in args.require if marker not in text]
        if missing:
            raise SystemExit(f"required log markers missing: {missing}")
        present = [marker for marker in args.forbid if marker in text]
        if present:
            raise SystemExit(f"forbidden log markers present: {present}")
    print(json.dumps({
        "runId": args.run,
        "outcome": result.get("outcome"),
        "receiptKeys": sorted(receipt),
        "receiptCode": receipt.get("code"),
        "exitCode": receipt.get("exitCode"),
        "cleanupTree": tree,
        "supervisorExit": supervisor_proof,
        "pids": pids,
        "logText": None if text is None else text[:4000],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
