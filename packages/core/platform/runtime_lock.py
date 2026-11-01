"""Process lifetime exclusion for a local runtime authority.

The lock file is never deleted: unlinking a live lock would create two owners.
"""
import os
import errno


class RuntimeLockBusy(BlockingIOError):
    """Confirmed native nonblocking lock contention, never an IO failure."""


def _windows_region(stream, *, unlock=False):
    # LockFileEx preserves GetLastError, unlike CRT errno=EACCES, which cannot
    # distinguish a lock violation from unrelated access failures.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD),
                    ('hEvent', wintypes.HANDLE)]

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    overlap = Overlapped()
    handle = msvcrt.get_osfhandle(stream.fileno())
    if unlock:
        call = api.UnlockFileEx
        call.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                         wintypes.DWORD, ctypes.POINTER(Overlapped)]
        call.restype = wintypes.BOOL
        ok = call(handle, 0, 1, 0, ctypes.byref(overlap))
    else:
        call = api.LockFileEx
        call.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                         wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
        call.restype = wintypes.BOOL
        ok = call(handle, 3, 0, 1, 0, ctypes.byref(overlap))  # EXCLUSIVE | FAIL_IMMEDIATELY
    if not ok:
        code = ctypes.get_last_error()
        if not unlock and code == 33:  # ERROR_LOCK_VIOLATION, not ACCESS_DENIED(5)
            raise RuntimeLockBusy(errno.EAGAIN, 'Runtime lock is held by another handle')
        raise ctypes.WinError(code)


def _posix_lock(stream):
    import fcntl
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        # Only errors from flock itself; mkdir/open errors never enter here.
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise RuntimeLockBusy(exc.errno, 'Runtime lock is held by another handle') from exc
        raise


class RuntimeLock:
    def __init__(self, path):
        self.path = path
        self.file = None

    def acquire(self):
        from packages.core.platform import supports
        if not supports("job.runtimeOwner"):
            raise NotImplementedError("job.runtimeOwner unsupported on this platform")
        if self.file is not None:
            raise RuntimeError("runtime lock already held")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                _windows_region(stream)
            elif os.name == "posix":
                _posix_lock(stream)
            else:
                raise RuntimeError("runtime ownership unsupported on this platform")
        except BaseException:
            stream.close()
            raise
        self.file = stream

    def release(self):
        if self.file is None:
            return
        try:
            if os.name == "nt":
                _windows_region(self.file, unlock=True)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None
