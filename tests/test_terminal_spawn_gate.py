"""Pan Terminal P1：原子 spawn 与四证据启动门禁（spawn_win.py）真实内核验证。

覆盖（任务卡）：挂起创建期间子进程从未执行（无副作用）；assign 失败 /
成员关系未证明 / 成员查询失败 / 异常 resume（prev=0、prev=2）一律 fail-closed
（不 resume、不发布、只清理自建资源）；清理部分失败保留 owned 资源并可重试；
四要素证据装配 ``UnverifiedOwnershipGate`` 并驱动 ``build_runtime`` 真实运行；
manifest 边界（失败不发布 backend）。

注入钩子仍调用**真实内核 API 的错误入参**（无效 job/process handle、双重
ResumeThread/SuspendThread 组合），不是“假装失败”；仍属测试构造，不构成对
真实 OS 故障的复现（报告如实标注）。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.terminal import identity
from packages.core.terminal.backend import ConPtyBackend
from packages.core.terminal.contracts import (
    OwnershipGateError,
    OwnershipMode,
    OwnershipPolicy,
    ProcessStatus,
    RuntimeState,
)
from packages.core.terminal.guard import JobObjectGuard
from packages.core.terminal.ownership import UnverifiedOwnershipGate, build_runtime
from packages.core.terminal.spawn_win import (
    SpawnDenied,
    build_command_line,
    build_environment_block,
    spawn_conpty_suspended,
)

PYTHON = sys.executable
WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows ConPTY 原语")


# --------------------------------------------------------------------------- injection helpers
def assign_with_invalid_job_handle(guard, hprocess) -> None:
    """真实 API、无效 job 句柄：AssignProcessToJobObject 失败 -> 抛 OSError。"""
    k = identity.kernel32()
    ctypes.set_last_error(0)
    ok = k.AssignProcessToJobObject(
        wintypes.HANDLE(0xDEAD), wintypes.HANDLE(hprocess)
    )
    if not ok:
        raise OSError(int(ctypes.get_last_error()), "injected invalid job handle")


def membership_against_other_guard(other: JobObjectGuard):
    """真实 API：对**另一个** guard 查询 -> 真实返回 False（未证明成员）。"""

    def check(guard, hprocess) -> bool:
        return other.is_member(hprocess)

    return check


def membership_query_failure(guard, hprocess) -> bool:
    """真实 API、无效进程句柄：IsProcessInJob 查询失败 -> 抛 OSError。"""
    k = identity.kernel32()
    in_job = wintypes.BOOL()
    ctypes.set_last_error(0)
    ok = k.IsProcessInJob(
        wintypes.HANDLE(0xDEAD), wintypes.HANDLE(guard.raw_handle), ctypes.byref(in_job)
    )
    if not ok:
        raise OSError(int(ctypes.get_last_error()), "injected invalid process handle")
    return bool(in_job.value)


def double_resume(hthread) -> int:
    """真实 API：连续两次 ResumeThread -> 第二次返回 0（原子性声明失效）。"""
    k = identity.kernel32()
    first = k.ResumeThread(wintypes.HANDLE(hthread))
    second = k.ResumeThread(wintypes.HANDLE(hthread))
    assert int(first) == 1, f"第一次 ResumeThread 应为 1，得 {int(first)}"
    return int(second)


def suspend_then_resume(hthread) -> int:
    """真实 API：先 SuspendThread 再 ResumeThread -> 返回 2（仍挂起）。"""
    k = identity.kernel32()
    k.SuspendThread(wintypes.HANDLE(hthread))
    return int(k.ResumeThread(wintypes.HANDLE(hthread)))


def marker_child(marker: str) -> list[str]:
    return [
        PYTHON,
        "-c",
        f"open(r'{marker}', 'w').write('ran'); import time; time.sleep(30)",
    ]


def sleep_child() -> list[str]:
    return [PYTHON, "-c", "import time; time.sleep(60)"]


def wait_for_file(path: str, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.05)
    return False


# --------------------------------------------------------------------------- pure logic
def test_env_block_and_command_line_pure_logic():
    """命令行引号与环境块构造（跨平台纯逻辑）。"""
    assert build_command_line(["cmd.exe", "/q", "/d"]) == "cmd.exe /q /d"
    quoted = build_command_line([r"C:\a b\x.exe", "arg with space"])
    assert quoted == '"C:\\a b\\x.exe" "arg with space"'
    assert build_environment_block(None) is None
    block = build_environment_block({"B": "2", "a": "1"})
    assert block == "a=1\x00B=2\x00\x00"
    with pytest.raises(ValueError):
        build_command_line([])
    with pytest.raises(ValueError):
        build_environment_block({"A=B": "x"})


# --------------------------------------------------------------------------- gates
@windows_only
def test_suspended_no_side_effects_and_exactly_one_resume(tmp_path):
    """挂起期子进程从未执行（marker 不存在）；resume 门禁恰一次。"""
    marker = str(tmp_path / "suspended_marker.txt")
    guard = JobObjectGuard()
    attempt = spawn_conpty_suspended(
        marker_child(marker), cwd=str(tmp_path), guard=guard, resume=False
    )
    try:
        ev = attempt.evidence
        assert ev is not None
        assert ev.suspended_created is True
        assert ev.atomic_with_spawn is True and ev.assigned is True
        assert ev.handle_bound_for_cleanup is True
        assert ev.resumed is False and ev.resume_previous_suspend_count is None
        assert isinstance(ev.identity.created_at_filetime, int)

        time.sleep(1.2)
        assert not Path(marker).exists(), "挂起期子进程不得执行（marker 必须不存在）"
        assert identity.wait_state(attempt.h_process) is ProcessStatus.ALIVE

        ok, prev = attempt.resume()
        assert ok is True and prev == 1
        assert wait_for_file(marker) is True
        refused, prev2 = attempt.resume()
        assert refused is False and prev2 is None
        assert attempt.resume_detail["reason"] == "already_resumed"
    finally:
        if attempt.h_process and identity.wait_state(attempt.h_process) is not ProcessStatus.DEAD:
            attempt.terminate_raw()
            attempt.wait_dead(3.0)
        release = attempt.staged_release()
        assert release["closed"] is True
        assert guard.close() is True


@windows_only
def test_assign_failure_fail_closed(tmp_path):
    """assign 失败（真实 ERROR_*）：不 resume、不发布、清理自建资源。"""
    marker = str(tmp_path / "assign_fail_marker.txt")
    guard = JobObjectGuard()
    with pytest.raises(SpawnDenied) as excinfo:
        spawn_conpty_suspended(
            marker_child(marker),
            cwd=str(tmp_path),
            guard=guard,
            assign_impl=assign_with_invalid_job_handle,
        )
    denied = excinfo.value
    assert denied.stage == "assign_guard_job"
    assert denied.cleanup is not None
    release = denied.cleanup["release"]
    assert release["closed"] is True and release["retained"] == []
    assert denied.retryable is False
    time.sleep(0.8)
    assert not Path(marker).exists(), "被拒子进程不得执行"
    assert (guard.active_processes() or 0) == 0
    assert guard.close() is True


@windows_only
def test_membership_false_fail_closed(tmp_path):
    """成员关系真实返回 False：拒绝（不 resume、清理、guard 无残留）。"""
    marker = str(tmp_path / "membership_false_marker.txt")
    guard = JobObjectGuard()
    other = JobObjectGuard()
    try:
        with pytest.raises(SpawnDenied) as excinfo:
            spawn_conpty_suspended(
                marker_child(marker),
                cwd=str(tmp_path),
                guard=guard,
                membership_impl=membership_against_other_guard(other),
            )
        denied = excinfo.value
        assert denied.stage == "verify_guard_membership"
        assert denied.cleanup["release"]["closed"] is True
        time.sleep(0.8)
        assert not Path(marker).exists()
        assert (guard.active_processes() or 0) == 0
    finally:
        assert other.close() is True
        assert guard.close() is True


@windows_only
def test_membership_query_failure_fail_closed(tmp_path):
    """成员查询失败（真实 API 错误入参）：视为未证明 -> fail-closed。"""
    marker = str(tmp_path / "membership_query_marker.txt")
    guard = JobObjectGuard()
    with pytest.raises(SpawnDenied) as excinfo:
        spawn_conpty_suspended(
            marker_child(marker),
            cwd=str(tmp_path),
            guard=guard,
            membership_impl=membership_query_failure,
        )
    assert excinfo.value.stage == "verify_guard_membership"
    assert excinfo.value.cleanup["release"]["closed"] is True
    time.sleep(0.8)
    assert not Path(marker).exists()
    assert (guard.active_processes() or 0) == 0
    assert guard.close() is True


@windows_only
def test_abnormal_resume_prev_zero_cleans_running_child(tmp_path):
    """异常 resume（prev=0=原子性声明失效）：拒绝并终止**已在跑**的自建子进程。"""
    marker = str(tmp_path / "resume_zero_marker.txt")
    guard = JobObjectGuard()
    with pytest.raises(SpawnDenied) as excinfo:
        spawn_conpty_suspended(
            marker_child(marker),
            cwd=str(tmp_path),
            guard=guard,
            resume_impl=double_resume,
        )
    denied = excinfo.value
    assert denied.stage == "resume_thread"
    assert denied.detail["resume_error"] == "unexpected_previous_suspend_count"
    assert denied.detail["resume_return"] == 0
    terminate = denied.cleanup["terminate"]
    assert terminate["confirmed_dead"] is True
    assert denied.cleanup["release"]["closed"] is True
    assert (guard.active_processes() or 0) == 0
    assert guard.close() is True


@windows_only
def test_abnormal_resume_prev_two_never_ran(tmp_path):
    """异常 resume（prev=2=仍挂起）：拒绝；子进程从未执行（marker 不存在）。"""
    marker = str(tmp_path / "resume_two_marker.txt")
    guard = JobObjectGuard()
    with pytest.raises(SpawnDenied) as excinfo:
        spawn_conpty_suspended(
            marker_child(marker),
            cwd=str(tmp_path),
            guard=guard,
            resume_impl=suspend_then_resume,
        )
    denied = excinfo.value
    assert denied.stage == "resume_thread"
    assert denied.detail["resume_return"] == 2
    time.sleep(1.0)
    assert not Path(marker).exists()
    assert denied.cleanup["release"]["closed"] is True
    assert (guard.active_processes() or 0) == 0
    assert guard.close() is True


@windows_only
def test_cleanup_failure_retained_then_retry(tmp_path):
    """清理部分失败：保留 owned 资源（不谎报 closed）-> 重试成功。"""
    guard = JobObjectGuard()
    attempt = spawn_conpty_suspended(sleep_child(), cwd=str(tmp_path), guard=guard)
    try:
        assert attempt.terminate_raw() is True
        assert attempt.wait_dead(3.0) is True
        attempt.inject_release_failure("output_read")
        first = attempt.staged_release()
        assert first["closed"] is False and first["retryable"] is True
        assert "output_read" in first["retained"]
        # 失败不牵连其余资源：它们仍在 retained 清单中
        assert "hpc" in first["retained"] and "h_process" in first["retained"]
        assert attempt.closed is False
        second = attempt.staged_release()
        assert second["closed"] is True and second["retryable"] is False
        assert "output_read" in second["released"]
    finally:
        remaining = attempt.staged_release()
        assert remaining["closed"] is True
        assert guard.close() is True


@windows_only
def test_gate_wiring_and_runtime_start(tmp_path):
    """四要素门禁由真实原语装配：verify 通过；篡改证据拒绝；build_runtime 真跑。"""
    guard = JobObjectGuard()
    attempt = spawn_conpty_suspended(
        [PYTHON, "-c", "import sys; print('gate-ok', flush=True); import time; time.sleep(30)"],
        cwd=str(tmp_path),
        guard=guard,
    )
    try:
        evidence = attempt.evidence
        assert evidence is not None
        gate_ev = evidence.ownership_evidence()
        UnverifiedOwnershipGate(gate_ev).verify()  # 不抛 = 四要素齐备

        # 篡改证据必须被拒绝（bool 不是装饰：缺任一要素 -> OwnershipGateError）
        for tampered in (
            replace(gate_ev, assigned=False),
            replace(gate_ev, atomic_with_spawn=False),
            replace(gate_ev, identity=None),
            replace(gate_ev, handle_bound_for_cleanup=False),
        ):
            with pytest.raises(OwnershipGateError):
                UnverifiedOwnershipGate(tampered).verify()

        # 装配 build_runtime：真实运行 -> 读取输出 -> 关闭收敛
        backend = ConPtyBackend(attempt, guard=guard, owns_guard=False)
        policy = OwnershipPolicy(
            mode=OwnershipMode.SERVICE,
            lifecycle_owner="runner",
            tree_guard_kind="job-object",
            tree_guard=guard,
        )
        runtime = build_runtime(
            "term_gate_wiring",
            backend,
            ownership=policy,
            identity=backend.identity,
            identity_probe=identity.probe_process,
            eof_grace=1.0,
        )
        runtime.start(rows=30, cols=100, gate=backend.gate)
        assert runtime.state is RuntimeState.RUNNING

        deadline = time.monotonic() + 10
        text = ""
        while time.monotonic() < deadline and "gate-ok" not in text:
            page = runtime.read_from(0)
            text = b"".join(c.data for c in page.chunks).decode("utf-8", "replace")
            time.sleep(0.05)
        assert "gate-ok" in text

        report = runtime.close(reason="test-gate")
        assert report.ok is True
        assert runtime.state is RuntimeState.EXITED
    finally:
        release = attempt.staged_release()
        assert release["closed"] is True, release
        assert guard.close() is True


@windows_only
def test_backend_spawn_denied_not_published(tmp_path):
    """失败不发布 backend：不存在的可执行文件 -> SpawnDenied 且清理完毕。"""
    missing = str(tmp_path / "definitely-missing-exe.exe")
    with pytest.raises(SpawnDenied) as excinfo:
        ConPtyBackend.spawn([missing], cwd=str(tmp_path))
    denied = excinfo.value
    assert denied.stage == "create_process_suspended"
    assert denied.cleanup["release"]["closed"] is True
    assert denied.retryable is False
