"""Read only the already identity-verified owned server's thread desktops."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import psutil
from owned_runtime import verified_identity

HERE = Path(__file__).resolve().parent
runtime = HERE / 'runtime-click-20261017-native1'
identity = verified_identity(runtime)
user = ctypes.WinDLL('user32', use_last_error=True)
user.GetThreadDesktop.argtypes = [wintypes.DWORD]
user.GetThreadDesktop.restype = wintypes.HANDLE
user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int,
    ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
desktops = []
for thread in psutil.Process(identity['pid']).threads():
    handle = user.GetThreadDesktop(thread.id)
    name = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    if handle and user.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
        desktops.append({'thread': thread.id, 'desktop': name.value})
    else:
        desktops.append({'thread': thread.id, 'error': ctypes.get_last_error()})
result = {'pid': identity['pid'], 'jobId': 'job_fae467053d20b479dfda26f8',
          'hideWindow': True, 'threadDesktops': desktops,
          'scope': 'owned runtime PID after command/FILETIME/listener verification'}
(HERE / 'runtime-context.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
