"""T-040 task context inheritance and report pairing tests."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CbcAdapter


def _reset():
    worker.workers.clear()
    worker._workers_by_session.clear()
    worker._task_status.clear()
    worker._queue_locks.clear()
    worker._spawn_locks.clear()
    _sess._cache.clear()


def _setup(session_id="ses-t040"):
    s = _sess.Session(id=session_id, name="t040", adapter="cbc", model="test")
    _sess._cache[s.id] = s
    w = worker.Worker(
        worker_id="worker-t040",
        session_id=s.id,
        adapter=CbcAdapter(),
        status="idle",
        process=None,
        pending_signal=asyncio.Queue(),
    )
    worker.workers[w.worker_id] = w
    return s, w


def test_agent_send_inherits_without_turning_followups_into_duplicates(monkeypatch):
    """Repeated ordinary sends share context but each remains a queue item."""
    _reset()

    async def no_save(_session):
        return None

    monkeypatch.setattr(_sess, "save_async", no_save)
    s, _w = _setup()

    async def scenario():
        await worker.assign(s.id, "formal one", task_id="T-001")
        await worker.send_session(s.id, "followup one")
        await worker.send_session(s.id, "followup two")
        await worker.assign(s.id, "formal two", task_id="T-002")
        await worker.send_session(s.id, "followup three")

    asyncio.run(scenario())
    items = s.queue_pending
    assert [item["taskId"] for item in items] == [
        "T-001", "T-001", "T-001", "T-002", "T-002"
    ]
    assert items[0]["taskIdSource"] == "assign"
    assert all(item["taskIdSource"] == "active" for item in items[1:3])
    assert items[3]["taskIdSource"] == "assign"
    assert items[4]["taskIdSource"] == "active"
    assert len(items) == 5
    assert s.active_task_id == "T-002"
    _reset()


def test_completed_task_context_is_cleared_and_does_not_leak_to_send(monkeypatch):
    _reset()

    async def no_save(_session):
        return None

    monkeypatch.setattr(_sess, "save_async", no_save)
    s, _w = _setup()

    async def scenario():
        await worker.assign(s.id, "formal", task_id="T-001")
        assert worker._clear_active_task_if_current(s, "T-001") is True
        return await worker.send_session(s.id, "ordinary after completion")

    result = asyncio.run(scenario())
    assert result["status"] == "queued"
    assert s.active_task_id is None
    assert s.queue_pending[-1]["taskId"] is None
    assert "taskIdSource" not in s.queue_pending[-1]
    _reset()


def test_active_task_id_round_trips_and_recovery_only_uses_pending_formal_task(monkeypatch):
    _reset()

    async def no_save(_session):
        return None

    monkeypatch.setattr(_sess, "save_async", no_save)
    s, _w = _setup()

    async def scenario():
        await worker.assign(s.id, "formal", task_id="T-001")
        data = s.to_dict()
        restored = _sess.Session._from_data(data)
        assert restored.active_task_id == "T-001"
        assert worker._migrate_queue_delivery_state(restored, restore_ledger=True) is False

        completed = _sess.Session._from_data({
            "id": "ses-completed", "name": "completed",
            "history": [{"role": "user", "content": "done", "taskId": "T-old"}],
            "last_result": {"status": "done", "taskId": "T-old"},
        })
        assert completed.active_task_id is None
        assert worker._migrate_queue_delivery_state(completed, restore_ledger=True) is False

    asyncio.run(scenario())
    _reset()


def test_inherited_send_does_not_consume_formal_assign_idempotency(monkeypatch):
    _reset()

    async def no_save(_session):
        return None

    monkeypatch.setattr(_sess, "save_async", no_save)
    s, _w = _setup()

    async def scenario():
        first = await worker.assign(s.id, "formal", task_id="T-001")
        followup = await worker.send_session(s.id, "followup")
        retry = await worker.assign(s.id, "formal retry", task_id="T-001")
        return first, followup, retry

    first, followup, retry = asyncio.run(scenario())
    assert first["status"] == "queued"
    assert followup["status"] == "queued"
    assert retry["status"] == "pending"
    assert len(s.queue_pending) == 2
    _reset()


def test_report_uses_the_task_id_carried_by_the_current_input(monkeypatch):
    """A report is paired from the current queue item, not old task state."""
    _reset()

    async def no_save(_session):
        return None

    async def fake_consumer(current_worker, _text, _source, _session,
                            *, on_handoff=None):
        current_task_id = current_worker._current_task_id
        await on_handoff()
        await worker._enqueue_report(
            current_worker.session_id, "done", "answer", current_task_id,
            current_worker.worker_id)

    monkeypatch.setattr(_sess, "save_async", no_save)
    monkeypatch.setattr(worker, "_consumer_oneshot", fake_consumer)
    monkeypatch.setattr(worker, "resolve_execution_mode", lambda *_: "oneshot")
    s, w = _setup()
    manager = _sess.Session(id="ses-manager", name="manager")
    s.managed_by = manager.id
    manager.report_subscriptions = {s.id}
    _sess._cache[manager.id] = manager

    async def scenario():
        await worker.assign(s.id, "formal", task_id="T-001")
        # This inherited message is the input whose turn will produce the
        # report; it must retain T-001 even after the formal row is handed off.
        await worker.send_session(s.id, "followup")
        item = s.queue_pending[0]
        await worker._deliver_queue_unit(w, s, [item])

    asyncio.run(scenario())
    assert manager.queue_pending[-1]["taskId"] == "T-001"
    assert manager.queue_pending[-1]["status"] == "done"
    _reset()
