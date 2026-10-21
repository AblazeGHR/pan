"""Owned Windows JSON-RPC console probe; never touches another window."""
import ctypes
import json
import os
import sys
from ctypes import wintypes

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
user = ctypes.WinDLL('user32', use_last_error=True)
kernel.GetConsoleWindow.restype = wintypes.HWND
user.IsWindowVisible.argtypes = [wintypes.HWND]
# Record delivery of Windows' actual console-close event with a distinctive
# exit code; no synthetic signal or process termination is used by the host.
@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
def on_console_event(event):
    if event == 2:  # CTRL_CLOSE_EVENT
        os._exit(23)
    return False
kernel.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
assert kernel.SetConsoleCtrlHandler(on_console_event, True)
for line in sys.stdin:
    request = json.loads(line)
    hwnd = kernel.GetConsoleWindow()
    window_pid = wintypes.DWORD()
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
    name = ctypes.create_unicode_buffer(256)
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetClassNameW(hwnd, name, len(name))
    result = ({'protocol_version': 'pan-capability/v1'}
              if request['method'] == 'provider.initialize' else
              {'pid': os.getpid(), 'ppid': os.getppid(), 'hwnd': int(hwnd or 0),
               'visible': bool(user.IsWindowVisible(hwnd)), 'window_pid': window_pid.value,
               'window_class': name.value})
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
