"""Handoff retargets Job targets using only temporary registry/session data."""

import asyncio
import concurrent.futures
import json
import threading
import time

from packages.core import background_jobs as jobs
from packages.core import session as sess
from packages.core import worker
from packages.scheduler import store as scheduler_store
from packages.web import server


OLD = "ses_handoff_old"
NEW = "ses_handoff_new"


def _active_root(tmp_path, monkeypatch):
    root = tmp_path / "active-jobs"
    fallback = tmp_path / "background-jobs-fallback"
    monkeypatch.setenv("PAN_SCHEDULER_DIR", str(root))
    monkeypatch.setenv("PAN_BACKGROUND_JOBS_DIR", str(fallback))
    monkeypatch.setattr(scheduler_store, "DEFAULT_ROOT", tmp_path / "default-jobs")
    return root, fallback


def _save(root, job):
    jobs._save(job, registry_root=root)


def _read(root, job_id):
    return json.loads((root / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))


def _session(session_id, tmp_path):
    value = sess.Session(id=session_id, name=session_id, workdir=str(tmp_path))
    sess._cache[session_id] = value
    return value


def test_retarget_covers_all_target_kinds_shapes_statuses_and_only_target_fields(
        tmp_path, monkeypatch):
    root, fallback = _active_root(tmp_path, monkeypatch)
    run_history = '{"jobId":"job_done","targetSessionId":"ses_handoff_old"}\n'
    (root / "runs.jsonl").parent.mkdir(parents=True)
    (root / "runs.jsonl").write_text(run_history, encoding="utf-8")

    _save(root, {
        "jobId": "job_process", "kind": jobs.BACKGROUND_PROCESS_KIND,
        "status": "running", "targetSessionId": OLD,
        "targetStruct": {"sessionId": OLD, "extension": "keep"},
        "creatorSessionId": OLD, "source": "agent", "sourceSessionId": OLD,
        "sourceStruct": {"type": "agent", "sessionId": OLD},
        "name": "process name", "label": "process label",
    })
    _save(root, {
        "jobId": "job_message", "kind": jobs.SESSION_MESSAGE_KIND,
        "status": "pending", "targetStruct": {"sessionId": OLD},
        "text": "message", "creatorSessionId": OLD,
    })
    _save(root, {
        "jobId": "job_broadcast", "kind": jobs.SESSION_BROADCAST_KIND,
        "status": "completed",
        "targetSessionIds": [OLD, "ses_other", NEW, OLD],
        "targetStruct": {"sessionId": OLD,
                         "sessionIds": [OLD, "ses_other", NEW, OLD]},
        "lastDelivery": {"status": "queued", "sessionId": OLD,
                         "targetSessionIds": [OLD]},
    })
    _save(root, {
        "jobId": "job_scheduled", "kind": jobs.SCHEDULED_TASK_KIND,
        "status": "failed", "targetSessionId": OLD,
        "lastStatus": "failed", "lastFireAt": "historical-fire",
        "undeliveredFires": [{"targetSessionId": OLD}],
    })
    _save(root, {
        "jobId": "job_scheduled_child", "kind": jobs.BACKGROUND_PROCESS_KIND,
        "status": "completed", "scheduledParentJobId": "job_scheduled",
        "targetSessionId": OLD, "exitCode": 0,
    })
    _save(root, {
        "jobId": "job_lifecycle", "kind": jobs.SERVICE_LIFECYCLE_KIND,
        "status": "completed", "requestId": "req-1",
    })
    _save(root, {
        "jobId": "job_unmatched", "kind": jobs.SESSION_MESSAGE_KIND,
        "status": "scheduled", "targetSessionId": "ses_other",
    })
    _save(fallback, {
        "jobId": "job_wrong_root", "kind": jobs.SESSION_MESSAGE_KIND,
        "status": "pending", "targetSessionId": OLD,
    })

    # The direct recovery helper follows the Jobs product's scheduler-root
    # precedence and never scans the lower-priority root or migration backups.
    result = jobs.retarget_session_jobs(OLD, NEW)

    assert result == {
        "oldSessionId": OLD, "newSessionId": NEW, "scanned": 7,
        "updated": 5, "unchanged": 2, "errors": [],
    }
    process = _read(root, "job_process")
    assert process["targetSessionId"] == NEW
    assert process["targetStruct"] == {"sessionId": NEW, "extension": "keep"}
    assert jobs.job_public_view(process)["target"]["sessionId"] == NEW
    assert process["creatorSessionId"] == OLD
    assert process["source"] == "agent"
    assert process["sourceSessionId"] == OLD
    assert process["sourceStruct"] == {"type": "agent", "sessionId": OLD}
    assert process["name"] == "process name"
    assert process["label"] == "process label"

    message = _read(root, "job_message")
    assert message["targetStruct"]["sessionId"] == NEW
    assert message["targetSessionId"] == NEW  # structured-only gets legacy sync
    assert jobs.job_public_view(message)["target"]["sessionId"] == NEW

    broadcast = _read(root, "job_broadcast")
    expected_targets = [NEW, "ses_other"]
    assert broadcast["targetSessionIds"] == expected_targets
    assert broadcast["targetStruct"] == {
        "sessionId": NEW, "sessionIds": expected_targets,
    }
    assert jobs.job_public_view(broadcast)["target"] == {
        "sessionId": NEW, "sessionIds": expected_targets,
    }
    assert broadcast["lastDelivery"] == {
        "status": "queued", "sessionId": OLD, "targetSessionIds": [OLD],
    }

    scheduled = _read(root, "job_scheduled")
    assert scheduled["targetSessionId"] == NEW
    assert scheduled["targetStruct"] == {"sessionId": NEW}  # legacy-only sync
    assert scheduled["lastStatus"] == "failed"
    assert scheduled["lastFireAt"] == "historical-fire"
    assert scheduled["undeliveredFires"] == [{"targetSessionId": OLD}]
    assert _read(root, "job_scheduled_child")["targetSessionId"] == NEW
    assert _read(root, "job_lifecycle")["requestId"] == "req-1"
    assert _read(root, "job_unmatched")["targetSessionId"] == "ses_other"
    assert (root / "runs.jsonl").read_text(encoding="utf-8") == run_history
    assert _read(fallback, "job_wrong_root")["targetSessionId"] == OLD


def test_retarget_is_noop_without_match_and_idempotent_after_update(tmp_path):
    root = tmp_path / "registry"
    _save(root, {"jobId": "job_one", "targetSessionId": "ses_other"})

    no_match = jobs.retarget_session_jobs(OLD, NEW, registry_root=root)
    assert no_match["updated"] == 0
    assert no_match["unchanged"] == 1
    assert no_match["errors"] == []

    _save(root, {"jobId": "job_two", "targetSessionId": OLD})
    first = jobs.retarget_session_jobs(OLD, NEW, registry_root=root)
    second = jobs.retarget_session_jobs(OLD, NEW, registry_root=root)
    assert first["updated"] == 1 and first["unchanged"] == 1
    assert second["updated"] == 0 and second["unchanged"] == 2
    assert second["errors"] == []


def test_single_record_write_failure_is_reported_and_safe_to_retry(
        tmp_path, monkeypatch):
    root = tmp_path / "registry"
    _save(root, {"jobId": "job_write_fail", "targetSessionId": OLD})
    _save(root, {"jobId": "job_write_ok", "targetSessionId": OLD})
    write = jobs._atomic_write

    def fail_one(path, value):
        if path.name == "job_write_fail.json":
            raise OSError("injected atomic replace failure")
        return write(path, value)

    monkeypatch.setattr(jobs, "_atomic_write", fail_one)
    partial = jobs.retarget_session_jobs(OLD, NEW, registry_root=root)
    assert partial["updated"] == 1
    assert partial["errors"] == [{
        "jobId": "job_write_fail",
        "error": "OSError: injected atomic replace failure",
    }]
    assert _read(root, "job_write_fail")["targetSessionId"] == OLD
    assert _read(root, "job_write_ok")["targetSessionId"] == NEW

    monkeypatch.setattr(jobs, "_atomic_write", write)
    retried = jobs.retarget_session_jobs(OLD, NEW, registry_root=root)
    assert retried["updated"] == 1
    assert retried["unchanged"] == 1
    assert retried["errors"] == []


def test_retarget_waits_for_the_existing_job_lock(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    job_id = "job_lock_contract"
    _save(root, {"jobId": job_id, "targetSessionId": OLD})
    real_lock = jobs._job_lock
    held = threading.Event()
    release = threading.Event()
    retarget_waiting = threading.Event()

    def observed_lock(target_job_id, registry_root=None):
        if threading.current_thread().name.startswith("retarget-worker"):
            retarget_waiting.set()
        return real_lock(target_job_id, registry_root)

    monkeypatch.setattr(jobs, "_job_lock", observed_lock)

    def hold_job_lock():
        with real_lock(job_id, root):
            held.set()
            assert release.wait(timeout=5)

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="retarget-worker") as pool:
        holder = pool.submit(hold_job_lock)
        assert held.wait(timeout=5)
        retarget = pool.submit(
            lambda: jobs.retarget_session_jobs(OLD, NEW, registry_root=root),
        )
        assert retarget_waiting.wait(timeout=5)
        assert not retarget.done()
        release.set()
        holder.result(timeout=5)
        result = retarget.result(timeout=5)

    assert result["updated"] == 1
    assert _read(root, job_id)["targetSessionId"] == NEW


def test_successful_handoff_retargets_jobs_and_broadcasts_summary(
        tmp_path, monkeypatch):
    root, _ = _active_root(tmp_path, monkeypatch)
    _session(OLD, tmp_path)
    _save(root, {
        "jobId": "job_handoff_message", "kind": jobs.SESSION_MESSAGE_KIND,
        "targetSessionId": OLD, "status": "pending",
    })
    events = []

    async def capture(event):
        events.append(event)

    monkeypatch.setattr(server, "broadcast", capture)
    result = asyncio.run(server.api_session_handoff(
        OLD, {"handoffPrompt": "temporary test handoff"}))

    assert result["ok"] is True
    new_id = result["session"]["id"]
    assert new_id != OLD
    assert result["jobRetarget"] == {
        "oldSessionId": OLD, "newSessionId": new_id, "scanned": 1,
        "updated": 1, "unchanged": 0, "errors": [],
    }
    assert _read(root, "job_handoff_message")["targetSessionId"] == new_id
    retarget_event = next(
        event for event in events
        if event["type"] == "session.handoff.jobs_retargeted")
    assert retarget_event["jobRetarget"] == result["jobRetarget"]


def test_handoff_failure_does_not_touch_jobs(tmp_path, monkeypatch):
    root, _ = _active_root(tmp_path, monkeypatch)
    _session(OLD, tmp_path)
    _save(root, {
        "jobId": "job_handoff_failure", "targetSessionId": OLD,
        "targetStruct": {"sessionId": OLD},
    })
    before = _read(root, "job_handoff_failure")
    monkeypatch.setattr(sess, "handoff_session", lambda *args, **kwargs: "injected failure")

    result = asyncio.run(server.api_session_handoff(
        OLD, {"handoffPrompt": "temporary test handoff"}))

    assert result == {"error": "injected failure"}
    assert _read(root, "job_handoff_failure") == before


def test_successful_handoff_keeps_successor_visible_when_one_job_write_fails(
        tmp_path, monkeypatch):
    root, _ = _active_root(tmp_path, monkeypatch)
    _session(OLD, tmp_path)
    _save(root, {"jobId": "job_partial_failure", "targetSessionId": OLD})
    write = jobs._atomic_write
    events = []

    def fail_one(path, value):
        if path.name == "job_partial_failure.json":
            raise OSError("injected disk failure")
        return write(path, value)

    async def capture(event):
        events.append(event)

    monkeypatch.setattr(jobs, "_atomic_write", fail_one)
    monkeypatch.setattr(server, "broadcast", capture)
    result = asyncio.run(server.api_session_handoff(
        OLD, {"handoffPrompt": "temporary test handoff"}))

    assert result["ok"] is True
    assert result["session"]["id"] != OLD
    assert result["jobRetarget"]["updated"] == 0
    assert result["jobRetarget"]["errors"] == [{
        "jobId": "job_partial_failure", "error": "OSError: injected disk failure",
    }]
    assert _read(root, "job_partial_failure")["targetSessionId"] == OLD
    assert any(event.get("jobRetarget") == result["jobRetarget"] for event in events)


def test_inflight_message_send_cannot_be_recalled_but_next_run_uses_retargeted_id(
        tmp_path, monkeypatch):
    root = tmp_path / "registry"
    _session(OLD, tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def send(session_id, text, **kwargs):
        calls.append(session_id)
        if len(calls) == 1:
            started.set()
            await release.wait()
        return {"status": "queued", "sessionId": session_id}

    monkeypatch.setattr(worker, "send_session", send)
    job = jobs.start_message(
        OLD, "repeat", {"type": "interval", "intervalSeconds": 1},
        registry_root=root)
    due = time.time() + 2

    async def exercise():
        running = asyncio.create_task(
            jobs.run_due_message_jobs(now=due, registry_root=root))
        await asyncio.wait_for(started.wait(), timeout=5)
        changed = jobs.retarget_session_jobs(OLD, NEW, registry_root=root)
        assert changed["updated"] == 1
        release.set()
        assert await asyncio.wait_for(running, timeout=5) == 1

        stored = jobs.get(job["jobId"], registry_root=root)
        assert stored["status"] == "scheduled"
        assert stored["targetSessionId"] == NEW
        jobs._update(job["jobId"], {
            "nextRunAt": jobs._iso_utc(time.time() - 1),
        }, registry_root=root)
        assert await jobs.run_due_message_jobs(
            now=time.time() + 2, registry_root=root) == 1

    asyncio.run(exercise())
    assert calls == [OLD, NEW]
