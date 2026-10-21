import json
import pytest
from packages.core import launcher
from packages.remote import mcp_runtime as runtime


def settings(root):
    tunnel = root / "tunnel.yml"
    tunnel.write_text("tunnel: test\ningress:\n  - service: http://127.0.0.1:9742\n", encoding="utf-8")
    config = {"port": 8768, "mcp_remote": {"enabled": True, "port": 9742,
        "public_hostname": "mcp.example.com", "access_issuer": "https://team.cloudflareaccess.com",
        "access_audience": "test-aud", "config_path": str(tunnel)}}
    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")


def test_disabled_never_spawns(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_spawn", lambda *a: pytest.fail("must not spawn"))
    with pytest.raises(launcher.LauncherError, match="Enable mcp_remote"):
        runtime.control(tmp_path, "start")


def test_foreign_listener_is_never_adopted_or_stopped(tmp_path, monkeypatch):
    settings(tmp_path)
    monkeypatch.setattr(launcher, "_cloudflared_binary", lambda c: "cloudflared")
    monkeypatch.setattr(launcher, "listener_owner", lambda p: 777)
    monkeypatch.setattr(runtime, "_spawn", lambda *a: pytest.fail("must not spawn"))
    with pytest.raises(launcher.LauncherError, match="unowned"):
        runtime.control(tmp_path, "start")


def test_stop_keeps_record_when_identity_rejected(tmp_path, monkeypatch):
    runtime._save(tmp_path, {"tunnel": {"pid": 7, "createdAt": 1}})
    monkeypatch.setattr(launcher, "_terminate_record", lambda *a, **k: {"stopped": False, "reason": "identity mismatch"})
    with pytest.raises(launcher.LauncherError, match="identity mismatch"):
        runtime.control(tmp_path, "stop")
    assert runtime._read(tmp_path)["tunnel"]["pid"] == 7


def test_start_is_idempotent_for_owned_pair(tmp_path, monkeypatch):
    settings(tmp_path)
    runtime._save(tmp_path, {"gateway": {"pid": 1}, "tunnel": {"pid": 2}})
    monkeypatch.setattr(launcher, "_cloudflared_binary", lambda c: "cloudflared")
    monkeypatch.setattr(runtime, "_identity", lambda *a: {"ok": True})
    monkeypatch.setattr(runtime, "_spawn", lambda *a: pytest.fail("must not spawn"))
    assert runtime.control(tmp_path, "start") == {"ok": True}


def test_start_binds_gateway_to_checkout_and_pan_api(tmp_path, monkeypatch):
    """The owned gateway must load Pan's stdio registry from this checkout."""
    settings(tmp_path)
    spawned = []
    monkeypatch.setattr(launcher, "_cloudflared_binary", lambda c: "cloudflared")
    monkeypatch.setattr(launcher, "listener_owner", lambda p: None)
    monkeypatch.setattr(launcher, "resolve_python_argv",
                        lambda root, **kwargs: (["test-python"], "test"))
    monkeypatch.setattr(runtime, "_identity", lambda root, record: {"ok": bool(record)})

    def fake_spawn(root, state, name, argv, marker, process_type, entry):
        spawned.append((name, argv))
        state[name] = {"pid": 1}

    monkeypatch.setattr(runtime, "_spawn", fake_spawn)
    assert runtime.control(tmp_path, "start") == {"ok": True}

    gateway = next(argv for name, argv in spawned if name == "gateway")
    runtime_file = tmp_path / "data" / "mcp_remote" / "gateway.json"
    assert gateway == ["test-python", "-B", "-m", "packages.remote.mcp_gateway",
                       str(runtime_file)]
    data = json.loads(runtime_file.read_text(encoding="utf-8"))
    assert data["pan_project_root"] == str(tmp_path.resolve())
    assert data["pan_api_url"] == "http://127.0.0.1:8768"
    assert data["public_hostname"] == "mcp.example.com"
