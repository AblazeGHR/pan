"""Cross-process Job-handle ownership handoff probe (A -> B via DuplicateHandle).

Answers the rework question: can a Job Object's *handle ownership* move between
processes while the guarded tree keeps the same PIDs — and does the tree die
only when the last holder closes its handle?

Sequence (all processes probe-owned):

  1. P creates job K (KILL_ON_JOB_CLOSE), spawns tree child C, assigns C, then
     C spawns grandchild G (inherits K).  P verifies C and G are members.
  2. P duplicates its K handle into holder A (DuplicateHandle), then **closes
     its own handle**.  The tree must survive.
  3. A duplicates its handle into independent holder B (A -> B, cross-process)
     and B verifies membership through its own duplicated handle.
  4. A closes its handle and exits (original holder gone).  B still holds; the
     tree must survive with identical PIDs / creation times.
  5. B closes its handle while B stays alive (last owner disappears).  The
     whole tree must die — and it must be the handle close, not B's exit.

Documentation used:
  https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
  https://learn.microsoft.com/en-us/windows/win32/api/handleapi/nf-handleapi-duplicatehandle

Usage: <venv-python> job_handle_handoff.py [--out evidence/followup/job_handle_handoff.json]
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probe_lib as lib  # noqa: E402

PYTHON, PY_ENV = lib.probe_python()
ROLE = HERE / "handoff_role.py"
checks: list[dict] = []


def check(name: str, ok: bool, detail=None) -> bool:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: "
          f"{json.dumps(detail, ensure_ascii=False) if detail is not None else ''}",
          flush=True)
    return bool(ok)


class LineProc:
    """Child process speaking the line protocol on stdin/stdout/stderr."""

    def __init__(self, role: str):
        self.role = role
        self.proc = subprocess.Popen(
            [PYTHON, str(ROLE), "--role", role],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, env=PY_ENV)
        self.lines: list[str] = []
        self.queue: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        assert self.proc.stdout
        for raw in self.proc.stdout:
            line = raw.strip()
            self.lines.append(line)
            self.queue.put(line)

    def send(self, text: str) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def expect(self, prefix: str, timeout: float = 10.0) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = self.queue.get(timeout=remaining)
            except queue.Empty:
                return None
            if line.startswith(prefix):
                return line

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass


def identity_snapshot(pids: list[int]) -> dict:
    return {str(pid): (lib.identity(pid) or {}) for pid in pids}


def expected_map(*infos: dict | None) -> dict:
    """pid-keyed expectation for alive_same()."""
    return {str(info["pid"]): info for info in infos if info}


def alive_same(pids: list[int], expected: dict) -> bool:
    for pid in pids:
        raw = expected.get(str(pid), {}).get("createTimeFiletime")
        if not lib.identity_matches(pid, raw):
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "evidence" / "followup" /
                                             "job_handle_handoff.json"))
    args = parser.parse_args()
    report: dict = {
        "probe": "job_handle_handoff",
        "python": sys.version,
        "startedAt": time.time(),
        "docs": [
            "https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects",
            "https://learn.microsoft.com/en-us/windows/win32/api/handleapi/nf-handleapi-duplicatehandle",
        ],
    }
    a_proc = b_proc = tree_proc = None
    job = None
    try:
        # 1. create K, tree child C, grandchild G, membership verification
        job = lib.Job.create(kill_on_close=True)
        tree_proc = LineProc("tree")
        c_pid = tree_proc.proc.pid
        c_assign_ok, c_assign_err = job.assign(c_pid)
        tree_proc.send("GO")
        line = tree_proc.expect("G ") or ""
        g_pid = int(line.split()[1]) if len(line.split()) == 2 else None
        time.sleep(0.3)
        c_info = lib.identity(c_pid)
        g_info = lib.identity(g_pid) if g_pid else None
        expected = expected_map(c_info, g_info)
        check("H0 tree assigned to guard job", c_assign_ok, c_assign_err)
        check("H0 grandchild inherited the job",
              bool(g_pid) and job.is_member(g_pid) is True and g_info is not None,
              {"cMember": job.is_member(c_pid), "gPid": g_pid})
        report["tree"] = {"c": c_info, "g": g_info}

        # 2. duplicate the job handle into A (cross-process), then P drops its own
        a_proc = LineProc("a")
        a_target = lib.open_process(
            a_proc.proc.pid,
            lib.PROCESS_DUP_HANDLE | lib.PROCESS_QUERY_LIMITED_INFORMATION)
        ok, h_ka, err = lib.duplicate_handle(
            lib.current_process_handle(), job.handle, a_target)
        if a_target:
            lib.close_handle(a_target)
        a_proc.send(f"HANDLE {h_ka}")
        ack = a_proc.expect("A_HOLDS")
        check("H1 duplicate job handle into holder A", bool(ok) and ack == "A_HOLDS",
              {"ok": ok, "err": err, "ack": ack})
        report["hKA"] = h_ka
        job.close()  # P drops its own handle; A still holds
        report["pClosedOwnHandle"] = True
        ok, waited = lib.wait_until(
            lambda: not alive_same([c_pid, g_pid], expected), timeout=2.0)
        check("H2 tree survives after original creator closes its handle",
              not ok,  # `ok` means "death observed" — must be False
              {"survivedSeconds": round(waited, 3)})

        # 3. A -> B cross-process handoff; B introspects through its own handle
        b_proc = LineProc("b")
        a_proc.send(f"HANDOFF {b_proc.proc.pid}")
        line = a_proc.expect("HANDOFF_OK ") or ""
        h_kb = int(line.split()[1]) if len(line.split()) == 2 else None
        check("H3 A duplicated its handle into independent holder B",
              h_kb is not None, {"hKB": h_kb})
        report["hKB"] = h_kb
        b_proc.send(f"VALIDATE {h_kb} {c_pid}")
        line = b_proc.expect("B_HOLDS ") or ""
        report["bIntrospection"] = line
        check("H4 B verifies membership through the duplicated handle",
              "member=True" in line and "active=" in line, line)

        # 4. original holder A closes and exits; B remains sole holder
        a_proc.send("CLOSE")
        a_closed = a_proc.expect("A_CLOSED")
        ok, waited = lib.wait_until(lambda: not a_proc.alive(), timeout=5.0)
        check("H5 original holder A closed its handle and exited",
              a_closed == "A_CLOSED" and ok, {"waited": round(waited, 3)})
        tree_alive = alive_same([c_pid, g_pid], expected)
        check("H6 tree keeps same PIDs/creation times with B as sole holder",
              tree_alive and b_proc.alive(),
              {"cPid": c_pid, "gPid": g_pid, "bAlive": b_proc.alive()})
        report["identityAfterAExit"] = identity_snapshot([c_pid, g_pid])

        # 5. B closes the last handle while staying alive
        b_proc.send("CLOSE")
        b_closed = b_proc.expect("B_CLOSED")
        tree_dead, waited = lib.wait_until(
            lambda: (not lib.process_running(c_pid)) and (not lib.process_running(g_pid)),
            timeout=5.0)
        check("H7 B closed the last handle while still alive", b_closed == "B_CLOSED")
        check("H8 whole tree dies exactly when the last owner disappears",
              tree_dead, {"afterSeconds": round(waited, 3)})
        check("H9 tree death came from the handle close, not from B exiting",
              tree_dead and b_proc.alive(), {"bAliveAfterTreeDeath": b_proc.alive()})
        report["treeDeathAfterSeconds"] = round(waited, 3)
        b_proc.send("EXIT")
        lib.wait_until(lambda: not b_proc.alive(), timeout=5.0)
        return 0
    finally:
        # roles are killed through their Popen handles (pinned process objects);
        # the tree pids are swept with identity-verified termination.
        for proc in (a_proc, b_proc, tree_proc):
            if proc is not None:
                proc.stop()
        if job is not None:
            job.close()
        recorded: list[int] = []
        tree = report.get("tree") or {}
        for key in ("c", "g"):
            pid = (tree.get(key) or {}).get("pid")
            if pid:
                recorded.append(pid)
        for pid in recorded:
            if lib.process_running(pid):
                info = lib.identity(pid)
                if info:
                    lib.kill_verified(pid, info["createTimeFiletime"])
        time.sleep(0.5)
        leftovers = [pid for pid in recorded if lib.process_running(pid)]
        report["leftovers"] = leftovers
        check("H10 cleanup left no owned process alive", not leftovers, leftovers)
        report["results"] = checks
        report["passed"] = all(item["ok"] for item in checks)
        lib.write_json(args.out, report)
        print(json.dumps({"passed": report["passed"],
                          "results": [c["name"] for c in checks]}, indent=2))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
