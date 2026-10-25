"""POSIX ProcessOwner 逻辑单测（可在任意平台运行，含 Windows 本机）。

真实 POSIX 系统调用（``setsid`` / ``killpg`` / ``/proc`` / ``ps``）无法在 Windows
上执行，所以本文件用**可替换的系统调用薄封装**驱动控制流：

- ``_killpg`` / ``_group_exists`` / ``_pid_exists`` / ``read_posix_identity``
  是 ``process_posix`` 的模块级 seam（生产路径直接调真实系统调用）；
- 被测对象是 ``PosixProcessOwner`` 的真实控制流（SIGTERM→宽限→SIGKILL、
  身份不匹配拒绝、崩溃回收），不是另写一份逻辑。

真实内核语义由 ``tests/test_platform_process_contract.py::TestPosixProcessOwnerContract``
在 POSIX CI 上证明（Windows 本机显式 skip）。
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.platform import process_posix as posix
from packages.core.platform.process import (
    IDENTITY_SCHEMA,
    PLATFORM_POSIX,
    IdentityMismatchError,
    OwnershipUnknownError,
    ProcessIdentity,
    ProcessStatus,
    UnsupportedPlatformError,
)

# 控制流测试用固定编号，避免 Windows 缺 SIGKILL 时回退成 SIGTERM 掩盖升级路径。
SIGTERM = 15
SIGKILL = 9


class FakePopen:
    """只实现 ``PosixOwnedProcess`` 用到的两个方法。"""

    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired(str(self.pid), timeout)
        return self.returncode


class FakeGroup:
    """模拟进程组存活状态，并记录收到的信号。"""

    def __init__(self, *, die_on: set[int], popen: FakePopen | None = None,
                 exit_code: int = -15) -> None:
        self.alive = True
        self.calls: list[int] = []
        self._die_on = die_on
        self._popen = popen
        self._exit_code = exit_code

    def killpg(self, pgid: int, sig: int) -> None:
        self.calls.append(sig)
        if sig in self._die_on:
            self.alive = False
            if self._popen is not None:
                self._popen.returncode = self._exit_code

    def exists(self, pgid: int) -> bool:
        return self.alive


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch) -> posix.PosixProcessOwner:
    monkeypatch.setattr(posix, "_require_posix", lambda: None)
    monkeypatch.setattr(posix, "_sigterm", lambda: SIGTERM)
    monkeypatch.setattr(posix, "_sigkill", lambda: SIGKILL)
    monkeypatch.setattr(
        posix,
        "_signal_name",
        lambda sig: {SIGTERM: "SIGTERM", SIGKILL: "SIGKILL"}.get(sig, f"signal-{sig}"),
    )
    return posix.PosixProcessOwner()


def make_handle(
    owner: posix.PosixProcessOwner,
    *,
    pid: int = 4321,
    birth: str = "100",
    pgid: int | None = 4321,
) -> tuple[posix.PosixOwnedProcess, FakePopen, ProcessIdentity]:
    identity = ProcessIdentity(
        pid=pid,
        kind="starttime",
        birth=birth,
        platform=PLATFORM_POSIX,
        boot_id="boot-a",
        pgid=pgid,
    )
    popen = FakePopen(pid)
    return posix.PosixOwnedProcess(owner, popen, identity, pid if pgid is None else pgid), popen, identity


def install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    identity: ProcessIdentity | None,
    state: str | None = "S",
    group: FakeGroup | None = None,
    pid_exists: bool | None = None,
) -> None:
    monkeypatch.setattr(
        posix, "read_posix_identity", lambda pid: (identity, state)
    )
    if group is not None:
        monkeypatch.setattr(posix, "_killpg", group.killpg)
        monkeypatch.setattr(posix, "_group_exists", group.exists)
    monkeypatch.setattr(posix, "_pid_exists", lambda pid: pid_exists)


# --------------------------------------------------------------------------- pure parsing
def test_parse_proc_stat_handles_comm_with_spaces_and_parens() -> None:
    rest = ["S", "1", "4242", "4242"] + ["0"] * 15 + ["98765"] + ["0"] * 30
    text = "4242 (name with ) (parens) " + " ".join(rest) + "\n"
    fields = posix.parse_proc_stat(text)
    assert fields["state"] == "S"
    assert fields["ppid"] == "1"
    assert fields["pgrp"] == "4242"
    assert fields["starttime"] == "98765"


def test_parse_proc_stat_rejects_truncated() -> None:
    with pytest.raises(ValueError):
        posix.parse_proc_stat("1 (x) S 1")
    with pytest.raises(ValueError):
        posix.parse_proc_stat("1 (unterminated")


def test_read_linux_identity_from_fake_proc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rest = ["S", "1", "777", "777"] + ["0"] * 15 + ["424242"] + ["0"] * 30
    stat_dir = tmp_path / "777"
    stat_dir.mkdir()
    (stat_dir / "stat").write_text("777 (python) " + " ".join(rest) + "\n", encoding="utf-8")
    monkeypatch.setattr(posix, "read_boot_id", lambda: "boot-xyz")

    identity, state = posix.read_linux_identity(777, proc_root=tmp_path)
    assert state == "S"
    assert identity is not None
    assert identity.kind == "starttime"
    assert identity.birth == "424242"
    assert identity.boot_id == "boot-xyz"
    assert identity.pgid == 777
    assert identity.platform == PLATFORM_POSIX

    missing, missing_state = posix.read_linux_identity(778, proc_root=tmp_path)
    assert missing is None and missing_state is None


def test_read_ps_identity_parses_lstart_pgid_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        posix,
        "_run_ps",
        lambda pid: (0, "Sun Oct 25 18:23:54 2026  4321 S+\n"),
    )
    identity, state = posix.read_ps_identity(4321)
    assert state == "S"
    assert identity is not None
    assert identity.kind == "lstart"
    assert identity.birth == "Sun Oct 25 18:23:54 2026"
    assert identity.pgid == 4321


def test_read_ps_identity_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(posix, "_run_ps", lambda pid: (1, ""))
    assert posix.read_ps_identity(1) == (None, None)
    monkeypatch.setattr(posix, "_run_ps", lambda pid: (0, "garbage\n"))
    assert posix.read_ps_identity(1) == (None, None)


def test_read_posix_identity_requires_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 未替换平台门：Windows 上必须显式“不支持”，不是返回伪造身份。
    if sys.platform == "win32":
        with pytest.raises(UnsupportedPlatformError):
            posix.read_posix_identity(1)


def test_identity_matches_requires_same_boot_id() -> None:
    base = ProcessIdentity(
        pid=1, kind="starttime", birth="9", platform=PLATFORM_POSIX, boot_id="a"
    )
    assert base.matches(dataclasses.replace(base, boot_id="a")) is True
    assert base.matches(dataclasses.replace(base, boot_id="b")) is False
    assert base.matches(dataclasses.replace(base, boot_id=None)) is False


# --------------------------------------------------------------------------- check
def test_check_alive_and_matching(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = make_handle(owner)
    install(monkeypatch, identity=identity)
    check = owner.check(identity)
    assert check.status is ProcessStatus.ALIVE
    assert check.ours is True


def test_check_detects_reused_pid(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = make_handle(owner)
    install(monkeypatch, identity=dataclasses.replace(identity, birth="999"))
    check = owner.check(identity)
    assert check.status is ProcessStatus.ALIVE
    assert check.ours is False


def test_check_zombie_is_dead(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = make_handle(owner)
    install(monkeypatch, identity=identity, state="Z")
    assert owner.check(identity).status is ProcessStatus.DEAD


def test_check_missing_pid_is_dead(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = make_handle(owner)
    install(monkeypatch, identity=None, state=None, pid_exists=False)
    assert owner.check(identity).status is ProcessStatus.DEAD


def test_check_unreadable_but_existing_is_unknown(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = make_handle(owner)
    install(monkeypatch, identity=None, state=None, pid_exists=True)
    assert owner.check(identity).status is ProcessStatus.UNKNOWN


# --------------------------------------------------------------------------- terminate
def test_terminate_escalates_to_sigkill(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle, popen, identity = make_handle(owner)
    group = FakeGroup(die_on={SIGKILL}, popen=popen, exit_code=-9)
    install(monkeypatch, identity=identity, group=group)

    result = owner.terminate(handle, grace=0.05)
    assert group.calls[0] == SIGTERM
    assert SIGKILL in group.calls
    assert result.escalated is True
    assert result.requested is True
    assert result.exited is True
    assert result.remaining == ()
    assert popen.returncode == -9


def test_terminate_is_graceful_when_sigterm_suffices(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle, popen, identity = make_handle(owner)
    group = FakeGroup(die_on={SIGTERM}, popen=popen, exit_code=-15)
    install(monkeypatch, identity=identity, group=group)

    result = owner.terminate(handle, grace=1.0)
    assert group.calls == [SIGTERM]
    assert result.escalated is False
    assert result.signal_sent == "SIGTERM"
    assert result.remaining == ()
    assert result.exited is True


def test_terminate_refuses_identity_mismatch(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle, popen, identity = make_handle(owner)
    group = FakeGroup(die_on={SIGKILL}, popen=popen)
    install(
        monkeypatch,
        identity=dataclasses.replace(identity, pid=identity.pid, birth="other"),
        group=group,
    )
    with pytest.raises(IdentityMismatchError):
        owner.terminate(handle, grace=0.05)
    assert group.calls == []


def test_terminate_refuses_unknown_identity(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle, popen, _identity = make_handle(owner)
    group = FakeGroup(die_on={SIGKILL}, popen=popen)
    install(monkeypatch, identity=None, state=None, group=group, pid_exists=None)
    with pytest.raises(OwnershipUnknownError):
        owner.terminate(handle, grace=0.05)
    assert group.calls == []


def test_terminate_reports_remaining_when_group_survives(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle, popen, identity = make_handle(owner)
    group = FakeGroup(die_on=set(), popen=popen)  # 永远杀不掉
    install(monkeypatch, identity=identity, group=group)

    result = owner.terminate(handle, grace=0.0)
    assert result.escalated is True
    assert result.exited is False
    assert result.remaining == (4321,)


def test_kill_tree_confirms_empty_group(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle, popen, identity = make_handle(owner)
    group = FakeGroup(die_on={SIGKILL}, popen=popen, exit_code=-9)
    install(monkeypatch, identity=identity, group=group)

    result = owner.kill_tree(handle, timeout=1.0)
    assert result.owned == (4321,)
    assert result.remaining == ()
    assert result.confirmed is True
    assert group.calls == [SIGKILL]


# --------------------------------------------------------------------------- recover
def _record(identity: ProcessIdentity) -> dict:
    record = {"schema": IDENTITY_SCHEMA, "platform": PLATFORM_POSIX}
    record.update(identity.as_dict())
    return record


def test_recover_terminates_verified_process(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = make_handle(owner)
    group = FakeGroup(die_on={SIGTERM})
    install(monkeypatch, identity=identity, group=group)

    result = owner.recover(_record(identity), terminate=True, grace=1.0, timeout=1.0)
    assert result.action == "terminated"
    assert result.verified is True
    assert result.terminated is True
    assert result.remaining == ()
    assert group.calls[0] == SIGTERM


def test_recover_refuses_when_leader_is_dead(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = make_handle(owner)
    group = FakeGroup(die_on={SIGKILL})
    install(monkeypatch, identity=None, state=None, group=group, pid_exists=False)

    result = owner.recover(_record(identity), terminate=True)
    assert result.action == "already_dead"
    assert result.terminated is False
    assert group.calls == []  # leader 已死：拒绝按 pgid 盲杀


def test_recover_refuses_pgid_change(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = make_handle(owner)
    moved = dataclasses.replace(identity, pgid=9999)
    group = FakeGroup(die_on={SIGKILL})
    install(monkeypatch, identity=moved, group=group)

    result = owner.recover(_record(identity), terminate=True)
    assert result.action == "refused_pgid_changed"
    assert result.terminated is False
    assert group.calls == []


def test_recover_refuses_non_leader_pgid(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = make_handle(owner)
    joined = dataclasses.replace(identity, pgid=5555)
    group = FakeGroup(die_on={SIGKILL})
    install(monkeypatch, identity=identity, group=group)

    result = owner.recover(_record(joined), terminate=True)
    assert result.action == "unknown"
    assert result.terminated is False
    assert group.calls == []


def test_recover_refuses_identity_mismatch(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = make_handle(owner)
    group = FakeGroup(die_on={SIGKILL})
    install(
        monkeypatch,
        identity=dataclasses.replace(identity, birth="other"),
        group=group,
    )

    result = owner.recover(_record(identity), terminate=True)
    assert result.action == "refused_identity"
    assert result.verified is False
    assert group.calls == []


def test_recover_without_terminate(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = make_handle(owner)
    group = FakeGroup(die_on={SIGKILL})
    install(monkeypatch, identity=identity, group=group)

    result = owner.recover(_record(identity), terminate=False)
    assert result.action == "none"
    assert result.verified is True
    assert group.calls == []


def test_recover_rejects_unusable_record(
    owner: posix.PosixProcessOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, identity=None, pid_exists=None)
    result = owner.recover({"pid": 1})
    assert result.action == "unknown"
    assert result.terminated is False
