"""PTY runtime：所有权 + 持续 drain + 退出/清理契约（平台无关，P0；r2）。

契约要点（契约报告 §2.3 / M4-M6 / M12-M13、实施计划 §7.1/§8.5 与 MA 审查 r2）：

1. reader 循环**只以** ``read()`` 抛 ``EOFError``（或通道错误）结束；
   ``alive()=false`` **不是**结束条件（真实 ConPTY 上进程退出后仍可能有尾部输出）。
2. ``process_exit_seen`` / ``reader_done`` / ``channel_eof`` / ``output_complete``
   是四个不同事实，分开公布；**只有对端真实 EOF** 才声明输出完整。
3. 身份核验三态（r2）：探针无结论（``None``/``UNKNOWN``）或探针缺失 -> **fail-closed**，
   拒绝任何终止/interrupt/取消并保 owner（``None`` 只表示未知，**不能证明 dead**）；
   明确 ``DEAD``（retained handle / Job 证据）才放行；根死但 Job 孙进程仍活时
   仍清理整个 Job。
4. ``close()`` 顺序：身份核验 -> 所有权快照 -> 可选中断 -> 终止 -> 整树终止 +
   残留核对 -> drain 收敛/join reader -> 释放句柄。所有权未知、根仍活、guard
   查询失败、残留非空、reader 未收敛——任何一条都不得 EXITED/取消。
5. 取消原语（关闭后端句柄）会连带终止进程，因此**只在终止成功、整树确认、
   根确认退出之后**才允许；否则会毁掉“进程仍存活、owner 待重试”的证据。
6. 有界清理（r2）：``terminate`` 与 ``backend.close`` 都在专用 worker 线程里执行
   并有界等待；未完成的 worker 保留复用（**重试不重叠**），不把阻塞调用放在
   close 主线程无限等待。
7. 输出消费者（r2）：按**绝对字节偏移**投递（emulator 可检测 gap 并显式降级，
   禁止冒充连续/完整供料）；失败计数有界且只记脱敏类型名（不落消息/秘密）。
8. ``detach()`` 需要真实宿主能力（``DetachHandler``）；没有注入时明确
   ``DetachUnsupportedError``，核心不伪造 detached 事实。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .contracts import (
    DEFAULT_CONSUMER_ERROR_TYPES,
    DEFAULT_EOF_GRACE_SECONDS,
    DEFAULT_HANDLE_CLOSE_TIMEOUT,
    DEFAULT_OUTPUT_LOG_BYTES,
    DEFAULT_READ_SIZE,
    CleanupReport,
    DetachReport,
    DetachUnsupportedError,
    DrainStopReason,
    ExitInfo,
    IdentityCheck,
    IdentityProbe,
    IllegalStateTransition,
    OutputConsumer,
    OwnershipGateError,
    OwnershipPolicy,
    ProcessIdentity,
    ProcessProbe,
    ProcessStatus,
    PtyBackend,
    RuntimeState,
    StartupOwnershipGate,
    TerminateOutcome,
    TreeGuard,
)
from .output import OutputLog

_LEGAL_TRANSITIONS: dict[RuntimeState, frozenset[RuntimeState]] = {
    # CREATED -> EXITING: 允许“创建后未启动就直接 close”，调用方无需特判。
    RuntimeState.CREATED: frozenset({RuntimeState.STARTING, RuntimeState.EXITING}),
    RuntimeState.STARTING: frozenset(
        {RuntimeState.RUNNING, RuntimeState.EXITING, RuntimeState.LOST}
    ),
    RuntimeState.RUNNING: frozenset({RuntimeState.EXITING}),
    RuntimeState.EXITING: frozenset(
        {RuntimeState.EXITED, RuntimeState.CLEANUP_FAILED, RuntimeState.LOST}
    ),
    RuntimeState.EXITED: frozenset(),
    # CLEANUP_FAILED -> EXITING: 保留 owner 后允许重试收敛。
    RuntimeState.CLEANUP_FAILED: frozenset({RuntimeState.EXITING, RuntimeState.EXITED}),
    RuntimeState.LOST: frozenset(),
}


class _BoundedCall:
    """有界执行一个可能阻塞的调用。

    - 首次 ``run`` 启动 daemon worker；超时返回 ``finished=False``，worker
      **保留**——下次 ``run`` 继续等待同一 worker（重试不重叠）；
    - 已完成且成功的结果可重复消费（不重跑）；已完成且失败的可 ``reset``
      后允许新一轮尝试（仍不重叠）。
    """

    def __init__(self, fn: Callable[[], Any]) -> None:
        self._fn = fn
        self._worker: threading.Thread | None = None
        self._done = threading.Event()
        self._result: Any = None
        self._error: str | None = None

    def reset(self, fn: Callable[[], Any] | None = None) -> None:
        if self._worker is not None and self._worker.is_alive():
            raise RuntimeError("cannot reset a bounded call while its worker is still running")
        if fn is not None:
            self._fn = fn
        self._worker = None
        self._done = threading.Event()
        self._result = None
        self._error = None

    @property
    def started(self) -> bool:
        return self._worker is not None

    @property
    def finished(self) -> bool:
        return self._worker is not None and self._done.is_set()

    @property
    def succeeded(self) -> bool:
        return self.finished and self._error is None

    def run(self, timeout: float) -> tuple[bool, Any, str | None]:
        """返回 ``(finished, result, error)``；``finished=False`` 表示仍在执行。"""
        if not self.started:
            def _work() -> None:
                try:
                    self._result = self._fn()
                except Exception as exc:  # noqa: BLE001 - 有界调用的失败必须如实记录
                    self._error = f"{type(exc).__name__}: {exc}"
                finally:
                    self._done.set()

            self._worker = threading.Thread(
                target=_work, name="pty-bounded-call", daemon=True
            )
            self._worker.start()
        if not self._done.wait(max(0.0, timeout)):
            return False, None, None
        return True, self._result, self._error


class PtyRuntime:
    """拥有一个 PTY、进度输出日志、进程树与生命周期状态。

    生产装配必须经 ``ownership.build_runtime``（fail-closed 工厂）；直接构造仅用于
    测试/专用宿主，调用方需自行保证所有权语义。
    """

    def __init__(
        self,
        terminal_id: str,
        backend: PtyBackend,
        *,
        output_cap: int = DEFAULT_OUTPUT_LOG_BYTES,
        read_size: int = DEFAULT_READ_SIZE,
        terminator: TreeGuard | None = None,
        eof_grace: float = DEFAULT_EOF_GRACE_SECONDS,
        identity: ProcessIdentity | None = None,
        identity_probe: IdentityProbe | None = None,
        require_startup_gate: bool = False,
        ownership: OwnershipPolicy | None = None,
        output_consumer: OutputConsumer | None = None,
    ) -> None:
        self.terminal_id = terminal_id
        self._backend = backend
        self._read_size = int(read_size)
        self._terminator = terminator
        self._eof_grace = float(eof_grace)
        self.log = OutputLog(output_cap)
        self._state = RuntimeState.CREATED
        self._state_lock = threading.Lock()
        self._exit_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._output_lock = threading.Lock()
        self.exit = ExitInfo()
        self.rows = 0
        self.cols = 0
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._eof_seen = threading.Event()
        self._bytes_observed_at_exit = 0
        self._process_exit_seen_at: float | None = None
        self._cancel_requested = False
        self._consumer_error_types: list[str] = []
        self._consumer_failure_count = 0
        # 有界清理 worker（跨 close 重试保留复用，保证重试不重叠）。
        self._terminate_call: _BoundedCall | None = None
        self._backend_close_call: _BoundedCall | None = None
        # 清理身份核验：只用记录过的身份与新探针的显式三态结论，避免 PID 复用误杀。
        self.identity = identity
        self._identity_probe = identity_probe
        self._require_startup_gate = require_startup_gate
        self.ownership = ownership
        self._output_consumer = output_consumer
        self._detached = bool(ownership.detached) if ownership is not None else False

    # -- 状态 ---------------------------------------------------------
    @property
    def state(self) -> RuntimeState:
        with self._state_lock:
            return self._state

    @property
    def detached(self) -> bool:
        return self._detached

    @property
    def consumer_errors(self) -> tuple[str, ...]:
        """输出消费者失败的类型名（有界、脱敏；不包含异常消息，防秘密泄漏）。"""
        with self._output_lock:
            return tuple(self._consumer_error_types)

    @property
    def consumer_failure_count(self) -> int:
        with self._output_lock:
            return self._consumer_failure_count

    @property
    def consumer_failed(self) -> bool:
        """供料粘滞事实：一旦失败为真（直到进程结束不自动清除）。"""
        with self._output_lock:
            return self._consumer_failure_count > 0

    def _set_state(self, target: RuntimeState) -> None:
        with self._state_lock:
            if target is self._state:
                return
            if target not in _LEGAL_TRANSITIONS[self._state]:
                raise IllegalStateTransition(f"{self._state.value} -> {target.value}")
            self._state = target

    # -- 启动 / 读取 ---------------------------------------------------
    def start(
        self,
        *,
        rows: int,
        cols: int,
        gate: StartupOwnershipGate | None = None,
    ) -> None:
        """启动 reader。

        fail-closed：``gate.verify()`` 不通过时抛 ``OwnershipGateError``，且
        **不进入 RUNNING、不启动 reader**。声明了树守卫的 runtime 必须提供启动
        所有权证据（四要素）；门禁证据中的身份会被 runtime 采用，作为后续清理
        核验的依据。
        """
        if gate is not None:
            gate.verify()
            evidence = getattr(gate, "evidence", None)
            evidence_identity = (
                getattr(evidence, "identity", None) if evidence is not None else None
            )
            if evidence_identity is not None:
                if self.identity is not None and not self.identity.matches(evidence_identity):
                    raise OwnershipGateError(
                        "runtime 既有身份与启动证据不一致：拒绝启动（避免用错身份做清理核验）"
                    )
                self.identity = evidence_identity
        elif self._require_startup_gate:
            raise OwnershipGateError(
                "该 runtime 声明了树守卫但启动时未提供所有权证据："
                "必须提供 (assigned, atomic_with_spawn, identity, handle_bound_for_cleanup) 四要素"
            )
        self.rows, self.cols = int(rows), int(cols)
        self._set_state(RuntimeState.STARTING)
        self._set_state(RuntimeState.RUNNING)
        self._reader = threading.Thread(
            target=self._drain_loop, name=f"pty-drain-{self.terminal_id}", daemon=True
        )
        self._reader.start()

    def request_drain_stop(self) -> None:
        """请求 drain 在下一个循环检查点停止（不关句柄、不终止进程）。

        单独置位不保证立即打断阻塞中的 ``read``（那是取消语义，见 ``close()``）。
        """
        self._stop.set()

    def _fanout(self, data: bytes) -> None:
        """同一 reader 临界区内：追加 OutputLog，再按绝对偏移投递输出消费者。

        消费者（P1 的权威仿真器供料）收到 ``(seq, data)``：``seq`` 是该块首字节
        的绝对偏移——若与期望位置不符即存在缺口，**必须显式降级**，不得假设
        供料连续或声称完整。消费者**必须快速返回**（在 reader 临界区内调用）。
        消费异常不终止 drain；失败只记脱敏类型名与计数（有界）。
        """
        with self._output_lock:
            seq = self.log.append(data)
            consumer = self._output_consumer
            if consumer is None:
                return
            try:
                consumer(seq, data)
            except Exception as exc:  # noqa: BLE001 - 消费失败不拖死终端
                self._consumer_failure_count += 1
                type_name = type(exc).__name__
                if (
                    type_name not in self._consumer_error_types
                    and len(self._consumer_error_types) < DEFAULT_CONSUMER_ERROR_TYPES
                ):
                    self._consumer_error_types.append(type_name)

    def _drain_loop(self) -> None:
        """持续读到通道 EOF；结束原因分类记录，绝不混同。"""
        stop_reason = DrainStopReason.UNKNOWN
        channel_eof = False
        while True:
            if self._stop.is_set() and not self._cancel_requested:
                stop_reason = DrainStopReason.STOP_REQUESTED
                break
            try:
                data = self._backend.read(self._read_size)
            except EOFError:
                if self._cancel_requested:
                    # 通道是我们自己关掉的：即使表现为 EOFError，也不是对端 EOF。
                    stop_reason = DrainStopReason.CANCELLED
                else:
                    channel_eof = True
                    stop_reason = DrainStopReason.EOF
                break
            except (OSError, RuntimeError, ValueError) as exc:
                if self._cancel_requested:
                    # 取消原语（关闭句柄）在自己触发的 read 上表现为传输层异常，
                    # 不是“意外的通道错误”，但也不是对端 EOF。
                    stop_reason = DrainStopReason.CANCELLED
                else:
                    with self._exit_lock:
                        self.exit.channel_error = f"{type(exc).__name__}: {exc}"
                    stop_reason = DrainStopReason.CHANNEL_ERROR
                break
            if data:
                self._fanout(data)
                continue
            # 空读：后端此刻等价于“暂时没有数据”。进程已退出则进入有界 grace，
            # 避免永久卡死；grace 内通道若到达 EOF 会走 EOFError 分支。
            if not self._backend.alive():
                if self._process_exit_seen_at is None:
                    self._process_exit_seen_at = time.monotonic()
                    self._bytes_observed_at_exit = self.log.total_bytes
                if time.monotonic() - self._process_exit_seen_at > self._eof_grace:
                    stop_reason = DrainStopReason.EOF_TIMEOUT
                    break
            time.sleep(0.001)
        with self._exit_lock:
            self.exit.observed_at = time.monotonic()
            self.exit.drain_stop_reason = stop_reason
            self.exit.channel_eof = channel_eof
            # 只有对端真实 EOF 才声明“输出完整”；取消/错误/超时/stop 一律不声明。
            self.exit.output_complete = stop_reason is DrainStopReason.EOF
            self.exit.bytes_at_stop = self.log.total_bytes
            self.exit.reader_done = True
        self._eof_seen.set()

    def wait_eof(self, timeout: float) -> bool:
        """等 reader **结束**（不区分结束原因；原因看 ``exit.drain_stop_reason``）。"""
        return self._eof_seen.wait(timeout)

    # -- 有界后端调用（取消 / 释放句柄） ---------------------------------
    def _bounded_backend_close(self, timeout: float) -> str | None:
        """有界关闭后端句柄（阻塞的 close 不占住 close 主线程）。

        返回 ``None``=成功；``"timeout"``=仍在执行（worker 保留复用）；
        ``"unsupported"``=后端无 close；其它=异常描述。已失败的可重试（不重叠）。
        """
        close = getattr(self._backend, "close", None)
        if not callable(close):
            return "unsupported"
        call = self._backend_close_call
        if call is None:
            self._backend_close_call = call = _BoundedCall(close)
        elif call.finished and not call.succeeded:
            call.reset(close)
        finished, _result, error = call.run(timeout)
        if not finished:
            return "timeout"
        return error

    def _cancel_drain(self, close_timeout: float) -> str:
        """取消 reader 的阻塞读（关闭终端 IO 句柄）。返回取消手段供报告记录。"""
        self._cancel_requested = True
        self._stop.set()
        outcome = self._bounded_backend_close(close_timeout)
        if outcome is None:
            return "backend-close"
        if outcome == "timeout":
            return "backend-close-timeout"
        if outcome == "unsupported":
            return "unsupported"
        return f"backend-close-failed:{outcome}"

    def _converge_drain(self, grace: float, *, close_timeout: float) -> dict[str, Any]:
        """收敛 reader。**不允许**把 daemon 线程当作回收保证。

        - 先在 grace 内等 reader 自然结束；
        - 没结束就请求取消（关句柄，有界），再在 grace 内等；
        - 仍未结束 -> ``reader_converged=False``，调用方必须保留 owner。
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
        cancel_kind = self._cancel_drain(close_timeout)
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
    def read_from(self, cursor: int, *, max_bytes: int | None = None):
        """按绝对游标读取输出：见 ``output.OutputLog.read_from``。"""
        return self.log.read_from(cursor, max_bytes=max_bytes)

    def write(self, data: bytes) -> int:
        """输入只在 ``running`` 接受；``exiting``（清理进行中）一律拒绝。

        r2：允许 EXITING 会与 close 的终止/取消并行——迟到的写可能落在
        已判定死亡的会话上，因此收紧为仅 RUNNING。
        """
        if self.state is not RuntimeState.RUNNING:
            raise IllegalStateTransition(f"write in state {self.state.value}")
        return self._backend.write(data)

    def resize(self, rows: int, cols: int) -> bool:
        """尺寸变更。仅 RUNNING；迟到 resize 返回 False（不抛异常）。"""
        if self.state is not RuntimeState.RUNNING:
            return False
        self._backend.resize(rows, cols)
        self.rows, self.cols = int(rows), int(cols)
        return True

    # -- 退出事件的对外发布 --------------------------------------------
    def poll_exit(self) -> ExitInfo:
        """把“根进程退出”与“reader 结束 / 通道 EOF / 输出完整”分开公布。"""
        with self._exit_lock:
            info = self.exit
            if not info.process_exit_seen and not self._backend.alive():
                info.process_exit_seen = True
                info.code = self._backend.exit_code()
                info.reason = "process-exit"
            if info.reader_done and not info.process_exit_seen:
                # 通道先结束而进程仍在：可能是错误/超时/取消，不能当作正常退出。
                info.reason = f"reader-stopped:{info.drain_stop_reason.value}"
            return info

    def wait_exit(self, timeout: float) -> ExitInfo:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            info = self.poll_exit()
            if info.process_exit_seen:
                # 进程已退出：再给 drain 一个有限窗口补尾部输出。
                self.wait_eof(min(2.0, max(0.0, deadline - time.monotonic())))
                return self.poll_exit()
            time.sleep(0.01)
        return self.poll_exit()

    def child_pids(self) -> list[int]:
        if self._terminator is None:
            return []
        root = self._backend.pid
        return [p for p in self._terminator.owned_pids(root) if p != root]

    # -- 身份核验（r2 三态 fail-closed） ---------------------------------
    def _identity_check(self) -> tuple[IdentityCheck, str | None]:
        """返回 (结论, 拒绝原因)；只有 ``verified``/``dead-confirmed``/``not-required``
        允许继续清理。

        - 无记录身份 -> ``not-required``（没有核验对象）；
        - 有记录身份但探针缺失 -> ``unknown``（无法核验，拒绝）；
        - 探针异常 -> ``probe-failed``；
        - 探针无结论（``None``/``UNKNOWN``/无可比身份）-> ``unknown``
          （``None`` 只表示未知，**不能证明 dead**）；
        - ``ALIVE`` 且身份匹配 -> ``verified``；不匹配 -> ``mismatch``；
        - ``DEAD``（retained handle / Job 明确证据）-> ``dead-confirmed``。
        """
        if self.identity is None:
            return IdentityCheck.NOT_REQUIRED, None
        if self._identity_probe is None:
            return IdentityCheck.UNKNOWN, (
                "recorded identity present but no identity probe: cannot verify liveness; "
                "fail-closed (no terminate/interrupt/cancel)"
            )
        pid = self._backend.pid
        if not pid:
            return IdentityCheck.UNKNOWN, (
                "recorded identity present but backend pid unavailable: fail-closed"
            )
        try:
            probe = self._identity_probe(int(pid))
        except Exception as exc:  # noqa: BLE001
            return IdentityCheck.PROBE_FAILED, f"identity probe failed: {type(exc).__name__}: {exc}"
        if probe is None:
            return IdentityCheck.UNKNOWN, (
                "identity probe returned no verdict (None = unknown, not dead): fail-closed"
            )
        if isinstance(probe, ProcessProbe):
            status, identity = probe.status, probe.identity
        elif isinstance(probe, ProcessIdentity):
            # 兼容过渡期旧探针：裸身份按 ALIVE + 该身份处理。
            status, identity = ProcessStatus.ALIVE, probe
        elif isinstance(probe, ProcessStatus):
            status, identity = probe, None
        else:
            return IdentityCheck.UNKNOWN, (
                f"identity probe returned unsupported verdict {type(probe).__name__}: fail-closed"
            )
        if status is ProcessStatus.DEAD:
            # 明确已退出（retained handle signaled / Job 证据）：允许整树清理；
            # 根死不等于树死——孙进程由 TreeGuard 继续清理。
            return IdentityCheck.CONFIRMED_DEAD, None
        if status is not ProcessStatus.ALIVE:
            return IdentityCheck.UNKNOWN, (
                "identity probe verdict unknown (not dead): fail-closed"
            )
        if identity is None:
            return IdentityCheck.UNKNOWN, (
                "probe says alive but provides no comparable identity: fail-closed"
            )
        if self.identity.matches(identity):
            return IdentityCheck.VERIFIED, None
        return IdentityCheck.MISMATCH, (
            f"identity mismatch (refusing to terminate): recorded pid={self.identity.pid} "
            f"filetime={self.identity.created_at_filetime}, live pid={identity.pid} "
            f"filetime={identity.created_at_filetime}"
        )

    # -- 清理 ---------------------------------------------------------
    def close(
        self,
        *,
        reason: str = "explicit-close",
        interrupt: bool = True,
        interrupt_delay: float = 0.15,
        terminate_timeout: float = 1.5,
        tree_timeout: float = 2.0,
        reader_grace: float = 2.0,
        handle_close_timeout: float = DEFAULT_HANDLE_CLOSE_TIMEOUT,
        terminate: Callable[[bool], None] | None = None,
    ) -> CleanupReport:
        """清理契约：失败必须保留 owner。

        顺序（任何一步失败都会在报告里如实标注并保留 owner）：

        1. 身份核验（无结论/缺探针/不匹配即拒杀，不做任何终止/interrupt）；
        2. 所有权快照（必须在终止**之前**取得；异常=所有权未知，fail-closed）；
        3. 可选中断（Ctrl-C）；
        4. 终止（有界 worker；未完成则保留复用，重试不重叠）；
        5. 整树终止 + 残留核对（空快照也要调用——真实 guard 可依赖 Job 身份；
           采用 guard 返回值；任何异常都 fail-closed）；
        6. 根存活确认（terminate 返回 ≠ 根死）；
        7. reader 收敛（长时间不结束才取消——**且仅在终止成功、整树确认、
           根确认退出之后**）；
        8. 释放后端句柄（有界，不占主线程无限等待）。

        ``terminate`` 允许注入替代终止实现（测试失败路径用）；``reader_grace``
        是 reader 收敛的两段等待上限；``handle_close_timeout`` 是取消/释放句柄
        的单次有界等待。
        """
        started = time.monotonic()
        with self._close_lock:
            report = CleanupReport(terminal_id=self.terminal_id, requested_reason=reason)
            if self.state in (RuntimeState.EXITED, RuntimeState.LOST):
                report.state_after = self.state
                report.backend_closed = True
                report.seconds = round(time.monotonic() - started, 3)
                return report

            # 1) 身份核验先于任何终止动作：无结论/缺探针/不匹配时
            #    **拒绝任何终止/interrupt/取消**（防 PID 复用误杀与不可验证下手）。
            check, detail = self._identity_check()
            report.identity_check = check
            if check in (
                IdentityCheck.UNKNOWN,
                IdentityCheck.MISMATCH,
                IdentityCheck.PROBE_FAILED,
            ):
                self._set_state(RuntimeState.EXITING)
                self._set_state(RuntimeState.CLEANUP_FAILED)
                report.terminate_result = (
                    TerminateOutcome.REFUSED_IDENTITY_MISMATCH
                    if check is IdentityCheck.MISMATCH
                    else TerminateOutcome.REFUSED_IDENTITY_UNKNOWN
                )
                report.error = detail
                report.owner_retained = True
                report.state_after = self.state
                report.seconds = round(time.monotonic() - started, 3)
                return report

            self._set_state(RuntimeState.EXITING)
            root_pid = self._backend.pid

            # 2) 所有权快照必须在终止之前取得；异常=所有权未知（fail-closed）。
            snapshot_error: str | None = None
            if self._terminator is not None:
                try:
                    report.tree_owned_pids = tuple(self._terminator.owned_pids(root_pid))
                except Exception as exc:  # noqa: BLE001
                    snapshot_error = (
                        f"tree ownership snapshot failed: {type(exc).__name__}: {exc}"
                    )

            # 3) 可选中断。
            if interrupt:
                try:
                    if self._backend.alive():
                        self._backend.write(b"\x03")
                        report.interrupt_sent = True
                        time.sleep(max(0.0, interrupt_delay))
                except Exception as exc:  # noqa: BLE001
                    report.interrupt_error = repr(exc)

            # 4) 终止（有界 worker：未完成保留复用，重试不重叠）。
            if terminate is None:
                do_terminate: Callable[[], None] = lambda: self._backend.terminate(True)
            else:
                do_terminate = lambda: terminate(True)
            call = self._terminate_call
            if call is None:
                self._terminate_call = call = _BoundedCall(do_terminate)
            elif call.finished and not call.succeeded:
                # 上一次尝试已失败：允许新一轮（仍不重叠）；未完成则直接复用。
                call.reset(do_terminate)
            finished, _result, error = call.run(terminate_timeout)
            if not finished:
                report.terminate_result = TerminateOutcome.TIMED_OUT
            elif error is not None:
                report.terminate_result = TerminateOutcome.ERROR
                report.terminate_error = error
            else:
                report.terminate_result = TerminateOutcome.RETURNED

            # 5) 整树终止 + 残留核对。空快照也要调用 terminate_tree：真实 guard
            #    可依赖 Job 身份枚举（根死仍能找到孙进程），不能按 root PID 扫。
            tree_error: str | None = snapshot_error
            if self._terminator is not None and snapshot_error is None:
                owned_after: Any = ()
                remaining_from_terminate: Any = ()
                try:
                    owned_after, remaining_from_terminate = self._terminator.terminate_tree(
                        root_pid, timeout=tree_timeout
                    )
                except Exception as exc:  # noqa: BLE001
                    tree_error = f"tree terminate failed: {type(exc).__name__}: {exc}"
                else:
                    if owned_after:
                        # 采用 guard 返回值：可能比我们的快照更完整（Job 枚举）。
                        report.tree_owned_pids = tuple(owned_after)
                    # 核对集只含 guard 枚举出的成员：根是否退出由 backend 存活
                    # 检查单独负责；“无 OS 级守卫”的显式降级实现不得被自动
                    # 塞入 PID 而误报残留。
                    verify = sorted(set(report.tree_owned_pids))
                    try:
                        remaining_now = self._terminator.remaining(verify)
                    except Exception as exc:  # noqa: BLE001
                        tree_error = (
                            f"tree remaining probe failed: {type(exc).__name__}: {exc}"
                        )
                    else:
                        report.tree_remaining_pids = tuple(
                            sorted(set(remaining_now) | set(remaining_from_terminate))
                        )
            if tree_error is not None:
                # 所有权/残留不可确认：保守按“可能仍存活”处理，配合 failures 保 owner。
                conservative = set(report.tree_owned_pids)
                if root_pid:
                    conservative.add(int(root_pid))
                report.tree_remaining_pids = tuple(sorted(conservative))

            # 6) 根存活确认：terminate “返回”不等于根已退出。
            root_alive = False
            liveness_error: str | None = None
            try:
                root_alive = bool(self._backend.alive())
            except Exception as exc:  # noqa: BLE001
                root_alive = True  # 无法确认：保守视为可能存活
                liveness_error = f"root liveness probe failed: {type(exc).__name__}: {exc}"

            # 7) reader 收敛：取消（关句柄）会连带终止进程，**只在终止成功、
            #    整树确认、根确认退出之后**才允许；否则保留“进程仍存活、
            #    owner 待重试”的证据。
            terminate_ok = report.terminate_result is TerminateOutcome.RETURNED
            if (
                terminate_ok
                and tree_error is None
                and not report.tree_remaining_pids
                and not root_alive
            ):
                conv = self._converge_drain(reader_grace, close_timeout=handle_close_timeout)
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
            if report.cancel_kind == "backend-close":
                # 取消原语即关闭句柄；失败/超时（backend-close-failed:* /
                # backend-close-timeout）不算已关闭。
                report.backend_closed = True

            failures: list[str] = []
            if report.terminate_result is not TerminateOutcome.RETURNED:
                failures.append(f"terminate={report.terminate_result.value}")
            if report.terminate_error:
                failures.append(f"terminate_error={report.terminate_error}")
            if snapshot_error:
                failures.append(snapshot_error)
            if tree_error:
                failures.append(tree_error)
            if report.tree_remaining_pids:
                failures.append(f"still-alive={list(report.tree_remaining_pids)}")
            if root_alive:
                failures.append("root still alive after terminate")
            if liveness_error:
                failures.append(liveness_error)
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

            # 8) 全部成功：有界释放后端句柄后才允许进入 exited。
            if not report.backend_closed:
                outcome = self._bounded_backend_close(handle_close_timeout)
                if outcome is None:
                    report.backend_closed = True
                elif outcome == "timeout":
                    report.error = (
                        "backend close timed out (worker retained; owner kept for retry)"
                    )
                else:
                    report.error = f"close failed: {outcome}"
            if report.error:
                self._set_state(RuntimeState.CLEANUP_FAILED)
                report.owner_retained = True
            else:
                self._set_state(RuntimeState.EXITED)
            report.state_after = self.state
            report.seconds = round(time.monotonic() - started, 3)
            return report

    # -- detach（需要真实宿主能力） --------------------------------------
    def detach(self) -> DetachReport:
        """显式 runtime detach：保留原 PTY/PID，交独立宿主持有。

        没有注入真实宿主能力（``OwnershipPolicy.detach_handler``）时**必须**失败：
        本核心不提供伪造实现。失败不改变 runtime 状态、不触碰 PTY。

        注意：这与 ``AttachmentRegistry.detach(token)``（浏览器/连接级撤销）是
        完全不同的语义：后者只释放一个 attachment，不改变进程寿命。
        """
        with self._close_lock:
            if self.state is not RuntimeState.RUNNING:
                raise IllegalStateTransition(f"detach in state {self.state.value}")
            handler = self.ownership.detach_handler if self.ownership is not None else None
            if handler is None:
                raise DetachUnsupportedError(
                    "无真实宿主能力（DetachHandler）：runtime.detach 需要独立宿主持有 "
                    "PTY/句柄，公共核心不伪造 detached 事实"
                )
            report = handler.request_detach(self)
            self._detached = True
            return report
