"""Scenario driver for the ConPTY atomic-spawn spike.

Scenarios (all create only their own processes, all bounded by timeouts):

  s1_atomic_suspended      suspended child executes nothing, is guard-assigned
                           before it can run, then resumes, round-trips UTF-8
                           through the pty and reports its exit code.
  s2_resize                ResizePseudoConsole reaches the hosted application.
  s3_assign_failure        injected assignment failure is fail-closed: never
                           resumed, own child only is cleaned up, nothing published.
  s4_holder_hardkill       the guard-holding runner is hard-killed; the whole
                           tree (child + grandchild) must disappear via kernel
                           kill-on-close, with no cleanup code running.
  s5_reader_cancel         a reader blocked in ReadFile converges on cancel and
                           on output-handle close, within a bound.
  s6_ambient_breakaway     characterises this machine's ambient job and the
                           CREATE_BREAKAWAY_FROM_JOB behaviour.

Usage:
    E:/software/miniforge/python.exe audit/terminal/codex/spawn/spawn_driver.py --scenario all
    ... --scenario s1_atomic_suspended --keep
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spawn_win as sw  # noqa: E402

CHILD_PROBE = os.path.join(HERE, "child_probe.py")
HOLDER = os.path.join(HERE, "holder.py")
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def scenario_dir(name: str, keep: bool) -> str:
    root = tempfile.mkdtemp(prefix=f"conpty-spawn-{name}-")
    return root


def cleanup_root(root: str, keep: bool) -> bool:
    if keep:
        return False
    shutil.rmtree(root, ignore_errors=True)
    return not os.path.exists(root)


def child_cmd(marker: str, extra: str = "") -> str:
    return f'"{sys.executable}" "{CHILD_PROBE}" --marker "{marker}" {extra}'


def survivor_report(pids: list[tuple[int, int]]) -> list[dict]:
    """Which of our tracked (pid, filetime) identities are still alive?"""
    out = []
    for pid, ft in pids:
        info = sw.retrieve_identity_by_pid(pid)
        if info.get("open_ok") and info.get("still_active"):
            out.append({"pid": pid, "creation_filetime": ft, "still_active": True})
    return out


def sweep_own(pids: list[tuple[int, int]], log: list[str]) -> list[dict]:
    """Fail-safe cleanup: verified-kill only identities we created ourselves."""
    results = []
    for pid, ft in pids:
        r = sw.kill_verified(pid, ft)
        r["sweep"] = True
        results.append(r)
        log.append(f"[sweep] {r}")
    return results


# --------------------------------------------------------------------------- s1
def s1_atomic_suspended(root: str, out: dict, log: list[str]) -> list[dict]:
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    marker = os.path.join(root, "child_marker.txt")
    guard = sw.GuardJob()
    sess = None
    tracked: list[tuple[int, int]] = []
    try:
        out["ambient_job_before_spawn"] = sw.is_caller_in_any_job()
        sess = sw.ConPtySession.spawn(
            child_cmd(marker, "--exit-code 7"), root, 100, 30, guard, resume=False
        )
        rec = sess.record
        tracked.append((rec.pid, rec.creation_filetime))
        out["spawn_record"] = rec.__dict__
        out["guard_active_after_assign"] = guard.active_processes()

        check("identity_captured_from_retained_handle", rec.pid > 0 and rec.creation_filetime > 0,
              {"pid": rec.pid, "creation_filetime": rec.creation_filetime})
        check("assigned_to_guard_before_resume", rec.child_in_guard_job, rec.child_in_guard_job)
        check("guard_job_reports_member", (guard.active_processes() or 0) >= 1,
              guard.active_processes())
        check("still_suspended_after_spawn", not rec.resumed, rec.resumed)

        # The crux: a process that had run would have written the marker in
        # milliseconds. Observe a generous window while still suspended.
        time.sleep(2.5)
        out["marker_exists_while_suspended"] = os.path.exists(marker)
        out["process_alive_while_suspended"] = sess.is_alive()
        check("no_execution_while_suspended", not os.path.exists(marker), os.path.exists(marker))
        check("process_alive_but_not_running", sess.is_alive(), sess.is_alive())

        sess.start_reader()
        ok_resume, prev = sess.resume()
        out["resume"] = {"ok": ok_resume, "previous_suspend_count": prev}
        check("resume_reports_previous_suspend_count_1", prev == 1, prev)
        check("resume_succeeded", ok_resume, ok_resume)

        got_tail = sess.wait_output_contains("TAIL_MARKER", 25)
        code = sess.wait_exit(10)
        text = sess.output_text()
        out["pty_output"] = text
        out["exit_code"] = code
        out["marker_exists_after_resume"] = os.path.exists(marker)

        check("executed_after_resume", os.path.exists(marker), os.path.exists(marker))
        check("pty_utf8_roundtrip", "中文测试" in text and "utf8-roundtrip" in text,
              [l for l in text.splitlines() if "UNICODE" in l][:1])
        check("pty_tail_output_present", got_tail, got_tail)
        check("exit_code_preserved", code == 7, code)
        check("child_saw_pty_size_100x30", "100x30" in text,
              [l for l in text.splitlines() if "CONSOLE_SIZE" in l][:1])

        out["guard_active_after_exit"] = guard.active_processes()
        check("guard_drained_after_exit", (guard.active_processes() or 0) == 0,
              guard.active_processes())
    finally:
        if sess:
            out["close"] = sess.close()
        # closing the last guard handle must terminate any leftover tree
        guard.close()
        time.sleep(1.0)
        out["survivors_after_guard_close"] = survivor_report(tracked)
        check("no_survivors_after_guard_close", not out["survivors_after_guard_close"],
              out["survivors_after_guard_close"])
        out["sweep"] = sweep_own(tracked, log)
        out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s2
def s2_resize(root: str, out: dict, log: list[str]) -> list[dict]:
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    marker = os.path.join(root, "child_marker.txt")
    guard = sw.GuardJob()
    sess = None
    tracked: list[tuple[int, int]] = []
    try:
        sess = sw.ConPtySession.spawn(
            child_cmd(marker, "--hold 8"), root, 100, 30, guard, resume=True
        )
        tracked.append((sess.record.pid, sess.record.creation_filetime))
        sess.start_reader()
        check("initial_size_seen_by_child", sess.wait_output_contains("100x30", 20),
              [l for l in sess.output_text().splitlines() if "CONSOLE_SIZE" in l][:2])

        ok, hr = sess.resize(132, 43)
        out["resize_call"] = {"ok": ok, "hresult": hr, "cols": 132, "rows": 43}
        check("resize_call_succeeded", ok, hr)

        seen = sess.wait_output_contains("132x43", 15)
        out["pty_output"] = sess.output_text()
        check("child_observed_new_size", seen,
              [l for l in out["pty_output"].splitlines() if "CONSOLE_SIZE" in l])
        code = sess.wait_exit(20)
        out["exit_code"] = code
    finally:
        if sess:
            out["close"] = sess.close()
        guard.close()
        time.sleep(1.0)
        out["survivors_after_guard_close"] = survivor_report(tracked)
        check("no_survivors_after_guard_close", not out["survivors_after_guard_close"],
              out["survivors_after_guard_close"])
        out["sweep"] = sweep_own(tracked, log)
        out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s3
def s3_assign_failure(root: str, out: dict, log: list[str]) -> list[dict]:
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    marker = os.path.join(root, "child_marker.txt")
    guard = sw.GuardJob()
    denied: dict | None = None
    try:
        try:
            sw.ConPtySession.spawn(
                child_cmd(marker, "--exit-code 3"), root, 100, 30, guard,
                inject_assign_failure=True, resume=False,
            )
            check("spawn_was_denied", False, "spawn unexpectedly succeeded")
        except sw.SpawnDenied as exc:
            denied = {"stage": exc.stage, "detail": exc.detail}
            out["spawn_denied"] = denied
            check("spawn_was_denied", True, exc.stage)
            check("denied_at_assign_stage", exc.stage == "assign_guard_job", exc.stage)
            check("child_never_resumed", exc.detail.get("resumed") is False,
                  exc.detail.get("resumed"))
            check("assign_error_recorded", exc.detail.get("assign_last_error") is not None,
                  exc.detail.get("assign_last_error"))

        pid = (denied or {}).get("detail", {}).get("pid")
        out["guard_active_after_failure"] = guard.active_processes()
        check("guard_job_empty_after_failure", (guard.active_processes() or 0) == 0,
              guard.active_processes())

        time.sleep(2.0)
        out["marker_exists"] = os.path.exists(marker)
        check("child_never_executed", not os.path.exists(marker), os.path.exists(marker))

        if pid:
            info = sw.retrieve_identity_by_pid(pid)
            out["denied_child_identity"] = info
            check("denied_child_terminated_by_spawn_path", not info.get("still_active"), info)
        else:
            check("denied_child_terminated_by_spawn_path", False, "no pid recorded")
    finally:
        guard.close()
        time.sleep(0.5)
        out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s4
def s4_holder_hardkill(root: str, out: dict, log: list[str]) -> list[dict]:
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    report_path = os.path.join(root, "holder_report.json")
    holder_py = HOLDER
    proc = subprocess.Popen(
        [sys.executable, holder_py, "--report", report_path, "--temp-root", root, "--hold", "300"],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )
    log.append(f"[holder] launched pid={proc.pid}")
    report: dict = {}
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            if os.path.exists(report_path):
                try:
                    report = json.load(open(report_path, encoding="utf-8"))
                    if report.get("ready") is not None:
                        break
                except json.JSONDecodeError:
                    pass
            time.sleep(0.2)
        out["holder_report"] = report
        check("holder_ready", report.get("ready") is True, report.get("error"))

        holder_pid = report.get("holder_pid")
        holder_ft = report.get("holder_creation_filetime")
        child_pid = report.get("child_pid")
        child_ft = report.get("child_creation_filetime")

        grandchild_pid = None
        gm = report.get("grandchild_marker")
        if gm and os.path.exists(gm):
            for line in open(gm, encoding="utf-8"):
                if "GRANDCHILD pid=" in line:
                    grandchild_pid = int(line.strip().split("pid=")[1])
        out["grandchild_pid"] = grandchild_pid

        grandchild_ft = None
        if grandchild_pid:
            gi = sw.retrieve_identity_by_pid(grandchild_pid)
            grandchild_ft = gi.get("creation_filetime")
            out["grandchild_identity_before"] = gi
            check("grandchild_running_before_kill", gi.get("still_active"), gi)

        check("holder_holds_guard_member", (report.get("guard_active_at_ready") or 0) >= 2,
              report.get("guard_active_at_ready"))

        # ---- hard kill the runner/holder: no cleanup code can run ----
        kill = sw.kill_verified(holder_pid, holder_ft)
        out["holder_kill"] = kill
        check("holder_hard_killed", kill.get("killed") is True, kill)

        # kernel must reap the tree via kill-on-close of the last handle
        time.sleep(3.0)
        tracked = [(p, f) for p, f in ((child_pid, child_ft), (grandchild_pid, grandchild_ft)) if p and f]
        out["survivors_after_holder_kill"] = survivor_report(tracked)
        check("child_gone_after_holder_kill",
              not any(s["pid"] == child_pid for s in out["survivors_after_holder_kill"]),
              out["survivors_after_holder_kill"])
        check("grandchild_gone_after_holder_kill",
              grandchild_pid is None or
              not any(s["pid"] == grandchild_pid for s in out["survivors_after_holder_kill"]),
              out["survivors_after_holder_kill"])

        # holder itself must be gone
        hi = sw.retrieve_identity_by_pid(holder_pid)
        out["holder_identity_after_kill"] = hi
        check("holder_gone", not hi.get("still_active"), hi)

        out["sweep"] = sweep_own(tracked, log)
    finally:
        if proc.poll() is None:
            # the Popen handle is not a termination path: only verified kill is
            try:
                proc.wait(timeout=1)
            except Exception:
                pass
        out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s5
def s5_reader_cancel(root: str, out: dict, log: list[str]) -> list[dict]:
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    marker = os.path.join(root, "child_marker.txt")
    tracked: list[tuple[int, int]] = []

    # --- arm A: CancelSynchronousIo on the blocked reader ---------------------
    guard_a = sw.GuardJob()
    sess = None
    try:
        # child that stays alive and quiet so the reader blocks on ReadFile
        sess = sw.ConPtySession.spawn(
            f'"{sys.executable}" "{CHILD_PROBE}" --marker "{marker}" --hold 30',
            root, 80, 24, guard_a, resume=True,
        )
        tracked.append((sess.record.pid, sess.record.creation_filetime))
        sess.start_reader()
        time.sleep(2.0)
        # let the reader drain the startup burst, then it blocks on ReadFile
        before = len(sess.output_bytes())
        out["arm_cancel_synchronous_io"] = sess.cancel_read(timeout=6.0)
        out["bytes_before_cancel"] = before
        check("reader_converged_on_cancel",
              out["arm_cancel_synchronous_io"]["reader_converged"],
              out["arm_cancel_synchronous_io"])
        check("cancel_reported_success",
              out["arm_cancel_synchronous_io"].get("cancel_ok"),
              out["arm_cancel_synchronous_io"])
        # Measured: cancellation is prompt (0.0s) and ReadFile fails with
        # ERROR_OPERATION_ABORTED (995). This is the mechanism a backend must use.
        check("cancel_is_prompt_under_1s",
              out["arm_cancel_synchronous_io"]["converge_seconds"] < 1.0,
              out["arm_cancel_synchronous_io"]["converge_seconds"])
        check("cancel_error_is_operation_aborted",
              "995" in (out["arm_cancel_synchronous_io"].get("reader_error") or ""),
              out["arm_cancel_synchronous_io"].get("reader_error"))
    finally:
        if sess:
            out["close_arm_a"] = sess.close()
        guard_a.close()
        time.sleep(0.8)
        out["survivors_arm_a"] = survivor_report(tracked)
        check("arm_a_no_survivors", not out["survivors_arm_a"], out["survivors_arm_a"])
        sweep_own(tracked, log)

    # --- arm B: closing the read end under the blocked reader -----------------
    # Hypothesis to test, not an assumption: is closing the output handle an
    # acceptable substitute for CancelSynchronousIo? The child here deliberately
    # finishes on its own after ~8s, so the arm can distinguish "the close
    # aborted the read" from "the read only ended because the writer went away".
    guard_b = sw.GuardJob()
    sess_b = None
    tracked_b: list[tuple[int, int]] = []
    try:
        sess_b = sw.ConPtySession.spawn(
            f'"{sys.executable}" "{CHILD_PROBE}" --marker "{marker}.b" --hold 8',
            root, 80, 24, guard_b, resume=True,
        )
        tracked_b.append((sess_b.record.pid, sess_b.record.creation_filetime))
        sess_b.start_reader()
        time.sleep(2.0)
        arm_b = sess_b.cancel_read_by_closing_output(timeout=20.0)
        out["arm_close_output_handle"] = arm_b
        out["arm_b_child_hold_seconds"] = 8
        check("reader_eventually_converged_on_output_close",
              arm_b["reader_converged"], arm_b)
        # Measured negative result: closing the handle does NOT promptly abort a
        # pending synchronous ReadFile. On this machine the read only failed
        # ~28s in, once the child had exited and the pipe broke -- so the
        # "close the handle" shortcut is not a valid convergence strategy.
        check("close_handle_alone_is_not_prompt",
              arm_b["converge_seconds"] > 2.0, arm_b["converge_seconds"])
        check("close_handle_yields_no_abort_error",
              "995" not in (arm_b.get("reader_error") or ""),
              arm_b.get("reader_error"))
    finally:
        if sess_b:
            out["close_arm_b"] = sess_b.close()
        guard_b.close()
        time.sleep(0.8)
        out["survivors_arm_b"] = survivor_report(tracked_b)
        check("arm_b_no_survivors", not out["survivors_arm_b"], out["survivors_arm_b"])
        sweep_own(tracked_b, log)
        out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s6
def s6_ambient_breakaway(root: str, out: dict, log: list[str]) -> list[dict]:
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    out["caller_job_report"] = sw.current_process_job_report()
    check("ambient_job_state_recorded", "in_any_job" in out["caller_job_report"],
          out["caller_job_report"])

    cmd = f'"{sys.executable}" -c "import time;time.sleep(3)"'
    plain = sw.spawn_plain_probe(cmd, root, flags=0)
    out["child_without_breakaway"] = plain
    check("plain_child_created", plain.get("created"), plain)

    brk = sw.spawn_plain_probe(cmd, root, flags=sw.CREATE_BREAKAWAY_FROM_JOB)
    out["child_with_breakaway"] = brk
    if brk.get("created"):
        check("breakaway_child_created", True, brk)
        out["breakaway_escaped_ambient_job"] = not brk.get("in_any_job")
    else:
        # Documented outcome when a job in the chain forbids breakaway:
        # CreateProcess fails with ERROR_ACCESS_DENIED (5).
        out["breakaway_escaped_ambient_job"] = None
        check("breakaway_failure_recorded", True,
              {"last_error": brk.get("last_error"), "name": brk.get("last_error_name")})

    # A guard job created by a process already inside the ambient chain must
    # still accept our suspended child (nested job creation).
    guard = sw.GuardJob()
    sess = None
    tracked: list[tuple[int, int]] = []
    try:
        marker = os.path.join(root, "nested_marker.txt")
        sess = sw.ConPtySession.spawn(
            child_cmd(marker, "--exit-code 0"), root, 90, 25, guard, resume=True
        )
        tracked.append((sess.record.pid, sess.record.creation_filetime))
        out["nested_assign_record"] = {
            "child_in_guard_job": sess.record.child_in_guard_job,
            "child_in_ambient_job": sess.record.child_in_ambient_job,
            "assign_last_error": sess.record.assign_last_error,
        }
        check("nested_guard_assign_succeeded", sess.record.child_in_guard_job,
              out["nested_assign_record"])
        check("child_also_in_ambient_chain", sess.record.child_in_ambient_job,
              sess.record.child_in_ambient_job)
    except sw.SpawnDenied as exc:
        out["nested_assign_record"] = {"denied": exc.detail}
        check("nested_guard_assign_succeeded", False, exc.detail)
    finally:
        if sess:
            sess.close()
        guard.close()
        time.sleep(0.8)
        out["survivors"] = survivor_report(tracked)
        check("no_survivors", not out["survivors"], out["survivors"])
        sweep_own(tracked, log)
        out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s7
def s7_ctypes_traps(root: str, out: dict, log: list[str]) -> list[dict]:
    """Reproduce the ctypes-level traps found while building the spawn path.

    These are not Win32 behaviour claims -- they are claims about calling these
    APIs through ctypes on this machine, which is what the backend will do.
    """
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    k = sw.kernel32
    si = sw.STARTUPINFOW()
    si.cb = ctypes.sizeof(sw.STARTUPINFOW)
    pi = sw.PROCESS_INFORMATION()
    buf = ctypes.create_unicode_buffer(f'"{sys.executable}" -c "import time;time.sleep(5)"')
    ok = k.CreateProcessW(None, buf, None, None, False, sw.CREATE_SUSPENDED,
                          None, None, ctypes.byref(si), ctypes.byref(pi))
    check("probe_child_created", bool(ok), bool(ok))
    try:
        # trap 1: NULL for GetProcessTimes' optional out-parameters
        raised = None
        try:
            k.GetProcessTimes(pi.hProcess, ctypes.byref(sw.wintypes.FILETIME()), None, None, None)
        except OSError as exc:
            raised = repr(exc)
        out["getprocesstimes_with_none"] = raised
        check("null_outparam_raises_access_violation",
              raised is not None and "access violation" in raised.lower(), raised)

        # control: supplying all four pointers works
        creation = sw.read_creation_filetime(pi.hProcess)
        out["getprocesstimes_all_pointers"] = creation
        check("all_pointers_succeeds", creation is not None and creation > 0, creation)

        # trap 2: struct layout is what Win32 expects
        sizes = {
            "STARTUPINFOW": ctypes.sizeof(sw.STARTUPINFOW),
            "STARTUPINFOEXW": ctypes.sizeof(sw.STARTUPINFOEXW),
            "PROCESS_INFORMATION": ctypes.sizeof(sw.PROCESS_INFORMATION),
            "JOBOBJECT_EXTENDED_LIMIT_INFORMATION": ctypes.sizeof(sw.JOBOBJECT_EXTENDED_LIMIT_INFORMATION),
            "offset_lpAttributeList": sw.STARTUPINFOEXW.lpAttributeList.offset,
        }
        out["struct_sizes"] = sizes
        check("startupinfoex_layout_matches_win32",
              sizes["STARTUPINFOW"] == 104 and sizes["STARTUPINFOEXW"] == 112
              and sizes["offset_lpAttributeList"] == 104 and sizes["PROCESS_INFORMATION"] == 24,
              sizes)
    finally:
        k.TerminateProcess(pi.hProcess, 0)
        k.WaitForSingleObject(pi.hProcess, 5000)
        k.CloseHandle(pi.hThread)
        k.CloseHandle(pi.hProcess)
        out["checks"] = checks
    return checks


SCENARIOS = {
    "s1_atomic_suspended": s1_atomic_suspended,
    "s2_resize": s2_resize,
    "s3_assign_failure": s3_assign_failure,
    "s4_holder_hardkill": s4_holder_hardkill,
    "s5_reader_cancel": s5_reader_cancel,
    "s6_ambient_breakaway": s6_ambient_breakaway,
    "s7_ctypes_traps": s7_ctypes_traps,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all", choices=["all", *SCENARIOS])
    ap.add_argument("--out", default=os.path.join(HERE, "evidence"))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    summary = {"scenarios": {}, "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    for name in names:
        root = scenario_dir(name, args.keep)
        log: list[str] = []
        out: dict = {
            "scenario": name,
            "temp_root": root,
            "keep": args.keep,
            "python": sys.executable,
            "python_version": sys.version.split()[0],
        }
        t0 = time.time()
        try:
            checks = SCENARIOS[name](root, out, log)
        except Exception as exc:  # a scenario bug must not hide evidence
            checks = [{"check": "scenario_completed", "ok": False, "detail": repr(exc)}]
            import traceback
            out["traceback"] = traceback.format_exc()
        out["checks"] = checks
        out["passed"] = all(c["ok"] for c in checks)
        out["failed_checks"] = [c for c in checks if not c["ok"]]
        out["seconds"] = round(time.time() - t0, 2)
        out["log"] = log
        out["temp_removed"] = cleanup_root(root, args.keep)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(args.out, f"{name}-{stamp}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
        summary["scenarios"][name] = {
            "passed": out["passed"], "checks": len(checks),
            "failed": [c["check"] for c in out["failed_checks"]],
            "seconds": out["seconds"], "evidence": os.path.basename(path),
            "temp_removed": out["temp_removed"],
        }
        print(f"{name:24s} passed={out['passed']!s:5s} checks={len(checks):2d} "
              f"failed={[c['check'] for c in out['failed_checks']]}")

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    print()
    print("summary:", json.dumps(summary["scenarios"], ensure_ascii=False)[:2000])
    return 0 if all(s["passed"] for s in summary["scenarios"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
