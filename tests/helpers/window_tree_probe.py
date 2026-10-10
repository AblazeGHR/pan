"""Owned Windows console probe; every controlled edge applies the public policy."""
from __future__ import annotations

import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from packages.jobs.window import command_window_options
from packages.core.terminal.identity import read_creation_filetime, kernel32


def snapshot():
    kernel = kernel32()
    kernel.GetConsoleWindow.restype = wintypes.HWND
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    hwnd = kernel.GetConsoleWindow()
    return {"pid": __import__("os").getpid(),
            "filetime": str(read_creation_filetime(kernel.GetCurrentProcess())),
            "consoleHwnd": int(hwnd or 0), "visible": bool(user.IsWindowVisible(hwnd))}


def main():
    hidden, depth = sys.argv[1] == "hidden", int(sys.argv[2])
    # Give the owned console host time to finish creating its window.
    time.sleep(0.2)
    evidence = snapshot()
    evidence["depth"] = depth
    if depth:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                  sys.argv[1], str(depth - 1)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, **command_window_options(hidden))
        handle = int(child._handle)
        filetime = read_creation_filetime(handle)
        assert filetime is not None
        try:
            output, error = child.communicate(timeout=15)
            assert child.returncode == 0, error.decode("utf-8", "replace")
            evidence["child"] = json.loads(output)
            # venv redirectors may report an actual descendant PID. Retain both.
            evidence["childLaunch"] = {"pid": child.pid, "filetime": str(filetime),
                                      "exitCode": child.returncode}
        finally:
            if child.poll() is None:
                kernel = kernel32()
                assert kernel.GetProcessId(wintypes.HANDLE(handle)) == child.pid
                assert read_creation_filetime(handle) == filetime
                assert kernel.TerminateProcess(wintypes.HANDLE(handle), 1)
                child.wait(timeout=5)
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    main()
