"""Backend contracts on every host; native process evidence is separate."""
import os
import subprocess
import sys
import socket
from types import SimpleNamespace

import pytest

from packages.core.platform import service_process
from packages.core.platform import process as ownership


@pytest.mark.parametrize("host", ["nt", "posix"])
def test_spawn_preserves_windows_and_uses_owned_posix(host, monkeypatch, tmp_path):
    calls = []
    child = SimpleNamespace(pid=42)
    owned = SimpleNamespace(popen=child)
    record = {"schema": "pan.process-identity/1", "pid": 42, "birth": "strong"}
    owner = SimpleNamespace(
        spawn=lambda argv, **kw: calls.append((argv, kw)) or owned,
        recoverable_identity=lambda handle: record if handle is owned else None)
    monkeypatch.setattr(service_process, "os", SimpleNamespace(name=host))
    monkeypatch.setattr(ownership, "default_process_owner", lambda: owner)
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)) or child)
    result = service_process.spawn(["python", "main.py"], cwd=tmp_path, env={},
        stdin=None, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=512)
    assert result is child
    args, options = calls.pop()
    assert not calls and args == ["python", "main.py"]
    assert options["cwd"] == tmp_path and options["env"] == {}
    if host == "nt":
        assert options["creationflags"] == 512 and options["stdin"] is None
        assert options["close_fds"] is True
    else:
        assert options["stdin"] == subprocess.DEVNULL and "creationflags" not in options
        assert child.pan_owned_process is owned and child.pan_process_identity == record


@pytest.mark.parametrize("identity", [None, {}, {"pid": 99}])
def test_missing_or_mismatched_identity_never_signals(identity, monkeypatch):
    monkeypatch.setattr(ownership, "default_process_owner", lambda: pytest.fail("no recovery authority"))
    assert service_process.terminate({"pid": 42, "processIdentity": identity})["stopped"] is False


@pytest.mark.parametrize("action,verified,remaining,expected", [
    ("terminated", True, (), True), ("already_dead", True, (), True),
    ("unknown", False, (), False), ("refused_identity", False, (), False),
    ("terminated", True, (42,), False), ("already_dead", False, (), False),
])
def test_recovery_requires_positive_proof(action, verified, remaining, expected, monkeypatch):
    result = ownership.RecoveryResult(action=action, verified=verified,
        terminated=action == "terminated", remaining=remaining)
    calls = []
    owner = SimpleNamespace(recover=lambda record, **kw: calls.append((record, kw)) or result)
    monkeypatch.setattr(ownership, "default_process_owner", lambda: owner)
    record = {"pid": 42, "birth": "original"}
    assert service_process.terminate({"pid": 42, "processIdentity": record})["stopped"] is expected
    assert calls == [(record, {"terminate": True})]


def test_recovery_permission_error_is_explicit(monkeypatch):
    def denied(*a, **kw):
        raise PermissionError("synthetic permission denial")
    monkeypatch.setattr(ownership, "default_process_owner", lambda: SimpleNamespace(recover=denied))
    with pytest.raises(PermissionError, match="synthetic"):
        service_process.terminate({"pid": 42, "processIdentity": {"pid": 42}})


def test_native_owned_process_spawn_and_exit(tmp_path):
    """Real local OS spawn/wait only; no claim of HTTP or lifecycle parity."""
    child = service_process.spawn([sys.executable, "-c", "raise SystemExit(7)"],
        cwd=tmp_path, env=os.environ.copy(), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert child.wait(timeout=10) == 7
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_native_launcher_http_identity_and_offline(tmp_path):
    """Synthetic HTTP main on a dynamic port, through real launcher primitives."""
    from packages.core import launcher
    from packages.core.platform.execution_host import service_offline_evidence
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    assert port not in {8767, 8768}
    (tmp_path / "main.py").write_text(
        "import os\nfrom http.server import HTTPServer, BaseHTTPRequestHandler\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  self.send_response(200); self.end_headers(); self.wfile.write(b'{}')\n"
        "HTTPServer(('127.0.0.1', int(os.environ['PAN_PORT'])), Handler).serve_forever()\n",
        encoding="utf-8")
    record = launcher._start_main(tmp_path, port, [sys.executable], True,
                                   tmp_path / "data/logs/launcher.log")
    try:
        ready = launcher.wait_ready(root=tmp_path, port=port, expected_pid=record["pid"],
            expected_created_at=record["createdAt"], timeout=10)
        assert ready["ok"] is True and ready["http"] is True
        assert ready["identity"]["ok"] is True
        stopped = launcher._terminate_record(record, tmp_path, log_path=None)
        assert stopped["stopped"] is True
        proof = service_offline_evidence(root=str(tmp_path), port=port,
            old_pid=record["pid"], old_pid_created_at=record["createdAt"])
        assert proof["ok"] is True, proof
    finally:
        if launcher.process_alive(record["pid"], record["createdAt"]):
            outcome = launcher._terminate_record(record, tmp_path, log_path=None)
            assert outcome["stopped"], outcome


def test_canonical_restart_uses_existing_cli_start_lock(tmp_path, monkeypatch):
    from packages.core import launcher, main_lifecycle, cli, platform
    monkeypatch.setattr(platform, "supports", lambda name: True)
    monkeypatch.setattr(main_lifecycle, "service_process_identity", lambda *a: {"ok": True})
    monkeypatch.setattr(launcher, "stop_service", lambda *a, **kw: {"stopped": True})
    monkeypatch.setattr(main_lifecycle, "offline_checks", lambda **kw: {"ok": True})
    calls = []
    def start(root, **kw):
        with pytest.raises(launcher.LauncherError, match="Another pan start"):
            with cli.start_lock(root):
                pytest.fail("canonical restart did not hold shared lock")
        calls.append(root)
    monkeypatch.setattr(launcher, "start_service", start)
    monkeypatch.setattr(main_lifecycle, "ready_checks", lambda **kw: {
        "ok": True, "newPid": 43, "newPidCreatedAt": 20., "port": 43219,
        "http": True, "identity": {"ok": True, "root": str(tmp_path.resolve())},
        "listenerOwner": 43})
    phases = []
    store = SimpleNamespace(phase=lambda *a, **kw: phases.append(a[3]))
    doc = {"effectId": "effect_original", "attempt": {}, "claim": "claim_original",
        "effectKey": {"runId": "run_original", "stepId": "step_original",
                      "targetId": "main", "operationOrdinal": 0},
        "frozenPlan": {"operation": "service.restart", "arguments": {
            "root": str(tmp_path), "port": 43219, "oldPid": 42, "oldPidCreatedAt": 10.}}}
    result = main_lifecycle.run_canonical_supervisor(store, doc)
    assert result["outcome"] == "succeeded" and calls == [tmp_path.resolve()]
    assert phases == ["stopping", "stopped", "starting", "checking_ready"]
