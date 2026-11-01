"""Only native nonblocking lock contention can become an owner-busy state."""
import errno
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from packages.jobs.runtime_owner import OwnerFence, OwnershipConflict


@pytest.mark.parametrize('number', [errno.EACCES, errno.ENOSPC, errno.EIO])
def test_lock_open_errors_are_not_ownership_conflicts(tmp_path, monkeypatch, number):
    owner = OwnerFence(tmp_path)
    failure = OSError(number, 'owned fixture open failure')
    original = Path.open

    def broken(path, *args, **kwargs):
        if path == owner.lock.path:
            raise failure
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', broken)
    with pytest.raises(OSError) as raised:
        owner.claim(0)
    assert raised.value is failure
    assert not isinstance(raised.value, OwnershipConflict)
    assert owner.lock.file is None


@pytest.mark.parametrize('number,busy', [(errno.EACCES, True), (errno.EAGAIN, True), (errno.EIO, False), (errno.ENOSPC, False)])
def test_posix_native_error_classification_only_at_flock(monkeypatch, number, busy):
    from packages.core.platform.runtime_lock import _posix_lock, RuntimeLockBusy
    failure = OSError(number, 'fixture flock result')
    def failed(*args):
        raise failure
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(flock=failed, LOCK_EX=2, LOCK_NB=4))
    with pytest.raises(OSError) as raised:
        _posix_lock(SimpleNamespace(fileno=lambda: 17))
    assert isinstance(raised.value, RuntimeLockBusy) is busy
    if not busy:
        assert raised.value is failure


@pytest.mark.parametrize('code,unlock,busy', [(33, False, True), (5, False, False), (112, False, False), (33, True, False)])
def test_windows_native_error_classification_does_not_use_crt_eacces(monkeypatch, code, unlock, busy):
    import ctypes
    from packages.core.platform.runtime_lock import _windows_region, RuntimeLockBusy
    class FailedCall:
        def __call__(self, *args):
            return False
    native = SimpleNamespace(LockFileEx=FailedCall(), UnlockFileEx=FailedCall())
    failure = OSError(errno.EACCES, 'original Win32 error ' + str(code))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: native, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: code, raising=False)
    monkeypatch.setattr(ctypes, 'WinError', lambda code: failure, raising=False)
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(get_osfhandle=lambda fd: fd))
    with pytest.raises(OSError) as raised:
        _windows_region(SimpleNamespace(fileno=lambda: 17), unlock=unlock)
    assert isinstance(raised.value, RuntimeLockBusy) is busy
    if not busy:
        assert raised.value is failure


def test_noncontention_lock_io_error_preserves_original_exception(tmp_path, monkeypatch):
    owner = OwnerFence(tmp_path)
    failure = OSError(errno.ENOSPC, 'owned fixture write/disk failure')
    def broken():
        raise failure
    monkeypatch.setattr(owner.lock, 'acquire', broken)
    with pytest.raises(OSError) as raised:
        owner.claim(0)
    assert raised.value is failure
    assert owner.lock.file is None
