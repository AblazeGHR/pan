"""Cloudflared launcher contract tests; no real tunnel is started."""

import json
from pathlib import Path

from packages.core import launcher


def _config(root: Path, *, quick: bool = True) -> None:
    (root / "config.json").write_text(json.dumps({
        "port": 8767,
        "remote": {
            "enabled": True,
            "quick_tunnel": quick,
            "config_path": str(root / "source.yml"),
            "protocol": "http2",
        },
    }), encoding="utf-8")


def test_named_tunnel_rewrites_port_and_protocol_inside_checkout(tmp_path):
    _config(tmp_path, quick=False)
    (tmp_path / "source.yml").write_text(
        "tunnel: test\n ingress:\n  service: http://localhost:8768\n protocol: quic\n",
        encoding="utf-8",
    )
    argv, marker = launcher._named_tunnel_config(tmp_path, launcher.load_config(tmp_path), 8767)
    output = Path(marker)
    assert argv[:2] == ["tunnel", "--config"]
    assert output.is_relative_to(tmp_path / "data" / "cloudflared")
    text = output.read_text(encoding="utf-8")
    assert "http://localhost:8767" in text
    assert "protocol: http2" in text
    assert "protocol: quic" not in text


def test_start_cloudflared_records_quick_pid_and_marker(tmp_path, monkeypatch):
    _config(tmp_path, quick=True)
    state = {"version": 2, "root": str(tmp_path), "port": 8767,
             "main": None, "qq": None, "cloudflared": None}

    class FakeProcess:
        pid = 7331

    monkeypatch.setattr(launcher, "_cloudflared_binary", lambda config: "cloudflared.exe")
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(launcher, "process_create_time", lambda pid: 55.0)
    monkeypatch.setattr(launcher, "process_identity", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(launcher, "process_alive", lambda *args, **kwargs: False)
    record = launcher.start_cloudflared(tmp_path, 8767, state)
    assert record["pid"] == 7331
    assert record["processType"] == "cloudflared"
    assert "pan_cf_quick_8767.log" in record["marker"]
    saved = launcher.load_state(tmp_path)
    assert saved["cloudflared"]["pid"] == 7331


def test_remote_disabled_does_not_resolve_or_start_cloudflared(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(json.dumps({"remote": {"enabled": False}}), encoding="utf-8")
    called = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: called.append(args))
    state = {"version": 2, "root": str(tmp_path), "port": 8767,
             "main": None, "qq": None, "cloudflared": None}
    assert launcher.start_cloudflared(tmp_path, 8767, state) is None
    assert called == []


def test_cloudflared_status_is_record_scoped_not_a_process_scan(tmp_path, monkeypatch):
    state = {"version": 2, "root": str(tmp_path), "port": 8767,
             "main": None, "qq": None,
             "cloudflared": {"pid": 9, "createdAt": 1.0, "root": str(tmp_path),
                              "processType": "cloudflared", "entry": "cloudflared", "marker": "marker"}}
    launcher.save_state(tmp_path, state)
    monkeypatch.setattr(launcher, "process_identity", lambda *args, **kwargs: {"ok": False, "error": "PID reused"})
    result = launcher.owned_cloudflared(tmp_path)
    assert result["record"]["pid"] == 9
    assert result["identity"]["ok"] is False
