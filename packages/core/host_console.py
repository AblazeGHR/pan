"""The Pan host's interactive Windows console, independent of durable log handles.

Never hides a parent terminal. Managed launches own a separate classic console;
hidden launches allocate one only on demand. Hide retains it (and its buffer),
so no FreeConsole/blocked-stdin-reader race is possible.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading


class ConsoleError(RuntimeError):
    pass


class Output:
    """Keep logger stream references stable across console switches."""
    def __init__(self, manager, durable):
        self.manager, self.durable = manager, durable

    def write(self, value):
        # The file is authoritative; a broken/closed console cannot stop Pan.
        written = self.durable.write(value)
        self.durable.flush()
        with self.manager.lock:
            if self.manager.output is not None:
                try:
                    self.manager.output.write(value)
                    self.manager.output.flush()
                except (OSError, ValueError):
                    pass
        return written

    def flush(self):
        self.durable.flush()

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self.durable, name)


class HostConsole:
    def __init__(self):
        self.lock = threading.RLock()
        self.managed = os.environ.get("PAN_MANAGED_CONSOLE") == "1"
        self.output = None
        self.input = None
        self.reader_started = False
        if os.name == "nt":
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.user = ctypes.WinDLL("user32", use_last_error=True)
            self.kernel.GetConsoleWindow.restype = wintypes.HWND
            self.kernel.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
            self.user.IsWindowVisible.argtypes = [wintypes.HWND]
            self.user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

    def snapshot(self):
        if os.name != "nt":
            return {"state": "unsupported", "managed": False}
        hwnd = self.kernel.GetConsoleWindow()
        return {"state": ("visible" if self.user.IsWindowVisible(hwnd) else "hidden")
                if hwnd else "absent", "hwnd": int(hwnd or 0), "managed": self.managed,
                "pid": os.getpid(), "interactive": self.input is not None}

    def _exclusive(self):
        from ctypes import wintypes
        pids = (wintypes.DWORD * 64)()
        count = self.kernel.GetConsoleProcessList(pids, len(pids))
        return count == 1 and pids[0] == os.getpid()

    def _bind(self):
        self.kernel.SetConsoleOutputCP(65001)
        self.kernel.SetConsoleCP(65001)
        if self.output is None:
            self.output = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        if self.input is None:
            self.input = open("CONIN$", "r", encoding="utf-8", errors="replace")
        # Rebind native standard handles, while Python stdout/stderr and logger
        # references continue to use Output + the original durable file handles.
        import msvcrt
        self.kernel.SetStdHandle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
        for number, stream in ((-10, self.input), (-11, self.output), (-12, self.output)):
            if not self.kernel.SetStdHandle(number & 0xffffffff, msvcrt.get_osfhandle(stream.fileno())):
                raise ctypes.WinError(ctypes.get_last_error())
        sys.stdin = self.input

    def change(self, action):
        if os.name != "nt":
            raise ConsoleError("Console control is available only on Windows")
        with self.lock:
            if not self.managed:
                raise ConsoleError("Foreground/shared console is unmanaged; refusing to alter the parent terminal")
            hwnd = self.kernel.GetConsoleWindow()
            if action == "show" and not hwnd:
                if not self.kernel.AllocConsole():
                    raise ConsoleError(f"AllocConsole failed: {ctypes.WinError(ctypes.get_last_error())}")
                hwnd = self.kernel.GetConsoleWindow()
                self.kernel.SetConsoleTitleW(f"Pan host console - PID {os.getpid()}")
            if not hwnd:
                return self.snapshot()  # hide of an absent console is idempotent
            if not self._exclusive():
                raise ConsoleError("Console is shared; refusing to show/hide another process's terminal")
            if action == "show":
                try:
                    self._bind()
                except OSError as exc:
                    raise ConsoleError(f"Console stdio binding failed: {exc}") from exc
            self.user.ShowWindow(hwnd, 5 if action == "show" else 0)
            visible = bool(self.user.IsWindowVisible(hwnd))
            if visible != (action == "show"):
                raise ConsoleError("Console visibility could not be confirmed (possibly a pseudoconsole)")
            return self.snapshot()

    def initialize(self):
        if os.name != "nt" or not self.managed:
            return
        sys.stdout = Output(self, sys.stdout)
        sys.stderr = Output(self, sys.stderr)
        if self.kernel.GetConsoleWindow():
            self._bind()
        else:
            # CREATE_NO_WINDOW on Windows can leave a windowless console
            # association: AllocConsole then returns ERROR_ACCESS_DENIED even
            # though GetConsoleWindow is NULL. Detach once, before any reader
            # starts. File-backed Python streams remain valid. Later hide/show
            # retains the allocated console and never detaches a live reader.
            if not self.kernel.FreeConsole():
                raise ConsoleError(f"Initial console detach failed: {ctypes.WinError(ctypes.get_last_error())}")

    def start_input(self, port, strategy):
        if os.name != "nt" or not self.managed:
            from .console_exit import start_console_exit
            return start_console_exit(port, strategy)
        if self.reader_started:
            return
        self.reader_started = True
        import msvcrt
        from .console_exit import ConsoleExit
        from .cli import request
        console = ConsoleExit(lambda payload: request(port, "/api/main/exit", payload), strategy)
        def read():
            line = ""
            pause = threading.Event()
            while True:
                try:
                    submitted = None
                    with self.lock:
                        if self.input is not None and msvcrt.kbhit():
                            value = msvcrt.getwch()
                            if value in ("\x00", "\xe0"):
                                msvcrt.getwch()
                            elif value in ("\r", "\n"):
                                print()
                                submitted = line
                                line = ""
                            elif value == "\b":
                                if line:
                                    line = line[:-1]
                                    print("\b \b", end="", flush=True)
                            else:
                                line += value
                                print(value, end="", flush=True)
                    if submitted is not None:
                        # Menu choices can issue HTTP; never hold the console
                        # lock while waiting for the event loop's response.
                        console.line(submitted)
                    console.tick()
                except (OSError, ValueError):
                    # No console/closed input is not host shutdown authority.
                    pass
                pause.wait(0.05)
        threading.Thread(target=read, name="pan-managed-console-input", daemon=True).start()
        print("Pan console: q + Enter follows Exit policy; qqq + Enter preserves Illegal. hide retains this console.")


console = HostConsole()
