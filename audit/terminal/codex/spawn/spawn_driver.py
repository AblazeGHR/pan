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
def s7_api_contract_checks(root: str, out: dict, log: list[str]) -> list[dict]:
    """Confirm we call the ConPTY/process APIs with pointers they actually accept.

    ``GetProcessTimes`` declares four ``[out] FILETIME`` parameters and marks none
    of them optional
    (<https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes>).
    Passing NULL therefore violates the API's pointer contract, and the access
    violation observed when doing so is the consequence of that violation --
    not a quirk of ctypes and not evidence that NULL is "allowed but broken".
    The check below records both the contract violation and the correct call.
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
        raised = None
        try:
            k.GetProcessTimes(pi.hProcess, ctypes.byref(sw.wintypes.FILETIME()), None, None, None)
        except OSError as exc:
            raised = repr(exc)
        out["getprocesstimes_with_null_outparams"] = raised
        check("null_outparam_is_rejected_by_the_api",
              raised is not None and "access violation" in raised.lower(), raised)

        creation = sw.read_creation_filetime(pi.hProcess)
        out["getprocesstimes_all_pointers"] = creation
        check("contract_conformant_call_succeeds", creation is not None and creation > 0, creation)

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

        # liveness primitive: the documented wait semantics, on a live object
        out["wait_state_live_child"] = sw.wait_state(pi.hProcess)
        check("wait_state_alive_for_running_child",
              sw.wait_state(pi.hProcess) == "alive", out["wait_state_live_child"])
    finally:
        k.TerminateProcess(pi.hProcess, 0)
        k.WaitForSingleObject(pi.hProcess, 5000)
        out["wait_state_after_terminate"] = sw.wait_state(pi.hProcess)
        check("wait_state_dead_after_terminate",
              sw.wait_state(pi.hProcess) == "dead", out["wait_state_after_terminate"])
        k.CloseHandle(pi.hThread)
        k.CloseHandle(pi.hProcess)
        out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s8
def s8_liveness_primitives(root: str, out: dict, log: list[str]) -> list[dict]:
    """Liveness must come from WaitForSingleObject(0), not from the exit code.

    Two failure modes are exercised, both as real processes rather than as mocks:
      * a process that genuinely exits with code 259 -- the same numeric value as
        STILL_ACTIVE -- must read as dead and must not be terminated;
      * a live process whose expected FILETIME is off by one must be refused.
    """
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # --- part 1: a real exit code 259 with the handle still retained ----------
    marker = os.path.join(root, "exit259.txt")
    guard = sw.GuardJob()
    sess = None
    tracked: list[tuple[int, int]] = []
    try:
        sess = sw.ConPtySession.spawn(
            child_cmd(marker, "--exit-code 259"), root, 90, 25, guard, resume=True
        )
        tracked.append((sess.record.pid, sess.record.creation_filetime))
        sess.start_reader()
        code = sess.wait_exit(25)
        out["child_requested_exit_code"] = 259
        out["wait_exit_returned"] = code
        out["exit_code_informational"] = sess.exit_code()
        out["liveness_with_retained_handle"] = sess.liveness()
        out["is_alive_with_retained_handle"] = sess.is_alive()

        check("child_really_exited_with_259", code == 259, code)
        informational = sess.exit_code()
        out["informational_exit_code_read"] = informational
        check("informational_exit_code_is_259", informational == 259, informational)
        check("liveness_reads_dead_despite_259", sess.liveness() == "dead",
              sess.liveness())
        check("is_alive_false_despite_259", sess.is_alive() is False, sess.is_alive())

        # the verified-kill path must refuse an already-exited process, and must
        # not "kill" it just because the exit code looked like STILL_ACTIVE
        kill = sw.kill_verified(sess.record.pid, sess.record.creation_filetime)
        out["kill_verified_on_exited_259"] = kill
        check("verified_kill_refuses_exited_259",
              kill.get("killed") is False and kill.get("reason") == "not_running", kill)
        check("verified_kill_saw_state_dead", kill.get("state") == "dead", kill.get("state"))
    finally:
        if sess:
            out["close_part1"] = sess.close()
        guard.close()
        time.sleep(0.8)
        out["survivors_part1"] = survivor_report(tracked)
        check("part1_no_survivors", not out["survivors_part1"], out["survivors_part1"])
        sweep_own(tracked, log)

    # --- part 2: wrong FILETIME (+1) on a live process must refuse ------------
    guard2 = sw.GuardJob()
    sess2 = None
    tracked2: list[tuple[int, int]] = []
    try:
        sess2 = sw.ConPtySession.spawn(
            child_cmd(os.path.join(root, "live.txt"), "--hold 25"), root, 90, 25, guard2, resume=True
        )
        pid, ft = sess2.record.pid, sess2.record.creation_filetime
        tracked2.append((pid, ft))
        sess2.start_reader()
        time.sleep(2.0)
        out["live_liveness"] = sess2.liveness()
        check("live_process_reads_alive", sess2.liveness() == "alive", sess2.liveness())

        wrong = sw.kill_verified(pid, ft + 1)
        out["kill_verified_wrong_filetime"] = wrong
        check("verified_kill_refuses_wrong_filetime",
              wrong.get("killed") is False and wrong.get("reason") == "identity_mismatch", wrong)
        time.sleep(0.5)
        still = sw.retrieve_identity_by_pid(pid)
        out["identity_after_refused_kill"] = still
        check("target_still_alive_after_refused_kill", still.get("state") == "alive", still)

        # positive control: the exact identity is accepted
        good = sw.kill_verified(pid, ft)
        out["kill_verified_correct_filetime"] = good
        check("verified_kill_accepts_exact_identity", good.get("killed") is True, good)
    finally:
        if sess2:
            out["close_part2"] = sess2.close()
        guard2.close()
        time.sleep(0.8)
        out["survivors_part2"] = survivor_report(tracked2)
        check("part2_no_survivors", not out["survivors_part2"], out["survivors_part2"])
        sweep_own(tracked2, log)
        out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s9
def s9_failclosed_gates(root: str, out: dict, log: list[str]) -> list[dict]:
    """Unproven guard membership, and an implausible resume, must both refuse.

    IMPORTANT: every failure here is injected at the wrapper level (an argument to
    ``spawn``), not produced by the operating system. These results demonstrate
    that the *gate logic* refuses; they are not evidence about real OS errors.
    """
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    out["injection_note"] = ("wrapper-level simulation via spawn() arguments; "
                             "not a real OS failure reproduction")
    cases = [
        ("membership_query_failure", dict(inject_membership_query_failure=True),
         "verify_guard_membership"),
        ("membership_reported_false", dict(inject_membership_false=True),
         "verify_guard_membership"),
        ("resume_previous_count_0", dict(inject_resume_count=0), "resume_thread"),
        ("resume_previous_count_2", dict(inject_resume_count=2), "resume_thread"),
        ("resume_returned_wait_failed", dict(inject_resume_count=0xFFFFFFFF), "resume_thread"),
    ]
    results = {}
    for name, kwargs, expect_stage in cases:
        marker = os.path.join(root, f"{name}.txt")
        guard = sw.GuardJob()
        try:
            try:
                sw.ConPtySession.spawn(child_cmd(marker), root, 80, 24, guard,
                                       resume=True, **kwargs)
                results[name] = {"denied": False}
                check(f"{name}_denied", False, "spawn unexpectedly succeeded")
                continue
            except sw.SpawnDenied as exc:
                entry = {"denied": True, "stage": exc.stage, "detail": exc.detail}
                results[name] = entry
                check(f"{name}_denied_at_expected_stage", exc.stage == expect_stage,
                      {"got": exc.stage, "want": expect_stage})
                check(f"{name}_not_resumed", exc.detail.get("resumed") is False,
                      exc.detail.get("resumed"))
                cleanup = exc.detail.get("cleanup") or {}
                check(f"{name}_pty_released",
                      bool((cleanup.get("pty") or {}).get("pty_closed")),
                      cleanup.get("pty"))
                check(f"{name}_attr_list_released",
                      bool((cleanup.get("attr_list") or {}).get("attr_list_deleted")),
                      cleanup.get("attr_list"))
        finally:
            # nothing may have run
            results.setdefault(name, {})["marker_exists"] = os.path.exists(marker)
            check(f"{name}_child_never_executed", not os.path.exists(marker),
                  os.path.exists(marker))
            # Query membership *while we still own the handle*: a closed handle
            # must not be asked (measured: it can answer with garbage).
            settled = None
            for _ in range(30):
                settled = guard.active_processes()
                if settled == 0:
                    break
                time.sleep(0.1)
            results[name]["guard_active_while_owned"] = settled
            results[name]["owner_handle_alive"] = guard.raw_handle is not None
            check(f"{name}_guard_empty_while_owned", settled == 0, settled)
            guard.close()
            results[name]["guard_active_after_close"] = guard.active_processes()
            check(f"{name}_closed_owner_reports_unknown",
                  guard.active_processes() is None, guard.active_processes())
    out["cases"] = results
    out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s10
def s10_resource_release(root: str, out: dict, log: list[str]) -> list[dict]:
    """Resource-release evidence for the paths that used to leak or lie."""
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # --- part 1: a denied spawn must release the pseudoconsole + attr list ----
    guard = sw.GuardJob()
    try:
        try:
            sw.ConPtySession.spawn(child_cmd(os.path.join(root, "denied.txt")), root,
                                   80, 24, guard, inject_assign_failure=True, resume=False)
            check("denied_spawn_raised", False, "no SpawnDenied")
        except sw.SpawnDenied as exc:
            cleanup = exc.detail.get("cleanup") or {}
            out["denied_cleanup"] = cleanup
            check("denied_spawn_raised", True, exc.stage)
            check("denied_path_closed_pseudo_console",
                  bool((cleanup.get("pty") or {}).get("pty_closed")),
                  cleanup.get("pty"))
            check("denied_path_deleted_attr_list",
                  bool((cleanup.get("attr_list") or {}).get("attr_list_deleted")),
                  cleanup.get("attr_list"))
            check("denied_path_closed_process_handles",
                  len(cleanup.get("process_handles_closed") or []) >= 1,
                  cleanup.get("process_handles_closed"))
            check("denied_path_closed_pipes",
                  len(cleanup.get("pipes_closed") or []) >= 1,
                  cleanup.get("pipes_closed"))
    finally:
        guard.close()

    # --- part 2: a close() that cannot stop the reader must keep ownership ----
    marker = os.path.join(root, "stuck.txt")
    guard2 = sw.GuardJob()
    sess = None
    tracked: list[tuple[int, int]] = []
    try:
        # The child finishes on its own after ~6s so this arm can be bounded and
        # the retry can succeed deterministically.
        sess = sw.ConPtySession.spawn(child_cmd(marker, "--hold 6"), root, 80, 24,
                                      guard2, resume=True)
        tracked.append((sess.record.pid, sess.record.creation_filetime))
        sess.start_reader()
        time.sleep(2.0)

        # Wrapper-level simulation of "the reader cannot be stopped": remove the
        # cancellable thread handle (so CancelSynchronousIo cannot be issued) and
        # hide the output handle from close() (so it cannot force convergence
        # that way either). Both must be simulated together: measurements show
        # closing the output handle does sometimes abort a pending read, and
        # replacing the completion Event is not a valid simulation at all --
        # the reader thread calls self.reader_returned.set() on whatever object
        # the attribute currently holds, so it would just set the new one.
        saved_handle = sess._reader_thread_handle
        saved_output = sess._output_read
        sess._reader_thread_handle = None
        sess._output_read = None
        out["simulation_note"] = ("reader thread handle and output handle hidden from "
                                  "close(); wrapper-level simulation, not a real hung I/O")
        first = sess.close(drain_timeout=2.0)
        out["close_while_stuck"] = first
        check("stuck_close_reports_not_closed", first.get("closed") is False, first.get("closed"))
        check("stuck_close_is_retryable", first.get("retryable") is True, first.get("retryable"))
        check("stuck_close_keeps_owner_open", sess._closed is False, sess._closed)
        check("stuck_close_did_not_destroy_pty", sess._hpc is not None,
              "hpc still owned" if sess._hpc else "hpc was destroyed")
        check("stuck_close_reports_reason",
              "reader_not_converged" in (first.get("errors") or []), first.get("errors"))

        # let the child exit so the writer goes away and the reader can finish,
        # then prove a retry on the same owner succeeds
        sess.wait_exit(15)
        time.sleep(0.5)
        sess._reader_thread_handle = saved_handle
        sess._output_read = saved_output
        second = sess.close(drain_timeout=8.0)
        out["close_after_retry"] = second
        check("retry_close_succeeds", second.get("closed") is True, second.get("closed"))
        check("retry_close_destroyed_pty",
              bool((second.get("pty") or {}).get("pty_closed")), second.get("pty"))
        check("retry_close_deleted_attr_list",
              bool((second.get("attr_list") or {}).get("attr_list_deleted")),
              second.get("attr_list"))
    finally:
        if sess and not sess._closed:
            try:
                sess.close(drain_timeout=3.0)
            except Exception:
                pass
        guard2.close()
        time.sleep(0.8)
        out["survivors"] = survivor_report(tracked)
        check("no_survivors", not out["survivors"], out["survivors"])
        sweep_own(tracked, log)
        out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s11
def s11_std_handle_matrix(root: str, out: dict, log: list[str]) -> list[dict]:
    """Committed matrix behind the STARTF_USESTDHANDLES finding.

    For each combination of (STARTF_USESTDHANDLES + NULL std handles) and
    bInheritHandles, checks whether the child's stdout lands on the
    pseudoconsole. The child writes to its stdout handle and to CONOUT$ --
    whichever appears in the pty bytes shows where each path went.

    Scope: this machine, this Windows build. The conclusion is a statement about
    what was measured here, not a general rule for all environments.
    """
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    child = (
        "import ctypes,sys;"
        "k=ctypes.WinDLL('kernel32',use_last_error=True);"
        "k.CreateFileW.restype=ctypes.c_void_p;"
        "h=k.CreateFileW('CONOUT$',0x40000000,3,None,3,0,None);"
        "n=ctypes.c_ulong(0);"
        "k.WriteFile(ctypes.c_void_p(h), b'VIA_CONOUT\\n', 11, ctypes.byref(n), None) "
        "if h and h != 0xFFFFFFFFFFFFFFFF else None;"
        "sys.stdout.write('VIA_STDOUT\\n');sys.stdout.flush()"
    )
    rows = []
    for use_null in (True, False):
        for inherit in (False, True):
            guard = sw.GuardJob()
            sess = None
            tracked: list[tuple[int, int]] = []
            # Inline child: writes one marker through its stdout handle and one
            # straight to the console device, so the pty bytes reveal which path
            # each took.
            cmd = f'"{sys.executable}" -c "{child}"'
            try:
                sess = sw.ConPtySession.spawn(cmd, root, 90, 25, guard, resume=True,
                                              use_std_handles_null=use_null,
                                              inherit_handles=inherit)
                tracked.append((sess.record.pid, sess.record.creation_filetime))
                sess.start_reader()
                sess.wait_exit(15)
                time.sleep(0.6)
                text = sess.output_bytes()
                rows.append({
                    "startf_use_std_handles_null": use_null,
                    "b_inherit_handles": inherit,
                    "conout_in_pty": b"VIA_CONOUT" in text,
                    "stdout_in_pty": b"VIA_STDOUT" in text,
                    "pty_bytes": len(text),
                })
            except sw.SpawnDenied as exc:
                rows.append({
                    "startf_use_std_handles_null": use_null,
                    "b_inherit_handles": inherit,
                    "denied": exc.stage, "detail": exc.detail,
                })
            finally:
                if sess:
                    sess.close()
                guard.close()
                time.sleep(0.5)
                sweep_own(tracked, log)

    out["matrix"] = rows
    out["scope"] = ("measured on Windows 11 build 26200 / CPython 3.12.12 on this "
                    "machine; not a claim about every build or parent topology")
    control = [r for r in rows if r["startf_use_std_handles_null"]]
    without = [r for r in rows if not r["startf_use_std_handles_null"]]
    check("conout_reaches_pty_in_all_rows",
          all(r.get("conout_in_pty") for r in rows), rows)
    check("stdout_reaches_pty_only_with_the_flag",
          all(r.get("stdout_in_pty") for r in control)
          and not any(r.get("stdout_in_pty") for r in without), rows)
    check("flag_alone_decided_outcome_on_this_machine",
          len({r.get("stdout_in_pty") for r in control}) == 1
          and len({r.get("stdout_in_pty") for r in without}) == 1, rows)
    out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s12
def s12_resume_gate(root: str, out: dict, log: list[str]) -> list[dict]:
    """The public resume() path must enforce the same exactly-once gate as spawn.

    Previously a second resume() re-issued ResumeThread on an unsuspended thread,
    got 0, and still wrote ``record.resumed = True`` -- reporting a resume that
    did not happen. Repeat calls must be refused outright, and the factual record
    must not be rewritten.
    """
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    marker = os.path.join(root, "resume_gate.txt")
    guard = sw.GuardJob()
    sess = None
    tracked: list[tuple[int, int]] = []
    try:
        sess = sw.ConPtySession.spawn(child_cmd(marker, "--hold 4"), root, 80, 24,
                                      guard, resume=False)
        tracked.append((sess.record.pid, sess.record.creation_filetime))
        out["suspended_record"] = {"resumed": sess.record.resumed,
                                   "prev_count": sess.record.resume_previous_suspend_count}
        check("starts_suspended", sess.record.resumed is False, sess.record.resumed)
        time.sleep(1.0)
        out["marker_before_resume"] = os.path.exists(marker)
        check("no_execution_before_resume", not os.path.exists(marker), out["marker_before_resume"])

        first_ok, first_prev = sess.resume()
        out["first_resume"] = {"ok": first_ok, "previous_suspend_count": first_prev,
                               "detail": sess.resume_detail}
        check("first_resume_ok", first_ok is True, first_ok)
        check("first_resume_reports_previous_count_1", first_prev == 1, first_prev)
        check("record_marked_resumed", sess.record.resumed is True, sess.record.resumed)
        check("record_previous_count_is_1",
              sess.record.resume_previous_suspend_count == 1,
              sess.record.resume_previous_suspend_count)

        # second call: must refuse, and must not rewrite the record
        record_before = (sess.record.resumed, sess.record.resume_previous_suspend_count)
        second_ok, second_prev = sess.resume()
        record_after = (sess.record.resumed, sess.record.resume_previous_suspend_count)
        out["second_resume"] = {"ok": second_ok, "previous_suspend_count": second_prev,
                                "detail": sess.resume_detail,
                                "record_before": record_before, "record_after": record_after}
        check("second_resume_refused", second_ok is False, second_ok)
        check("second_resume_reports_already_resumed",
              sess.resume_detail.get("reason") == "already_resumed", sess.resume_detail)
        check("second_resume_does_not_claim_not_resumed",
              sess.resume_detail.get("currently_resumed") is True, sess.resume_detail)
        check("record_unchanged_by_second_call", record_before == record_after,
              {"before": record_before, "after": record_after})

        # the child must actually have run exactly once
        sess.start_reader()
        got = sess.wait_output_contains("TAIL_MARKER", 20)
        out["pty_output"] = sess.output_text()
        check("child_ran_after_single_resume", got, got)
        out["marker_after_resume"] = os.path.exists(marker)
        check("execution_observed_after_resume", os.path.exists(marker),
              out["marker_after_resume"])
    finally:
        if sess:
            out["close"] = sess.close()
        guard.close()
        time.sleep(0.8)
        out["survivors"] = survivor_report(tracked)
        check("no_survivors", not out["survivors"], out["survivors"])
        sweep_own(tracked, log)
        out["checks"] = checks
    return checks


# --------------------------------------------------------------------------- s13
def s13_close_failure_retry(root: str, out: dict, log: list[str]) -> list[dict]:
    """A close() whose release step fails must stay owned and be retryable.

    Injecting a failure into ``_destroy_pty`` (then ``_delete_attr_list``) must
    yield ``closed=False, retryable=True`` with the failed resource still held and
    the other handles deliberately left alone; removing the injection and calling
    close() again on the same owner must succeed. Injection is wrapper-level.
    """
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    out["injection_note"] = ("wrapper-level: session._destroy_pty / _delete_attr_list are "
                             "replaced with a failing stub; not a real OS failure")

    for label, attr in (("pty_close", "_destroy_pty"), ("attr_list_delete", "_delete_attr_list")):
        guard = sw.GuardJob()
        sess = None
        tracked: list[tuple[int, int]] = []
        try:
            sess = sw.ConPtySession.spawn(
                child_cmd(os.path.join(root, f"{label}.txt"), "--hold 3"),
                root, 80, 24, guard, resume=True)
            tracked.append((sess.record.pid, sess.record.creation_filetime))
            sess.start_reader()
            time.sleep(2.0)
            sess.wait_exit(10)

            original = getattr(sess, attr)
            setattr(sess, attr, lambda: {"failed": True, "injected": True})
            first = sess.close(drain_timeout=3.0)
            setattr(sess, attr, original)
            out[f"{label}_first_close"] = first

            check(f"{label}_close_reports_not_closed", first.get("closed") is False,
                  first.get("closed"))
            check(f"{label}_close_is_retryable", first.get("retryable") is True,
                  first.get("retryable"))
            check(f"{label}_owner_retained", sess._closed is False, sess._closed)
            check(f"{label}_error_names_the_resource",
                  any(label.split("_")[0] in e for e in (first.get("errors") or [])),
                  first.get("errors"))
            retained = {r["resource"] for r in (first.get("retained") or [])}
            out[f"{label}_retained_resources"] = sorted(retained)
            check(f"{label}_retained_inventory_mentions_failure",
                  ("pseudo_console" in retained) if attr == "_destroy_pty" else ("attr_list" in retained),
                  sorted(retained))

            second = sess.close(drain_timeout=5.0)
            out[f"{label}_retry_close"] = second
            check(f"{label}_retry_succeeds", second.get("closed") is True, second.get("closed"))
            if attr == "_destroy_pty":
                check(f"{label}_retry_closed_pseudo_console",
                      bool((second.get("pty") or {}).get("pty_closed")), second.get("pty"))
            else:
                check(f"{label}_retry_deleted_attr_list",
                      bool((second.get("attr_list") or {}).get("attr_list_deleted")),
                      second.get("attr_list"))
        finally:
            if sess and not sess._closed:
                try:
                    sess.close(drain_timeout=3.0)
                except Exception:
                    pass
            guard.close()
            time.sleep(0.8)
            out[f"{label}_survivors"] = survivor_report(tracked)
            check(f"{label}_no_survivors", not out[f"{label}_survivors"],
                  out[f"{label}_survivors"])
            sweep_own(tracked, log)
    out["checks"] = checks
    return checks


SCENARIOS = {
    "s1_atomic_suspended": s1_atomic_suspended,
    "s2_resize": s2_resize,
    "s3_assign_failure": s3_assign_failure,
    "s4_holder_hardkill": s4_holder_hardkill,
    "s5_reader_cancel": s5_reader_cancel,
    "s6_ambient_breakaway": s6_ambient_breakaway,
    "s7_api_contract": s7_api_contract_checks,
    "s8_liveness_primitives": s8_liveness_primitives,
    "s9_failclosed_gates": s9_failclosed_gates,
    "s10_resource_release": s10_resource_release,
    "s11_std_handle_matrix": s11_std_handle_matrix,
    "s12_resume_gate": s12_resume_gate,
    "s13_close_failure_retry": s13_close_failure_retry,
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
