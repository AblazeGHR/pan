"""Preserve MA's exact owned failed notification before precise cleanup."""
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

HERE = Path(__file__).resolve().parent
db = Path(os.environ['LOCALAPPDATA']) / 'Microsoft/Windows/Notifications/wpndatabase.db'
with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as connection:
    connection.row_factory = sqlite3.Row
    rows = [dict(row) for row in connection.execute(
        'SELECT n.Tag,n.[Group],n.ArrivalTime,CAST(n.Payload AS TEXT) AS Payload,'
        'h.PrimaryId,h.HandlerType,h.WNSId FROM Notification n '
        'JOIN NotificationHandler h ON n.HandlerId=h.RecordId '
        'WHERE h.PrimaryId=? AND n.Tag=? AND n.[Group]=?',
        ('Pan.Desktop.Notifications', 'owned-visible-20261017', 'Pan-owned-visible'))]
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
kernel.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
session = wintypes.DWORD()
if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
    raise ctypes.WinError(ctypes.get_last_error())
user = ctypes.WinDLL('user32', use_last_error=True)
user.GetThreadDesktop.restype = wintypes.HANDLE
user.GetProcessWindowStation.restype = wintypes.HANDLE
user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                         ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
def object_name(handle):
    name = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    if not user.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    return name.value
result = {'recordedAt': datetime.now(timezone.utc).isoformat(),
          'helperEvidence': json.loads((HERE / 'visible-send.json').read_text(encoding='utf-8')),
          'humanObservation': {'banner': False, 'notificationCenterPanel': False,
              'reportedBy': 'MA ses_8110c6ece332c2b7 relaying current user observation',
              'userQuote': '没有，什么也没有', 'sendLocalTime': '2026-10-17T23:42:55+08:00'},
          'exactOwnedRows': rows, 'observerProcess': {'pid': os.getpid(),
              'sessionId': session.value, 'activeConsoleSessionId': kernel.WTSGetActiveConsoleSessionId(),
              'desktop': object_name(user.GetThreadDesktop(kernel.GetCurrentThreadId())),
              'windowStation': object_name(user.GetProcessWindowStation())}}
(HERE / 'visible-failure.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
