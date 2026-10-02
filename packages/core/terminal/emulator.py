"""权威仿真器宿主：runner 内常驻 headless-xterm sidecar 桥（P1，TA-B）。

本模块实现 :class:`contracts.AuthoritativeEmulator`，并把
``runtime.output_consumer`` 的绝对偏移供料接入 sidecar：

- ``feed_at(seq, data)`` 与 ``contracts.OutputConsumer``（``Callable[[int, bytes], None]``）
  签名一致，可直接作为 ``build_runtime(..., output_consumer=emulator.feed_at)``
  的入参；``feed(data)`` 是协议要求的连续供料入口（追加到当前生产前沿）。
- 供料**快速有界入队**：所有 op（feed/resize/snapshot/barrier/reset）进入同一条
  FIFO，4 MiB / 8192 项上限（含控制命令）；队列打满置位 ``feed_lag`` 并显式拒绝
  （producer frontier 仍推进、拒绝区间有界记录），绝不阻塞 PTY reader。
- ``snapshot.cursor`` = sidecar **已解析应用**（write 回调确认）的绝对字节位置；
  **禁止**用 producer 的 total_bytes 冒充。cursor 语义 = 协议 A：客户端从该
  cursor 重拉原字节续流，**不预喂 pending tail**（不双消费）。
- 引擎不可用、检测到未知序列、hold-back 强制喂入、feed 队列溢出、序列 gap、
  duplicate 供料、显式 reset：一律 ``fidelity=partial|unavailable`` /
  ``recovery=partial|degraded|none``；``full`` 仅在被测能力矩阵内出现。

进程所有权与关闭纪律（runner 侧职责；r2 返工后统一为有界整 Job 清理）：

- sidecar 以 ``CREATE_SUSPENDED`` 创建 → 在 resume 之前完成 retained handle
  身份读取、``AssignProcessToJobObject``、``is_member`` 与本 Job ``active``
  核验（false/unknown/查询失败一律 fail-closed，不 resume）；runner 进程硬死
  时内核关闭 Job 句柄并清理 sidecar，**不依赖 runner 的清理代码**。
- 所有清理路径（构造失败 / ``close``）共享同一总 deadline 与同一顺序：
  **先清树**（retained-handle 核验终止 root + ``terminate_tree`` 整 Job 终止与
  核验；根 DEAD 不算整树空）→ **再**取消/join reader/stderr/applier/在途操作 →
  **最后**关闭流（``BufferedReader.close`` 在 reader 持有缓冲锁时可能无界等待，
  因此仅在 reader/stderr 均已退出后执行，否则保留引用）。失败一律
  ``closed=False`` 且资源保留（报告可重试），绝不假称已关闭；``close()``
  并发调用由 ``_close_lock`` 串行化，重复调用幂等。
- 构造失败抛 :class:`EmulatorStartupError`：携带可重试 ``owner``
  （``retry_cleanup()``）与 ``residual`` 残留信息（pid/身份/清理结果），不仅文本。
- sidecar stdin/stdout 为有界二进制帧（u32 长度前缀）；帧超限、断帧、EOF、
  异常、卡住均有明确可见状态；64 位偏移一律十进制字符串（无 JS 浮点）。
- token/秘密不进入 sidecar：协议里没有任何认证字段。

cursor 账本一致性（r2）：

- ``reset_baseline()`` 的账本更新统一发生在 **ack 处理路径**且只应用一次：
  即使调用方已超时，迟到 ack 到达后仍回填实际执行账本；
- reset 未确认（超时且已在途）期间 ``cursors_valid=False``：结构性禁止把旧
  cursor 当续流起点（``diagnostics()["cursors_valid"]`` / 快照 note/recovery
  可见），确认或引擎不可用后由判定收敛，禁止已知失同步继续续流。

已知边界（不夸大）：

- 本模块是 **headless↔headless** 引擎宿主；真实浏览器渲染保真属 P3 验收。
- 引擎序列化能力缺口（滚动区、synchronized output、光标可见性等）由 sidecar
  检测器降级为 partial；详见
  ``docs/design/PAN_TERMINAL_EMULATOR_INTERFACES_20261003.md``。
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, BinaryIO, NoReturn

from .contracts import (
    AppliedSnapshot,
    BackendUnavailableError,
    Fidelity,
    ProcessIdentity,
    Recovery,
)

__all__ = [
    "EmulatorCloseReport",
    "EmulatorProtocolError",
    "EmulatorStartupError",
    "EmulatorUnavailableError",
    "HeadlessEmulator",
    "PROTOCOL_VERSION",
    "StartupCleanupOwner",
]

PROTOCOL_VERSION = 1

_FRAME_STRUCT = struct.Struct("<II")
FRAME_MAX_BYTES = 8 * 1024 * 1024
FRAME_MAX_HEADER_BYTES = 64 * 1024
STDERR_KEEP_BYTES = 8 * 1024

DEFAULT_QUEUE_MAX_BYTES = 4 * 1024 * 1024  # 计划 §13：4 MiB
DEFAULT_QUEUE_MAX_OPS = 8192  # 计划 §13：8192 项（控制命令同预算）
DEFAULT_MAX_PENDING_TAIL = 8 * 1024
DEFAULT_MAX_REJECTED_RANGES = 64
DEFAULT_MAX_REASONS = 64
DEFAULT_MAX_DIAGNOSTICS = 64
DEFAULT_SNAPSHOT_TIMEOUT = 2.0
DEFAULT_CONTROL_TIMEOUT = 2.0
DEFAULT_STARTUP_TIMEOUT = 15.0
DEFAULT_SHUTDOWN_TIMEOUT = 5.0
DEFAULT_KILL_TIMEOUT = 5.0

_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000


class EmulatorProtocolError(RuntimeError):
    """sidecar 帧协议违例（帧超限/断帧/坏 JSON）：可见、有界、fail-closed。"""


class EmulatorUnavailableError(BackendUnavailableError):
    """sidecar 依赖缺失/启动失败：构造时解释；由 runner 决定基本终端策略。"""


class EmulatorStartupError(EmulatorUnavailableError):
    """构造失败（含清理结果）：携带可重试 owner 与残留信息，不仅文本。

    - ``owner``（:class:`StartupCleanupOwner`）：自有（proc/guard/线程）引用，
      可调用 ``retry_cleanup()`` 在有界预算内重试清理（幂等）；
    - ``residual``：残留事实 dict（pid / raw FILETIME 字符串 / 清理核验结果 /
      stderr 摘要），供 runner 决策与审计。
    """

    def __init__(
        self,
        message: str,
        *,
        owner: "StartupCleanupOwner | None" = None,
        residual: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.owner = owner
        self.residual = dict(residual or {})


@dataclass
class StartupCleanupOwner:
    """构造失败后仍可重试清理的自有资源句柄（不扫描陌生 PID）。

    ``retry_cleanup`` 对**同一 owner** 内部串行化（``_lock``）：并发调用不重叠
    执行 ``TerminateProcess``/关资源；``timeout`` 是**该次调用**的总预算（包含
    等锁时间）。抢锁/预算超时返回 ``closed=false + retryable=true`` 且保留引用；
    完成后幂等（已收敛资源再次清理返回 ``closed=true``，不会重复终止制造假 false）。
    """

    proc: Any = None
    guard: Any = None
    pid: int | None = None
    identity: ProcessIdentity | None = None
    reader: Any = None
    stderr: Any = None
    applier: Any = None
    kill_timeout: float = DEFAULT_KILL_TIMEOUT
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    def retry_cleanup(self, *, timeout: float = 10.0) -> dict[str, Any]:
        """重试整 Job 清理（同 owner 串行；总 deadline 含锁等待；幂等、有界）。"""
        t0 = time.monotonic()
        budget = max(0.0, float(timeout))
        acquired = self._lock.acquire(timeout=budget)
        if not acquired:
            return {
                "closed": False,
                "retryable": True,
                "reason": "owner-busy-timeout",
                "deadline_exceeded": True,
                "seconds": round(time.monotonic() - t0, 3),
                "detail": (
                    "owner-busy: another retry_cleanup() holds this owner's lock; "
                    "resources retained, retryable"
                ),
            }
        try:
            remaining = max(0.0, budget - (time.monotonic() - t0))
            if remaining <= 0.0:
                return {
                    "closed": False,
                    "retryable": True,
                    "reason": "owner-deadline-exhausted",
                    "deadline_exceeded": True,
                    "seconds": round(time.monotonic() - t0, 3),
                    "detail": "owner lock acquired after the total deadline expired; retryable",
                }
            detail: list[str] = []
            outcome = _reap_process_resources(
                proc=self.proc,
                guard=self.guard,
                pid=self.pid,
                identity=self.identity,
                reader=self.reader,
                stderr=self.stderr,
                applier=self.applier,
                budget=remaining,
                kill_timeout=self.kill_timeout,
                detail=detail,
            )
            outcome["detail"] = ", ".join(detail) if detail else "ok"
            outcome.setdefault("retryable", not outcome.get("closed", False))
            outcome.setdefault("seconds", round(time.monotonic() - t0, 3))
            return outcome
        finally:
            self._lock.release()


@dataclass(frozen=True)
class EmulatorCloseReport:
    """``HeadlessEmulator.close()`` 的结果（失败保留资源、可重试）。

    ``closed`` 为收敛总判定：进程退出（retained handle）+ 整 Job 核验空 +
    guard 关闭 + reader/stderr/applier 全部 join + 三流全部关闭，缺一即 False
    且相应资源保留供重试。
    """

    closed: bool
    graceful: bool
    forced: bool
    process_exited: bool
    guard_closed: bool
    reader_joined: bool
    stderr_joined: bool
    applier_joined: bool
    streams_closed: bool
    job_verified: bool
    detail: str
    seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "closed": self.closed,
            "graceful": self.graceful,
            "forced": self.forced,
            "process_exited": self.process_exited,
            "guard_closed": self.guard_closed,
            "reader_joined": self.reader_joined,
            "stderr_joined": self.stderr_joined,
            "applier_joined": self.applier_joined,
            "streams_closed": self.streams_closed,
            "job_verified": self.job_verified,
            "detail": self.detail,
            "seconds": self.seconds,
        }


@dataclass
class _ControlCall:
    kind: str
    timeout: float
    event: threading.Event = field(default_factory=threading.Event)
    header: dict[str, Any] | None = None
    payload: bytes = b""
    failure: str | None = None
    expired: bool = False
    sent: bool = False


@dataclass
class _Op:
    op_id: int
    kind: str
    data: bytes | None = None
    abs_start: int = 0
    abs_end: int = 0
    rows: int = 0
    cols: int = 0
    ms: int = 0
    target: str = ""
    call: _ControlCall | None = None
    ledger_applied: bool = False  # reset 账本只应用一次（ack 路径回填）


class _FrameReader:
    """有界二进制帧读取（每帧自校验；断帧/超限 -> ``EmulatorProtocolError``）。"""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream

    def read_frame(self) -> tuple[dict[str, Any], bytes] | None:
        head = self._read_exact(8, allow_eof=True)
        if head is None:
            return None
        total, header_len = _FRAME_STRUCT.unpack(head)
        if (
            total > FRAME_MAX_BYTES
            or header_len > FRAME_MAX_HEADER_BYTES
            or header_len == 0
            or header_len > total
        ):
            raise EmulatorProtocolError(
                f"frame bounds violated: total={total} header={header_len}"
            )
        body = self._read_exact(total)
        if body is None:  # pragma: no cover - defensive (EOF mid-frame)
            raise EmulatorProtocolError("truncated frame body")
        try:
            header = json.loads(body[:header_len].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EmulatorProtocolError(f"bad frame header: {type(exc).__name__}") from exc
        if not isinstance(header, dict):
            raise EmulatorProtocolError("frame header is not an object")
        return header, bytes(body[header_len:])

    def _read_exact(self, n: int, *, allow_eof: bool = False) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._stream.read(n - len(buf))
            if not chunk:
                if allow_eof and not buf:
                    return None
                raise EmulatorProtocolError("channel closed mid-frame")
            buf.extend(chunk)
        return bytes(buf)


def encode_frame(header: dict[str, Any], payload: bytes = b"") -> bytes:
    """编码一帧（供内部与测试使用；超限即拒绝，不截断）。"""
    head = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(head) > FRAME_MAX_HEADER_BYTES:
        raise EmulatorProtocolError(f"header too large: {len(head)}")
    total = len(head) + len(payload)
    if total > FRAME_MAX_BYTES:
        raise EmulatorProtocolError(f"frame too large: {total}")
    return _FRAME_STRUCT.pack(total, len(head)) + head + payload


def _default_sidecar_path() -> Path:
    # 专属路径：packages/core/terminal/emulator_sidecar/（与 emulator.py 同层级）
    return Path(__file__).resolve().parent / "emulator_sidecar" / "sidecar.mjs"


def _resolve_node_binary(explicit: str | None) -> str | None:
    if explicit:
        candidate = Path(explicit)
        return str(candidate) if candidate.is_file() else None
    env_value = os.environ.get("PAN_TERMINAL_NODE_BIN")
    if env_value:
        candidate = Path(env_value)
        return str(candidate) if candidate.is_file() else None
    found = shutil.which("node")
    return str(found) if found else None


def _resume_process(process_handle: int) -> None:
    """Resume a ``CREATE_SUSPENDED`` child (single resume; no thread handle needed)."""
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = int(ntdll.NtResumeProcess(wintypes.HANDLE(int(process_handle))))
    if status != 0:
        raise OSError(f"NtResumeProcess failed: 0x{status & 0xFFFFFFFF:08x}")


def _force_terminate_root(
    proc: Any,
    guard: Any,
    pid: int | None,
    identity: ProcessIdentity | None,
    *,
    budget: float,
    detail: list[str],
    kill_timeout: float = DEFAULT_KILL_TIMEOUT,
) -> dict[str, Any]:
    """清树：retained-handle 核验的 root 终止 + 整 Job 终止与核验。

    以**真实 guard 所有权**为准（Job 成员枚举 / ``TerminateJobObject``），
    不按名字/命令行扫描陌生 PID。根 DEAD **不算**整树空：即使 root 已退出，
    也必须执行 ``terminate_tree`` 并核验剩余成员为空（``remaining == []``）。

    ``kill_timeout``（F3 接线）：身份核验终止的**每次等待上限** =
    ``min(剩余总预算, kill_timeout)``。这是本层的等待参数，不是 OS 级
    ``TerminateProcess`` 硬时限承诺（进程终止完成时刻由内核调度决定）。
    """
    from . import identity as identity_module

    outcome: dict[str, Any] = {
        "root_killed": False,
        "root_dead": False,
        "job_terminate_called": False,
        "job_owned": None,
        "job_verified": False,
        "remaining": None,
        "errors": [],
    }
    deadline = time.monotonic() + max(0.0, float(budget))
    handle = 0
    if proc is not None:
        handle = int(getattr(proc, "_handle", 0) or 0)
    # 1) root：仅在 retained handle 显示 ALIVE 且身份可比对时终止（fail-closed）
    if handle:
        state = identity_module.wait_state(handle)
        if state is identity_module.ProcessStatus.ALIVE:
            expected_ft = (
                identity.created_at_filetime
                if identity is not None
                else None
            )
            if expected_ft is not None:
                # F3：等待上限 = min(剩余总预算, kill_timeout)；不再硬编码。
                kill_wait = min(
                    max(0.0, deadline - time.monotonic()),
                    max(0.0, float(kill_timeout)),
                )
                result = identity_module.kill_verified(
                    int(pid if pid is not None else proc.pid),
                    identity,
                    wait_timeout=kill_wait,
                )
                outcome["root_killed"] = bool(result.killed)
                if not result.killed and result.reason != "not_running":
                    outcome["errors"].append(f"kill-verified:{result.reason}")
            else:
                outcome["errors"].append("kill-verified:expected-identity-missing")
        outcome["root_dead"] = identity_module.wait_state(handle) is (
            identity_module.ProcessStatus.DEAD
        )
    # 2) 整 Job 终止与核验（root 已死也必须做：根 DEAD 非整树空）
    if guard is None:
        outcome["errors"].append("guard-unavailable")
    elif not getattr(guard, "handle_open", False):
        # Job 句柄已被本进程关闭（前一轮 close 的 guard.close）：KILL_ON_JOB_CLOSE
        # 的语义是"关闭最后一个句柄 -> 内核终止全部关联进程并销毁 Job"。
        # F6：这是**有明示假设链的语义证据**（CloseHandle 成功 + guard 创建时自证
        # KILL_ON_JOB_CLOSE 且无 breakaway 位 + 句柄默认不可继承 + 单持有者），
        # 不是 post-close 内核查询；该时刻 active/成员查询均不可用。
        outcome["job_verified"] = True
        outcome["job_closed_previously"] = True
    else:
        try:
            owned, remaining = guard.terminate_tree(
                pid, timeout=min(2.5, max(0.0, deadline - time.monotonic()))
            )
            outcome["job_terminate_called"] = True
            outcome["job_owned"] = owned
            outcome["remaining"] = remaining
            outcome["job_verified"] = remaining == []
        except Exception as exc:  # noqa: BLE001 - 所有权未知必须如实上报
            outcome["errors"].append(f"terminate-tree:{type(exc).__name__}")
    # 3) root 复核（terminate_tree 可能刚刚终止了它）
    if handle and not outcome["root_dead"]:
        outcome["root_dead"] = identity_module.wait_state(handle) is (
            identity_module.ProcessStatus.DEAD
        )
    if not outcome["root_dead"]:
        outcome["errors"].append("root-not-dead")
    return outcome


def _close_proc_streams(proc: Any, detail: list[str]) -> bool:
    """关闭子进程三个流；失败如实记录并返回 False（不抛）。

    调用前提：reader/stderr 线程已退出（否则 ``BufferedReader.close`` 可能在
    对方持有缓冲锁时无界等待——r2 审查 P11 的根因）。
    """
    ok = True
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(proc, name, None)
        if stream is None:
            continue
        try:
            stream.close()
        except (OSError, ValueError):
            ok = False
            detail.append(f"{name}-close-error")
    return ok


def _reap_process_resources(
    proc: Any,
    guard: Any,
    pid: int | None,
    identity: ProcessIdentity | None,
    reader: Any = None,
    stderr: Any = None,
    applier: Any = None,
    *,
    budget: float = 5.0,
    kill_timeout: float = DEFAULT_KILL_TIMEOUT,
    detail: list[str] | None = None,
) -> dict[str, Any]:
    """统一整 Job 清理与核验（构造失败 / ``close`` / owner 重试共用；全程有界）。

    顺序：**先清树**（root 终止 + ``terminate_tree`` + 核验）→ **再**取消/join
    reader/stderr/applier/在途操作 → **最后**关闭流 → ``guard.close()``（内核级
    兜底）。失败保留引用，``closed=False`` 可重试。``budget`` 是本次调用的总
    预算（不设最小下限；预算为 0 时各步骤快速保守返回）。``kill_timeout`` 透传
    给 ``_force_terminate_root``（核对每次 kill 等待上限，F3）。
    """
    detail = detail if detail is not None else []
    t0 = time.monotonic()
    deadline = t0 + max(0.0, float(budget))
    outcome: dict[str, Any] = {
        "root_dead": False,
        "root_killed": False,
        "job_terminate_called": False,
        "job_verified": False,
        "remaining": None,
        "reader_joined": False,
        "stderr_joined": False,
        "applier_joined": False,
        "streams_closed": False,
        "guard_closed": False,
        "errors": [],
        "seconds": 0.0,
        "closed": False,
    }
    # 1) 清树（可注入：测试以 monkeypatch 替换 _force_terminate_root 模拟终止失败）
    term = _force_terminate_root(
        proc,
        guard,
        pid,
        identity,
        budget=max(0.0, deadline - time.monotonic()),
        detail=detail,
        kill_timeout=kill_timeout,
    )
    outcome["root_dead"] = bool(term.get("root_dead"))
    outcome["root_killed"] = bool(term.get("root_killed"))
    outcome["job_terminate_called"] = bool(term.get("job_terminate_called"))
    outcome["job_verified"] = bool(term.get("job_verified"))
    outcome["remaining"] = term.get("remaining")
    outcome["job_owned"] = term.get("job_owned")
    outcome["errors"].extend(term.get("errors") or [])

    def _join(thread: Any, slice_seconds: float) -> bool:
        if thread is None:
            return True
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=min(slice_seconds, remaining))
        return not thread.is_alive()

    # 2) 取消/join 线程与在途操作（applier 已由调用方以 closing 唤醒）
    outcome["reader_joined"] = _join(reader, 2.5)
    outcome["stderr_joined"] = _join(stderr, 1.5)
    outcome["applier_joined"] = _join(applier, 1.0)
    # 3) 关闭流：仅在 reader 与 stderr 均已退出后（否则保留引用，可重试）
    if outcome["reader_joined"] and outcome["stderr_joined"] and proc is not None:
        outcome["streams_closed"] = _close_proc_streams(proc, detail)
        if not outcome["streams_closed"]:
            detail.append("stream-close-failed-retryable")
    else:
        detail.append("streams-retained-reader-active")
    # 4) guard.close（最后一个 Job 句柄：内核终止整树；失败保留可重试）
    if guard is not None:
        try:
            outcome["guard_closed"] = bool(guard.close())
        except Exception as exc:  # noqa: BLE001 - 如实上报
            outcome["guard_closed"] = False
            outcome["errors"].append(f"guard-close:{type(exc).__name__}")
        if not outcome["guard_closed"]:
            detail.append("guard-close-failed-retryable")
    else:
        outcome["guard_closed"] = True

    # 5) 最终 root 复核：终止是异步的（TerminateProcess 返回 != signaled），
    #    本步骤用**剩余预算内**的有界确认窗口避免"已发起终止但尚未 signaled"
    #    被过早采样成假 process_exited=False；不延长总预算（deadline 封顶），
    #    预算耗尽时窗口为 0（立即采样一次）。
    if not outcome["root_dead"] and proc is not None:
        from . import identity as identity_module

        handle = int(getattr(proc, "_handle", 0) or 0)
        if handle:
            confirm_until = min(deadline, time.monotonic() + 0.75)
            while time.monotonic() < confirm_until:
                if identity_module.wait_state(handle) is identity_module.ProcessStatus.DEAD:
                    break
                time.sleep(0.02)
            outcome["root_dead"] = identity_module.wait_state(handle) is (
                identity_module.ProcessStatus.DEAD
            )

    outcome["closed"] = bool(
        outcome["root_dead"]
        and outcome["job_verified"]
        and outcome["guard_closed"]
        and outcome["reader_joined"]
        and outcome["stderr_joined"]
        and outcome["applier_joined"]
        and outcome["streams_closed"]
    )
    outcome["seconds"] = round(time.monotonic() - t0, 3)
    return outcome


class HeadlessEmulator:
    """runner 生命周期内常驻的权威仿真器（Node headless-xterm sidecar 宿主）。

    直接构造即启动 sidecar；构造失败抛 :class:`EmulatorStartupError`
    （依赖缺失/门禁失败/握手失败；携带可重试 owner 与残留信息）。
    生产装配由 runner 负责；本类不触碰 PTY。
    """

    ENGINE_FAMILY = "xterm-headless+serialize"

    def __init__(
        self,
        *,
        cols: int = 80,
        rows: int = 24,
        scrollback: int = 1000,
        start_cursor: int = 0,
        node_binary: str | None = None,
        sidecar_path: str | os.PathLike[str] | None = None,
        max_queue_bytes: int = DEFAULT_QUEUE_MAX_BYTES,
        max_queue_ops: int = DEFAULT_QUEUE_MAX_OPS,
        max_pending_tail_bytes: int = DEFAULT_MAX_PENDING_TAIL,
        max_rejected_ranges: int = DEFAULT_MAX_REJECTED_RANGES,
        max_reasons: int = DEFAULT_MAX_REASONS,
        max_diagnostics: int = DEFAULT_MAX_DIAGNOSTICS,
        snapshot_timeout: float = DEFAULT_SNAPSHOT_TIMEOUT,
        control_timeout: float = DEFAULT_CONTROL_TIMEOUT,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
        kill_timeout: float = DEFAULT_KILL_TIMEOUT,
    ) -> None:
        # --- immutable configuration -------------------------------------
        self._cols = max(1, int(cols))
        self._rows = max(1, int(rows))
        self._scrollback = max(0, int(scrollback))
        self._max_queue_bytes = max(1, int(max_queue_bytes))
        self._max_queue_ops = max(1, int(max_queue_ops))
        self._max_pending_tail_bytes = max(0, int(max_pending_tail_bytes))
        self._max_rejected_ranges = max(1, int(max_rejected_ranges))
        self._max_reasons = max(1, int(max_reasons))
        self._max_diagnostics = max(1, int(max_diagnostics))
        self._snapshot_timeout = max(0.01, float(snapshot_timeout))
        self._control_timeout = max(0.01, float(control_timeout))
        self._startup_timeout = max(0.1, float(startup_timeout))
        self._shutdown_timeout = max(0.1, float(shutdown_timeout))
        self._kill_timeout = max(0.1, float(kill_timeout))

        # --- producer-side ledger (absolute byte offsets) -----------------
        self._lock = threading.RLock()
        self._ops: deque[_Op] = deque()
        self._queue_bytes = 0
        self._queue_ops = 0
        self._producer_frontier = int(start_cursor)
        self._expected_next = int(start_cursor)
        self._applied_cursor = int(start_cursor)
        self._next_op_id = 1

        self._feed_lag = False
        self._gap_ranges: deque[tuple[int, int]] = deque()
        self._duplicate_ranges: deque[tuple[int, int]] = deque()
        # 严格有界（max_rejected_ranges + overflow 计数）。
        self._rejected_ranges: list[tuple[int, int]] = []
        self._reasons: list[str] = []
        self._reason_overflow = 0
        self._overflow: dict[str, int] = {}
        self._counters: dict[str, int] = {
            "feed_ops": 0,
            "control_ops": 0,
            "rejected_feed_blocks": 0,
            "rejected_feed_bytes": 0,
            "duplicate_blocks": 0,
            "gap_blocks": 0,
            "control_expired_skipped": 0,
            "control_queue_overflow": 0,
            "control_timeouts": 0,
            "late_results": 0,
            "ops_dropped_engine_dead": 0,
            "resets": 0,
            "unconfirmed_ops": 0,
            "resize_rejected": 0,
            "resize_confirmed": 0,
            "engine_op_errors": 0,
        }

        self._reset_count = 0
        self._baseline_cursor = int(start_cursor)
        # reset 已发送但未确认：期间结构性禁止旧 cursor 作为续流起点
        self._reset_unconfirmed = False
        self._applied_resize: list[int] | None = None
        self._engine_error_note: str | None = None
        self._sticky_error_note: str | None = None
        self._engine_dead = False
        self._closing = False
        self._closed = False
        self._close_report: EmulatorCloseReport | None = None
        self._close_lock = threading.Lock()

        self._cond = threading.Condition(self._lock)
        self._ack_lock = threading.Lock()
        self._ack_cond = threading.Condition(self._ack_lock)
        self._inflight_op_id: int | None = None
        self._inflight_ack: tuple[dict[str, Any], bytes] | None = None

        self._ready_event = threading.Event()
        self._ready_info: dict[str, Any] | None = None
        self._startup_failure: str | None = None
        self._engine_desc = self.ENGINE_FAMILY

        self._stderr_lock = threading.Lock()
        self._stderr_buf = bytearray()
        self._stderr_dropped = 0

        self._proc: subprocess.Popen[bytes] | None = None
        self._guard: Any = None
        self._sidecar_pid: int | None = None
        self._sidecar_identity: ProcessIdentity | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._applier_thread: threading.Thread | None = None

        # --- platform gate ------------------------------------------------
        if sys.platform != "win32":
            raise EmulatorStartupError(
                "HeadlessEmulator 生产宿主仅支持 Windows（Job Object 所有权）；"
                f"sys.platform={sys.platform!r}",
                owner=None,
                residual={"reason": "unsupported-platform"},
            )

        node = _resolve_node_binary(node_binary)
        if not node:
            raise EmulatorStartupError(
                "未找到 Node 运行时：sidecar 不可用。请安装 Node（>=18）或通过 "
                "node_binary / PAN_TERMINAL_NODE_BIN 指定；终端基本策略由 runner 决定。",
                owner=None,
                residual={"reason": "node-missing"},
            )
        sidecar = Path(sidecar_path) if sidecar_path is not None else _default_sidecar_path()
        if not sidecar.is_file():
            raise EmulatorStartupError(
                f"sidecar 脚本不存在：{sidecar}",
                owner=None,
                residual={"reason": "sidecar-script-missing", "sidecar_path": str(sidecar)},
            )

        self._node_binary = node
        self._sidecar_path = sidecar
        self._spawn_and_handshake()

    # ------------------------------------------------------------------
    # spawn / handshake
    # ------------------------------------------------------------------

    def _spawn_and_handshake(self) -> None:
        from . import guard as guard_module
        from . import identity as identity_module

        try:
            self._guard = guard_module.JobObjectGuard()
        except Exception as exc:
            raise EmulatorStartupError(
                f"Job 守卫创建失败（{type(exc).__name__}: {exc}）",
                owner=StartupCleanupOwner(),
                residual={"reason": "guard-create-failed"},
            ) from exc

        flags = _CREATE_SUSPENDED | _CREATE_NO_WINDOW
        try:
            self._proc = subprocess.Popen(
                [self._node_binary, str(self._sidecar_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=flags,
                cwd=str(self._sidecar_path.parent),
            )
        except OSError as exc:
            self._fail_startup(
                f"sidecar 进程创建失败（{type(exc).__name__}: {exc}）", cause=exc
            )

        self._sidecar_pid = int(self._proc.pid)
        try:
            # retained handle 身份读取先于 assign/resume：此后任何失败都有
            # 可核验身份用于清理（kill_verified 拒绝无身份目标）。
            ft = identity_module.read_creation_filetime(int(self._proc._handle))  # type: ignore[attr-defined]
            if ft is None:
                raise OSError("无法读取 sidecar 创建时间（retained handle，fail-closed）")
            self._sidecar_identity = ProcessIdentity(
                self._sidecar_pid, created_at_filetime=int(ft)
            )
            self._guard.assign(int(self._proc._handle))  # type: ignore[attr-defined]
            # assign 之后、resume 之前：成员与 active 核验。
            # false / unknown / 查询失败一律 fail-closed（不 resume、不发布 ready）。
            if not self._guard.is_member(int(self._proc._handle)):  # type: ignore[attr-defined]
                raise OSError("assign 后 is_member=False：sidecar 不在自有 Job（fail-closed）")
            active = self._guard.active_processes()
            if active is None:
                raise OSError("Job active 查询失败（unknown，fail-closed）")
            if int(active) < 1:
                raise OSError(f"Job active={int(active)}：sidecar 未计入（fail-closed）")
            _resume_process(int(self._proc._handle))  # type: ignore[attr-defined]
        except Exception as exc:
            self._fail_startup(f"sidecar 门禁失败（{type(exc).__name__}: {exc}）", cause=exc)

        self._stderr_thread = threading.Thread(
            target=self._stderr_loop, name="emulator-stderr", daemon=True
        )
        self._stderr_thread.start()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="emulator-reader", daemon=True
        )
        self._reader_thread.start()

        hello = {
            "v": PROTOCOL_VERSION,
            "type": "hello",
            "cols": self._cols,
            "rows": self._rows,
            "scrollback": self._scrollback,
            "max_pending_tail": self._max_pending_tail_bytes,
            "max_reasons": self._max_reasons,
            "max_rejected_ranges": self._max_rejected_ranges,
            "start_cursor": str(self._producer_frontier),
        }
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(encode_frame(hello))
            self._proc.stdin.flush()
        except (OSError, ValueError, EmulatorProtocolError) as exc:
            self._fail_startup(f"sidecar hello 写入失败（{type(exc).__name__}）", cause=exc)

        if not self._ready_event.wait(self._startup_timeout):
            self._fail_startup(
                f"sidecar 启动握手超时（{self._startup_timeout}s）；"
                f"stderr={self._stderr_digest()!r}"
            )
        if self._startup_failure is not None:
            self._fail_startup(f"sidecar 不可用：{self._startup_failure}")

        self._applier_thread = threading.Thread(
            target=self._applier_loop, name="emulator-applier", daemon=True
        )
        self._applier_thread.start()

    def _fail_startup(self, reason: str, *, cause: BaseException | None = None) -> NoReturn:
        """构造失败统一出口：有界整 Job 清理 + 携带 owner/残留信息的异常。

        清理顺序与 ``close`` 一致（先清树 → join 线程 → 关流 → guard.close），
        总预算为 ``shutdown_timeout``；异常携带可重试 ``owner``（``retry_cleanup``）
        与 ``residual``（pid/身份/清理核验/stderr 摘要），不仅文本。
        """
        detail: list[str] = []
        owner = StartupCleanupOwner(
            proc=self._proc,
            guard=self._guard,
            pid=self._sidecar_pid,
            identity=self._sidecar_identity,
            reader=self._reader_thread,
            stderr=self._stderr_thread,
            applier=self._applier_thread,
            kill_timeout=self._kill_timeout,
        )
        outcome = _reap_process_resources(
            proc=self._proc,
            guard=self._guard,
            pid=self._sidecar_pid,
            identity=self._sidecar_identity,
            reader=self._reader_thread,
            stderr=self._stderr_thread,
            applier=self._applier_thread,
            budget=self._shutdown_timeout,
            kill_timeout=self._kill_timeout,
            detail=detail,
        )
        residual = {
            "reason": reason,
            "pid": self._sidecar_pid,
            "identity_filetime": (
                str(self._sidecar_identity.created_at_filetime)
                if self._sidecar_identity is not None
                else None
            ),
            "process_exited": outcome["root_dead"],
            "job_verified": outcome["job_verified"],
            "guard_closed": outcome["guard_closed"],
            "cleanup_closed": outcome["closed"],
            "cleanup_seconds": outcome["seconds"],
            "cleanup_detail": ", ".join(detail) if detail else "ok",
            "cleanup_errors": list(outcome["errors"]),
            "stderr_digest": self._stderr_digest(),
        }
        message = (
            f"{reason}；清理={'converged' if outcome['closed'] else 'retained'}"
            f"（{outcome['seconds']}s, pid={self._sidecar_pid}）"
        )
        raise EmulatorStartupError(message, owner=owner, residual=residual) from cause

    # ------------------------------------------------------------------
    # background threads
    # ------------------------------------------------------------------

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    return
                with self._stderr_lock:
                    room = STDERR_KEEP_BYTES - len(self._stderr_buf)
                    if room > 0:
                        self._stderr_buf.extend(chunk[:room])
                    self._stderr_dropped += max(0, len(chunk) - max(room, 0))
        except (OSError, ValueError):
            return

    def _stderr_digest(self) -> str:
        with self._stderr_lock:
            text = bytes(self._stderr_buf).decode("utf-8", errors="replace")
            if self._stderr_dropped:
                text += f" ...(+{self._stderr_dropped}B dropped)"
            return text[-2048:]

    def _reader_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        reader = _FrameReader(proc.stdout)
        while True:
            try:
                frame = reader.read_frame()
            except EmulatorProtocolError as exc:
                self._note_engine_failure(f"protocol-error:{exc}")
                return
            except Exception as exc:  # noqa: BLE001 - engine faults must stay visible
                self._note_engine_failure(f"read-error:{type(exc).__name__}")
                return
            if frame is None:
                self._note_engine_failure("eof")
                return
            header, payload = frame
            self._dispatch_frame(header, payload)

    def _dispatch_frame(self, header: dict[str, Any], payload: bytes) -> None:
        frame_type = str(header.get("type", ""))
        if frame_type == "ready":
            self._ready_info = header
            self._engine_desc = str(header.get("engine") or self.ENGINE_FAMILY)
            self._ready_event.set()
            return
        if frame_type == "fatal":
            code = str(header.get("code", "fatal"))
            detail = str(header.get("detail", ""))[:512]
            self._startup_failure = f"{code}:{detail}"
            self._note_engine_failure(f"fatal:{code}")
            return
        if frame_type in ("applied", "snapshot", "barrier", "error"):
            self._deliver_ack(header, payload)
            return
        with self._lock:
            self._count_locked("unexpected_frames")

    def _deliver_ack(self, header: dict[str, Any], payload: bytes) -> None:
        with self._ack_lock:
            if header.get("op") == self._inflight_op_id:
                self._inflight_ack = (header, payload)
                self._ack_cond.notify_all()
            else:
                with self._lock:
                    self._count_locked("late_results")

    def _note_engine_failure(self, reason: str) -> None:
        """sidecar 不可用：sticky 降级；唤醒所有等待者；清空队列。"""
        pending_calls: list[_ControlCall] = []
        with self._lock:
            self._engine_dead = True
            if self._engine_error_note is None:
                self._engine_error_note = reason
            while self._ops:
                op = self._ops.popleft()
                self._release_op_locked(op)
                if op.call is not None:
                    pending_calls.append(op.call)
            self._cond.notify_all()
        with self._ack_lock:
            self._ack_cond.notify_all()
        for call in pending_calls:
            self._finish_call(call, failure=f"engine-unavailable:{reason}")
        self._ready_event.set()

    # ------------------------------------------------------------------
    # queue helpers (all called under self._lock)
    # ------------------------------------------------------------------

    def _count_locked(self, name: str, amount: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + int(amount)

    def _record_reason_locked(self, reason: str) -> None:
        if reason in self._reasons:
            return
        if len(self._reasons) >= self._max_reasons:
            self._reason_overflow += 1
            return
        self._reasons.append(reason)

    def _record_overflow_locked(self, bucket: str) -> None:
        self._overflow[bucket] = self._overflow.get(bucket, 0) + 1

    def _record_bounded_range_locked(
        self, ranges: deque[tuple[int, int]], bucket: str, start: int, end: int
    ) -> None:
        if len(ranges) >= self._max_rejected_ranges:
            self._record_overflow_locked(bucket)
            return
        ranges.append((int(start), int(end)))

    def _can_enqueue_locked(self, nbytes: int) -> bool:
        if self._queue_ops + 1 > self._max_queue_ops:
            return False
        if self._queue_bytes + nbytes > self._max_queue_bytes:
            return False
        return True

    def _release_op_locked(self, op: _Op) -> None:
        self._queue_ops = max(0, self._queue_ops - 1)
        if op.data:
            self._queue_bytes = max(0, self._queue_bytes - len(op.data))

    def _finish_call(self, call: _ControlCall, *, failure: str | None = None) -> None:
        if failure is not None and call.failure is None:
            call.failure = failure
        call.event.set()

    def _enqueue_control(
        self,
        kind: str,
        timeout: float,
        *,
        payload: bytes = b"",
        rows: int = 0,
        cols: int = 0,
    ) -> _ControlCall | None:
        call = _ControlCall(kind=kind, timeout=timeout)
        nbytes = len(payload)
        with self._lock:
            if self._closing or self._closed or self._engine_dead:
                return None
            if not self._can_enqueue_locked(nbytes):
                self._record_overflow_locked("control_queue")
                self._count_locked("control_queue_overflow")
                self._record_reason_locked("CONTROL_QUEUE_OVERFLOW")
                return None
            op = _Op(
                op_id=self._next_op_id,
                kind=kind,
                call=call,
                data=payload or None,
                rows=max(1, int(rows)) if rows else 0,
                cols=max(1, int(cols)) if cols else 0,
            )
            self._next_op_id += 1
            self._ops.append(op)
            self._queue_ops += 1
            self._queue_bytes += nbytes
            self._count_locked("control_ops")
            self._cond.notify_all()
        return call

    # ------------------------------------------------------------------
    # AuthoritativeEmulator protocol surface
    # ------------------------------------------------------------------

    @property
    def feed_lag(self) -> bool:
        with self._lock:
            return self._feed_lag

    def feed(self, data: bytes) -> None:
        """连续供料入口：视作从当前期望位置追加。"""
        with self._lock:
            seq = self._expected_next
        self.feed_at(seq, data)

    def feed_at(self, seq: int, data: bytes) -> None:
        """runtime ``OutputConsumer``：入参 ``(绝对字节偏移, 原始字节)`。

        快速、有界、永不阻塞：只做绝对偏移核验与入队/拒绝记账；不等待 sidecar。
        """
        payload = bytes(data)
        n = len(payload)
        if n == 0:
            return
        start = int(seq)
        end = start + n
        with self._lock:
            expected = self._expected_next
            if start < expected:
                # 重复/双消费供料：拒绝再次投递（防尾字节双消费），显式降级。
                self._record_bounded_range_locked(
                    self._duplicate_ranges, "duplicate_range", start, end
                )
                self._count_locked("duplicate_blocks")
                self._record_reason_locked("DUPLICATE_FEED_BYTES")
                self._producer_frontier = max(self._producer_frontier, end)
                return
            if start > expected:
                self._record_bounded_range_locked(self._gap_ranges, "gap_range", expected, start)
                self._count_locked("gap_blocks")
                self._record_reason_locked("SOURCE_GAP_STICKY")
            self._expected_next = end
            self._producer_frontier = max(self._producer_frontier, end)
            if self._closing or self._closed or self._engine_dead:
                self._count_locked("ops_dropped_engine_dead")
                return
            if not self._can_enqueue_locked(n):
                self._feed_lag = True
                self._count_locked("rejected_feed_blocks")
                self._count_locked("rejected_feed_bytes", n)
                # 严格有界：拒绝区间最多保留 max_rejected_ranges 条，溢出只计数
                self._record_bounded_range_locked(
                    self._rejected_ranges, "rejected_range", start, end
                )
                self._record_reason_locked("FEED_QUEUE_OVERFLOW")
                return
            op = _Op(
                op_id=self._next_op_id,
                kind="feed",
                data=payload,
                abs_start=start,
                abs_end=end,
            )
            self._next_op_id += 1
            self._ops.append(op)
            self._queue_bytes += n
            self._queue_ops += 1
            self._count_locked("feed_ops")
            self._cond.notify_all()

    def resize(self, rows: int, cols: int) -> None:
        """入队一次 resize（与 feed 同一有序通道；占同一 op 预算）。

        **无 per-op 回执**：调用方不得由"已调用 resize"推断终端/PTY/引擎三方
        尺寸一致；需要确认时使用 :meth:`resize_wait`（不改冻结协议签名）。
        """
        with self._lock:
            if self._closing or self._closed or self._engine_dead:
                self._count_locked("ops_dropped_engine_dead")
                return
            if not self._can_enqueue_locked(0):
                self._record_overflow_locked("control_queue")
                self._count_locked("control_queue_overflow")
                self._count_locked("resize_rejected")
                self._record_reason_locked("CONTROL_QUEUE_OVERFLOW")
                return
            op = _Op(
                op_id=self._next_op_id,
                kind="resize",
                rows=max(1, int(rows)),
                cols=max(1, int(cols)),
            )
            self._next_op_id += 1
            self._ops.append(op)
            self._queue_ops += 1
            self._count_locked("control_ops")
            self._cond.notify_all()

    def resize_wait(self, rows: int, cols: int, *, timeout: float | None = None) -> bool:
        """入队 resize 并等待引擎确认（三方一致核对面；不改冻结协议签名）。

        返回 ``True`` 仅当 sidecar 回报的 ``rows/cols`` 与请求一致；超时（排队中
        过期则不执行）、队列满拒绝、引擎不可用、确认值不一致均返回 ``False``。
        """
        t = self._control_timeout if timeout is None else max(0.01, float(timeout))
        rows_i, cols_i = max(1, int(rows)), max(1, int(cols))
        call = self._enqueue_control("resize", t, rows=rows_i, cols=cols_i)
        if call is None:
            with self._lock:
                if not (self._closing or self._closed or self._engine_dead):
                    self._count_locked("resize_rejected")
            return False
        if not call.event.wait(t):
            self._expire_control(call)
            with self._lock:
                self._count_locked("control_timeouts")
                self._count_locked("resize_rejected")
            return False
        if call.failure is not None or call.header is None:
            return False
        header = call.header
        return (
            str(header.get("type")) == "applied"
            and int(header.get("rows", -1)) == rows_i
            and int(header.get("cols", -1)) == cols_i
        )

    def snapshot(self, *, timeout: float | None = None) -> AppliedSnapshot:
        t = self._snapshot_timeout if timeout is None else max(0.01, float(timeout))
        return self._control_snapshot("snapshot", t)

    def reset_baseline(self) -> AppliedSnapshot:
        return self._control_snapshot("reset", self._control_timeout)

    def restore_screen(self, serialized_screen: str) -> bool:
        """协议 A 恢复入口：把快照的序列化屏幕状态写入引擎。

        标准恢复流程（不预喂 pending tail、不双消费）：

        1. ``snap = snapshot()``；
        2. ``restore_screen(snap.serialized_screen)`` 重建屏幕状态；
        3. 从 ``snap.cursor`` 重拉**原始字节**并 ``feed_at`` 续流——pending tail
           的字节自然包含在这段重放里，只消费一次。

        状态重放**不计入**源流绝对偏移账本（它不是源数据）。返回 ``False``
        表示引擎不可用或队列满，调用方必须按降级（fresh view）处理。
        """
        payload = serialized_screen.encode("utf-8")
        call = self._enqueue_control("restore", self._control_timeout, payload=payload)
        if call is None:
            return False
        if not call.event.wait(call.timeout):
            self._expire_control(call)
            with self._lock:
                self._count_locked("control_timeouts")
            return False
        return call.failure is None

    def barrier(self, *, timeout: float | None = None) -> dict[str, Any]:
        """诊断/测试用 barrier：返回已应用位置与引擎状态（有界超时）。"""
        t = self._control_timeout if timeout is None else max(0.01, float(timeout))
        call = self._enqueue_control("barrier", t)
        if call is None:
            return {"ok": False, "reason": "control-unavailable", **self._ledger_snapshot()}
        if not call.event.wait(t):
            self._expire_control(call)
            self._count_locked("control_timeouts")
            return {"ok": False, "reason": "barrier-timeout", **self._ledger_snapshot()}
        if call.failure:
            return {"ok": False, "reason": call.failure, **self._ledger_snapshot()}
        assert call.header is not None
        merged = dict(call.header)
        merged.update(self._ledger_snapshot())
        return merged

    def _control_snapshot(self, kind: str, timeout: float) -> AppliedSnapshot:
        with self._lock:
            if self._closing or self._closed:
                return self._degraded_snapshot_now("emulator-closing")
        call = self._enqueue_control(kind, timeout)
        if call is None:
            if self._engine_dead:
                return self._degraded_snapshot_now("engine-unavailable")
            return self._degraded_snapshot_now("control-queue-overflow")
        if not call.event.wait(timeout):
            self._expire_control(call)
            with self._lock:
                self._count_locked("control_timeouts")
                if kind == "reset":
                    # 结构性禁止：已发送（在途）的 reset 结果未知时，旧 cursor
                    # 不得作为续流起点，直到迟到 ack 回填或引擎不可用。
                    self._reset_unconfirmed = bool(call.sent)
            return self._timeout_snapshot(kind, timeout, call)
        if call.failure:
            return self._degraded_snapshot_now(f"engine-unavailable:{call.failure}")
        assert call.header is not None
        # reset 账本更新统一发生在 ack 处理路径（_complete_op），此处不再重复应用，
        # 保证"只应用一次"且迟到 ack 同样回填。
        return self._compose_snapshot(call.header, call.payload)

    def _expire_control(self, call: _ControlCall) -> None:
        with self._lock:
            call.expired = True

    # ------------------------------------------------------------------
    # result handling
    # ------------------------------------------------------------------

    def _apply_reset_result(self, header: dict[str, Any]) -> None:
        """reset 确认后的新基线记账（按命令实际执行位置，不取 producer frontier）。

        旧基线之前的 gap/duplicate 记录同时清除：它们属于 reset 之前的历史，
        不得永久污染新基线的 cursor。``feed_lag`` 与检测原因**不**被清除——
        reset 不假恢复 full（``BASELINE_RESET_FRESH_VIEW`` 保持 partial）。
        """
        with self._lock:
            self._reset_count += 1
            self._count_locked("resets")
            baseline = int(header.get("baseline_frontier", "-1"))
            if baseline < 0:
                baseline = int(header.get("applied_bytes", str(self._applied_cursor)))
            self._baseline_cursor = baseline
            applied = int(header.get("applied_bytes", "0"))
            self._applied_cursor = max(self._applied_cursor, applied)
            self._gap_ranges.clear()
            self._duplicate_ranges.clear()
            self._reasons = [
                reason
                for reason in self._reasons
                if reason not in ("SOURCE_GAP_STICKY", "DUPLICATE_FEED_BYTES")
            ]
            self._record_reason_locked("BASELINE_RESET_FRESH_VIEW")
            # 确认到达：结构性禁止解除（账本与实际执行位置已对齐）
            self._reset_unconfirmed = False

    def _ledger_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "producer_frontier": self._producer_frontier,
                "expected_next": self._expected_next,
                "applied_cursor": self._applied_cursor,
                "queue_bytes": self._queue_bytes,
                "queue_ops": self._queue_ops,
                "feed_lag": self._feed_lag,
                "reset_count": self._reset_count,
                "baseline_cursor": self._baseline_cursor,
                "engine_dead": self._engine_dead,
            }

    def _python_reasons_locked(self) -> list[str]:
        reasons: list[str] = []
        if self._engine_error_note:
            reasons.append("ENGINE_UNAVAILABLE")
        if self._feed_lag:
            reasons.append("FEED_QUEUE_OVERFLOW")
        reasons.extend(self._reasons)
        return reasons

    def _compose_snapshot(self, header: dict[str, Any], payload: bytes) -> AppliedSnapshot:
        serialized = payload.decode("utf-8", errors="replace")
        node_applied = int(header.get("applied_bytes", "0"))
        node_rows = int(header.get("rows", self._rows))
        node_cols = int(header.get("cols", self._cols))
        node_fidelity = str(header.get("fidelity", "partial"))
        node_recovery = str(header.get("recovery", "partial"))
        node_reasons = [str(r) for r in (header.get("reasons") or [])]

        with self._lock:
            self._applied_cursor = max(self._applied_cursor, node_applied)
            py_reasons = self._python_reasons_locked()
            feed_lag = self._feed_lag
            engine_dead = self._engine_dead
            engine_note = self._engine_error_note
            sticky_note = self._sticky_error_note
            duplicate_blocks = self._counters.get("duplicate_blocks", 0)
            gap_info = list(self._gap_ranges)
            applied = self._applied_cursor
            reset_unconfirmed = self._reset_unconfirmed

        reasons: list[str] = []
        reason_overflow = False
        for reason in py_reasons + node_reasons:
            if reason in reasons:
                continue
            if len(reasons) >= self._max_reasons:
                reason_overflow = True
                continue
            reasons.append(reason)
        if reason_overflow and "REASON_TABLE_OVERFLOW" not in reasons:
            if len(reasons) >= self._max_reasons:
                reasons[-1] = "REASON_TABLE_OVERFLOW"
            else:
                reasons.append("REASON_TABLE_OVERFLOW")

        # reset 未确认（已发送、结果未知）期间：旧 cursor 被结构性禁止续流
        degraded = feed_lag or sticky_note is not None or reset_unconfirmed
        if engine_dead:
            fidelity = Fidelity.UNAVAILABLE
            recovery = Recovery.NONE
        else:
            fidelity = Fidelity.FULL if not reasons else Fidelity.PARTIAL
            if node_fidelity == "unavailable":
                fidelity = Fidelity.UNAVAILABLE
            if degraded:
                recovery = Recovery.DEGRADED
            elif reasons:
                recovery = Recovery.PARTIAL
            else:
                recovery = Recovery.FULL
            if node_recovery == "degraded":
                recovery = Recovery.DEGRADED

        note_bits = [
            f"protocol=A(cursor={applied}, pull original bytes from cursor; do not pre-feed pending tail)",
            f"engine={self._engine_desc}",
        ]
        if gap_info:
            note_bits.append(f"source_gap={gap_info[:1]}")
        if duplicate_blocks:
            note_bits.append(f"duplicate_blocks={duplicate_blocks}")
        if reset_unconfirmed:
            note_bits.append("reset-unconfirmed(cursors_valid=false; do not resume from old cursor)")
        if reasons:
            note_bits.append("reasons=" + ",".join(reasons[:8]))
        if engine_note:
            note_bits.append(f"engine_error={engine_note}")
        if sticky_note:
            note_bits.append(f"engine_sticky_error={sticky_note}")
        return AppliedSnapshot(
            serialized_screen=serialized,
            cursor=applied,
            rows=node_rows,
            cols=node_cols,
            fidelity=fidelity,
            recovery=recovery,
            feed_lag=feed_lag,
            engine=self._engine_desc,
            note="; ".join(note_bits),
        )

    def _degraded_snapshot_now(self, reason: str) -> AppliedSnapshot:
        with self._lock:
            applied = self._applied_cursor
            feed_lag = self._feed_lag
            engine_dead = self._engine_dead
            rows, cols = self._rows, self._cols
            engine_note = self._engine_error_note
            sticky_note = self._sticky_error_note
        # 没有拿到快照内容：不得声明 partial"恢复"（fidelity=unavailable）
        fidelity = Fidelity.UNAVAILABLE
        recovery = Recovery.NONE if engine_dead else Recovery.DEGRADED
        note = f"snapshot unavailable: {reason}; cursor={applied} is last confirmed applied"
        if engine_note:
            note += f"; engine_error={engine_note}"
        if sticky_note:
            note += f"; engine_sticky_error={sticky_note}"
        return AppliedSnapshot(
            serialized_screen="",
            cursor=applied,
            rows=rows,
            cols=cols,
            fidelity=fidelity,
            recovery=recovery,
            feed_lag=feed_lag,
            engine=self._engine_desc,
            note=note,
        )

    def _timeout_snapshot(self, kind: str, timeout: float, call: _ControlCall) -> AppliedSnapshot:
        with self._lock:
            applied = self._applied_cursor
            engine_dead = self._engine_dead
            feed_lag = self._feed_lag
            queue_ops = self._queue_ops
            producer = self._producer_frontier
            rows, cols = self._rows, self._cols
        note = (
            f"{kind}-timeout after {timeout}s (request skipped if still queued; "
            f"not executed late); cursor={applied} (applied, != producer frontier {producer}); "
            f"queue_ops={queue_ops}"
        )
        if kind == "reset":
            note += "; reset unconfirmed (may still execute if already sent): re-baseline state unknown"
        # 超时未取得快照：fidelity=unavailable（没有任何可用的恢复内容）
        fidelity = Fidelity.UNAVAILABLE
        recovery = Recovery.NONE if engine_dead else Recovery.DEGRADED
        return AppliedSnapshot(
            serialized_screen="",
            cursor=applied,
            rows=rows,
            cols=cols,
            fidelity=fidelity,
            recovery=recovery,
            feed_lag=feed_lag,
            engine=self._engine_desc,
            note=note,
        )

    # ------------------------------------------------------------------
    # applier loop
    # ------------------------------------------------------------------

    def _applier_loop(self) -> None:
        while True:
            with self._cond:
                while not self._ops and not self._closing:
                    self._cond.wait(timeout=0.5)
                if self._closing:
                    # close() owns the lifecycle from here: drop the backlog
                    # and leave (remaining control calls get a bounded answer).
                    dropped_calls: list[_ControlCall] = []
                    while self._ops:
                        leftover = self._ops.popleft()
                        self._release_op_locked(leftover)
                        if leftover.call is not None:
                            dropped_calls.append(leftover.call)
                    for call in dropped_calls:
                        self._finish_call(call, failure="emulator-closing")
                    return
                op = self._ops.popleft()
                skip_call: _ControlCall | None = None
                skip = False
                if self._engine_dead:
                    self._release_op_locked(op)
                    self._count_locked("ops_dropped_engine_dead")
                    skip_call = op.call
                    skip = True
                elif op.call is not None and op.call.expired:
                    # Expired control op: never executed late (checked in the
                    # same critical section that dequeues it).
                    self._release_op_locked(op)
                    self._count_locked("control_expired_skipped")
                    skip = True
                else:
                    # `sent` 与过期检查在同一临界区：调用方超时后读 `sent`
                    # 即可精确区分"仍在排队（不执行）"与"已在途（结果未知）"。
                    if op.call is not None:
                        op.call.sent = True
                    with self._ack_lock:
                        self._inflight_op_id = op.op_id
                        self._inflight_ack = None
            if skip:
                if skip_call is not None:
                    self._finish_call(skip_call, failure="engine-unavailable")
                continue
            if not self._send_op(op):
                self._abort_inflight(op)
                continue
            ack = self._wait_ack(op)
            self._complete_op(op, ack)

    def _send_op(self, op: _Op) -> bool:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        header: dict[str, Any] = {"v": PROTOCOL_VERSION, "op": op.op_id}
        payload = b""
        if op.kind == "feed":
            header["type"] = "feed"
            header["abs_start"] = str(op.abs_start)
            header["abs_end"] = str(op.abs_end)
            payload = op.data or b""
        elif op.kind == "resize":
            header["type"] = "resize"
            header["rows"] = op.rows
            header["cols"] = op.cols
        elif op.kind == "reset":
            header["type"] = "reset"
        elif op.kind == "restore":
            header["type"] = "restore"
            payload = op.data or b""
        elif op.kind == "snapshot":
            header["type"] = "snapshot"
        elif op.kind == "barrier":
            header["type"] = "barrier"
        elif op.kind == "shutdown":
            header["type"] = "shutdown"
        elif op.kind == "test_stall":
            header["type"] = "test_stall"
            header["ms"] = op.ms
        elif op.kind == "test_inject_error":
            header["type"] = "test_inject_error"
            header["target"] = op.target
        else:  # pragma: no cover - internal guard
            return False
        try:
            proc.stdin.write(encode_frame(header, payload))
            proc.stdin.flush()
            return True
        except Exception as exc:  # noqa: BLE001 - applier 线程不得因流异常死亡
            self._note_engine_failure(f"send-error:{type(exc).__name__}")
            return False

    def _wait_ack(self, op: _Op) -> tuple[dict[str, Any], bytes] | None:
        with self._ack_lock:
            while True:
                if self._inflight_ack is not None:
                    ack = self._inflight_ack
                    self._inflight_ack = None
                    return ack
                if self._engine_dead or self._closing:
                    return None
                self._ack_cond.wait(timeout=0.25)

    def _abort_inflight(self, op: _Op) -> None:
        with self._ack_lock:
            self._inflight_op_id = None
            self._inflight_ack = None
        with self._lock:
            self._release_op_locked(op)
            self._count_locked("unconfirmed_ops")
        if op.call is not None:
            self._finish_call(op.call, failure="send-failed")

    def _complete_op(self, op: _Op, ack: tuple[dict[str, Any], bytes] | None) -> None:
        with self._ack_lock:
            self._inflight_op_id = None
        with self._lock:
            self._release_op_locked(op)
        if ack is None:
            with self._lock:
                self._count_locked("unconfirmed_ops")
            if op.call is not None:
                self._finish_call(op.call, failure="unconfirmed")
            return
        header, payload = ack
        frame_type = str(header.get("type", ""))
        if frame_type == "error":
            code = str(header.get("code", "op-error"))
            detail = str(header.get("detail", ""))[:256]
            with self._lock:
                self._count_locked("engine_op_errors")
                # An engine op error is a sticky degradation: the loop keeps
                # serving, but fidelity/recovery never silently return to
                # full (the error is surfaced in the snapshot note/reasons).
                if self._sticky_error_note is None:
                    self._sticky_error_note = f"{code}:{detail}"
                self._record_reason_locked("ENGINE_OP_ERROR_STICKY")
            if op.call is not None:
                self._finish_call(op.call, failure=f"{code}:{detail}")
            return
        applied = header.get("applied_bytes")
        if applied is not None:
            with self._lock:
                self._applied_cursor = max(self._applied_cursor, int(applied))
        if op.kind == "reset" and not op.ledger_applied:
            # ack 路径统一回填账本（只应用一次）：即使调用方已超时，迟到 ack
            # 也会把 reset_count/baseline 对齐到实际执行位置并解除 cursor 禁止。
            op.ledger_applied = True
            self._apply_reset_result(header)
        if op.kind == "resize":
            with self._lock:
                self._applied_resize = [
                    int(header.get("rows", op.rows)),
                    int(header.get("cols", op.cols)),
                ]
                self._count_locked("resize_confirmed")
        if op.call is not None:
            op.call.header = header
            op.call.payload = payload
            op.call.event.set()

    # ------------------------------------------------------------------
    # diagnostics / close
    # ------------------------------------------------------------------

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            data: dict[str, Any] = {
                "engine": self._engine_desc,
                "engine_dead": self._engine_dead,
                "engine_error": self._engine_error_note,
                "engine_sticky_error": self._sticky_error_note,
                "sidecar_pid": self._sidecar_pid,
                "sidecar_filetime": (
                    str(self._sidecar_identity.created_at_filetime)
                    if self._sidecar_identity
                    else None
                ),
                "node_binary": self._node_binary,
                "producer_frontier": str(self._producer_frontier),
                "expected_next": str(self._expected_next),
                "applied_cursor": str(self._applied_cursor),
                "baseline_cursor": str(self._baseline_cursor),
                "reset_count": self._reset_count,
                "reset_unconfirmed": self._reset_unconfirmed,
                "cursors_valid": self._cursors_valid_locked(),
                "applied_resize": (
                    list(self._applied_resize) if self._applied_resize is not None else None
                ),
                "feed_lag": self._feed_lag,
                "queue_bytes": self._queue_bytes,
                "queue_ops": self._queue_ops,
                "max_queue_bytes": self._max_queue_bytes,
                "max_queue_ops": self._max_queue_ops,
                "reasons": list(self._reasons)[: self._max_diagnostics],
                "reason_overflow": self._reason_overflow,
                "gap_ranges": [list(r) for r in self._gap_ranges][: self._max_diagnostics],
                "duplicate_ranges": [list(r) for r in self._duplicate_ranges][
                    : self._max_diagnostics
                ],
                "rejected_ranges": [list(r) for r in self._rejected_ranges][
                    : self._max_diagnostics
                ],
                "overflow": dict(self._overflow),
                "counters": dict(self._counters),
                "closing": self._closing,
                "closed": self._closed,
            }
        return data

    # -- test hooks (deterministic fault injection; not used in production) --
    def _test_stall(self, ms: int) -> bool:
        with self._lock:
            if self._closing or self._closed or self._engine_dead:
                return False
            if not self._can_enqueue_locked(0):
                return False
            op = _Op(op_id=self._next_op_id, kind="test_stall", ms=max(0, min(int(ms), 10_000)))
            self._next_op_id += 1
            self._ops.append(op)
            self._queue_ops += 1
            self._cond.notify_all()
        return True

    def _test_inject_feed_error(self, target: str = "next-feed") -> bool:
        with self._lock:
            if self._closing or self._closed or self._engine_dead:
                return False
            if not self._can_enqueue_locked(0):
                return False
            op = _Op(op_id=self._next_op_id, kind="test_inject_error", target=str(target))
            self._next_op_id += 1
            self._ops.append(op)
            self._queue_ops += 1
            self._cond.notify_all()
        return True

    def close(self, *, timeout: float | None = None) -> EmulatorCloseReport:
        """有界、幂等、可重试的关闭；并发调用由 ``_close_lock`` 串行化。

        ``timeout`` 是**本次调用**的总预算：从入口开始计时，**包含**等待
        ``_close_lock`` 的时间。抢锁超时 → 合法 ``closed=false`` + 锁忙诊断
        （可重试；不触碰另一调用的状态/缓存、保 owner）。拿到锁后只使用剩余
        预算，**不重置 deadline、不用最小下限多次延长**。已收敛的缓存报告可
        返回，但 ``seconds`` 恒为本次调用的实际耗时（不冒充历史调用）。

        顺序：优雅 shutdown（有界切片）→ **统一整 Job 清理与核验**（先清树：
        root 终止 + ``terminate_tree`` + 成员/active 核验 → 再 join
        reader/stderr/applier 与在途操作 → 最后关闭流 → ``guard.close``）。
        任一步失败：``closed=False`` 且资源保留（同一 owner 重试收敛）。
        """
        start = time.monotonic()
        budget = self._shutdown_timeout if timeout is None else max(0.0, float(timeout))
        acquired = self._close_lock.acquire(timeout=budget)
        if not acquired:
            return self._close_lock_busy_report(start, budget)
        try:
            return self._close_serialized(start, budget)
        finally:
            self._close_lock.release()

    def _close_lock_busy_report(self, start: float, budget: float) -> EmulatorCloseReport:
        """抢锁失败的合法报告：不触碰资源/状态、不改另一调用缓存、可重试。"""
        return EmulatorCloseReport(
            closed=False,
            graceful=False,
            forced=False,
            process_exited=False,
            guard_closed=False,
            reader_joined=False,
            stderr_joined=False,
            applier_joined=False,
            streams_closed=False,
            job_verified=False,
            detail=(
                f"close-lock-busy: another close() call holds _close_lock; this "
                f"call's budget ({budget}s) expired while waiting; retryable"
            ),
            seconds=round(time.monotonic() - start, 3),
        )

    def _close_serialized(self, start: float, budget: float) -> EmulatorCloseReport:
        with self._lock:
            cached = self._close_report
            if cached is not None and cached.closed:
                # 已收敛缓存可返回；seconds 必须是本次调用实际耗时（不冒充 T1）。
                return replace(cached, seconds=round(time.monotonic() - start, 3))
        deadline = start + budget
        detail: list[str] = []
        graceful = False
        proc = self._proc

        # 1) graceful shutdown：仅当进程仍在、引擎未死、未进入关闭；预算切片。
        if (
            proc is not None
            and proc.poll() is None
            and not self._engine_dead
            and not self._closing
        ):
            remaining = max(0.0, deadline - time.monotonic())
            slice_ = min(remaining, max(0.5, budget * 0.5))
            if slice_ > 0.0:
                call = self._enqueue_control("shutdown", slice_)
                if call is not None:
                    if call.event.wait(slice_) and call.header is not None:
                        graceful = True
                    else:
                        detail.append("shutdown-unacknowledged")
            else:
                detail.append("graceful-skipped-no-budget")

        # 2) 停止 applier：closing 丢弃队列并唤醒在途 ack 等待者。
        with self._lock:
            self._closing = True
            self._cond.notify_all()
        with self._ack_lock:
            self._ack_cond.notify_all()

        # 3) 统一整 Job 清理与核验（仅用剩余预算，不设最小下限；失败保留可重试）。
        outcome = _reap_process_resources(
            proc=proc,
            guard=self._guard,
            pid=self._sidecar_pid,
            identity=self._sidecar_identity,
            reader=self._reader_thread,
            stderr=self._stderr_thread,
            applier=self._applier_thread,
            budget=max(0.0, deadline - time.monotonic()),
            kill_timeout=self._kill_timeout,
            detail=detail,
        )
        forced = bool(outcome.get("root_killed")) or bool(outcome.get("job_owned"))
        report = EmulatorCloseReport(
            closed=bool(outcome["closed"]),
            graceful=graceful,
            forced=forced,
            process_exited=bool(outcome["root_dead"]),
            guard_closed=bool(outcome["guard_closed"]),
            reader_joined=bool(outcome["reader_joined"]),
            stderr_joined=bool(outcome["stderr_joined"]),
            applier_joined=bool(outcome["applier_joined"]),
            streams_closed=bool(outcome["streams_closed"]),
            job_verified=bool(outcome["job_verified"]),
            detail=", ".join(detail) if detail else "ok",
            seconds=time.monotonic() - start,
        )
        with self._lock:
            self._closed = report.closed
            self._close_report = report
        return report

    # ------------------------------------------------------------------
    # lifecycle helpers for callers
    # ------------------------------------------------------------------

    @property
    def engine_alive(self) -> bool:
        proc = self._proc
        return bool(proc is not None and proc.poll() is None and not self._engine_dead)

    @property
    def applied_cursor(self) -> int:
        with self._lock:
            return self._applied_cursor

    @property
    def producer_frontier(self) -> int:
        with self._lock:
            return self._producer_frontier

    @property
    def sidecar_pid(self) -> int | None:
        return self._sidecar_pid

    def _cursors_valid_locked(self) -> bool:
        return (
            not self._gap_ranges
            and not self._duplicate_ranges
            and not self._engine_dead
            and not self._closing
            and not self._reset_unconfirmed
        )

    @property
    def cursors_valid(self) -> bool:
        """绝对 cursor 是否可信（与 ``diagnostics()["cursors_valid"]`` 同一判定）。

        不满足任一条件即为 False，cursor 不得作为续流起点：

        - 粘滞 gap / duplicate 供料；
        - 引擎不可用 / 正在关闭；
        - **reset 已发送但未确认**（结果未知，禁止已知失同步继续续流；迟到
          ack 或引擎不可用后判定收敛）。
        """
        with self._lock:
            return self._cursors_valid_locked()

    def __enter__(self) -> "HeadlessEmulator":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
