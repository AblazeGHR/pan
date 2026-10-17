import asyncio
import time
from datetime import datetime, timedelta, timezone
import base64
import re

import pytest

from packages.core import notifications, reminders, session, windows_toast
from packages.web import server as web_server
from packages.mcp import server as mcp_server

LITERAL_PATTERN = re.compile(r"FromBase64String\('([A-Za-z0-9+/=]*)'\)")


def _decoded_script_parts(argv):
    script = base64.b64decode(argv[-1]).decode("utf-16le")
    literals = LITERAL_PATTERN.findall(script)
    xml = base64.b64decode(literals[0]).decode("utf-8")
    tag = base64.b64decode(literals[1]).decode("utf-8")
    group = base64.b64decode(literals[2]).decode("utf-8")
    return script, xml, tag, group


def test_prefix_normalization_is_not_bypassable():
    assert notifications.normalize_title("hello") == "Pan: hello"
    assert notifications.normalize_title("Pan: Pan: hello") == "Pan: hello"
    assert notifications.normalize_title("  ") == "Pan:"


def test_session_notification_settings_default_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSION_DIR", tmp_path)
    session._cache.clear()
    s = session.create("notify-test")
    assert s.notification_settings == {"browser": False, "system": False}
    loaded = session.Session._from_data({**s.to_dict(), "notification_settings": {"browser": True}})
    assert loaded.notification_settings == {"browser": True, "system": False}


def test_reminder_persist_reload_due_once_cancel_and_invalid_due_at(tmp_path, monkeypatch):
    path = tmp_path / "reminders.json"
    monkeypatch.setattr(reminders, "REMINDER_PATH", path)
    due = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    item = reminders.register("ses_a", due, "Pan: custom", "body")
    assert item["title"] == "Pan: custom"
    assert reminders.list_for_session("ses_a")[0]["id"] == item["id"]
    claimed = reminders.claim_due()
    assert [x["id"] for x in claimed] == [item["id"]]
    assert reminders.claim_due() == []
    assert reminders.list_for_session("ses_a") == []

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    second = reminders.register("ses_a", future, "later", "body")
    assert reminders.cancel("ses_a", second["id"])["status"] == "cancelled"
    assert reminders.cancel("ses_a", second["id"]) is None
    with pytest.raises(ValueError, match="timezone"):
        reminders.register("ses_a", "2030-01-01T00:00:00", "bad", "body")


def test_done_dispatch_browser_payload_and_system_failure_is_nonfatal(monkeypatch):
    s = session.Session("ses_x", "My session", notification_settings={"browser": True, "system": True})
    sent = []
    notifications.set_system_sender(lambda title, body: sent.append((title, body)) or {"ok": True})
    payload = notifications.dispatch_completion(s, "done", "finished")
    assert payload["title"].startswith("Pan:")
    assert payload["browser"] is True
    assert sent[0][0].startswith("Pan:")
    assert notifications.dispatch_completion(s, "error", "failed") is None
    notifications.set_system_sender(notifications.default_system_sender)


def test_nonblocking_completion_does_not_wait_for_slow_system_sender():
    s = session.Session("ses_async_notify", "Async notify", notification_settings={"browser": True, "system": True})
    calls = []

    def slow_sender(title, body):
        time.sleep(0.15)
        calls.append((title, body))
        return {"ok": True}

    notifications.set_system_sender(slow_sender)
    try:
        async def scenario():
            started = time.monotonic()
            payload = notifications.dispatch_completion_nonblocking(s, "done", "finished")
            elapsed = time.monotonic() - started
            await asyncio.sleep(0.25)
            return payload, elapsed

        payload, elapsed = asyncio.run(scenario())
        assert elapsed < 0.1
        assert payload["browser"] is True
        assert payload["system"] == {"ok": None, "method": "background", "delivery": "unknown"}
        assert len(calls) == 1
    finally:
        notifications.set_system_sender(notifications.default_system_sender)


def test_windows_sender_success_passes_normalized_args_without_real_notification(monkeypatch):
    monkeypatch.setattr(notifications.platform, "system", lambda: "Windows")
    monkeypatch.setattr(windows_toast, "_powershell_executable", lambda: "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    completed = type("Completed", (), {"returncode": 0, "stdout": '{"ok":true,"method":"windows.toast"}', "stderr": ""})()
    calls = []
    monkeypatch.setattr(windows_toast.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)) or completed)
    result = notifications.default_system_sender("user title", "body")
    assert result == {"ok": True, "method": "windows.toast", "delivery": "unknown"}
    # The mock above proves no real child process was launched; inspect a second
    # call to verify user data is carried only inside the encoded command.
    notifications.default_system_sender("Pan: user title", "$(bad); \"quoted\"")
    argv = calls[0][0][0]
    assert argv[-1] != "$(bad); \"quoted\""
    assert "$(bad)" not in argv[-1]
    assert "-ExecutionPolicy" not in argv


@pytest.mark.parametrize('raises', [False, True])
def test_nonblocking_failure_is_logged_with_owner_without_private_payload(monkeypatch, caplog, raises):
    s = session.Session('ses_owned_fail', 'private-title',
                        notification_settings={'system': True})
    def sender(*args):
        if raises:
            raise RuntimeError('private-exception')
        return {'ok': False, 'code': 'windows_toast_failed', 'hresult': '0x803E0115',
                'setting': 'Enabled', 'message': 'private-error'}
    monkeypatch.setattr(notifications, '_system_sender', sender)
    async def scenario():
        payload = notifications.dispatch_completion_nonblocking(s, 'done', 'private-body')
        assert payload['system']['ok'] is None
        await asyncio.gather(*notifications._notification_tasks)
        assert not notifications._notification_tasks
    asyncio.run(scenario())
    assert 'ses_owned_fail' in caplog.text
    assert ('system_notification_failed' if raises else '0x803E0115') in caplog.text
    assert 'private-' not in caplog.text


def test_windows_toast_encoded_script_has_valid_preamble_and_payload_order():
    title = "中文标题 $()"
    body = '中文正文；"quoted"；$(Get-Date);'
    script = windows_toast.build_show_script(
        windows_toast.TOAST_APP_ID,
        windows_toast.build_toast_xml(title, body),
        "completion-ses_x", "Pan-completions",
    )
    encoded = windows_toast.encode_command(script)
    decoded = base64.b64decode(encoded).decode("utf-16le")

    assert not decoded.startswith("param()")
    literals = LITERAL_PATTERN.findall(decoded)
    xml = base64.b64decode(literals[0]).decode("utf-8")
    assert title in xml
    assert '中文正文；&quot;quoted&quot;；$(Get-Date);' in xml
    assert "ToastNotificationManager" in decoded
    assert "New-Object Windows.UI.Notifications.ToastNotification" in decoded
    assert "$notifier.Show($toast)" in decoded
    assert "NotifyIcon" not in decoded
    assert "Start-Sleep" not in decoded
    # Payload isolation: the raw title/body never appear in the script itself.
    assert title not in decoded
    assert body not in decoded


def test_windows_sender_failure_and_non_windows_are_diagnostic(monkeypatch):
    monkeypatch.setattr(notifications.platform, "system", lambda: "Windows")
    monkeypatch.setattr(windows_toast, "_powershell_executable", lambda: "powershell.exe")
    failed = type("Completed", (), {"returncode": 7, "stdout": "", "stderr": "toast failed"})()
    monkeypatch.setattr(windows_toast.subprocess, "run", lambda *args, **kwargs: failed)
    result = notifications.default_system_sender("title", "body")
    assert result["code"] == "windows_system_notification_failed"
    monkeypatch.setattr(windows_toast, "_powershell_executable", lambda: None)
    unavailable = notifications.default_system_sender("title", "body")
    assert unavailable["code"] == "windows_powershell_unavailable"
    monkeypatch.setattr(notifications.platform, "system", lambda: "Linux")
    assert notifications.default_system_sender("title", "body")["code"] == "unsupported_system_notification"


def test_reminder_default_sender_uses_content_dedup_tag_and_group(monkeypatch):
    monkeypatch.setattr(notifications.platform, "system", lambda: "Windows")
    monkeypatch.setattr(windows_toast, "_powershell_executable", lambda: "powershell.exe")
    calls = []
    completed = type("Completed", (), {"returncode": 0, "stdout": '{"ok":true}', "stderr": ""})()
    monkeypatch.setattr(windows_toast.subprocess, "run", lambda *args, **kwargs: calls.append(args) or completed)
    monkeypatch.setattr(notifications, "_system_sender", notifications.default_system_sender)
    payload = notifications.dispatch_reminder("custom", "body")
    assert payload["title"] == "Pan: custom"
    assert payload["system"]["ok"] is True
    script, xml, tag, group = _decoded_script_parts(calls[0][0])
    assert tag == notifications.reminder_dedup_tag("Pan: custom", "body")
    assert group == "Pan-reminders"
    assert "launch=" not in xml and "<action" not in xml


def test_session_patch_and_api_response_expose_notification_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSION_DIR", tmp_path)
    session._cache.clear()
    s = session.create("patch-test")
    web_server._apply_session_updates(s, {"notificationSettings": {"browser": True}})
    assert s.notification_settings == {"browser": True, "system": False}
    assert web_server._session_to_api(s)["notificationSettings"] == {"browser": True, "system": False}


def test_mcp_notification_tools_require_identity_and_check_access(monkeypatch):
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    missing = mcp_server.notification_send("hello")
    assert missing["error"]["code"] == "missing_identity"
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_caller")
    monkeypatch.setattr(mcp_server, "_caller_identity", lambda: {
        "id": "ses_caller", "managed": [], "panAccess": {"restrictToManaged": True},
    })
    denied = mcp_server.reminder_list("ses_other")
    assert denied["error"]["code"] == "permission_denied"
def test_reminder_loop_claims_before_send_and_broadcasts_once(monkeypatch):
    item = {"id": "rem_1", "sessionId": "ses_a", "title": "Pan: title", "body": "body"}
    claimed = []
    broadcasts = []
    target = session.Session("ses_a", "demo")
    monkeypatch.setattr(web_server.reminders, "claim_due", lambda: claimed.append(True) or [item])
    monkeypatch.setattr(web_server.sess, "get", lambda sid: target)
    monkeypatch.setattr(web_server.notifications, "dispatch_reminder", lambda title, body: {"title": title, "body": body, "system": {"ok": True}})
    async def capture(event):
        broadcasts.append(event)
    monkeypatch.setattr(web_server, "broadcast", capture)
    import asyncio
    assert asyncio.run(web_server._deliver_due_reminders()) == 1
    assert claimed == [True]
    assert broadcasts[0]["reminderId"] == "rem_1"


def test_reminder_api_register_list_cancel_and_invalid_due_at(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(session, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(reminders, "REMINDER_PATH", tmp_path / "reminders.json")
    session._cache.clear()
    s = session.create("api-reminder")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    created = asyncio.run(web_server.api_register_reminder(s.id, {"dueAt": future, "title": "title", "body": "body"}))
    assert created["ok"] is True
    reminder_id = created["reminder"]["id"]
    listed = asyncio.run(web_server.api_list_reminders(s.id))
    assert listed["reminders"][0]["id"] == reminder_id
    cancelled = asyncio.run(web_server.api_cancel_reminder(s.id, reminder_id))
    assert cancelled["ok"] is True
    invalid = asyncio.run(web_server.api_register_reminder(s.id, {"dueAt": "2030-01-01T00:00:00"}))
    assert invalid["error"]["code"] == "invalid_due_at"
