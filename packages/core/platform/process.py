"""跨平台进程所有权契约（平台层）与 Windows 适配器。

本模块与 :mod:`packages.core.platform.process_posix` 是同一契约的两份实现：

- **契约**（本模块）：``ProcessOwner`` + ``OwnedProcess`` + 身份/结果数据类型；
- **Windows 实现**（本模块）：包装现有 ``packages.core.terminal.guard``
  （``JobObjectGuard``）与 ``packages.core.terminal.identity``（FILETIME 身份、
  单句柄核验终止）。行为保持现状：整树终止只经 Job 句柄，不按 PID 裸杀树；
- **POSIX 实现**：:mod:`packages.core.platform.process_posix`（进程组 + 持久身份）。

硬约束（与根 ``AGENTS.md`` 一致）：

1. **导入无副作用、全平台可导入**：本模块顶层不导入任何 Windows-only 模块。
   ``terminal.guard`` / ``terminal.identity`` 在**方法内**延迟导入——它们顶层
   导入 ``ctypes.wintypes``，在 POSIX 上不可导入。
2. 非 Windows 上构造 :class:`WindowsProcessOwner` 抛
   :class:`UnsupportedPlatformError`（显式“不支持”，不是导入期崩溃）。
3. **fail-closed**：查不到 / 打不开 / 身份不可比对一律 ``UNKNOWN``，绝不冒充
   ``DEAD``；没有可比对身份的目标一律拒绝终止。

契约方法（两种实现同义）：

======================  ============================================================
``spawn``               启动子进程并绑定所有权（Windows=Job Object，POSIX=独立
                        session/进程组）；返回 :class:`OwnedProcess`，其中
                        ``identity`` 是 spawn 时由**同一句柄/同一进程**读到的身份。
``identity``            已绑定身份（``OwnedProcess``）或按 PID 现查（int，尽力而为）。
``is_alive``            三态存活判定（``ALIVE`` / ``DEAD`` / ``UNKNOWN``）。
``check``               ``is_alive`` 的严格版：同时回答“是否仍是我们的进程”。
``wait``                等待根进程退出并返回退出码（有界超时，不杀进程）。
``terminate``           优雅停止：能发信号就 SIGTERM→宽限→SIGKILL（POSIX）；
                        没有优雅信号的平台（Windows）核验后硬杀根进程，再清整树。
                        返回时“自有成员”应已清空或如实给出残留。
``kill_tree``           立即强杀整树并核对残留（不等宽限）。
``recoverable_identity``把身份转成 JSON 安全的可持久化记录（崩溃后回收用）。
``verify`` / ``recover``按持久记录重新校验身份；**只有校验通过才允许回收**。
======================  ============================================================

行为边界（如实声明，不得当作已验证能力）：

- Windows 的 ``spawn`` 是“Popen 后立即 assign Job”，不是
  ``spawn_conpty_suspended`` 的原子挂起语义：子进程在 assign 前有极短窗口可能
  已经执行（PTY 路径仍必须用 ``spawn_win.spawn_conpty_suspended``）。非 PTY 的
  Worker/Job 现状也是这个语义。
- Windows 没有 SIGTERM 等价物：``terminate`` 的 ``grace`` 只用于等待退出码，
  终止本身是 ``TerminateProcess`` + ``TerminateJobObject``（与现状一致）。
- Job 句柄随宿主进程消失时，``KILL_ON_JOB_CLOSE`` 会终止整树；因此 Windows 上
  “崩溃后回收”多数情况下什么都不用做，``recover`` 仍会先验证身份再决定。
- ``close()`` 关闭 Job 句柄：若还有存活成员，内核会终止整棵树（最后兜底）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

__all__ = [
    "PLATFORM_POSIX",
    "PLATFORM_WINDOWS",
    "IDENTITY_SCHEMA",
    "ProcessStatus",
    "ProcessOwnerError",
    "UnsupportedPlatformError",
    "OwnershipUnknownError",
    "IdentityMismatchError",
    "ProcessIdentity",
    "OwnedProcess",
    "WaitResult",
    "TerminateResult",
    "TreeTerminationResult",
    "IdentityCheck",
    "RecoveryResult",
    "ProcessOwner",
    "WindowsProcessOwner",
    "WindowsOwnedProcess",
    "default_process_owner",
]

PLATFORM_WINDOWS = "windows"
PLATFORM_POSIX = "posix"

#: 持久身份记录格式版本；``recoverable_identity`` 写入，``verify``/``recover`` 读取。
IDENTITY_SCHEMA = "pan.process-identity/1"


# --------------------------------------------------------------------------- errors
class ProcessOwnerError(RuntimeError):
    """进程所有权层的基类异常。"""


class UnsupportedPlatformError(ProcessOwnerError):
    """当前平台不提供该实现（显式不支持，调用方应降级/禁用而不是猜）。"""


class OwnershipUnknownError(ProcessOwnerError):
    """所有权/残留/身份查询失败：调用方必须 fail-closed，不得声称已死/已清。"""


class IdentityMismatchError(ProcessOwnerError):
    """身份不匹配（PID 可能已被复用）：拒绝终止。"""


# --------------------------------------------------------------------------- status
class ProcessStatus(str, Enum):
    """单 PID 的显式存活判定。

    ``DEAD`` 必须来自真实证据（进程已退出 / 僵尸）；查不到、打不开、身份不可
    比对一律 ``UNKNOWN``（fail-closed）。
    """

    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- identity
@dataclass(frozen=True)
class ProcessIdentity:
    """持久进程身份 = PID + 出生标记（按平台取最精确可得来源）。

    ``kind`` 说明出生标记的语义，跨 kind **永不匹配**：

    - ``"filetime"``：Windows 原始 64 位 FILETIME（100ns，字符串保存）；
    - ``"starttime"``：Linux ``/proc/<pid>/stat`` 字段 22（开机后 clock ticks）；
    - ``"lstart"``：macOS ``ps -o lstart=''``（**1 秒分辨率**，弱于前两者）。

    ``boot_id`` 只有 Linux 提供（``/proc/sys/kernel/random/boot_id``）：跨重启
    后 starttime 会从头计数，boot_id 让身份在重启后仍然不可混淆。比较时严格
    相等（``None`` 只与 ``None`` 相等）。

    ``pgid`` 是 spawn 时的进程组（POSIX 回收用），**不参与身份比较**：进程组
    成员关系可以变化，身份只认 pid + birth。
    """

    pid: int
    kind: str
    birth: str
    platform: str
    boot_id: str | None = None
    pgid: int | None = None
    image: str | None = None

    def matches(self, other: "ProcessIdentity | None") -> bool:
        """严格相等（无容差）：任何字段不可比对 -> False（拒绝即安全）。"""
        if not isinstance(other, ProcessIdentity):
            return False
        return (
            self.pid == other.pid
            and self.platform == other.platform
            and self.kind == other.kind
            and self.birth == other.birth
            and self.boot_id == other.boot_id
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": int(self.pid),
            "kind": self.kind,
            "birth": self.birth,
            "platform": self.platform,
            "boot_id": self.boot_id,
            "pgid": None if self.pgid is None else int(self.pgid),
            "image": self.image,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProcessIdentity":
        """从持久记录还原；字段缺失/类型不对 -> ``ValueError``（fail-closed）。"""
        if not isinstance(raw, Mapping):
            raise ValueError(f"身份记录必须是 mapping，实际 {type(raw).__name__}")
        try:
            pid = int(raw["pid"])
            kind = str(raw["kind"])
            birth = str(raw["birth"])
            platform = str(raw["platform"])
        except KeyError as exc:
            raise ValueError(f"身份记录缺少字段 {exc.args[0]!r}") from exc
        if pid <= 0:
            raise ValueError(f"非法 pid={pid}")
        if not kind or not birth or not platform:
            raise ValueError("身份记录 kind/birth/platform 不能为空")
        boot_id = raw.get("boot_id")
        pgid = raw.get("pgid")
        image = raw.get("image")
        return cls(
            pid=pid,
            kind=kind,
            birth=birth,
            platform=platform,
            boot_id=None if boot_id is None else str(boot_id),
            pgid=None if pgid is None else int(pgid),
            image=None if image is None else str(image),
        )


# --------------------------------------------------------------------------- results
@dataclass(frozen=True)
class WaitResult:
    """``wait`` 的结果；``timed_out=True`` 表示进程仍在跑（**没有**被杀）。"""

    exited: bool
    exit_code: int | None
    timed_out: bool
    seconds: float
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "exited": self.exited,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "seconds": self.seconds,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class TerminateResult:
    """``terminate`` 的结果。

    - ``requested``：是否真的发出了终止请求（已退出时可能为 False）；
    - ``exited``：根进程退出已确认；
    - ``escalated``：是否升级为强杀（POSIX 的 SIGKILL / Windows 的硬终止）；
    - ``remaining``：仍未确认结束的**自有成员**（空 = 整组已清）；未知用异常表达。
    """

    requested: bool
    exited: bool
    escalated: bool
    signal_sent: str | None = None
    exit_code: int | None = None
    remaining: tuple[int, ...] = ()
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "exited": self.exited,
            "escalated": self.escalated,
            "signal_sent": self.signal_sent,
            "exit_code": self.exit_code,
            "remaining": list(self.remaining),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class TreeTerminationResult:
    """``kill_tree`` 的结果；``confirmed=True`` 当且仅当 ``remaining`` 为空。"""

    owned: tuple[int, ...]
    remaining: tuple[int, ...]
    confirmed: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "owned": list(self.owned),
            "remaining": list(self.remaining),
            "confirmed": self.confirmed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class IdentityCheck:
    """身份复核结果；只有 ``status=ALIVE`` 且 ``ours=True`` 才允许终止。"""

    status: ProcessStatus
    ours: bool | None
    expected: ProcessIdentity | None = None
    observed: ProcessIdentity | None = None
    detail: str = ""

    @property
    def verified_alive(self) -> bool:
        return self.status is ProcessStatus.ALIVE and self.ours is True

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "ours": self.ours,
            "expected": None if self.expected is None else self.expected.as_dict(),
            "observed": None if self.observed is None else self.observed.as_dict(),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RecoveryResult:
    """崩溃后按持久身份回收的结果。

    ``action``：``none`` / ``already_dead`` / ``refused_identity`` /
    ``refused_pgid_changed`` / ``unknown`` / ``terminated``。
    """

    action: str
    verified: bool
    terminated: bool
    remaining: tuple[int, ...] = ()
    check: IdentityCheck | None = None
    detail: str = ""
    exit_code: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "verified": self.verified,
            "terminated": self.terminated,
            "remaining": list(self.remaining),
            "check": None if self.check is None else self.check.as_dict(),
            "detail": self.detail,
            "exit_code": self.exit_code,
        }


# --------------------------------------------------------------------------- handle
class OwnedProcess:
    """一次 ``spawn`` 的所有权句柄（平台实现各自持有 Popen / Job / 进程组）。

    句柄**绑定创建它的 owner 实例**（``owner``），不得跨 owner 使用；``close()``
    释放平台资源（Windows 关闭 Job 句柄 = 兜底终止整树；POSIX 只标记关闭）。
    """

    platform: str = ""

    def __init__(
        self, owner: "ProcessOwner", pid: int, identity: ProcessIdentity
    ) -> None:
        self.owner = owner
        self.pid = int(pid)
        self.identity = identity
        self._closed = False
        self._close_lock = threading.Lock()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:  # pragma: no cover - 由平台实现覆盖
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - 诊断
        return (
            f"{type(self).__name__}(pid={self.pid}, platform={self.platform!r}, "
            f"closed={self.closed})"
        )


# --------------------------------------------------------------------------- contract
class ProcessOwner(ABC):
    """进程所有权契约（平台实现见 ``WindowsProcessOwner`` / ``PosixProcessOwner``）。

    实现必须满足：

    1. ``spawn`` 返回的 ``identity`` 来自**该子进程自身**（Windows 保留句柄 /
       POSIX 同一 pid 的 stat），不得事后按 PID 重开冒充；
    2. 一切终止前都要过身份复核（自有句柄可用绑定身份；外部记录必须重新校验）；
    3. 查询失败抛 :class:`OwnershipUnknownError`，不返回“已清/已死”；
    4. ``terminate`` 返回时要么 ``remaining`` 为空、要么如实给出残留。
    """

    platform: str = ""

    # ------------------------------------------------------------- spawn
    @abstractmethod
    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        stdin: Any = subprocess.DEVNULL,
        stdout: Any = None,
        stderr: Any = None,
        hide_window: bool = True,
    ) -> OwnedProcess:
        """启动并绑定一个自有进程；失败时不得留下未纳管的后代。"""

    # ------------------------------------------------------------- queries
    @abstractmethod
    def identity(self, target: OwnedProcess | int) -> ProcessIdentity | None:
        """已绑定身份（句柄）或按 PID 现查（``None`` = 查不到，不等于已死）。"""

    @abstractmethod
    def check(self, identity: ProcessIdentity) -> IdentityCheck:
        """严格复核：存活状态 + 是否仍是记录里的那个进程。"""

    def is_alive(self, target: OwnedProcess | ProcessIdentity | int) -> ProcessStatus:
        """三态存活判定（不做“是否我们的”判断；那用 :meth:`check`）。"""
        if isinstance(target, OwnedProcess):
            identity = target.identity
        elif isinstance(target, ProcessIdentity):
            identity = target
        else:
            identity = self.identity(int(target))
        if identity is None:
            return ProcessStatus.UNKNOWN
        return self.check(identity).status

    # ------------------------------------------------------------- lifecycle
    @abstractmethod
    def wait(self, handle: OwnedProcess, *, timeout: float | None = None) -> WaitResult:
        """等待根进程退出；超时只返回 ``timed_out``，**不**终止进程。"""

    @abstractmethod
    def terminate(
        self, handle: OwnedProcess, *, grace: float = 5.0
    ) -> TerminateResult:
        """优雅停止整棵自有进程树（平台能力内尽量优雅，超时升级强杀）。"""

    @abstractmethod
    def kill_tree(
        self, handle: OwnedProcess, *, timeout: float = 5.0
    ) -> TreeTerminationResult:
        """立即强杀整棵自有进程树，并在 ``timeout`` 内核对残留。"""

    # ------------------------------------------------------------- recovery
    def recoverable_identity(self, handle: OwnedProcess) -> dict[str, Any]:
        """可持久化的 JSON 安全身份记录（含 schema/platform/pgid）。"""
        if not isinstance(handle, OwnedProcess):
            raise TypeError(f"handle 必须是 OwnedProcess，实际 {type(handle).__name__}")
        record = {
            "schema": IDENTITY_SCHEMA,
            "platform": self.platform,
            "recorded_at": round(time.time(), 3),
        }
        record.update(handle.identity.as_dict())
        return record

    def verify(self, record: Mapping[str, Any]) -> IdentityCheck:
        """按持久记录重新校验；跨平台记录一律 ``UNKNOWN``（不可验证）。"""
        try:
            expected = self._identity_from_record(record)
        except ValueError as exc:
            return IdentityCheck(
                status=ProcessStatus.UNKNOWN,
                ours=None,
                detail=f"身份记录不可用：{exc}",
            )
        return self.check(expected)

    @abstractmethod
    def recover(
        self,
        record: Mapping[str, Any],
        *,
        terminate: bool = True,
        grace: float = 5.0,
        timeout: float = 5.0,
    ) -> RecoveryResult:
        """崩溃后回收：先 :meth:`verify`，只有身份成立才允许终止。"""

    # ------------------------------------------------------------- helpers
    def _identity_from_record(self, record: Mapping[str, Any]) -> ProcessIdentity:
        if not isinstance(record, Mapping):
            raise ValueError(f"身份记录必须是 mapping，实际 {type(record).__name__}")
        schema = record.get("schema")
        if schema is not None and str(schema) != IDENTITY_SCHEMA:
            raise ValueError(f"未知身份记录版本 {schema!r}")
        platform = str(record.get("platform", ""))
        if platform != self.platform:
            raise ValueError(
                f"身份记录属于 {platform or 'unknown'}，本实现是 {self.platform}"
            )
        return ProcessIdentity.from_dict(record)

    def _require_handle(self, handle: Any) -> OwnedProcess:
        if not isinstance(handle, OwnedProcess):
            raise TypeError(f"handle 必须是 OwnedProcess，实际 {type(handle).__name__}")
        if handle.owner is not self:
            raise ValueError("handle 属于另一个 ProcessOwner 实例")
        if handle.closed:
            raise ProcessOwnerError("handle 已关闭")
        return handle


# --------------------------------------------------------------------------- windows
def _terminal_guard() -> Any:
    """延迟导入 Windows Job 守卫（模块顶层 import ``ctypes.wintypes``）。"""
    from packages.core.terminal import guard as guard_module

    return guard_module


def _terminal_identity() -> Any:
    """延迟导入 Windows 身份原语（同上）。"""
    from packages.core.terminal import identity as identity_module

    return identity_module


#: ``CREATE_NO_WINDOW`` / ``CREATE_NEW_CONSOLE``（仅 Windows 使用；不 import ctypes）。
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_CONSOLE = 0x00000010


class WindowsOwnedProcess(OwnedProcess):
    """Windows 自有进程：``Popen`` + 自持 ``JobObjectGuard``。"""

    platform = PLATFORM_WINDOWS

    def __init__(
        self,
        owner: "WindowsProcessOwner",
        popen: subprocess.Popen[Any],
        identity: ProcessIdentity,
        guard: Any,
    ) -> None:
        super().__init__(owner, int(popen.pid), identity)
        self.popen = popen
        self.guard = guard

    def close(self) -> None:
        """关闭 Job 句柄（最后兜底；存活成员会被内核终止）。幂等、失败可重试。"""
        with self._close_lock:
            if self._closed:
                return
            guard = self.guard
            if guard is None:
                self._closed = True
                return
            if not guard.close():
                # 不谎报已释放：句柄保留在句柄对象上，调用方可重试 close()。
                raise OwnershipUnknownError(
                    "CloseHandle 失败：Job 句柄仍被持有（可重试 close）"
                )
            self.guard = None
            self._closed = True


class WindowsProcessOwner(ProcessOwner):
    """Windows 实现：Job Object 所有权 + FILETIME 身份。

    所有实质导入都在调用时发生；非 Windows 上构造即抛
    :class:`UnsupportedPlatformError`，模块本身仍可导入。
    """

    platform = PLATFORM_WINDOWS

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise UnsupportedPlatformError(
                f"WindowsProcessOwner 仅支持 Windows（sys.platform={sys.platform!r}）"
            )

    # ------------------------------------------------------------- spawn
    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        stdin: Any = subprocess.DEVNULL,
        stdout: Any = None,
        stderr: Any = None,
        hide_window: bool = True,
    ) -> WindowsOwnedProcess:
        args = [str(a) for a in argv]
        if not args:
            raise ValueError("argv 不能为空：拒绝无命令的 spawn")
        guard_module = _terminal_guard()
        identity_module = _terminal_identity()
        guard = guard_module.JobObjectGuard()
        popen: subprocess.Popen[Any] | None = None
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0 if hide_window else 1  # SW_HIDE / SW_SHOWNORMAL
            popen = subprocess.Popen(
                args,
                cwd=cwd,
                env=None if env is None else {str(k): str(v) for k, v in env.items()},
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                creationflags=_CREATE_NO_WINDOW if hide_window else _CREATE_NEW_CONSOLE,
                startupinfo=startupinfo,
            )
            handle = int(popen._handle)  # type: ignore[attr-defined]
            guard.assign(handle)
            if not guard.is_member(handle):
                raise OwnershipUnknownError(
                    "IsProcessInJob 未证明成员关系：拒绝交出所有权"
                )
            active = guard.active_processes()
            if active is None or int(active) < 1:
                raise OwnershipUnknownError(
                    f"Job active_processes={active!r}：assigned 无法证明"
                )
            creation_ft = identity_module.read_creation_filetime(handle)
            if creation_ft is None:
                raise OwnershipUnknownError(
                    "GetProcessTimes 读取创建时间失败：无精确身份即拒绝"
                )
        except BaseException:
            # 自建子进程 + 自建 guard：清理后原样抛出（不返回半可用句柄）。
            self._cleanup_failed_spawn(popen, guard)
            raise
        identity = ProcessIdentity(
            pid=int(popen.pid),
            kind="filetime",
            birth=str(int(creation_ft)),
            platform=PLATFORM_WINDOWS,
            image=args[0],
        )
        return WindowsOwnedProcess(self, popen, identity, guard)

    @staticmethod
    def _cleanup_failed_spawn(
        popen: "subprocess.Popen[Any] | None", guard: Any
    ) -> None:
        if popen is not None:
            try:
                popen.kill()
            except OSError:
                pass
            try:
                popen.wait(timeout=5.0)
            except (subprocess.TimeoutExpired, OSError):
                pass
        try:
            guard.terminate_tree(None, timeout=2.0)
        except Exception:  # noqa: BLE001 - 清理失败不掩盖原始异常
            pass
        try:
            guard.close()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- queries
    def identity(self, target: WindowsOwnedProcess | int) -> ProcessIdentity | None:
        if isinstance(target, OwnedProcess):
            self._require_handle(target)
            return target.identity
        identity_module = _terminal_identity()
        pid = int(target)
        handle = identity_module.open_process_for_probe(pid)
        if not handle:
            return None
        try:
            filetime = identity_module.read_creation_filetime(handle)
        finally:
            identity_module.close_handle_checked(handle)
        if filetime is None:
            return None
        return ProcessIdentity(
            pid=pid,
            kind="filetime",
            birth=str(int(filetime)),
            platform=PLATFORM_WINDOWS,
        )

    def check(self, identity: ProcessIdentity) -> IdentityCheck:
        if identity.platform != PLATFORM_WINDOWS or identity.kind != "filetime":
            return IdentityCheck(
                status=ProcessStatus.UNKNOWN,
                ours=None,
                expected=identity,
                detail=f"身份来源不可在 Windows 复核（kind={identity.kind!r}）",
            )
        identity_module = _terminal_identity()
        handle = identity_module.open_process_for_probe(identity.pid)
        if not handle:
            return IdentityCheck(
                status=ProcessStatus.UNKNOWN,
                ours=None,
                expected=identity,
                detail="OpenProcess 失败：查不到/打不开 = unknown，不等于 dead",
            )
        try:
            state = identity_module.wait_state(handle)
            filetime = identity_module.read_creation_filetime(handle)
        finally:
            identity_module.close_handle_checked(handle)
        observed = (
            None
            if filetime is None
            else ProcessIdentity(
                pid=identity.pid,
                kind="filetime",
                birth=str(int(filetime)),
                platform=PLATFORM_WINDOWS,
                image=identity.image,
            )
        )
        state_value = str(getattr(state, "value", state))
        if state_value == ProcessStatus.DEAD.value:
            ours = None if observed is None else observed.matches(identity)
            return IdentityCheck(
                status=ProcessStatus.DEAD,
                ours=ours,
                expected=identity,
                observed=observed,
                detail="句柄 signaled：该 PID 的进程已退出",
            )
        if state_value == ProcessStatus.ALIVE.value:
            if observed is None:
                return IdentityCheck(
                    status=ProcessStatus.UNKNOWN,
                    ours=None,
                    expected=identity,
                    detail="存活但读不到 FILETIME：无结论（fail-closed）",
                )
            if observed.matches(identity):
                return IdentityCheck(
                    status=ProcessStatus.ALIVE,
                    ours=True,
                    expected=identity,
                    observed=observed,
                    detail="FILETIME 精确匹配",
                )
            return IdentityCheck(
                status=ProcessStatus.ALIVE,
                ours=False,
                expected=identity,
                observed=observed,
                detail="FILETIME 不匹配：PID 已被复用，拒绝终止",
            )
        return IdentityCheck(
            status=ProcessStatus.UNKNOWN,
            ours=None,
            expected=identity,
            observed=observed,
            detail="WaitForSingleObject 未知状态：fail-closed",
        )

    # ------------------------------------------------------------- lifecycle
    def wait(
        self, handle: WindowsOwnedProcess, *, timeout: float | None = None
    ) -> WaitResult:
        owned = self._require_handle(handle)
        started = time.monotonic()
        try:
            code = owned.popen.wait(
                timeout=None if timeout is None else max(0.0, float(timeout))
            )
        except subprocess.TimeoutExpired:
            return WaitResult(
                exited=False,
                exit_code=None,
                timed_out=True,
                seconds=round(time.monotonic() - started, 4),
                detail="等待超时：进程仍在运行（未终止）",
            )
        return WaitResult(
            exited=True,
            exit_code=None if code is None else int(code),
            timed_out=False,
            seconds=round(time.monotonic() - started, 4),
        )

    def terminate(
        self, handle: WindowsOwnedProcess, *, grace: float = 5.0
    ) -> TerminateResult:
        """核验后硬杀根进程，再经 Job 清整树（Windows 无 SIGTERM 等价物）。"""
        owned = self._require_handle(handle)
        identity_module = _terminal_identity()
        grace = max(0.0, float(grace))
        result = identity_module.kill_verified(
            owned.identity.pid,
            int(owned.identity.birth),
            wait_timeout=grace,
        )
        reason = str(result.reason)
        if reason == "identity_mismatch":
            raise IdentityMismatchError(
                "FILETIME 不匹配：拒绝终止（PID 可能已被复用）"
            )
        if reason in {"open_failed", "state_unknown", "terminate_failed"}:
            raise OwnershipUnknownError(
                f"核验终止不可确认（reason={reason}, last_error={result.last_error}）"
            )
        if reason not in {"terminated", "not_running"}:
            raise OwnershipUnknownError(f"未预期的核验终止结果 reason={reason!r}")

        tree = self.kill_tree(owned, timeout=max(grace, 1.0))
        exit_code = owned.popen.poll()
        return TerminateResult(
            requested=bool(result.attempted),
            exited=tree.confirmed or exit_code is not None,
            escalated=True,
            signal_sent="TerminateProcess",
            exit_code=None if exit_code is None else int(exit_code),
            remaining=tree.remaining,
            detail=(
                f"Windows 无可发送的优雅信号；根进程 reason={reason}，"
                f"整树 Job 清扫 confirmed={tree.confirmed}"
            ),
        )

    def kill_tree(
        self, handle: WindowsOwnedProcess, *, timeout: float = 5.0
    ) -> TreeTerminationResult:
        owned = self._require_handle(handle)
        guard = owned.guard
        if guard is None or not guard.handle_open:
            raise OwnershipUnknownError("Job 句柄不可用：整树所有权未知（fail-closed）")
        try:
            owned_pids, remaining = guard.terminate_tree(
                owned.pid, timeout=max(0.0, float(timeout))
            )
        except Exception as exc:  # GuardQueryError -> 统一为所有权未知
            raise OwnershipUnknownError(f"Job 整树终止失败：{exc}") from exc
        return TreeTerminationResult(
            owned=tuple(sorted(int(p) for p in owned_pids)),
            remaining=tuple(sorted(int(p) for p in remaining)),
            confirmed=not remaining,
            detail="TerminateJobObject + active==0 双证据" if not remaining else "仍有残留",
        )

    # ------------------------------------------------------------- recovery
    def recover(
        self,
        record: Mapping[str, Any],
        *,
        terminate: bool = True,
        grace: float = 5.0,
        timeout: float = 5.0,
    ) -> RecoveryResult:
        try:
            expected = self._identity_from_record(record)
        except ValueError as exc:
            return RecoveryResult(
                action="unknown",
                verified=False,
                terminated=False,
                detail=f"身份记录不可用：{exc}",
            )
        check = self.check(expected)
        if check.status is ProcessStatus.ALIVE and check.ours is True:
            if not terminate:
                return RecoveryResult(
                    action="none",
                    verified=True,
                    terminated=False,
                    check=check,
                    detail="身份成立且仍存活；调用方选择不终止",
                )
            identity_module = _terminal_identity()
            result = identity_module.kill_verified(
                expected.pid, int(expected.birth), wait_timeout=max(grace, 0.0)
            )
            if str(result.reason) == "identity_mismatch":
                return RecoveryResult(
                    action="refused_identity",
                    verified=False,
                    terminated=False,
                    check=check,
                    detail="再次核验时身份不匹配：拒绝终止",
                )
            killed = str(result.reason) in {"terminated", "not_running"}
            return RecoveryResult(
                action="terminated" if killed else "unknown",
                verified=True,
                terminated=killed,
                check=check,
                exit_code=result.exit_code_informational,
                detail=(
                    "已按持久身份核验并终止根进程；Job 句柄随崩溃消失，"
                    "残留成员由 KILL_ON_JOB_CLOSE 兜底（无法在此枚举）"
                    if killed
                    else f"核验终止未确认（reason={result.reason}）"
                ),
            )
        if check.status is ProcessStatus.DEAD:
            return RecoveryResult(
                action="already_dead",
                verified=check.ours is True,
                terminated=False,
                check=check,
                detail="目标进程已退出，无需回收",
            )
        if check.status is ProcessStatus.ALIVE and check.ours is False:
            return RecoveryResult(
                action="refused_identity",
                verified=False,
                terminated=False,
                check=check,
                detail="身份不匹配：拒绝终止",
            )
        return RecoveryResult(
            action="unknown",
            verified=False,
            terminated=False,
            check=check,
            detail="身份/存活不可确认：fail-closed，不终止",
        )


# --------------------------------------------------------------------------- factory
def default_process_owner() -> ProcessOwner:
    """按当前平台返回实现；不支持时抛 :class:`UnsupportedPlatformError`。"""
    if sys.platform == "win32":
        return WindowsProcessOwner()
    if os.name == "posix":
        from packages.core.platform.process_posix import PosixProcessOwner

        return PosixProcessOwner()
    raise UnsupportedPlatformError(f"没有适用于 sys.platform={sys.platform!r} 的实现")
