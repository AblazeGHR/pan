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
    "BackendCloseError",
    "WinptyBackend",
    "DEFAULT_PUMP_BUFFER_BYTES",
    "DEFAULT_WRITE_BUDGET_SECONDS",
]

#: pump 内部缓冲上限：超出则 pump 暂停读（有界、不丢弃、不泄洪）。
DEFAULT_PUMP_BUFFER_BYTES = 4 * 1024 * 1024
#: 单次 read() 默认拉取上限。
DEFAULT_PUMP_READ_SIZE = 64 * 1024
#: write() 的默认总预算（秒）：预算耗尽返回 partial。
DEFAULT_WRITE_BUDGET_SECONDS = 5.0
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
        """持续读输出管道到有界缓冲（不丢弃、消费者慢时暂停读 = 背压）。"""
        handle = open_current_thread_handle()
        with self._lifecycle_lock:
            self._reader_handle = handle
        try:
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
        finally:
            with self._cv:
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

        - 串行：同一 backend 的写互相串行；与 close 的句柄释放通过注册表 +
          收敛协议互斥，杜绝句柄复用竞态；
        - 有界预算：总预算（默认 5s）内尽力写完；预算耗尽返回 partial（不抛）；
        - close 并发：``CancelSynchronousIo`` 中止进行中的写 -> 抛
          ``BackendClosedError``（携带部分写入计数）；管道断链 -> ``OSError``。
        """
        payload = bytes(data)
        if not payload:
            return 0
        budget = self._write_budget if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + budget
        with self._input_lock:
            with self._lifecycle_lock:
                if self._closed or self._closing:
                    raise BackendClosedError("backend 已关闭/正在关闭：write 拒绝")
                if not self._spawn.input_write:
                    raise BackendClosedError("input_write 句柄已释放：write 拒绝")
            thread_handle = open_current_thread_handle()
            tid = threading.get_ident()
            with self._io_lock:
                self._writer_threads[tid] = thread_handle
            total = 0
            try:
                while total < len(payload):
                    if time.monotonic() >= deadline:
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
                            raise BackendClosedError(
                                "write 被 close 中止（ERROR_OPERATION_ABORTED）",
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
                with self._io_lock:
                    self._writer_threads.pop(tid, None)
                if not close_handle_checked(thread_handle):
                    self._orphan_handles.append(thread_handle)
            return total

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

    def probe_retained(self) -> ProcessProbe:
        """retained-handle 三态探针（供 runtime/诊断；不重开 PID）。"""
        handle = self._spawn.h_process
        if not handle:
            with self._lifecycle_lock:
                if self._dead_proven:
                    return ProcessProbe(
                        ProcessStatus.DEAD,
                        self.identity,
                        "cached: 释放前已确认 signaled",
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
          本机实测（build 26200）不会产生 OS 级 CTRL_C_EVENT，仅表现为
          “阻塞中的控制台读被释放”——不承诺 Ctrl-C 语义（见文档边界）。
        """
        with self._lifecycle_lock:
            if self._closed:
                raise BackendClosedError("backend 已关闭：terminate 拒绝")
        if not force:
            try:
                self.write(b"\x03")
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

        1. 读者收敛：置 stop -> ``CancelSynchronousIo`` 重试 -> join 线程；
        2. 写者收敛：取消注册的 writer 线程 -> 取得 ``_input_lock`` 证明无写者；
        3. 释放资源（官方顺序）：output_read -> ClosePseudoConsole（有界 worker）
           -> 属性表 -> input_write -> 其余管道 -> 线程句柄 -> h_process；
        4. 若 guard 归 backend 自持：最后关闭 Job 句柄（kill-on-close 兜底）。

        要求进程已确认退出（``wait_state == DEAD``）才释放清理绑定；否则
        ``staged_release`` 拒绝释放（保留可重试）。重复 close 幂等。
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

    def _converge_reader(self, timeout: float) -> tuple[bool, dict[str, Any]]:
        thread = self._reader_thread
        if thread is None:
            return True, {"reader": "not-started"}
        if not thread.is_alive():
            return True, {"reader": "already-exited"}
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
        deadline = time.monotonic() + max(0.0, float(timeout))
        attempts = 0
        while True:
            with self._io_lock:
                handles = list(self._writer_threads.values())
            if not handles:
                break
            for handle in handles:
                cancel_synchronous_io(handle)
                attempts += 1
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        remaining = max(0.0, deadline - time.monotonic())
        if remaining > 0:
            got = self._input_lock.acquire(timeout=remaining)
        else:
            got = self._input_lock.acquire(blocking=False)
        if got:
            # 持锁证明：无写者处于“已注册/写中”；随后复查注册表。
            with self._io_lock:
                still = list(self._writer_threads.values())
            for handle in still:
                cancel_synchronous_io(handle)
                attempts += 1
            self._input_lock.release()
        with self._io_lock:
            leftovers = list(self._writer_threads.values())
        detail = {
            "cancel_attempts": attempts,
            "input_lock_acquired": bool(got),
            "registered_writers": len(leftovers),
        }
        return (not leftovers), detail

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
