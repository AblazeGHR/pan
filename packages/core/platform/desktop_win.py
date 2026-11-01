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
from packages.jobs.cleanup import CleanupStack, close_resources, stop_window_guard
from pathlib import Path


class HiddenDesktop:
    def __init__(self):
        from packages.core.platform import supports
        from packages.core.platform.process import UnsupportedPlatformError
        if not supports("process.hiddenDesktop"):
            raise UnsupportedPlatformError("process.hiddenDesktop unsupported on this platform")
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

    Hidden commands enter a kill-on-close Job atomically at process creation.
    Runner death contains descendants even before Python receives their handles.
    """
    def __init__(self, argv, cwd, log, *, shell=False):
        from packages.core.platform import supports
        from packages.core.platform.process import UnsupportedPlatformError
        if not supports("process.hiddenDesktop"):
            raise UnsupportedPlatformError("process.hiddenDesktop unsupported on this platform")
        import msvcrt
        from packages.core.platform.process_structs_win import _STARTUPINFOW
        from packages.jobs.window_guard import OwnedWindowGuard, descendant_pids, owned_descendant_handle
        self.desktop = HiddenDesktop()
        self.handle = 0
        self.thread_handle = 0
        self.pid = None
        self.window_guard = None
        self.job_guard = None
        try:
            self.window_guard = OwnedWindowGuard(
                lambda: descendant_pids(self.pid) if self.pid else (),
                owned_handle=lambda pid: owned_descendant_handle(self.pid, self.handle, pid))
            # Started before creation so a window shown immediately by the root is seen.
            if not self.window_guard.start():
                raise OSError("owned window guard failed before legacy command creation")
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel = self.kernel
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetExitCodeProcess.restype = wintypes.BOOL
            command = (f'{subprocess.list2cmdline([os.environ.get("ComSpec") or str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe")])} /c "{argv}"'
                       if shell else subprocess.list2cmdline(argv))
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
                    from packages.core.terminal.guard import JobObjectGuard
                    from packages.core.platform.process_creation_win import create_owned_process
                    self.job_guard = JobObjectGuard(kill_on_close=True)
                    process, thread, pid = create_owned_process(
                        command, cwd, None, startup, 0x08000000,
                        self.job_guard, hidden=True, handle_list=handles)
                    self.handle, self.pid = process, pid
                    self.thread_handle = thread
                finally:
                    for h, flag in zip(handles, previous): os.set_handle_inheritable(h, flag)
        except BaseException:
            with CleanupStack() as cleanup:
                cleanup.callback(self.close)
                if self.handle:
                    self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
                    def terminate():
                        if not self.kernel.TerminateProcess(self.handle, 1):
                            raise ctypes.WinError(ctypes.get_last_error())
                    cleanup.callback(terminate)
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
        def close_process():
            if self.handle:
                if not self.kernel.CloseHandle(self.handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handle = 0
        def close_thread():
            if self.thread_handle:
                if not self.kernel.CloseHandle(self.thread_handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.thread_handle = 0
        def close_job():
            if self.job_guard is not None and self.job_guard.handle_open:
                with CleanupStack() as cleanup:
                    cleanup.callback(self.job_guard.close)
                    if self.pid is not None:
                        _, remaining = self.job_guard.terminate_tree(self.pid, timeout=5.0)
                        if remaining:
                            raise RuntimeError("owned legacy tree termination unconfirmed")
        close_resources(
            close_job,
            close_process,
            close_thread,
            lambda: stop_window_guard(self.window_guard) if self.window_guard is not None else None,
            self.desktop.close)

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
