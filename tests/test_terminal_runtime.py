"""Pan Terminal P0：PtyRuntime 生命周期、drain 与清理契约（跨平台）。

覆盖契约报告 M4/M5/M6/M12/M13 与实施计划 §7.1/§13，并增补：

- EOF 与 alive 分离（进程已退出的尾部输出仍然读全）；
- 结束原因分类（eof / cancelled / channel-error / eof-timeout / stop-requested，
  只有 eof 才 output_complete）；
- 清理顺序（身份核验 -> 所有权快照 -> 中断 -> 终止 -> 整树 -> drain -> 关句柄）；
- 失败与取消无效：保 owner、可重试、取消只在终止+整树成功后发生；
- 输出消费者（P1 仿真器供料接线）同序投递、异常不拖死 drain。

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

    能复现“``alive()=false`` 但通道里仍有尾部数据”、终止失败、通道错误、
    阻塞读与取消、取消无效等分支。
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
        self._lock = threading.Lock()

    def _record(self, name: str) -> None:
        if self._events is not None:
            with self._events_lock:
                self._events.append(name)

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
        if self.fail_terminate:
            raise RuntimeError("terminate failed (injected)")

    def close(self) -> None:
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
    ) -> None:
        self._owned = list(owned)
        self._remaining = list(remaining)
        self._events = events
        self._lock = threading.Lock()

    def _record(self, name: str) -> None:
        if self._events is not None:
            with self._lock:
                self._events.append(name)

    def describe(self) -> dict[str, Any]:
        return {"kind": "scripted", "os_level_guard": False}

    def owned_pids(self, root_pid: int | None) -> list[int]:
        self._record("owned")
        return list(self._owned)

    def remaining(self, pids: Iterable[int]) -> list[int]:
        self._record("remaining")
        return list(self._remaining)

    def terminate_tree(
        self, root_pid: int | None, *, timeout: float
    ) -> tuple[list[int], list[int]]:
        self._record("terminate_tree")
        return list(self._owned), list(self._remaining)


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
    backend = ScriptedBackend([], block_forever=True, alive_returns=True, events=events)
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
# 输出消费者（P1 仿真器供料接线）
# ---------------------------------------------------------------------------


def test_output_consumer_receives_chunks_in_order():
    seen: list[bytes] = []
    backend = ScriptedBackend([b"a", b"bc", b"d"])
    runtime = PtyRuntime(TID, backend, output_consumer=seen.append)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    assert seen == [b"a", b"bc", b"d"]
    assert b"".join(c.data for c in runtime.read_from(0).chunks) == b"abcd"


def test_output_consumer_error_recorded_but_drain_continues():
    def bad_consumer(data: bytes) -> None:
        raise RuntimeError("consumer boom")

    backend = ScriptedBackend([b"x", b"y"])
    runtime = PtyRuntime(TID, backend, output_consumer=bad_consumer)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    assert runtime.consumer_errors and "consumer boom" in runtime.consumer_errors[0]
    assert b"".join(c.data for c in runtime.read_from(0).chunks) == b"xy"
    assert runtime.poll_exit().output_complete is True


def test_no_consumer_is_fine_and_consumer_is_optional():
    backend = ScriptedBackend([b"x"])
    runtime = PtyRuntime(TID, backend)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    assert runtime.consumer_errors == ()
