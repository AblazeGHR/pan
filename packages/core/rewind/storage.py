"""Atomic, session-scoped sidecar records for rewind jobs and checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from packages.core.data_catalog import DATA_ROOT

_lock = threading.RLock()


@contextmanager
def _record_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        name = "Local\\PanRewind_" + hashlib.sha256(str(lock_path.resolve()).encode()).hexdigest()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        mutex = kernel32.CreateMutexW(None, False, name)
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        wait = kernel32.WaitForSingleObject(mutex, 0xFFFFFFFF)
        if wait not in (0, 0x80):
            kernel32.CloseHandle(mutex)
            raise OSError(f"rewind lock failed: {wait}")
        try:
            yield
        finally:
            kernel32.ReleaseMutex(mutex)
            kernel32.CloseHandle(mutex)
        return
    with _lock:
        lock_path.touch(exist_ok=True)
        handle = lock_path.open("r+b")
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{secrets.token_hex(4)}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    try:
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


class RewindRecordStore:
    """One JSON record per rewind operation under data/rewind/<session>."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else DATA_ROOT / "rewind"

    def path_for(self, session_id: str, job_id: str) -> Path:
        if not session_id or Path(session_id).name != session_id or any(c in session_id for c in "\\/:\0"):
            raise ValueError("invalid session id")
        if not job_id or Path(job_id).name != job_id or any(c in job_id for c in "\\/:\0"):
            raise ValueError("invalid rewind job id")
        return self.root / session_id / f"{job_id}.json"

    def save(self, record: dict[str, Any]) -> Path:
        session_id = str(record.get("session_id") or record.get("sessionId") or "")
        job_id = str(record.get("job_id") or record.get("jobId") or "")
        path = self.path_for(session_id, job_id)
        with _record_lock(path):
            _atomic_write(path, record)
        return path

    def load(self, session_id: str, job_id: str) -> dict[str, Any] | None:
        path = self.path_for(session_id, job_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def delete_session(self, session_id: str) -> bool:
        path = self.root / session_id
        if not path.exists():
            return False
        import shutil
        shutil.rmtree(path)
        return True
