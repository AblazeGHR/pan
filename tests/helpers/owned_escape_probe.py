"""Owned descendant that deliberately escapes to the input desktop.

The previous rounds recorded this as a real counterexample: an owned process
that calls SetThreadDesktop(Default) can create a genuinely visible window on
the user's input desktop (same-user desktop separation is not a fence). This
probe reproduces that escape *bounded and self-cleaning* so a guard can be
measured against it:

- starts on the runner-assigned private desktop and proves placement first;
- opens WinSta0\\Default, selects it on this thread, creates one small opaque
  STATIC window, shows it without activation, holds it briefly, destroys it and
  restores the original desktop;
- writes identity/placement/timing facts so the driver can attribute the window
  by PID and raw creation FILETIME; it never touches any foreign window.

The window is intentionally tiny and short-lived. It is never enumerated or
hidden by this probe; an external observer/guard does that.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes as w
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

for name, args, result in [
    ("GetThreadDesktop", [w.DWORD], w.HANDLE),
    ("OpenInputDesktop", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
    ("CloseDesktop", [w.HANDLE], w.BOOL),
    ("GetUserObjectInformationW", [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
    ("GetWindowThreadProcessId", [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
    ("CreateWindowExW", [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p], w.HWND),
    ("DestroyWindow", [w.HWND], w.BOOL),
    ("GetClassNameW", [w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
    ("GetWindowRect", [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
    ("IsWindowVisible", [w.HWND], w.BOOL),
    ("ShowWindow", [w.HWND, ctypes.c_int], w.BOOL),
    ("OpenDesktopW", [w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
    ("SetThreadDesktop", [w.HANDLE], w.BOOL),
    ("GetProcessWindowStation", [], w.HANDLE),
    ("OpenWindowStationW", [w.LPCWSTR, w.BOOL, w.DWORD], w.HANDLE),
    ("SetProcessWindowStation", [w.HANDLE], w.BOOL),
    ("CloseWindowStation", [w.HANDLE], w.BOOL),
    ("GetForegroundWindow", [], w.HWND),
    ("UpdateWindow", [w.HWND], w.BOOL),
    ("TranslateMessage", [ctypes.c_void_p], w.BOOL),
    ("DispatchMessageW", [ctypes.c_void_p], ctypes.c_void_p),
    ("PeekMessageW", [ctypes.c_void_p, w.HWND, w.UINT, w.UINT, w.UINT], w.BOOL),
    ("RegisterClassW", [ctypes.c_void_p], w.ATOM),
    ("DefWindowProcW", [w.HWND, w.UINT, w.WPARAM, w.LPARAM], ctypes.c_ssize_t),
    ("GetDC", [w.HWND], w.HDC),
    ("ReleaseDC", [w.HWND, w.HDC], ctypes.c_int),
    ("FillRect", [w.HDC, ctypes.POINTER(w.RECT), w.HBRUSH], ctypes.c_int),
]:
    f = getattr(user32, name)
    f.argtypes = args
    f.restype = result
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
gdi32.CreateSolidBrush.argtypes = [w.DWORD]
gdi32.CreateSolidBrush.restype = w.HBRUSH
kernel32.GetCurrentThreadId.restype = w.DWORD

#: Dark owned fixture; these lifecycle tests do not claim pixel invisibility.
PROBE_CLASS = "PanOwnedVisibleProbe"
_DARK_GRAY = 0x00202020


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR)]


_WndProc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
_wnd_proc = _WndProc(lambda hwnd, msg, wparam, lparam: user32.DefWindowProcW(hwnd, msg, wparam, lparam))
_brushes = []


def register_probe_class():
    info = WNDCLASSW()
    info.lpfnWndProc = ctypes.cast(_wnd_proc, ctypes.c_void_p)
    info.hbrBackground = gdi32.CreateSolidBrush(_DARK_GRAY)
    _brushes.append(info.hbrBackground)
    info.lpszClassName = PROBE_CLASS
    if not user32.RegisterClassW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error not in (1410,):  # ERROR_CLASS_ALREADY_EXISTS
            raise ctypes.WinError(error)
    return info.hbrBackground


def _hvalue(handle):
    if handle is None:
        return 0
    if isinstance(handle, ctypes.c_void_p):
        return int(handle.value or 0)
    return int(handle)


def desktop_name(handle):
    buf = ctypes.create_unicode_buffer(256)
    needed = w.DWORD()
    if not user32.GetUserObjectInformationW(handle, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buf.value


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--hold-ms", type=int, default=700)
    parser.add_argument("--width", type=int, default=220)
    parser.add_argument("--height", type=int, default=80)
    parser.add_argument("--show-mode", choices=["noactivate", "activate", "visible-create"], default="noactivate")
    parser.add_argument("--depth", type=int, default=0)
    parser.add_argument("--x", type=int, default=60)
    parser.add_argument("--y", type=int, default=60)
    parser.add_argument("--force", action="store_true",
                        help="create the probe window on the current desktop without escaping")
    args = parser.parse_args(argv)
    kernel32.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    kernel32.GetCurrentProcess.restype = w.HANDLE
    creation, exit_t, kernel_t, user_t = (w.FILETIME() for _ in range(4))
    assert kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), ctypes.byref(creation),
                                    ctypes.byref(exit_t), ctypes.byref(kernel_t), ctypes.byref(user_t))
    creation_filetime = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    started = time.perf_counter_ns()
    original = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    original_name = desktop_name(original)
    previous_station = user32.GetProcessWindowStation()
    station = previous_station
    station_name = desktop_name(station)
    input_handle = user32.OpenInputDesktop(0, False, 0x41)
    if not input_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    input_name = desktop_name(input_handle)
    user32.CloseDesktop(input_handle)
    facts = {"pid": os.getpid(), "creationFiletime": creation_filetime, "depth": args.depth, "originalDesktop": original_name,
             "station": station_name, "inputDesktop": input_name, "escaped": False, "showMode": args.show_mode}
    out = Path(args.out)
    out.write_text(json.dumps(facts), encoding="utf-8")  # Identity before any window is created.
    if args.force:
        facts["escaped"] = original_name == input_name
    elif original_name == input_name:
        facts["note"] = "started on input desktop; escape not exercised"
        out.write_text(json.dumps(facts), encoding="utf-8")
        return 0
    else:
        input_station = user32.OpenWindowStationW("WinSta0", False, 0x37F)
        if not input_station or not user32.SetProcessWindowStation(input_station):
            raise ctypes.WinError(ctypes.get_last_error())
        target = user32.OpenDesktopW(input_name, 0, False, 0x00FF)
        if not target or not user32.SetThreadDesktop(target):
            raise ctypes.WinError(ctypes.get_last_error())
    hwnd = 0
    try:
        facts["escaped"] = facts["escaped"] or desktop_name(
            user32.GetThreadDesktop(kernel32.GetCurrentThreadId())) == input_name
        facts["foregroundBefore"] = _hvalue(user32.GetForegroundWindow())
        register_probe_class()
        created = time.perf_counter_ns()
        hwnd = user32.CreateWindowExW(0, PROBE_CLASS, "Pan owned escape probe", 0x80000000 | (0x10000000 if args.show_mode == "visible-create" else 0),
                                      args.x, args.y, args.width, args.height,
                                      None, None, None, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        rect = w.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        facts.update({"hwnd": _hvalue(hwnd), "rect": [rect.left, rect.top, rect.right, rect.bottom],
                      "createdTicksNs": created, "shownTicksNs": None, "hiddenTicksNs": None,
                      "destroyedTicksNs": None})
        shown = time.perf_counter_ns()
        user32.ShowWindow(hwnd, 4 if args.show_mode == "noactivate" else 5)
        user32.UpdateWindow(hwnd)
        facts["shownTicksNs"] = shown
        facts["visibleAfterShow"] = bool(user32.IsWindowVisible(hwnd))
        facts["foregroundAfterShow"] = _hvalue(user32.GetForegroundWindow())
        deadline = time.monotonic() + args.hold_ms / 1000.0
        focus_samples = []
        message = w.MSG()
        while time.monotonic() < deadline:
            while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
            focus_samples.append(_hvalue(user32.GetForegroundWindow()))
            if not user32.IsWindowVisible(hwnd):
                facts["hiddenTicksNs"] = time.perf_counter_ns()
                break
            time.sleep(0.001)
        facts["focusSamples"] = focus_samples
        facts["foregroundStolen"] = any(value != facts["foregroundBefore"] for value in focus_samples)
        facts["hiddenByGuard"] = facts["hiddenTicksNs"] is not None
        if facts["hiddenTicksNs"] is None:
            facts["hiddenTicksNs"] = time.perf_counter_ns()
        facts["foregroundAfterHold"] = _hvalue(user32.GetForegroundWindow())
        user32.DestroyWindow(hwnd)
        facts["destroyedTicksNs"] = time.perf_counter_ns()
        hwnd = 0
        if args.depth:
            child_out = out.with_name(out.stem + f".child{args.depth - 1}.json")
            child_argv = [sys.executable, str(Path(__file__).resolve()), "--out", str(child_out),
                          "--hold-ms", str(args.hold_ms), "--depth", str(args.depth - 1),
                          "--show-mode", args.show_mode, "--x", str(args.x + 40), "--y", str(args.y + 40)]
            if args.force:
                child_argv.append("--force")
            child = subprocess.Popen(child_argv, creationflags=0x08000000)
            facts["childPid"] = child.pid
            child.wait(timeout=30)
            facts["childExit"] = child.returncode
    finally:
        if hwnd:
            user32.DestroyWindow(hwnd)
        if not args.force:
            user32.SetThreadDesktop(original)
            user32.CloseDesktop(target)
            user32.SetProcessWindowStation(previous_station)
            user32.CloseWindowStation(input_station)
    facts["totalMs"] = round((time.perf_counter_ns() - started) / 1e6, 3)
    out.write_text(json.dumps(facts), encoding="utf-8")
    print(json.dumps({"escaped": facts["escaped"], "hwnd": facts.get("hwnd"),
                      "hiddenByGuard": facts.get("hiddenByGuard")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
