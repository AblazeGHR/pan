"""T-062.5: Session summary is a bounded, revisioned pure projection."""

import asyncio
import json
import threading
import time

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

    first = sess.backfill_summary_projections_sync()
    after_first = json.loads(main.read_text(encoding="utf-8"))
    second = sess.backfill_summary_projections_sync()
    after_second = json.loads(main.read_text(encoding="utf-8"))

    assert first["repaired"] == 1
    assert second["repaired"] == 0
    assert after_first["summary_projection"] == after_second["summary_projection"]
    assert after_first["summary_projection"]["history_total"] == 2
    assert history.read_bytes() == before_history


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
