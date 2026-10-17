"""Owned, bounded toast diagnostics. Never scans other notification payloads."""
import ctypes
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core import windows_toast

APP = 'Pan.Owned.Toast20261017'
HERE = Path(__file__).resolve().parent


def rows():
    path = Path(os.environ['LOCALAPPDATA']) / 'Microsoft/Windows/Notifications/wpndatabase.db'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as con:
        return con.execute('SELECT n.Tag, n."Group", CAST(n.Payload AS TEXT) FROM Notification n '
                           'JOIN NotificationHandler h ON h.RecordId=n.HandlerId '
                           'WHERE h.PrimaryId=?', (APP,)).fetchall()


def child(mode):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetThreadDesktop.restype = ctypes.c_void_p
    user.GetUserObjectInformationW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                               ctypes.c_ulong, ctypes.c_void_p]
    name = ctypes.create_unicode_buffer(256)
    needed = ctypes.c_ulong()
    handle = user.GetThreadDesktop(ctypes.WinDLL('kernel32').GetCurrentThreadId())
    user.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed))
    print(json.dumps({'mode': mode, 'pid': os.getpid(), 'desktop': name.value,
                     'result': windows_toast.show_toast(
                         'Pan: OWNED DIAGNOSTIC ' + mode, 'Local delivery regression 20261017',
                         tag='owned-' + mode, group='Pan-owned-diag', app_id=APP)}), flush=True)


def main():
    from packages.jobs.desktop import DesktopCommand, HiddenDesktop
    summary = []
    # Python's STARTUPINFO exposes only a subset of native fields: assigning
    # lpDesktop on that object is ignored. Use the native DesktopCommand path.
    for mode in ('hidden', 'token-default', 'combined'):
        argv = [sys.executable, str(Path(__file__).resolve()), mode]
        if mode in ('token-default', 'combined'):
            if mode == 'token-default':
                original = HiddenDesktop.__init__
                def init(self):
                    original(self)
                    self.path = 'Default'
                HiddenDesktop.__init__ = init
            try:
                with (HERE / (mode + '.log')).open('w', encoding='utf-8') as log:
                    with DesktopCommand(argv, str(ROOT), log) as proc:
                        code = proc.wait(timeout=30)
                output = (HERE / (mode + '.log')).read_text(encoding='utf-8')
            finally:
                if mode == 'token-default':
                    HiddenDesktop.__init__ = original
        else:
            startup = subprocess.STARTUPINFO()
            result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                                    startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
                                    timeout=30)
            output, code = result.stdout + result.stderr, result.returncode
        time.sleep(1)
        summary.append({'mode': mode, 'exitcode': code, 'output': output,
                        'ownedTags': [row[0] for row in rows()]})
    (HERE / 'matrix.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    if len(sys.argv) > 1:
        child(sys.argv[1])
    else:
        main()
