"""PTY runtime：所有权 + 持续 drain + 退出/清理契约（平台无关，P0）。

契约要点（对应契约报告 §2.3 / M4-M6 / M12-M13 与实施计划 §7.1/§8.5）：

1. reader 循环**只以** ``read()`` 抛 ``EOFError``（或通道错误）结束；
   ``alive()=false`` **不是**结束条件（真实 ConPTY 上进程退出后仍可能有尾部输出）。
2. ``process_exit_seen`` / ``reader_done`` / ``channel_eof`` / ``output_complete``
   是四个不同事实，分开公布；**只有对端真实 EOF** 才声明输出完整。
3. ``close()`` 顺序：**身份核验 -> 所有权快照 -> 可选中断 -> 终止 -> 整树终止+残留
   核对 -> drain 收敛/join reader -> 释放句柄**。任何一步失败都进入
   ``cleanup-failed`` 并保留 owner（记录可重试），绝不谎报 ``exited``。
4. 取消原语（关闭后端句柄）会连带终止进程，因此**只在终止与整树都确认成功之后**
   才允许取消；否则会毁掉“进程仍存活、owner 待重试”的证据。
5. ``detach()`` 需要真实宿主能力（``DetachHandler``）；没有注入时明确
   ``DetachUnsupportedError``，核心不伪造 detached 事实。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .contracts import (
    DEFAULT_EOF_GRACE_SECONDS,
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
        self._consumer_errors: list[str] = []
        # 清理身份核验：只用记录过的身份与赋值时的同一 handle，避免 PID 复用误杀。
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
        """输出消费者异常记录（消费失败不拖死 reader，但必须如实记录）。"""
        with self._output_lock:
            return tuple(self._consumer_errors)

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
        """同一 reader 临界区内：追加 OutputLog，再按序投递输出消费者。

        消费者（P1 的权威仿真器供料）**必须快速返回**：它在 reader 临界区内被
        调用，阻塞会拖慢 PTY 读取。消费异常不终止 drain，但被如实记录。
        """
        with self._output_lock:
            self.log.append(data)
            consumer = self._output_consumer
            if consumer is None:
                return
            try:
                consumer(data)
            except Exception as exc:  # noqa: BLE001 - 消费失败不拖死终端
                self._consumer_errors.append(f"{type(exc).__name__}: {exc}")

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

    def _cancel_drain(self) -> str:
        """取消 reader 的阻塞读（关闭终端 IO 句柄）。返回取消手段供报告记录。"""
        self._cancel_requested = True
        self._stop.set()
        close = getattr(self._backend, "close", None)
        if not callable(close):
            return "unsupported"
        try:
            close()
            return "backend-close"
        except Exception as exc:  # noqa: BLE001
            return f"backend-close-failed:{type(exc).__name__}"

    def _converge_drain(self, grace: float) -> dict[str, Any]:
        """收敛 reader。**不允许**把 daemon 线程当作回收保证。

        - 先在 grace 内等 reader 自然结束；
        - 没结束就请求取消（关句柄），再在 grace 内等；
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
    def read_from(self, cursor: int, *, max_bytes: int | None = None):
        """按绝对游标读取输出：见 ``output.OutputLog.read_from``。"""
        return self.log.read_from(cursor, max_bytes=max_bytes)

    def write(self, data: bytes) -> int:
        if self.state not in (RuntimeState.RUNNING, RuntimeState.EXITING):
            raise IllegalStateTransition(f"write in state {self.state.value}")
        return self._backend.write(data)

    def resize(self, rows: int, cols: int) -> bool:
        """尺寸变更。非运行状态返回 False（迟到 resize 有明确语义，不抛异常）。"""
        if self.state not in (RuntimeState.RUNNING, RuntimeState.EXITING):
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

    # -- 身份核验 ------------------------------------------------------
    def _identity_mismatch(self) -> str | None:
        """返回不匹配原因；``None`` 表示可继续清理。

        fail-closed：无法取得可比字段（PID/FILETIME 都缺）也算不匹配——宁可拒绝
        清理并保留 owner，也不对不可验证的 PID 下手。探针返回 ``None``（进程已
        不可探测）按已退出处理，不阻止正常收敛。
        """
        if self.identity is None or self._identity_probe is None:
            return None
        pid = self._backend.pid
        if not pid:
            return None
        try:
            live = self._identity_probe(int(pid))
        except Exception as exc:  # noqa: BLE001
            return f"identity probe failed: {type(exc).__name__}: {exc}"
        if live is None:
            return None
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
        tree_timeout: float = 2.0,
        reader_grace: float = 2.0,
        terminate: Callable[[bool], None] | None = None,
    ) -> CleanupReport:
        """清理契约：失败必须保留 owner。

        顺序（任何一步失败都会在报告里如实标注并保留 owner）：

        1. 身份核验（不通过即拒杀）；
        2. 所有权快照（必须在终止**之前**取得，根 PID 消失后无法枚举后代）；
        3. 可选中断（Ctrl-C）；
        4. 终止（有界，超时记录 ``timed-out``）；
        5. 整树终止 + 残留核对；
        6. reader 收敛（长时间不结束才取消——**且仅当终止与整树都成功**）；
        7. 释放后端句柄。

        ``terminate`` 允许注入替代终止实现（测试失败路径用）；``reader_grace``
        是 reader 收敛的两段等待上限（自然结束 / 取消后各一段）。
        """
        started = time.monotonic()
        with self._close_lock:
            report = CleanupReport(terminal_id=self.terminal_id, requested_reason=reason)
            if self.state in (RuntimeState.EXITED, RuntimeState.LOST):
                report.state_after = self.state
                report.backend_closed = True
                report.seconds = round(time.monotonic() - started, 3)
                return report

            # 1) 身份核验先于任何终止动作：不可验证或与记录不符时拒杀（防 PID 复用误杀）。
            mismatch = self._identity_mismatch()
            if mismatch is not None:
                self._set_state(RuntimeState.EXITING)
                self._set_state(RuntimeState.CLEANUP_FAILED)
                report.identity_check = (
                    IdentityCheck.PROBE_FAILED
                    if mismatch.startswith("identity probe failed")
                    else IdentityCheck.MISMATCH
                )
                report.terminate_result = TerminateOutcome.REFUSED_IDENTITY_MISMATCH
                report.error = mismatch
                report.owner_retained = True
                report.state_after = self.state
                report.seconds = round(time.monotonic() - started, 3)
                return report
            report.identity_check = (
                IdentityCheck.VERIFIED
                if self.identity is not None
                else IdentityCheck.NOT_REQUIRED
            )

            self._set_state(RuntimeState.EXITING)
            root_pid = self._backend.pid
            # 2) 所有权快照必须在终止之前取得。
            if self._terminator is not None and root_pid:
                report.tree_owned_pids = tuple(self._terminator.owned_pids(root_pid))

            # 3) 可选中断。
            if interrupt:
                try:
                    if self._backend.alive():
                        self._backend.write(b"\x03")
                        report.interrupt_sent = True
                        time.sleep(max(0.0, interrupt_delay))
                except Exception as exc:  # noqa: BLE001
                    report.interrupt_error = repr(exc)

            # 4) 终止（有界）。
            do_terminate = terminate or (lambda force: self._backend.terminate(force))
            done = threading.Event()
            box: dict[str, str] = {}

            def _term() -> None:
                try:
                    do_terminate(True)
                    box["result"] = "returned"
                except Exception as exc:  # noqa: BLE001
                    box["result"] = "error"
                    box["error"] = repr(exc)
                finally:
                    done.set()

            worker = threading.Thread(
                target=_term, name=f"pty-terminate-{self.terminal_id}", daemon=True
            )
            worker.start()
            if not done.wait(terminate_timeout):
                report.terminate_result = TerminateOutcome.TIMED_OUT
            elif box.get("result") == "returned":
                report.terminate_result = TerminateOutcome.RETURNED
            else:
                report.terminate_result = TerminateOutcome.ERROR
                report.terminate_error = box.get("error")

            # 5) 整树终止 + 残留核对（先快照过，才可能核对）。
            if self._terminator is not None and report.tree_owned_pids:
                try:
                    self._terminator.terminate_tree(root_pid, timeout=tree_timeout)
                except Exception as exc:  # noqa: BLE001
                    prior = f"{report.terminate_error}; " if report.terminate_error else ""
                    report.terminate_error = f"{prior}tree terminate failed: {type(exc).__name__}: {exc}"
                try:
                    report.tree_remaining_pids = tuple(
                        self._terminator.remaining(report.tree_owned_pids)
                    )
                except Exception as exc:  # noqa: BLE001
                    # 无法核对残留：保守按“仍存活”处理（fail-closed，保留 owner）。
                    report.tree_remaining_pids = tuple(report.tree_owned_pids)
                    prior = f"{report.terminate_error}; " if report.terminate_error else ""
                    report.terminate_error = f"{prior}tree remaining probe failed: {type(exc).__name__}: {exc}"

            # 6) reader 收敛：取消（关句柄）会连带终止进程，**只在终止与整树都
            #    确认成功之后**才允许；否则保留“进程仍存活、owner 待重试”的证据。
            terminate_ok = (
                report.terminate_result is TerminateOutcome.RETURNED
                and not report.terminate_error
            )
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
            if report.cancel_kind == "backend-close":
                # 取消原语即关闭句柄；失败（backend-close-failed:*）不算已关闭。
                report.backend_closed = True

            failures: list[str] = []
            if report.terminate_result is not TerminateOutcome.RETURNED:
                failures.append(f"terminate={report.terminate_result.value}")
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

            # 7) 全部成功：释放后端句柄后才允许进入 exited。
            if not report.backend_closed:
                try:
                    self._backend.close()
                    report.backend_closed = True
                except Exception as exc:  # noqa: BLE001
                    report.error = f"close failed: {exc!r}"
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
