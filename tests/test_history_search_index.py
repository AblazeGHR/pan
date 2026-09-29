from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from threading import RLock
from types import SimpleNamespace

from packages.core import history_search_index
from packages.core.history_search_index import search_history


def _pan_id(number: int) -> str:
    return f"pan:{number:032x}"


def _session(session_id, history, *, epoch="epoch-a", revision=1):
    return SimpleNamespace(
        id=session_id,
        name=session_id,
        history=history,
        history_epoch=epoch,
        history_revision=revision,
        _history_loaded=True,
        _summary_lock=RLock(),
        summary_projection={"history_total": len(history)},
    )


def _search(path, sessions, query, *, limit=50, live_ids=None, after=None):
    return search_history(
        path,
        sessions,
        [session.id for session in sessions] if live_ids is None else live_ids,
        query,
        limit=limit,
        after=after,
        load_session=lambda _session_id: None,
    )


def test_filters_history_to_one_hit_per_durable_user_or_assistant_message(tmp_path):
    duplicate_id = _pan_id(3)
    history = [
        {"role": "user", "content": "Needle appears; needle repeats.", "messageId": _pan_id(1)},
        {"role": "assistant", "content": "needle in assistant body", "messageId": _pan_id(2)},
        {"role": "assistant", "content": "needle first copy", "messageId": duplicate_id},
        {"role": "assistant", "content": "needle latest copy", "messageId": duplicate_id},
        {"role": "user", "content": "needle in QQ conversation body",
         "messageId": _pan_id(11), "source": "qq"},
        {"role": "thinking", "content": "needle hidden thought", "messageId": _pan_id(4)},
        {"role": "tool", "content": "needle tool output", "messageId": _pan_id(5)},
        {"role": "error", "content": "needle error", "messageId": _pan_id(6)},
        {"role": "system", "content": "needle system", "messageId": _pan_id(7)},
        {"role": "debug", "content": "needle debug", "messageId": _pan_id(8)},
        {"role": "user", "content": "needle injected prompt", "messageId": _pan_id(9),
         "source": "system_prompt"},
        {"role": "user", "content": "needle legacy", "messageId": "legacy:s:epoch:10"},
        {"role": "user", "content": "needle no Pan identity", "nativeItemId": "native-1"},
        {"role": "user", "content": "needle malformed identity", "messageId": "pan:not-a-uuid"},
        {"role": "assistant", "content": "   ", "messageId": _pan_id(10)},
    ]
    session = _session("s-filter", history)

    result = _search(tmp_path / "search.sqlite3", [session], " NEEDLE ")

    assert [hit["messageId"] for hit in result["hits"]] == [
        _pan_id(1), _pan_id(2), duplicate_id, _pan_id(11),
    ]
    assert result["hits"][0]["messageIndex"] == 0
    assert result["hits"][2]["messageIndex"] == 3
    assert result["hits"][0]["role"] == "user"
    assert result["hits"][1]["role"] == "assistant"
    assert result["hits"][0]["historyEpoch"] == "epoch-a"
    assert result["hits"][0]["historyRevision"] == 1
    assert result["hits"][0]["historyTotal"] == len(history)


def test_literal_substring_supports_cjk_short_text_case_and_fts_special_inputs(tmp_path):
    content = 'MixedCase value OR NOT NEAR; quote "hello", +++ [] 中文你好世界'
    session = _session("s-literal", [
        {"role": "user", "content": content, "messageId": _pan_id(1)},
    ])
    path = tmp_path / "search.sqlite3"

    for query in (
        "mixedcase", "OR", "NEAR", '"hello"', "+++", "[]", "中文",
        "你好世界", "中",
    ):
        result = _search(path, [session], query)
        assert len(result["hits"]) == 1, query
        assert result["hits"][0]["messageId"] == _pan_id(1), query

    assert _search(path, [session], "absent")["hits"] == []


def test_empty_query_does_not_create_or_open_the_index(tmp_path):
    path = tmp_path / "not-created.sqlite3"
    session = _session("s-empty", [
        {"role": "user", "content": "text", "messageId": _pan_id(1)},
    ])

    result = _search(path, [session], "  \t ")

    assert result == {"hits": [], "versions": [], "limit": 50, "hasMore": False}
    assert not path.exists()


def test_incremental_revision_and_epoch_replace_rows_without_duplicate_ids(tmp_path):
    message_id = _pan_id(1)
    session = _session("s-revision", [
        {"role": "user", "content": "original text", "messageId": message_id},
    ])
    path = tmp_path / "search.sqlite3"

    first = _search(path, [session], "original")
    assert len(first["hits"]) == 1

    session.history[0]["content"] = "revised text"
    session.history_revision = 2
    session.summary_projection["history_total"] = 1
    revised = _search(path, [session], "revised")
    assert [hit["messageId"] for hit in revised["hits"]] == [message_id]
    assert revised["hits"][0]["historyRevision"] == 2
    assert _search(path, [session], "original")["hits"] == []

    session.history[0]["content"] = "reimported text"
    session.history_epoch = "epoch-b"
    session.history_revision = 3
    reimported = _search(path, [session], "reimported")
    assert [hit["messageId"] for hit in reimported["hits"]] == [message_id]
    assert reimported["hits"][0]["historyEpoch"] == "epoch-b"
    assert reimported["hits"][0]["historyRevision"] == 3


def test_new_epoch_replaces_even_when_its_revision_is_lower(tmp_path):
    message_id = _pan_id(12)
    session = _session("s-epoch-reset", [
        {"role": "user", "content": "old epoch phrase", "messageId": message_id},
    ], epoch="epoch-old", revision=90)
    path = tmp_path / "search.sqlite3"
    assert len(_search(path, [session], "old epoch")["hits"]) == 1

    session.history[0]["content"] = "new epoch phrase"
    session.history_epoch = "epoch-new"
    session.history_revision = 1
    replaced = _search(path, [session], "new epoch")

    assert [hit["messageId"] for hit in replaced["hits"]] == [message_id]
    assert replaced["hits"][0]["historyEpoch"] == "epoch-new"
    assert replaced["hits"][0]["historyRevision"] == 1
    assert _search(path, [session], "old epoch")["hits"] == []


def test_missing_index_file_rebuilds_and_deleted_sessions_are_pruned(tmp_path):
    path = tmp_path / "search.sqlite3"
    session = _session("s-rebuild", [
        {"role": "assistant", "content": "rebuildable content", "messageId": _pan_id(1)},
    ])
    assert len(_search(path, [session], "rebuildable")["hits"]) == 1
    assert path.exists()

    path.unlink()
    rebuilt = _search(path, [session], "rebuildable")
    assert len(rebuilt["hits"]) == 1
    assert session.history[0]["content"] == "rebuildable content"

    pruned = _search(path, [], "rebuildable", live_ids=[])
    assert pruned["hits"] == []
    assert pruned["versions"] == []
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT value FROM history_search_meta WHERE key=?",
            (f"digest:{session.id}",),
        ).fetchone() is None


def test_result_limit_is_bounded_and_global_order_is_scope_then_history(tmp_path):
    first = _session("s-first", [
        {"role": "user", "content": "shared first", "messageId": _pan_id(1)},
        {"role": "assistant", "content": "shared second", "messageId": _pan_id(2)},
    ])
    second = _session("s-second", [
        {"role": "user", "content": "shared third", "messageId": _pan_id(3)},
    ])

    limited = _search(
        tmp_path / "search.sqlite3", [first, second], "shared", limit=1,
    )
    assert limited["limit"] == 1
    assert limited["hasMore"] is True
    assert [(hit["sessionId"], hit["messageIndex"]) for hit in limited["hits"]] == [
        ("s-first", 0),
    ]

    capped = _search(
        tmp_path / "search.sqlite3", [first, second], "shared", limit=10_000,
    )
    assert capped["limit"] == 100
    assert [(hit["sessionId"], hit["messageIndex"]) for hit in capped["hits"]] == [
        ("s-first", 0), ("s-first", 1), ("s-second", 0),
    ]


def test_keyset_pages_cover_more_than_one_hundred_hits_without_duplicates(tmp_path):
    history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"PageNeedle {index}; pageneedle repeated",
            "messageId": _pan_id(index + 1),
        }
        for index in range(237)
    ]
    session = _session("s-many-pages", history)
    path = tmp_path / "search.sqlite3"

    first = _search(path, [session], "PageNeedle", limit=73)
    pages = [first]
    while pages[-1]["hasMore"]:
        previous = pages[-1]
        last = previous["hits"][-1]
        after = (0, last["messageIndex"])
        pages.append(_search(
            path, [session], "PageNeedle", limit=73, after=after,
        ))

    hits = [hit for page in pages for hit in page["hits"]]
    assert [hit["messageIndex"] for hit in hits] == list(range(237))
    assert [hit["messageId"] for hit in hits] == [
        _pan_id(index + 1) for index in range(237)
    ]
    assert [len(page["hits"]) for page in pages] == [73, 73, 73, 18]
    assert len({hit["messageId"] for hit in hits}) == 237
    assert pages[-1]["hasMore"] is False


def test_total_change_reindexes_even_when_epoch_and_revision_are_unchanged(tmp_path):
    session = _session("s-total-change", [
        {"role": "user", "content": "total needle", "messageId": _pan_id(1)},
        {"role": "assistant", "content": "total needle", "messageId": _pan_id(2)},
    ])
    path = tmp_path / "search.sqlite3"
    initial = _search(path, [session], "needle")
    assert initial["versions"][0]["historyTotal"] == 2

    session.history.append({
        "role": "user", "content": "total needle", "messageId": _pan_id(3),
    })
    session.summary_projection["history_total"] = 3
    changed = _search(path, [session], "needle")

    assert len(changed["hits"]) == 3
    assert changed["versions"][0]["historyEpoch"] == "epoch-a"
    assert changed["versions"][0]["historyRevision"] == 1
    assert changed["versions"][0]["historyTotal"] == 3


def test_parallel_search_requests_do_not_duplicate_or_lose_a_message(tmp_path):
    path = tmp_path / "parallel.sqlite3"
    session = _session("s-parallel", [
        {"role": "user", "content": "concurrent phrase", "messageId": _pan_id(1)},
    ])

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _index: _search(path, [session], "concurrent"),
            range(8),
        ))

    assert all([hit["messageId"] for hit in result["hits"]] == [_pan_id(1)]
               for result in results)


def _indexed_rowid(path, session_id, message_id):
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """SELECT rowid FROM history_search_messages
               WHERE session_id=? AND message_id=?""",
            (session_id, message_id),
        ).fetchone()
    return row[0] if row else None


def test_verified_append_preserves_prefix_rows_and_indexes_only_suffix(tmp_path, monkeypatch):
    path = tmp_path / "append.sqlite3"
    session = _session("s-append", [
        {"role": "user", "content": "needle first", "messageId": _pan_id(1)},
        {"role": "thinking", "content": "needle excluded", "messageId": _pan_id(2)},
    ])
    assert len(_search(path, [session], "needle")["hits"]) == 1
    original_rowid = _indexed_rowid(path, session.id, _pan_id(1))
    session.history.extend([
        {"role": "assistant", "content": "needle second", "messageId": _pan_id(3)},
        {"role": "user", "content": "needle legacy", "messageId": "legacy:s:0:3"},
    ])
    session.history_revision += 2
    def unexpected_rebuild(*_args):
        raise AssertionError("verified append rebuilt the full Session")
    with monkeypatch.context() as patch:
        patch.setattr(history_search_index, "_replace_session", unexpected_rebuild)
        result = _search(path, [session], "needle")

    assert [hit["messageId"] for hit in result["hits"]] == [_pan_id(1), _pan_id(3)]
    assert _indexed_rowid(path, session.id, _pan_id(1)) == original_rowid
    assert _indexed_rowid(path, session.id, _pan_id(2)) is None
    assert _search(path, [session], "needle")["hits"] == result["hits"]


def test_changed_prefix_with_growing_total_rebuilds_instead_of_appending(tmp_path, monkeypatch):
    path = tmp_path / "changed-prefix.sqlite3"
    session = _session("s-prefix", [
        {"role": "user", "content": "old needle", "messageId": _pan_id(1)},
        {"role": "thinking", "content": "hidden needle", "messageId": _pan_id(2)},
    ])
    assert len(_search(path, [session], "old needle")["hits"]) == 1
    session.history[0]["content"] = "new needle"
    session.history[1]["role"] = "assistant"
    session.history.append({
        "role": "assistant", "content": "tail needle", "messageId": _pan_id(3),
    })
    session.history_revision += 1
    original_replace = history_search_index._replace_session
    rebuilds = []
    def record_rebuild(*args):
        rebuilds.append(True)
        return original_replace(*args)
    with monkeypatch.context() as patch:
        patch.setattr(history_search_index, "_replace_session", record_rebuild)
        result = _search(path, [session], "needle")

    assert rebuilds == [True]
    assert [hit["messageId"] for hit in result["hits"]] == [
        _pan_id(1), _pan_id(2), _pan_id(3),
    ]
    assert _search(path, [session], "old needle")["hits"] == []


def test_suffix_duplicate_id_rebuilds_to_last_occurrence(tmp_path):
    path = tmp_path / "duplicate.sqlite3"
    session = _session("s-duplicate", [
        {"role": "user", "content": "old needle", "messageId": _pan_id(1)},
    ])
    assert len(_search(path, [session], "old needle")["hits"]) == 1
    session.history.append({
        "role": "assistant", "content": "new needle", "messageId": _pan_id(1),
    })
    session.history_revision += 1

    assert _search(path, [session], "old needle")["hits"] == []
    result = _search(path, [session], "new needle")
    assert [(hit["messageId"], hit["messageIndex"]) for hit in result["hits"]] == [
        (_pan_id(1), 1),
    ]


def test_missing_prefix_proof_rebuilds_before_next_append(tmp_path):
    path = tmp_path / "old-index.sqlite3"
    session = _session("s-old-index", [
        {"role": "user", "content": "old needle", "messageId": _pan_id(1)},
    ])
    _search(path, [session], "needle")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "DELETE FROM history_search_meta WHERE key=?",
            (f"digest:{session.id}",),
        )
    session.history.append({
        "role": "assistant", "content": "new needle", "messageId": _pan_id(2),
    })
    session.history_revision += 1
    assert [hit["messageId"] for hit in _search(path, [session], "needle")["hits"]] == [
        _pan_id(1), _pan_id(2),
    ]
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT value FROM history_search_meta WHERE key=?",
            (f"digest:{session.id}",),
        ).fetchone() is not None


def test_same_total_id_rewrite_replaces_old_identity(tmp_path):
    path = tmp_path / "id-rewrite.sqlite3"
    session = _session("s-id-rewrite", [
        {"role": "user", "content": "needle text", "messageId": _pan_id(1)},
    ])
    _search(path, [session], "needle")
    session.history[0]["messageId"] = _pan_id(2)
    session.history_revision += 1
    assert [hit["messageId"] for hit in _search(path, [session], "needle")["hits"]] == [
        _pan_id(2),
    ]
    assert _indexed_rowid(path, session.id, _pan_id(1)) is None


def test_parallel_queries_after_append_index_suffix_once(tmp_path):
    path = tmp_path / "parallel-append.sqlite3"
    session = _session("s-parallel-append", [
        {"role": "user", "content": "needle first", "messageId": _pan_id(1)},
    ])
    _search(path, [session], "needle")
    session.history.append({
        "role": "assistant", "content": "needle second", "messageId": _pan_id(2),
    })
    session.history_revision += 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _index: _search(path, [session], "needle"), range(8),
        ))
    assert all([hit["messageId"] for hit in result["hits"]] == [
        _pan_id(1), _pan_id(2),
    ] for result in results)
    with sqlite3.connect(path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM history_search_messages WHERE session_id=?",
            (session.id,),
        ).fetchone()[0]
    assert count == 2
