"""Stdlib-only owned Windows tree and runner; all state under explicit tmp root."""
from contextlib import closing
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def publish(path, payload):
    """Publish complete fixture identity before readers observe the filename."""
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temporary.write_text(json.dumps(payload), encoding='utf-8')
    os.replace(temporary, path)


def desktop():
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentThreadId.restype = w.DWORD
    user.GetThreadDesktop.argtypes = [w.DWORD]
    user.GetThreadDesktop.restype = w.HANDLE
    user.GetUserObjectInformationW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    user.GetUserObjectInformationW.restype = w.BOOL
    buf = ctypes.create_unicode_buffer(256)
    needed = w.DWORD()
    assert user.GetUserObjectInformationW(user.GetThreadDesktop(kernel.GetCurrentThreadId()), 2, buf, ctypes.sizeof(buf), ctypes.byref(needed))
    return buf.value


def filetime():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    stamps = [w.FILETIME() for _ in range(4)]
    assert kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(s) for s in stamps))
    return stamps[0].dwHighDateTime << 32 | stamps[0].dwLowDateTime


def until(check):
    end = time.monotonic() + 20
    while not check():
        if time.monotonic() >= end:
            raise TimeoutError('owned fixture gate timed out')
        time.sleep(.01)


def main():
    mode, root = sys.argv[1], Path(sys.argv[2])
    if mode == 'tree':
        depth = int(sys.argv[3])
        child = None
        if depth:
            child = subprocess.Popen([sys.executable, __file__, 'tree', str(root), str(depth-1)], creationflags=0x08000000)
        publish(root / f'{depth}.json', {'pid': os.getpid(), 'birth': filetime(), 'desktop': desktop()})
        until(lambda: (root / 'release').exists())
        if child:
            assert child.wait(10) == 0
    elif mode == 'creation-runner':
        from packages.core.platform.process import WindowsProcessOwner
        from packages.core.platform.desktop_win import HiddenDesktop
        from packages.core.platform.restricted_token_win import HiddenJobToken
        from packages.core.terminal.identity import read_creation_filetime
        names = []
        original_desktop = HiddenDesktop.__init__
        def capture(obj):
            original_desktop(obj)
            names.append(obj.name)
        HiddenDesktop.__init__ = capture
        original_create = HiddenJobToken.create_process
        def gate(token, *args):
            process, thread, pid = original_create(token, *args)
            publish(root / 'suspended.json', {
                'pid': pid, 'birth': read_creation_filetime(process), 'desktop': names[0]})
            until(lambda: (root / 'release').exists())
            return process, thread, pid
        HiddenJobToken.create_process = gate
        owned = WindowsProcessOwner().spawn([sys.executable, '-c',
            'from pathlib import Path; Path('+repr(str(root / 'must-not-execute'))+').touch()'])
        owned.close()
    elif mode == 'runner':
        from packages.core.platform.process import WindowsProcessOwner
        owner = WindowsProcessOwner()
        with closing(owner.spawn([sys.executable, __file__, 'tree', str(root), '2'])) as owned:
            until(lambda: (root / '0.json').exists())
            publish(root / 'runner.json', {'pid': owned.pid, 'birth': owned.identity.birth, 'desktop': owned.popen.desktop.name})
            until(lambda: (root / 'release').exists())
            assert owner.wait(owned, timeout=10).exited
    else:
        raise ValueError(mode)


if __name__ == '__main__':
    main()
