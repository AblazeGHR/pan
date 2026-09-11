from datetime import datetime, timedelta, timezone

import pytest

from packages.core import notifications, reminders, session
from packages.web import server as web_server
from packages.mcp import server as mcp_server


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
