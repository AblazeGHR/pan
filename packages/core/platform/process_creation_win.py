"""Popen stdio adapter to the existing native owned-desktop creation path.

Private Windows module. Popen still owns pipe construction, communicate, wait,
and the retained process handle; only its creation edge is replaced.
"""
import ctypes
from ctypes import wintypes as w
import inspect
import os
import subprocess
import sys

_POPEN_EXECUTE_CHILD = subprocess.Popen._execute_child

from packages.core.platform.process_structs_win import _STARTUPINFOW, _PROCESS_INFORMATION
from packages.jobs.cleanup import close_resources, stop_window_guard


def create_owned_process(command, cwd, env, startup, flags, guard, *, hidden, handle_list=()):
    """Existing DesktopCommand creation algorithm, also used by ProcessOwner.

    Never fall back to post-create assignment. Membership is proven while the
    initial thread is suspended. All partial native resources remain owned here.
    """
    from packages.jobs.owned_process import job_startup_info, EXTENDED_STARTUPINFO_PRESENT
    from packages.core.platform.restricted_token_win import HiddenJobToken
    from packages.jobs.cleanup import CleanupStack
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.ResumeThread.argtypes = [w.HANDLE]
    kernel.ResumeThread.restype = w.DWORD
    kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    kernel.TerminateProcess.restype = w.BOOL
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    process = thread = 0
    block = None
    if env is not None:
        for key, value in env.items():
            if not key or '\0' in key or '\0' in value or '=' in key[1:]:
                raise ValueError('illegal environment variable')
        block = ctypes.create_unicode_buffer(''.join(
            key + '=' + value + '\0' for key, value in sorted(env.items(), key=lambda item: item[0].upper())) + '\0')
        flags |= 0x400  # CREATE_UNICODE_ENVIRONMENT
    sys.audit('subprocess.Popen', None, command, cwd, env)
    try:
        with job_startup_info(startup, guard, handle_list=handle_list) as extended:
            flags |= 0x4 | EXTENDED_STARTUPINFO_PRESENT
            if hidden:
                token = HiddenJobToken()
                with CleanupStack() as cleanup:
                    cleanup.callback(token.close)
                    process, thread, pid = token.create_process(command, cwd, block, extended, flags)
            else:
                kernel.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p,
                    ctypes.c_void_p, w.BOOL, w.DWORD, ctypes.c_void_p, w.LPCWSTR,
                    ctypes.c_void_p, ctypes.POINTER(_PROCESS_INFORMATION)]
                kernel.CreateProcessW.restype = w.BOOL
                info = _PROCESS_INFORMATION()
                if not kernel.CreateProcessW(None, ctypes.create_unicode_buffer(command), None, None,
                        True, flags, ctypes.cast(block, ctypes.c_void_p) if block is not None else None,
                        cwd, ctypes.byref(extended), ctypes.byref(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                process, thread, pid = int(info.hProcess), int(info.hThread), int(info.dwProcessId)
        if not guard.is_member(process):
            raise RuntimeError('creation-time owned Job membership unproven')
        if kernel.ResumeThread(thread) != 1:
            raise RuntimeError('owned command resume count was not exactly one')
        return process, thread, pid
    except BaseException:
        # These are exact handles returned by our own CreateProcess, never PIDs.
        with CleanupStack() as cleanup:
            for handle in (thread, process):
                if handle:
                    def close(handle=handle):
                        if not kernel.CloseHandle(handle):
                            raise ctypes.WinError(ctypes.get_last_error())
                    cleanup.callback(close)
            if process:
                def terminate():
                    if not kernel.TerminateProcess(process, 1):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if kernel.WaitForSingleObject(process, 5000) != 0:
                        raise RuntimeError('failed creation child termination unconfirmed')
                cleanup.callback(terminate)
            raise


class OwnedPopen(subprocess.Popen):
    def __init__(self, args, *, guard, hidden, **kwargs):
        self.owned_guard = guard
        self.desktop = None
        self.window_guard = None
        self.hidden = hidden
        try:
            if hidden:
                from packages.core.platform.desktop_win import HiddenDesktop
                from packages.jobs.window_guard import OwnedWindowGuard, descendant_pids, owned_descendant_handle
                self.desktop = HiddenDesktop()
                self.window_guard = OwnedWindowGuard(
                    lambda: descendant_pids(self.pid) if getattr(self, 'pid', None) else (),
                    owned_handle=lambda pid: owned_descendant_handle(
                        getattr(self, 'pid', None), int(self._handle) if getattr(self, '_handle', None) else 0, pid))
                if not self.window_guard.start():
                    raise OSError('owned window guard failed before command creation')
            super().__init__(args, **kwargs)
        except BaseException:
            from packages.jobs.cleanup import CleanupStack
            with CleanupStack() as cleanup:
                cleanup.callback(self.close_owned_resources)
                def stop_partial_tree():
                    _, remaining = guard.terminate_tree(None, timeout=5.0)
                    if remaining:
                        raise RuntimeError('partial owned creation tree cleanup unconfirmed')
                cleanup.callback(stop_partial_tree)
                raise

    def _execute_child(self, *args, **kwargs):
        # Bind by name for CPython 3.12/3.14's private callback, fail explicitly
        # if its shape changes rather than silently dropping a caller option.
        values = inspect.signature(_POPEN_EXECUTE_CHILD).bind(self, *args, **kwargs).arguments
        if values['shell'] or values['executable'] or values['preexec_fn'] or values['pass_fds']:
            raise ValueError('unsupported private owned Popen option')
        p2cr, p2cw, c2pr, c2pw, errr, errw = (values[k] for k in
            ('p2cread', 'p2cwrite', 'c2pread', 'c2pwrite', 'errread', 'errwrite'))
        try:
            startup = _STARTUPINFOW()
            startup.cb = ctypes.sizeof(startup)
            startup.dwFlags = 0x101
            startup.wShowWindow = 0 if self.hidden else 1
            startup.lpDesktop = self.desktop.path if self.desktop else None
            startup.hStdInput, startup.hStdOutput, startup.hStdError = p2cr, c2pw, errw
            inherited = self._filter_handle_list([int(p2cr), int(c2pw), int(errw)])
            process, thread, pid = create_owned_process(
                subprocess.list2cmdline(values['args']),
                os.fsdecode(values['cwd']) if values['cwd'] is not None else None,
                values['env'], startup, values['creationflags'], self.owned_guard,
                hidden=self.hidden, handle_list=inherited)
            self._child_created = True
            self._handle = subprocess.Handle(process)
            self.pid = pid
            import _winapi
            _winapi.CloseHandle(thread)
        finally:
            self._close_pipe_fds(p2cr, p2cw, c2pr, c2pw, errr, errw)

    def close_owned_resources(self):
        def close_process():
            handle = getattr(self, '_handle', None)
            if handle is not None:
                self.wait(timeout=5.0)
                handle.Close()
        close_resources(
            close_process,
            lambda: stop_window_guard(self.window_guard) if self.window_guard is not None else None,
            lambda: self.desktop.close() if self.desktop is not None else None)
