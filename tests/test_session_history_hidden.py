from __future__ import annotations

import asyncio
import copy
import hashlib
from pathlib import Path

from packages.core import session as sess
from packages.core.hidden_messages import HiddenMessageStore
from packages.web import server


def _prepare_session(tmp_path, monkeypatch, name, history):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path)
    sess._cache.clear()
    session = sess.create(name)
    session.history = copy.deepcopy(history)
    sess.save_full(session)
    return session


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_hide_preserves_transcript_and_is_idempotent(tmp_path, monkeypatch):
    session = _prepare_session(
        tmp_path,
        monkeypatch,
        "history-hidden",
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ],
    )
    history_path = sess._history_path(session.id)
    session_path = sess._path(session.id)
    before_history_hash = _sha256(history_path)
    before_session_hash = _sha256(session_path)
    before_history = copy.deepcopy(sess.get(session.id).history)

    page = server._api_history(
        session.id,
        session.history,
        include_identity=True,
        history_epoch=session.history_epoch,
    )
    message_id = page[0]["messageId"]
    result = asyncio.run(server.api_delete_session_history(session.id, message_id))

    assert result == {"ok": True, "messageId": message_id, "historyTotal": 1}
    assert _sha256(history_path) == before_history_hash
    assert _sha256(session_path) == before_session_hash
    assert sess.get(session.id).history == before_history
    assert len(sess.get(session.id).history) == 2
    assert server._hidden_message_store().hidden_ids(session.id) == {message_id}

    repeated = asyncio.run(server.api_delete_session_history(session.id, message_id))
    assert repeated == result
    assert _sha256(history_path) == before_history_hash
    assert _sha256(session_path) == before_session_hash


def test_hidden_message_is_absent_from_detail_page(tmp_path, monkeypatch):
    session = _prepare_session(
        tmp_path,
        monkeypatch,
        "history-hidden-outputs",
        [
            {"role": "user", "content": "keep one"},
            {"role": "assistant", "content": "hide this secret"},
            {"role": "user", "content": "keep two"},
        ],
    )
    wire = server._api_history(
        session.id,
        session.history,
        include_identity=True,
        history_epoch=session.history_epoch,
    )
    hidden_id = wire[1]["messageId"]
    result = asyncio.run(server.api_delete_session_history(session.id, hidden_id))
    assert result["ok"] is True
    assert result["historyTotal"] == 2

    detail = server._session_to_api(sess.get(session.id))
    assert [row["content"] for row in detail["history"]] == ["keep one", "keep two"]
    assert detail["historyTotal"] == 2

    first_page = asyncio.run(server.api_session_history(session.id, before=0, limit=1))
    assert first_page["total"] == 2
    assert first_page["hasMore"] is True
    assert [row["content"] for row in first_page["history"]] == ["keep two"]
    assert first_page["history"][0]["messageId"] == wire[2]["messageId"]

    older_page = asyncio.run(server.api_session_history(session.id, before=first_page["start"], limit=1))
    assert older_page["total"] == 2
    assert older_page["hasMore"] is False
    assert [row["content"] for row in older_page["history"]] == ["keep one"]


def test_legacy_identity_uses_raw_index_after_hiding(tmp_path, monkeypatch):
    session = _prepare_session(
        tmp_path,
        monkeypatch,
        "history-hidden-legacy",
        [
            {"role": "user", "content": "one"},
            {"role": "tool", "content": "internal"},
            {"role": "assistant", "content": "two"},
        ],
    )
    epoch = session.history_epoch
    wire = server._api_history(
        session.id, session.history, include_identity=True, history_epoch=epoch,
    )
    assert wire[0]["messageId"] == f"legacy:{session.id}:{epoch}:0"
    assert wire[2]["messageId"] == f"legacy:{session.id}:{epoch}:2"

    hidden = asyncio.run(server.api_delete_session_history(session.id, wire[0]["messageId"]))
    assert hidden["ok"] is True
    page = asyncio.run(server.api_session_history(session.id, before=0, limit=50))
    assert [row["content"] for row in page["history"]] == ["internal", "two"]
    assert page["history"][1]["messageId"] == wire[2]["messageId"]

    internal = asyncio.run(server.api_delete_session_history(session.id, wire[1]["messageId"]))
    assert internal["ok"] is False
    assert internal["error"]["code"] == "message_not_deletable"


def test_invalid_and_unknown_message_ids_keep_compatibility_errors(tmp_path, monkeypatch):
    session = _prepare_session(
        tmp_path,
        monkeypatch,
        "history-hidden-errors",
        [{"role": "user", "content": "hello"}],
    )
    invalid = asyncio.run(server.api_delete_session_history(session.id, "nope"))
    assert invalid["error"]["code"] == "invalid_message_id"
    unknown = asyncio.run(server.api_delete_session_history(session.id, "msg_missing"))
    assert unknown["error"]["code"] == "message_not_found"
    missing = asyncio.run(server.api_delete_session_history("ses_missing", "msg_missing"))
    assert missing["error"]["code"] == "session_not_found"


def test_session_delete_cleans_hidden_message_sidecar(tmp_path, monkeypatch):
    session = _prepare_session(
        tmp_path,
        monkeypatch,
        "history-hidden-cleanup",
        [{"role": "user", "content": "remove me"}],
    )
    message_id = server._api_history(
        session.id, session.history, include_identity=True,
        history_epoch=session.history_epoch,
    )[0]["messageId"]
    asyncio.run(server.api_delete_session_history(session.id, message_id))
    sidecar = tmp_path.parent / "hidden-messages" / f"{session.id}.json"
    assert sidecar.exists()

    server._delete_session_storage(session.id, cleanup_auxiliary=True)
    assert not sidecar.exists()
    assert not sidecar.with_suffix(sidecar.suffix + ".lock").exists()
    assert sess.get(session.id) is None
    assert not sess._history_path(session.id).exists()


def test_ensure_history_message_ids_is_read_only(tmp_path, monkeypatch):
    session = _prepare_session(
        tmp_path,
        monkeypatch,
        "history-hidden-readonly",
        [{"role": "user", "content": "unchanged"}],
    )
    history_path = sess._history_path(session.id)
    session_path = sess._path(session.id)
    before = (_sha256(history_path), _sha256(session_path), copy.deepcopy(session.history))
    assert sess.ensure_history_message_ids(session) is False
    assert (_sha256(history_path), _sha256(session_path), session.history) == before
