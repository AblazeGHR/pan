"""Managed-session readonly invariants."""

import asyncio

from packages.core import session as sess
from packages.core import worker
from packages.web import server


def _pair():
    manager = sess.Session(id="manager", name="manager", managed=["child"])
    child = sess.Session(id="child", name="child", managed_by="manager")
    sess._cache.update({manager.id: manager, child.id: child})
    return manager, child


def test_readonly_defaults_and_legacy_data(monkeypatch, tmp_path):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    sess._cache.clear()
    path = tmp_path / "sessions" / "legacy.json"
    path.parent.mkdir()
    path.write_text('{"id":"legacy","name":"legacy"}', encoding="utf-8")
    loaded = sess.get("legacy")
    assert loaded.readonly_session is False
    assert loaded.to_dict()["readonly_session"] is False


def test_readonly_api_requires_existing_managed_relationship():
    manager, child = _pair()
    result = asyncio.run(server.api_readonly({
        "managerId": manager.id, "sessionId": child.id, "readonlySession": True,
    }))
    assert result["ok"] is True and child.readonly_session is True
    assert sess.get(child.id).readonly_session is True
    result = asyncio.run(server.api_readonly({
        "managerId": manager.id, "sessionId": child.id, "readonlySession": False,
    }))
    assert result["readonlySession"] is False

    other = sess.Session(id="other", name="other")
    sess._cache[other.id] = other
    denied = asyncio.run(server.api_readonly({
        "managerId": manager.id, "sessionId": other.id, "readonlySession": True,
    }))
    assert denied["error"]["code"] == "permission_denied"
    assert other.readonly_session is False


def test_all_durable_delivery_paths_are_blocked_before_enqueue():
    manager, child = _pair()
    child.readonly_session = True
    before = list(child.queue_pending)
    message = worker.READONLY_SESSION_ERROR

    assert asyncio.run(worker.assign(
        child.id, "task", source="agent", source_session_id=manager.id))["result"] == message
    assert asyncio.run(worker.send_session(
        child.id, "message", source="agent", source_session_id=manager.id))["result"] == message
    notify = asyncio.run(worker.enqueue_notice(
        child.id, "notice", source="agent", source_session_id=manager.id))
    assert notify["error"]["message"] == message
    assert child.queue_pending == before
