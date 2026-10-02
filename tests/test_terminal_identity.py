"""Pan Terminal P1：Windows 身份原语（identity.py）真实内核验证。

覆盖：retained-handle 三态 ProcessProbe（unknown 不等于 dead）；raw64 FILETIME
与跨 JS 字符串边界；259 歧义（exit code == STILL_ACTIVE 而 wait_state 已 dead）；
``kill_verified`` 单句柄核验终止（unknown/错身份不杀）；平台守卫（非 Windows
调用明确 ``BackendUnavailableError``）。

只用自建子进程（subprocess）与内核原语；有界等待；不接触任何既有服务。
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.terminal import identity
from packages.core.terminal.contracts import (
    BackendUnavailableError,
    ProcessIdentity,
    ProcessStatus,
)

PYTHON = sys.executable
WINDOWS = sys.platform == "win32"
pytestmark = pytest.mark.skipif(not WINDOWS, reason="Windows kernel 原语")


# --------------------------------------------------------------------------- helpers
def spawn_child(code: str) -> subprocess.Popen:
    return subprocess.Popen([PYTHON, "-c", code])


def open_handle(pid: int, access: int | None = None) -> int:
    k = identity.kernel32()
    if access is None:
        access = identity.SYNCHRONIZE | identity.PROCESS_QUERY_LIMITED_INFORMATION
    h = k.OpenProcess(access, False, pid)
    assert h, f"OpenProcess 失败（pid={pid}）"
    return int(h)


def wait_gone(pid: int, timeout: float = 8.0) -> bool:
    """诊断等待：进程对象消失（PID 查无）或 signaled。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        h = identity.open_process_for_probe(pid)
        if not h:
            return True  # PID 查无（对象已释放）；见 wait_state 的 fail-closed 语义
        try:
            if identity.wait_state(h) is ProcessStatus.DEAD:
                return True
        finally:
            identity.close_handle_checked(h)
        time.sleep(0.05)
    return False


# --------------------------------------------------------------------------- tests
def test_kernel32_platform_guard(monkeypatch):
    """非 Windows 调用明确 BackendUnavailableError（导入本身无副作用）。"""
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(BackendUnavailableError):
        identity.kernel32()
    with pytest.raises(BackendUnavailableError):
        identity.wait_state(1)


def test_probe_alive_identity_and_retained_dead():
    """ALIVE 附身份；终止后 retained handle 给 DEAD（真实 signaled 证据）。"""
    proc = spawn_child("import time; time.sleep(60)")
    try:
        h = open_handle(
            proc.pid,
            identity.SYNCHRONIZE
            | identity.PROCESS_QUERY_LIMITED_INFORMATION
            | identity.PROCESS_TERMINATE,
        )
        try:
            probe = identity.probe_handle(h, pid=proc.pid)
            assert probe.status is ProcessStatus.ALIVE
            assert probe.identity is not None
            assert probe.identity.pid == proc.pid
            assert isinstance(probe.identity.created_at_filetime, int)
            assert probe.identity.created_at_filetime > 0

            # 按 PID 探针同样 ALIVE 且身份一致（FILETIME 精确相等）
            pid_probe = identity.probe_process(proc.pid)
            assert pid_probe.status is ProcessStatus.ALIVE
            assert pid_probe.identity is not None
            assert pid_probe.identity.created_at_filetime == probe.identity.created_at_filetime

            # 终止（仅自建子进程，经同一 handle）-> retained handle DEAD
            assert identity.kernel32().TerminateProcess(wintypes.HANDLE(h), 7), (
                "TerminateProcess 失败"
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and identity.wait_state(h) is not ProcessStatus.DEAD:
                time.sleep(0.02)
            dead = identity.probe_handle(h, pid=proc.pid)
            assert dead.status is ProcessStatus.DEAD
        finally:
            identity.close_handle_checked(h)
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_probe_unknown_is_not_dead():
    """查不到/打不开/以及“现查陌生 PID 的 signaled”一律 UNKNOWN，绝不当 dead（r3 §13.4）。"""
    # PID 4（System）不可打开：open 失败 -> UNKNOWN
    probe = identity.probe_process(4)
    assert probe.status is ProcessStatus.UNKNOWN
    # 非法 handle：wait_state -> UNKNOWN
    assert identity.wait_state(0) is ProcessStatus.UNKNOWN

    # 已退出但对象仍被我们持有：retained handle 给 DEAD（合法证据）；
    # 现查同 PID 的 fresh handle 即使 signaled 也只能给 UNKNOWN（不得放行）
    proc = spawn_child("import time; time.sleep(30)")
    handle = open_handle(
        proc.pid,
        identity.SYNCHRONIZE
        | identity.PROCESS_QUERY_LIMITED_INFORMATION
        | identity.PROCESS_TERMINATE,
    )
    try:
        assert identity.kernel32().TerminateProcess(wintypes.HANDLE(handle), 7)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and identity.wait_state(handle) is not ProcessStatus.DEAD:
            time.sleep(0.02)
        retained = identity.probe_handle(handle, pid=proc.pid)
        assert retained.status is ProcessStatus.DEAD  # retained handle：允许
        fresh = identity.probe_process(proc.pid)
        assert fresh.status is ProcessStatus.UNKNOWN  # fresh handle signaled：不放行
        assert "retained" in fresh.detail or "DEAD 证据" in fresh.detail
        proc.wait(timeout=5)
    finally:
        identity.close_handle_checked(handle)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    # 对象彻底释放后同样 UNKNOWN（有界轮询等待对象释放）
    gone = identity.probe_process(proc.pid)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and gone.status is not ProcessStatus.UNKNOWN:
        time.sleep(0.05)
        gone = identity.probe_process(proc.pid)
    assert gone.status is ProcessStatus.UNKNOWN
    assert gone.status is not ProcessStatus.DEAD


def test_exit_code_259_ambiguity_with_retained_handle():
    """真实退出码 259 且 retained handle：wait_state=DEAD，而信息字段读 259。"""
    proc = spawn_child("import sys; sys.exit(259)")
    try:
        h = open_handle(proc.pid)
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and identity.wait_state(h) is not ProcessStatus.DEAD:
                time.sleep(0.02)
            assert identity.wait_state(h) is ProcessStatus.DEAD
            assert identity.informational_exit_code(h) == 259
            probe = identity.probe_handle(h, pid=proc.pid)
            assert probe.status is ProcessStatus.DEAD  # signaled 语义，不靠 259
        finally:
            identity.close_handle_checked(h)
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_kill_verified_refusals_and_success():
    """kill_verified：unknown/错身份不终止；精确匹配才杀（单句柄）。"""
    proc = spawn_child("import time; time.sleep(60)")
    try:
        h = open_handle(proc.pid)
        try:
            probe = identity.probe_handle(h, pid=proc.pid)
            assert probe.status is ProcessStatus.ALIVE and probe.identity is not None
            filetime = probe.identity.created_at_filetime
        finally:
            identity.close_handle_checked(h)

        # 1) 期望身份缺失 -> 拒绝
        refused = identity.kill_verified(proc.pid, None)
        assert refused.killed is False and refused.reason == "expected_identity_missing"

        # 2) FILETIME 差 1 -> identity_mismatch，目标保持存活
        mismatch = identity.kill_verified(proc.pid, int(filetime) + 1)
        assert mismatch.killed is False and mismatch.reason == "identity_mismatch"
        assert proc.poll() is None

        # 3) 打不开（PID 4 System）-> open_failed，不终止
        unopenable = identity.kill_verified(4, 0)
        assert unopenable.killed is False and unopenable.reason == "open_failed"

        # 4) 精确身份 -> 终止并 signaled
        killed = identity.kill_verified(
            proc.pid, ProcessIdentity(pid=proc.pid, created_at_filetime=filetime)
        )
        assert killed.killed is True and killed.reason == "terminated"
        assert killed.signaled_after_terminate is True
        assert killed.as_dict()["expected_filetime"] == str(filetime)
        assert killed.as_dict()["observed_filetime"] == str(filetime)

        # 5) 已退出 -> not_running（拒绝重复终止）
        again = identity.kill_verified(
            proc.pid, ProcessIdentity(pid=proc.pid, created_at_filetime=filetime)
        )
        assert again.killed is False and again.reason in ("not_running", "open_failed")
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_filetime_json_is_string_and_exact():
    """跨 JS 输出 FILETIME 一律字符串（raw64 超出 JS 安全整数）。"""
    raw = 134354391933373234  # 实测量级（> 2**53）
    assert raw > 2**53
    assert identity.filetime_json(raw) == str(raw)
    assert identity.filetime_json(None) is None
    ident = ProcessIdentity(pid=123, created_at_filetime=raw)
    payload = identity.identity_json(ident)
    assert payload is not None and isinstance(payload["created_at_filetime"], str)
    dumped = json.loads(json.dumps(payload))
    assert dumped["created_at_filetime"] == str(raw)
    assert int(dumped["created_at_filetime"]) == raw  # 精确往返
    # kill_verified 结果也走字符串（见 test_kill_verified_refusals_and_success）


def test_close_handle_checked_and_handle_count():
    """CloseHandle 返回值检查（真实失败入参）与句柄计数可读。"""
    # 无效句柄值：CloseHandle 失败（真实 ERROR_INVALID_HANDLE），不冒充成功
    assert identity.close_handle_checked(0xDEAD0000) is False
    proc = spawn_child("import time; time.sleep(30)")
    try:
        h = open_handle(proc.pid)
        assert identity.close_handle_checked(h) is True
        count = identity.get_process_handle_count()
        assert isinstance(count, int) and count > 0
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_cancel_synchronous_io_no_pending_returns_not_found():
    """无挂起同步 I/O 时 CancelSynchronousIo 返回 False/ERROR_NOT_FOUND（真实行为）。"""
    handle = identity.open_current_thread_handle()
    try:
        ok, err = identity.cancel_synchronous_io(handle)
        assert ok is False
        assert err == identity.ERROR_NOT_FOUND
    finally:
        identity.close_handle_checked(handle)
