"""Observe the hook thread's actual desktop without creating any window."""
import argparse
import ctypes
from ctypes import wintypes as w
import importlib.util
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('guard_under_test', args.source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    u = ctypes.WinDLL('user32', use_last_error=True)
    k = ctypes.WinDLL('kernel32')
    u.GetThreadDesktop.argtypes = [w.DWORD]
    u.GetThreadDesktop.restype = w.HANDLE
    u.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    u.OpenInputDesktop.restype = w.HANDLE
    u.CloseDesktop.argtypes = [w.HANDLE]
    u.GetUserObjectInformationW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]

    def name(handle):
        value = ctypes.create_unicode_buffer(256)
        needed = w.DWORD()
        assert u.GetUserObjectInformationW(handle, 2, value, ctypes.sizeof(value), ctypes.byref(needed))
        return value.value

    guard = module.OwnedWindowGuard(lambda: frozenset())
    assert guard.start()
    desktop = u.OpenInputDesktop(0, False, 0x41)
    assert desktop
    try:
        facts = dict(callerDesktop=name(u.GetThreadDesktop(k.GetCurrentThreadId())),
                     hookDesktop=name(u.GetThreadDesktop(guard._thread_id)), inputDesktop=name(desktop))
    finally:
        assert u.CloseDesktop(desktop)
        assert guard.stop()
    Path(args.out).write_text(json.dumps(facts), encoding='utf-8')


if __name__ == '__main__':
    main()
