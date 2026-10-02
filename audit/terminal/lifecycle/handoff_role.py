"""Helper roles for job_handle_handoff.py — all processes are probe-owned.

Roles (line protocol on stdin/stdout):

  tree   waits for ``GO``, then spawns a grandchild sleeper (which inherits the
         guard job) and prints ``G <pid>``; then idles.
  a      receives ``HANDLE <value>`` (a duplicated job handle valid in this
         process), prints ``A_HOLDS``; on ``HANDOFF <b_pid>`` duplicates its
         job handle into process B and prints ``HANDOFF_OK <value_in_b>``;
         on ``CLOSE`` closes its handle, prints ``A_CLOSED`` and exits.
  b      receives ``VALIDATE <handle> <member_pid>``, introspects the job via
         its own duplicated handle and prints ``B_HOLDS active=<n> member=<bool>``;
         on ``CLOSE`` closes the handle, prints ``B_CLOSED`` and keeps running
         (so the tree death cannot be attributed to B exiting);
         on ``EXIT`` exits.
"""
from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probe_lib as lib  # noqa: E402

PYTHON, PY_ENV = lib.probe_python()


def say(text: str) -> None:
    print(text, flush=True)


def role_tree() -> None:
    try:
        sys.stdin.readline()
    except Exception:
        pass
    grandchild = subprocess.Popen(
        [PYTHON, "-c", "import time; time.sleep(300)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=PY_ENV)
    say(f"G {grandchild.pid}")
    time.sleep(300)


def role_a() -> None:
    line = sys.stdin.readline().strip()
    if not line.startswith("HANDLE "):
        say("A_BAD_LINE")
        return
    handle = int(line.split()[1])
    say("A_HOLDS")
    for line in sys.stdin:
        command = line.strip()
        if command.startswith("HANDOFF "):
            b_pid = int(command.split()[1])
            b_handle = lib.open_process(
                b_pid, lib.PROCESS_DUP_HANDLE | lib.PROCESS_QUERY_LIMITED_INFORMATION)
            if not b_handle:
                say("HANDOFF_FAIL open_process")
                continue
            try:
                ok, new_handle, err = lib.duplicate_handle(
                    lib.current_process_handle(), handle, b_handle)
                say(f"HANDOFF_OK {new_handle}" if ok else f"HANDOFF_FAIL dup err={err}")
            finally:
                lib.close_handle(b_handle)
        elif command == "CLOSE":
            lib.close_handle(handle)
            say("A_CLOSED")
            return


def role_b() -> None:
    line = sys.stdin.readline().strip()
    parts = line.split()
    if len(parts) != 3 or parts[0] != "VALIDATE":
        say("B_BAD_LINE")
        return
    handle = int(parts[1])
    member_pid = int(parts[2])
    active = lib.query_active_processes(handle)
    member = lib.is_process_in_job(member_pid, handle)
    say(f"B_HOLDS active={active} member={member}")
    for line in sys.stdin:
        command = line.strip()
        if command == "CLOSE":
            lib.close_handle(handle)
            say("B_CLOSED")
            # keep running: the later tree death must come from the handle
            # close, not from this process exiting
        elif command == "EXIT":
            return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True, choices=["tree", "a", "b"])
    args = parser.parse_args()
    if args.role == "tree":
        role_tree()
    elif args.role == "a":
        role_a()
    else:
        role_b()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
