"""Failure controls plus target-native evidence; never enable a capability."""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from packages.core.platform import process as ownership, service_process
from packages.core.platform import service_process_posix as evidence


def fixture_record():
    leader = ownership.ProcessIdentity(42, "starttime", "100", "posix", "boot", 42)
    record = {"pid": 42, "processIdentity": leader.as_dict(),
              "processTreeSnapshot": {"leader": leader.as_dict(), "members": [leader.as_dict()]}}
    return leader, record


@pytest.mark.parametrize("group,remaining,expected", [(False, (), True), (True, (), False), (False, (77,), False)])
def test_dead_leader_only_readonly_group_member_proof(monkeypatch, group, remaining, expected):
    from packages.core.platform import process_posix as backend
    leader, record = fixture_record()
    owner = SimpleNamespace(identity=lambda pid: leader,
        check=lambda identity: ownership.IdentityCheck(ownership.ProcessStatus.DEAD, None, identity))
    monkeypatch.setattr(ownership, "default_process_owner", lambda: owner)
    monkeypatch.setattr(backend, "_pid_exists", lambda pid: False)
    monkeypatch.setattr(backend, "_group_exists", lambda pgid: group)
    monkeypatch.setattr(backend.PosixProcessOwner, "_member_remaining", lambda members: remaining)
    monkeypatch.setattr(backend, "_killpg", lambda *a: pytest.fail("dead leader grants no signal"))
    result = evidence.offline(record)
    assert result["confirmed"] is expected and result["signalAuthorized"] is False


@pytest.mark.parametrize("fault", ["missing", "wrong_birth", "wrong_boot", "permission"])
def test_tree_proof_fail_closed(monkeypatch, fault):
    from packages.core.platform import process_posix as backend
    leader, record = fixture_record()
    owner = SimpleNamespace(identity=lambda pid: leader,
        check=lambda identity: ownership.IdentityCheck(ownership.ProcessStatus.DEAD, None, identity))
    monkeypatch.setattr(ownership, "default_process_owner", lambda: owner)
    if fault == "missing":
        record.pop("processTreeSnapshot")
    elif fault == "wrong_birth":
        record["processTreeSnapshot"]["leader"]["birth"] = "another"
    elif fault == "wrong_boot":
        record["processIdentity"]["boot_id"] = "another"
    else:
        def denied(pid):
            raise PermissionError("synthetic denied")
        monkeypatch.setattr(backend, "_pid_exists", denied)
    assert evidence.offline(record)["confirmed"] is False


def test_serialization_failure_cleans_original_spawn_handle(monkeypatch, tmp_path):
    calls = []
    child = SimpleNamespace(pid=42)
    owned = SimpleNamespace(popen=child)
    def fail(handle):
        raise ValueError("identity serialization failed")
    owner = SimpleNamespace(spawn=lambda *a, **kw: owned, recoverable_identity=fail,
        kill_tree=lambda handle, **kw: calls.append((handle, kw)) or SimpleNamespace(confirmed=True))
    monkeypatch.setattr(service_process, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(ownership, "default_process_owner", lambda: owner)
    with pytest.raises(ValueError, match="identity serialization"):
        service_process.spawn(["synthetic"], cwd=tmp_path, env={}, stdin=None, stdout=None, stderr=None)
    assert calls == [(owned, {"timeout": 5})]


def test_absent_pid_and_port_cannot_erase_original_tree_unknown(monkeypatch, tmp_path):
    from packages.core import launcher
    _, record = fixture_record()
    record.update(createdAt=10., processType="main")
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(launcher, "checkout_root", lambda root: tmp_path)
    monkeypatch.setattr(launcher, "load_state", lambda root: {"main": record})
    monkeypatch.setattr(launcher, "refresh_qq_state", lambda *a: None)
    monkeypatch.setattr(launcher, "process_identity", lambda *a, **kw: {"ok": False})
    monkeypatch.setattr(launcher, "_same_record_alive", lambda record: False)
    monkeypatch.setattr(launcher, "clear_state", lambda root: pytest.fail("unknown tree must retain original evidence"))
    monkeypatch.setattr(service_process, "offline", lambda record: {"confirmed": False, "reason": "live child"})
    with pytest.raises(launcher.LauncherError, match="tree exit unconfirmed"):
        launcher.stop_service(tmp_path, 43129, 42, 10.)


def test_terminate_dead_leader_can_only_return_readonly_proof(monkeypatch, tmp_path):
    from packages.core import launcher
    _, record = fixture_record()
    record.update(createdAt=10., processType="main")
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(launcher, "process_identity", lambda *a, **kw: {"ok": False})
    monkeypatch.setattr(service_process, "terminate", lambda record: pytest.fail("no signal for dead leader"))
    monkeypatch.setattr(service_process, "offline", lambda record: {"confirmed": True, "reason": "original group/member absence"})
    assert launcher._terminate_record(record, tmp_path, log_path=None)["stopped"] is True


@pytest.mark.skipif(os.name != "posix", reason="actual POSIX spawn-abort group proof requires target OS")
def test_native_strong_identity_failure_aborts_own_group(monkeypatch, tmp_path):
    from packages.core.platform import process_posix as backend
    original_read, original_popen = backend.read_posix_identity, subprocess.Popen
    retained = []
    def spawn(*a, **kw):
        child = original_popen(*a, **kw)
        retained.append(child)
        return child
    monkeypatch.setattr(backend.subprocess, "Popen", spawn)
    monkeypatch.setattr(backend, "read_posix_identity",
        lambda pid: original_read(pid) if pid == os.getpid() else (None, None))
    try:
        with pytest.raises(ownership.OwnershipUnknownError):
            ownership.default_process_owner().spawn([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path)
        assert len(retained) == 1 and retained[0].returncode is not None
        assert not backend._group_exists(retained[0].pid)
    finally:
        for child in retained:
            if child.returncode is None:
                child.kill(); child.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="actual POSIX read-only group proof requires target OS")
def test_native_original_group_absence_after_graceful_exit(tmp_path):
    release = tmp_path / "release"
    child = service_process.spawn([sys.executable, "-c",
        "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]);\nwhile not p.exists(): time.sleep(.02)", str(release)],
        cwd=tmp_path, env=os.environ.copy(), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    record = {"pid": child.pid, "processIdentity": child.pan_process_identity}
    try:
        record["processTreeSnapshot"] = evidence.capture(record)
        release.touch()
        child.wait(timeout=5)
        assert evidence.offline(record)["confirmed"] is True
        record.pop("processTreeSnapshot")
        assert evidence.offline(record)["confirmed"] is False
    finally:
        release.touch()
        child.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="actual leader-exit/live-child group requires target OS")
def test_native_leader_exit_preserves_live_child_unknown_without_signal(tmp_path, monkeypatch):
    import time
    import psutil
    from packages.core.platform import process_posix as backend
    parent_release, child_release = tmp_path / "parent-release", tmp_path / "child-release"
    child_pid = tmp_path / "child-pid"
    child_code = "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); end=time.monotonic()+15;\nwhile not p.exists() and time.monotonic()<end: time.sleep(.02)"
    parent_code = (
        "import pathlib,sys,time,subprocess; "
        "c=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
        "pathlib.Path(sys.argv[3]).write_text(str(c.pid)); p=pathlib.Path(sys.argv[4]);\n"
        "while not p.exists(): time.sleep(.02)")
    parent = service_process.spawn([sys.executable, "-c", parent_code, child_code,
        str(child_release), str(child_pid), str(parent_release)], cwd=tmp_path, env=os.environ.copy(),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert child_pid.exists()
        pid = int(child_pid.read_text())
        birth = psutil.Process(pid).create_time()
        record = {"pid": parent.pid, "processIdentity": parent.pan_process_identity}
        record["processTreeSnapshot"] = evidence.capture(record)
        assert len(record["processTreeSnapshot"]["members"]) >= 2
        parent_release.touch(); parent.wait(timeout=5)
        original_signal = backend._killpg
        def readonly(pgid, sig):
            assert sig == 0, "dead leader cannot authorize a destructive signal"
            return original_signal(pgid, sig)
        monkeypatch.setattr(backend, "_killpg", readonly)
        proof = evidence.offline(record)
        assert proof["confirmed"] is False and proof["groupPresent"] is True
        assert pid in proof["remaining"]
        assert psutil.Process(pid).create_time() == birth
        assert psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    finally:
        parent_release.touch(); child_release.touch()
        parent.wait(timeout=5)
