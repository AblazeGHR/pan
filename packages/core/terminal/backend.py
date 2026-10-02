"""ConPtyBackend：Windows 生产 PTY 后端（PtyBackend 协议实现，P1/TA-B）。

分层：

- :mod:`identity` 提供内核原语（三态探针 / 单句柄核验终止 / 取消原语）；
- :mod:`guard` 提供 Job 整树守卫；
- :mod:`spawn_win` 提供挂起式原子 spawn 与会话资源属主（``ConPtySpawn``）；
- 本模块把三者装配成 ``PtyBackend``：并发、取消、串行化、有界预算与
  **失败不丢 owned 资源、不谎报 closed** 的关闭协议。

关键语义（与 contracts.py / P0 接口文档 r2 对齐）：

- **read**：只返回**原始字节**；``EOFError`` **只**在真实对端断链时抛出
  （``ERROR_BROKEN_PIPE`` / 0 字节读——本机实测：子进程自然退出后输出管道
  *不会* EOF，EOF 只有 ClosePseudoConsole 之后才出现；进程退出 ≠ 通道 EOF）。
  ``alive() == False`` 不是读取结束条件；进程已退出且静默超过静默窗口时返回
  空字节（配合 runtime 的 ``eof_grace``），不冒充 EOF。
- **write**：partial 明确返回实际写入字节数；总预算有界（默认 5s），预算耗尽
  返回 partial；close 并发时经 ``CancelSynchronousIo`` 中止并抛
  ``BackendClosedError``；写入之间串行（``_input_lock``），与 close 的句柄
  释放互斥（注册表 + 关闭前收敛），避免句柄复用/释放竞态。
- **alive**：三态；``UNKNOWN`` **抛** ``BackendProbeError``，绝不返回 False
  冒充 dead。post-close 无证据时同样抛错；已确认 dead 则缓存为 False。
- **exit_code**：仅当保留句柄 signaled（真实退出）才提供，**包括真实退出码
  259**；未退出/未知一律 None。
- **terminate**：只经保留的 hProcess（``TerminateProcess``）；已退出 = no-op；
  整树归 ``guard.terminate_tree``。
- **close**：分阶段（stop/cancel/join 读线程 -> writer 收敛 -> 释放句柄 ->
  关闭自持 guard）；任一步失败**抛** ``BackendCloseError``（携带报告）并保留
  未释放资源，可重试；重复 close 幂等；释放顺序 = 官方建议（先输出管道，
  再 ClosePseudoConsole，再属性表，其余句柄，最后进程句柄）。
  **旧 build 边界**：<26100 的 ClosePseudoConsole 可能阻塞，本实现用有界
  worker + 超时保留 HPCON 可重试；已实测机器（build 26200）立即返回，
  旧 build 未真机验证。

**r3 对齐（核心窄修 b5017d1d；本后端不改 core、直接满足其契约）**：

- **句柄级并发契约（``PtyBackend`` r3）**：read/write/resize/terminate/close 的
  同句柄并发由本后端内部串行化——read 走 ``_cv``（等待期间不持锁、不占全局锁）；
  write 走有界 ``_input_lock`` + writer 注册表；resize/释放走 ``_op_lock``；
  close 与在途 write/terminate 通过“关门（``_closing``）+ 收敛（CancelSynchronousIo
  + 注册表空/输入锁可得）”串行，**先收敛后释放**，杜绝句柄复用/释放竞态。
  `read` 阻塞**不会**阻止 write/terminate/取消：三者都不需要 ``_cv``。
- **已接纳输入有界收敛（runtime r3 输入门）**：write 的总预算（默认 1.5s <
  ``DEFAULT_INPUT_DRAIN_TIMEOUT``=2.0s）由**预算计时器**强制——到期即对写线程
  发起 `CancelSynchronousIo`，即使单次 WriteFile 被管道堵死也**按期返回 partial**；
  串行锁同样有界获取（不可得 -> ``BackendBusyError``）。因此 close 的
  interrupt 阶段（直接调 ``backend.write``）与“已接纳输入等待”都不会被无界
  WriteFile 卡死。
- **DEAD 证据绑定（``ProcessProbe`` r3 §13.4）**：``backend.probe(pid)`` 只对
  本终端根 PID 用 spawn 时**同一 retained handle** 判定；非本终端 PID 一律
  ``UNKNOWN``（不现查陌生 PID）。``identity.probe_process`` 也只给
  ALIVE/UNKNOWN（fresh-handle signaled 不作为 DEAD 放行证据）。
- **guard 自界（``TreeGuard`` r3）**：JobObjectGuard 的每个操作都是单次内核
  查询或受 ``timeout`` 约束的有界轮询（上限钳制 30s），无阻塞等待；runtime 侧
  的 ``_BoundedCall`` 只是外层兜底。

边界（不得宣称）：

- detach 宿主可行性**不在本模块**（无 DetachHandler 实现；见计划 §5.1）；
- 未做长时间压力与跨 build 验证；ambient Job 的 KILL_ON_JOB_CLOSE 边界
  见 spawn 报告 §7（未覆盖）。
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Mapping, Sequence

from .contracts import (
    ProcessIdentity,
    ProcessOwnershipEvidence,
    ProcessProbe,
    ProcessStatus,
)
from .guard import JobObjectGuard
from .identity import (
    ERROR_BROKEN_PIPE,
    ERROR_OPERATION_ABORTED,
    WindowsApiUnavailable,
    cancel_synchronous_io,
    close_handle_checked,
    informational_exit_code,
    open_current_thread_handle,
    probe_handle,
    wait_state,
)
from .ownership import UnverifiedOwnershipGate
from .spawn_win import (
    ConPtySpawn,
    SpawnDenied,
    SpawnEvidence,
    spawn_conpty_suspended,
)

__all__ = [
    "ConPtyBackend",
    "ConPtyError",
    "BackendClosedError",
    "BackendProbeError",
    "BackendBusyError",
    "BackendPumpError",
    "BackendCloseError",
    "WinptyBackend",
    "DEFAULT_PUMP_BUFFER_BYTES",
    "DEFAULT_WRITE_BUDGET_SECONDS",
    "DEFAULT_INTERRUPT_WRITE_BUDGET_SECONDS",
]

#: pump 内部缓冲上限：超出则 pump 暂停读（有界、不丢弃、不泄洪）。
DEFAULT_PUMP_BUFFER_BYTES = 4 * 1024 * 1024
#: 单次 read() 默认拉取上限。
DEFAULT_PUMP_READ_SIZE = 64 * 1024
#: write() 的默认总预算（秒）：**含单次被堵塞的 WriteFile**（预算计时器到期时
#: 对写线程发起 ``CancelSynchronousIo`` 并返回 partial）。取 1.5s < runtime 的
#: ``DEFAULT_INPUT_DRAIN_TIMEOUT``（2.0s，r3 输入门），让“已接纳输入有界收敛”
#: 在正常路径上能于 runtime 的等待窗口内清空；超时仍保 owner（fail-closed）。
DEFAULT_WRITE_BUDGET_SECONDS = 1.5
#: interrupt（``terminate(force=False)`` 写 ``\\x03``）的预算：runtime.close 的
#: interrupt 阶段直接调用 backend.write，必须自身有界，不得被无界 WriteFile 卡死。
DEFAULT_INTERRUPT_WRITE_BUDGET_SECONDS = 0.5
#: 单次 WriteFile 分块上限。
DEFAULT_WRITE_CHUNK_BYTES = 64 * 1024
#: 进程已退出且静默该窗口后，read() 返回空字节（不给 runtime 永久阻塞）。
DEFAULT_QUIET_WINDOW_SECONDS = 0.15
#: 释放失败重试的默认收敛预算。
DEFAULT_CLOSE_WAIT_SECONDS = 3.0
DEFAULT_PTY_CLOSE_TIMEOUT_SECONDS = 2.0
#: terminate 后确认对象 signaled 的有界等待（本机实测：ConPTY 客户端在控制台
#: 阻塞时，TerminateProcess 到 signaled 存在 ~0.5s 级延迟；不等待会让调用方的
#: “根确认退出”步骤立即 fail-closed）。
DEFAULT_TERMINATE_CONFIRM_SECONDS = 1.0

ERROR_NO_DATA = 232
ERROR_HANDLE_EOF = 38
TERMINATE_EXIT_CODE = 0xDEAD


def _win_err(exc: BaseException) -> int | None:
    """从 OSError 取 Win32 错误码（winerror 优先，回退 errno）。"""
    err = getattr(exc, "winerror", None)
    if err is None:
        err = getattr(exc, "errno", None)
    return int(err) if isinstance(err, int) else None


class ConPtyError(RuntimeError):
    """ConPtyBackend 错误基类。"""


class BackendClosedError(ConPtyError):
    """backend 已关闭或正在关闭：拒绝新操作（不假成功）。

    ``written`` 属性记录中止前已接受的输入字节数（可能为 0）。
    """

    def __init__(self, message: str, *, written: int | None = None) -> None:
        super().__init__(message)
        self.written = written


class BackendProbeError(ConPtyError):
    """存活/身份探针无结论：调用方必须 fail-closed，不得据此认为已死。"""


class BackendBusyError(ConPtyError):
    """输入串行锁在有界预算内不可得（另有在途写入未收敛）：拒绝排队等待。"""


class BackendPumpError(ConPtyError):
    """pump 线程级失败或非 EOF 结束：读通道不可用（脱敏诊断，不冒充 EOF）。"""


class BackendCloseError(ConPtyError):
    """close() 失败：未释放的资源保留在 ``report["retained"]`` 中，可重试。"""

    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report

    @property
    def retryable(self) -> bool:
        return bool(self.report.get("retryable", True))


class ConPtyBackend:
    """Windows 生产后端（纯 ctypes；非 Windows 构造/创建时明确拒绝）。

    一个后端对象 = 一次原子 spawn 的会话：自有 pump 线程持续读输出（有界
    缓冲），read() 从缓冲消费；write/resize 与 close 串行化；清理经
    :meth:`close`，失败保留 owner 可重试。

    并发契约（r3）：同一批句柄上的 read/write/resize/terminate/close 由本对象
    内部串行化；read 阻塞不持全局锁（write/terminate/probe/取消均不依赖它）。
    runtime 只负责“关门后不再发起新输入 + 有界等待已接纳输入收敛”。
    """

    def __init__(
        self,
        spawn: ConPtySpawn,
        *,
        guard: JobObjectGuard | None,
        owns_guard: bool,
        buffer_cap: int = DEFAULT_PUMP_BUFFER_BYTES,
        read_size: int = DEFAULT_PUMP_READ_SIZE,
        write_budget: float = DEFAULT_WRITE_BUDGET_SECONDS,
        write_chunk: int = DEFAULT_WRITE_CHUNK_BYTES,
        quiet_window: float = DEFAULT_QUIET_WINDOW_SECONDS,
        terminate_wait: float = DEFAULT_TERMINATE_CONFIRM_SECONDS,
        start_pump: bool = True,
    ) -> None:
        self._spawn = spawn
        self._guard = guard
        self._owns_guard = bool(owns_guard)
        self._buffer_cap = max(1, int(buffer_cap))
        self._read_size = max(1, int(read_size))
        self._write_budget = max(0.0, float(write_budget))
        self._write_chunk = max(1, int(write_chunk))
        self._quiet_window = max(0.0, float(quiet_window))
        self._terminate_wait = max(0.0, float(terminate_wait))

        self._cv = threading.Condition()
        self._buf = bytearray()
        self._last_data_at = time.monotonic()
        self._drain_eof = False
        self._drain_error: Exception | None = None
        self._drain_cancelled = False
        self._drain_done = False
        # R6/R5：pump 线程级诊断（脱敏 = 只记类型名；显式退出原因）。
        self._pump_fatal: str | None = None
        self._pump_exit: str = "not-started"

        self._lifecycle_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._input_lock = threading.Lock()
        self._op_lock = threading.Lock()
        self._closing = False
        self._closed = False
        self._dead_proven = False
        self._exit_code: int | None = None
        self._exit_code_known = False
        #: 最近一次 terminate(force=True) 是否在有界预算内确认 signaled。
        self.last_terminate_confirmed: bool | None = None

        self._writer_threads: dict[int, int] = {}
        self._orphan_handles: list[int] = []

        self._reader_thread: threading.Thread | None = None
        self._reader_handle = 0
        self._reader_stop = threading.Event()
        if start_pump:
            self._start_pump()

    # ------------------------------------------------------------------ spawn
    @classmethod
    def spawn(
        cls,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        rows: int = 24,
        cols: int = 80,
        guard: JobObjectGuard | None = None,
        resume: bool = True,
        buffer_cap: int = DEFAULT_PUMP_BUFFER_BYTES,
        read_size: int = DEFAULT_PUMP_READ_SIZE,
        write_budget: float = DEFAULT_WRITE_BUDGET_SECONDS,
        write_chunk: int = DEFAULT_WRITE_CHUNK_BYTES,
        quiet_window: float = DEFAULT_QUIET_WINDOW_SECONDS,
    ) -> "ConPtyBackend":
        """原子 spawn 一个 ConPTY 会话；非 Windows 明确 ``BackendUnavailableError``。

        任何门禁失败 -> 抛 :class:`SpawnDenied`（fail-closed：不返回 backend，
        不发布 running）；清理失败保留在 ``SpawnDenied.cleanup["retained"]``，
        可用 ``exc.retry_cleanup()`` 重试。
        """
        if sys.platform != "win32":
            raise WindowsApiUnavailable(
                f"ConPtyBackend 仅支持 Windows（sys.platform={sys.platform!r}）："
                "非 Windows 上创建终端会得到明确的 BackendUnavailableError"
            )
        owns_guard = guard is None
        guarded = guard if guard is not None else JobObjectGuard()
        try:
            attempt = spawn_conpty_suspended(
                argv,
                cwd=cwd,
                env=env,
                rows=rows,
                cols=cols,
                guard=guarded,
                resume=resume,
            )
        except SpawnDenied as denied:
            if owns_guard:
                denied.detail.setdefault("owns_guard_closed", guarded.close())
            raise
        return cls(
            attempt,
            guard=guarded,
            owns_guard=owns_guard,
            buffer_cap=buffer_cap,
            read_size=read_size,
            write_budget=write_budget,
            write_chunk=write_chunk,
            quiet_window=quiet_window,
        )

    # ------------------------------------------------------------------ 只读属性
    @property
    def pid(self) -> int | None:
        return self._spawn.pid or None

    @property
    def identity(self) -> ProcessIdentity | None:
        return self._spawn.identity

    @property
    def guard(self) -> JobObjectGuard | None:
        return self._guard

    @property
    def spawn_evidence(self) -> SpawnEvidence | None:
        return self._spawn.evidence

    @property
    def ownership_evidence(self) -> ProcessOwnershipEvidence | None:
        ev = self._spawn.evidence
        return ev.ownership_evidence() if ev is not None else None

    @property
    def gate(self) -> UnverifiedOwnershipGate | None:
        """四证据启动门禁（真实原语产生；装配 build_runtime 时传给 start()）。"""
        ev = self.ownership_evidence
        return UnverifiedOwnershipGate(ev) if ev is not None else None

    @property
    def closed(self) -> bool:
        with self._lifecycle_lock:
            return self._closed

    # ------------------------------------------------------------------ pump
    def _start_pump(self) -> None:
        thread = threading.Thread(
            target=self._pump_loop, name=f"conpty-pump-{self.pid}", daemon=True
        )
        self._reader_thread = thread
        thread.start()

    def _pump_loop(self) -> None:
        """持续读输出管道到有界缓冲（不丢弃、消费者慢时暂停读 = 背压）。

        R6：**任何**线程级失败（含 ``open_current_thread_handle`` 抛错）都必须被
        捕获、脱敏记录（类型名）并置 ``_drain_done``，绝不静默死线程——否则
        ``read``/``close`` 会永远等一个已死的 pump。
        R5：退出原因显式落 ``_pump_exit``（eof/cancelled/error:<Type>/fatal:<Type>/
        stopped-requested），供 close 报告与 ``describe()`` 区分。
        """
        handle = 0
        try:
            handle = open_current_thread_handle()
            with self._lifecycle_lock:
                self._reader_handle = handle
            while not self._reader_stop.is_set():
                try:
                    data = self._spawn.read_raw(self._read_size)
                except OSError as exc:
                    err = _win_err(exc)
                    with self._cv:
                        if err == ERROR_OPERATION_ABORTED:
                            self._drain_cancelled = True
                        elif err in (
                            ERROR_BROKEN_PIPE,
                            ERROR_NO_DATA,
                            ERROR_HANDLE_EOF,
                        ):
                            # 真实对端断链（本机实测：仅 ClosePseudoConsole 后出现）
                            self._drain_eof = True
                        else:
                            self._drain_error = exc
                        self._cv.notify_all()
                    break
                if not data:
                    with self._cv:
                        self._drain_eof = True  # 0 字节读 = 断链
                        self._cv.notify_all()
                    break
                with self._cv:
                    self._buf.extend(data)
                    self._last_data_at = time.monotonic()
                    self._cv.notify_all()
                    while (
                        len(self._buf) > self._buffer_cap
                        and not self._reader_stop.is_set()
                        and not self._closed
                    ):
                        self._cv.wait(0.1)
                    if self._reader_stop.is_set():
                        break
        except Exception as exc:  # noqa: BLE001 - 线程级失败必须可见（R6）
            with self._cv:
                self._pump_fatal = type(exc).__name__
                if self._drain_error is None:
                    self._drain_error = BackendPumpError(
                        f"ConPTY pump 线程级失败（{type(exc).__name__}）：读通道不可用"
                    )
                self._cv.notify_all()
        finally:
            with self._cv:
                if self._pump_exit == "not-started":
                    if self._pump_fatal:
                        self._pump_exit = f"fatal:{self._pump_fatal}"
                    elif self._drain_eof:
                        self._pump_exit = "eof"
                    elif self._drain_cancelled:
                        self._pump_exit = "cancelled"
                    elif self._drain_error is not None:
                        self._pump_exit = f"error:{type(self._drain_error).__name__}"
                    elif self._reader_stop.is_set():
                        self._pump_exit = "stopped-requested"
                    else:
                        self._pump_exit = "exited"
                self._drain_done = True
                self._cv.notify_all()

    def _started_reader(self) -> threading.Thread | None:  # pragma: no cover
        return self._reader_thread

    # ------------------------------------------------------------------ read
    def read(self, size: int, *, timeout: float | None = None) -> bytes:
        """读取输出原始字节（默认阻塞；可选有界等待）。

        - 有数据：返回至多 ``size`` 字节（不足不等待）；
        - 真实对端断链：抛 ``EOFError``（**只**认断链/0 字节读，不认 alive=false）；
        - 进程已退出且静默超过静默窗口：返回 ``b""``（交给 runtime 的 eof_grace，
          不冒充 EOF）；
        - ``timeout`` 给定且预算内无数据/无事件：返回 ``b""``（“此刻没有数据”，
          与 runtime 的空读语义一致；不冒充 EOF）；
        - backend 关闭中/已关闭：抛 :class:`BackendClosedError`（不冒充 EOF）。
        """
        if int(size) <= 0:
            raise ValueError("read size 必须 > 0")
        size = int(size)
        deadline = (
            None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        )
        with self._cv:
            while True:
                if self._buf:
                    take = min(size, len(self._buf))
                    data = bytes(self._buf[:take])
                    del self._buf[:take]
                    return data
                if self._drain_eof:
                    raise EOFError(
                        "ConPTY 输出通道 EOF（对端断链：管道关闭/0 字节读）"
                    )
                if self._drain_error is not None:
                    raise self._drain_error
                with self._lifecycle_lock:
                    closed, closing = self._closed, self._closing
                if closed:
                    raise BackendClosedError("backend 已关闭：read 拒绝")
                if closing:
                    raise BackendClosedError("backend 正在关闭：read 被取消")
                if self._drain_done:
                    # pump 已结束但没有 eof/error 标志（fatal / stop）：不得静默等死线程
                    raise BackendPumpError(
                        f"ConPTY pump 已终止（{self._pump_exit}）：读通道不可用（非 EOF）"
                    )
                if self._process_dead_and_quiet_locked():
                    return b""
                wait_for = 0.05
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return b""  # 有界等待耗尽：如实返回“无数据”
                    wait_for = min(wait_for, remaining)
                self._cv.wait(wait_for)

    def _process_dead_and_quiet_locked(self) -> bool:
        """调用须持有 ``_cv``；进程已确认退出且静默窗口已过 -> True。"""
        with self._lifecycle_lock:
            dead = self._dead_proven or self._spawn.death_confirmed
        if not dead:
            handle = self._spawn.h_process
            if not handle:
                return False
            state = wait_state(handle)
            if state is ProcessStatus.DEAD:
                with self._lifecycle_lock:
                    self._dead_proven = True
                dead = True
            elif state is ProcessStatus.UNKNOWN:
                return False
        if not dead:
            return False
        return (time.monotonic() - self._last_data_at) >= self._quiet_window

    # ------------------------------------------------------------------ write
    def write(self, data: bytes, *, timeout: float | None = None) -> int:
        """写入输入；返回**实际写入**字节数（partial 明确返回）。

        - 串行（r3 句柄级并发契约）：同一 backend 的写互相串行；与 close 的
          句柄释放通过注册表 + 收敛协议互斥，杜绝句柄复用/释放竞态；
        - **总预算有界（默认 1.5s，含单次被堵塞的 WriteFile）**：预算计时器到期
          时对**本线程**发起 ``CancelSynchronousIo``，中止进行中的同步写，返回
          partial（不抛）——不会让一次无界 WriteFile 把 close/取消拖死；
        - 串行锁也有界获取：另一个写者堵塞时不把本调用（含 interrupt 的
          ``\\x03``）无限拖住，锁不可得 -> ``BackendBusyError``；
        - close 并发：close 的取消优先，抛 ``BackendClosedError``（携带部分写入
          计数）；管道断链 -> ``OSError``。

        诚实边界：预算计时器按“本调用”计时；若取消恰好落在两次 WriteFile 之间，
        下一次 WriteFile（同一调用内）不会再发起（``timer_fired`` 检查），write
        直接返回 partial。极窄的延迟窗口内取消若落空，本调用仍在预算内返回。
        """
        payload = bytes(data)
        if not payload:
            return 0
        budget = self._write_budget if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + budget
        # 有界获取输入串行锁（r3：句柄级并发安全 + 输入不拖 close）。
        if not self._input_lock.acquire(timeout=max(0.05, budget)):
            raise BackendBusyError(
                "input 串行锁在预算内不可得：另有在途写入未收敛（不无限等待）"
            )
        try:
            with self._lifecycle_lock:
                if self._closed or self._closing:
                    raise BackendClosedError("backend 已关闭/正在关闭：write 拒绝")
                if not self._spawn.input_write:
                    raise BackendClosedError("input_write 句柄已释放：write 拒绝")
            thread_handle = open_current_thread_handle()
            tid = threading.get_ident()
            with self._io_lock:
                self._writer_threads[tid] = thread_handle
            budget_state: dict[str, Any] = {
                "lock": threading.Lock(),
                "finished": False,
                "timer_fired": False,
                "cancel_attempts": 0,
            }

            def _budget_fired() -> bool:
                with budget_state["lock"]:
                    return bool(budget_state["timer_fired"])

            def _on_budget_timeout() -> None:
                # 预算到期强制点：**重试**取消本线程上可能仍在阻塞/待发的同步
                # WriteFile，直到本次写调用结束——覆盖“timer_fired 检查后、
                # WriteFile 之前”的窗口：一次取消可能落空，不能只发一次（R4）。
                cap = time.monotonic() + max(5.0, budget * 4.0)
                while True:
                    with budget_state["lock"]:
                        if budget_state["finished"]:
                            return
                        budget_state["timer_fired"] = True
                        budget_state["cancel_attempts"] += 1
                    cancel_synchronous_io(thread_handle)
                    with budget_state["lock"]:
                        if budget_state["finished"]:
                            return
                    if time.monotonic() >= cap:
                        return  # 极端兜底；close 的 _converge_writers 仍会取消注册句柄
                    time.sleep(0.02)

            remaining_after_lock = max(0.0, deadline - time.monotonic())
            timer: threading.Timer | None = None
            if remaining_after_lock > 0:
                # 预算**含锁等待**：计时器只拿剩余时间（R4）。
                timer = threading.Timer(remaining_after_lock, _on_budget_timeout)
                timer.daemon = True
                timer.start()
            total = 0
            try:
                try:
                    while total < len(payload):
                        if time.monotonic() >= deadline or _budget_fired():
                            break  # 有界预算：返回 partial
                        with self._lifecycle_lock:
                            if self._closing:
                                raise BackendClosedError(
                                    "write 被 close 取消（CancelSynchronousIo 收敛）",
                                    written=total,
                                )
                        chunk = payload[total : total + self._write_chunk]
                        try:
                            written = self._spawn.write_raw(chunk)
                        except OSError as exc:
                            err = _win_err(exc)
                            if err == ERROR_OPERATION_ABORTED:
                                with self._lifecycle_lock:
                                    closing = self._closing
                                if closing:
                                    raise BackendClosedError(
                                        "write 被 close 中止（ERROR_OPERATION_ABORTED）",
                                        written=total,
                                    ) from exc
                                if _budget_fired():
                                    break  # 预算自取消：返回 partial（诚实语义）
                                raise BackendClosedError(
                                    "write 被取消（非预算路径的 ERROR_OPERATION_ABORTED）",
                                    written=total,
                                ) from exc
                            raise
                        if written <= 0:
                            # 无进展：等待预算；不忙等、不谎报
                            if time.monotonic() >= deadline:
                                break
                            time.sleep(0.005)
                            continue
                        total += written
                finally:
                    # 先让取消者收敛（finished + join），句柄才可能安全关闭（R4：
                    # 不 join 就可能在关闭线程句柄后仍被回调使用 -> 陈旧句柄）。
                    with budget_state["lock"]:
                        budget_state["finished"] = True
                    if timer is not None:
                        timer.cancel()
                        timer.join(1.0)
            finally:
                with self._io_lock:
                    self._writer_threads.pop(tid, None)
                if timer is not None and timer.is_alive():
                    # 取消者未收敛：句柄保活，交由 close 重试（不得当作已回收）
                    self._orphan_handles.append(thread_handle)
                elif not close_handle_checked(thread_handle):
                    self._orphan_handles.append(thread_handle)
            return total
        finally:
            self._input_lock.release()

    # ------------------------------------------------------------------ resize
    def resize(self, rows: int, cols: int) -> None:
        """``ResizePseudoConsole`` 到实际子尺寸（与 close 的句柄释放互斥）。"""
        with self._op_lock:
            with self._lifecycle_lock:
                if self._closed or self._closing:
                    raise BackendClosedError("backend 已关闭/正在关闭：resize 拒绝")
            self._spawn.resize_raw(int(rows), int(cols))

    # ------------------------------------------------------------------ liveness
    def _liveness(self) -> ProcessStatus:
        with self._lifecycle_lock:
            if self._dead_proven or self._spawn.death_confirmed:
                return ProcessStatus.DEAD
            if self._closed or not self._spawn.h_process:
                return ProcessStatus.UNKNOWN
        handle = self._spawn.h_process
        state = wait_state(handle)
        if state is ProcessStatus.DEAD:
            with self._lifecycle_lock:
                self._dead_proven = True
        return state

    def alive(self) -> bool:
        """三态存活：``True``/``False`` 均有真实证据；``UNKNOWN`` 抛错。

        - retained handle signaled -> False（已退出，缓存）；
        - WAIT_TIMEOUT -> True；
        - 未知（句柄已释放且无死亡证据 / WAIT_FAILED）-> :class:`BackendProbeError`
          （**不返回 False 冒充 dead**）。
        """
        state = self._liveness()
        if state is ProcessStatus.ALIVE:
            return True
        if state is ProcessStatus.DEAD:
            return False
        raise BackendProbeError(
            "进程存活状态未知（句柄不可用或 WaitForSingleObject 失败）："
            "fail-closed，不返回 False 冒充 dead"
        )

    def exit_code(self) -> int | None:
        """退出码：**仅**在保留句柄已 signaled 时提供（含真实退出码 259）。

        未退出 / 状态未知 / 句柄已释放且无 cached 事实 -> ``None``（不得用
        GetExitCodeProcess 的 259 判活或伪造已退出）。
        """
        with self._lifecycle_lock:
            if self._exit_code_known:
                return self._exit_code
            if self._closed or not self._spawn.h_process:
                # 句柄已释放：退出码已不可读（即使已确认 dead 也不猜测）。
                return None
        handle = self._spawn.h_process
        if wait_state(handle) is not ProcessStatus.DEAD:
            return None
        code = informational_exit_code(handle)
        with self._lifecycle_lock:
            self._dead_proven = True
            self._exit_code = code
            self._exit_code_known = True
        return code

    def probe(self, pid: int) -> ProcessProbe:
        """``IdentityProbe``（**r3 §13.4 绑定版**）：DEAD 只来自 spawn 时同一 retained handle。

        - ``pid`` 非本终端根 PID -> ``UNKNOWN``（不现查陌生 PID，PID 复用不得冒充）；
        - 本终端根 PID -> retained handle 三态；句柄已释放但有 retained/Job 层面
          的死亡证据（释放前已确认 signaled）-> ``DEAD``；否则 ``UNKNOWN``。
        """
        if self.pid is None or int(pid) != int(self.pid):
            return ProcessProbe(
                ProcessStatus.UNKNOWN,
                None,
                "非本终端根 PID：不查陌生 PID（r3）；DEAD 证据必须绑定 spawn 时 retained handle/Job",
            )
        return self.probe_retained()

    def probe_retained(self) -> ProcessProbe:
        """retained-handle 三态探针（供 runtime/诊断；不重开 PID）。"""
        handle = self._spawn.h_process
        if not handle:
            with self._lifecycle_lock:
                dead = self._dead_proven
            if dead or self._spawn.death_confirmed:
                return ProcessProbe(
                    ProcessStatus.DEAD,
                    self.identity,
                    "retained/Job 证据：释放前已确认 signaled（不依赖现查 PID）",
                )
            return ProcessProbe(
                ProcessStatus.UNKNOWN, None, "进程句柄已释放且无死亡证据：unknown"
            )
        probe = probe_handle(handle, pid=self.pid)
        if probe.status is ProcessStatus.DEAD:
            with self._lifecycle_lock:
                self._dead_proven = True
        return probe

    def wait_dead(self, timeout: float) -> bool:
        """在有界预算内等待退出（供测试/宿主使用）；不改变任何状态。"""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            state = self._liveness()
            if state is ProcessStatus.DEAD:
                return True
            if state is ProcessStatus.UNKNOWN or time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    # ------------------------------------------------------------------ terminate
    def terminate(self, force: bool) -> None:
        """请求终止根进程（只经保留 hProcess；整树归 guard）。

        - ``force=True``：``TerminateProcess``；已 signaled = no-op；调用失败
          但重新核验已 dead 也视为达成；否则抛 ``OSError``；成功后在
          **有界预算**（默认 1.0s）内等待对象 signaled——本机实测 ConPTY
          客户端在控制台阻塞时存在 ~0.5s 级延迟，若不确认，调用方的“根确认
          退出”会立即 fail-closed。预算耗尽不抛错但如实记录
          ``last_terminate_confirmed=False``（terminate 仍是**请求**语义，
          最终确认由调用方的存活探针负责）；
        - ``force=False``：best-effort 写 ``\\x03``（Ctrl-C）请求优雅退出；
          **不承诺 OS 级 CTRL_C_EVENT**：build 26200 有界定位（raw 0x03 /
          helper attach+``GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)`` /
          helper attach+``WriteConsoleInput(CONIN$)`` 键记录，含读取中与计算中两种
          子进程形态）四候选均不可投递，仅表现为“阻塞中的控制台读被释放”——
          R1 保持未验收，机制/证据见实现报告 §2.6.1。
        """
        with self._lifecycle_lock:
            if self._closed:
                raise BackendClosedError("backend 已关闭：terminate 拒绝")
        if not force:
            # interrupt 路径（runtime.close 直接调用）：短预算 + 有界锁获取，
            # 自身有界，绝不阻塞 close 的 interrupt 阶段。
            try:
                self.write(b"\x03", timeout=DEFAULT_INTERRUPT_WRITE_BUDGET_SECONDS)
            except (ConPtyError, OSError):
                pass
            return
        state = self._liveness()
        if state is ProcessStatus.DEAD:
            self.last_terminate_confirmed = True
            return
        if state is ProcessStatus.UNKNOWN:
            raise BackendProbeError(
                "terminate 前无法确认存活状态（unknown）：拒绝盲杀（fail-closed）"
            )
        ok = self._spawn.terminate_raw(TERMINATE_EXIT_CODE)
        if not ok:
            # 竞争：调用失败但进程可能已退出 -> 复核（真实证据），仍活则报错。
            if self._liveness() is ProcessStatus.DEAD:
                self.last_terminate_confirmed = True
                return
            raise OSError(
                f"TerminateProcess 失败（{self._spawn.terminate_error}）"
            )
        deadline = time.monotonic() + self._terminate_wait
        while time.monotonic() < deadline:
            if self._liveness() is ProcessStatus.DEAD:
                self.last_terminate_confirmed = True
                return
            time.sleep(0.02)
        self.last_terminate_confirmed = False

    # ------------------------------------------------------------------ close
    def close(
        self,
        *,
        reader_wait_timeout: float = DEFAULT_CLOSE_WAIT_SECONDS,
        writer_wait_timeout: float = DEFAULT_CLOSE_WAIT_SECONDS,
        pty_close_timeout: float = DEFAULT_PTY_CLOSE_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """分阶段关闭（失败抛 :class:`BackendCloseError` 并保留 owned 资源）。

        0. **死亡/所有权前置门禁（R3）**：先核验进程已退出（retained/Job 证据）；
           活/unknown 一律拒绝，且**不停止 pump、不关输入门、不取消读、不释放任何
           句柄**——不破坏仍存活的终端（拒绝后可继续 read/write/resize）；
        1. 读者收敛：置 stop -> ``CancelSynchronousIo`` 重试 -> join 线程；
        2. 写者收敛：取消注册的 writer 线程 -> **且确实取得** ``_input_lock``
           证明无写者（持有锁未注册的写者同样必须收敛，R4）；
        3. 释放资源（官方顺序）：output_read -> ClosePseudoConsole（有界 worker）
           -> 属性表 -> input_write -> 其余管道 -> 线程句柄 -> h_process；
        4. 若 guard 归 backend 自持：最后关闭 Job 句柄（kill-on-close 兜底）。

        重复 close 幂等。runtime 正常路径先 terminate 再 close（语义不变）。
        """
        started = time.perf_counter()
        with self._close_lock:
            if self._closed:
                report = {
                    "closed": True,
                    "already_closed": True,
                    "seconds": 0.0,
                }
                return report
            # 0) 先核验、后破坏 IO（R3）：活/unknown -> 拒绝且不触碰任何 IO 状态。
            state = self._liveness()
            if state is not ProcessStatus.DEAD:
                raise BackendCloseError(
                    f"close refused: process not confirmed dead (state={state.value})："
                    "先终止整树并确认退出；拒绝在存活终端上停止 pump/关输入门/释放句柄",
                    {
                        "retryable": True,
                        "retained": self._retained_inventory(),
                        "process_state": state.value,
                        "io_untouched": True,
                    },
                )
            with self._lifecycle_lock:
                self._closing = True
            with self._cv:
                self._cv.notify_all()

            # 1) 读者收敛（stop 先行；CancelSynchronousIo 有界重试 + join）
            converged, reader_detail = self._converge_reader(reader_wait_timeout)
            if not converged:
                raise BackendCloseError(
                    "reader_not_converged：读线程未在有界预算内退出（owner 保留，可重试）",
                    {
                        "retryable": True,
                        "retained": self._retained_inventory(),
                        "reader": reader_detail,
                    },
                )
            # 2) 写者收敛（取消注册 writer + 取得输入锁证明无写者）
            writers_ok, writer_detail = self._converge_writers(writer_wait_timeout)
            if not writers_ok:
                raise BackendCloseError(
                    "writer_not_converged：输入写者未收敛（owner 保留，可重试）",
                    {
                        "retryable": True,
                        "retained": self._retained_inventory(),
                        "reader": reader_detail,
                        "writers": writer_detail,
                    },
                )

            # 3) 释放句柄（与 resize 的句柄使用互斥）
            release: dict[str, Any]
            with self._op_lock:
                release = self._spawn.staged_release(
                    require_dead=True, pty_close_timeout=pty_close_timeout
                )
            if not release.get("closed"):
                raise BackendCloseError(
                    "release_failed：资源未全部释放（保留可重试；不谎报 closed）",
                    {
                        "retryable": True,
                        "retained": release.get("retained", []),
                        "release": release,
                        "reader": reader_detail,
                        "writers": writer_detail,
                    },
                )

            # 4) 自持 guard 的 Job 句柄（kill-on-close 兜底最后关闭）
            if self._owns_guard and self._guard is not None:
                if not self._guard.close():
                    raise BackendCloseError(
                        "guard_close_failed：Job 句柄关闭失败（保留可重试）",
                        {
                            "retryable": True,
                            "retained": ["job_handle"],
                            "release": release,
                        },
                    )
            orphans = list(self._orphan_handles)
            leftover_orphans: list[int] = []
            for handle in orphans:
                if not close_handle_checked(handle):
                    leftover_orphans.append(handle)
            self._orphan_handles = leftover_orphans
            if leftover_orphans:
                raise BackendCloseError(
                    "orphan_handles_not_closed：临时线程句柄仍未能释放（保留可重试）",
                    {"retryable": True, "retained": [f"handle:{h}" for h in leftover_orphans]},
                )
            with self._lifecycle_lock:
                self._closed = True
            return {
                "closed": True,
                "already_closed": False,
                "reader": reader_detail,
                "writers": writer_detail,
                "release": release,
                "seconds": round(time.perf_counter() - started, 4),
            }

    def _pump_diagnostics(self) -> dict[str, Any]:
        """R5：显式 reader/pump 诊断（eof/cancelled/error/fatal/stop 可区分）。"""
        return {
            "stop_requested": self._reader_stop.is_set(),
            "pump_exit": self._pump_exit,
            "pump_fatal": self._pump_fatal,
            "drain_done": self._drain_done,
            "drain_eof": self._drain_eof,
            "drain_cancelled": self._drain_cancelled,
            "drain_error_type": None
            if self._drain_error is None
            else type(self._drain_error).__name__,
        }

    def _converge_reader(self, timeout: float) -> tuple[bool, dict[str, Any]]:
        thread = self._reader_thread
        if thread is None:
            return True, {"reader": "not-started", "thread_exited": True, **self._pump_diagnostics()}
        if not thread.is_alive():
            return True, {"reader": "already-exited", "thread_exited": True, **self._pump_diagnostics()}
        self._reader_stop.set()
        with self._cv:
            self._cv.notify_all()
        deadline = time.monotonic() + max(0.0, float(timeout))
        attempts = 0
        last_cancel: tuple[bool, int] | None = None
        while True:
            if not thread.is_alive():
                break
            with self._lifecycle_lock:
                handle = self._reader_handle
            if handle:
                last_cancel = cancel_synchronous_io(handle)
                attempts += 1
            if time.monotonic() >= deadline:
                break
            thread.join(min(0.02, max(0.0, deadline - time.monotonic())))
        thread.join(max(0.0, deadline - time.monotonic()))
        converged = not thread.is_alive()
        detail = {
            "cancel_attempts": attempts,
            "cancel_last": {"ok": last_cancel[0], "err": last_cancel[1]}
            if last_cancel
            else None,
            "thread_exited": converged,
            # R5：显式 reader/pump 诊断（eof/cancelled/error/fatal/stop 可区分）
            **self._pump_diagnostics(),
        }
        if converged:
            # 关闭读线程句柄（注册期间不再被使用；失败保留可重试）
            with self._lifecycle_lock:
                handle = self._reader_handle
                if handle:
                    if close_handle_checked(handle):
                        self._reader_handle = 0
                    else:
                        detail["reader_handle_close_failed"] = True
        return converged, detail

    def _converge_writers(self, timeout: float) -> tuple[bool, dict[str, Any]]:
        """写者收敛（R4）：**注册表为空 且 确实取得 `_input_lock`** 才算收敛。

        只满足其中一个都不算：处在“已持锁、尚未注册线程句柄”窗口的写者不在注册表
        里，必须等它注册/退出到“拿得到锁”为止；否则 close 会与该写者即将发起的
        WriteFile 竞态（提前释放 input_write）。锁不可得期间持续取消已注册写者。
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        attempts = 0
        lock_acquired = False
        while True:
            with self._io_lock:
                handles = list(self._writer_threads.values())
            if handles:
                for handle in handles:
                    cancel_synchronous_io(handle)
                    attempts += 1
            else:
                # 注册表为空：唯一还要证明的是“没有写者持锁（含未注册窗口）”。
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    lock_acquired = self._input_lock.acquire(
                        timeout=min(0.05, remaining)
                    )
                else:
                    lock_acquired = self._input_lock.acquire(blocking=False)
                if lock_acquired:
                    # 持锁期间不可能有新注册；复查并取消后立即释放。
                    with self._io_lock:
                        still = list(self._writer_threads.values())
                    for handle in still:
                        cancel_synchronous_io(handle)
                        attempts += 1
                    with self._io_lock:
                        leftovers_held = list(self._writer_threads.values())
                    self._input_lock.release()
                    detail = {
                        "cancel_attempts": attempts,
                        "input_lock_acquired": True,
                        "registered_writers": len(leftovers_held),
                    }
                    return (not leftovers_held), detail
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        with self._io_lock:
            leftovers = list(self._writer_threads.values())
        detail = {
            "cancel_attempts": attempts,
            "input_lock_acquired": lock_acquired,
            "registered_writers": len(leftovers),
        }
        return False, detail

    def _retained_inventory(self) -> list[str]:
        names = list(self._spawn.retained_resources())
        if self._reader_thread is not None and self._reader_thread.is_alive():
            names.append("pump_thread")
        with self._lifecycle_lock:
            if self._reader_handle:
                names.append("pump_thread_handle")
        if self._guard is not None and self._guard.handle_open:
            names.append("job_handle")
        return names

    # ------------------------------------------------------------------ 诊断
    def inject_release_failure_for_test(self, resource: str) -> None:
        """**测试专用**：让指定资源的下一次释放走失败分支（见 spawn_win 同名方法）。

        包装层模拟（不是真实 OS 失败），用于验证“部分失败保留 owner、重试成功”；
        生产代码不得调用。
        """
        self._spawn.inject_release_failure(resource)

    def describe(self) -> dict[str, Any]:
        with self._lifecycle_lock:
            closed, closing, dead = self._closed, self._closing, self._dead_proven
        evidence = self._spawn.evidence
        identity = self._spawn.identity
        return {
            "backend": "ConPtyBackend",
            "platform": sys.platform,
            "pid": self.pid,
            "identity": {
                "pid": identity.pid if identity else None,
                "created_at_filetime": str(identity.created_at_filetime)
                if identity and identity.created_at_filetime
                else None,
            },
            # SpawnEvidence.as_dict 已经把 FILETIME 渲染成字符串（跨 JS 安全）
            "gate": evidence.as_dict() if evidence else None,
            "guard": self._guard.describe() if self._guard else None,
            "owns_guard": self._owns_guard,
            "closing": closing,
            "closed": closed,
            "dead_proven": dead,
            "last_terminate_confirmed": self.last_terminate_confirmed,
            "buffer_bytes": len(self._buf),
            "drain_eof": self._drain_eof,
            "drain_error": None if self._drain_error is None else type(self._drain_error).__name__,
            "drain_cancelled": self._drain_cancelled,
            "drain_done": self._drain_done,
            "pump_exit": self._pump_exit,
            "pump_fatal": self._pump_fatal,
        }


class WinptyBackend:
    """参照回归后端（pywinpty）——**本阶段不实现，也不是生产依赖**。

    - 不进入生产门禁路径（``pywinpty.spawn`` 不暴露 creationflags，无法满足
      ``atomic_with_spawn``；见实施计划 §5.3 与 P0 接口文档 §4.1）；
    - 生产依赖面只允许 ConPtyBackend（纯 ctypes + 标准库）；本类仅为接口
      占位，构造即明确拒绝，避免误接线。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise WindowsApiUnavailable(
            "WinptyBackend 参照实现未提供（P1 范围只有 ConPtyBackend）；"
            "pywinpty 不是生产依赖，不得接入生产门禁路径"
        )
