import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, patch

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


def _append_matching_messages(name, count, phrase="PageNeedle"):
    session = sess.create(name=name, adapter="cbc")
    for index in range(count):
        sess.append_history(session, {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"{phrase} item-{index}; {phrase.lower()} repeated",
        })
    sess.save_full(session)
    return session


def _collect_pages(**params):
    hits = []
    cursor = None
    while True:
        page_params = dict(params)
        if cursor is not None:
            page_params["cursor"] = cursor
        page = _search(**page_params)
        assert set(page) == {"hits", "versions", "limit", "hasMore", "nextCursor"}
        hits.extend(page["hits"])
        assert page["hasMore"] is (page["nextCursor"] is not None)
        if page["nextCursor"] is None:
            return hits
        cursor = page["nextCursor"]


def _assert_cursor_stale(cursor, **params):
    with pytest.raises(HTTPException) as error:
        _search(**params, cursor=cursor)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "history_search_cursor_expired"


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


def test_api_pages_all_single_session_and_global_hits_in_stable_order():
    first = _append_matching_messages("search page first", 217)
    second = _append_matching_messages("search page second", 139)

    single_hits = _collect_pages(q="PageNeedle", sessionId=first.id, limit=67)
    assert [hit["messageIndex"] for hit in single_hits] == list(range(217))
    assert len({hit["messageId"] for hit in single_hits}) == 217
    assert all(hit["sessionId"] == first.id for hit in single_hits)
    assert all(set(hit) == {
        "sessionId", "messageId", "messageIndex", "role", "snippet",
        "historyEpoch", "historyRevision", "historyTotal",
    } for hit in single_hits)

    capped_first = _search(q="PageNeedle", sessionId=first.id, limit=10_000)
    assert capped_first["limit"] == 100
    assert len(capped_first["hits"]) == 100
    assert capped_first["hasMore"] is True
    capped_second = _search(
        q="PageNeedle", sessionId=first.id, limit=10_000,
        cursor=capped_first["nextCursor"],
    )
    assert len(capped_second["hits"]) == 100
    assert capped_second["nextCursor"] is not None
    capped_third = _search(
        q="PageNeedle", sessionId=first.id, limit=10_000,
        cursor=capped_second["nextCursor"],
    )
    assert len(capped_third["hits"]) == 17
    assert capped_third["nextCursor"] is None

    global_hits = _collect_pages(q="PageNeedle", limit=81)
    expected = [
        (session.id, index, row["messageId"])
        for session in sess.list_all(load_history=True)
        for index, row in enumerate(session.history)
        if row.get("role") in {"user", "assistant"}
        and row.get("messageId", "").startswith("pan:")
        and "pageneedle" in row.get("content", "").lower()
    ]
    assert [(hit["sessionId"], hit["messageIndex"], hit["messageId"])
            for hit in global_hits] == expected
    assert len(global_hits) == 356
    assert sum(hit["sessionId"] == first.id for hit in global_hits) == 217
    assert sum(hit["sessionId"] == second.id for hit in global_hits) == 139
    assert len({hit["messageId"] for hit in global_hits}) == 356
    assert all(
        hit["historyTotal"] == (217 if hit["sessionId"] == first.id else 139)
        for hit in global_hits
    )


def test_api_pagination_boundaries_empty_results_and_literal_short_cjk_punctuation():
    session = sess.create(name="search literals", adapter="cbc")
    sess.append_history(session, {
        "role": "user",
        "content": 'MixedCase, 中文你好世界; quote "OR" +++ [] needle needle',
    })
    sess.append_history(session, {
        "role": "assistant",
        "content": 'MixedCase second 中文; quote "OR" +++ [] needle',
    })
    sess.save_full(session)

    expected_hits = {
        "mixedcase": 2, "xEd": 2, "中文": 2, "中": 2,
        "你好世界": 1, '"OR"': 2, "+++": 2, "[]": 2, "needle": 2,
    }
    for query, expected_count in expected_hits.items():
        page = _search(q=query, sessionId=session.id, limit=1)
        assert page["hits"], query
        if expected_count == 2:
            assert page["nextCursor"] is not None, query
            second_page = _search(
                q=query, sessionId=session.id, limit=1, cursor=page["nextCursor"],
            )
            assert len(page["hits"]) + len(second_page["hits"]) == expected_count, query
            assert second_page["nextCursor"] is None, query
            assert second_page["hasMore"] is False
        else:
            assert len(page["hits"]) == expected_count
            assert page["nextCursor"] is None

    no_matches = _search(q="not-present-literal", sessionId=session.id, limit=5)
    assert no_matches["hits"] == []
    assert no_matches["hasMore"] is False
    assert no_matches["nextCursor"] is None

    exact_boundary = _search(q="MixedCase", sessionId=session.id, limit=2)
    assert len(exact_boundary["hits"]) == 2
    assert exact_boundary["hasMore"] is False
    assert exact_boundary["nextCursor"] is None


def test_api_counts_one_literal_hit_per_real_user_or_assistant_pan_message():
    session = sess.create(name="search identity filters", adapter="cbc")
    user_id = "pan:00000000-0000-4000-8000-000000000001"
    assistant_id = "pan:00000000-0000-4000-8000-000000000002"
    sess.replace_history(session, [
        {"role": "user", "content": "LiteralNeedle repeated literalneedle",
         "messageId": user_id, "source": "qq"},
        {"role": "assistant", "content": "literalneedle assistant",
         "messageId": assistant_id},
        {"role": "user", "content": "literalneedle legacy",
         "messageId": "legacy:session:epoch:2"},
        {"role": "thinking", "content": "literalneedle thinking",
         "messageId": "pan:00000000-0000-4000-8000-000000000003"},
        {"role": "tool", "content": "literalneedle tool",
         "messageId": "pan:00000000-0000-4000-8000-000000000004"},
        {"role": "error", "content": "literalneedle error",
         "messageId": "pan:00000000-0000-4000-8000-000000000005"},
        {"role": "system", "content": "literalneedle system",
         "messageId": "pan:00000000-0000-4000-8000-000000000006"},
        {"role": "user", "content": "literalneedle injected prompt",
         "messageId": "pan:00000000-0000-4000-8000-000000000007",
         "source": "system_prompt"},
    ])
    sess.save_full(session)

    result = _search(q="LITERALNEEDLE", sessionId=session.id, limit=1)
    assert [hit["messageId"] for hit in result["hits"]] == [user_id]
    assert result["hits"][0]["role"] == "user"
    assert result["nextCursor"]
    second_page = _search(
        q="LITERALNEEDLE", sessionId=session.id, limit=1,
        cursor=result["nextCursor"],
    )
    assert [hit["messageId"] for hit in second_page["hits"]] == [assistant_id]
    assert second_page["nextCursor"] is None


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


def test_api_cursor_is_bound_to_query_scope_and_page_size():
    first = _append_matching_messages("search cursor binding", 4, "BoundNeedle")
    second = _append_matching_messages("search cursor other", 2, "BoundNeedle")
    page = _search(q="BoundNeedle", sessionId=first.id, limit=1)
    cursor = page["nextCursor"]
    assert cursor and len(cursor) <= server._HISTORY_SEARCH_CURSOR_MAX_LENGTH

    _assert_cursor_stale(cursor, q="changed", sessionId=first.id, limit=1)
    _assert_cursor_stale(cursor, q="BoundNeedle", sessionId=second.id, limit=1)
    _assert_cursor_stale(cursor, q="BoundNeedle", sessionId=first.id, limit=2)
    body, signature = cursor.split(".")
    tampered = body + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
    _assert_cursor_stale(tampered,
                         q="BoundNeedle", sessionId=first.id, limit=1)


def test_api_cursor_expires_after_append_full_save_reimport_delete_or_reorder():
    appended = _append_matching_messages("search cursor append", 3, "CursorNeedle")
    append_page = _search(q="CursorNeedle", sessionId=appended.id, limit=1)
    sess.append_history(appended, {"role": "assistant", "content": "CursorNeedle new"})
    sess.save(appended)
    _assert_cursor_stale(
        append_page["nextCursor"], q="CursorNeedle", sessionId=appended.id, limit=1,
    )

    replaced = _append_matching_messages("search cursor replace", 3, "CursorNeedle")
    replace_page = _search(q="CursorNeedle", sessionId=replaced.id, limit=1)
    old_epoch = replaced.history_epoch
    replacement = [dict(row) for row in replaced.history]
    replacement[-1]["content"] = "CursorNeedle replacement"
    sess.replace_history(replaced, replacement)
    sess.save_full(replaced)
    assert replaced.history_epoch != old_epoch
    _assert_cursor_stale(
        replace_page["nextCursor"], q="CursorNeedle", sessionId=replaced.id, limit=1,
    )

    deleted = _append_matching_messages("search cursor deleted", 3, "CursorNeedle")
    survivor = _append_matching_messages("search cursor survivor", 3, "CursorNeedle")
    delete_page = _search(q="CursorNeedle", limit=1)
    assert delete_page["nextCursor"]
    sess.delete(deleted.id)
    _assert_cursor_stale(delete_page["nextCursor"], q="CursorNeedle", limit=1)
    assert survivor.id in {session.id for session in sess.list_all(load_history=False)}

    first = _append_matching_messages("search cursor order first", 2, "OrderNeedle")
    second = _append_matching_messages("search cursor order second", 2, "OrderNeedle")
    order_page = _search(q="OrderNeedle", limit=1)
    assert order_page["nextCursor"]
    assert sess.apply_order([second.id, first.id]) is None
    _assert_cursor_stale(order_page["nextCursor"], q="OrderNeedle", limit=1)

    single_scope_page = _search(
        q="OrderNeedle", sessionId=first.id, limit=1,
    )
    assert single_scope_page["nextCursor"]
    assert sess.apply_order([first.id, second.id]) is None
    _assert_cursor_stale(
        single_scope_page["nextCursor"],
        q="OrderNeedle", sessionId=first.id, limit=1,
    )


def test_api_cursor_expires_when_total_changes_without_revision_change():
    session = _append_matching_messages("search cursor total", 3, "TotalNeedle")
    page = _search(q="TotalNeedle", sessionId=session.id, limit=1)
    original_revision = session.history_revision
    session.history.append({
        "role": "user",
        "content": "TotalNeedle total-only row",
        "messageId": "pan:00000000-0000-4000-8000-000000000099",
    })
    session.summary_projection["history_total"] = len(session.history)
    assert session.history_revision == original_revision

    _assert_cursor_stale(
        page["nextCursor"], q="TotalNeedle", sessionId=session.id, limit=1,
    )


def test_api_rejects_a_page_when_history_changes_during_the_search(monkeypatch):
    session = _append_matching_messages("search concurrent change", 3, "RaceNeedle")
    original_search = history_search_index.search_history
    changed = False

    def search_then_append(*args, **kwargs):
        nonlocal changed
        result = original_search(*args, **kwargs)
        if not changed:
            changed = True
            sess.append_history(session, {
                "role": "assistant", "content": "RaceNeedle concurrent append",
            })
            sess.save(session)
        return result

    monkeypatch.setattr(history_search_index, "search_history", search_then_append)
    with pytest.raises(HTTPException) as error:
        _search(q="RaceNeedle", sessionId=session.id, limit=1)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "history_search_snapshot_changed"


def test_api_cursor_expires_after_native_session_reimport(tmp_path):
    native_id = "history-search-reimport"

    def source_file(name, contents):
        path = tmp_path / name
        path.write_text("".join(
            json.dumps({
                "type": "message",
                "role": role,
                "sessionId": native_id,
                "content": [{"type": "text", "text": content}],
                "timestamp": index + 1,
            }) + "\n"
            for index, (role, content) in enumerate(contents)
        ), encoding="utf-8")
        return path

    source1 = source_file("source1.jsonl", [
        ("user", "ReimportNeedle first"),
        ("assistant", "ReimportNeedle second"),
        ("user", "ReimportNeedle third"),
    ])
    with patch.object(server, "broadcast", new=AsyncMock()), \
         patch("packages.core.adapters.cbc.sessions._resolve_session_file",
               return_value=source1):
        imported = asyncio.run(server.api_cbc_sessions_import({
            "session_id": native_id, "cwd": "D:/tmp/history-search-reimport",
        }))
    assert "error" not in imported, imported
    session_id = imported["id"]

    page = _search(q="ReimportNeedle", sessionId=session_id, limit=1)
    assert page["nextCursor"]
    old_epoch = sess.get(session_id).history_epoch
    source2 = source_file("source2.jsonl", [
        ("user", "ReimportNeedle updated first"),
        ("assistant", "ReimportNeedle updated second"),
        ("user", "ReimportNeedle updated third"),
    ])
    with patch.object(server, "broadcast", new=AsyncMock()), \
         patch("packages.core.adapters.cbc.sessions._resolve_session_file",
               return_value=source2):
        reimported = asyncio.run(server.api_cbc_sessions_import({
            "session_id": native_id, "cwd": "D:/tmp/history-search-reimport",
        }))
    assert reimported.get("reimported") is True
    assert sess.get(session_id).history_epoch != old_epoch
    _assert_cursor_stale(
        page["nextCursor"], q="ReimportNeedle", sessionId=session_id, limit=1,
    )


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
