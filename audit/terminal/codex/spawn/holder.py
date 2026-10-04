"""Runner/guard-holder stand-in for the atomic-spawn spike.

Mirrors the ownership layout the implementation plan §5.1/§5.3 calls "layout B":
this process is born owning the PTY, holds the ``KILL_ON_JOB_CLOSE`` guard job
for the whole tree itself, and (in the hard-kill scenario) is destroyed with no
chance to run any cleanup code. Whatever survives afterwards is the kernel's
doing, not ours.

It reports identities to a JSON file for the driver, then idles so the driver can
kill it. It never touches a process it did not create.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spawn_win as sw  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--temp-root", required=True)
    ap.add_argument("--hold", type=float, default=300.0)
    ap.add_argument("--cols", type=int, default=100)
    ap.add_argument("--rows", type=int, default=30)
    args = ap.parse_args()

    child_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "child_probe.py")
    marker = os.path.join(args.temp_root, "child_marker.txt")
    grandchild_marker = os.path.join(args.temp_root, "grandchild_marker.txt")
    cmd = (
        f'"{sys.executable}" "{child_py}" --marker "{marker}" '
        f'--grandchild-marker "{grandchild_marker}" --grandchild-hold 200 --hold 1'
    )

    report: dict = {
        "holder_pid": os.getpid(),
        "holder_creation_filetime": None,
        "temp_root": args.temp_root,
        "marker": marker,
        "grandchild_marker": grandchild_marker,
        "started_at": time.time(),
    }

    guard = sw.GuardJob()
    sess = None
    try:
        report["holder_creation_filetime"] = sw.read_creation_filetime(
            sw.kernel32.GetCurrentProcess()
        )
        sess = sw.ConPtySession.spawn(cmd, args.temp_root, args.cols, args.rows, guard)
        rec = sess.record
        report.update({
            "child_pid": rec.pid,
            "child_creation_filetime": rec.creation_filetime,
            "child_in_guard_job": rec.child_in_guard_job,
            "guard_active_after_assign": rec.guard_active_after_assign,
            "ready": True,
        })
        sess.start_reader()
        # Give the child time to spawn the grandchild and record it.
        deadline = time.time() + 15
        while time.time() < deadline:
            if os.path.exists(grandchild_marker):
                break
            time.sleep(0.1)
        time.sleep(0.5)
        report["child_alive"] = sess.is_alive()
        report["guard_active_at_ready"] = guard.active_processes()
        report["pty_output_head"] = sess.output_text()[:300]
    except Exception as exc:
        report["ready"] = False
        report["error"] = repr(exc)
    finally:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)

    # Idle until the driver kills this process. Crucially: no cleanup is run on
    # the kill path, so any tree death after a hard kill is kernel-driven.
    time.sleep(args.hold)
    # If we are allowed to exit normally, close the guard handle: that alone
    # must terminate the whole tree.
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass
    guard.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
