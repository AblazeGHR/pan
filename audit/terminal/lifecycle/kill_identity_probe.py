"""Targeted tests for the single-handle atomic verify-then-terminate path.

The reworked ``probe_lib.kill_verified`` opens the PID once with
``PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE`` and performs the
creation-time check, the exit-code check and ``TerminateProcess`` on that same
handle.  This probe injects verification failures and proves no termination
happens:

  K1 wrong_create_time_rejected   a mismatching 100ns FILETIME is rejected and
                                  the live process is untouched
  K2 exited_handle_held_rejected  a terminated process whose PID is still
                                  addressable (parent holds the Popen handle)
                                  is reported not-running and not terminated
  K3 correct_identity_terminated  the same single-handle path terminates the
                                  verified process

Usage: <venv-python> kill_identity_probe.py [--out evidence/followup/kill_identity_probe.json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probe_lib as lib  # noqa: E402

PYTHON, PY_ENV = lib.probe_python()
results: list[dict] = []


def record(name: str, passed: bool, facts: dict) -> None:
    results.append({"name": name, "pass": bool(passed), "facts": facts})
    print(f"[{'PASS' if passed else 'FAIL'}] {name} :: "
          f"{json.dumps(facts, ensure_ascii=False)[:400]}", flush=True)


def spawn_sleeper(code: str = "import time; time.sleep(120)") -> subprocess.Popen:
    return subprocess.Popen(
        [PYTHON, "-c", code], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=PY_ENV)


def cleanup_procs(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        info = lib.identity(proc.pid)
        if info and lib.process_running(proc.pid):
            lib.kill_verified(proc.pid, info["createTimeFiletime"])
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


def case_wrong_create_time() -> None:
    proc = spawn_sleeper()
    facts: dict = {}
    try:
        time.sleep(0.3)
        info = lib.identity(proc.pid)
        wrong = (info["createTimeFiletime"] + 1) if info else None
        facts["pid"] = proc.pid
        facts["actualCreateTimeFiletime"] = info["createTimeFiletime"] if info else None
        facts["requestedCreateTimeFiletime"] = wrong
        detail = lib.kill_verified_detail(proc.pid, wrong)
        facts["detail"] = detail
        alive_after = lib.process_running(proc.pid)
        facts["stillRunningAfterRejectedKill"] = alive_after
        passed = (detail["opened"] and detail["filetimeMatch"] is False
                  and detail["terminated"] is False and alive_after)
    finally:
        cleanup_procs([proc])
    record("K1_wrong_create_time_rejected", passed, facts)


def case_exited_handle_held() -> None:
    """Child exits while the parent keeps the Popen handle: the PID stays
    addressable (OpenProcess succeeds) even though the process is terminated."""
    proc = spawn_sleeper("import time; time.sleep(0.6)")
    facts: dict = {}
    try:
        info = lib.identity(proc.pid)
        time.sleep(1.4)  # child has exited; parent deliberately does not wait()/poll()
        facts["pid"] = proc.pid
        facts["popenHandleStillOpen"] = proc.returncode is None
        facts["openProcessSucceedsWhileExited"] = lib.process_create_time_raw(proc.pid) is not None
        facts["observedExitCode"] = lib.process_exit_code(proc.pid)
        facts["processRunning"] = lib.process_running(proc.pid)
        detail = lib.kill_verified_detail(proc.pid, info["createTimeFiletime"])
        facts["detail"] = detail
        passed = (facts["openProcessSucceedsWhileExited"]
                  and facts["observedExitCode"] not in (None, lib.STILL_ACTIVE)
                  and detail["opened"] and detail["filetimeMatch"] is True
                  and detail["running"] is False and detail["terminated"] is False)
    finally:
        cleanup_procs([proc])
    record("K2_exited_handle_held_rejected", passed, facts)


def case_correct_identity() -> None:
    proc = spawn_sleeper()
    facts: dict = {}
    try:
        time.sleep(0.3)
        info = lib.identity(proc.pid)
        facts["pid"] = proc.pid
        facts["createTimeFiletime"] = info["createTimeFiletime"]
        detail = lib.kill_verified_detail(proc.pid, info["createTimeFiletime"])
        facts["detail"] = detail
        dead, waited = lib.wait_until(lambda: not lib.process_running(proc.pid), timeout=5)
        facts["deadAfterTerminate"] = dead
        facts["deadAfterSeconds"] = round(waited, 3)
        passed = detail["terminated"] and dead
    finally:
        cleanup_procs([proc])
    record("K3_correct_identity_terminated", passed, facts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out",
                        default=str(HERE / "evidence" / "followup" / "kill_identity_probe.json"))
    args = parser.parse_args()
    TEMP = Path(tempfile.gettempdir()) / f"pan-term-kill-{os.getpid()}"
    TEMP.mkdir(parents=True, exist_ok=True)
    case_wrong_create_time()
    case_exited_handle_held()
    case_correct_identity()
    report = {
        "probe": "kill_identity_probe",
        "python": sys.version,
        "startedAt": time.time(),
        "mechanism": {
            "openAccess": "PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE (single OpenProcess)",
            "checks": ["GetProcessTimes FILETIME equality", "GetExitCodeProcess == STILL_ACTIVE"],
            "terminate": "TerminateProcess on the same handle",
            "toctou": ("the handle pins one process object, so PID reuse after the open "
                       "cannot redirect the terminate; a reused PID opened later would "
                       "present a different FILETIME and be rejected"),
        },
        "docs": [
            "https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess",
            "https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes",
        ],
        "results": results,
        "passed": all(item["pass"] for item in results),
    }
    lib.write_json(args.out, report)
    shutil.rmtree(TEMP, ignore_errors=True)
    print(json.dumps({"passed": report["passed"],
                      "results": [r["name"] for r in results]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
