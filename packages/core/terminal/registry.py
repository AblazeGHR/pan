"""全局 Terminal 注册表：原子持久化 + 跨进程锁（平台无关，P0）。

- 每终端一个 JSON 文件：``<root>/<terminal_id>.json``（默认 ``data/terminals/``，
  ``PAN_TERMINALS_DIR`` 可覆盖）；
- **JSON 无任何秘密/token 字段**（记录结构白名单，见 ``TerminalRecord.to_dict``）；
  运行期对象（``PtyRuntime``、IPC 凭证）不属于本层；
- 写路径原子：临时文件 + ``os.replace``（Windows 文件扫描器短暂占用时重试，
  绝不回退为截断写）；
- 跨进程互斥：Windows 命名内核 mutex / POSIX ``flock``（独立实现，不复用
  ``background_jobs`` 代码）；
- ``remove`` 只允许 ``exited``/``lost``：清理失败的记录必须保留，供重试与
  reconcile 收敛（不删除失败事实）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .contracts import (
    TERMINAL_ID_PREFIX,
    IllegalStateTransition,
    RegistryCorruptError,
    RegistryError,
    RegistryLockTimeout,
    RuntimeState,
    TerminalExistsError,
    TerminalRecord,
    UnknownTerminalError,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY_ROOT = _PROJECT_ROOT / "data" / "terminals"

_TERMINAL_ID_RE = re.compile(r"^term_[A-Za-z0-9_-]{1,64}$")


def _validate_terminal_id(terminal_id: str) -> str:
    if not isinstance(terminal_id, str) or not _TERMINAL_ID_RE.match(terminal_id):
        raise ValueError(f"invalid terminal id: {terminal_id!r}")
    return terminal_id


def _resolve_root(root: str | os.PathLike[str] | None) -> Path:
    if root is not None:
        return Path(root).expanduser()
    value = os.environ.get("PAN_TERMINALS_DIR")
    return Path(value).expanduser() if value else DEFAULT_REGISTRY_ROOT


@contextmanager
def _windows_named_mutex(root: Path, timeout: float) -> Iterator[None]:
    """按 registry 路径命名的内核 mutex（进程死亡自动释放）。"""
    import ctypes
    from ctypes import wintypes

    digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    mutex = kernel32.CreateMutexW(None, False, f"Local\\PanTerminalRegistry_{digest}")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    wait_timeout = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
    wait = kernel32.WaitForSingleObject(mutex, wait_timeout)
    if wait == 0x102:  # WAIT_TIMEOUT
        kernel32.CloseHandle(mutex)
        raise RegistryLockTimeout(f"registry lock timeout after {timeout}s: {root}")
    if wait not in (0x0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
        error = ctypes.get_last_error()
        kernel32.CloseHandle(mutex)
        if wait == 0xFFFFFFFF:  # WAIT_FAILED
            raise ctypes.WinError(error)
        raise OSError(f"WaitForSingleObject failed: {wait}")
    try:
        yield
    finally:
        try:
            if not kernel32.ReleaseMutex(mutex):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if not kernel32.CloseHandle(mutex):
                raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def _posix_flock(root: Path, timeout: float) -> Iterator[None]:
    import fcntl

    lock_path = root / ".registry.lock"
    handle = open(lock_path, "a+b")
    try:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise RegistryLockTimeout(f"registry lock timeout after {timeout}s: {root}")
                time.sleep(0.01)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def _registry_lock(root: Path, timeout: float) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        with _windows_named_mutex(root, timeout):
            yield
    else:
        with _posix_flock(root, timeout):
            yield


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Windows 文件扫描器/并发关闭的 reader 会短暂拒绝 replace；有界重试，
    # 绝不回退为截断写。
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.01 * (attempt + 1))


def _load_json(path: Path) -> dict[str, Any] | None:
    """读取 JSON；不存在返回 None；损坏抛 ``RegistryCorruptError``（不静默丢弃）。"""
    for attempt in range(20):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return None
        except PermissionError:
            if attempt == 19:
                raise RegistryError(f"cannot read terminal record: {path.name}")
            time.sleep(0.01 * (attempt + 1))
    else:  # pragma: no cover - 循环必然 break/return
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RegistryCorruptError(f"corrupt terminal record: {path.name}") from exc
    if not isinstance(data, dict):
        raise RegistryCorruptError(f"corrupt terminal record (not an object): {path.name}")
    return data


class TerminalRegistry:
    """文件持久化的全局终端注册表。

    本层是**数据层**：不持有 ``PtyRuntime``、不参与 IPC。运行期对象由服务/
    runner 进程各自在其内存表中维护。
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        lock_timeout: float = 5.0,
    ) -> None:
        self.root = _resolve_root(root)
        self.lock_timeout = float(lock_timeout)

    # -- 工具 ---------------------------------------------------------
    @staticmethod
    def new_terminal_id() -> str:
        return f"{TERMINAL_ID_PREFIX}{uuid.uuid4().hex[:16]}"

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, terminal_id: str) -> Path:
        _validate_terminal_id(terminal_id)
        return self.root / f"{terminal_id}.json"

    # -- 写路径（跨进程互斥 + 原子写） ----------------------------------
    def create(self, record: TerminalRecord) -> TerminalRecord:
        """创建新记录；已存在抛 ``TerminalExistsError``。"""
        self._ensure_root()
        with _registry_lock(self.root, self.lock_timeout):
            path = self._path(record.terminal_id)
            if path.exists():
                raise TerminalExistsError(record.terminal_id)
            now = time.time()
            if record.created_at <= 0:
                record.created_at = now
            record.updated_at = now
            _atomic_write(path, record.to_dict())
        return record

    def save(self, record: TerminalRecord) -> TerminalRecord:
        """upsert：写入完整记录并刷新 ``updated_at``（reconcile/状态更新用）。"""
        self._ensure_root()
        with _registry_lock(self.root, self.lock_timeout):
            path = self._path(record.terminal_id)
            now = time.time()
            if record.created_at <= 0:
                record.created_at = now
            record.updated_at = now
            _atomic_write(path, record.to_dict())
        return record

    def remove(self, terminal_id: str) -> None:
        """移除记录。**仅** ``exited``/``lost`` 允许；失败记录不删。"""
        self._ensure_root()
        with _registry_lock(self.root, self.lock_timeout):
            path = self._path(terminal_id)
            data = _load_json(path)
            if data is None:
                raise UnknownTerminalError(terminal_id)
            record = TerminalRecord.from_dict(data)
            if record.status not in (RuntimeState.EXITED, RuntimeState.LOST):
                raise IllegalStateTransition(
                    f"cannot remove terminal in state {record.status.value}"
                )
            try:
                path.unlink()
            except FileNotFoundError:
                raise UnknownTerminalError(terminal_id) from None

    # -- 读路径 --------------------------------------------------------
    def get(self, terminal_id: str) -> TerminalRecord:
        data = _load_json(self._path(terminal_id))
        if data is None:
            raise UnknownTerminalError(terminal_id)
        return TerminalRecord.from_dict(data)

    def exists(self, terminal_id: str) -> bool:
        try:
            self.get(terminal_id)
            return True
        except UnknownTerminalError:
            return False

    def list(self) -> list[TerminalRecord]:
        """列出全部记录（按文件名排序；不持锁——单文件替换保证读到的完整）。"""
        if not self.root.exists():
            return []
        records: list[TerminalRecord] = []
        for path in sorted(self.root.glob(f"{TERMINAL_ID_PREFIX}*.json")):
            data = _load_json(path)
            if data is None:  # 并发删除：跳过，不构造半记录
                continue
            records.append(TerminalRecord.from_dict(data))
        return records
