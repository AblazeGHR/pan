"""Closed-browser sender contract; these tests never launch a notification."""
import asyncio
import base64
from types import SimpleNamespace

import pytest

from packages.core import notifications, config


@pytest.mark.parametrize("base", ["http://127.0.0.1:49123", "https://localhost:49124/", "http://[::1]:49125"])
def test_deep_link_uses_runtime_instance_and_only_session(monkeypatch, base):
    monkeypatch.setattr(config, "resolve_pan_api_url", lambda: base)
    assert notifications.completion_deep_link("ses_closed") == base.rstrip("/") + "/react/?session=ses_closed"


@pytest.mark.parametrize("base", ["file:///tmp/pan", "https://external.example", "http://user:secret@localhost", "http://127.0.0.1:0", "http://localhost:99999", "http://localhost/api", "http://localhost?token=secret", "http://localhost/#secret"])
def test_deep_link_rejects_nonlocal_or_credentialed_launch_targets(monkeypatch, base):
    monkeypatch.setattr(config, "resolve_pan_api_url", lambda: base)
    assert notifications.completion_deep_link("ses_closed") is None


@pytest.mark.parametrize("session_id", [None, "", "a" * 65, "ses_a&token=secret", "../x", "中文", "ses_x\n"])
def test_deep_link_rejects_invalid_session_reference(session_id):
    assert notifications.completion_deep_link(session_id) is None


def test_default_completion_sender_captures_distinct_targets_and_keeps_channels(monkeypatch):
    monkeypatch.setattr(config, "resolve_pan_api_url", lambda: "http://127.0.0.1:49123")
    monkeypatch.setattr(notifications.platform, "system", lambda: "Windows")
    monkeypatch.setattr(notifications.shutil, "which", lambda _: "powershell.exe")
    calls = []
    monkeypatch.setattr(notifications.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(notifications, "_system_sender", notifications.default_system_sender)
    for sid in ("ses_a", "ses_b"):
        session = SimpleNamespace(id=sid, name="Fixture", notification_settings={"browser": True, "system": True})
        event = notifications.dispatch_completion(session, "done", "private result")
        assert event["browser"] is True
        assert event["system"]["delivery"] == "unknown"
        assert "private result" not in str(event["system"])
    assert len(calls) == 2
    for (argv, kwargs), sid in zip(calls, ("ses_a", "ses_b")):
        script = base64.b64decode(argv[-1]).decode("utf-16le")
        assert base64.b64encode(f"http://127.0.0.1:49123/react/?session={sid}".encode()).decode() in script
        assert "private result" not in script
        assert "-STA" in argv
        assert kwargs["timeout"] == 75
        assert "$notify.add_BalloonTipClicked" in script
        assert "Application]::Run($context)" in script
        assert "$notify.Dispose()" in script


def test_nonblocking_path_snapshots_target_before_background_execution(monkeypatch):
    seen = []
    monkeypatch.setattr(notifications, "_send_completion_system", lambda title, body, sid: seen.append(sid) or {"ok": True})
    session = SimpleNamespace(id="ses_original", name="Fixture", notification_settings={"system": True})
    async def scenario():
        event = notifications.dispatch_completion_nonblocking(session, "done", "done")
        session.id = "ses_changed"
        await asyncio.sleep(0.1)
        return event
    event = asyncio.run(scenario())
    assert event["system"]["method"] == "background"
    assert seen == ["ses_original"]


def test_no_session_link_preserves_existing_sender(monkeypatch):
    monkeypatch.setattr(config, "resolve_pan_api_url", lambda: "http://external.example")
    assert notifications.completion_deep_link("ses_a") is None
    script = base64.b64decode(notifications._windows_notifyicon_command("title", "body")).decode("utf-16le")
    assert "Start-Sleep -Seconds 6" in script
    assert "Start-Process" not in script
