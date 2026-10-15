"""Hide input-desktop windows an owned command tree creates (not a sandbox).

The candidate already places an owned command tree on a private ``PanJob_*``
desktop, so ordinary owned UI never reaches the input desktop. Desktop
separation is not a security boundary, though: an owned process that calls
``SetThreadDesktop(Default)`` can still create a genuinely visible window on the
user's input desktop (recorded counterexample). This guard mitigates that
gap by reacting to window-creation events; it cannot enforce a focus boundary.

Scope and limits:

- Only PIDs reported as owned (the Job Object members, plus the retained root
  PID) are ever acted on. Foreign processes, the default-terminal broker and
  other users' windows are never touched or hidden.
- An out-of-context event hook only receives events for windows created on the
  calling thread's desktop. The hook thread explicitly selects the input desktop
  even when its runner inherited a private desktop. Windows an owned process
  creates on its private desktop are therefore untouched and keep their normal
  visibility there.
- CREATE/SHOW notifications are asynchronous. When CREATE arrives early,
  setting WS_EX_NOACTIVATE can reduce activation; it does not establish that
  the first SHOW was prevented. Resize/hide requests use asynchronous APIs.
  Cross-process style changes can still block, including on custom window
  procedures; the observed COM helper classes are excluded from style changes.
- A brief flash before the hide is possible and is measured, never claimed as
  zero.
- It is a mitigation, not a security fence: a same-user program can still race
  the guard or re-show a window. Non-Windows and ``hideWindow=false`` runs do not
  use it.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

EVENT_OBJECT_CREATE = 0x8000
EVENT_OBJECT_SHOW = 0x8002
OBJID_WINDOW = 0
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
SW_HIDE = 0
WM_QUIT = 0x0012
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_HIDEWINDOW = 0x0080
SWP_ASYNCWINDOWPOS = 0x4000
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260

try:  # WINFUNCTYPE only exists on Windows; the module must still import elsewhere.
    _WinEventProc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                       wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
except AttributeError:  # pragma: no cover - non-Windows import
    _WinEventProc = None


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * MAX_PATH)]


def descendant_pids(root_pid, limit=4096, *, parent_map=None):
    """Best-effort snapshot of ``root_pid`` plus its live descendants.

    Used by the legacy runner, which owns a single process rather than a Job
    Object. Ownership here is the observed parent/child tree, not a proven Job
    membership; an unrelated process that adopts a matching PID is not claimed.
    """
    if not root_pid:
        return ()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return (root_pid,)
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        parents = {}
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        if parent_map is not None:
            parent_map.update(parents)
        owned = {root_pid}
        changed = True
        while changed and len(owned) < limit:
            changed = False
            for pid, parent in parents.items():
                if pid not in owned and parent in owned:
                    owned.add(pid)
                    changed = True
        return tuple(owned)
    finally:
        kernel32.CloseHandle(snapshot)


def owned_descendant_handle(root_pid, root_handle, window_pid):
    """Prove a legacy parent chain with handles and monotonic creation identities.

    An exited/unopenable intermediate parent makes ownership unknown. This is
    weaker than Job membership, but never treats a historical PID as ownership.
    The caller retains the returned target handle through window mutation.
    """
    from packages.core.terminal.identity import (
        open_process_for_probe, read_creation_filetime, close_handle_checked,
    )
    if not root_pid or not root_handle:
        return None
    root_creation = read_creation_filetime(root_handle)
    parents = {}
    if window_pid not in descendant_pids(root_pid, parent_map=parents):
        return None
    handles = []
    target = None
    success = False
    try:
        child_creation = None
        current = window_pid
        seen = set()
        while current and current not in seen:
            seen.add(current)
            handle = open_process_for_probe(current)
            if not handle:
                return None
            handles.append(handle)
            creation = read_creation_filetime(handle)
            if creation is None or (child_creation is not None and creation > child_creation):
                return None
            if target is None:
                target = handle
            if current == root_pid:
                success = creation == root_creation
                return target if success else None
            child_creation = creation
            current = parents.get(current)
        return None
    finally:
        for handle in handles:
            if not success or handle != target:
                close_handle_checked(handle)


class OwnedWindowGuard:
    """Hide windows whose owning PID is reported as owned, on a worker thread."""

    def __init__(self, owned_pids, *, on_hide=None, refresh=0.05, verify_ms=40.0,
                 pre_show_noactivate=True, shrink_on_create=True, owned_handle=None,
                 desktop_name=None):
        self._owned_pids = owned_pids
        self._owned_handle = owned_handle
        self._on_hide = on_hide
        self._refresh = refresh
        self._verify_ms = verify_ms
        self._noactivate = pre_show_noactivate
        self._shrink = shrink_on_create
        self._cache = (0.0, frozenset())
        self._thread = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.error: int | None = None
        self.records: list[dict] = []
        # A named desktop supports isolated real-window verification. Production
        # callers leave this unset so escapes to the user's input desktop are seen.
        self._desktop_name = desktop_name
        self.desktop_name = None
        self.cleanup_error = None

    # -- ownership ---------------------------------------------------------
    def _owned(self) -> frozenset:
        stamp, values = self._cache
        now = time.monotonic()
        if now - stamp > self._refresh:
            try:
                values = frozenset(int(pid) for pid in self._owned_pids())
            except Exception:
                values = frozenset()
            self._cache = (now, values)
        return values

    # -- hook --------------------------------------------------------------
    def _callback(self, _hook, event, hwnd, id_object, id_child, _tid, _ms):
        if id_object != OBJID_WINDOW or id_child != 0 or not hwnd:
            return
        user32 = self._user32
        retained = None
        try:
            if not user32.IsWindow(hwnd):
                return
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if self._owned_handle is not None:
                retained = self._owned_handle(pid.value)
                if not retained:
                    return
            elif pid.value not in self._owned():
                # A new descendant may have appeared since the cached snapshot.
                self._cache = (0.0, frozenset())
                if pid.value not in self._owned():
                    return
            requested = time.perf_counter_ns()
            shrank = False
            if self._shrink:
                # Queue mitigation before synchronous style changes. Scheduling
                # and the application's minimum size still bound the result.
                shrank = bool(user32.SetWindowPos(hwnd, None, 0, 0, 1, 1,
                    SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS))
            window_class = self._class_of(hwnd)
            # These COM/TSF helper windows may sit on threads that never pump
            # cross-thread style messages while the command waits for children.
            # Hide asynchronously, but do not block the event queue changing them.
            change_style = window_class not in {
                "SystemUserAdapterWindowClass", "OleMainThreadWndClass", "CicMarshalWndClass", ""
            }
            noactivate = False
            if event == EVENT_OBJECT_CREATE and self._noactivate and change_style:
                # Best effort: out-of-context delivery can follow the first SHOW.
                style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
                if not (style & WS_EX_NOACTIVATE):
                    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)
                noactivate = bool(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_NOACTIVATE)
            user32.ShowWindowAsync(hwnd, SW_HIDE)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                SWP_HIDEWINDOW | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                                | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
            visible = bool(user32.IsWindowVisible(hwnd))
            record = {"hwnd": int(hwnd or 0), "pid": pid.value, "class": self._class_of(hwnd),
                      "event": event, "noactivateApplied": noactivate, "shrinkRequested": shrank,
                      "requestedTicksNs": requested, "requestCompletedTicksNs": time.perf_counter_ns(),
                      "stillVisible": visible,
                      "creationFiletime": self._creation_of(retained),
                      "latencyMs": round((time.perf_counter_ns() - requested) / 1e6, 3)}
            self.records.append(record)
            if len(self.records) > 2048:
                del self.records[:1024]
            if self._on_hide is not None:
                self._on_hide(record)
        except Exception as error:
            self.records.append({"event": event, "hwnd": int(hwnd), "error": repr(error)})
            return
        finally:
            if retained:
                from packages.core.terminal.identity import close_handle_checked
                close_handle_checked(retained)

    def _creation_of(self, handle):
        if not handle:
            return None
        from packages.core.terminal.identity import read_creation_filetime
        return read_creation_filetime(handle)

    def _class_of(self, hwnd):
        buf = ctypes.create_unicode_buffer(256)
        if self._user32.GetClassNameW(hwnd, buf, 256):
            return buf.value
        return ""

    def _run(self):
        from ctypes import wintypes as w
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32 = user32
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        user32.OpenInputDesktop.restype = w.HANDLE
        user32.OpenDesktopW.argtypes = [w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD]
        user32.OpenDesktopW.restype = w.HANDLE
        user32.SetThreadDesktop.argtypes = [w.HANDLE]
        user32.SetThreadDesktop.restype = w.BOOL
        user32.CloseDesktop.argtypes = [w.HANDLE]
        user32.CloseDesktop.restype = w.BOOL
        user32.GetThreadDesktop.argtypes = [w.DWORD]
        user32.GetThreadDesktop.restype = w.HANDLE
        kernel32.GetCurrentThreadId.restype = w.DWORD
        original = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        user32.GetUserObjectInformationW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                    w.DWORD, ctypes.POINTER(w.DWORD)]
        desktop = (user32.OpenDesktopW(self._desktop_name, 0, False, 0x00FF)
                   if self._desktop_name is not None else user32.OpenInputDesktop(0, False, 0x00FF))
        if not desktop:
            self.error = ctypes.get_last_error()
            self._ready.set()
            return
        try:
            if not user32.SetThreadDesktop(desktop):
                self.error = ctypes.get_last_error()
                self._ready.set()
                return
            name = ctypes.create_unicode_buffer(256)
            needed = w.DWORD()
            if not user32.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
                self.error = ctypes.get_last_error()
                self._ready.set()
                return
            self.desktop_name = name.value
            self._run_hook(user32, kernel32)
        finally:
            if not user32.SetThreadDesktop(original):
                self.cleanup_error = ctypes.get_last_error()
            if not user32.CloseDesktop(desktop):
                self.cleanup_error = ctypes.get_last_error()

    def _run_hook(self, user32, kernel32):
        from ctypes import wintypes as w
        user32.SetWinEventHook.argtypes = [w.UINT, w.UINT, w.HMODULE, ctypes.c_void_p,
                                           w.DWORD, w.DWORD, w.UINT]
        user32.SetWinEventHook.restype = w.HANDLE
        user32.UnhookWinEvent.argtypes = [w.HANDLE]
        user32.UnhookWinEvent.restype = w.BOOL
        user32.GetMessageW.argtypes = [ctypes.c_void_p, w.HWND, w.UINT, w.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
        user32.DispatchMessageW.restype = ctypes.c_void_p
        user32.TranslateMessage.argtypes = [ctypes.c_void_p]
        user32.TranslateMessage.restype = w.BOOL
        user32.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]
        user32.PostThreadMessageW.restype = w.BOOL
        user32.IsWindow.argtypes = [w.HWND]
        user32.IsWindow.restype = w.BOOL
        user32.IsWindowVisible.argtypes = [w.HWND]
        user32.IsWindowVisible.restype = w.BOOL
        user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        user32.GetWindowThreadProcessId.restype = w.DWORD
        long_get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        long_set = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        long_get.argtypes = [w.HWND, ctypes.c_int]
        long_get.restype = ctypes.c_ssize_t
        long_set.argtypes = [w.HWND, ctypes.c_int, ctypes.c_ssize_t]
        long_set.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW = long_get
        user32.SetWindowLongPtrW = long_set
        user32.ShowWindowAsync.argtypes = [w.HWND, ctypes.c_int]
        user32.ShowWindowAsync.restype = w.BOOL
        user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, w.UINT]
        user32.SetWindowPos.restype = w.BOOL
        kernel32.GetCurrentThreadId.restype = w.DWORD
        self._thread_id = kernel32.GetCurrentThreadId()
        proc = _WinEventProc(self._callback)
        self._proc = proc
        hook = user32.SetWinEventHook(EVENT_OBJECT_CREATE, EVENT_OBJECT_SHOW, None, proc,
                                      0, 0, WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
        if not hook:
            self.error = ctypes.get_last_error()
            self._ready.set()
            return
        message = w.MSG()
        self._ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWinEvent(hook)
            self._hook = 0

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, name="pan-owned-window-guard", daemon=True)
        self._thread.start()
        ready = self._ready.wait(timeout=2.0)
        return ready and self.error is None and self._thread.is_alive()

    def stop(self, timeout=2.0):
        if self._thread is None:
            return
        if self._thread_id:
            from ctypes import wintypes as w
            ctypes.WinDLL("user32", use_last_error=True).PostThreadMessageW(
                w.DWORD(self._thread_id), WM_QUIT, 0, 0)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            return False
        self._thread = None
        return self.cleanup_error is None
