"""Reentrant per-profile process lock for authentication state transactions."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import threading
import stat

_guard = threading.Lock()
_locks = {}
_local = threading.local()


def require_private_file(path):
    if os.name != 'nt' and stat.S_IMODE(Path(path).stat().st_mode) != 0o600:
        raise OSError('Authentication storage cannot enforce private file permissions')


@contextmanager
def auth_lock(path):
    from . import supports
    if not supports('auth.persistence'):
        raise OSError('Authentication persistence is unsupported on this platform')
    path = Path(path).resolve()
    identity = str(path)
    with _guard:
        lock = _locks.setdefault(identity, threading.RLock())
    with lock:
        held = getattr(_local, 'held', set())
        if identity in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with _os_lock(path):
            _local.held = held | {identity}
            try:
                yield
            finally:
                _local.held = held


@contextmanager
def _os_lock(path):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        name = 'Local\\PanAuth_' + hashlib.sha256(str(path).casefold().encode()).hexdigest()
        handle = kernel.CreateMutexW(None, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = kernel.WaitForSingleObject(handle, 5000) in (0, 0x80)
        try:
            if not acquired:
                raise OSError('Authentication storage lock unavailable')
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
        return
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        require_private_file(path)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
