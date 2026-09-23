"""Unit coverage for the internal Pan launcher contract.

These tests never bind a Pan port and never start a real provider, QQ bridge,
or cloudflared process.  The isolated process/E2E layer is intentionally
separate from these identity and lifecycle primitives.
"""

import json
import os
import types
from pathlib import Path

import pytest

from packages.core import launcher


def _info(root: Path, *, created: float = 10.0, name: str = "python.exe",
          command: list[str] | None = None, cwd: Path | None = None) -> dict:
    return {
        "pid": 41,
        "createdAt": created,
        "name": name,
        "exe": str(root / ".venv" / "Scripts" / name),
        "cmdline": command or [str(root / ".venv" / "Scripts" / name), str(root / "main.py"),
                               f"--pan-root-marker={root}"],
        "cwd": str(cwd or root),
        "running": True,
    }


def test_python_resolver_finds_checkout_venv_entry(tmp_path, monkeypatch):
    monkeypatch.delenv("PAN_PYTHON", raising=False)
    entry = tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )
    entry.parent.mkdir(parents=True)
    entry.write_text("placeholder", encoding="utf-8")
    if os.name != "nt":
        entry.chmod(0o755)
    argv, source = launcher.resolve_python_argv(tmp_path, probe=False)
    assert argv == [str(entry)]
    assert source == "checkout .venv"


def test_identity_requires_creation_root_entry_type_and_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_inspect", lambda pid: _info(tmp_path))
    result = launcher.process_identity(
        41, tmp_path, 10.0, process_type="main",
        marker=f"--pan-root-marker={tmp_path}",
    )
    assert result["ok"] is True
    assert result["rootMarker"] is True
    assert result["entryMarker"] is True
    assert result["commandMarker"] is True

    assert launcher.process_identity(41, tmp_path, 11.1, process_type="main")["ok"] is False
    monkeypatch.setattr(launcher, "_inspect", lambda pid: _info(
        tmp_path, cwd=tmp_path.parent,
        command=["python.exe", str(tmp_path.parent / "main.py")],
    ))
    assert launcher.process_identity(41, tmp_path, 10.0, process_type="main")["ok"] is False
    monkeypatch.setattr(launcher, "_inspect", lambda pid: _info(
        tmp_path, command=["python.exe", str(tmp_path / "other.py")],
    ))
    assert launcher.process_identity(41, tmp_path, 10.0, process_type="main")["ok"] is False


def test_identity_rejects_checkout_prefix_and_pid_reuse(tmp_path, monkeypatch):
    target = tmp_path / "Pan"
    sibling = tmp_path / "Pan-test"
    target.mkdir()
    sibling.mkdir()
    monkeypatch.setattr(launcher, "_inspect", lambda pid: _info(
        target, command=["python.exe", str(sibling / "main.py"), f"--pan-root-marker={sibling}"],
        cwd=sibling,
    ))
    result = launcher.process_identity(41, target, 10.0, process_type="main")
    assert result["ok"] is False
    assert "outside" in result["error"]


def test_readiness_accepts_verified_venv_child_and_rejects_unrelated_owner(tmp_path, monkeypatch):
    import psutil

    monkeypatch.setattr(launcher, "listener_owner", lambda port: 52)
    monkeypatch.setattr(launcher, "process_create_time", lambda pid: {41: 10.0, 52: 10.1}[pid])
    monkeypatch.setattr(launcher, "_inspect", lambda pid: {
        **_info(tmp_path, created={41: 10.0, 52: 10.1}[pid]), "pid": pid,
    })
    monkeypatch.setattr(launcher, "_http_ready", lambda port: (True, None))
    monkeypatch.setattr(psutil, "Process", lambda pid: types.SimpleNamespace(
        parents=lambda: [types.SimpleNamespace(pid=41)] if pid == 52 else [],
    ))
    ready = launcher.readiness(root=tmp_path, port=8767, expected_pid=41, expected_created_at=10.0)
    assert ready["ok"] is True
    assert ready["listenerOwner"] == 52
    assert ready["verifiedDescendant"] is True

    monkeypatch.setattr(psutil, "Process", lambda pid: types.SimpleNamespace(parents=lambda: []))
    unrelated = launcher.readiness(root=tmp_path, port=8767, expected_pid=41, expected_created_at=10.0)
    assert unrelated["ok"] is False
    assert "does not match" in unrelated["error"]


def test_start_refuses_unverified_port_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "configured_port", lambda root: 8767)
    monkeypatch.setattr(launcher, "listener_owner", lambda port: 9001)
    monkeypatch.setattr(launcher, "process_create_time", lambda pid: 12.0)
    monkeypatch.setattr(launcher, "process_identity", lambda *args, **kwargs: {"ok": False, "error": "foreign"})
    with pytest.raises(launcher.PortConflict):
        launcher.start_service(tmp_path)


def test_duplicate_start_uses_state_identity_before_launch(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    state = {
        "version": 2, "root": str(root), "port": 8767,
        "main": {"pid": 41, "createdAt": 10.0, "processType": "main",
                  "entry": "main.py", "marker": "marker"},
        "cloudflared": None, "qq": None,
    }
    (root / "data").mkdir()
    (root / "data" / "process.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(launcher, "process_identity", lambda *args, **kwargs: {"ok": True})
    with pytest.raises(launcher.DuplicateStart):
        launcher.start_service(root)


def test_stale_state_is_replaced_without_killing_reused_pid(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    old = {"pid": 41, "createdAt": 1.0, "processType": "main", "entry": "main.py", "marker": "old"}
    state = {"version": 2, "root": str(root), "port": 8767, "main": old,
             "cloudflared": None, "qq": None}
    (root / "data").mkdir()
    (root / "data" / "process.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(launcher, "configured_port", lambda root: 8767)
    monkeypatch.setattr(launcher, "listener_owner", lambda port: None)
    monkeypatch.setattr(launcher, "resolve_python_argv", lambda root, probe=True: (["python"], "test"))
    monkeypatch.setattr(launcher, "_console_hidden", lambda root: False)
    new = {"pid": 42, "createdAt": 20.0, "root": str(root), "processType": "main",
           "entry": "main.py", "marker": f"--pan-root-marker={root}", "argv": []}
    monkeypatch.setattr(launcher, "_start_main", lambda *args, **kwargs: new)
    monkeypatch.setattr(launcher, "wait_ready", lambda **kwargs: {"ok": True, "listenerOwner": 42})
    monkeypatch.setattr(launcher, "start_cloudflared", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "process_identity", lambda *args, **kwargs: {"ok": False, "error": "stale"})
    result = launcher.start_service(root, timeout=0.1)
    assert result["main"]["pid"] == 42
    assert json.loads((root / "data" / "process.json").read_text(encoding="utf-8"))["main"]["pid"] == 42


def test_start_records_verified_listener_child_for_later_stop(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(launcher, "configured_port", lambda root: 8767)
    monkeypatch.setattr(launcher, "listener_owner", lambda port: None)
    monkeypatch.setattr(launcher, "resolve_python_argv", lambda root, probe=True: (["python"], "test"))
    monkeypatch.setattr(launcher, "_console_hidden", lambda root: False)
    parent = launcher._record(41, root, "main", ["python", str(root / "main.py")], 10.0,
                              f"--pan-root-marker={root}")
    monkeypatch.setattr(launcher, "_start_main", lambda *args, **kwargs: parent)
    monkeypatch.setattr(launcher, "wait_ready", lambda **kwargs: {
        "ok": True, "listenerOwner": 52, "listenerOwnerCreatedAt": 10.1,
        "verifiedDescendant": True,
    })
    monkeypatch.setattr(launcher, "start_cloudflared", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "process_identity", lambda *args, **kwargs: {"ok": False})
    result = launcher.start_service(root, timeout=0.1)
    assert result["main"]["pid"] == 52
    assert json.loads((root / "data" / "process.json").read_text(encoding="utf-8"))["main"]["pid"] == 52


def test_graceful_exit_success_does_not_use_fallback(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    main = {"pid": 41, "createdAt": 10.0, "root": str(root), "processType": "main",
            "entry": "main.py", "marker": f"--pan-root-marker={root}", "argv": []}
    state = {"version": 2, "root": str(root), "port": 8767, "main": main,
             "cloudflared": None, "qq": None}
    (root / "data").mkdir()
    (root / "data" / "process.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(launcher, "process_identity", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(launcher, "_request_internal_shutdown", lambda port, log: (True, None))
    monkeypatch.setattr(launcher, "process_alive", lambda pid, created=None: False)
    monkeypatch.setattr(launcher, "listener_owner", lambda port: None)
    result = launcher.exit_service(root, 8767, 41, 10.0)
    assert result["stopped"] is True
    assert result["coordinated"] is True
    assert result["fallback"] is False
    assert not (root / "data" / "process.json").exists()


def test_failed_coordination_falls_back_only_for_verified_target(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    main = {"pid": 41, "createdAt": 10.0, "root": str(root), "processType": "main",
            "entry": "main.py", "marker": f"--pan-root-marker={root}", "argv": []}
    state = {"version": 2, "root": str(root), "port": 8767, "main": main,
             "cloudflared": None, "qq": None}
    (root / "data").mkdir()
    (root / "data" / "process.json").write_text(json.dumps(state), encoding="utf-8")
    calls = []
    monkeypatch.setattr(launcher, "process_identity", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(launcher, "_request_internal_shutdown", lambda port, log: (False, "connection refused"))
    monkeypatch.setattr(launcher, "process_alive", lambda pid, created=None: True)
    monkeypatch.setattr(launcher, "listener_owner", lambda port: None)
    monkeypatch.setattr(launcher, "_terminate_record", lambda record, root, **kwargs: calls.append(record) or {
        "stopped": True, "fallback": True,
    })
    result = launcher.exit_service(root, 8767, 41, 10.0)
    assert result["fallback"] is True
    assert [call["pid"] for call in calls] == [41]


def test_restart_never_terminates_a_live_unverified_pid(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    main = {"pid": 41, "createdAt": 10.0, "root": str(root), "processType": "main",
            "entry": "main.py", "marker": "wrong-checkout", "argv": []}
    (root / "data").mkdir()
    (root / "data" / "process.json").write_text(json.dumps({
        "version": 2, "root": str(root), "port": 8767, "main": main,
        "cloudflared": None, "qq": None,
    }), encoding="utf-8")
    monkeypatch.setattr(launcher, "process_identity", lambda *args, **kwargs: {
        "ok": False, "error": "process command line marker does not match",
    })
    monkeypatch.setattr(launcher, "process_alive", lambda *args, **kwargs: True)
    monkeypatch.setattr(launcher, "listener_owner", lambda port: 41)
    monkeypatch.setattr(
        launcher, "_terminate_record",
        lambda *args, **kwargs: pytest.fail("unverified PID must never reach termination"),
    )
    with pytest.raises(launcher.LauncherError, match="unverified"):
        launcher.stop_service(root, 8767, require_identity=False)


def test_exit_cleans_verified_qq_and_cloudflared_records(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    records = {
        "main": {"pid": 41, "createdAt": 10.0, "root": str(root), "processType": "main", "entry": "main.py", "marker": "m"},
        "qq": {"pid": 42, "createdAt": 11.0, "root": str(root), "processType": "qq", "entry": "bot.py", "marker": "q"},
        "cloudflared": {"pid": 43, "createdAt": 12.0, "root": str(root), "processType": "cloudflared", "entry": "cloudflared", "marker": "cf"},
    }
    state = {"version": 2, "root": str(root), "port": 8767, **records}
    (root / "data").mkdir()
    (root / "data" / "process.json").write_text(json.dumps(state), encoding="utf-8")
    killed = []
    monkeypatch.setattr(launcher, "process_identity", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(launcher, "_request_internal_shutdown", lambda port, log: (True, None))
    monkeypatch.setattr(launcher, "process_alive", lambda pid, created=None: False)
    monkeypatch.setattr(launcher, "listener_owner", lambda port: None)
    monkeypatch.setattr(launcher, "_terminate_record", lambda record, root, **kwargs: killed.append(record["processType"]) or {
        "stopped": True, "fallback": False,
    })
    launcher.exit_service(root, 8767, 41, 10.0)
    assert killed == ["qq", "cloudflared"]


def test_lifecycle_and_entry_have_no_stop_batch_dependency():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "scripts" / "stop_pan.bat").exists()
    assert "stop_pan.bat" not in (root / "packages" / "core" / "main_lifecycle.py").read_text(encoding="utf-8")
    start = (root / "scripts" / "start_pan.bat").read_text(encoding="utf-8").lower()
    assert "packages.core.launcher" in start
    assert "taskkill" not in start
