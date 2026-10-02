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
from typing import Any, Callable, ClassVar, Iterable, Protocol, Sequence, runtime_checkable

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
    - 驱逐以**整块**为单位，避免把一次 VT 序列从中间切断。
    - 单块大于容量时保留尾部并记录 gap（仍保证有界）。
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
    """退出事实。三个字段是三个不同事实，必须分开判断。"""

    code: int | None = None
    process_exit_seen: bool = False
    channel_eof: bool = False
    output_complete: bool = False
    reason: str = "unknown"
    observed_at: float = 0.0
    channel_error: str | None = None


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
    def start(self, *, rows: int, cols: int) -> None:
        self.rows, self.cols = rows, cols
        self._set_state(RuntimeState.STARTING)
        self._set_state(RuntimeState.RUNNING)
        self._reader = threading.Thread(
            target=self._drain_loop, name=f"pty-drain-{self.terminal_id}", daemon=True
        )
        self._reader.start()

    def _drain_loop(self) -> None:
        """契约核心：持续读到通道 EOF，绝不以 ``alive()`` 为假提前退出。"""
        while not self._stop.is_set():
            try:
                data = self._backend.read(self._read_size)
            except EOFError:
                self.exit.channel_eof = True
                break
            except (OSError, RuntimeError, ValueError) as exc:
                self.exit.channel_error = f"{type(exc).__name__}: {exc}"
                self.exit.channel_eof = True
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
                    self.exit.channel_error = self.exit.channel_error or "eof-timeout"
                    break
            time.sleep(0.001)
        self.exit.observed_at = time.monotonic()
        self.exit.channel_eof = True
        self.exit.output_complete = True
        self._eof_seen.set()

    def wait_eof(self, timeout: float) -> bool:
        return self._eof_seen.wait(timeout)

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
        """把“根进程退出”与“通道 EOF / 输出完整”分开公布。"""
        if not self.exit.process_exit_seen and not self._backend.alive():
            self.exit.process_exit_seen = True
            self.exit.code = self._backend.exit_code()
            self.exit.reason = "process-exit"
        if self.exit.channel_eof and self.exit.process_exit_seen:
            self.exit.reason = self.exit.reason or "channel-eof"
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

    # -- 清理 ---------------------------------------------------------
    def close(
        self,
        *,
        reason: str = "explicit-close",
        interrupt: bool = True,
        interrupt_delay: float = 0.15,
        terminate_timeout: float = 1.5,
        terminate: Callable[[bool], None] | None = None,
    ) -> CleanupReport:
        """清理契约：失败必须保留 owner。

        ``terminate`` 允许注入替代终止实现，用于验证失败路径（探针用）。
        """
        started = time.monotonic()
        report = CleanupReport(terminal_id=self.terminal_id, requested_reason=reason)
        if self.state in (RuntimeState.EXITED, RuntimeState.LOST):
            report.state_after = self.state
            report.backend_closed = True
            report.seconds = round(time.monotonic() - started, 3)
            return report
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

        failures: list[str] = []
        if report.terminate_result != "returned":
            failures.append(f"terminate={report.terminate_result}")
        if report.terminate_error:
            failures.append(f"terminate_error={report.terminate_error}")
        if report.tree_remaining_pids:
            failures.append(f"still-alive={list(report.tree_remaining_pids)}")

        if failures:
            # 清理失败：**不关闭后端句柄**、不移除记录，保留 owner 以便重试；
            # 绝不谎报 exited（reader 线程保持 daemon，等待下次重试收敛）。
            self._set_state(RuntimeState.CLEANUP_FAILED)
            report.owner_retained = True
            report.error = "; ".join(failures)
            report.state_after = self.state
            report.seconds = round(time.monotonic() - started, 3)
            return report

        # 正常收敛：等 drain 收尾（EOF 或超时）后再关闭句柄，避免 reader 与 close 并行。
        self.wait_eof(2.0)
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=2.0)
        report.reader_joined = not (self._reader is not None and self._reader.is_alive())
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
    terminal_id: str
    client_id: str
    generation: int
    role: str  # "control" | "observer"
    rows: int
    cols: int


class StaleLeaseError(RuntimeError):
    pass


class NotControlLeaseError(RuntimeError):
    pass


class AttachmentRegistry:
    """一个终端可有多个观察连接，但同一时刻只有一个输入控制权。

    尺寸跟随当前输入控制客户端；控制权转交后 generation 自增，
    旧客户端的输入/resize 一律拒绝，避免网络中迟到消息继续操作终端。
    """

    def __init__(self, registry: TerminalRegistry) -> None:
        self._registry = registry
        self._gen: dict[str, int] = {}
        self._control: dict[str, LeaseToken] = {}
        self._observers: dict[str, set[str]] = {}
        self._lock = threading.RLock()

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
            generation = self._gen.get(terminal_id, 0)
            if role == "control":
                generation += 1
                self._gen[terminal_id] = generation
            token = LeaseToken(terminal_id, client_id, generation, role, rows, cols)
            if role == "control":
                self._control[terminal_id] = token
            else:
                self._observers.setdefault(terminal_id, set()).add(client_id)
            return token

    def transfer_control(self, token: LeaseToken, *, to_client: str) -> LeaseToken:
        self._validate(token)
        if token.role != "control":
            raise NotControlLeaseError("observer lease cannot transfer control")
        return self.attach(token.terminal_id, to_client, role="control")

    def detach(self, token: LeaseToken) -> None:
        with self._lock:
            if token.role == "control" and self._control.get(token.terminal_id) == token:
                del self._control[token.terminal_id]
            else:
                self._observers.get(token.terminal_id, set()).discard(token.client_id)

    def validate(self, token: LeaseToken) -> None:
        self._validate(token)

    def _validate(self, token: LeaseToken) -> None:
        self._registry.get(token.terminal_id)
        current = self._gen.get(token.terminal_id, 0)
        if token.generation != current:
            raise StaleLeaseError(
                f"lease generation {token.generation} != current {current}"
            )

    def send(self, token: LeaseToken, data: bytes) -> int:
        self._validate(token)
        if token.role != "control":
            raise NotControlLeaseError("observer lease cannot write to the terminal")
        with self._lock:
            self._control.setdefault(token.terminal_id, token)
        record = self._registry.get(token.terminal_id)
        assert record.runtime is not None
        return record.runtime.write(data)

    def resize(self, token: LeaseToken, rows: int, cols: int) -> bool:
        self._validate(token)
        if token.role != "control":
            raise NotControlLeaseError("observer lease cannot resize the terminal")
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
# 8. 能力扩展点：默认 managed 与显式 detach（待其他 TA 证据）
# --------------------------------------------------------------------------


class OwnershipMode(str, Enum):
    SERVICE = "service"  # 默认：随 Pan 服务生死
    DETACHED = "detached"  # 显式 runtime detach：独立所有者，保留原 PTY/进程


class OwnershipPolicy(Protocol):
    """所有权策略扩展点。本阶段**只有接口**，没有实现。

    需要另外三条工作流的证据才能定型：
    - 生命周期 TA：同 PID 重连、父进程崩溃、Windows Job 限制；
    - CBC / Codex TA：无中断 TUI 是否可行、是否需要双通道；
    - MA：默认 managed 与显式 detach 的产品承诺边界。
    """

    mode: ClassVar[OwnershipMode]

    def on_service_shutdown(self, report: CleanupReport) -> CleanupReport: ...

    def reconnect_hint(self, terminal_id: str) -> dict[str, Any]: ...


class DetachedOwnershipNotImplemented(RuntimeError):
    pass


def require_detached_ownership(policy: Any | None) -> None:
    """显式失败的能力扩展点：未定型前不允许静默降级。"""
    raise DetachedOwnershipNotImplemented(
        "显式 runtime detach（保留原 PTY/进程的独立所有者）尚未定型："
        "需要 lifecycle TA 的同 PID 重连/父进程崩溃证据与 Windows Job Object 结论。"
        "默认 managed 路径不需要该策略。"
    )
