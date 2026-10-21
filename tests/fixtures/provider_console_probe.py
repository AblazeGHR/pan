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
def desktop_windows():
    # Observe only windows belonging to the owned provider tree. EnumWindows
    # can also expose session windows; never assert on or mutate those.
    from packages.jobs.window_guard import descendant_pids
    owned = set(descendant_pids(int(os.environ['PAN_TEST_PROVIDER_HOST_PID'])))
    owned.discard(int(os.environ['PAN_TEST_PROVIDER_HOST_PID']))
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    @callback_type
    def visit(hwnd, _):
        name = ctypes.create_unicode_buffer(256)
        user.GetClassNameW(hwnd, name, len(name))
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in owned and user.IsWindowVisible(hwnd) and name.value != 'PseudoConsoleWindow':
            found.append({'hwnd': int(hwnd), 'class': name.value})
        return True
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    user.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user.GetThreadDesktop.restype = wintypes.HANDLE
    user.EnumDesktopWindows.argtypes = [wintypes.HANDLE, callback_type, wintypes.LPARAM]
    assert user.EnumDesktopWindows(user.GetThreadDesktop(kernel.GetCurrentThreadId()), visit, 0)
    return found

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
               'window_class': name.value, 'desktop_windows': desktop_windows()})
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
