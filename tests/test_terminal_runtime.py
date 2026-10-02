"""Pan Terminal P0：PtyRuntime 生命周期、drain 与清理契约（跨平台）。

覆盖契约报告 M4/M5/M6/M12/M13 与实施计划 §7.1/§13，并含 MA 审查返工 r2 的
先失败后通过回归：

- 身份核验三态：probe 未知（None/UNKNOWN）或缺失 -> fail-closed 拒绝任何
  终止/interrupt/取消（不得 EXITED）；明确 DEAD（retained handle/Job 证据）
  才放行；根死但 Job 孙进程仍活时仍清理整个 Job；
- 树所有权：owned 快照异常/remaining 异常 -> 不得 EXITED 或取消 reader；
  空快照不跳过 terminate_tree；采用 terminate_tree 返回值；根仍活不得取消；
- 有界清理：terminate 超时保留/复用 worker（重试不重叠）；backend.close 有界
  （不阻塞 close 主线程）；迟到 write/resize 与 close 竞态被拒；
- 输出消费者：绝对 seq 供料（gap 可检测）、失败计数有界且脱敏。

本模块同时定义跨测试文件复用的确定性 support（``ScriptedBackend`` /
``RecordingTreeGuard``）：lease / ownership / driver 测试通过显式 importlib
加载本文件复用，避免在写范围之外新增 tests 共享模块。
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.terminal.contracts import (
    DrainStopReason,
    IllegalStateTransition,
    OwnershipGateError,
    ProcessIdentity,
    ProcessOwnershipEvidence,
    RuntimeState,
    TerminateOutcome,
)
from packages.core.terminal.ownership import UnverifiedOwnershipGate
from packages.core.terminal.runtime import PtyRuntime

TID = "term_runtime0000000001"

# ---------------------------------------------------------------------------
# 共享 support（其它测试文件经 _load_support() 复用）
# ---------------------------------------------------------------------------


def _load_support():
    """把本文件作为普通模块加载，供兄弟测试文件取用共享 support。"""
    path = Path(__file__).resolve()
    spec = importlib.util.spec_from_file_location("_terminal_runtime_support", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ScriptedBackend:
    """确定性测试后端（**不代表真实 CLI/PTY 行为**）。

    能复现“``alive()=false`` 但通道里仍有尾部数据”、终止失败/超时、终止 no-op、
    通道错误、阻塞读与取消、取消无效、close 阻塞等分支。
    """

    def __init__(
        self,
        chunks: Iterable[bytes] = (),
        *,
        alive_returns: bool = False,
        exit_code: int | None = 0,
        fail_terminate: bool = False,
        channel_error: str | None = None,
        write_error: str | None = None,
        resize_error: str | None = None,
        pid: int = 4242,
        empty_reads: bool = False,
        block_forever: bool = False,
        cancel_on_close: bool = True,
        abort_on_release: bool = False,
        events: list[str] | None = None,
        alive_after_terminate: bool | None = None,
        terminate_delay: float = 0.0,
        close_gate: threading.Event | None = None,
        write_gate: threading.Event | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self._alive_returns = alive_returns
        self._exit_code = exit_code
        self.fail_terminate = fail_terminate
        self._channel_error = channel_error
        self._write_error = write_error
        self._resize_error = resize_error
        self.pid = pid
        self._empty_reads = empty_reads
        self._block_forever = block_forever
        self._cancel_on_close = cancel_on_close
        self._abort_on_release = abort_on_release
        self._release = threading.Event()
        self._events = events
        self._events_lock = threading.Lock()
        self.writes: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.terminate_calls: list[bool] = []
        self.closed = False
        self.read_calls = 0
        self.alive_after_terminate = alive_after_terminate
        self.terminate_delay = terminate_delay
        self.close_gate = close_gate
        self.write_gate = write_gate
        self._lock = threading.Lock()

    def _record(self, name: str) -> None:
        if self._events is not None:
            with self._events_lock:
                self._events.append(name)

    def set_alive(self, value: bool) -> None:
        """测试钩子：外部世界（进程后来退出/资料更新）改变存活事实。"""
        self._alive_returns = value

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
        # 模拟无读超时的后端：只有 close()/release 才会放行。
        self._release.wait(30.0)
        if self._abort_on_release:
            raise ConnectionAbortedError("[WinError 10053] simulated abort")
        raise EOFError

    def write(self, data: bytes) -> int:
        if self.write_gate is not None:
            self.write_gate.wait(2.0)
        if self._write_error is not None:
            raise RuntimeError(self._write_error)
        self._record("write")
        self.writes.append(data)
        return len(data)

    def resize(self, rows: int, cols: int) -> None:
        if self._resize_error is not None:
            raise RuntimeError(self._resize_error)
        self._record("resize")
        self.resizes.append((rows, cols))

    def alive(self) -> bool:
        return self._alive_returns

    def exit_code(self) -> int | None:
        return self._exit_code if not self._alive_returns else None

    def terminate(self, force: bool) -> None:
        self._record("terminate")
        self.terminate_calls.append(force)
        if self.terminate_delay:
            time.sleep(self.terminate_delay)
        if self.alive_after_terminate is not None:
            self._alive_returns = self.alive_after_terminate
        if self.fail_terminate:
            raise RuntimeError("terminate failed (injected)")

    def close(self) -> None:
        if self.close_gate is not None:
            self.close_gate.wait(2.0)
        self._record("close")
        self.closed = True
        if self._cancel_on_close:
            self._release.set()

    def release_blocked_read(self) -> None:
        self._release.set()

    def seed(self, *chunks: bytes) -> None:
        with self._lock:
            self._chunks.extend(chunks)


class RecordingTreeGuard:
    """记录调用顺序的测试树守卫（``os_level_guard=False``，不是生产证明）。"""

    def __init__(
        self,
        *,
        owned: Iterable[int] = (4242,),
        remaining: Iterable[int] = (),
        events: list[str] | None = None,
        owned_error: str | None = None,
        terminate_error: str | None = None,
        remaining_error: str | None = None,
        terminate_owned: Iterable[int] | None = None,
        terminate_remaining: Iterable[int] | None = None,
    ) -> None:
        self._owned = list(owned)
        self._remaining = list(remaining)
        self._events = events
        self.owned_error = owned_error
        self.terminate_error = terminate_error
        self.remaining_error = remaining_error
        self._terminate_owned = terminate_owned
        self._terminate_remaining = terminate_remaining
        self._lock = threading.Lock()

    def _record(self, name: str) -> None:
        if self._events is not None:
            with self._lock:
                self._events.append(name)

    def describe(self) -> dict[str, Any]:
        return {"kind": "scripted", "os_level_guard": False}

    def owned_pids(self, root_pid: int | None) -> list[int]:
        self._record("owned")
        if self.owned_error is not None:
            raise RuntimeError(self.owned_error)
        return list(self._owned)

    def remaining(self, pids: Iterable[int]) -> list[int]:
        self._record("remaining")
        if self.remaining_error is not None:
            raise RuntimeError(self.remaining_error)
        return list(self._remaining)

    def terminate_tree(
        self, root_pid: int | None, *, timeout: float
    ) -> tuple[list[int], list[int]]:
        self._record("terminate_tree")
        if self.terminate_error is not None:
            raise RuntimeError(self.terminate_error)
        owned = list(self._terminate_owned if self._terminate_owned is not None else self._owned)
        remaining = list(
            self._terminate_remaining if self._terminate_remaining is not None else self._remaining
        )
        return owned, remaining


# ---------------------------------------------------------------------------
# 状态机与启动门禁
# ---------------------------------------------------------------------------


def test_initial_state_and_gate_rejection_keeps_created():
    backend = ScriptedBackend([b"x"])
    runtime = PtyRuntime(TID, backend, require_startup_gate=True, terminator=RecordingTreeGuard())
    assert runtime.state is RuntimeState.CREATED
    with pytest.raises(OwnershipGateError):
        runtime.start(rows=24, cols=80)
    assert runtime.state is RuntimeState.CREATED
    assert not runtime.wait_eof(0.05)  # reader 未启动
    assert backend.read_calls == 0


def test_start_adopts_evidence_identity():
    backend = ScriptedBackend([b"x"])
    identity = ProcessIdentity(pid=4242, created_at_filetime=123456)
    evidence = ProcessOwnershipEvidence(
        assigned=True,
        atomic_with_spawn=True,
        identity=identity,
        handle_bound_for_cleanup=True,
        guard="scripted",
    )
    runtime = PtyRuntime(
        TID,
        backend,
        require_startup_gate=True,
        terminator=RecordingTreeGuard(),
        identity_probe=lambda pid: identity,
    )
    runtime.start(rows=24, cols=80, gate=UnverifiedOwnershipGate(evidence))
    assert runtime.state is RuntimeState.RUNNING
    assert runtime.identity is identity
    assert runtime.wait_eof(2.0)


def test_gate_identity_conflict_rejected():
    backend = ScriptedBackend([b"x"])
    recorded = ProcessIdentity(pid=4242, created_at_filetime=1)
    conflicting = ProcessIdentity(pid=4242, created_at_filetime=2)
    runtime = PtyRuntime(TID, backend, identity=recorded, require_startup_gate=True)
    gate = UnverifiedOwnershipGate(
        ProcessOwnershipEvidence(
            assigned=True,
            atomic_with_spawn=True,
            identity=conflicting,
            handle_bound_for_cleanup=True,
            guard="scripted",
        )
    )
    with pytest.raises(OwnershipGateError):
        runtime.start(rows=10, cols=40, gate=gate)
    assert runtime.state is RuntimeState.CREATED


def test_missing_gate_with_declared_guard_rejected():
    backend = ScriptedBackend([b"x"])
    runtime = PtyRuntime(TID, backend, require_startup_gate=True, terminator=RecordingTreeGuard())
    with pytest.raises(OwnershipGateError):
        runtime.start(rows=10, cols=40, gate=None)


# ---------------------------------------------------------------------------
# drain：EOF / alive / 结束原因分类
# ---------------------------------------------------------------------------


def test_drain_captures_tail_after_alive_false():
    backend = ScriptedBackend([b"tail-bytes"], alive_returns=False, exit_code=7)
    runtime = PtyRuntime(TID, backend, terminator=None)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    info = runtime.poll_exit()
    assert info.drain_stop_reason is DrainStopReason.EOF
    assert info.channel_eof is True
    assert info.output_complete is True
    assert info.reader_done is True
    assert info.process_exit_seen is True
    assert info.code == 7
    page = runtime.read_from(0)
    assert b"".join(c.data for c in page.chunks) == b"tail-bytes"


def test_alive_false_is_not_a_stop_condition_without_eof():
    backend = ScriptedBackend([], empty_reads=True, alive_returns=False)
    runtime = PtyRuntime(TID, backend, eof_grace=0.05)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    info = runtime.poll_exit()
    assert info.drain_stop_reason is DrainStopReason.EOF_TIMEOUT
    assert info.channel_eof is False
    assert info.output_complete is False


def test_channel_error_classified_not_eof():
    backend = ScriptedBackend([], channel_error="boom")
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    info = runtime.poll_exit()
    assert info.drain_stop_reason is DrainStopReason.CHANNEL_ERROR
    assert info.channel_eof is False
    assert info.output_complete is False
    assert "boom" in (info.channel_error or "")


def test_stop_requested_not_output_complete():
    backend = ScriptedBackend([b"a"], empty_reads=True, alive_returns=True)
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    runtime.request_drain_stop()
    assert runtime.wait_eof(2.0)
    info = runtime.poll_exit()
    assert info.drain_stop_reason is DrainStopReason.STOP_REQUESTED
    assert info.output_complete is False
    assert info.channel_eof is False


def test_cancelled_is_not_eof():
    backend = ScriptedBackend([], block_forever=True, abort_on_release=True)
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    report = runtime.close(interrupt=False, terminate_timeout=1.0, reader_grace=0.3)
    assert report.terminate_result is TerminateOutcome.RETURNED
    assert report.cancel_kind == "backend-close"
    assert report.reader_cancelled is True
    assert report.reader_converged is True
    assert report.state_after is RuntimeState.EXITED
    assert report.ok
    info = runtime.poll_exit()
    assert info.drain_stop_reason is DrainStopReason.CANCELLED
    assert info.channel_eof is False
    assert info.output_complete is False


def test_channel_eof_while_process_alive_marks_reader_stopped():
    backend = ScriptedBackend([b"z"], alive_returns=True)
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    info = runtime.poll_exit()
    assert info.reader_done is True
    assert info.process_exit_seen is False
    assert info.reason == "reader-stopped:eof"


# ---------------------------------------------------------------------------
# 清理：失败保留 owner、重试、取消无效、关闭顺序
# ---------------------------------------------------------------------------


def test_terminate_failure_retains_owner_then_retry_converges():
    backend = ScriptedBackend([], block_forever=True, fail_terminate=True)
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    report = runtime.close(interrupt=False, terminate_timeout=0.5, reader_grace=0.2)
    assert report.terminate_result is TerminateOutcome.ERROR
    assert report.state_after is RuntimeState.CLEANUP_FAILED
    assert report.owner_retained is True
    assert report.backend_closed is False
    assert backend.closed is False  # 未取消、未释放句柄
    assert report.cancel_kind == "skipped-terminate-failed"

    backend.fail_terminate = False
    report2 = runtime.close(interrupt=False, terminate_timeout=1.0, reader_grace=0.3)
    assert report2.state_after is RuntimeState.EXITED
    assert report2.ok
    assert backend.closed is True
    assert len(backend.terminate_calls) == 2


def test_ineffective_cancel_retains_owner_then_retry_converges():
    backend = ScriptedBackend([], block_forever=True, cancel_on_close=False)
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    report = runtime.close(interrupt=False, terminate_timeout=0.5, reader_grace=0.15)
    assert report.terminate_result is TerminateOutcome.RETURNED
    assert report.reader_cancelled is True
    assert report.cancel_kind == "backend-close"  # 调用成功但未取消阻塞读
    assert report.reader_converged is False
    assert report.state_after is RuntimeState.CLEANUP_FAILED
    assert report.owner_retained is True

    backend.release_blocked_read()
    assert runtime.wait_eof(1.0)
    report2 = runtime.close(interrupt=False, terminate_timeout=0.5, reader_grace=0.3)
    assert report2.state_after is RuntimeState.EXITED
    assert report2.ok


def test_tree_remaining_blocks_cancel_and_keeps_owner():
    guard = RecordingTreeGuard(owned=(4242, 9001), remaining=(9001,))
    backend = ScriptedBackend([], block_forever=True, cancel_on_close=False)
    runtime = PtyRuntime(TID, backend, terminator=guard)
    runtime.start(rows=10, cols=40)
    report = runtime.close(interrupt=False, terminate_timeout=0.5, reader_grace=0.15)
    assert report.tree_owned_pids == (4242, 9001)
    assert report.tree_remaining_pids == (9001,)
    assert report.cancel_kind == "skipped-terminate-failed"
    assert report.reader_cancelled is False
    assert backend.closed is False
    assert report.state_after is RuntimeState.CLEANUP_FAILED
    assert report.owner_retained is True
    backend.release_blocked_read()
    assert runtime.wait_eof(1.0)


def test_close_order_identity_owned_interrupt_terminate_tree_close():
    events: list[str] = []
    backend = ScriptedBackend(
        [], block_forever=True, alive_returns=True, alive_after_terminate=False, events=events
    )
    guard = RecordingTreeGuard(owned=(4242,), remaining=(), events=events)
    identity = ProcessIdentity(pid=4242, created_at_filetime=777)

    def probe(pid: int) -> ProcessIdentity:
        events.append("probe")
        return identity

    runtime = PtyRuntime(
        TID, backend, terminator=guard, identity=identity, identity_probe=probe
    )
    runtime.start(rows=10, cols=40)
    report = runtime.close(
        reason="order-test",
        interrupt=True,
        interrupt_delay=0.0,
        terminate_timeout=1.0,
        reader_grace=0.3,
    )
    assert report.ok
    ordered = [
        name
        for name in events
        if name in ("probe", "owned", "write", "terminate", "terminate_tree", "remaining", "close")
    ]
    assert ordered.index("probe") < ordered.index("owned")
    assert ordered.index("owned") < ordered.index("write")  # 快照先于中断/终止
    assert ordered.index("write") < ordered.index("terminate")
    assert ordered.index("terminate") < ordered.index("terminate_tree")
    assert ordered.index("terminate_tree") <= ordered.index("remaining")
    assert ordered.index("remaining") < ordered.index("close")


def test_close_without_start_is_safe_and_idempotent():
    backend = ScriptedBackend([])
    runtime = PtyRuntime(TID, backend)
    report = runtime.close(interrupt=False)
    assert report.ok
    assert report.cancel_kind == "reader-not-started"
    assert report.state_after is RuntimeState.EXITED
    report2 = runtime.close(interrupt=False)
    assert report2.state_after is RuntimeState.EXITED
    assert len(backend.terminate_calls) == 1


def test_concurrent_close_is_serialized():
    backend = ScriptedBackend([b"x"])
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    reports = []
    barrier = threading.Barrier(2)

    def closer() -> None:
        barrier.wait()
        reports.append(runtime.close(interrupt=False))

    threads = [threading.Thread(target=closer) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert len(reports) == 2
    assert all(report.state_after is RuntimeState.EXITED for report in reports)
    assert len(backend.terminate_calls) == 1


def test_write_and_resize_after_exit():
    backend = ScriptedBackend([b"x"])
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    assert runtime.close(interrupt=False).ok
    assert runtime.state is RuntimeState.EXITED
    with pytest.raises(IllegalStateTransition):
        runtime.write(b"x")
    assert runtime.resize(30, 90) is False


def test_wait_exit_reports_process_exit_and_code():
    backend = ScriptedBackend([b"tail"], alive_returns=False, exit_code=3)
    runtime = PtyRuntime(TID, backend, eof_grace=1.0)
    runtime.start(rows=10, cols=40)
    info = runtime.wait_exit(2.0)
    assert info.process_exit_seen is True
    assert info.code == 3
    assert runtime.wait_eof(1.0)


# ---------------------------------------------------------------------------
# r2 回归：树所有权与根存活（先失败后通过）
# ---------------------------------------------------------------------------


def test_root_alive_after_noop_terminate_blocks_cancel_then_retry_converges():
    backend = ScriptedBackend(
        [], block_forever=True, alive_returns=True, alive_after_terminate=True
    )
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    first = runtime.close(interrupt=False, terminate_timeout=0.5, reader_grace=0.15)
    assert first.terminate_result is TerminateOutcome.RETURNED  # terminate 返回 ≠ 根死
    assert first.state_after is RuntimeState.CLEANUP_FAILED
    assert first.owner_retained is True
    assert first.reader_cancelled is False  # 根仍活：不得取消 reader
    assert backend.closed is False

    backend.set_alive(False)  # 外部世界确认根退出后重试
    second = runtime.close(interrupt=False, terminate_timeout=0.5, reader_grace=0.3)
    assert second.state_after is RuntimeState.EXITED
    assert second.ok


def test_empty_tree_snapshot_still_calls_terminate_tree_and_blocks_when_root_alive():
    events: list[str] = []
    guard = RecordingTreeGuard(owned=(), remaining=(), events=events)
    backend = ScriptedBackend(
        [b"x"], alive_returns=True, alive_after_terminate=True, events=events
    )
    runtime = PtyRuntime(TID, backend, terminator=guard)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    first = runtime.close(interrupt=False, terminate_timeout=0.5, reader_grace=0.15)
    # 空快照不再跳过整树终止：真实 guard 可（根死后）枚举整个 Job。
    assert "terminate_tree" in events
    assert first.tree_owned_pids == ()
    assert first.state_after is RuntimeState.CLEANUP_FAILED  # 根仍活：不得 EXITED
    assert first.reader_cancelled is False

    backend.set_alive(False)
    second = runtime.close(interrupt=False, terminate_timeout=0.5, reader_grace=0.3)
    assert second.state_after is RuntimeState.EXITED
    assert second.ok


def test_owned_snapshot_error_is_fail_closed():
    events: list[str] = []
    guard = RecordingTreeGuard(owned=(), events=events, owned_error="snapshot boom")
    backend = ScriptedBackend([], block_forever=True)
    runtime = PtyRuntime(TID, backend, terminator=guard)
    runtime.start(rows=10, cols=40)
    report = runtime.close(interrupt=False, terminate_timeout=0.5, reader_grace=0.15)
    assert report.state_after is RuntimeState.CLEANUP_FAILED
    assert report.owner_retained is True
    assert report.reader_cancelled is False
    assert report.cancel_kind == "skipped-terminate-failed"
    assert "snapshot failed" in (report.error or "")
    assert "terminate_tree" not in events  # 所有权未知：不做基于 guard 的树终止
    backend.release_blocked_read()
    assert runtime.wait_eof(1.0)


def test_remaining_probe_error_is_fail_closed():
    guard = RecordingTreeGuard(owned=(4242,), remaining_error="remaining boom")
    backend = ScriptedBackend([], block_forever=True)
    runtime = PtyRuntime(TID, backend, terminator=guard)
    runtime.start(rows=10, cols=40)
    report = runtime.close(interrupt=False, terminate_timeout=0.5, reader_grace=0.15)
    assert report.tree_remaining_pids == (4242,)  # 无法核对：保守按仍存活
    assert report.state_after is RuntimeState.CLEANUP_FAILED
    assert report.owner_retained is True
    assert report.reader_cancelled is False
    backend.release_blocked_read()
    assert runtime.wait_eof(1.0)


def test_terminate_tree_return_adopted():
    guard = RecordingTreeGuard(
        owned=(4242,), remaining=(), terminate_owned=(4242, 7001), terminate_remaining=()
    )
    backend = ScriptedBackend([b"x"], alive_after_terminate=False)
    runtime = PtyRuntime(TID, backend, terminator=guard)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    report = runtime.close(interrupt=False, terminate_timeout=0.5)
    # 采用 guard 返回值：根死后 guard 仍能枚举整个 Job（含孙进程）。
    assert report.tree_owned_pids == (4242, 7001)
    assert report.tree_remaining_pids == ()
    assert report.state_after is RuntimeState.EXITED
    assert report.ok


# ---------------------------------------------------------------------------
# r2 回归：有界清理 worker（重试不重叠、不阻塞主线程）
# ---------------------------------------------------------------------------


def test_blocked_terminate_worker_reused_across_retries():
    backend = ScriptedBackend(
        [], block_forever=True, terminate_delay=0.4, alive_after_terminate=False
    )
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    first = runtime.close(interrupt=False, terminate_timeout=0.1, reader_grace=0.1)
    assert first.terminate_result is TerminateOutcome.TIMED_OUT
    assert first.state_after is RuntimeState.CLEANUP_FAILED
    assert first.owner_retained is True
    assert len(backend.terminate_calls) == 1

    second = runtime.close(interrupt=False, terminate_timeout=1.0, reader_grace=0.3)
    assert len(backend.terminate_calls) == 1  # 复用同一 worker：重试不重叠
    assert second.terminate_result is TerminateOutcome.RETURNED
    assert second.state_after is RuntimeState.EXITED
    assert second.ok


def test_blocked_backend_close_is_bounded_then_retry_converges():
    close_gate = threading.Event()
    backend = ScriptedBackend([], block_forever=True, close_gate=close_gate)
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    first = runtime.close(
        interrupt=False, terminate_timeout=0.5, reader_grace=0.15, handle_close_timeout=0.15
    )
    assert first.cancel_kind == "backend-close-timeout"  # 有界：未无限等待
    assert first.reader_cancelled is True
    assert first.reader_converged is False
    assert first.state_after is RuntimeState.CLEANUP_FAILED
    assert first.owner_retained is True

    close_gate.set()  # 外部解除阻塞：close worker 完成并解除阻塞读
    assert runtime.wait_eof(2.0)
    second = runtime.close(
        interrupt=False, terminate_timeout=0.5, reader_grace=0.3, handle_close_timeout=1.0
    )
    assert second.state_after is RuntimeState.EXITED
    assert second.ok
    assert backend.closed is True


def test_late_write_and_resize_rejected_during_close():
    backend = ScriptedBackend([b"x"], terminate_delay=0.3, alive_after_terminate=False)
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    closed = threading.Event()
    reports = []

    def closer() -> None:
        reports.append(runtime.close(interrupt=False, terminate_timeout=2.0))
        closed.set()

    thread = threading.Thread(target=closer)
    thread.start()
    deadline = time.monotonic() + 2.0
    while runtime.state is not RuntimeState.EXITING and time.monotonic() < deadline:
        time.sleep(0.005)
    assert runtime.state is RuntimeState.EXITING
    with pytest.raises(IllegalStateTransition):
        runtime.write(b"late")
    assert runtime.resize(20, 60) is False
    assert closed.wait(5.0)
    thread.join(5)
    assert reports[0].ok
    assert all(write != b"late" for write in backend.writes)


# ---------------------------------------------------------------------------
# r2 回归：输出消费者（绝对 seq 供料、失败有界脱敏）
# ---------------------------------------------------------------------------


def test_output_consumer_receives_absolute_seq_in_order():
    seen: list[tuple[int, bytes]] = []
    backend = ScriptedBackend([b"a", b"bc", b"d"])
    runtime = PtyRuntime(TID, backend, output_consumer=lambda seq, data: seen.append((seq, data)))
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    assert seen == [(0, b"a"), (1, b"bc"), (3, b"d")]  # 绝对 seq：emulator 可检测 gap
    assert b"".join(c.data for c in runtime.read_from(0).chunks) == b"abcd"


def test_consumer_gap_visible_via_absolute_seq():
    seen: list[int] = []

    def failing_once(seq: int, data: bytes) -> None:
        seen.append(seq)
        if len(seen) == 1:
            raise RuntimeError("feed hiccup")

    backend = ScriptedBackend([b"aa", b"bb"])
    runtime = PtyRuntime(TID, backend, output_consumer=failing_once)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    # 后续块仍带绝对 seq 投递：失败块留下的位置缺口对 emulator 可见（可降级/重对齐）。
    assert seen == [0, 2]
    assert runtime.consumer_failure_count == 1
    assert runtime.consumer_failed is True


def test_consumer_failures_bounded_and_redacted():
    sentinel = "SENTINEL-DO-NOT-LEAK-9f3a"

    def bad_consumer(seq: int, data: bytes) -> None:
        raise RuntimeError(f"boom {sentinel}")

    backend = ScriptedBackend([b"x"] * 20)
    runtime = PtyRuntime(TID, backend, output_consumer=bad_consumer)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    assert runtime.consumer_failure_count == 20  # 计数完整
    assert runtime.consumer_failed is True
    assert len(runtime.consumer_errors) <= 8  # 有界
    assert runtime.consumer_errors == ("RuntimeError",)  # 只有脱敏类型名
    assert sentinel not in repr(runtime.consumer_errors)
    assert sentinel not in repr(runtime.exit.as_dict())  # 诊断记录不落秘密


def test_consumer_error_types_bounded_across_exception_kinds():
    counter = {"n": 0}
    error_types = [RuntimeError, ValueError, OSError, TypeError]

    def bad_consumer(seq: int, data: bytes) -> None:
        counter["n"] += 1
        raise error_types[counter["n"] % 4]("x")

    backend = ScriptedBackend([b"y"] * 12)
    runtime = PtyRuntime(TID, backend, output_consumer=bad_consumer)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    assert runtime.consumer_failure_count == 12
    assert set(runtime.consumer_errors) == {"RuntimeError", "ValueError", "OSError", "TypeError"}
    assert len(runtime.consumer_errors) <= 8


def test_no_consumer_is_fine_and_consumer_is_optional():
    backend = ScriptedBackend([b"x"])
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    assert runtime.consumer_errors == ()
    assert runtime.consumer_failure_count == 0
    assert runtime.consumer_failed is False
