"""Closed-browser sender contract; these tests never launch a notification.

The Windows path is a real WinRT toast recorded in the notification center;
the click target travels inside the toast XML as the validated loopback deep
link and is opened by the default browser. All payloads must stay base64-only
on the command line, and no message loop / NotifyIcon may come back.
"""
import asyncio
import base64
import re
from types import SimpleNamespace

import pytest

from packages.core import notifications, config, windows_toast

LITERAL_PATTERN = re.compile(r"FromBase64String\('([A-Za-z0-9+/=]*)'\)")


def _decoded_parts(argv):
    script = base64.b64decode(argv[-1]).decode("utf-16le")
    literals = LITERAL_PATTERN.findall(script)
    xml = base64.b64decode(literals[0]).decode("utf-8")
    tag = base64.b64decode(literals[1]).decode("utf-8")
    group = base64.b64decode(literals[2]).decode("utf-8")
    return script, xml, tag, group


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
    monkeypatch.setattr(windows_toast, "_powershell_executable", lambda: "powershell.exe")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"ok":true,"method":"windows.toast","setting":"Enabled"}', stderr="")

    monkeypatch.setattr(windows_toast.subprocess, "run", fake_run)
    monkeypatch.setattr(notifications, "_system_sender", notifications.default_system_sender)
    for sid in ("ses_a", "ses_b"):
        session = SimpleNamespace(id=sid, name="Fixture", notification_settings={"browser": True, "system": True})
        event = notifications.dispatch_completion(session, "done", "private result")
        assert event["browser"] is True
        assert event["system"]["ok"] is True
        assert event["system"]["method"] == "windows.toast"
        assert event["system"]["delivery"] == "unknown"
        assert "private result" not in str(event["system"])
    assert len(calls) == 2
    for (argv, kwargs), sid in zip(calls, ("ses_a", "ses_b")):
        script, xml, tag, group = _decoded_parts(argv)
        assert f"http://127.0.0.1:49123/react/?session={sid}" in xml
        assert "private result" not in script
        assert "private result" in xml
        assert tag == f"completion-{sid}"
        assert group == "Pan-completions"
        assert "-STA" in argv
        assert "-ExecutionPolicy" not in argv
        assert kwargs["timeout"] == 20
        assert "NotifyIcon" not in script
        assert "$notify.add_BalloonTipClicked" not in script
        assert "Application]::Run($context)" not in script
        assert "Start-Sleep" not in script


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
    monkeypatch.setattr(notifications.platform, "system", lambda: "Windows")
    monkeypatch.setattr(windows_toast, "_powershell_executable", lambda: "powershell.exe")
    calls = []
    monkeypatch.setattr(
        windows_toast.subprocess, "run",
        lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr=""),
    )
    result = notifications.default_system_sender("title", "body")
    assert result["ok"] is True
    assert "delivery" not in result
    assert len(calls) == 1
    script, xml, tag, group = _decoded_parts(calls[0])
    # No click target at all: plain 2-arg injectable-style send stays possible.
    assert "launch=" not in xml and "<action" not in xml
    assert tag == "" and group == ""
    assert "Start-Process" not in script
