"""ProcessOwner 双实现契约测试（同一套行为用例跑两份实现）。

- Windows：本机真实 Job Object（``terminal.guard``）+ FILETIME 身份；
- POSIX：真实 ``start_new_session`` + 进程组 ``killpg`` + 持久身份。本机为
  Windows 时**显式 skip**，需要 ubuntu / macos CI 或 POSIX 主机才能证明
  （不是静默跳过）。

全部子进程都是本测试自建的 ``sys.executable`` 探针，有界等待、不接触既有服务。
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.platform.process import (
    IDENTITY_SCHEMA,
    PLATFORM_POSIX,
    PLATFORM_WINDOWS,
    IdentityMismatchError,
    ProcessIdentity,
    ProcessOwner,
    ProcessStatus,
    UnsupportedPlatformError,
    WindowsProcessOwner,
    default_process_owner,
)

# 用真实解释器而不是 uv shim：杀 shim 会连带改变控制台/进程组语义。
PYTHON = getattr(sys, "_base_executable", None) or sys.executable


def _sleep_cmd(seconds: int = 120) -> list[str]:
    return [PYTHON, "-c", f"import time; time.sleep({int(seconds)})"]


def _mismatched_birth(identity: ProcessIdentity) -> str:
    # Exercise a VALID different lifetime, separately from malformed-record
    # negatives; the status/refusal assertions below are unchanged.
    if identity.kind == "proc_uniqueid":
        parts = identity.birth.split(":")
        assert parts[0] == "lifetime-v1" and len(parts) == 4
        parts[1] = str(int(parts[1]) + 1)
        return ":".join(parts)
    return f"{identity.birth}-forged"


@contextlib.contextmanager
def spawned(owner: ProcessOwner, argv: list[str], **kwargs):
    """spawn + 保证清理（kill_tree 失败不影响 close 兜底）。"""
    handle = owner.spawn(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    try:
        yield handle
    finally:
        try:
            owner.kill_tree(handle, timeout=5.0)
        except Exception:  # noqa: BLE001 - 清理尽力而为，断言已在用例内完成
            pass
        handle.close()


def _wait_for(predicate, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class ProcessOwnerContract:
    """平台无关行为契约；子类只负责提供 ``owner`` fixture。"""

    @pytest.fixture
    def owner(self) -> ProcessOwner:  # pragma: no cover - 由子类覆盖
        raise NotImplementedError

    # ------------------------------------------------------------- spawn / identity
    def test_spawn_binds_exact_identity(self, owner: ProcessOwner) -> None:
        with spawned(owner, _sleep_cmd()) as handle:
            assert handle.pid > 0
            assert handle.identity.pid == handle.pid
            assert handle.identity.platform == owner.platform
            assert handle.identity.kind in {"filetime", "starttime", "lstart", "proc_uniqueid"}
            assert handle.identity.birth
            assert owner.is_alive(handle) is ProcessStatus.ALIVE
            check = owner.check(handle.identity)
            assert check.status is ProcessStatus.ALIVE
            assert check.ours is True
            assert check.verified_alive is True

    def test_identity_by_pid_matches_spawn_identity(self, owner: ProcessOwner) -> None:
        with spawned(owner, _sleep_cmd()) as handle:
            fetched = owner.identity(handle.pid)
            assert fetched is not None
            assert fetched.matches(handle.identity)

    def test_identity_of_own_process_is_readable(self, owner: ProcessOwner) -> None:
        mine = owner.identity(os.getpid())
        assert mine is not None
        assert mine.pid == os.getpid()
        assert owner.check(mine).verified_alive is True

    # ------------------------------------------------------------- wait
    def test_wait_returns_exit_code(self, owner: ProcessOwner) -> None:
        with spawned(owner, [PYTHON, "-c", "raise SystemExit(7)"]) as handle:
            result = owner.wait(handle, timeout=30.0)
            assert result.exited is True
            assert result.timed_out is False
            assert result.exit_code == 7

    def test_wait_timeout_does_not_terminate(self, owner: ProcessOwner) -> None:
        with spawned(owner, _sleep_cmd()) as handle:
            result = owner.wait(handle, timeout=0.3)
            assert result.exited is False
            assert result.timed_out is True
            assert owner.is_alive(handle) is ProcessStatus.ALIVE

    def test_wait_after_exit_is_stable(self, owner: ProcessOwner) -> None:
        with spawned(owner, [PYTHON, "-c", "raise SystemExit(3)"]) as handle:
            first = owner.wait(handle, timeout=30.0)
            second = owner.wait(handle, timeout=1.0)
            assert first.exit_code == 3
            assert second.exited is True
            assert second.exit_code == 3

    # ------------------------------------------------------------- terminate
    def test_terminate_stops_process(self, owner: ProcessOwner) -> None:
        handle = owner.spawn(
            _sleep_cmd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            result = owner.terminate(handle, grace=3.0)
            assert result.exited is True
            assert result.remaining == ()
            assert owner.wait(handle, timeout=10.0).exited is True
        finally:
            handle.close()

    def test_terminate_is_idempotent(self, owner: ProcessOwner) -> None:
        handle = owner.spawn(
            _sleep_cmd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            owner.terminate(handle, grace=3.0)
            again = owner.terminate(handle, grace=1.0)
            assert again.exited is True
            assert again.remaining == ()
        finally:
            handle.close()

    def test_kill_tree_removes_grandchild(self, owner: ProcessOwner, tmp_path: Path) -> None:
        script = tmp_path / "tree.py"
        probe = tmp_path / "grand.pid"
        script.write_text(
            "import subprocess, sys, time\n"
            "from pathlib import Path\n"
            "grand = subprocess.Popen([sys.executable, '-c',"
            " 'import time; time.sleep(120)'])\n"
            "Path(sys.argv[1]).write_text(str(grand.pid), encoding='utf-8')\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )
        with spawned(owner, [PYTHON, str(script), str(probe)]) as handle:
            assert _wait_for(lambda: probe.exists() and bool(probe.read_text().strip()), 20.0)
            grandchild_pid = int(probe.read_text(encoding="utf-8").strip())
            grandchild = owner.identity(grandchild_pid)
            assert grandchild is not None
            result = owner.kill_tree(handle, timeout=10.0)
            assert result.confirmed is True
            assert result.remaining == ()
            assert _wait_for(lambda: owner.identity(grandchild_pid) is None, 10.0)
            after = owner.check(grandchild)
            assert after.status is not ProcessStatus.ALIVE

    # ------------------------------------------------------------- identity refusal
    def test_check_detects_reused_pid(self, owner: ProcessOwner) -> None:
        with spawned(owner, _sleep_cmd()) as handle:
            forged = dataclasses.replace(
                handle.identity, birth=_mismatched_birth(handle.identity)
            )
            check = owner.check(forged)
            assert check.status is ProcessStatus.ALIVE
            assert check.ours is False
            assert forged.matches(handle.identity) is False

    def test_recover_refuses_mismatched_identity(self, owner: ProcessOwner) -> None:
        other = type(owner)()
        with spawned(owner, _sleep_cmd()) as handle:
            forged = dataclasses.replace(
                handle.identity, birth=_mismatched_birth(handle.identity)
            )
            record = {
                "schema": IDENTITY_SCHEMA,
                "platform": owner.platform,
                **forged.as_dict(),
            }
            assert other.verify(record).verified_alive is False
            result = other.recover(record, terminate=True, grace=1.0, timeout=2.0)
            assert result.action == "refused_identity"
            assert result.terminated is False
            assert owner.is_alive(handle) is ProcessStatus.ALIVE

    # ------------------------------------------------------------- recovery
    def test_recoverable_identity_roundtrip(self, owner: ProcessOwner) -> None:
        with spawned(owner, _sleep_cmd()) as handle:
            record = owner.recoverable_identity(handle)
            # 记录必须能跨进程持久化（JSON），且能还原成同一身份。
            payload = json.dumps(record)
            restored = ProcessIdentity.from_dict(json.loads(payload))
            assert restored.matches(handle.identity)
            assert ProcessIdentity.from_dict(record).matches(handle.identity)
            check = owner.verify(record)
            assert check.verified_alive is True

    def test_verify_rejects_foreign_platform_record(self, owner: ProcessOwner) -> None:
        with spawned(owner, _sleep_cmd()) as handle:
            record = owner.recoverable_identity(handle)
            record["platform"] = (
                PLATFORM_WINDOWS if owner.platform == PLATFORM_POSIX else PLATFORM_POSIX
            )
            check = owner.verify(record)
            assert check.status is ProcessStatus.UNKNOWN
            assert check.ours is None

    def test_recover_terminates_verified_process(self, owner: ProcessOwner) -> None:
        restarted = type(owner)()
        handle = owner.spawn(
            _sleep_cmd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            record = owner.recoverable_identity(handle)
            result = restarted.recover(record, terminate=True, grace=2.0, timeout=5.0)
            assert result.verified is True
            assert result.action == "terminated"
            assert result.terminated is True
            assert result.remaining == ()
            assert owner.wait(handle, timeout=10.0).exited is True
        finally:
            handle.close()

    def test_recover_can_verify_without_terminating(self, owner: ProcessOwner) -> None:
        restarted = type(owner)()
        with spawned(owner, _sleep_cmd()) as handle:
            record = owner.recoverable_identity(handle)
            result = restarted.recover(record, terminate=False)
            assert result.action == "none"
            assert result.verified is True
            assert result.terminated is False
            assert owner.is_alive(handle) is ProcessStatus.ALIVE

    def test_recover_reports_already_dead(self, owner: ProcessOwner) -> None:
        restarted = type(owner)()
        with spawned(owner, [PYTHON, "-c", "pass"]) as handle:
            record = owner.recoverable_identity(handle)
            assert owner.wait(handle, timeout=30.0).exited is True
            result = restarted.recover(record, terminate=True)
            assert result.terminated is False
            assert result.action in {"already_dead", "unknown"}

    # ------------------------------------------------------------- handle hygiene
    def test_close_is_idempotent(self, owner: ProcessOwner) -> None:
        with spawned(owner, _sleep_cmd()) as handle:
            owner.terminate(handle, grace=3.0)
            handle.close()
            handle.close()
            assert handle.closed is True

    def test_handle_cannot_cross_owner(self, owner: ProcessOwner) -> None:
        other = type(owner)()
        with spawned(owner, _sleep_cmd()) as handle:
            with pytest.raises(ValueError):
                other.wait(handle, timeout=0.1)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows Job Object / FILETIME 契约需要真实 Windows（本机相关实现）",
)
class TestWindowsProcessOwnerContract(ProcessOwnerContract):
    @pytest.fixture
    def owner(self) -> ProcessOwner:
        return WindowsProcessOwner()


@pytest.mark.skipif(
    os.name != "posix",
    reason=(
        "POSIX 契约测试需要真实 POSIX 主机（ubuntu/macos CI）："
        "start_new_session/killpg//proc/ps 语义无法在 Windows 上验证；"
        "本机为 Windows，显式跳过而不是静默通过"
    ),
)
class TestPosixProcessOwnerContract(ProcessOwnerContract):
    @pytest.fixture
    def owner(self) -> ProcessOwner:
        from packages.core.platform.process_posix import PosixProcessOwner

        return PosixProcessOwner()


# --------------------------------------------------------------------------- factory / guards
def test_default_process_owner_matches_platform() -> None:
    owner = default_process_owner()
    if sys.platform == "win32":
        assert isinstance(owner, WindowsProcessOwner)
        assert owner.platform == PLATFORM_WINDOWS
    else:
        assert owner.platform == PLATFORM_POSIX


@pytest.mark.skipif(os.name == "posix", reason="仅在非 POSIX 主机上验证显式不支持")
def test_posix_owner_rejects_calls_on_windows() -> None:
    from packages.core.platform.process_posix import PosixProcessOwner

    owner = PosixProcessOwner()  # 构造无副作用；平台方法调用期拒绝
    with pytest.raises(UnsupportedPlatformError):
        owner.spawn([PYTHON, "-c", "pass"])
    with pytest.raises(UnsupportedPlatformError):
        owner.identity(os.getpid())


@pytest.mark.skipif(sys.platform == "win32", reason="仅在 POSIX 主机上验证 Windows 实现门禁")
def test_windows_owner_rejects_construction_on_posix() -> None:
    with pytest.raises(UnsupportedPlatformError):
        WindowsProcessOwner()


def test_identity_record_requires_known_fields() -> None:
    with pytest.raises(ValueError):
        ProcessIdentity.from_dict({"pid": 1})
    with pytest.raises(ValueError):
        ProcessIdentity.from_dict(
            {"pid": -1, "kind": "starttime", "birth": "1", "platform": "posix"}
        )


def test_identity_mismatch_error_is_process_owner_error() -> None:
    from packages.core.platform.process import ProcessOwnerError

    assert issubclass(IdentityMismatchError, ProcessOwnerError)
