import asyncio
import sqlite3

import pytest
from fastapi import HTTPException

from packages.core import session as sess
from packages.core import history_search_index
from packages.web import server


def _append_session(name, role, content):
    session = sess.create(name=name, adapter="cbc")
    sess.append_history(session, {"role": role, "content": content})
    sess.save(session)
    return session


def _search(**params):
    return asyncio.run(server.api_history_search(**params))


def _index_path():
    return sess.SESSION_DIR.parent / "history_search.sqlite3"


def test_api_supports_current_session_and_global_scope_and_rebuilds_deleted_index(
):
    first = _append_session("search first", "user", "Global keyword in first")
    second = _append_session("search second", "assistant", "Global keyword in second")

    # A blank search is lazy: it does not create the disposable database.
    empty = _search(q="   ")
    assert empty["hits"] == []
    assert not _index_path().exists()

    global_result = _search(q="KEYWORD")
    assert {hit["sessionId"] for hit in global_result["hits"]} == {
        first.id, second.id,
    }
    assert {version["sessionId"] for version in global_result["versions"]} == {
        first.id, second.id,
    }
    for hit in global_result["hits"]:
        assert set(hit) == {
            "sessionId", "messageId", "messageIndex", "role", "snippet",
            "historyEpoch", "historyRevision", "historyTotal",
        }
        assert hit["messageId"].startswith("pan:")
        assert hit["messageIndex"] == 0
        assert hit["historyTotal"] == 1

    scoped = _search(q="keyword", sessionId=first.id)
    assert [hit["sessionId"] for hit in scoped["hits"]] == [first.id]
    assert [version["sessionId"] for version in scoped["versions"]] == [first.id]

    capped = _search(q="keyword", limit=1)
    assert capped["limit"] == 1
    assert capped["hasMore"] is True
    assert len(capped["hits"]) == 1

    index_path = _index_path()
    durable_before = sess._history_path(first.id).read_bytes()
    index_path.unlink()
    rebuilt = _search(q="keyword", sessionId=first.id)
    assert len(rebuilt["hits"]) == 1
    assert index_path.exists()
    assert sess._history_path(first.id).read_bytes() == durable_before

    sess.delete(second.id)
    after_delete = _search(q="keyword")
    assert second.id not in {hit["sessionId"] for hit in after_delete["hits"]}
    assert second.id not in {version["sessionId"] for version in after_delete["versions"]}


def test_append_same_id_revision_and_full_save_epoch_are_reflected_once():
    session = _append_session("search revision", "user", "old durable phrase")
    original_id = session.history[0]["messageId"]
    original_epoch = session.history_epoch
    original_revision = session.history_revision
    assert len(_search(q="durable")["hits"]) == 1

    replacement = [dict(row) for row in session.history]
    replacement[0]["content"] = "new durable phrase"
    sess.replace_history(session, replacement)
    sess.save_full(session)
    assert session.history[0]["messageId"] == original_id
    assert session.history_epoch != original_epoch
    assert session.history_revision > original_revision

    assert _search(q="old durable")["hits"] == []
    replaced = _search(q="new durable")
    assert [hit["messageId"] for hit in replaced["hits"]] == [original_id]
    assert replaced["hits"][0]["historyEpoch"] == session.history_epoch
    assert replaced["hits"][0]["historyRevision"] == session.history_revision

    revision_before_append = session.history_revision
    sess.append_history(session, {
        "role": "assistant",
        "content": "new durable phrase again",
    })
    sess.save(session)
    appended = _search(q="new durable")
    assert len(appended["hits"]) == 2
    assert len({hit["messageId"] for hit in appended["hits"]}) == 2
    assert all(hit["historyRevision"] == revision_before_append + 1
               for hit in appended["hits"])


def test_cold_session_search_loads_canonical_history_before_building_index():
    session = _append_session("search cold", "assistant", "cold history phrase")
    sess._cache.clear()
    sess._all_loaded = False

    result = _search(q="cold history")

    assert [hit["sessionId"] for hit in result["hits"]] == [session.id]
    assert result["hits"][0]["messageId"] == session.history[0]["messageId"]
    assert result["hits"][0]["historyEpoch"] == session.history_epoch
    assert sess.get(session.id, load_history=False)._history_loaded is False


def test_missing_session_and_index_failure_are_clear_and_do_not_block_history_save(
    monkeypatch,
):
    assert _search(q="content", sessionId="missing-session") == {
        "error": "Session not found",
    }

    session = sess.create(name="search failure", adapter="cbc")
    original_connect = history_search_index._connect

    def fail_open(_path):
        raise sqlite3.OperationalError("no such module: fts5")

    monkeypatch.setattr(history_search_index, "_connect", fail_open)
    with pytest.raises(HTTPException) as error:
        _search(q="searchable")
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "history_search_unavailable"
    assert "FTS5" in error.value.detail["message"]

    sess.append_history(session, {"role": "user", "content": "searchable body"})
    sess.save(session)
    assert len(sess._read_jsonl(sess._history_path(session.id))) == 1
    monkeypatch.setattr(history_search_index, "_connect", original_connect)
