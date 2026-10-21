"""Owned Windows stdio subtree; establish kill-on-close before external code."""
import ctypes
from ctypes import wintypes
import subprocess
import sys

from packages.core.terminal.guard import JobObjectGuard
from packages.jobs.window import command_window_options


def main():
    guard = JobObjectGuard(kill_on_close=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    guard.assign(kernel.GetCurrentProcess())
    # Keep the guard alive until kernel process retirement, including on error.
    result = subprocess.call(sys.argv[1:], stdin=sys.stdin.buffer,
                             stdout=sys.stdout.buffer, stderr=sys.stderr.buffer,
                             **command_window_options())
    _ = guard
    return result


if __name__ == '__main__':
    sys.exit(main())
