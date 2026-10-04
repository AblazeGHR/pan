"""Shared Win32 helpers for the Pan Terminal lifecycle / detach probe.

Scope: process identity (PID + creation time), process-tree enumeration,
Windows Job Object wrappers, loopback JSON-line control protocol, and small
JSON/file helpers.  Everything here is probe-local; it does not import or
modify production modules (the one deliberate exception is
``job_object_probe.py``, which imports ``packages.core.takeover_job`` read-only
to exercise the production class with self-built children).

No third-party dependency is required: ctypes only.
"""
from __future__ import annotations

import ctypes
import json
import os
import socket
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Iterable

IS_WINDOWS = os.name == "nt"

# ── Access rights / job constants ────────────────────────────────────────────
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_DUP_HANDLE = 0x0040

JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

JobObjectBasicAccountingInformation = 1
JobObjectExtendedLimitInformation = 9

CREATE_BREAKAWAY_FROM_JOB = 0x01000000

FILETIME_EPOCH_DELTA = 11644473600  # seconds between 1601-01-01 and 1970-01-01

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)
_k32.CloseHandle.restype = wintypes.BOOL
_k32.GetProcessTimes.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
)
_k32.GetProcessTimes.restype = wintypes.BOOL
_k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_k32.GetExitCodeProcess.restype = wintypes.BOOL
_k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
_k32.TerminateProcess.restype = wintypes.BOOL
_k32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_k32.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
_k32.IsProcessInJob.restype = wintypes.BOOL
_k32.CreateJobObjectW.restype = wintypes.HANDLE
_k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
_k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_k32.AssignProcessToJobObject.restype = wintypes.BOOL
_k32.SetInformationJobObject.argtypes = (
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
_k32.SetInformationJobObject.restype = wintypes.BOOL
_k32.QueryInformationJobObject.argtypes = (
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
_k32.QueryInformationJobObject.restype = wintypes.BOOL
_k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
_k32.TerminateJobObject.restype = wintypes.BOOL
_k32.GetCurrentProcess.restype = wintypes.HANDLE


def last_error_message() -> str:
    return ctypes.WinError(ctypes.get_last_error()).strerror or "unknown"


# ── Process identity ─────────────────────────────────────────────────────────

class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("per_process", ctypes.c_longlong),
        ("per_job", ctypes.c_longlong),
        ("flags", wintypes.DWORD),
        ("min_ws", ctypes.c_size_t),
        ("max_ws", ctypes.c_size_t),
        ("active", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", ctypes.c_ulonglong * 6),
        ("memory", ctypes.c_size_t * 4),
    ]


class _BasicAccounting(ctypes.Structure):
    _fields_ = [
        ("total_user", ctypes.c_longlong),
        ("total_kernel", ctypes.c_longlong),
        ("period_user", ctypes.c_longlong),
        ("period_kernel", ctypes.c_longlong),
        ("page_faults", wintypes.DWORD),
        ("total_processes", wintypes.DWORD),
        ("active_processes", wintypes.DWORD),
        ("terminated_processes", wintypes.DWORD),
    ]


def _filetime_to_int(ft: wintypes.FILETIME) -> int:
    return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)


def process_create_time_raw(pid: int) -> int | None:
    """Exact 100ns FILETIME of process creation, or None when not inspectable."""
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        created = wintypes.FILETIME()
        empty = wintypes.FILETIME()
        ok = _k32.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(empty),
            ctypes.byref(empty), ctypes.byref(empty))
        if not ok:
            return None
        return _filetime_to_int(created)
    finally:
        _k32.CloseHandle(handle)


def filetime_to_seconds(raw: int) -> float:
    return raw / 10_000_000.0 - FILETIME_EPOCH_DELTA


def process_create_time(pid: int) -> float | None:
    raw = process_create_time_raw(pid)
    return None if raw is None else filetime_to_seconds(raw)


def process_image_path(pid: int) -> str | None:
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        _k32.CloseHandle(handle)


STILL_ACTIVE = 259


def process_exit_code(pid: int) -> int | None:
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        return int(code.value)
    finally:
        _k32.CloseHandle(handle)


def process_running(pid: int) -> bool:
    """True only for a live process.

    A terminated process whose handle is still held by its parent keeps its PID
    reserved on Windows (OpenProcess and GetProcessTimes still succeed), so
    liveness must also check GetExitCodeProcess != STILL_ACTIVE.
    """
    code = process_exit_code(pid)
    return code == STILL_ACTIVE


def pid_alive(pid: int) -> bool:
    return process_running(pid)


def identity(pid: int | None) -> dict | None:
    """(pid, raw create time, seconds, image) — the probe's identity tuple."""
    if not pid:
        return None
    raw = process_create_time_raw(pid)
    if raw is None:
        return None
    return {
        "pid": int(pid),
        "createTimeFiletime": raw,
        "createTime": filetime_to_seconds(raw),
        "image": process_image_path(pid),
        "exitCode": process_exit_code(pid),
        "running": process_running(pid),
    }


def identity_matches(pid: int, raw_create_time: int | None) -> bool:
    """Same process (PID + exact creation time) and still running.

    Observation helper only: it opens the PID twice (create time, then exit
    code), so it must not be used as the verification step of a kill.  The
    termination path uses ``kill_verified``, which opens one handle and
    verifies and terminates through that same handle.
    """
    if raw_create_time is None:
        return False
    if process_create_time_raw(pid) != raw_create_time:
        return False
    return process_running(pid)


def is_process_in_job(pid: int, job_handle: int | None = None) -> bool | None:
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        result = wintypes.BOOL()
        ok = _k32.IsProcessInJob(
            handle, wintypes.HANDLE(job_handle) if job_handle else None,
            ctypes.byref(result))
        return bool(result.value) if ok else None
    finally:
        _k32.CloseHandle(handle)


def kill_verified_detail(pid: int, raw_create_time: int | None) -> dict:
    """Single-handle atomic verify-then-terminate; returns the decision trail.

    ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE)`` is
    performed exactly once.  The creation-time and exit-code checks and the
    ``TerminateProcess`` call all use that same handle, so the handle pins one
    process object: if the PID were reused after the open, the pinned object is
    still the one we verified, and a mismatching creation time is rejected
    before any termination.  This removes the check-then-kill TOCTOU window
    that a separate ``OpenProcess`` for termination would introduce.
    """
    result = {
        "pid": pid, "requestedCreateTimeFiletime": raw_create_time,
        "opened": False, "createTimeFiletime": None, "filetimeMatch": False,
        "running": False, "terminated": False,
    }
    if raw_create_time is None:
        return result
    handle = _k32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, False, pid)
    if not handle:
        return result
    result["opened"] = True
    try:
        created = wintypes.FILETIME()
        empty = wintypes.FILETIME()
        if not _k32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(empty),
                ctypes.byref(empty), ctypes.byref(empty)):
            return result
        result["createTimeFiletime"] = _filetime_to_int(created)
        result["filetimeMatch"] = (result["createTimeFiletime"] == raw_create_time)
        if not result["filetimeMatch"]:
            return result
        code = wintypes.DWORD()
        if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return result
        result["running"] = (int(code.value) == STILL_ACTIVE)
        if not result["running"]:
            return result
        result["terminated"] = bool(_k32.TerminateProcess(handle, 1))
        return result
    finally:
        _k32.CloseHandle(handle)


def kill_verified(pid: int, raw_create_time: int | None) -> bool:
    """Terminate only when identity matches on the same handle; no blind kill."""
    return kill_verified_detail(pid, raw_create_time)["terminated"]


# ── Process tree enumeration ─────────────────────────────────────────────────

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def snapshot_processes() -> list[dict]:
    TH32CS_SNAPPROCESS = 0x2
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return []
    out: list[dict] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if k32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                out.append({
                    "pid": int(entry.th32ProcessID),
                    "ppid": int(entry.th32ParentProcessID),
                    "exe": entry.szExeFile,
                })
                if not k32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        k32.CloseHandle(snapshot)
    return out


def descendants(root_pids: Iterable[int], snapshot: list[dict] | None = None) -> list[dict]:
    """All live descendants (excluding roots) of the given PIDs."""
    procs = snapshot if snapshot is not None else snapshot_processes()
    by_parent: dict[int, list[dict]] = {}
    for item in procs:
        by_parent.setdefault(item["ppid"], []).append(item)
    seen: set[int] = set()
    result: list[dict] = []
    queue = list(root_pids)
    while queue:
        for child in by_parent.get(queue.pop(0), []):
            if child["pid"] in seen:
                continue
            seen.add(child["pid"])
            result.append(child)
            queue.append(child["pid"])
    return result


def tree_identity(root_pids: Iterable[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for pid in set(root_pids):
        item = identity(pid)
        if item:
            out[pid] = item
    for item in descendants(root_pids):
        info = identity(item["pid"])
        if info:
            out[item["pid"]] = info
    return out


# ── Job Object wrapper ───────────────────────────────────────────────────────

_k32.DuplicateHandle.argtypes = (
    wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
)
_k32.DuplicateHandle.restype = wintypes.BOOL
DUPLICATE_SAME_ACCESS = 0x00000002


def current_process_handle() -> int:
    return int(_k32.GetCurrentProcess())


def open_process(pid: int, access: int) -> int | None:
    handle = _k32.OpenProcess(access, False, pid)
    return int(handle) if handle else None


def close_handle(handle: int) -> bool:
    return bool(_k32.CloseHandle(wintypes.HANDLE(handle)))


def duplicate_handle(source_process_handle: int, source_handle: int,
                     target_process_handle: int,
                     desired_access: int = 0) -> tuple[bool, int, int]:
    """Duplicate a handle into another process.

    ``desired_access=0`` with the DUPLICATE_SAME_ACCESS option keeps the
    original access rights.  The returned value is valid inside the *target*
    process only; it must be passed there via IPC.
    """
    new_handle = wintypes.HANDLE()
    ok = _k32.DuplicateHandle(
        wintypes.HANDLE(source_process_handle), wintypes.HANDLE(source_handle),
        wintypes.HANDLE(target_process_handle), ctypes.byref(new_handle),
        desired_access, False, DUPLICATE_SAME_ACCESS)
    if not ok:
        return False, 0, ctypes.get_last_error()
    return True, int(new_handle.value), 0


def query_active_processes(handle: int) -> int | None:
    info = _BasicAccounting()
    ok = _k32.QueryInformationJobObject(
        wintypes.HANDLE(handle), JobObjectBasicAccountingInformation,
        ctypes.byref(info), ctypes.sizeof(info), None)
    return int(info.active_processes) if ok else None


class Job:
    """Minimal Job Object wrapper for probe evidence."""

    def __init__(self, handle: int, *, kill_on_close: bool, breakaway_ok: bool):
        self.handle = int(handle)
        self.kill_on_close = kill_on_close
        self.breakaway_ok = breakaway_ok
        self.closed = False

    @classmethod
    def create(cls, *, kill_on_close: bool = True, breakaway_ok: bool = False) -> "Job":
        handle = _k32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(f"CreateJobObjectW failed: {last_error_message()}")
        flags = 0
        if kill_on_close:
            flags |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if breakaway_ok:
            flags |= JOB_OBJECT_LIMIT_BREAKAWAY_OK
        if flags:
            limits = _ExtendedLimits()
            limits.basic.flags = flags
            if not _k32.SetInformationJobObject(
                    wintypes.HANDLE(handle), JobObjectExtendedLimitInformation,
                    ctypes.byref(limits), ctypes.sizeof(limits)):
                error = last_error_message()
                _k32.CloseHandle(wintypes.HANDLE(handle))
                raise OSError(f"SetInformationJobObject failed: {error}")
        return cls(handle, kill_on_close=kill_on_close, breakaway_ok=breakaway_ok)

    def assign(self, pid: int) -> tuple[bool, int]:
        handle = _k32.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION,
            False, pid)
        if not handle:
            return False, ctypes.get_last_error()
        try:
            ok = _k32.AssignProcessToJobObject(wintypes.HANDLE(self.handle), handle)
            return bool(ok), (0 if ok else ctypes.get_last_error())
        finally:
            _k32.CloseHandle(handle)

    def is_member(self, pid: int) -> bool | None:
        return is_process_in_job(pid, self.handle)

    def active_processes(self) -> int | None:
        info = _BasicAccounting()
        ok = _k32.QueryInformationJobObject(
            wintypes.HANDLE(self.handle), JobObjectBasicAccountingInformation,
            ctypes.byref(info), ctypes.sizeof(info), None)
        return int(info.active_processes) if ok else None

    def terminate(self) -> bool:
        return bool(_k32.TerminateJobObject(wintypes.HANDLE(self.handle), 1))

    def close(self) -> None:
        if not self.closed and self.handle:
            _k32.CloseHandle(wintypes.HANDLE(self.handle))
            self.closed = True
            self.handle = 0


def process_in_any_job(pid: int) -> bool | None:
    return is_process_in_job(pid, None)


# ── Loopback JSON-line protocol ──────────────────────────────────────────────

def pick_free_port() -> int:
    """Ask the OS for a verified-free loopback port (bind then release)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


class LineConn:
    """Newline-delimited JSON connection."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = b""

    def send(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
        self.sock.sendall(data)

    def recv(self, timeout: float | None = 10.0) -> dict | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while b"\n" not in self.buf:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.sock.settimeout(remaining)
            else:
                self.sock.settimeout(None)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            except OSError:
                return None
            if not chunk:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))

    def request(self, obj: dict, timeout: float | None = 10.0) -> dict | None:
        self.send(obj)
        return self.recv(timeout)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def read_endpoint(data_root: str | Path, timeout: float = 15.0) -> dict:
    path = Path(data_root) / "runtime.json"
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise TimeoutError(f"runtime endpoint not readable: {path} ({last_error})")


def connect_runtime(data_root: str | Path, token: str, role: str,
                    client_id: str, timeout: float = 10.0) -> tuple[LineConn, dict]:
    endpoint = read_endpoint(data_root, timeout=timeout)
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            sock.connect(("127.0.0.1", int(endpoint["port"])))
            conn = LineConn(sock)
            ack = conn.request({
                "cmd": "hello", "token": token, "role": role, "clientId": client_id,
            })
            if not ack or not ack.get("ok"):
                conn.close()
                raise ConnectionError(f"hello rejected: {ack}")
            return conn, endpoint
        except OSError as exc:
            last_error = exc
            sock.close()
            time.sleep(0.1)
    raise TimeoutError(f"cannot connect runtime on port {endpoint.get('port')}: {last_error}")


# ── Helpers ──────────────────────────────────────────────────────────────────

def write_json(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(
        f"{path.suffix}.{os.getpid()}.{os.urandom(3).hex()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def wait_until(condition: Callable[[], bool], timeout: float,
               interval: float = 0.05) -> tuple[bool, float]:
    start = time.monotonic()
    while True:
        if condition():
            return True, time.monotonic() - start
        if time.monotonic() - start >= timeout:
            return False, time.monotonic() - start
        time.sleep(interval)


def now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def wait_port_closed(port: int, timeout: float = 5.0) -> tuple[bool, float]:
    """True when a fresh TCP connect to the loopback port is refused."""
    start = time.monotonic()
    while True:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            code = probe.connect_ex(("127.0.0.1", port))
        finally:
            probe.close()
        if code != 0:
            return True, time.monotonic() - start
        if time.monotonic() - start >= timeout:
            return False, time.monotonic() - start
        time.sleep(0.1)


def venv_python() -> str:
    return sys.executable


def probe_python() -> tuple[str, dict]:
    """(single-process interpreter, env) for spawning probe children.

    The uv venv's ``Scripts/python.exe`` is a trampoline that starts the base
    interpreter as a *child*, so ``Popen.pid`` would not be the PID the child
    reports.  CPython's documented venv launcher protocol (``__PYVENV_LAUNCHER__``)
    lets the base interpreter run in a single process while keeping the venv
    prefix (pywinpty importable), which keeps PID identity checks exact.
    """
    base = getattr(sys, "_base_executable", None) or sys.executable
    env = dict(os.environ)
    if Path(base).resolve() != Path(sys.executable).resolve():
        env["__PYVENV_LAUNCHER__"] = str(sys.executable)
    return str(base), env
