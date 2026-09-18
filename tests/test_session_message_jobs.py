"""T-043 durable time-based Session-message Job contracts."""

import asyncio
import time

import pytest

from packages.core import background_jobs as jobs
from packages.core import session as sess
from packages.core import worker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "DEFAULT_ROOT", tmp_path / "background_jobs")
    monkeypatch.setattr(jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sess, "_all_loaded", False)
    sess._cache.clear()
    yield
    sess._cache.clear()


def _session(tmp_path, sid="ses_target"):
    result = sess.Session(id=sid, name=sid, workdir=str(tmp_path))
    sess._cache[sid] = result
    return result


def test_once_delay_persists_and_dispatches_via_send_semantics(monkeypatch, tmp_path):
    target = _session(tmp_path)
    caller = _session(tmp_path, "ses_caller")
    calls = []

    async def send(session_id, text, **kwargs):
        calls.append((session_id, text, kwargs))
        return {"status": "queued", "sessionId": session_id, "workerId": "worker-1"}

    monkeypatch.setattr(worker, "send_session", send)
    job = jobs.start_message(
        target.id, "////by agent : ses_caller | caller\nrun report",
        {"type": "once", "delaySeconds": 1},
        description="T-043 once", source="agent", source_session_id=caller.id,
        creator_session_id=caller.id)
    assert job["kind"] == jobs.SESSION_MESSAGE_KIND
    assert job["targetSessionId"] == target.id
    assert job["description"] == "T-043 once"
    assert job["creatorSessionId"] == caller.id
    assert job["status"] == "pending"

    assert asyncio.run(jobs.run_due_message_jobs(now=time.time() + 2)) == 1
    stored = jobs.get(job["jobId"])
    assert stored["status"] == "completed"
    assert stored["runCount"] == 1
    assert calls == [(target.id, job["text"], {
        "source": "agent", "source_session_id": caller.id})]


def test_absolute_once_and_schedule_edit_are_persisted(monkeypatch, tmp_path):
    _session(tmp_path)
    now = time.time()
    job = jobs.start_message("ses_target", "hello", {
        "type": "once", "at": jobs._iso_utc(now + 300)}, registry_root=None)
    changed = jobs.update_message(job["jobId"], schedule={
        "type": "interval", "intervalSeconds": 60}, description="recurring")
    assert changed["status"] == "pending"
    assert changed["schedule"] == {"type": "interval", "intervalSeconds": 60.0}
    assert changed["description"] == "recurring"
    assert changed["nextRunAt"]


@pytest.mark.parametrize("schedule", [
    {"type": "once", "delaySeconds": 1, "at": "2030-01-01T00:00:00Z"},
    {"type": "interval", "intervalSeconds": 0},
    {"type": "weekly", "weekday": 7, "time": "09:00"},
    {"type": "weekly", "weekday": 1, "time": "25:00"},
])
def test_invalid_time_schedule_is_rejected(tmp_path, schedule):
    _session(tmp_path)
    with pytest.raises(ValueError):
        jobs.start_message("ses_target", "hello", schedule)


def test_interval_repeats_after_restart_recovery_and_cancel_stops_future_runs(
        monkeypatch, tmp_path):
    _session(tmp_path)
    calls = []

    async def send(session_id, text, **kwargs):
        calls.append(text)
        return {"status": "queued", "sessionId": session_id}

    monkeypatch.setattr(worker, "send_session", send)
    job = jobs.start_message("ses_target", "tick", {
        "type": "interval", "intervalSeconds": 1})
    assert asyncio.run(jobs.run_due_message_jobs(now=time.time() + 2)) == 1
    after_first = jobs.get(job["jobId"])
    assert after_first["status"] == "scheduled"
    assert after_first["runCount"] == 1
    # A new scheduler process sees the persisted due record and sends one
    # occurrence; it does not rely on an in-memory timer.
    assert asyncio.run(jobs.run_due_message_jobs(
        now=time.time() + 4)) == 1
    assert jobs.get(job["jobId"])["runCount"] == 2
    jobs.cancel_message(job["jobId"])
    assert jobs.get(job["jobId"])["status"] == "cancelled"
    assert asyncio.run(jobs.run_due_message_jobs(now=time.time() + 100)) == 0
    assert len(calls) == 2


def test_weekly_schedule_accepts_weekday_and_local_time(tmp_path):
    _session(tmp_path)
    job = jobs.start_message("ses_target", "weekly", {
        "type": "weekly", "weekday": 2, "time": "09:30"})
    assert job["schedule"] == {"type": "weekly", "weekday": 2, "time": "09:30"}
    assert job["nextRunAt"]


def test_stale_running_job_is_requeued_after_service_restart(monkeypatch, tmp_path):
    _session(tmp_path)
    calls = []

    async def send(session_id, text, **kwargs):
        calls.append(text)
        return {"status": "queued", "sessionId": session_id}

    monkeypatch.setattr(worker, "send_session", send)
    job = jobs.start_message("ses_target", "recover", {
        "type": "once", "delaySeconds": 60})
    stale = jobs.get(job["jobId"])
    stale.update(status="running", runStartedAt=time.time() - 30,
                 nextRunAt=jobs._iso_utc(time.time() - 30))
    jobs._save(stale)
    assert asyncio.run(jobs.run_due_message_jobs(now=time.time())) == 1
    assert calls == ["recover"]
    assert jobs.get(job["jobId"])["status"] == "completed"


def test_message_job_never_enters_process_runner_retry_path(tmp_path):
    _session(tmp_path)
    job = jobs.start_message("ses_target", "hello", {
        "type": "once", "delaySeconds": 60})
    with pytest.raises(ValueError, match="edited or recreated"):
        jobs.retry(job["jobId"])


def test_scheduled_broadcast_deduplicates_and_isolates_target_failures(monkeypatch, tmp_path):
    for sid in ("ses_a", "ses_b", "ses_c", "ses_caller"):
        _session(tmp_path, sid)
    calls = []

    async def send(session_id, text, **kwargs):
        calls.append((session_id, text, kwargs))
        if session_id == "ses_b":
            raise RuntimeError("target unavailable")
        return {"status": "queued", "workerId": f"worker-{session_id}"}

    monkeypatch.setattr(worker, "send_session", send)
    job = jobs.start_broadcast(
        ["ses_a", "ses_b", "ses_a", "ses_c"],
        "////by agent : ses_caller | sender\nrun",
        {"type": "once", "delaySeconds": 1},
        description="scheduled fan-out", source="agent",
        source_session_id="ses_caller", creator_session_id="ses_caller")
    assert job["kind"] == jobs.SESSION_BROADCAST_KIND
    assert job["targetSessionIds"] == ["ses_a", "ses_b", "ses_c"]
    assert job["creatorSessionId"] == "ses_caller"

    assert asyncio.run(jobs.run_due_message_jobs(now=time.time() + 2)) == 1
    stored = jobs.get(job["jobId"])
    assert stored["status"] == "completed"
    assert stored["runCount"] == 1
    assert stored["lastDelivery"]["status"] == "partial"
    assert [item["sessionId"] for item in stored["lastDelivery"]["results"]] == [
        "ses_a", "ses_b", "ses_c"]
    assert stored["lastDelivery"]["results"][1]["status"] == "error"
    assert [item[0] for item in calls] == ["ses_a", "ses_b", "ses_c"]
    assert all(item[2] == {"source": "agent", "source_session_id": "ses_caller"}
               for item in calls)
    # A completed one-shot occurrence is idempotent across a second scheduler scan.
    assert asyncio.run(jobs.run_due_message_jobs(now=time.time() + 1000)) == 0
    assert [item[0] for item in calls] == ["ses_a", "ses_b", "ses_c"]


def test_scheduled_broadcast_interval_recovery_and_no_burst(monkeypatch, tmp_path):
    for sid in ("ses_a", "ses_b"):
        _session(tmp_path, sid)
    calls = []

    async def send(session_id, text, **kwargs):
        calls.append(session_id)
        return {"status": "queued", "sessionId": session_id}

    monkeypatch.setattr(worker, "send_session", send)
    job = jobs.start_broadcast(["ses_a", "ses_b"], "tick", {
        "type": "interval", "intervalSeconds": 60})
    assert asyncio.run(jobs.run_due_message_jobs(now=time.time() + 61)) == 1
    first = jobs.get(job["jobId"])
    assert first["status"] == "scheduled"
    assert first["runCount"] == 1
    assert len(calls) == 2

    stale = jobs.get(job["jobId"])
    stale.update(status="running", runStartedAt=time.time() - 30,
                 nextRunAt=jobs._iso_utc(time.time() - 30))
    jobs._save(stale)
    assert asyncio.run(jobs.run_due_message_jobs(now=time.time())) == 1
    recovered = jobs.get(job["jobId"])
    assert recovered["status"] == "scheduled"
    assert recovered["runCount"] == 2
    assert len(calls) == 4
    assert recovered["nextRunAt"]


def test_scheduled_broadcast_edit_and_cancel_prevent_future_send(monkeypatch, tmp_path):
    for sid in ("ses_a", "ses_b", "ses_c"):
        _session(tmp_path, sid)
    calls = []

    async def send(session_id, text, **kwargs):
        calls.append(session_id)
        return {"status": "queued", "sessionId": session_id}

    monkeypatch.setattr(worker, "send_session", send)
    job = jobs.start_broadcast(["ses_a", "ses_b"], "later", {
        "type": "once", "delaySeconds": 60})
    changed = jobs.update_message(
        job["jobId"], target_session_ids=["ses_c", "ses_c", "ses_a"],
        schedule={"type": "once", "delaySeconds": 120}, description="edited")
    assert changed["targetSessionIds"] == ["ses_c", "ses_a"]
    assert changed["status"] == "pending"
    assert changed["description"] == "edited"
    cancelled = jobs.cancel_message(job["jobId"])
    assert cancelled["status"] == "cancelled"
    assert asyncio.run(jobs.run_due_message_jobs(now=time.time() + 1000)) == 0
    assert calls == []
