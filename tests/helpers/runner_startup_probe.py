"""Test-only sitecustomize payload: observe our own runner before its imports."""
import atexit
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import sys

modules = ('packages.core.background_runner', 'packages.core.background_jobs', 'packages.jobs.independent_runner')
if any(module in sys.orig_argv for module in modules):
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    u = ctypes.WinDLL('user32', use_last_error=True)
    k.GetConsoleWindow.restype = w.HWND
    u.GetThreadDesktop.argtypes = [w.DWORD]; u.GetThreadDesktop.restype = w.HANDLE
    u.GetUserObjectInformationW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    desktop = ctypes.create_unicode_buffer(256); needed = w.DWORD()
    u.GetUserObjectInformationW(u.GetThreadDesktop(k.GetCurrentThreadId()), 2, desktop, ctypes.sizeof(desktop), ctypes.byref(needed))
    members = (w.DWORD * 32)()
    count = k.GetConsoleProcessList(members, 32)
    from packages.core.terminal.identity import read_creation_filetime, open_process_for_probe, close_handle_checked
    k.GetCurrentProcess.restype = w.HANDLE
    evidence = dict(pid=os.getpid(), parent=os.getppid(), filetime=str(read_creation_filetime(k.GetCurrentProcess())), console=int(k.GetConsoleWindow() or 0), consoleClients=list(members)[:min(count, 32)], desktop=desktop.value, executable=sys.executable, prefix=sys.prefix, argv=sys.orig_argv)
    parent_handle = open_process_for_probe(os.getppid())
    try:
        evidence['parentFiletime'] = str(read_creation_filetime(parent_handle))
    finally:
        close_handle_checked(parent_handle)
    destination = Path(OUTPUT_DIRECTORY) / (str(os.getpid()) + '.json')
    destination.write_text(json.dumps(evidence), encoding='utf-8')
    @atexit.register
    def final_modules():
        entry = sys.modules['__main__']
        evidence['entryModule'] = dict(name=entry.__spec__.name, path=entry.__file__, sha256=hashlib.sha256(Path(entry.__file__).read_bytes()).hexdigest())
        evidence['loadedModules'] = {name: dict(path=module.__file__, sha256=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()) for name in modules if (module := sys.modules.get(name)) is not None and getattr(module, '__file__', None)}
        destination.write_text(json.dumps(evidence), encoding='utf-8')
