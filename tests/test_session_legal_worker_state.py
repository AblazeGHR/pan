"""Persistence and attribution tests for lastLegalWorkerState."""

import json
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as sess  # noqa: E402
from packages.core import worker  # noqa: E402
from packages.core.adapters import CbcAdapter  # noqa: E402
from packages.web import server as web_server  # noqa: E402


@pytest.fixture
def isolated_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path)
    sess._cache.clear()
    sess._newline_terminated_jsonl.clear()
    yield tmp_path
    sess._cache.clear()
    sess._newline_terminated_jsonl.clear()


def test_last_legal_worker_state_round_trips_in_existing_metadata(isolated_sessions):
    session = sess.create("legal-state")
    session.last_legal_worker_state = "idle"
    session.queue_pending.append({"type": "task", "text": "preserve me"})
    sess.save(session)

    metadata = json.loads((isolated_sessions / f"{session.id}.json").read_text())
    assert metadata["last_legal_worker_state"] == "idle"
    assert metadata["queue_pending"] == [{"type": "task", "text": "preserve me"}]

    sess._cache.clear()
    loaded = sess.get(session.id)
    assert loaded is not None
    assert loaded.last_legal_worker_state == "idle"


def test_last_legal_worker_state_save_is_atomic_and_does_not_leave_tmp(isolated_sessions):
    session = sess.create("atomic-legal-state")
    session.last_legal_worker_state = "offline"
    sess.save(session)

    metadata_path = isolated_sessions / f"{session.id}.json"
    assert json.loads(metadata_path.read_text())["last_legal_worker_state"] == "offline"
    assert not (isolated_sessions / f"{session.id}.json.tmp").exists()


def test_delete_removes_metadata_and_last_legal_state_history(isolated_sessions):
    session = sess.create("delete-legal-state")
    session.last_legal_worker_state = "offline"
    session.history.append({"role": "user", "content": "history"})
    sess.save(session)
    metadata_path = isolated_sessions / f"{session.id}.json"
    history_path = isolated_sessions / f"{session.id}.history.jsonl"
    assert metadata_path.exists() and history_path.exists()

    sess.delete(session.id)

    assert not metadata_path.exists()
    assert not history_path.exists()
    assert sess.get(session.id) is None


def test_external_eof_only_changes_live_runtime_and_preserves_legal_state(
    isolated_sessions, monkeypatch,
):
    session = sess.create("external-eof")
    session.last_legal_worker_state = "idle"
    sess.save(session)

    class DeadProcess:
        returncode = 1

    w = worker.Worker(
        worker_id="external-eof-worker",
        session_id=session.id,
        adapter=CbcAdapter(),
        status="idle",
        process=DeadProcess(),
        pending_signal=None,
    )
    worker.workers[w.worker_id] = w
    worker._register_worker(w)

    async def no_stdout(_worker):
        if False:
            yield b""

    monkeypatch.setattr(worker, "_iter_stdout_lines", no_stdout)
    monkeypatch.setattr(worker, "_broadcast", None, raising=False)
    asyncio.run(worker._read_stdout(w))

    assert w.status == "zombie"
    assert session.last_legal_worker_state == "idle"
    assert sess.get(session.id).last_legal_worker_state == "idle"


def test_recovery_candidates_use_persisted_running_state_and_skip_live_workers(
    isolated_sessions, monkeypatch,
):
    candidate = sess.create("recovery-candidate")
    candidate.last_legal_worker_state = "running"
    sess.save(candidate)

    active = sess.create("worker-still-live")
    active.last_legal_worker_state = "running"
    sess.save(active)

    not_running = sess.create("not-running")
    not_running.last_legal_worker_state = "idle"
    sess.save(not_running)

    candidate_metadata = isolated_sessions / f"{candidate.id}.json"
    persisted_before = candidate_metadata.read_bytes()

    live_worker = worker.Worker(
        worker_id="active-worker",
        session_id=active.id,
        adapter=CbcAdapter(),
        status="idle",
    )
    monkeypatch.setattr(
        worker,
        "find_alive_worker_by_session",
        lambda session_id: live_worker if session_id == active.id else None,
    )

    result = asyncio.run(web_server.api_session_recovery_candidates())

    assert result == {
        "sessions": [{
            "id": candidate.id,
            "name": candidate.name,
            "adapter": candidate.adapter,
            "workdir": candidate.workdir,
            "updatedAt": candidate.updated_at,
            "lastLegalWorkerState": "running",
        }],
    }
    assert candidate_metadata.read_bytes() == persisted_before


def test_runtime_sync_helper_persists_observed_offline_state(isolated_sessions):
    session = sess.create("runtime-sync")
    session.last_legal_worker_state = "running"
    sess.save(session)

    result = asyncio.run(worker.sync_legal_worker_state_to_runtime(session.id))

    assert result == {
        "sessionId": session.id,
        "status": "updated",
        "legalWorkerState": "offline",
        "runtimeWorkerStatus": "offline",
    }
    assert session.last_legal_worker_state == "offline"
    metadata = json.loads((isolated_sessions / f"{session.id}.json").read_text())
    assert metadata["last_legal_worker_state"] == "offline"


def test_runtime_sync_does_not_mark_a_still_running_provider_process_offline(
    isolated_sessions,
):
    session = sess.create("runtime-still-active")
    session.last_legal_worker_state = "running"
    sess.save(session)

    class LiveProcess:
        returncode = None

    runtime = worker.Worker(
        worker_id="runtime-still-active-worker",
        session_id=session.id,
        adapter=CbcAdapter(),
        status="zombie",
        process=LiveProcess(),
    )
    worker.workers[runtime.worker_id] = runtime
    worker._register_worker(runtime)
    try:
        async def sync_after_consumer_stopped():
            stopped_consumer = asyncio.create_task(asyncio.sleep(0))
            await stopped_consumer
            runtime._consume_task = stopped_consumer
            return await worker.sync_legal_worker_state_to_runtime(session.id)

        result = asyncio.run(sync_after_consumer_stopped())
        assert result["status"] == "error"
        assert "not stopped" in result["error"]
        assert session.last_legal_worker_state == "running"
    finally:
        worker.workers.pop(runtime.worker_id, None)
        worker._workers_by_session.pop(session.id, None)


def test_runtime_sync_uses_live_worker_status(isolated_sessions):
    session = sess.create("runtime-held")
    session.last_legal_worker_state = "running"
    sess.save(session)

    class LiveProcess:
        returncode = None

    runtime = worker.Worker(
        worker_id="runtime-held-worker",
        session_id=session.id,
        adapter=CbcAdapter(),
        status="held",
        process=LiveProcess(),
    )
    worker.workers[runtime.worker_id] = runtime
    worker._register_worker(runtime)
    try:
        result = asyncio.run(worker.sync_legal_worker_state_to_runtime(session.id))
        assert result["status"] == "updated"
        assert result["legalWorkerState"] == "held"
        assert result["runtimeWorkerStatus"] == "held"
        assert session.last_legal_worker_state == "held"
    finally:
        worker.workers.pop(runtime.worker_id, None)
        worker._workers_by_session.pop(session.id, None)


def test_shutdown_can_preserve_pre_exit_legal_running_without_a_worker(
    isolated_sessions, monkeypatch,
):
    session = sess.create("preserve-on-exit")
    session.last_legal_worker_state = "running"
    sess.save(session)
    worker.workers.clear()
    worker._workers_by_session.clear()
    worker._worker_generations.clear()
    worker._recovery_required.clear()

    async def no_recoveries(**kwargs):
        return None

    monkeypatch.setattr(worker, "drain_recoveries", no_recoveries)
    asyncio.run(worker.shutdown_all(
        mark_legal_offline=True,
        preserve_legal_running_session_ids=[session.id],
    ))

    assert session.last_legal_worker_state == "running"
    metadata = json.loads((isolated_sessions / f"{session.id}.json").read_text())
    assert metadata["last_legal_worker_state"] == "running"


def test_shutdown_marks_requested_persisted_running_session_offline_without_worker(
    isolated_sessions, monkeypatch,
):
    session = sess.create("offline-on-exit")
    session.last_legal_worker_state = "running"
    sess.save(session)
    worker.workers.clear()
    worker._workers_by_session.clear()
    worker._worker_generations.clear()
    worker._recovery_required.clear()

    async def no_recoveries(**kwargs):
        return None

    monkeypatch.setattr(worker, "drain_recoveries", no_recoveries)
    asyncio.run(worker.shutdown_all(
        mark_legal_offline=True,
        mark_legal_offline_session_ids=[session.id],
    ))

    assert session.last_legal_worker_state == "offline"
    metadata = json.loads((isolated_sessions / f"{session.id}.json").read_text())
    assert metadata["last_legal_worker_state"] == "offline"
