"""Private Windows desktop for an owned command tree (not a security sandbox).

Never switches the input desktop. The runner retains the non-inheritable handle
through command execution (and independent Job Object cleanup). Descendants'
own UI inherits the parent's desktop. HiddenJobToken suppresses the measured
ordinary console delegation path. Explicit desktop selection, a modified child
DACL and external brokers remain outside this inherited-desktop guarantee.
"""
import ctypes
from ctypes import wintypes
import secrets
import os
import subprocess
from pathlib import Path


class HiddenDesktop:
    def __init__(self):
        self.name = "PanJob_" + secrets.token_hex(16)
        self.handle = 0
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        self.user.CreateDesktopW.restype = wintypes.HANDLE
        self.user.CloseDesktop.argtypes = [wintypes.HANDLE]
        self.user.CloseDesktop.restype = wintypes.BOOL
        # Desktop access only, no SWITCHDESKTOP. NULL security attributes make
        # this runner handle non-inheritable; lpDesktop selects the task desktop.
        self.handle = int(self.user.CreateDesktopW(self.name, None, None, 0,
                                                  0x000F00FF, None) or 0)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        self.path = self.name

    def close(self):
        if self.handle:
            if not self.user.CloseDesktop(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = 0


class DesktopCommand:
    """Legacy runner's retained-handle command on an explicit private desktop.

    This preserves its existing registry/cancellation semantics. Independent
    jobs retain their stronger suspended-create/Job Object lifecycle instead.
    """
    def __init__(self, argv, cwd, log, *, shell=False):
        import msvcrt
        from packages.jobs.independent_runner import _STARTUPINFOW, _PROCESS_INFORMATION
        from packages.jobs.window_guard import OwnedWindowGuard, descendant_pids, owned_descendant_handle
        self.desktop = HiddenDesktop()
        self.handle = 0
        self.pid = None
        self.window_guard = OwnedWindowGuard(
            lambda: descendant_pids(self.pid) if self.pid else (),
            owned_handle=lambda pid: owned_descendant_handle(self.pid, self.handle, pid))
        # Started before creation so a window shown immediately by the root is seen.
        if not self.window_guard.start():
            self.window_guard.stop()
            self.desktop.close()
            raise OSError("owned window guard failed before legacy command creation")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel = self.kernel
        kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
            ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION)]
        kernel.CreateProcessW.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        command = (f'{subprocess.list2cmdline([os.environ.get("ComSpec") or str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe")])} /c "{argv}"'
                   if shell else subprocess.list2cmdline(argv))
        try:
            with open("NUL", "rb") as null_in:
                handles = [msvcrt.get_osfhandle(null_in.fileno()), msvcrt.get_osfhandle(log.fileno())]
                previous = [os.get_handle_inheritable(h) for h in handles]
                try:
                    for h in handles: os.set_handle_inheritable(h, True)
                    startup = _STARTUPINFOW()
                    startup.cb = ctypes.sizeof(startup)
                    startup.lpDesktop = self.desktop.path
                    startup.dwFlags = 0x101
                    startup.wShowWindow = 0
                    startup.hStdInput, startup.hStdOutput, startup.hStdError = handles[0], handles[1], handles[1]
                    from packages.jobs.restricted_token import HiddenJobToken
                    hidden_token = HiddenJobToken()
                    try:
                        process, thread, pid = hidden_token.create_process(
                            command, cwd, None, startup, 0x08000004)
                    finally:
                        hidden_token.close()
                    self.handle, self.pid = process, pid
                    try:
                        if kernel.ResumeThread(thread) != 1:
                            raise RuntimeError("legacy command resume count was not exactly one")
                    finally:
                        if not kernel.CloseHandle(thread): raise ctypes.WinError(ctypes.get_last_error())
                finally:
                    for h, flag in zip(handles, previous): os.set_handle_inheritable(h, flag)
        except BaseException:
            if self.handle:
                kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
                kernel.TerminateProcess(self.handle, 1)
            self.close()
            raise

    def wait(self, timeout=None):
        result = self.kernel.WaitForSingleObject(self.handle, 0xFFFFFFFF if timeout is None else int(timeout * 1000))
        if result == 0x102: raise subprocess.TimeoutExpired("desktop command", timeout)
        if result != 0: raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def close(self):
        try:
            if self.handle:
                if not self.kernel.CloseHandle(self.handle): raise ctypes.WinError(ctypes.get_last_error())
                self.handle = 0
        finally:
            self.window_guard.stop()
            self.desktop.close()

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
