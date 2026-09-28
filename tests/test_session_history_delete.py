import asyncio
from types import SimpleNamespace

from packages.core import session as sess
from packages.web import server


def test_delete_history_message_rewrites_jsonl_and_rejects_stale_id(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path)
    sess._cache.clear()
    session = sess.create("history-delete")
    session.history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    sess.save_full(session)

    sess.ensure_history_message_ids(session)
    page = server._api_history(session.id, session.history, include_identity=True)
    result = asyncio.run(server.api_delete_session_history(session.id, page[0]["messageId"]))
    assert result["ok"] is True
    remaining = sess.get(session.id).history
    assert len(remaining) == 1
    assert remaining[0]["role"] == "assistant" and remaining[0]["content"] == "world"
    assert sess._read_jsonl(sess._history_path(session.id)) == sess.get(session.id).history

    stale = asyncio.run(server.api_delete_session_history(session.id, page[0]["messageId"]))
    assert stale["ok"] is False
    assert stale["error"]["code"] == "message_not_found"


def test_delete_history_message_hides_internal_roles(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path)
    sess._cache.clear()
    session = sess.create("history-delete-internal")
    session.history = [{"role": "tool", "content": "internal", "_pan_message_id": "msg_internal"}]
    sess.save_full(session)
    result = asyncio.run(server.api_delete_session_history(session.id, "msg_internal"))
    assert result["ok"] is False
    assert result["error"]["code"] == "message_not_deletable"


def test_delete_history_message_resolves_legacy_wire_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path)
    sess._cache.clear()
    session = sess.create("history-delete-legacy")
    session.history = [
        {"role": "user", "content": "one"},
        {"role": "tool", "content": "internal"},
        {"role": "assistant", "content": "two"},
    ]
    sess.save_full(session)
    epoch = str(getattr(session, "history_epoch", None) or "legacy")
    page = server._api_history(
        session.id, session.history, include_identity=True, history_epoch=epoch)
    legacy_id = page[0]["messageId"]
    assert legacy_id.startswith("legacy:")

    result = asyncio.run(server.api_delete_session_history(session.id, legacy_id))
    assert result["ok"] is True
    remaining = sess.get(session.id).history
    assert [message["content"] for message in remaining] == ["internal", "two"]

    stale = asyncio.run(server.api_delete_session_history(
        session.id, f"legacy:{session.id}:stale-epoch:0"))
    assert stale["ok"] is False
    assert stale["error"]["code"] == "message_not_found"

    page2 = server._api_history(
        session.id, remaining, include_identity=True,
        history_epoch=str(getattr(sess.get(session.id), "history_epoch", None) or "legacy"))
    tool_delete = asyncio.run(server.api_delete_session_history(
        session.id, page2[0]["messageId"]))
    assert tool_delete["ok"] is False
    assert tool_delete["error"]["code"] == "message_not_deletable"


def test_delete_history_message_rejected_while_session_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path)
    sess._cache.clear()
    session = sess.create("history-delete-busy")
    session.history = [
        {"role": "user", "content": "keep me"},
        {"role": "assistant", "content": "keep this too"},
    ]
    sess.save_full(session)
    sess.ensure_history_message_ids(session)
    page = server._api_history(session.id, session.history, include_identity=True)
    before = list(session.history)
    monkeypatch.setattr(
        server.worker,
        "find_alive_worker_by_session",
        lambda _session_id: SimpleNamespace(status="running"),
    )

    result = asyncio.run(server.api_delete_session_history(session.id, page[0]["messageId"]))

    assert result == {
        "ok": False,
        "error": {"code": "session_busy", "message": "任务运行中，无法删除消息"},
    }
    assert session.history == before
    assert sess._read_jsonl(sess._history_path(session.id)) == before
