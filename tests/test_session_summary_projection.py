"""T-062.5: Session summary is a bounded, revisioned pure projection."""

import asyncio
import json
import threading
import time

import pytest

from packages.core import session as sess
from packages.web import server


def _new_session(name="summary"):
    return sess.create(name=name, adapter="cbc", model="stored-model")


def test_summary_cold_list_does_not_load_history_config_or_attachment_io(
    monkeypatch,
):
    target = _new_session()
    for index in range(1000):
        sess.append_history(target, {
            "role": "assistant" if index % 2 else "user",
            "content": f"See [doc](docs/{index}.md#L2) {index}",
        })
    sess.save(target)

    # Simulate a service restart after the projection has been persisted. The
    # summary loader may read Session metadata JSON, but must not open the
    # companion JSONL or invoke any dynamic/default/attachment projection.
    sess._cache.clear()
    sess._all_loaded = False
    monkeypatch.setattr(sess, "_read_jsonl", lambda *_args: (_ for _ in ()).throw(
        AssertionError("summary list must not load history JSONL")
    ))
    monkeypatch.setattr(server, "load_config", lambda: (_ for _ in ()).throw(
        AssertionError("summary list must not load config.json")
    ))
    monkeypatch.setattr(server, "_project_editor_links", lambda *_args: (_ for _ in ()).throw(
        AssertionError("summary list must not project editor links")
    ))
    monkeypatch.setattr(server, "_normalize_legacy_attachment_links", lambda *_args: (_ for _ in ()).throw(
        AssertionError("summary list must not normalize attachment links")
    ))

    response = asyncio.run(server.api_list_sessions(summary=1))
    summary = next(item for item in response["sessions"] if item["id"] == target.id)
    assert summary["historyTotal"] == 1000
    assert summary["lastAssistantPreview"].startswith("See [doc]")
    assert "docs/999.md" in summary["lastDisplayPreview"]
    assert summary["summaryRevision"] >= 1000


def test_legacy_disk_session_uses_conservative_tail_fallback_without_migration(
    monkeypatch,
):
    session_dir = sess.SESSION_DIR
    session_dir.mkdir(parents=True, exist_ok=True)
    sid = "ses-legacy-summary"
    (session_dir / f"{sid}.json").write_text(json.dumps({
        "id": sid,
        "name": "legacy",
        "adapter": "cbc",
        "history": [{"role": "assistant", "content": "legacy preview"}],
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }), encoding="utf-8")
    (session_dir / f"{sid}.history.jsonl").write_text(
        '{"role":"assistant","content":"full legacy history"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sess, "_read_jsonl", lambda *_args: (_ for _ in ()).throw(
        AssertionError("legacy summary fallback must not load JSONL")
    ))

    result = asyncio.run(server.api_list_sessions(summary=1))
    summary = next(item for item in result["sessions"] if item["id"] == sid)
    assert summary["lastAssistantPreview"] == "legacy preview"
    assert summary["lastDisplayPreview"] == "legacy preview"
    assert summary["historyTotal"] is None
    assert "summary_projection" not in json.loads(
        (session_dir / f"{sid}.json").read_text(encoding="utf-8")
    )


def test_incomplete_projection_uses_main_tail_for_cold_summary_and_unknown_total(
    monkeypatch,
):
    """A dict-shaped upgrade projection is not authoritative until complete."""
    session_dir = sess.SESSION_DIR
    session_dir.mkdir(parents=True, exist_ok=True)
    sid = "ses-incomplete-summary"
    tail = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "latest answer"},
    ]
    (session_dir / f"{sid}.json").write_text(json.dumps({
        "id": sid,
        "name": "incomplete",
        "adapter": "cbc",
        "history": tail,
        # This shape came from an interrupted/older upgrade: it is a dict, but
        # lacks the required preview/revision fields and must not be trusted.
        "summary_projection": {"history_total": 2},
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }), encoding="utf-8")
    (session_dir / f"{sid}.history.jsonl").write_text(
        "\n".join(json.dumps({"role": "assistant", "content": f"full-{i}"})
                  for i in range(5)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sess, "_read_jsonl", lambda *_args: (_ for _ in ()).throw(
        AssertionError("cold summary must not load JSONL")
    ))

    result = asyncio.run(server.api_list_sessions(summary=1))
    summary = next(item for item in result["sessions"] if item["id"] == sid)
    assert summary["lastDisplayPreview"] == "latest answer"
    assert summary["lastAssistantPreview"] == "latest answer"
    assert summary["historyTotal"] is None


def test_incomplete_projection_is_rebuilt_from_full_history_and_persisted_atomically():
    session_dir = sess.SESSION_DIR
    session_dir.mkdir(parents=True, exist_ok=True)
    sid = "ses-incomplete-full"
    rows = [
        {"role": "user", "content": "question"},
        {"role": "thinking", "content": "internal"},
        {"role": "assistant", "content": "answer"},
    ]
    (session_dir / f"{sid}.json").write_text(json.dumps({
        "id": sid,
        "name": "full repair",
        "adapter": "cbc",
        "history": rows[-2:],
        "summary_projection": {"revision": 11, "history_total": "wrong"},
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }), encoding="utf-8")
    (session_dir / f"{sid}.history.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
    )

    loaded = sess.get(sid)
    assert loaded is not None
    projection = sess.summary_projection(loaded)
    assert projection["history_total"] == len(rows)
    assert projection["last_user_preview"] == "question"
    assert projection["last_assistant_preview"] == "answer"

    # Full load is read-only; an explicit save persists its exact repair.
    sess.save(loaded)
    persisted = json.loads((session_dir / f"{sid}.json").read_text(encoding="utf-8"))
    assert persisted["summary_projection"]["history_total"] == len(rows)
    assert persisted["summary_projection"]["last_display_preview"] == "answer"


def test_summary_projection_backfill_is_restart_idempotent_and_does_not_touch_history():
    session_dir = sess.SESSION_DIR
    session_dir.mkdir(parents=True, exist_ok=True)
    sid = "ses-backfill"
    rows = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    main = session_dir / f"{sid}.json"
    history = session_dir / f"{sid}.history.jsonl"
    main.write_text(json.dumps({
        "id": sid, "name": "backfill", "adapter": "cbc", "history": rows,
        "summary_projection": {"revision": 4},
    }), encoding="utf-8")
    history.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    before_history = history.read_bytes()
    cold = sess.get(sid, load_history=False)
    assert cold is not None
    sess._cache[sid] = cold

    first = sess.backfill_summary_projections_sync()
    after_first = json.loads(main.read_text(encoding="utf-8"))
    second = sess.backfill_summary_projections_sync()
    after_second = json.loads(main.read_text(encoding="utf-8"))

    assert first["repaired"] == 1
    assert second["repaired"] == 0
    assert after_first["summary_projection"] == after_second["summary_projection"]
    assert after_first["summary_projection"]["history_total"] == 2
    assert sess.summary_projection(cold)["history_total"] == 2
    assert history.read_bytes() == before_history


def test_summary_projection_completeness_accepts_legal_empty_values_only():
    empty = sess._empty_summary_projection(revision=0, history_total=0)
    assert sess.is_complete_summary_projection(empty)
    assert sess.is_complete_summary_projection({
        "summaryRevision": 0,
        "lastUserPreview": "",
        "lastAssistantPreview": "",
        "lastDisplayPreview": "",
        "lastSystemPreview": "",
        "lastThinkingPreview": "",
        "lastToolPreview": "",
        "lastMainRole": "",
        "historyTotal": 0,
        "updatedAt": "",
    })
    assert not sess.is_complete_summary_projection({
        **empty, "history_total": False,
    })
    assert not sess.is_complete_summary_projection({
        **empty, "history_total": None,
    })


def test_legacy_empty_history_is_a_real_zero_not_unknown():
    sid = "ses-empty-summary"
    sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    (sess.SESSION_DIR / f"{sid}.json").write_text(json.dumps({
        "id": sid, "name": "empty", "adapter": "cbc", "history": [],
    }), encoding="utf-8")

    result = asyncio.run(server.api_list_sessions(summary=1))
    summary = next(item for item in result["sessions"] if item["id"] == sid)
    assert summary["historyTotal"] == 0


def test_backfill_serializes_with_append_and_save_without_losing_order(monkeypatch):
    sid = "ses-backfill-race"
    sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    rows = [
        {"role": "user", "content": "before"},
        {"role": "assistant", "content": "answer"},
    ]
    (sess.SESSION_DIR / f"{sid}.json").write_text(json.dumps({
        "id": sid, "name": "race", "adapter": "cbc", "history": rows[-1:],
        "summary_projection": {"revision": 2},
    }), encoding="utf-8")
    history_path = sess.SESSION_DIR / f"{sid}.history.jsonl"
    history_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    cold = sess.get(sid, load_history=False)
    assert cold is not None

    entered = threading.Event()
    release = threading.Event()
    original_read = sess._summary_projection_from_jsonl

    def blocked_read(path, **kwargs):
        entered.set()  # called from the single repair worker thread
        # The test releases the worker after appending and queueing save.
        while not release.is_set():
            time.sleep(0.001)
        return original_read(path, **kwargs)

    monkeypatch.setattr(sess, "_summary_projection_from_jsonl", blocked_read)

    async def scenario():
        repair = asyncio.create_task(sess.backfill_summary_projections())
        await asyncio.to_thread(entered.wait)
        sess.append_history(cold, {"role": "user", "content": "after"})
        saving = asyncio.create_task(sess.save_async(cold))
        release.set()
        await asyncio.gather(repair, saving)

    asyncio.run(scenario())
    sess._cache.clear()
    sess._all_loaded = False
    loaded = sess.get(sid)
    assert loaded is not None
    assert [row["content"] for row in loaded.history] == ["before", "answer", "after"]
    assert sess.summary_projection(loaded)["history_total"] == 3


def test_hydrated_unsaved_append_backfill_then_save_converges_on_cold_restart(
    monkeypatch,
):
    """A repair must not mark an in-memory unsaved projection as durable."""
    sid = "ses-hydrated-backfill-race"
    sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    rows = [
        {"role": "user", "content": "before"},
        {"role": "assistant", "content": "old answer"},
    ]
    main = sess.SESSION_DIR / f"{sid}.json"
    history = sess.SESSION_DIR / f"{sid}.history.jsonl"
    main.write_text(json.dumps({
        "id": sid,
        "name": "hydrated race",
        "adapter": "cbc",
        "history": rows[-1:],
        "summary_projection": {"revision": 4},
    }), encoding="utf-8")
    history.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    hydrated = sess.get(sid)
    assert hydrated is not None
    assert hydrated._history_loaded is True
    entered = threading.Event()
    release = threading.Event()
    original_streaming_rebuild = sess._summary_projection_from_jsonl

    def blocked_streaming_rebuild(path, **kwargs):
        entered.set()
        assert release.wait(2), "backfill did not reach the JSONL read boundary"
        return original_streaming_rebuild(path, **kwargs)

    monkeypatch.setattr(sess, "_summary_projection_from_jsonl", blocked_streaming_rebuild)

    async def scenario():
        repair = asyncio.create_task(sess.backfill_summary_projections())
        assert await asyncio.to_thread(entered.wait, 2)
        sess.append_history(hydrated, {"role": "assistant", "content": "new answer"})
        saving = asyncio.create_task(sess.save_async(hydrated))
        release.set()
        await asyncio.gather(repair, saving)

    asyncio.run(scenario())

    # Deliberately do not call full get(): the regression was masked by full
    # load rebuilding from JSONL. The cold summary must be authoritative.
    sess._cache.clear()
    sess._all_loaded = False
    cold = sess.get(sid, load_history=False)
    assert cold is not None
    projection = sess.summary_projection(cold)
    assert projection["history_total"] == 3
    assert projection["last_display_preview"] == "new answer"
    assert projection["revision"] >= 6


@pytest.mark.parametrize(
    ("top_level_name", "projection_kind", "projection_factory"),
    [
        ("summaryProjection", "complete", lambda rows: {
            "summaryRevision": 3,
            "lastUserPreview": "camel question",
            "lastAssistantPreview": "camel answer",
            "lastSystemPreview": "",
            "lastThinkingPreview": "",
            "lastToolPreview": "",
            "lastDisplayPreview": "camel answer",
            "lastMainRole": "assistant",
            "historyTotal": len(rows),
            "updatedAt": "2026-01-01T00:00:00",
        }),
        ("summaryProjection", "incomplete", lambda rows: {
            "summaryRevision": 3, "historyTotal": len(rows),
        }),
        ("summary", "complete", lambda rows: {
            "summaryRevision": 3,
            "lastUserPreview": "camel question",
            "lastAssistantPreview": "camel answer",
            "lastSystemPreview": "",
            "lastThinkingPreview": "",
            "lastToolPreview": "",
            "lastDisplayPreview": "camel answer",
            "lastMainRole": "assistant",
            "historyTotal": len(rows),
            "updatedAt": "2026-01-01T00:00:00",
        }),
        ("summary", "incomplete", lambda rows: {
            "summaryRevision": 3, "historyTotal": len(rows),
        }),
    ],
    ids=["summaryProjection-complete", "summaryProjection-incomplete",
         "summary-complete", "summary-incomplete"],
)
def test_backfill_canonicalizes_camel_projection_aliases_and_reload(
    top_level_name, projection_kind, projection_factory, monkeypatch,
):
    sid = f"ses-{top_level_name}-{projection_kind}"
    sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    rows = [
        {"role": "user", "content": "camel question"},
        {"role": "assistant", "content": "camel answer"},
    ]
    main = sess.SESSION_DIR / f"{sid}.json"
    history = sess.SESSION_DIR / f"{sid}.history.jsonl"
    payload = {
        "id": sid,
        "name": sid,
        "adapter": "cbc",
        "history": rows[-1:],
        top_level_name: projection_factory(rows),
    }
    main.write_text(json.dumps(payload), encoding="utf-8")
    history.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    if projection_kind == "complete":
        monkeypatch.setattr(
            sess,
            "_summary_projection_from_jsonl",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("complete aliased projection must not scan JSONL")
            ),
        )

    result = sess.backfill_summary_projections_sync()
    assert result["errors"] == 0
    persisted = json.loads(main.read_text(encoding="utf-8"))
    assert "summaryProjection" not in persisted
    assert "summary" not in persisted
    assert sess.is_complete_summary_projection(persisted["summary_projection"])

    sess._cache.clear()
    sess._all_loaded = False
    cold = sess.get(sid, load_history=False)
    assert cold is not None
    projection = sess.summary_projection(cold)
    assert projection["history_total"] == 2
    assert projection["last_assistant_preview"] == "camel answer"


def test_backfill_preserves_unrelated_main_metadata_while_consuming_alias():
    sid = "ses-summary-alias-preserves-unknown"
    sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    main = sess.SESSION_DIR / f"{sid}.json"
    main.write_text(json.dumps({
        "id": sid,
        "name": sid,
        "adapter": "cbc",
        "summary": {"summaryRevision": 1, "historyTotal": 0},
        "future_metadata": {"keep": True},
    }), encoding="utf-8")

    result = sess.backfill_summary_projections_sync()
    assert result["errors"] == 0
    persisted = json.loads(main.read_text(encoding="utf-8"))
    assert "summary" not in persisted
    assert persisted["future_metadata"] == {"keep": True}


def test_backfill_read_error_is_failed_and_never_persists_zero(monkeypatch):
    sid = "ses-summary-read-error"
    sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    main = sess.SESSION_DIR / f"{sid}.json"
    history = sess.SESSION_DIR / f"{sid}.history.jsonl"
    main.write_text(json.dumps({
        "id": sid,
        "name": sid,
        "adapter": "cbc",
        "summary_projection": {"revision": 9},
    }), encoding="utf-8")
    history.write_text(
        json.dumps({"role": "assistant", "content": "must survive"}) + "\n",
        encoding="utf-8",
    )
    before = main.read_bytes()
    path_type = type(history)
    original_stat = path_type.stat

    def unavailable_history_stat(path, *args, **kwargs):
        if path == history:
            raise OSError("history temporarily unavailable")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "stat", unavailable_history_stat)

    result = sess.backfill_summary_projections_sync()
    assert result["state"] == "failed"
    assert result["errors"] == 1
    assert result["repaired"] == 0
    assert main.read_bytes() == before
    assert not list(sess.SESSION_DIR.glob("*.summary.tmp"))


def test_failed_backfill_does_not_broadcast_completion(monkeypatch):
    events: list[dict] = []

    async def failed_backfill(*, cancel_event=None):
        return {
            "state": "failed",
            "discovered": 1,
            "repaired": 0,
            "skipped": 0,
            "errors": 1,
        }

    async def record_broadcast(event):
        events.append(event)

    monkeypatch.setattr(sess, "backfill_summary_projections", failed_backfill)
    monkeypatch.setattr(server, "broadcast", record_broadcast)

    result = asyncio.run(
        server._run_summary_projection_backfill(threading.Event())
    )
    assert result["state"] == "failed"
    assert events == []


def test_shutdown_backfill_wait_is_bounded_without_cancelling_worker():
    release = threading.Event()
    cancel_event = threading.Event()

    async def slow_worker():
        await asyncio.to_thread(release.wait, 2)
        return {"state": "cancelled"}

    async def scenario():
        task = asyncio.create_task(slow_worker())
        await asyncio.sleep(0)
        started = time.monotonic()
        stopped = await server._shutdown_summary_projection_backfill(
            task, cancel_event, timeout=0.02,
        )
        elapsed = time.monotonic() - started
        assert stopped is False
        assert elapsed < 0.5
        assert cancel_event.is_set()
        assert not task.done()
        release.set()
        return await task

    assert asyncio.run(scenario())["state"] == "cancelled"


def test_backfill_cancellation_stops_at_session_boundary_and_reports_cancelled(
    monkeypatch,
):
    session_ids = [f"ses-cancel-{index}" for index in range(3)]
    sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    for sid in session_ids:
        (sess.SESSION_DIR / f"{sid}.json").write_text(json.dumps({
            "id": sid,
            "name": sid,
            "adapter": "cbc",
            "history": [{"role": "assistant", "content": sid}],
            "summary_projection": {"revision": 1},
        }), encoding="utf-8")

    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    before_later_files = {
        sid: (sess.SESSION_DIR / f"{sid}.json").read_bytes()
        for sid in session_ids[1:]
    }
    original_repair = sess._repair_summary_projection_file

    def slow_first_repair(sid):
        calls.append(sid)
        if len(calls) == 1:
            entered.set()
            assert release.wait(2), "test did not release the current atomic repair"
        return original_repair(sid)

    monkeypatch.setattr(sess, "_repair_summary_projection_file", slow_first_repair)
    cancel_event = threading.Event()

    async def scenario():
        task = asyncio.create_task(
            sess.backfill_summary_projections(cancel_event=cancel_event),
        )
        assert await asyncio.to_thread(entered.wait, 2)
        async def release_current_ticket():
            await asyncio.sleep(0.02)
            release.set()

        releaser = asyncio.create_task(release_current_ticket())
        stopped = await server._shutdown_summary_projection_backfill(
            task, cancel_event, timeout=1,
        )
        await releaser
        return stopped, task.result()

    stopped, result = asyncio.run(scenario())
    assert stopped is True
    assert result["state"] == "cancelled"
    assert calls == [session_ids[0]]
    assert not list(sess.SESSION_DIR.glob("*.summary.tmp"))
    assert "summary_projection" in json.loads(
        (sess.SESSION_DIR / f"{session_ids[0]}.json").read_text(encoding="utf-8")
    )
    for sid in session_ids[1:]:
        assert (sess.SESSION_DIR / f"{sid}.json").read_bytes() == before_later_files[sid]


def test_preview_roles_are_bounded_and_auxiliary_rows_do_not_hide_assistant():
    target = sess.Session(
        id="ses-preview",
        name="preview",
        history=[
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
            {"role": "thinking", "content": "internal reasoning"},
            {"role": "tool", "content": "tool output"},
        ],
    )

    summary = server._session_summary(target)
    assert summary["lastUserPreview"] == "question"
    assert summary["lastAssistantPreview"] == "answer"
    assert summary["lastDisplayPreview"] == "answer"
    assert summary["lastMessage"] == "answer"
    assert summary["historyTotal"] == 4

    long_text = "x" * (sess.SUMMARY_PREVIEW_MAX + 50)
    sess.append_history(target, {"role": "assistant", "content": long_text})
    summary = server._session_summary(target)
    assert len(summary["lastAssistantPreview"]) == sess.SUMMARY_PREVIEW_MAX
    assert summary["lastAssistantPreview"] == long_text[:sess.SUMMARY_PREVIEW_MAX]
    assert summary["lastDisplayPreview"] == summary["lastAssistantPreview"]


def test_summary_revision_persists_and_worker_ws_patch_is_monotonic():
    target = _new_session()
    first = server._session_summary(target)
    sess.append_history(target, {"role": "user", "content": "hello"})
    after_history = server._session_summary(target)
    assert after_history["summaryRevision"] > first["summaryRevision"]

    sess.save(target)
    persisted = json.loads(sess._path(target.id).read_text(encoding="utf-8"))
    assert persisted["summary_projection"]["revision"] == after_history["summaryRevision"]

    event = server._attach_session_summary_patch({
        "type": "worker.status",
        "sessionId": target.id,
        "workerId": "worker-1",
        "generation": 3,
        "taskId": "task-1",
        "taskSeq": 7,
        "status": "running",
    })
    assert event["session"]["summaryRevision"] > after_history["summaryRevision"]
    assert event["session"]["workerGeneration"] == 3
    assert event["session"]["workerTaskId"] == "task-1"

    stale = server._session_summary(target)
    assert stale["summaryRevision"] == event["session"]["summaryRevision"]
    newer = server._attach_session_summary_patch({
        "type": "worker.status",
        "sessionId": target.id,
        "workerId": "worker-1",
        "generation": 3,
        "taskId": "task-1",
        "taskSeq": 7,
        "status": "idle",
    })
    assert newer["session"]["summaryRevision"] > stale["summaryRevision"]
    assert newer["session"]["workerStatus"] == "idle"


def test_metadata_and_full_views_keep_summary_fields_without_changing_history_contract():
    target = _new_session("view-compat")
    sess.append_history(target, {"role": "user", "content": "keep me"})
    metadata = asyncio.run(server.api_get_session(target.id, view="metadata"))
    full = asyncio.run(server.api_get_session(target.id, view="full"))

    assert metadata["summaryRevision"] == full["summaryRevision"]
    assert metadata["lastUserPreview"] == "keep me"
    assert "history" not in metadata
    assert full["history"][-1]["content"] == "keep me"
