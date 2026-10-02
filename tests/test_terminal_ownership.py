"""Pan Terminal P0：所有权策略、四证据启动门禁与清理身份核验（跨平台）。

覆盖契约报告 M16/M17 与增补：fail-closed 工厂拒绝条件、``lifecycle_owner``
布局中立、四要素缺一拒绝且状态保持 created、身份不匹配/探针失败拒杀、
``ProcessIdentity.matches`` fail-closed、detach 需要真实宿主能力（Unsupported
而非假实现）、生命周期策略的服务关闭/重连语义。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.terminal.contracts import (
    DetachReport,
    DetachUnsupportedError,
    ExternalOwnershipNotYetValidated,
    IdentityCheck,
    IllegalStateTransition,
    OwnershipGateError,
    OwnershipMode,
    OwnershipPolicy,
    OwnershipPolicyRequired,
    ProcessIdentity,
    ProcessOwnershipEvidence,
    RuntimeState,
    TerminateOutcome,
    UnownedTreeRejected,
)
from packages.core.terminal.ownership import (
    NullTreeGuard,
    UnverifiedOwnershipGate,
    build_runtime,
)
from packages.core.terminal.runtime import PtyRuntime


def _load_runtime_support():
    """复用 test_terminal_runtime.py 中的 ScriptedBackend / RecordingTreeGuard。"""
    path = Path(__file__).resolve().parent / "test_terminal_runtime.py"
    spec = importlib.util.spec_from_file_location("_terminal_runtime_support_ownership", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_SUPPORT = _load_runtime_support()
ScriptedBackend = _SUPPORT.ScriptedBackend
RecordingTreeGuard = _SUPPORT.RecordingTreeGuard

TID = "term_own00000000000001"
IDENTITY = ProcessIdentity(pid=4242, created_at_filetime=134000000000000000)


def _service_policy(**overrides) -> OwnershipPolicy:
    fields = dict(
        mode=OwnershipMode.SERVICE,
        lifecycle_owner="pan-service",
        tree_guard_kind="job-object",
        tree_guard=NullTreeGuard(),
    )
    fields.update(overrides)
    return OwnershipPolicy(**fields)


class FakeDetachHandler:
    """测试用宿主能力：只记录调用，不做真实移交。"""

    def __init__(self) -> None:
        self.calls = 0

    def request_detach(self, runtime: PtyRuntime) -> DetachReport:
        self.calls += 1
        return DetachReport(
            terminal_id=runtime.terminal_id,
            detached_at=123.0,
            durable_owner="fake-host",
            reconnect_hint={"terminal_id": runtime.terminal_id},
        )


# ---------------------------------------------------------------------------
# fail-closed 工厂
# ---------------------------------------------------------------------------


def test_build_runtime_requires_policy():
    with pytest.raises(OwnershipPolicyRequired):
        build_runtime(TID, ScriptedBackend([]), ownership=None)


def test_build_runtime_rejects_external_mode():
    policy = OwnershipPolicy(
        mode=OwnershipMode.EXTERNAL, lifecycle_owner="external-host", tree_guard_kind="none"
    )
    with pytest.raises(ExternalOwnershipNotYetValidated):
        build_runtime(TID, ScriptedBackend([]), ownership=policy, acknowledge_unowned_tree=True)


def test_build_runtime_rejects_detached_without_host_capability():
    detached_policy = OwnershipPolicy(
        mode=OwnershipMode.DETACHED,
        lifecycle_owner="runner",
        tree_guard_kind="job-object",
        tree_guard=NullTreeGuard(),
        detached=True,
    )
    with pytest.raises(DetachUnsupportedError):
        build_runtime(TID, ScriptedBackend([]), ownership=detached_policy)
    flagged_service = _service_policy(detached=True)
    with pytest.raises(DetachUnsupportedError):
        build_runtime(TID, ScriptedBackend([]), ownership=flagged_service)


def test_build_runtime_rejects_declared_guard_without_implementation():
    policy = _service_policy(tree_guard=None)
    with pytest.raises(UnownedTreeRejected):
        build_runtime(TID, ScriptedBackend([]), ownership=policy)
    runtime = build_runtime(
        TID, ScriptedBackend([]), ownership=policy, acknowledge_unowned_tree=True
    )
    assert runtime.state is RuntimeState.CREATED


def test_lifecycle_owner_is_declarative_and_layout_neutral():
    for owner in ("pan-service", "runner", "external-host"):
        backend = ScriptedBackend([b"x"])
        policy = _service_policy(lifecycle_owner=owner, tree_guard=None, tree_guard_kind="none")
        runtime = build_runtime(
            TID, backend, ownership=policy, acknowledge_unowned_tree=True
        )
        runtime.start(rows=10, cols=40)
        assert runtime.wait_eof(2.0)
        assert runtime.close(interrupt=False).ok


def test_describe_reports_policy_facts_honestly():
    policy = _service_policy()
    described = policy.describe()
    assert described["mode"] == "service"
    assert described["tree_guard"]["kind"] == "none"
    assert described["tree_guard"]["os_level_guard"] is False
    assert NullTreeGuard().owned_pids(123) == []


# ---------------------------------------------------------------------------
# 四证据启动门禁
# ---------------------------------------------------------------------------


def test_gate_requires_all_four_elements():
    cases = [
        dict(assigned=False, atomic_with_spawn=True, identity=IDENTITY, handle_bound_for_cleanup=True),
        dict(assigned=True, atomic_with_spawn=False, identity=IDENTITY, handle_bound_for_cleanup=True),
        dict(assigned=True, atomic_with_spawn=True, identity=None, handle_bound_for_cleanup=True),
        dict(assigned=True, atomic_with_spawn=True, identity=IDENTITY, handle_bound_for_cleanup=False),
    ]
    for case in cases:
        evidence = ProcessOwnershipEvidence(guard="scripted", **case)
        with pytest.raises(OwnershipGateError):
            UnverifiedOwnershipGate(evidence).verify()
    UnverifiedOwnershipGate(
        ProcessOwnershipEvidence(
            assigned=True,
            atomic_with_spawn=True,
            identity=IDENTITY,
            handle_bound_for_cleanup=True,
            guard="scripted",
        )
    ).verify()


def test_assign_failure_keeps_created_and_no_reader():
    backend = ScriptedBackend([b"x"])
    policy = _service_policy(tree_guard=RecordingTreeGuard())
    runtime = build_runtime(TID, backend, ownership=policy)
    failed_gate = UnverifiedOwnershipGate(
        ProcessOwnershipEvidence(
            assigned=False,  # 注入 assign 失败
            atomic_with_spawn=True,
            identity=IDENTITY,
            handle_bound_for_cleanup=True,
            guard="job-object",
        )
    )
    with pytest.raises(OwnershipGateError):
        runtime.start(rows=10, cols=40, gate=failed_gate)
    assert runtime.state is RuntimeState.CREATED
    assert backend.read_calls == 0


# ---------------------------------------------------------------------------
# 清理身份核验（M17）
# ---------------------------------------------------------------------------


def test_identity_mismatch_refuses_termination():
    backend = ScriptedBackend([], block_forever=True)
    runtime = PtyRuntime(
        TID,
        backend,
        identity=ProcessIdentity(pid=4242, created_at_filetime=100),
        identity_probe=lambda pid: ProcessIdentity(pid=4242, created_at_filetime=101),
    )
    runtime.start(rows=10, cols=40)
    report = runtime.close(interrupt=False)
    assert report.terminate_result is TerminateOutcome.REFUSED_IDENTITY_MISMATCH
    assert report.identity_check is IdentityCheck.MISMATCH
    assert report.state_after is RuntimeState.CLEANUP_FAILED
    assert report.owner_retained is True
    assert backend.terminate_calls == []  # 未对不可核验的进程下手
    assert backend.closed is False
    backend.release_blocked_read()
    assert runtime.wait_eof(1.0)


def test_identity_probe_failure_refuses_termination():
    def broken_probe(pid: int):
        raise OSError("probe boom")

    backend = ScriptedBackend([], block_forever=True)
    runtime = PtyRuntime(
        TID, backend, identity=IDENTITY, identity_probe=broken_probe
    )
    runtime.start(rows=10, cols=40)
    report = runtime.close(interrupt=False)
    assert report.identity_check is IdentityCheck.PROBE_FAILED
    assert report.state_after is RuntimeState.CLEANUP_FAILED
    assert backend.terminate_calls == []
    backend.release_blocked_read()
    assert runtime.wait_eof(1.0)


def test_verified_identity_converges():
    backend = ScriptedBackend([b"x"])
    runtime = PtyRuntime(TID, backend, identity=IDENTITY, identity_probe=lambda pid: IDENTITY)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    report = runtime.close(interrupt=False)
    assert report.identity_check is IdentityCheck.VERIFIED
    assert report.ok


def test_probe_none_is_treated_as_gone():
    backend = ScriptedBackend([b"x"])
    runtime = PtyRuntime(TID, backend, identity=IDENTITY, identity_probe=lambda pid: None)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    report = runtime.close(interrupt=False)
    assert report.identity_check is IdentityCheck.VERIFIED
    assert report.ok


def test_process_identity_matches_is_fail_closed():
    base = ProcessIdentity(pid=1, created_at_filetime=10)
    assert base.matches(None) is False
    assert ProcessIdentity(pid=None).matches(base) is False
    assert base.matches(ProcessIdentity(pid=1)) is False  # 无可比时间字段
    assert base.matches(ProcessIdentity(pid=2, created_at_filetime=10)) is False
    assert base.matches(ProcessIdentity(pid=1, created_at_filetime=10)) is True
    assert base.matches(ProcessIdentity(pid=1, created_at_filetime=11)) is False
    assert ProcessIdentity(pid=1, created_at=100.0).matches(
        ProcessIdentity(pid=1, created_at=100.5), tolerance=1.0
    ) is True
    assert ProcessIdentity(pid=1, created_at=100.0).matches(
        ProcessIdentity(pid=1, created_at=100.5)
    ) is False


# ---------------------------------------------------------------------------
# detach：需要真实宿主能力
# ---------------------------------------------------------------------------


def test_detach_unsupported_without_host_capability():
    backend = ScriptedBackend([b"x"])
    policy = _service_policy(tree_guard=None, tree_guard_kind="none")
    runtime = build_runtime(TID, backend, ownership=policy, acknowledge_unowned_tree=True)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    with pytest.raises(DetachUnsupportedError):
        runtime.detach()
    assert runtime.detached is False
    assert runtime.state is RuntimeState.RUNNING
    assert backend.terminate_calls == []
    assert backend.closed is False


def test_detach_with_real_host_capability_returns_report():
    handler = FakeDetachHandler()
    policy = OwnershipPolicy(
        mode=OwnershipMode.DETACHED,
        lifecycle_owner="runner",
        tree_guard_kind="job-object",
        tree_guard=NullTreeGuard(),
        detached=True,
        detach_handler=handler,
    )
    backend = ScriptedBackend([b"x"])
    runtime = build_runtime(TID, backend, ownership=policy, require_startup_gate=False)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    report = runtime.detach()
    assert report.durable_owner == "fake-host"
    assert handler.calls == 1
    assert runtime.detached is True
    assert runtime.state is RuntimeState.RUNNING
    assert backend.terminate_calls == []  # detach 不终止 PTY
    assert backend.closed is False


def test_detach_before_running_is_illegal():
    handler = FakeDetachHandler()
    policy = OwnershipPolicy(
        mode=OwnershipMode.DETACHED,
        lifecycle_owner="runner",
        tree_guard_kind="job-object",
        tree_guard=NullTreeGuard(),
        detached=True,
        detach_handler=handler,
    )
    runtime = build_runtime(TID, ScriptedBackend([]), ownership=policy)
    with pytest.raises(IllegalStateTransition):
        runtime.detach()
    assert handler.calls == 0


# ---------------------------------------------------------------------------
# 生命周期策略的关闭/重连语义
# ---------------------------------------------------------------------------


def test_on_service_shutdown_service_closes():
    policy = _service_policy()
    backend = ScriptedBackend([b"x"])
    runtime = build_runtime(TID, backend, ownership=policy, require_startup_gate=False)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    report = policy.on_service_shutdown(runtime)
    assert report.requested_reason == "service-shutdown"
    assert report.ok


def test_on_service_shutdown_refuses_non_service():
    handler = FakeDetachHandler()
    policy = OwnershipPolicy(
        mode=OwnershipMode.DETACHED,
        lifecycle_owner="runner",
        tree_guard_kind="job-object",
        tree_guard=NullTreeGuard(),
        detached=True,
        detach_handler=handler,
    )
    backend = ScriptedBackend([b"x"])
    runtime = build_runtime(TID, backend, ownership=policy, require_startup_gate=False)
    runtime.start(rows=10, cols=40)
    assert runtime.wait_eof(2.0)
    with pytest.raises(DetachUnsupportedError):
        policy.on_service_shutdown(runtime)
    assert runtime.state is RuntimeState.RUNNING
    assert backend.terminate_calls == []


def test_reconnect_hint_only_supported_with_host_capability():
    service = _service_policy()
    assert service.reconnect_hint(TID)["reconnect_supported"] is False
    detached = OwnershipPolicy(
        mode=OwnershipMode.DETACHED,
        lifecycle_owner="runner",
        tree_guard_kind="job-object",
        tree_guard=NullTreeGuard(),
        detached=True,
        detach_handler=FakeDetachHandler(),
    )
    hint = detached.reconnect_hint(TID)
    assert hint["reconnect_supported"] is True
    assert hint["detached"] is True
