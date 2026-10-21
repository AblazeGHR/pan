"""Windows command boundary: join an owned kill-on-close Job before any user code."""
import ctypes
from ctypes import wintypes
import json
import subprocess
import sys

from packages.core.terminal.guard import JobObjectGuard
from packages.jobs.window import command_window_options


def main():
    # The guard handle is deliberately retained until process exit. Its kernel
    # close kills remaining descendants on success, failure or host timeout.
    guard = JobObjectGuard(kill_on_close=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    guard.assign(kernel.GetCurrentProcess())
    request = json.loads(sys.stdin.readline())
    try:
        return subprocess.run(request['argv'], cwd=request['cwd'],
            stdin=subprocess.DEVNULL, timeout=request['timeout_sec'],
            **command_window_options()).returncode
    finally:
        # No explicit close here: closing a Job containing this helper would
        # terminate it before its actual exit code is delivered to the host.
        # The retained handle closes atomically when Windows retires the helper.
        _ = guard


if __name__ == '__main__':
    sys.exit(main())
