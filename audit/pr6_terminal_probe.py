"""Read-only PR #6 audit; every spawned process belongs to this probe.

Run with the pinned PR dependencies in a disposable uv environment.
This verifies observed shortcomings, rather than pretending they are fixes.
"""
from __future__ import annotations

import argparse
import json
import queue
import re
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    from packages.core.rewind import driver
    import psutil

    results = {}

    class ExitedWithBufferedOutput:
        reads = 0

        def isalive(self):
            return False

        def read(self, size):
            self.reads += 1
            return "FINAL_OUTPUT"

    session = driver._PtySession.__new__(driver._PtySession)
    session.proc = ExitedWithBufferedOutput()
    session.queue = queue.Queue()
    session._read()
    assert session.proc.reads == 0 and session.queue.get_nowait() is None
    results["exit_before_drain"] = {"read_calls": 0, "buffered_output_read": False}

    class Flood:
        reads = 0

        def isalive(self):
            return self.reads < 2048

        def read(self, size):
            self.reads += 1
            return "x" * size

    session.proc = Flood()
    session.queue = queue.Queue()
    session._read()
    assert session.queue.maxsize == 0 and session.queue.qsize() == 2049
    results["queue_without_consumer"] = {
        "maxsize": session.queue.maxsize,
        "queued_characters": session.proc.reads * 4096,
    }

    # Exercise the PR close ordering with an actual Windows parent and child.
    # The fake PTY only replaces transport; process enumeration/termination
    # is the unmodified PR implementation and real OS process state.
    child_code = "import time; time.sleep(120)"
    parent_code = (
        "import subprocess,sys,time; "
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        "print(p.pid,flush=True); time.sleep(120)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-u", "-c", parent_code],
        stdout=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    child = None
    try:
        child = psutil.Process(int(parent.stdout.readline().strip()))

        class RootOnlyTermination:
            pid = parent.pid

            def isalive(self):
                return parent.poll() is None

            def write(self, value):
                pass

            def terminate(self, force):
                parent.kill()
                parent.wait(timeout=5)

            def close(self):
                pass

        session.proc = RootOnlyTermination()
        info = session.close()
        alive = child.is_running()
        assert alive and info["killed_pids"] == []
        results["enumerate_after_parent_terminated"] = {
            "cleanup": info, "owned_child_still_alive": alive,
        }
    finally:
        if child is not None and child.is_running():
            child.kill()
            child.wait(timeout=5)
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        parent.stdout.close()

    # Real pywinpty + pyte: Unicode input/output, ANSI parsing, no CBC calls.
    child_code = (
        "import sys,time; print('PAN_PTY_READY',flush=True); "
        "line=input(); print('\\x1b[32mPAN_ECHO:'+line+'\\x1b[0m',flush=True); "
        "time.sleep(30)"
    )
    session = driver._PtySession(
        [sys.executable, "-u", "-c", child_code], args.source,
    )
    pid = session.proc.pid
    try:
        ready, _ = session.wait_for(
            lambda value: "PAN_PTY_READY" in value, time.monotonic() + 10,
        )
        assert ready
        session.send("中文-terminal-42\r\n")
        echoed, screen = session.wait_for(
            lambda value: "PAN_ECHO:中文-terminal-42" in value,
            time.monotonic() + 10,
        )
        assert echoed and "\x1b[32m" not in screen
        results["live_pty_unicode_ansi"] = {"ready": ready, "echoed": echoed}
    finally:
        cleanup = session.close()
        session.reader.join(timeout=3)
        root_gone = not psutil.pid_exists(pid)
        results["live_simple_process_cleanup"] = {
            "cleanup": cleanup, "reader_joined": not session.reader.is_alive(),
            "root_gone": root_gone,
        }
        assert root_gone

    # Real PTY descendant probe, distinct from the helper-order reproduction.
    # The child ignores Ctrl-C so stopping the parent cannot masquerade as
    # verified ownership of the whole process tree.
    child_code = "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); time.sleep(120)"
    parent_code = (
        "import subprocess,sys,time; "
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        "time.sleep(1); print('OWNED_CHILD_PID='+str(p.pid),flush=True); time.sleep(120)"
    )
    session = driver._PtySession([sys.executable, "-u", "-c", parent_code], args.source)
    child = None
    try:
        ready, screen = session.wait_for(
            lambda value: "OWNED_CHILD_PID=" in value, time.monotonic() + 10,
        )
        assert ready
        child = psutil.Process(int(re.search(r"OWNED_CHILD_PID=(\d+)", screen).group(1)))
        cleanup = session.close()
        session.reader.join(timeout=3)
        results["live_pty_descendant_cleanup"] = {
            "cleanup": cleanup, "owned_child_still_alive": child.is_running(),
            "reader_joined": not session.reader.is_alive(),
        }
    finally:
        if session.proc.isalive():
            session.close()
        if child is not None and child.is_running():
            child.kill()
            child.wait(timeout=5)

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
