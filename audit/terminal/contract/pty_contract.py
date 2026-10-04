"""Pan Terminal / PTY 公共契约原型（探索用）。

范围与边界
----------
- 只定义**最小接口与状态机**，并提供可运行的参考实现与确定性测试后端。
- 不修改 ``packages/`` 下任何正式模块，不引入正式依赖锁，不注册到 Pan 服务。
- 平台后端延迟导入：没有 pywinpty 时仍可运行 mock 测试路径。

契约要点（对应 docs/design/PAN_TERMINAL_PTY_CONTRACT_20261003.md）：

1. 原始 VT 输出必须持续读到通道 EOF；``alive()`` 为假不是停止读的条件。
2. 输出日志有界、带绝对字节序号，落后于保留窗口时明确返回 gap。
3. 根进程退出、通道 EOF、终端完成是三个不同事实，分开公布。
4. 清理失败必须保留 owner 与状态，不能静默标记为已退出。
5. 输入控制权用 generation lease 表达，迟到消息被拒绝。
6. 屏幕快照必须来自终端仿真器状态，不能是尾部文本。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Protocol, Sequence, runtime_checkable

TERMINAL_ID_PREFIX = "term_"


# --------------------------------------------------------------------------
# 1. 后端原语
# --------------------------------------------------------------------------


@runtime_checkable
class PtyBackend(Protocol):
    """最小 PTY 后端：只暴露契约需要的原语。

    约定：
    - ``read`` 阻塞直到有数据、或通道结束；通道结束时抛 ``EOFError``。
    - ``read`` 返回值是**原始字节**，不做任何终端语义处理。
    - ``exit_code`` 在进程尚未退出时返回 ``None``。
    - ``terminate`` 只负责请求终止，不负责确认整树退出。
    """

    pid: int | None

    def read(self, size: int) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def alive(self) -> bool: ...

    def exit_code(self) -> int | None: ...

    def terminate(self, force: bool) -> None: ...

    def close(self) -> None: ...


class BackendUnavailableError(RuntimeError):
    """平台后端不可用（缺依赖/不支持）。创建终端时解释，不阻断整个 Pan。"""


class WinptyBackend:
    """pywinpty / ConPTY 适配（Windows）。

    实测（pywinpty 3.0.5）：进程退出后仍可能有尾部输出可读，最终由 ``read``
    抛 ``EOFError`` 表达通道 EOF；``eof`` 是**方法**不是属性。
    """

    def __init__(
        self,
        argv: Sequence[str],
        cwd: str | os.PathLike[str],
        *,
        rows: int = 24,
        cols: int = 80,
        env: dict[str, str] | None = None,
    ) -> None:
        try:
            from winpty import PtyProcess  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 平台相关
            raise BackendUnavailableError(
                "Windows PTY 后端需要 pywinpty；请在独立临时环境安装，例如 "
                "`uv run --no-project --with pywinpty==3.0.5 ...`"
            ) from exc
        self._proc = PtyProcess.spawn(
            list(argv),
            cwd=str(cwd),
            dimensions=(rows, cols),
            env={**(env or os.environ), "TERM": "xterm-256color"},
        )

    @property
    def pid(self) -> int | None:
        return getattr(self._proc, "pid", None)

    def read(self, size: int) -> bytes:
        data = self._proc.read(size)
        if data is None:
            raise EOFError
        return data.encode("utf-8", "surrogatepass")

    def write(self, data: bytes) -> int:
        return int(self._proc.write(data.decode("utf-8", "surrogatepass")))

    def resize(self, rows: int, cols: int) -> None:
        self._proc.setwinsize(rows, cols)

    def win_size(self) -> tuple[int, int]:
        return tuple(int(v) for v in self._proc.getwinsize())  # type: ignore[return-value]

    def alive(self) -> bool:
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    def exit_code(self) -> int | None:
        try:
            value = self._proc.exitstatus
        except Exception:
            return None
        return None if value is None else int(value)

    def terminate(self, force: bool) -> None:
        self._proc.terminate(force=force)

    def close(self) -> None:
        try:
            self._proc.close()
        except Exception:
            pass


class ScriptedBackend:
    """确定性测试后端。

    存在意义：模拟“``alive()`` 已经为假，但通道里仍有尾部数据”的顺序，
    以及终止失败、通道错误等分支。它**只用于契约逻辑测试**，不能作为
    真实 CLI / PTY 行为的证据。
    """

    def __init__(
        self,
        chunks: Sequence[bytes],
        *,
        alive_returns: bool = False,
        exit_code: int | None = 0,
        fail_terminate: bool = False,
        channel_error: str | None = None,
        write_error: str | None = None,
        resize_error: str | None = None,
        pid: int | None = 4242,
        empty_reads: bool = False,
        block_forever: bool = False,
        cancel_on_close: bool = True,
        abort_on_release: bool = False,
    ) -> None:
        self._chunks = list(chunks)
        # alive() 的返回值与是否还有缓冲数据**无关**：置 False 即可复现
        # “进程已退出、但通道里仍有尾部输出”的真实顺序（见探针 R1）。
        self._alive_returns = alive_returns
        self._exit_code = exit_code
        self._fail_terminate = fail_terminate
        self._channel_error = channel_error
        self._write_error = write_error
        self._resize_error = resize_error
        self.pid = pid
        # empty_reads: read() 永远返回 b""（模拟“没有数据但也不报 EOF”）
        self._empty_reads = empty_reads
        # block_forever: read() 阻塞，直到 close() 取消（模拟 pywinpty 无读超时）
        self._block_forever = block_forever
        self._cancel_on_close = cancel_on_close
        # abort_on_release: 取消后不是抛 EOFError 而是传输层异常
        # （复现真实 pywinpty 的 ConnectionAbortedError / WinError 10053）
        self._abort_on_release = abort_on_release
        self._release = threading.Event()
        self.writes: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.terminate_calls: list[bool] = []
        self.closed = False
        self.read_calls = 0
        self._lock = threading.Lock()

    def read(self, size: int) -> bytes:
        with self._lock:
            self.read_calls += 1
            if self._chunks:
                return self._chunks.pop(0)
            if self._channel_error is not None:
                raise RuntimeError(self._channel_error)
            if self._empty_reads:
                return b""
            if not self._block_forever:
                raise EOFError
        # 模拟 pywinpty：阻塞读只有 close() 取消或测试 release 才会返回
        self._release.wait(30.0)
        if self._abort_on_release:
            raise ConnectionAbortedError("[WinError 10053] simulated abort")
        raise EOFError

    def write(self, data: bytes) -> int:
        if self._write_error is not None:
            raise RuntimeError(self._write_error)
        self.writes.append(data)
        return len(data)

    def resize(self, rows: int, cols: int) -> None:
        if self._resize_error is not None:
            raise RuntimeError(self._resize_error)
        self.resizes.append((rows, cols))

    def alive(self) -> bool:
        return self._alive_returns

    def exit_code(self) -> int | None:
        return self._exit_code if not self._alive_returns else None

    def terminate(self, force: bool) -> None:
        self.terminate_calls.append(force)
        if self._fail_terminate:
            raise RuntimeError("terminate failed (injected)")

    def close(self) -> None:
        self.closed = True
        if self._cancel_on_close:
            # 取消阻塞读（模拟"关闭 pty 句柄让 read 抛 EOFError"）
            self._release.set()

    def release_blocked_read(self) -> None:
        """测试收尾用：放行还在阻塞的 read，避免 daemon 线程一直挂着。"""
        self._release.set()

    def seed(self, *chunks: bytes) -> None:
        with self._lock:
            self._chunks.extend(chunks)


# --------------------------------------------------------------------------
# 2. 有界输出日志：序号 / gap
# --------------------------------------------------------------------------


class InvalidCursorError(ValueError):
    """请求的游标超过已产生的字节数。"""


@dataclass(frozen=True)
class OutputChunk:
    seq: int
    data: bytes


@dataclass(frozen=True)
class OutputPage:
    """一次带游标的读取结果。

    - ``chunks`` 的首字节序号是 ``first_seq``。
    - ``gap`` 非空表示请求游标到保留窗口之间的字节**已被丢弃**，
      客户端必须从屏幕快照恢复，不能把这一段当作空。
    """

    chunks: tuple[OutputChunk, ...]
    first_seq: int
    next_cursor: int
    gap: tuple[int, int] | None
    truncated: bool


class OutputLog:
    """按字节上界保留的追加日志，序号是绝对字节偏移。

    设计取舍：
    - 序号连续：序号 = 该字节在流中的绝对偏移，与保留窗口无关，
      所以客户端可以安全地跨重连复用游标。
    - 驱逐以**整块**为单位：只保证"不因为驱逐而在保留窗口**起点**额外制造
      序列残片"，**不保证** VT 完整性，也不保证序列不被切割。

    **边界是字节流，不是序列流**（纠正过强表述）：以下三种情况都会让
    UTF-8 码点、CSI、OSC 跨块切割，契约无法也不应在日志层消除：

    1. ``PtyBackend.read(size)`` 的读取边界由后端/内核决定（ConPTY 会把一次
       写入拆成多个读事件），所以**块边界本身就可能在序列中间**；
    2. 单块超过上界时只保留尾部，切割点由"尾部对齐"决定；
    3. 保留窗口起点（``first_retained_seq``）可能落在一条转义序列内部，
       此时从窗口起点解析必然得到错误结果。

    因此契约规定：**序列重组与屏幕状态由仿真器/快照负责**；当客户端发现
    ``gap`` 或窗口起点可疑时，必须用快照恢复，而不是从保留窗口起点开始解析。
    """

    def __init__(self, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = int(max_bytes)
        self._chunks: list[OutputChunk] = []
        self._retained = 0
        self._total = 0
        self._dropped = 0
        self._lock = threading.Lock()

    # -- 写入 ---------------------------------------------------------
    def append(self, data: bytes) -> int:
        """追加一段字节。``data`` 可任意落在 UTF-8/CSI/OSC 中间（见类文档）。"""
        if not data:
            return self._total
        with self._lock:
            seq = self._total
            self._total += len(data)
            if len(data) > self.max_bytes:
                keep = data[-self.max_bytes :]
                self._dropped += len(data) - len(keep)
                self._chunks.append(OutputChunk(seq=seq + len(data) - len(keep), data=keep))
            else:
                self._chunks.append(OutputChunk(seq=seq, data=data))
            self._retained += len(self._chunks[-1].data)
            self._evict_locked()
            return seq

    def _evict_locked(self) -> None:
        while self._retained > self.max_bytes and len(self._chunks) > 1:
            victim = self._chunks.pop(0)
            self._retained -= len(victim.data)
            self._dropped += len(victim.data)

    # -- 读取 ---------------------------------------------------------
    def read_from(self, cursor: int, *, max_bytes: int | None = None) -> OutputPage:
        with self._lock:
            return self._read_locked(cursor, max_bytes)

    def _read_locked(self, cursor: int, max_bytes: int | None) -> OutputPage:
        if cursor < 0:
            raise InvalidCursorError(f"cursor must be >= 0, got {cursor}")
        if cursor > self._total:
            raise InvalidCursorError(f"cursor {cursor} > produced {self._total}")
        first_seq = self._chunks[0].seq if self._chunks else self._total
        gap: tuple[int, int] | None = (cursor, first_seq) if cursor < first_seq else None
        budget = self.max_bytes if max_bytes is None else max(0, int(max_bytes))
        taken: list[OutputChunk] = []
        truncated = False
        for chunk in self._chunks:
            data = chunk.data[max(0, cursor - chunk.seq) :]
            if not data:
                continue
            if budget <= 0:
                truncated = True
                break
            if len(data) > budget:
                data = data[:budget]
                truncated = True
            taken.append(OutputChunk(seq=max(cursor, chunk.seq), data=data))
            budget -= len(data)
            if truncated:
                break
        next_cursor = (
            taken[-1].seq + len(taken[-1].data) if taken else (cursor if budget <= 0 else self._total)
        )
        return OutputPage(
            chunks=tuple(taken),
            first_seq=taken[0].seq if taken else max(cursor, first_seq),
            next_cursor=next_cursor,
            gap=gap,
            truncated=truncated,
        )

    # -- 统计 ---------------------------------------------------------
    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total

    @property
    def retained_bytes(self) -> int:
        with self._lock:
            return self._retained

    @property
    def dropped_bytes(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def first_retained_seq(self) -> int:
        with self._lock:
            return self._chunks[0].seq if self._chunks else self._total


# --------------------------------------------------------------------------
# 3. 运行时：所有权 + 持续 drain + 退出/清理契约
# --------------------------------------------------------------------------


class RuntimeState(str, Enum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    EXITING = "exiting"
    EXITED = "exited"
    CLEANUP_FAILED = "cleanup-failed"
    LOST = "lost"


_LEGAL_TRANSITIONS: dict[RuntimeState, frozenset[RuntimeState]] = {
    # CREATED -> EXITING: 允许“创建后未启动就直接 close”，不需要调用方特判。
    RuntimeState.CREATED: frozenset({RuntimeState.STARTING, RuntimeState.EXITING}),
    RuntimeState.STARTING: frozenset({RuntimeState.RUNNING, RuntimeState.EXITING, RuntimeState.LOST}),
    RuntimeState.RUNNING: frozenset({RuntimeState.EXITING}),
    RuntimeState.EXITING: frozenset(
        {RuntimeState.EXITED, RuntimeState.CLEANUP_FAILED, RuntimeState.LOST}
    ),
    RuntimeState.EXITED: frozenset(),
    # CLEANUP_FAILED -> EXITING: 保留 owner 后允许重试收敛。
    RuntimeState.CLEANUP_FAILED: frozenset({RuntimeState.EXITING, RuntimeState.EXITED}),
    RuntimeState.LOST: frozenset(),
}


class IllegalStateTransition(RuntimeError):
    pass


@dataclass
class ExitInfo:
    """退出事实。这些是**不同事实**，必须分开判断，不能互相充当证据。

    - ``reader_done``：reader 线程结束了（**不代表**读到 EOF，也可能是错误/取消/超时）；
    - ``channel_eof``：``read()`` 报告**对端**真实通道 EOF（我们自己取消导致的断开不算）；
    - ``output_complete``：只有在真实 EOF 结账时才为真（错误/超时/stop/取消一律不为真）。

    实测补充：pywinpty 上"关闭句柄取消阻塞读"表现为 ``ConnectionAbortedError``
    （WinError 10053），即取消是**传输层异常**而不是 EOF；因此取消有独立的
    ``drain_stop_reason="cancelled"``，既不算通道错误，也不算输出完整。
    """

    code: int | None = None
    process_exit_seen: bool = False
    reader_done: bool = False
    channel_eof: bool = False
    output_complete: bool = False
    drain_stop_reason: str = "unknown"  # eof | cancelled | channel-error | eof-timeout | stop-requested
    reason: str = "unknown"
    observed_at: float = 0.0
    channel_error: str | None = None
    bytes_at_stop: int = 0

    @property
    def termination_complete(self) -> bool:
        """终端是否"干净地结束"：只有对端真实 EOF 才算。"""
        return self.output_complete


@dataclass
class CleanupReport:
    terminal_id: str
    requested_reason: str
    interrupt_sent: bool = False
    interrupt_error: str | None = None
    terminate_result: str = "not-attempted"  # returned | error | timed-out
    terminate_error: str | None = None
    tree_owned_pids: tuple[int, ...] = ()
    tree_remaining_pids: tuple[int, ...] = ()
    backend_closed: bool = False
    reader_joined: bool = False
    reader_converged: bool = False
    reader_cancelled: bool = False
    cancel_kind: str = "none"
    drain_stop_reason: str = "unknown"
    identity_check: str = "not-required"  # not-required | verified | mismatch | probe-failed
    state_after: RuntimeState = RuntimeState.EXITED
    owner_retained: bool = False
    error: str | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return (
            self.terminate_result == "returned"
            and not self.tree_remaining_pids
            and self.state_after is RuntimeState.EXITED
            and self.error is None
        )


class TreeTerminator(Protocol):
    """整树终止策略。Windows 首选 Job Object；占位实现见 ``PsutilTreeTerminator``。

    契约要求：**先** ``owned_pids`` 快照所有权，**再**终止，**最后**用
    ``remaining`` 核对。反过来（先杀再查）在根 PID 消失后必然返回空集，
    这正是 PR `_kill_tree` 的顺序缺陷。
    """

    def owned_pids(self, root_pid: int | None) -> list[int]: ...

    def remaining(self, pids: Iterable[int]) -> list[int]: ...

    def terminate_tree(self, root_pid: int | None, *, timeout: float) -> tuple[list[int], list[int]]: ...


class PsutilTreeTerminator:
    """psutil 兜底策略（探针用）。

    已知局限：根 PID 消失后无法再枚举其后代，返回值不是“整树已退出”的证明。
    Windows 正式实现应在 spawn 前建立 Job Object 所有权；本类只用于探针与
    “清理失败保留 owner”路径的验证。
    """

    def __init__(self) -> None:
        try:
            import psutil  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise BackendUnavailableError("psutil 兜底清理需要 psutil") from exc

    @staticmethod
    def _psutil() -> Any:
        import psutil

        return psutil
    def describe(self) -> dict[str, Any]:
        return {
            "kind": "psutil-tree",
            "os_level_guard": False,
            "note": "兜底策略：根 PID 消失后无法枚举后代，不是整树已退出的证明",
        }

    def owned_pids(self, root_pid: int | None) -> list[int]:
        if not root_pid:
            return []
        psutil = self._psutil()
        try:
            root = psutil.Process(root_pid)
            return [root_pid] + [c.pid for c in root.children(recursive=True)]
        except Exception:
            return []

    def remaining(self, pids: Iterable[int]) -> list[int]:
        psutil = self._psutil()
        out: list[int] = []
        for pid in pids:
            try:
                if psutil.pid_exists(int(pid)):
                    out.append(int(pid))
            except Exception:
                continue
        return out

    def terminate_tree(self, root_pid: int | None, *, timeout: float) -> tuple[list[int], list[int]]:
        if not root_pid:
            return [], []
        psutil = self._psutil()
        owned = self.owned_pids(root_pid)
        alive: list[Any] = []
        for pid in owned:
            try:
                proc = psutil.Process(pid)
                proc.kill()
                alive.append(proc)
            except Exception:
                continue
        remaining: list[int] = []
        deadline = time.monotonic() + timeout
        for proc in alive:
            wait = max(0.01, deadline - time.monotonic())
            try:
                psutil.wait_procs([proc], timeout=wait)
            except Exception:
                pass
        for pid in owned:
            if psutil.pid_exists(pid):
                remaining.append(pid)
        return owned, remaining


class PtyRuntime:
    """拥有一个 PTY、进度输出日志、进程树与生命周期状态。"""

    def __init__(
        self,
        terminal_id: str,
        backend: PtyBackend,
        *,
        output_cap: int = 1 << 20,
        read_size: int = 65536,
        terminator: TreeTerminator | None = None,
        eof_grace: float = 8.0,
        identity: ProcessIdentity | None = None,
        identity_probe: Callable[[int], ProcessIdentity | None] | None = None,
        require_startup_gate: bool = False,
        ownership: OwnershipPolicy | None = None,
    ) -> None:
        self.terminal_id = terminal_id
        self._backend = backend
        self._read_size = read_size
        self._terminator = terminator
        self._eof_grace = eof_grace
        self.log = OutputLog(output_cap)
        self._state = RuntimeState.CREATED
        self._state_lock = threading.Lock()
        self.exit = ExitInfo()
        self.rows = 0
        self.cols = 0
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._eof_seen = threading.Event()
        self._bytes_observed_at_exit = 0
        self._process_exit_seen_at: float | None = None
        self._cancel_requested = False
        # 清理身份核验：只用记录过的身份与同一 handle 终止，避免 PID 复用误杀。
        self.identity = identity
        self._identity_probe = identity_probe
        self._require_startup_gate = require_startup_gate
        self.ownership = ownership

    # -- 状态 ---------------------------------------------------------
    @property
    def state(self) -> RuntimeState:
        with self._state_lock:
            return self._state

    def _set_state(self, target: RuntimeState) -> None:
        with self._state_lock:
            if target is self._state:
                return
            if target not in _LEGAL_TRANSITIONS[self._state]:
                raise IllegalStateTransition(f"{self._state.value} -> {target.value}")
            self._state = target

    # -- 启动 / 读取 ---------------------------------------------------
    def start(self, *, rows: int, cols: int, gate: StartupOwnershipGate | None = None) -> None:
        """启动。

        fail-closed 门槛：``gate.verify()`` 不通过时抛异常、**不进入 RUNNING**
        （对应生产要求"assign 失败/未原子赋值 → 拒绝 running"）。凡是声明了树守卫
        的 runtime，必须提供启动所有权证据，否则拒绝启动。
        """
        if gate is not None:
            gate.verify()
            # 采用门禁证据里的身份：清理必须用**赋值时记录的身份**核验，
            # 而不是事后重新推断（后者在 PID 复用下不可靠）。
            evidence = getattr(gate, "evidence", None)
            evidence_identity = getattr(evidence, "identity", None) if evidence is not None else None
            if evidence_identity is not None:
                if self.identity is not None and not self.identity.matches(evidence_identity):
                    raise OwnershipGateError(
                        "runtime 既有身份与启动证据不一致：拒绝启动（避免用错身份做清理核验）"
                    )
                self.identity = evidence_identity
        elif self._require_startup_gate:
            raise OwnershipGateError(
                "该 runtime 声明了树守卫但启动时未提供所有权证据："
                "必须提供 (assigned, atomic_with_spawn, identity, handle_bound) 四要素"
            )
        self.rows, self.cols = rows, cols
        self._set_state(RuntimeState.STARTING)
        self._set_state(RuntimeState.RUNNING)
        self._reader = threading.Thread(
            target=self._drain_loop, name=f"pty-drain-{self.terminal_id}", daemon=True
        )
        self._reader.start()

    def _drain_loop(self) -> None:
        """契约核心：持续读到通道 EOF，绝不以 ``alive()`` 为假提前退出。

        结束原因必须**分类记录**，不能一律当成正常 EOF：
        ``eof`` / ``cancelled`` / ``channel-error`` / ``eof-timeout`` /
        ``stop-requested``。只有 ``eof`` 才置 ``channel_eof`` 与 ``output_complete``
        （``cancelled`` 是我们自己关的通道，不算对端 EOF）。
        """
        stop_reason = "unknown"
        channel_eof = False
        while True:
            if self._stop.is_set() and not self._cancel_requested:
                stop_reason = "stop-requested"
                break
            try:
                data = self._backend.read(self._read_size)
            except EOFError:
                if self._cancel_requested:
                    # 通道是我们自己关掉的：即使表现为 EOFError，也不是对端 EOF。
                    stop_reason = "cancelled"
                else:
                    channel_eof = True
                    stop_reason = "eof"
                break
            except (OSError, RuntimeError, ValueError) as exc:
                if self._cancel_requested:
                    # 取消原语（关闭句柄）在自己触发的 read 上表现为传输层异常
                    # （实测 pywinpty 抛 ConnectionAbortedError WinError 10053）。
                    # 这不是"意外的通道错误"，但**也不是**对端 EOF：通道是我们自己
                    # 断开的，所以 channel_eof/output_complete 都不置位。
                    stop_reason = "cancelled"
                else:
                    self.exit.channel_error = f"{type(exc).__name__}: {exc}"
                    stop_reason = "channel-error"
                break
            if data:
                self.log.append(data)
                continue
            # 空读：后端在当前实现下等价于“暂时没有数据”，退让后继续。
            if not self._backend.alive():
                if self._process_exit_seen_at is None:
                    self._process_exit_seen_at = time.monotonic()
                    self._bytes_observed_at_exit = self.log.total_bytes
                if time.monotonic() - self._process_exit_seen_at > self._eof_grace:
                    stop_reason = "eof-timeout"
                    break
            time.sleep(0.001)
        self.exit.observed_at = time.monotonic()
        self.exit.drain_stop_reason = stop_reason
        self.exit.channel_eof = channel_eof
        # 只有**对端真实 EOF** 才声明“输出完整”。取消（通道被我们自己断开）、
        # 通道错误、超时、stop 一律不声明。
        self.exit.output_complete = stop_reason == "eof"
        self.exit.bytes_at_stop = self.log.total_bytes
        self.exit.reader_done = True
        self._eof_seen.set()

    def wait_eof(self, timeout: float) -> bool:
        """等 reader **结束**（不区分结束原因；原因看 ``exit.drain_stop_reason``）。"""
        return self._eof_seen.wait(timeout)

    def _cancel_drain(self) -> str:
        """取消 reader 的阻塞读。

        取消原语就是关闭终端 IO 句柄：``pywinpty`` 无读超时，但关闭 pty 句柄会让
        阻塞中的 ``read()`` 抛 ``EOFError`` 而立即返回。返回取消手段，供报告记录。
        """
        self._cancel_requested = True
        self._stop.set()
        close = getattr(self._backend, "close", None)
        if not callable(close):
            return "unsupported"
        try:
            close()
            return "backend-close"
        except Exception as exc:
            return f"backend-close-failed:{type(exc).__name__}"

    def _converge_drain(self, grace: float) -> dict[str, Any]:
        """收敛 reader。**不允许**把 daemon 线程当作回收保证。

        - 先在 grace 内等 reader 自然结束；
        - 没结束就请求取消（关句柄），再在 grace 内等；
        - 仍未结束 → ``reader_converged=False``，调用方必须保留 owner。
        """
        if self._reader is None:
            # 从未启动 reader（例如创建后未 start 就 close）：不存在待回收线程。
            return {
                "reader_converged": True,
                "reader_joined": True,
                "reader_cancelled": False,
                "cancel_kind": "reader-not-started",
            }
        if self._eof_seen.wait(grace):
            self._join_reader(grace)
            return {
                "reader_converged": self._reader_joined(),
                "reader_joined": self._reader_joined(),
                "reader_cancelled": False,
                "cancel_kind": "none",
            }
        cancel_kind = self._cancel_drain()
        saw_end = self._eof_seen.wait(grace)
        self._join_reader(grace)
        return {
            "reader_converged": bool(saw_end and self._reader_joined()),
            "reader_joined": self._reader_joined(),
            "reader_cancelled": True,
            "cancel_kind": cancel_kind,
        }

    def _join_reader(self, timeout: float) -> None:
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=timeout)

    def _reader_joined(self) -> bool:
        return self._reader is None or not self._reader.is_alive()

    # -- 读写 / 尺寸 ---------------------------------------------------
    def read_from(self, cursor: int, *, max_bytes: int | None = None) -> OutputPage:
        return self.log.read_from(cursor, max_bytes=max_bytes)

    def write(self, data: bytes) -> int:
        if self.state not in (RuntimeState.RUNNING, RuntimeState.EXITING):
            raise IllegalStateTransition(f"write in state {self.state.value}")
        return self._backend.write(data)

    def resize(self, rows: int, cols: int) -> bool:
        """尺寸变更。进程已退出时返回 False，不抛异常（迟到 resize 必须有明确语义）。"""
        if self.state not in (RuntimeState.RUNNING, RuntimeState.EXITING):
            return False
        self._backend.resize(rows, cols)
        self.rows, self.cols = rows, cols
        return True

    # -- 退出事件的对外发布 --------------------------------------------
    def poll_exit(self) -> ExitInfo:
        """把“根进程退出”与“reader 结束 / 通道 EOF / 输出完整”分开公布。"""
        if not self.exit.process_exit_seen and not self._backend.alive():
            self.exit.process_exit_seen = True
            self.exit.code = self._backend.exit_code()
            self.exit.reason = "process-exit"
        if self.exit.reader_done and not self.exit.process_exit_seen:
            # 通道先结束而进程仍在：可能是错误/超时/被取消，不能当作正常退出。
            self.exit.reason = f"reader-stopped:{self.exit.drain_stop_reason}"
        return self.exit

    def wait_exit(self, timeout: float) -> ExitInfo:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            info = self.poll_exit()
            if info.process_exit_seen:
                # 进程已退出：再给 drain 一个有限窗口补尾部输出
                self.wait_eof(min(2.0, max(0.0, deadline - time.monotonic())))
                return self.poll_exit()
            time.sleep(0.01)
        return self.poll_exit()

    def child_pids(self) -> list[int]:
        if self._terminator is None:
            return []
        return [p for p in self._terminator.owned_pids(self._backend.pid) if p != self._backend.pid]

    # -- 身份核验 ------------------------------------------------------
    def _identity_mismatch(self) -> str | None:
        """返回不匹配原因；``None`` 表示可继续清理。

        fail-closed 规则：无法取得可比字段（PID/FILETIME 都缺）也算不匹配 ——
        宁可拒绝清理并保留 owner，也不对不可验证的 PID 下手（对齐现有
        ``background_jobs._owns_process`` 与 lifecycle 探针的身份原则）。
        """
        if self.identity is None or self._identity_probe is None:
            return None
        pid = self._backend.pid
        if not pid:
            return None
        try:
            live = self._identity_probe(pid)
        except Exception as exc:
            return f"identity probe failed: {type(exc).__name__}: {exc}"
        if live is None:
            return None  # 进程已不可探测：按已退出处理，不阻止正常收敛
        if live.matches(self.identity):
            return None
        return (
            f"identity mismatch (refusing to terminate): recorded pid={self.identity.pid} "
            f"filetime={self.identity.created_at_filetime}, live pid={live.pid} "
            f"filetime={live.created_at_filetime}"
        )

    # -- 清理 ---------------------------------------------------------
    def close(
        self,
        *,
        reason: str = "explicit-close",
        interrupt: bool = True,
        interrupt_delay: float = 0.15,
        terminate_timeout: float = 1.5,
        reader_grace: float = 2.0,
        terminate: Callable[[bool], None] | None = None,
    ) -> CleanupReport:
        """清理契约：失败必须保留 owner。

        ``terminate`` 允许注入替代终止实现，用于验证失败路径（探针用）。
        ``reader_grace`` 是 reader 收敛的两段等待上限（自然结束 / 取消后各一段）。
        """
        started = time.monotonic()
        report = CleanupReport(terminal_id=self.terminal_id, requested_reason=reason)
        if self.state in (RuntimeState.EXITED, RuntimeState.LOST):
            report.state_after = self.state
            report.backend_closed = True
            report.seconds = round(time.monotonic() - started, 3)
            return report

        # 身份核验先于任何终止动作：不可验证或与记录不符时**拒杀**（防 PID 复用误杀）。
        mismatch = self._identity_mismatch()
        if mismatch is not None:
            self._set_state(RuntimeState.EXITING)
            self._set_state(RuntimeState.CLEANUP_FAILED)
            report.identity_check = (
                "probe-failed" if mismatch.startswith("identity probe failed") else "mismatch"
            )
            report.terminate_result = "refused-identity-mismatch"
            report.error = mismatch
            report.owner_retained = True
            report.state_after = self.state
            report.seconds = round(time.monotonic() - started, 3)
            return report
        report.identity_check = "verified" if self.identity is not None else "not-required"

        self._set_state(RuntimeState.EXITING)
        root_pid = self._backend.pid
        # 所有权快照必须在终止**之前**取得：根 PID 消失后无法再枚举后代。
        if self._terminator is not None and root_pid:
            report.tree_owned_pids = tuple(self._terminator.owned_pids(root_pid))
        if interrupt:
            try:
                if self._backend.alive():
                    self._backend.write(b"\x03")
                    report.interrupt_sent = True
                    time.sleep(interrupt_delay)
            except Exception as exc:
                report.interrupt_error = repr(exc)

        do_terminate = terminate or (lambda force: self._backend.terminate(force))
        done = threading.Event()
        box: dict[str, str] = {}

        def _term() -> None:
            try:
                do_terminate(True)
                box["result"] = "returned"
            except Exception as exc:
                box["result"] = "error"
                box["error"] = repr(exc)
            finally:
                done.set()

        worker = threading.Thread(target=_term, name=f"pty-terminate-{self.terminal_id}", daemon=True)
        worker.start()
        if not done.wait(terminate_timeout):
            report.terminate_result = "timed-out"
        else:
            report.terminate_result = box.get("result", "error")
            if box.get("error"):
                report.terminate_error = box["error"]

        root_pid = self._backend.pid
        if self._terminator is not None and report.tree_owned_pids:
            # 清理自己拥有的后代，并用先前取得的所有权清单核对残留。
            self._terminator.terminate_tree(root_pid, timeout=2.0)
            report.tree_remaining_pids = tuple(self._terminator.remaining(report.tree_owned_pids))

        # reader 收敛：等自然结束 → 不行就取消并证明已回收；两者都不行则保留 owner。
        #
        # 注意：取消原语（关闭 pty 句柄）本身会终止终端进程，因此**只有在终止与整树
        # 都已成功时才允许取消**；否则会毁掉"进程仍存活、owner 待重试"的证据。
        terminate_ok = report.terminate_result == "returned" and not report.terminate_error
        tree_ok = not report.tree_remaining_pids
        if terminate_ok and tree_ok:
            conv = self._converge_drain(reader_grace)
        else:
            conv = {
                "reader_converged": False,
                "reader_joined": self._reader_joined(),
                "reader_cancelled": False,
                "cancel_kind": "skipped-terminate-failed",
            }
        report.reader_converged = bool(conv["reader_converged"])
        report.reader_joined = bool(conv["reader_joined"])
        report.reader_cancelled = bool(conv["reader_cancelled"])
        report.cancel_kind = str(conv["cancel_kind"])
        report.drain_stop_reason = self.exit.drain_stop_reason
        if report.cancel_kind.startswith("backend-close"):
            report.backend_closed = True  # 取消原语即关闭句柄

        failures: list[str] = []
        if report.terminate_result != "returned":
            failures.append(f"terminate={report.terminate_result}")
        if report.terminate_error:
            failures.append(f"terminate_error={report.terminate_error}")
        if report.tree_remaining_pids:
            failures.append(f"still-alive={list(report.tree_remaining_pids)}")
        if not report.reader_converged:
            failures.append(
                f"reader 未收敛（cancel={report.cancel_kind}）：无法证明读已取消并回收，"
                "拒绝标记 exited"
            )

        if failures:
            # 清理失败：不移除记录、保留 owner 以便重试；绝不谎报 exited。
            self._set_state(RuntimeState.CLEANUP_FAILED)
            report.owner_retained = True
            report.error = "; ".join(failures)
            report.state_after = self.state
            report.seconds = round(time.monotonic() - started, 3)
            return report

        if not report.backend_closed:
            try:
                self._backend.close()
                report.backend_closed = True
            except Exception as exc:
                report.error = f"close failed: {exc!r}"
        if report.error:
            self._set_state(RuntimeState.CLEANUP_FAILED)
            report.owner_retained = True
        else:
            self._set_state(RuntimeState.EXITED)
        report.state_after = self.state
        report.seconds = round(time.monotonic() - started, 3)
        return report


# --------------------------------------------------------------------------
# 4. 全局 Terminal registry
# --------------------------------------------------------------------------


@dataclass
class TerminalRecord:
    terminal_id: str
    owner: str
    state: RuntimeState
    created_at: float
    rows: int
    cols: int
    pid: int | None
    workspace_id: str | None = None
    session_id: str | None = None
    exit_code: int | None = None
    runtime: PtyRuntime | None = field(default=None, repr=False)


class UnknownTerminalError(KeyError):
    pass


class TerminalRegistry:
    """全局终端 registry。

    与 Agent Session / Worker 命名空间分开（``term_`` 前缀），
    可选关联 Workspace 或 Session。
    """

    OWNER_SERVICE = "service"
    OWNER_DETACHED = "detached"

    def __init__(self) -> None:
        self._records: dict[str, TerminalRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def new_terminal_id() -> str:
        return f"{TERMINAL_ID_PREFIX}{uuid.uuid4().hex[:16]}"

    def register(
        self,
        runtime: PtyRuntime,
        *,
        owner: str = OWNER_SERVICE,
        workspace_id: str | None = None,
        session_id: str | None = None,
    ) -> TerminalRecord:
        with self._lock:
            record = TerminalRecord(
                terminal_id=runtime.terminal_id,
                owner=owner,
                state=runtime.state,
                created_at=time.time(),
                rows=runtime.rows,
                cols=runtime.cols,
                pid=runtime._backend.pid,
                workspace_id=workspace_id,
                session_id=session_id,
                runtime=runtime,
            )
            self._records[record.terminal_id] = record
            return record

    def get(self, terminal_id: str) -> TerminalRecord:
        with self._lock:
            record = self._records.get(terminal_id)
            if record is None:
                raise UnknownTerminalError(terminal_id)
            record.state = record.runtime.state if record.runtime else record.state
            if record.runtime is not None:
                record.exit_code = record.runtime.exit.code
            return record

    def list(self) -> list[TerminalRecord]:
        with self._lock:
            return [self.get(tid) for tid in list(self._records)]

    def close(self, terminal_id: str, *, reason: str = "explicit-close", **kwargs: Any) -> CleanupReport:
        record = self.get(terminal_id)
        if record.runtime is None:
            raise UnknownTerminalError(terminal_id)
        report = record.runtime.close(reason=reason, **kwargs)
        with self._lock:
            # 清理失败时**保留**记录以便重试；成功退出才允许移除。
            record.state = report.state_after
        return report

    def remove(self, terminal_id: str) -> None:
        with self._lock:
            record = self._records.get(terminal_id)
            if record is None:
                raise UnknownTerminalError(terminal_id)
            if record.state not in (RuntimeState.EXITED, RuntimeState.LOST):
                raise IllegalStateTransition(
                    f"cannot remove terminal in state {record.state.value}"
                )
            del self._records[terminal_id]


# --------------------------------------------------------------------------
# 5. attachment / control lease
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LeaseToken:
    """一次附件（attachment）的授权凭证。

    ``revocation_id`` 是不透明随机串，由 registry 生成、**不对外可推导**：
    client_id 与 role 是调用方自报字段，不能作为授权依据；真正区分"同一个
    控制权"的是 revocation_id。撤销后该 id 进入 revoked 集合，任何持有副本的
    调用方都会被拒绝（含伪造 client_id/role 的 token）。
    """

    terminal_id: str
    client_id: str
    generation: int
    role: str  # "control" | "observer"
    rows: int
    cols: int
    revocation_id: str = ""


class StaleLeaseError(RuntimeError):
    pass


class NotControlLeaseError(RuntimeError):
    pass


class AttachmentRegistry:
    """一个终端可有多个观察连接，但同一时刻只有一个输入控制权。

    契约要点（对应 MA 的修正项）：

    1. **撤销必须让 token 立刻失效**：``detach`` 会把 token 的 ``revocation_id``
       记入 revoked 集合并对 control 自增 generation；此后该 token 的
       send/resize/transfer 全部被拒，且**不会**因任何 ``setdefault`` 之类
       写法被"复活"。
    2. **校验与操作在同一临界区内**：attach/transfer/detach/send/resize 共用一个
       可重入锁，"校验 → 写终端"是原子的，保证单 writer 的先后顺序；不存在
       "校验通过后、写之前被转交/撤销"的竞态窗口。
    3. **伪造 token 被拒**：控制权操作要求 token 与 registry 记录的控制权
       **同一 revocation_id**；role/client_id 自报不足以取得控制权。

    **明确未实现（不在本契约范围内）**：真实身份认证与网络授权（把 token 绑定到
    Pan 的登录用户/连接/ACL）、跨进程 token 保密、浏览器侧权限投影。本 registry
    只解决"进程内多客户端单 writer + 撤销语义"；授权必须由 Pan 现有入口在先完成。
    """

    def __init__(self, registry: TerminalRegistry) -> None:
        self._registry = registry
        self._gen: dict[str, int] = {}
        self._control: dict[str, LeaseToken] = {}
        self._observers: dict[str, set[str]] = {}
        self._revoked: set[str] = set()
        # attach/transfer/detach/send/resize 共用一个锁：校验与操作原子。
        self._lock = threading.RLock()

    # -- 内部：仅在持锁时调用 -------------------------------------------
    def _new_token(
        self, terminal_id: str, client_id: str, role: str, rows: int, cols: int
    ) -> LeaseToken:
        generation = self._gen.get(terminal_id, 0)
        if role == "control":
            generation += 1
            self._gen[terminal_id] = generation
            previous = self._control.get(terminal_id)
            if previous is not None:
                # 旧控制权立刻作废（转交/抢占），防止迟到消息继续操作终端。
                self._revoked.add(previous.revocation_id)
        return LeaseToken(
            terminal_id=terminal_id,
            client_id=client_id,
            generation=generation,
            role=role,
            rows=rows,
            cols=cols,
            revocation_id=uuid.uuid4().hex,
        )

    def _check_locked(self, token: LeaseToken, *, require_control: bool) -> None:
        """校验 token。

        ``generation`` 是**控制权世代**：只有控制权操作才要求世代匹配。
        观察者不携带任何权限，其有效性只由"是否被撤销"决定 —— 否则控制权
        转交/撤销会把无关的观察连接一起打断（探针 M14.9 复现过这个缺陷）。
        """
        self._registry.get(token.terminal_id)  # 未知/未授权终端直接失败
        if not token.revocation_id or token.revocation_id in self._revoked:
            raise StaleLeaseError("lease token 已被撤销")
        if not require_control:
            return
        # 先判权限类别：观察者 token 的失败原因应稳定为"无权限"，与世代无关。
        if token.role != "control":
            raise NotControlLeaseError("observer lease 不能写入/改尺寸/转交控制权")
        current = self._gen.get(token.terminal_id, 0)
        if token.generation != current:
            raise StaleLeaseError(f"lease generation {token.generation} != current {current}")
        holder = self._control.get(token.terminal_id)
        if holder is None or holder.revocation_id != token.revocation_id:
            raise NotControlLeaseError(
                "token 不是当前控制权持有者（已被撤销、已转交或为伪造 token）"
            )

    # -- 公开 API -------------------------------------------------------
    def attach(
        self,
        terminal_id: str,
        client_id: str,
        *,
        role: str = "observer",
        rows: int = 0,
        cols: int = 0,
    ) -> LeaseToken:
        self._registry.get(terminal_id)  # 未授权/不存在直接失败
        with self._lock:
            token = self._new_token(terminal_id, client_id, role, rows, cols)
            if role == "control":
                self._control[terminal_id] = token
            else:
                self._observers.setdefault(terminal_id, set()).add(client_id)
            return token

    def transfer_control(self, token: LeaseToken, *, to_client: str) -> LeaseToken:
        with self._lock:
            self._check_locked(token, require_control=True)
            new_token = self._new_token(token.terminal_id, to_client, "control", token.rows, token.cols)
            self._control[token.terminal_id] = new_token
            return new_token

    def detach(self, token: LeaseToken) -> None:
        """撤销该 token。撤销后其 send/resize/transfer 一律被拒，且不可复活。"""
        with self._lock:
            tid = token.terminal_id
            self._revoked.add(token.revocation_id)
            holder = self._control.get(tid)
            if holder is not None and holder.revocation_id == token.revocation_id:
                del self._control[tid]
                # 控制权撤销同时自增 generation：任何同一代的副本/迟到消息全部失效。
                self._gen[tid] = self._gen.get(tid, 0) + 1
            else:
                self._observers.get(tid, set()).discard(token.client_id)

    def is_revoked(self, token: LeaseToken) -> bool:
        with self._lock:
            return (not token.revocation_id) or token.revocation_id in self._revoked

    def validate(self, token: LeaseToken) -> None:
        with self._lock:
            self._check_locked(token, require_control=False)

    def send(self, token: LeaseToken, data: bytes) -> int:
        with self._lock:
            self._check_locked(token, require_control=True)
            record = self._registry.get(token.terminal_id)
            assert record.runtime is not None
            # 校验与写入在同一临界区：单 writer 顺序有保证。
            return record.runtime.write(data)

    def resize(self, token: LeaseToken, rows: int, cols: int) -> bool:
        with self._lock:
            self._check_locked(token, require_control=True)
            record = self._registry.get(token.terminal_id)
            assert record.runtime is not None
            return record.runtime.resize(rows, cols)

    def control_holder(self, terminal_id: str) -> LeaseToken | None:
        with self._lock:
            return self._control.get(terminal_id)


# --------------------------------------------------------------------------
# 6. automation screen observer
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenSnapshot:
    """屏幕快照。``fidelity`` 必须显式声明，不能让调用方误以为是权威渲染状态。"""

    rows: int
    cols: int
    lines: tuple[str, ...]
    cursor: tuple[int, int]
    alternate_screen: bool | None
    private_modes: frozenset[int]
    raw_mode_bits: frozenset[int]
    saved_cursor: bool
    scrollback_lines: int
    engine: str
    fidelity: str  # "partial" | "full" | "unavailable"
    note: str = ""


@dataclass(frozen=True)
class WaitOutcome:
    matched: bool
    timed_out: bool
    screen: ScreenSnapshot | None
    last_text: str
    observed_bytes: int


class ScreenObserver(Protocol):
    """自动化观察者（CBC driver 这类业务状态机的输入）。

    ``snapshot`` 必须是**仿真器状态**，不是尾部文本：尾部文本无法表达
    备用屏、光标位置、DEC 私有模式等恢复所需状态。
    """

    def feed(self, data: bytes) -> None: ...

    def snapshot(self) -> ScreenSnapshot: ...

    def wait_for(
        self,
        predicate: Callable[[str], bool],
        timeout: float,
        *,
        quiet_ms: float = 0.0,
    ) -> WaitOutcome: ...


# 备用屏相关 DEC 私有模式（47 为旧式，1047/1049 为现代 xterm 形式）
ALT_SCREEN_DEC_MODES = (47, 1047, 1049)


class PyteScreenObserver:
    """基于 pyte 0.8.2 的参考实现。

    实测限制（2026-10-03，见探针 R6/M7）——这些不是猜测，是源码与运行结果：

    1. pyte 把 DEC 私有模式存为 ``mode << 5``，所以 ``screen.mode`` 里看到的是
       33568 而不是 1049；必须右移还原。
    2. pyte **没有实现备用屏缓冲**（全源码无 47/1047/1049 处理）：TUI 内容直接
       写进同一个 buffer，退出备用屏时不会恢复主屏内容。
    3. pyte 没有 ``history``/滚动历史，也没有鼠标追踪、bracketed paste、
       Windows/Win32 输入模式（``?9001``）、焦点上报（``?1004``）等。

    因此本实现固定 ``fidelity="partial"``：可做**自动化判定**，不足以单独承担
    "网页 TUI 状态恢复"。权威快照应由完整仿真器（xterm.js + serialize addon
    或等价物）产出——该取舍列为待 TUI 探针证据确认的决定项。
    """

    ENGINE = "pyte"
    SUPPORTS_ALTERNATE_SCREEN_BUFFER = False
    SUPPORTS_SCROLLBACK = False

    def __init__(self, rows: int, cols: int) -> None:
        from pyte import Screen, Stream  # type: ignore[import-not-found]

        self.rows = rows
        self.cols = cols
        self._screen = Screen(cols, rows)
        self._stream = Stream(self._screen)
        self._bytes = 0
        self._lock = threading.Lock()

    def feed(self, data: bytes) -> None:
        with self._lock:
            text = data.decode("utf-8", "replace")
            self._stream.feed(text)
            self._bytes += len(data)

    def text(self) -> str:
        with self._lock:
            return "\n".join(self._screen.display).rstrip()

    def resize(self, rows: int, cols: int) -> None:
        """仿真器尺寸必须与 PTY 尺寸同步。

        契约要求：resize 属于 attachment 契约的一部分，**客户端仿真器与 PTY
        必须一起改尺寸**，否则 TUI 重绘基于错误的列数产生换行错位。
        """
        with self._lock:
            self._screen.resize(lines=rows, columns=cols)
            self.rows, self.cols = rows, cols

    @property
    def observed_bytes(self) -> int:
        return self._bytes

    @staticmethod
    def decode_modes(raw: Iterable[int]) -> tuple[frozenset[int], frozenset[int]]:
        """还原 pyte 的 ``mode << 5`` 私有位，返回 (私有模式, 普通模式)。"""
        raw_set = {int(m) for m in raw}
        private = frozenset(m >> 5 for m in raw_set if m > 31)
        plain = frozenset(m for m in raw_set if m <= 31)
        return private, plain

    def snapshot(self) -> ScreenSnapshot:
        with self._lock:
            raw = frozenset(int(m) for m in getattr(self._screen, "mode", frozenset()))
            private, plain = self.decode_modes(raw)
            cursor = (int(self._screen.cursor.y), int(self._screen.cursor.x))
            return ScreenSnapshot(
                rows=self._screen.lines,
                cols=self._screen.columns,
                lines=tuple(self._screen.display),
                cursor=cursor,
                alternate_screen=bool(private & set(ALT_SCREEN_DEC_MODES)),
                private_modes=private,
                raw_mode_bits=frozenset(raw),
                saved_cursor=bool(getattr(self._screen, "saved_columns", None) is not None),
                scrollback_lines=0,
                engine=f"{self.ENGINE} (single-buffer, no alt-screen, no scrollback)",
                fidelity="partial",
                note="pyte 无备用屏缓冲/滚动历史/鼠标/粘贴/键盘协议；模式位需 <<5 还原",
            )

    def wait_for(
        self,
        predicate: Callable[[str], bool],
        timeout: float,
        *,
        quiet_ms: float = 0.0,
    ) -> WaitOutcome:
        deadline = time.monotonic() + timeout
        last = self.text()
        settled_at: float | None = None
        while True:
            now = time.monotonic()
            if now >= deadline:
                return WaitOutcome(False, True, self.snapshot(), last, self._bytes)
            value = self.text()
            if predicate(value):
                if quiet_ms <= 0:
                    return WaitOutcome(True, False, self.snapshot(), value, self._bytes)
                if value != last:
                    settled_at = now
                elif settled_at is not None and (now - settled_at) * 1000 >= quiet_ms:
                    return WaitOutcome(True, False, self.snapshot(), value, self._bytes)
            last = value
            time.sleep(0.02)


def tail_text_view(raw: bytes, *, window: int = 4096) -> str:
    """反例工具：模拟"只保留尾部文本"的恢复方式，用于对比证明快照不可替代。

    实现把所有控制序列都剥掉（包括 ``ESC =`` 这类单字符序列），所以结果里
    既没有备用屏标志、也没有模式/光标信息——尾部文本通道在结构上无法承载
    终端状态。这正是"不能仅尾文本恢复"的可检验原因。
    """
    import re

    tail = raw[-window:]
    text = tail.decode("utf-8", "replace")
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)  # OSC
    text = re.sub(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]", "", text)  # CSI
    text = re.sub(r"\x1b[()*+#][0-9A-Za-z]", "", text)  # 字符集切换
    text = re.sub(r"\x1b.", "", text)  # ESC 单字符/兜底
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


# --------------------------------------------------------------------------
# 7. 自动化 driver 边界（CBC 业务菜单留在 driver）
# --------------------------------------------------------------------------


class AutomationDriver(Protocol):
    """业务状态机接口。公共核心只提供 runtime/observer/lease。"""

    driver_id: str

    def run(self, context: "AutomationContext") -> dict[str, Any]: ...


@dataclass
class AutomationContext:
    terminal_id: str
    runtime: PtyRuntime
    observer: ScreenObserver
    control: LeaseToken
    attachments: AttachmentRegistry
    timeout: float = 30.0

    def send_text(self, value: str) -> int:
        return self.attachments.send(self.control, value.encode("utf-8", "surrogatepass"))

    def send_keys(self, *keys: str) -> int:
        return self.attachments.send(self.control, "".join(keys).encode("ascii"))

    def screen_text(self) -> str:
        text = getattr(self.observer, "text", None)
        if callable(text):
            return str(text())
        snapshot = self.observer.snapshot()
        return "\n".join(snapshot.lines).rstrip()

    def wait_for(self, predicate: Callable[[str], bool], timeout: float, *, quiet_ms: float = 0.0) -> WaitOutcome:
        return self.observer.wait_for(predicate, timeout, quiet_ms=quiet_ms)


# --------------------------------------------------------------------------
# 8. 所有权策略与 fail-closed 工厂（**布局中立**）
# --------------------------------------------------------------------------


class OwnershipPolicyRequired(RuntimeError):
    """未显式给出所有权策略。工厂没有默认值，避免"默认无人负责整树"。"""


class UnownedTreeRejected(RuntimeError):
    """声明了树守卫能力却未提供守卫实现，且未显式确认接受"无 OS 级守卫"。"""


class DetachedOwnershipNotImplemented(RuntimeError):
    pass


class ExternalOwnershipNotYetValidated(RuntimeError):
    pass


class OwnershipGateError(RuntimeError):
    """启动所有权证据不完整：拒绝进入 running。"""


@dataclass(frozen=True)
class ProcessIdentity:
    """进程身份 = PID + 创建时间。

    ``created_at_filetime`` 是 Windows 100ns FILETIME（精确相等比较，无容差）；
    只有 ``created_at`` 时可给容差。两者都缺 → ``matches`` 恒为 False
    （fail-closed：不可验证即视为不匹配，拒绝对不可验证 PID 下手）。
    """

    pid: int | None
    created_at_filetime: int | None = None
    created_at: float | None = None
    image: str | None = None

    def matches(self, other: "ProcessIdentity | None", *, tolerance: float = 0.0) -> bool:
        if other is None or self.pid is None or other.pid is None:
            return False
        if self.pid != other.pid:
            return False
        if self.created_at_filetime is not None and other.created_at_filetime is not None:
            return int(self.created_at_filetime) == int(other.created_at_filetime)
        if self.created_at is not None and other.created_at is not None:
            return abs(float(self.created_at) - float(other.created_at)) <= tolerance
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "created_at_filetime": self.created_at_filetime,
            "created_at": self.created_at,
            "image": self.image,
        }


def win32_identity_probe(pid: int) -> "ProcessIdentity | None":
    """用 ``OpenProcess`` + ``GetProcessTimes`` 取 PID + 100ns FILETIME。

    与生命周期 TA 的教训一致：``OpenProcess`` 成功**不等于**进程存活
    （句柄被父进程持有的已终止进程仍可打开），所以本函数同时返回退出码，
    由 ``matches`` 只比较身份、不把句柄可打开当作存活证明。
    """
    if not pid:
        return None
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_t = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_t),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        filetime = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return ProcessIdentity(pid=int(pid), created_at_filetime=filetime)
    finally:
        kernel32.CloseHandle(handle)


def psutil_identity_probe(pid: int) -> "ProcessIdentity | None":
    try:
        import psutil

        proc = psutil.Process(int(pid))
        created = proc.create_time()
        return ProcessIdentity(pid=int(pid), created_at=created, image=proc.name())
    except Exception:
        return None


class NullTreeTerminator:
    """显式"无 OS 级树守卫"。

    只在调用方显式 ``acknowledge_unowned_tree=True`` 时可用；用于探针与
    "确实没有内核级守卫"的诚实标注，不作为生产默认。
    """

    def describe(self) -> dict[str, Any]:
        return {"kind": "none", "os_level_guard": False}

    def owned_pids(self, root_pid: int | None) -> list[int]:
        return []

    def remaining(self, pids: Iterable[int]) -> list[int]:
        return list(pids)

    def terminate_tree(self, root_pid: int | None, *, timeout: float) -> tuple[list[int], list[int]]:
        return [], []


class JobObjectTreeTerminator:
    """Windows Job Object 树守卫的占位声明（本阶段**不实现**）。

    故意不在这里提供实现：公共契约不绑死所有权布局，也**不把探索探针当作生产安全实现**。

    布局形态（都可用同一 `OwnershipPolicy` 表达，接口无需改动）：

    - 布局 A：Pan 服务持有 Job 句柄；
    - 布局 B：独立 runner 出生即持有 Job 句柄（生命周期 TA 推荐布局，其实测满足默认终止/detach/重连）；
    - 布局 C：A→B 经 `DuplicateHandle` + IPC 移交 Job 句柄所有权。

    已实测（生命周期 TA `9bd858e2`，MA 已接受为探索成果）：

    - 成员资格只增不减：运行中进程可 assign 加入（含嵌套），**无移除 API**，逃脱只能出生时 breakaway；
    - kill-on-close 绑定不能被"多加一个 Job"中和；
    - **句柄所有权可移交**：`DuplicateHandle` 移交给 A、再由 A 移交给 B；原持有者关闭句柄/退出不触发 kill，
      **最后一个句柄关闭（含持有者崩溃）才 0.0s 整树终止**。

    仍未测 / 未解决（不得当作已解决）：

    - **ConPTY host/IO 接管迁移**：未测，双方都不做可行性声明；
    - **spawn→assign 启动窗口未消除**（`pywinpty.spawn` 不暴露 creationflags），生产 backend 需另行加固；
    - 探针控制端点为 loopback + 明文 token，非产品安全模型。
    """

    def __init__(self, *_: Any, **__: Any) -> None:
        raise NotImplementedError(
            "Job Object 树守卫实现属生产层：需先落地挂起式 spawn/原子入组（消除 spawn→assign 窗口）、"
            "命名管道或 ACL 控制端点，并确认 ConPTY host/IO 归属（未测）。"
            "公共契约不在此绑死服务持 Job 或 runner 布局，也不照搬探索探针作为生产实现。"
        )


@dataclass(frozen=True)
class ProcessOwnershipEvidence:
    """启动所有权证据：四要素缺一不可。"""

    assigned: bool
    atomic_with_spawn: bool
    identity: ProcessIdentity | None
    handle_bound_for_cleanup: bool
    guard: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "assigned": self.assigned,
            "atomic_with_spawn": self.atomic_with_spawn,
            "identity": self.identity.as_dict() if self.identity else None,
            "handle_bound_for_cleanup": self.handle_bound_for_cleanup,
            "guard": self.guard,
            "detail": self.detail,
        }


class StartupOwnershipGate(Protocol):
    """启动前必须通过的所有权门槛；不通过则 runtime 不得进入 running。"""

    def verify(self) -> None: ...


class UnverifiedOwnershipGate:
    """按四要素校验启动所有权证据（fail-closed）。

    对应生产层要求：
    1. **spawn 后 assign 窗口**：``atomic_with_spawn`` 必须为真，即赋值与创建
       原子化（挂起创建 → 赋值 → 恢复），否则 child 在窗口内派生即可逃逸；
    2. **assign 失败必须拒绝 running**：``assigned`` 为假即拒绝；
    3. **清理身份查验 + terminate 同一 handle**：``identity`` 非空且
       ``handle_bound_for_cleanup`` 为真。
    """

    def __init__(self, evidence: ProcessOwnershipEvidence) -> None:
        self.evidence = evidence

    def verify(self) -> None:
        ev = self.evidence
        if not ev.assigned:
            raise OwnershipGateError(
                "spawn 后 assign 失败或未赋值：拒绝进入 running（必须先终止再重试）"
            )
        if not ev.atomic_with_spawn:
            raise OwnershipGateError(
                "所有权赋值未与 spawn 原子化：存在 spawn→assign 逃逸窗口，拒绝进入 running"
            )
        if ev.identity is None:
            raise OwnershipGateError("缺少进程身份（PID + 创建时间）：无法在清理时核验，拒绝进入 running")
        if not ev.handle_bound_for_cleanup:
            raise OwnershipGateError(
                "清理未绑定赋值时的同一 handle：按 PID 重新打开可能在 PID 复用时误杀，拒绝进入 running"
            )


class OwnershipMode(str, Enum):
    SERVICE = "service"  # 默认：随 Pan 服务生死
    DETACHED = "detached"  # 显式 runtime detach：独立所有者，保留原 PTY/进程
    EXTERNAL = "external"  # 由外部 runner/宿主持有（布局中立；语义未定义，探针只覆盖 runner 自持布局）


@dataclass(frozen=True)
class OwnershipPolicy:
    """所有权策略：**声明**谁持有树守卫，而不是把接口钉在某种布局上。

    ``lifecycle_owner`` 只是数据（例如 ``"pan-service"`` / ``"runner"`` /
    ``"external-host"``）：公共核心**不**按它分支，因此布局 A/B/C 都可以用同一
    接口表达。真正定型的是默认寿命语义（``mode``）。
    """

    mode: "OwnershipMode"
    lifecycle_owner: str
    tree_guard_kind: str  # "job-object" | "process-group" | "none"
    tree_guard: TreeTerminator | None = None
    lease_grace_seconds: float | None = None
    detached: bool = False
    notes: str = ""

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "lifecycle_owner": self.lifecycle_owner,
            "tree_guard_kind": self.tree_guard_kind,
            "tree_guard": getattr(self.tree_guard, "describe", lambda: None)()
            if self.tree_guard is not None
            else None,
            "lease_grace_seconds": self.lease_grace_seconds,
            "detached": self.detached,
            "notes": self.notes,
        }

    def on_service_shutdown(self, runtime: PtyRuntime) -> CleanupReport:
        """默认寿命策略：SERVICE 随 Pan 结束；非 service 不由 Pan 在此终止。

        非 service 被拒的原因**不是产品决定未定**（默认寿命与显式 detach 语义已由用户决定，且生命周期
        TA 已有实测支撑），而是本原型没有可用的内核级守卫 / 外部所有者实现：静默按 service 处理会
        违背"detach 后不随 Pan 终止"的既定语义，所以宁可 fail-closed。
        """
        if self.detached or self.mode is not OwnershipMode.SERVICE:
            raise DetachedOwnershipNotImplemented(
                "非 service 所有权不能按 service 语义在服务关闭时终止：本原型没有内核级守卫/外部所有者"
                "实现（生产前置项见 JobObjectTreeTerminator 文档）。"
            )
        return runtime.close(reason="service-shutdown")

    def reconnect_hint(self, terminal_id: str) -> dict[str, Any]:
        return {
            "terminal_id": terminal_id,
            "mode": self.mode.value,
            "lifecycle_owner": self.lifecycle_owner,
            "detached": self.detached,
            "reconnect_supported": False,
        }


def build_runtime(
    terminal_id: str,
    backend: PtyBackend,
    *,
    ownership: "OwnershipPolicy | None",
    acknowledge_unowned_tree: bool = False,
    require_startup_gate: bool | None = None,
    output_cap: int = 1 << 20,
    read_size: int = 65536,
    identity: ProcessIdentity | None = None,
    identity_probe: Callable[[int], ProcessIdentity | None] | None = None,
    eof_grace: float = 8.0,
) -> PtyRuntime:
    """fail-closed 工厂：没有明确所有权策略就不产出 runtime。

    校验顺序（任何一条不过就抛错，绝不静默降级）：
    1. ``ownership`` 必须有值（无默认布局）；
    2. DETACHED（本原型无内核级守卫实现）/ EXTERNAL（语义未定义）→ 拒绝；
    3. 声明了树守卫却给了 ``None`` 实现 → 必须显式 ``acknowledge_unowned_tree``；
    4. 有树守卫时默认要求启动所有权证据（``require_startup_gate`` 缺省为真）。
    """
    if ownership is None:
        raise OwnershipPolicyRequired(
            "必须显式传入 OwnershipPolicy：公共契约不预设「服务持 Job」或「runner 布局」"
        )
    if ownership.mode is OwnershipMode.DETACHED:
        raise DetachedOwnershipNotImplemented(
            "本原型没有内核级树守卫实现，无法满足 detach 语义（不随 Pan 终止 + 同 PID 重连）："
            "生命周期 TA 已在探针层实测该语义可行（s3/s5 + Job 句柄 P→A→B 移交），"
            "但生产落地前置项未解决（挂起式 spawn/原子入组、ACL 控制端点、ConPTY host/IO 归属未测）。"
            "见 docs/design/PAN_TERMINAL_PTY_CONTRACT_20261003.md §6A.1。"
        )
    if ownership.mode is OwnershipMode.EXTERNAL:
        raise ExternalOwnershipNotYetValidated(
            "外部所有者的默认寿命/崩溃/重连语义尚未定义（探针只覆盖 runner 自持布局）："
            "不得仅凭「外部进程持有」就放行。"
        )
    if ownership.tree_guard is None and not acknowledge_unowned_tree:
        raise UnownedTreeRejected(
            f"策略声明 tree_guard_kind={ownership.tree_guard_kind!r} 但未提供实现；"
            "若确实没有内核级守卫，必须显式 acknowledge_unowned_tree=True 并在证据中标注"
        )
    gate_required = (
        ownership.tree_guard is not None if require_startup_gate is None else require_startup_gate
    )
    return PtyRuntime(
        terminal_id,
        backend,
        output_cap=output_cap,
        read_size=read_size,
        terminator=ownership.tree_guard,
        eof_grace=eof_grace,
        identity=identity,
        identity_probe=identity_probe,
        require_startup_gate=gate_required,
        ownership=ownership,
    )


def require_detached_ownership(policy: Any | None) -> None:
    """显式失败的能力扩展点：本原型不提供实现前，不允许静默降级。"""
    raise DetachedOwnershipNotImplemented(
        "本原型不提供独立所有者实现：默认 managed 路径不需要该策略；detach 的生产实现见 "
        "docs/design/PAN_TERMINAL_PTY_CONTRACT_20261003.md §6A.1（语义已被探针实测，"
        "落地依赖内核级守卫 + 生产 backend 加固）。"
    )
