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

进程所有权（runner 侧职责）：

- sidecar 以 ``CREATE_SUSPENDED`` 创建 → assign 进本 emulator 自持的
  ``JobObjectGuard``（KILL_ON_JOB_CLOSE）→ ``NtResumeProcess``；runner 进程
  硬死时内核关闭 Job 句柄并清理 sidecar，**不依赖 runner 的清理代码**。
- ``close()`` 幂等、可重试：优雅 shutdown（有界）→ 身份核验终止
  （``identity.kill_verified``，同 handle）→ Job 级终止；任一步失败则
  ``closed=False`` 且资源保留（报告可重试），绝不假称已关闭。
- sidecar stdin/stdout 为有界二进制帧（u32 长度前缀）；帧超限、断帧、EOF、
  异常、卡住均有明确可见状态；64 位偏移一律十进制字符串（无 JS 浮点）。
- token/秘密不进入 sidecar：协议里没有任何认证字段。

已知边界（不夸大）：

- 本模块是 **headless↔headless** 引擎宿主；真实浏览器渲染保真属 P3 验收。
- 引擎序列化能力缺口（滚动区、synchronized output、光标可见性等）由 sidecar
  检测器降级为 partial；详见
  ``docs/design/PAN_TERMINAL_EMULATOR_INTERFACES_20261003.md``。

BASELINE REVISION (pre-fix)：本修订有意保留探索管线的两个前端化缺口，用于
"先失败后通过"证据：(1) ``reset_baseline()`` 把新基线接到 **producer frontier**
（含已入队但尚未执行的后续 feed），而不是命令实际执行位置；(2) reset 后旧的
序列 gap/duplicate 标记继续污染 cursor。修复版在提交历史中可见。
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

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
    "EmulatorUnavailableError",
    "HeadlessEmulator",
    "PROTOCOL_VERSION",
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


@dataclass(frozen=True)
class EmulatorCloseReport:
    """``HeadlessEmulator.close()`` 的结果（失败保留资源、可重试）。"""

    closed: bool
    graceful: bool
    forced: bool
    process_exited: bool
    guard_closed: bool
    reader_joined: bool
    applier_joined: bool
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
            "applier_joined": self.applier_joined,
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
    return Path(__file__).resolve().parents[3] / "emulator_sidecar" / "sidecar.mjs"


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


class HeadlessEmulator:
    """runner 生命周期内常驻的权威仿真器（Node headless-xterm sidecar 宿主）。

    直接构造即启动 sidecar；构造失败抛
    :class:`EmulatorUnavailableError`（依赖缺失/握手失败，资源尽力清理并在
    异常文本中如实报告残留）。生产装配由 runner 负责；本类不触碰 PTY。
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
        # BASELINE (pre-fix): rejection diagnostics grow without bound.
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
        }

        self._reset_count = 0
        self._baseline_cursor = int(start_cursor)
        self._engine_error_note: str | None = None
        self._sticky_error_note: str | None = None
        self._engine_dead = False
        self._closing = False
        self._closed = False
        self._close_report: EmulatorCloseReport | None = None

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
            raise EmulatorUnavailableError(
                "HeadlessEmulator 生产宿主仅支持 Windows（Job Object 所有权）；"
                f"sys.platform={sys.platform!r}"
            )

        node = _resolve_node_binary(node_binary)
        if not node:
            raise EmulatorUnavailableError(
                "未找到 Node 运行时：sidecar 不可用。请安装 Node（>=18）或通过 "
                "node_binary / PAN_TERMINAL_NODE_BIN 指定；终端基本策略由 runner 决定。"
            )
        sidecar = Path(sidecar_path) if sidecar_path is not None else _default_sidecar_path()
        if not sidecar.is_file():
            raise EmulatorUnavailableError(f"sidecar 脚本不存在：{sidecar}")

        self._node_binary = node
        self._sidecar_path = sidecar
        self._spawn_and_handshake()

    # ------------------------------------------------------------------
    # spawn / handshake
    # ------------------------------------------------------------------

    def _spawn_and_handshake(self) -> None:
        from . import guard as guard_module
        from . import identity as identity_module

        self._guard = guard_module.JobObjectGuard()
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
            self._guard.close()
            raise EmulatorUnavailableError(f"sidecar 进程创建失败：{exc}") from exc

        self._sidecar_pid = int(self._proc.pid)
        try:
            self._guard.assign(int(self._proc._handle))  # type: ignore[attr-defined]
            ft = identity_module.read_creation_filetime(int(self._proc._handle))  # type: ignore[attr-defined]
            if ft is None:
                raise OSError("无法读取 sidecar 创建时间（fail-closed）")
            self._sidecar_identity = ProcessIdentity(self._sidecar_pid, created_at_filetime=int(ft))
            _resume_process(int(self._proc._handle))  # type: ignore[attr-defined]
        except Exception as exc:  # fail-closed: never leave an unguarded sidecar
            detail = self._cleanup_failed_startup()
            raise EmulatorUnavailableError(
                f"sidecar 门禁失败（{type(exc).__name__}: {exc}）；清理结果：{detail}"
            ) from exc

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
            detail = self._cleanup_failed_startup()
            raise EmulatorUnavailableError(
                f"sidecar hello 写入失败（{type(exc).__name__}）；清理结果：{detail}"
            ) from exc

        if not self._ready_event.wait(self._startup_timeout):
            detail = self._cleanup_failed_startup()
            raise EmulatorUnavailableError(
                f"sidecar 启动握手超时（{self._startup_timeout}s）；stderr={self._stderr_digest()!r}；"
                f"清理结果：{detail}"
            )
        if self._startup_failure is not None:
            detail = self._cleanup_failed_startup()
            raise EmulatorUnavailableError(
                f"sidecar 不可用：{self._startup_failure}；清理结果：{detail}"
            )

        self._applier_thread = threading.Thread(
            target=self._applier_loop, name="emulator-applier", daemon=True
        )
        self._applier_thread.start()

    def _cleanup_failed_startup(self) -> str:
        """启动失败清理：尽力终止 + 关 Job + 关管道；返回如实结果文本。"""
        parts: list[str] = []
        proc = self._proc
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            proc.wait(timeout=2.0)
                        except subprocess.TimeoutExpired:
                            parts.append("process-still-alive")
                else:
                    parts.append("process-already-exited")
            except OSError as exc:
                parts.append(f"terminate-error:{type(exc).__name__}")
            for stream_name in ("stdin", "stdout", "stderr"):
                stream = getattr(proc, stream_name, None)
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        parts.append(f"{stream_name}-close-error")
        if self._guard is not None:
            try:
                closed = bool(self._guard.close())
                parts.append("guard-closed" if closed else "guard-close-failed")
            except Exception as exc:  # pragma: no cover - defensive
                parts.append(f"guard-error:{type(exc).__name__}")
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1.0)
        return ", ".join(parts) if parts else "nothing-to-clean"

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
        self, kind: str, timeout: float, *, payload: bytes = b""
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
            op = _Op(op_id=self._next_op_id, kind=kind, call=call, data=payload or None)
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
        """入队一次 resize（与 feed 同一有序通道；占同一 op 预算）。"""
        with self._lock:
            if self._closing or self._closed or self._engine_dead:
                self._count_locked("ops_dropped_engine_dead")
                return
            if not self._can_enqueue_locked(0):
                self._record_overflow_locked("control_queue")
                self._count_locked("control_queue_overflow")
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
            return self._timeout_snapshot(kind, timeout, call)
        if call.failure:
            return self._degraded_snapshot_now(f"engine-unavailable:{call.failure}")
        assert call.header is not None
        if kind == "reset":
            self._apply_reset_result(call.header)
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

        degraded = feed_lag or sticky_note is not None
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
        if op.call is not None:
            op.call.sent = True
        try:
            proc.stdin.write(encode_frame(header, payload))
            proc.stdin.flush()
            return True
        except (OSError, ValueError, EmulatorProtocolError) as exc:
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
        start = time.monotonic()
        with self._lock:
            if self._close_report is not None and self._close_report.closed:
                return self._close_report
        budget = self._shutdown_timeout if timeout is None else max(0.1, float(timeout))
        deadline = start + budget
        detail: list[str] = []
        graceful = False
        forced = False

        # 1) graceful: enqueue shutdown and let the applier deliver it in order.
        proc = self._proc
        if proc is None:
            self._closing = True
        elif proc.poll() is None and not self._engine_dead and not self._closing:
            call = self._enqueue_control("shutdown", max(0.1, deadline - time.monotonic()))
            if call is not None:
                remaining = max(0.0, deadline - time.monotonic())
                if call.event.wait(remaining) and call.header is not None:
                    graceful = True
                else:
                    detail.append("shutdown-unacknowledged")

        # 2) bounded wait for process exit
        process_exited = self._wait_process_exit(deadline - time.monotonic())

        # 3) forced path when the graceful path did not converge
        if not process_exited and proc is not None:
            forced = True
            detail.append(self._force_terminate())
            process_exited = self._wait_process_exit(max(0.5, deadline - time.monotonic()))
            if not process_exited:
                detail.append("process-still-alive-after-force")

        # 4) stop the applier (always; bounded)
        with self._lock:
            self._closing = True
            self._cond.notify_all()
        with self._ack_lock:
            self._ack_cond.notify_all()
        applier_joined = True
        if self._applier_thread is not None:
            self._applier_thread.join(timeout=2.0)
            applier_joined = not self._applier_thread.is_alive()

        # 5) only reap resources once the process is confirmed exited: a failed
        #    close retains the guard / pipes / reader so a retry can converge
        #    (closing the pipes or the job handle earlier would kill the
        #    sidecar through EOF / KILL_ON_JOB_CLOSE and fake convergence).
        reader_joined = False
        guard_closed = False
        if process_exited:
            if proc is not None:
                for stream_name in ("stdin", "stdout", "stderr"):
                    stream = getattr(proc, stream_name, None)
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            detail.append(f"{stream_name}-close-error")
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=2.0)
                reader_joined = not self._reader_thread.is_alive()
            else:
                reader_joined = True
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=1.0)
            if self._guard is not None:
                try:
                    guard_closed = bool(self._guard.close())
                except Exception as exc:  # pragma: no cover - defensive
                    guard_closed = False
                    detail.append(f"guard-close-error:{type(exc).__name__}")
                if not guard_closed:
                    detail.append("guard-close-failed-retryable")
            else:
                guard_closed = True
        else:
            detail.append("resources-retained-for-retry")

        closed = bool(process_exited and guard_closed and reader_joined and applier_joined)
        report = EmulatorCloseReport(
            closed=closed,
            graceful=graceful,
            forced=forced,
            process_exited=process_exited,
            guard_closed=guard_closed,
            reader_joined=reader_joined,
            applier_joined=applier_joined,
            detail=", ".join(detail) if detail else "ok",
            seconds=time.monotonic() - start,
        )
        with self._lock:
            self._closed = closed
            self._close_report = report
        return report

    def _wait_process_exit(self, timeout: float) -> bool:
        proc = self._proc
        if proc is None:
            return True
        if proc.poll() is not None:
            return True
        try:
            proc.wait(timeout=max(0.0, timeout))
            return True
        except subprocess.TimeoutExpired:
            return False

    def _force_terminate(self) -> str:
        """身份核验终止 → Job 终止（fail-closed 记录，不裸杀）。"""
        from . import identity as identity_module

        notes: list[str] = []
        pid = self._sidecar_pid
        if pid is not None and self._sidecar_identity is not None:
            result = identity_module.kill_verified(
                pid,
                self._sidecar_identity,
                wait_timeout=min(self._kill_timeout, 3.0),
            )
            notes.append(f"kill-verified:{result.reason}")
        if self._guard is not None and getattr(self._guard, "handle_open", False):
            try:
                owned, remaining = self._guard.terminate_tree(pid, timeout=2.0)
                notes.append(f"job-terminate:remaining={remaining}")
            except Exception as exc:
                notes.append(f"job-terminate-error:{type(exc).__name__}")
        return ";".join(notes) if notes else "no-terminate-path"

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

    @property
    def cursors_valid(self) -> bool:
        """绝对 cursor 是否可信：无粘滞 gap/duplicate 且引擎可用/未关闭。

        reset_baseline 之前发生的 gap/duplicate 属于旧基线，应在 reset 确认后
        被清除（新基线按命令实际执行位置定义）；否则 cursor 不可作为续流起点。
        """
        with self._lock:
            return (
                not self._gap_ranges
                and not self._duplicate_ranges
                and not self._engine_dead
                and not self._closing
            )

    def __enter__(self) -> "HeadlessEmulator":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
