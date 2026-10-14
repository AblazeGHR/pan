"""Real owned GUI/console probe. Never shows a window on the input desktop."""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import time

u = ctypes.WinDLL("user32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)
for name, args, result in [
    ("GetThreadDesktop", [w.DWORD], w.HANDLE),
    ("OpenInputDesktop", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
    ("CloseDesktop", [w.HANDLE], w.BOOL),
    ("GetUserObjectInformationW", [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
    ("GetWindowThreadProcessId", [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
    ("IsWindowVisible", [w.HWND], w.BOOL),
    ("ShowWindow", [w.HWND, ctypes.c_int], w.BOOL),
    ("CreateWindowExW", [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p], w.HWND),
    ("DestroyWindow", [w.HWND], w.BOOL),
    ("GetClassNameW", [w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
    ("OpenDesktopW", [w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
    ("SetThreadDesktop", [w.HANDLE], w.BOOL),
    ("GetProcessWindowStation", [], w.HANDLE),
    ("OpenWindowStationW", [w.LPCWSTR, w.BOOL, w.DWORD], w.HANDLE),
    ("SetProcessWindowStation", [w.HANDLE], w.BOOL),
    ("CloseWindowStation", [w.HANDLE], w.BOOL),
    ("GetWindowLongW", [w.HWND, ctypes.c_int], w.LONG),
]:
    f = getattr(u, name); f.argtypes = args; f.restype = result
k.GetConsoleWindow.restype = w.HWND
k.GetCurrentThreadId.restype = w.DWORD


def desktop_name(handle):
    buf = ctypes.create_unicode_buffer(256)
    needed = w.DWORD()
    if not u.GetUserObjectInformationW(handle, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buf.value


def process_filetime():
    k.GetCurrentProcess.restype = w.HANDLE
    k.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    times = [w.FILETIME() for _ in range(4)]
    assert k.GetProcessTimes(k.GetCurrentProcess(), *(ctypes.byref(t) for t in times))
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def window_info(hwnd, known_desktop=None):
    pid = w.DWORD()
    tid = u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    cls = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, cls, 256)
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.OpenProcess.restype = w.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    k.CloseHandle.argtypes = [w.HANDLE]
    process = k.OpenProcess(0x1000, False, pid.value)
    image = None
    creation_filetime = None
    if process:
        try:
            path = ctypes.create_unicode_buffer(1024); size = w.DWORD(1024)
            if k.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)): image = path.value
            k.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
            times = [w.FILETIME() for _ in range(4)]
            if k.GetProcessTimes(process, *(ctypes.byref(t) for t in times)):
                creation_filetime = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        finally: k.CloseHandle(process)
    return {"hwnd": int(hwnd or 0), "pid": pid.value, "thread": tid, "class": cls.value, "image": image,
            "creationFiletime": creation_filetime,
            "desktop": known_desktop or desktop_name(u.GetThreadDesktop(tid)),
            "visible": bool(u.IsWindowVisible(hwnd)), "styleVisible": bool(u.GetWindowLongW(hwnd, -16) & 0x10000000)}


def windows(handle):
    rows = []
    name = desktop_name(handle)
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    callback = callback_type(lambda hwnd, _: rows.append(window_info(hwnd, name)) or True)
    u.EnumDesktopWindows.argtypes = [w.HANDLE, callback_type, w.LPARAM]
    u.EnumDesktopWindows.restype = w.BOOL
    assert u.EnumDesktopWindows(handle, callback, 0)
    return rows


def input_desktop():
    previous = u.GetProcessWindowStation()
    station = u.OpenWindowStationW("WinSta0", False, 0x37F)
    assert station
    try:
        assert u.SetProcessWindowStation(station)
        try:
            handle = u.OpenInputDesktop(0, False, 0x41)
            assert handle
            return handle
        finally: assert u.SetProcessWindowStation(previous)
    finally: assert u.CloseWindowStation(station)


def main():
    depth, mode, dest = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    current = desktop_name(u.GetThreadDesktop(k.GetCurrentThreadId()))
    station_name = desktop_name(u.GetProcessWindowStation())
    station_flags = (w.DWORD * 3)()
    needed = w.DWORD()
    assert u.GetUserObjectInformationW(u.GetProcessWindowStation(), 1, station_flags, ctypes.sizeof(station_flags), ctypes.byref(needed))
    station_visible = bool(station_flags[2] & 1)
    input_handle = input_desktop()
    assert input_handle
    try: input_name = desktop_name(input_handle)
    finally: assert u.CloseDesktop(input_handle)
    if mode == "broker":
        assert current == input_name
        deadline = time.monotonic() + 10
        while not (dest / "request.json").exists():
            assert time.monotonic() < deadline
            time.sleep(.03)
        # Owned external broker: create a hidden GUI on its existing desktop.
        # Placement proves the boundary without displaying anything to the user.
        hwnd = u.CreateWindowExW(0, "STATIC", "Pan owned broker probe", 0x00CF0000,
                                0, 0, 160, 80, None, None, None, None)
        assert hwnd
        try: (dest / "broker.json").write_text(json.dumps(window_info(hwnd)), encoding="utf-8")
        finally: assert u.DestroyWindow(hwnd)
        return
    # Verify placement before allocating or showing GUI/console windows.
    assert current.startswith("PanJob_") and current != input_name, (current, input_name)
    if mode == "request":
        (dest / "request.json").write_text(json.dumps({"desktop": current, "station": station_name}), encoding="utf-8")
        return
    if mode == "hold":
        (dest / "hold.json").write_text(json.dumps({"pid": os.getpid(), "creationFiletime": process_filetime(), "desktop": current, "station": station_name}), encoding="utf-8")
        child = subprocess.Popen([sys.executable, __file__, "0", "sleep", str(dest)],
                                 creationflags=0x08000000)
        print("ready", child.pid, flush=True)
        time.sleep(60)
        return
    if mode == "sleep":
        (dest / "child.json").write_text(json.dumps({"pid": os.getpid(), "creationFiletime": process_filetime(), "desktop": current, "station": station_name}), encoding="utf-8")
        time.sleep(60)
        return
    if mode == "escape":
        original = u.GetThreadDesktop(k.GetCurrentThreadId())
        previous_station = u.GetProcessWindowStation()
        input_station = u.OpenWindowStationW("WinSta0", False, 0x37F)
        assert input_station and u.SetProcessWindowStation(input_station)
        other = u.OpenDesktopW(input_name, 0, False, 0x81)
        assert other
        try:
            success = bool(u.SetThreadDesktop(other))
            error = ctypes.get_last_error() if not success else 0
            selected = desktop_name(u.GetThreadDesktop(k.GetCurrentThreadId()))
            # Never allocate a console/create a GUI on the input desktop.
            if success: assert u.SetThreadDesktop(original)
            (dest / "escape.json").write_text(json.dumps({"success": success, "error": error,
                "selected": selected, "original": current, "station": station_name}), encoding="utf-8")
        finally:
            assert u.SetProcessWindowStation(previous_station)
            assert u.CloseDesktop(other)
            assert u.CloseWindowStation(input_station)
        return
    if mode.startswith("alloc"):
        # Keep the log handles; AllocConsole changes the process standard handles.
        log_out = os.dup(sys.stdout.fileno())
        log_err = os.dup(sys.stderr.fileno())
        k.FreeConsole()
        assert k.AllocConsole(), ctypes.get_last_error()
        os.dup2(log_out, sys.stdout.fileno()); os.close(log_out)
        os.dup2(log_err, sys.stderr.fileno()); os.close(log_err)
    hwnd = k.GetConsoleWindow()
    console = None
    if hwnd:
        deadline = time.monotonic() + 2
        while True:
            matches = [r for r in windows(u.GetThreadDesktop(k.GetCurrentThreadId())) if r["hwnd"] == int(hwnd)]
            if matches: break
            if time.monotonic() >= deadline:
                inp = input_desktop()
                try:
                    input_windows = windows(inp)
                    diagnosis = {"consoleHwnd": int(hwnd), "desktop": current,
                        "inputMatches": [r for r in input_windows if r["hwnd"] == int(hwnd)],
                        "inputTerminalWindows": [r for r in input_windows if r["image"] and
                            Path(r["image"]).name.lower() in ("windowsterminal.exe", "openconsole.exe")],
                        "privateWindows": windows(u.GetThreadDesktop(k.GetCurrentThreadId()))}
                    (dest / "console-diagnosis.json").write_text(json.dumps(diagnosis), encoding="utf-8")
                    print(json.dumps(diagnosis), file=sys.stderr, flush=True)
                finally: assert u.CloseDesktop(inp)
                raise AssertionError("console window absent from private desktop")
            time.sleep(.02)
        console = matches[0]
        initial_visible = console["visible"]
        assert console["desktop"] == current
        u.ShowWindow(hwnd, 5)
        u.ShowWindow(hwnd, 5)
        console = window_info(hwnd, current)
        console["initialVisible"] = initial_visible
    gui = u.CreateWindowExW(0, "STATIC", "Pan owned desktop probe", 0x10CF0000,
                           0, 0, 160, 80, None, None, None, None)
    assert gui, ctypes.get_last_error()
    u.ShowWindow(gui, 5)
    u.ShowWindow(gui, 5)
    try:
        evidence = {"pid": os.getpid(), "creationFiletime": process_filetime(), "depth": depth, "desktop": current,
                    "station": station_name, "stationVisible": station_visible, "inputDesktop": input_name,
                    "consoleAllocated": bool(k.GetConsoleCP()), "console": console, "gui": window_info(gui)}
        evidence["desktopWindows"] = windows(u.GetThreadDesktop(k.GetCurrentThreadId()))
        inp = input_desktop()
        assert inp
        try:
            evidence["inputOwnedWindows"] = [r for r in windows(inp) if r["pid"] == os.getpid()]
        finally: assert u.CloseDesktop(inp)
        assert evidence["gui"]["styleVisible"]
        (dest / f"{depth}.json").write_text(json.dumps(evidence), encoding="utf-8")
        print("probe", depth, flush=True)
        if depth:
            startup = subprocess.STARTUPINFO()
            startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            argv = [sys.executable, __file__, str(depth-1), mode, str(dest)]
            child = subprocess.Popen(argv,
                                     creationflags=0x10 if mode.startswith("new") else 0x08000000,
                                     startupinfo=startup if mode in ("new", "alloc") else None,
                                     stdout=sys.stdout, stderr=sys.stderr)
            try: assert child.wait(timeout=10) == 0
            finally:
                if child.poll() is None: child.kill(); child.wait(timeout=5)
    finally: assert u.DestroyWindow(gui)


if __name__ == "__main__": main()
