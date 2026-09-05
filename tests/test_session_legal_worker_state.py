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
