"""Session-scoped hidden history message records.

Hidden messages are UI state, not conversation state.  This sidecar is kept
separate from Session metadata and the transcript so hiding a message never
changes the JSONL consumed by a provider resume operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from packages.core.data_catalog import DATA_ROOT

_lock = threading.RLock()


@contextmanager
def _record_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        name = r"Local\PanHiddenMessages_" + hashlib.sha256(
            str(lock_path.resolve()).encode()
        ).hexdigest()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR,
        )
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
            raise OSError(f"hidden-message lock failed: {wait}")
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


def _atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _valid_session_id(session_id: str) -> bool:
    return bool(
        session_id
        and Path(session_id).name == session_id
        and not any(char in session_id for char in "\\/:\0")
    )


class HiddenMessageStore:
    """One JSON sidecar per Session containing hidden wire identities."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else DATA_ROOT / "hidden-messages"

    def path_for(self, session_id: str) -> Path:
        if not _valid_session_id(session_id):
            raise ValueError("invalid session id")
        return self.root / f"{session_id}.json"

    @staticmethod
    def _read(path: Path) -> set[str]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        values = value.get("hiddenMessageIds") if isinstance(value, dict) else None
        return {item for item in values if isinstance(item, str) and item} if isinstance(values, list) else set()

    def hidden_ids(self, session_id: str) -> set[str]:
        path = self.path_for(session_id)
        with _record_lock(path):
            return self._read(path)

    def hide(self, session_id: str, message_id: str) -> bool:
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("invalid message id")
        path = self.path_for(session_id)
        with _record_lock(path):
            hidden = self._read(path)
            if message_id in hidden:
                return False
            hidden.add(message_id)
            _atomic_write(path, {"hiddenMessageIds": sorted(hidden)})
            return True

    def delete_session(self, session_id: str) -> bool:
        path = self.path_for(session_id)
        removed = False
        with _record_lock(path):
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                pass
        try:
            path.with_suffix(path.suffix + ".lock").unlink(missing_ok=True)
        except OSError:
            pass
        return removed
