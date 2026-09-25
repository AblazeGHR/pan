"""Regression tests for the durable FIFO queue hand-off contract."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CbcAdapter


class _DeadProc:
    def __init__(self, returncode=1):
        self.returncode = returncode


def _cleanup():
    for task in list(worker._queue_retry_tasks.values()):
        task.cancel()
    worker._queue_retry_tasks.clear()
    for task in list(worker._recovery_tasks.values()):
        task.cancel()
    worker._recovery_tasks.clear()
    worker.workers.clear()
    worker._inflight_task_ids.clear()
    worker._task_status.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid="ses_mgr", **kwargs):
    s = _sess.Session(id=sid, name="test", **kwargs)
    _sess._cache[sid] = s
    return s


def _make_worker(sid, process=None):
    w = worker.Worker(
        worker_id="worker-mgr", session_id=sid,
        adapter=CbcAdapter(), status="idle", process=process,
        pending_signal=asyncio.Queue(), _task_done=asyncio.Event(),
        _hist_flush_event=asyncio.Event(),
    )
    worker.workers[w.worker_id] = w
    return w


def _make_task(i=1):
    return {
        "type": "task", "id": f"task{i}", "text": f"job {i}",
        "source": "agent", "seq": i, "taskId": f"tid{i}",
        "deliveryState": "queued",
    }


def _make_report(i=1):
    return {
        "type": "report", "id": f"report{i}", "source": "report",
        "status": "done", "result": f"r{i}", "sessionId": "ses_child",
        "taskId": f"t{i}", "workerId": "worker-1",
        "deliveryState": "queued",
    }


async def _noop_save(_session):
    return None


def test_stream_handoff_removes_only_after_write_boundary(monkeypatch):
    """A successful callback removes the row before provider business output."""
    _cleanup()
    s = _setup_session()
    first, second = _make_task(1), _make_task(2)
    s.queue_pending = [first, second]
    w = _make_worker(s.id)
    received = []
    monkeypatch.setattr(_sess, "save_async", _noop_save)

    async def fake_stream(ww, text, source, sess, *, on_handoff=None):
        assert sess.queue_pending[0] is first
        assert first["deliveryState"] == "writing"
        received.append((text, source))
        await on_handoff()
        # The provider function has not returned a business result yet, but the
        # hand-off has already completed and the row is gone.
        assert first not in sess.queue_pending

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "queue_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())
    assert received == [("job 1", "agent")]
    assert s.queue_pending == [second]
    assert s.history[0]["content"] == "job 1"
    _cleanup()


def test_failed_handoff_requeues_with_backoff(monkeypatch):
    _cleanup()
    s = _setup_session()
    task = _make_task()
    s.queue_pending = [task]
    w = _make_worker(s.id)
    monkeypatch.setattr(_sess, "save_async", _noop_save)

    async def failing_stream(ww, text, source, sess, *, on_handoff=None):
        raise OSError("stdin closed")

    monkeypatch.setattr(worker, "_consumer_stream", failing_stream)

    async def scenario():
        await w.pending_signal.put({"type": "queue_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())
    assert s.queue_pending == [task]
    assert task["deliveryState"] == "queued"
    assert task["deliveryAttempts"] == 1
    assert task["nextAttemptAt"] > 0
    assert s.history == []
    _cleanup()


def test_cancel_before_handoff_requeues(monkeypatch):
    _cleanup()
    s = _setup_session()
    task = _make_task()
    s.queue_pending = [task]
    w = _make_worker(s.id)
    started = asyncio.Event()
    monkeypatch.setattr(_sess, "save_async", _noop_save)

    async def blocked_stream(ww, text, source, sess, *, on_handoff=None):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "_consumer_stream", blocked_stream)

    async def scenario():
        consume = asyncio.create_task(worker._deliver_queue_unit(w, s, [task]))
        await started.wait()
        assert task["deliveryState"] == "writing"
        consume.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consume

    asyncio.run(scenario())
    assert task["deliveryState"] == "queued"
    assert task["deliveryAttempts"] == 1
    assert s.history == []
    _cleanup()


def test_task_history_receipt_survives_reserved_state_recovery(monkeypatch):
    """A recovered queue item reuses its old history row instead of appending it again."""
    _cleanup()
    s = _setup_session()
    task = _make_task()
    task.update({
        "kind": "task",
        "queueItemId": task["id"],
        "taskIdSource": "active",
        "dispatchState": "queued",
        "position": 0,
        "revision": 1,
    })
    s.queue_pending = [task]
    w = _make_worker(s.id)
    monkeypatch.setattr(_sess, "save_async", _noop_save)
    monkeypatch.setattr(worker, "_process_alive", lambda _worker: True)

    async def scenario():
        # This mark is the old format written before the crash. Its hash suffix
        # represented the queue lifecycle state at the first reservation.
        s.history = [{
            "role": "user",
            "content": task["text"],
            "source": "agent",
            "taskId": task["taskId"],
            "taskIdSource": "active",
            "delivered_keys": [f"task:{task['id']}:old-mutable-state-hash"],
        }]
        task["deliveryState"] = worker._DELIVERY_RESERVED
        task["reservedBy"] = w.worker_id
        task["reservedGeneration"] = w.generation
        task["reservedAt"] = 1.0
        worker._remember_queue_item(s, task, worker._DELIVERY_RESERVED)

        # Process recovery records the last phase and returns the same queue
        # item to queued state. That metadata used to change the receipt hash.
        assert worker._recover_delivery_states(s) is True
        assert task["deliveryState"] == worker._DELIVERY_QUEUED
        assert task["lastDeliveryState"] == worker._DELIVERY_RESERVED

        history_added = await worker._reserve_queue_unit(w, s, [task], task["text"])
        assert history_added is False
        assert len(s.history) == 1
        assert s.history[0]["content"] == task["text"]

    asyncio.run(scenario())
    _cleanup()


def test_report_history_key_ignores_mutable_delivery_phase_fields():
    report = _make_report()
    report.update({"dispatchState": "queued", "queueItemId": report["id"], "position": 0})
    original = worker._delivery_key(report)
    recovered = dict(report)
    recovered.update({
        "deliveryState": "queued",
        "dispatchState": "queued",
        "lastDeliveryState": "reserved",
        "deliveryAttempts": 1,
        "lastDeliveryError": "worker restarted before hand-off",
    })
    assert worker._delivery_key(recovered) == original
    s = _setup_session()
    old_bookkeeping = {
        "deliveryState", "reservedBy", "reservedGeneration", "reservedAt",
        "deliveryAttempts", "nextAttemptAt", "lastDeliveryError", "queueItemId",
    }
    legacy_identity = {key: value for key, value in report.items()
                       if key not in old_bookkeeping}
    legacy_digest = hashlib.sha1(
        json.dumps(legacy_identity, sort_keys=True, ensure_ascii=False, default=str)
        .encode("utf-8")
    ).hexdigest()[:12]
    legacy_key = f"report:{report['taskId']}:{legacy_digest}"
    s.history = [{"role": "user", "content": "report", "delivered_keys": [legacy_key]}]
    assert worker._delivery_mark_in_history(s, recovered) is True
    _cleanup()


def test_queue_reservation_persists_frontend_message_identity(monkeypatch):
    _cleanup()
    s = _setup_session()
    task = _make_task()
    s.queue_pending = [task]
    w = _make_worker(s.id)
    monkeypatch.setattr(_sess, "save_async", _noop_save)
    monkeypatch.setattr(worker, "_process_alive", lambda _worker: True)

    async def scenario():
        assert await worker._reserve_queue_unit(w, s, [task], task["text"]) is True
        assert s.history[0]["queueItemIds"] == [task["id"]]

    asyncio.run(scenario())
    _cleanup()


def test_report_and_qq_batch_is_contiguous_and_all_or_back(monkeypatch):
    _cleanup()
    s = _setup_session()
    report1, report2, task = _make_report(1), _make_report(2), _make_task(1)
    qq = {
        "type": "qq", "id": "qq1", "source": "qq", "qqTarget": "user:1",
        "targetType": "user", "targetId": "1", "nickname": "bob",
        "text": "hello", "time": "12:00", "deliveryState": "queued",
    }
    s.queue_pending = [report1, report2, qq, task]
    w = _make_worker(s.id)
    batches = []
    monkeypatch.setattr(_sess, "save_async", _noop_save)

    async def fake_stream(ww, text, source, sess, *, on_handoff=None):
        batches.append(text)
        assert all(item["deliveryState"] == "writing"
                   for item in (report1, report2, qq))
        await on_handoff()

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "report_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())
    assert len(batches) == 1
    assert all(value in batches[0] for value in ("r1", "r2", "hello"))
    assert s.queue_pending == [task]
    assert len(s.history) == 1
    assert len(s.history[0]["delivered_keys"]) == 3
    assert s.history[0]["queueItemIds"] == [report1["id"], report2["id"], qq["id"]]
    _cleanup()


def test_fifo_head_is_not_skipped_by_out_of_order_signal():
    _cleanup()
    s = _setup_session()
    first, report, second = _make_task(1), _make_report(1), _make_task(2)
    s.queue_pending = [first, report, second]
    assert worker._select_queue_unit(s) == [first]
    first["deliveryState"] = "reserved"
    assert worker._select_queue_unit(s) is None
    first["deliveryState"] = "queued"
    s.queue_pending = [report, first, second]
    unit = worker._select_queue_unit(s)
    assert unit == [report]
    assert second not in unit
    _cleanup()


def test_recovery_requeues_old_inflight_and_drops_sent_marker():
    _cleanup()
    s = _setup_session()
    unfinished, sent = _make_task(1), _make_task(2)
    unfinished["deliveryState"] = "in_flight"
    sent["deliveryState"] = "sent_to_cli"
    s.queue_pending = [unfinished, sent]
    w = _make_worker(s.id)

    changed = worker._recover_pending_signals(w, s)
    assert changed is True
    assert s.queue_pending == [unfinished]
    assert unfinished["deliveryState"] == "queued"
    assert unfinished["deliveryAttempts"] == 1
    assert w.pending_signal.get_nowait() == {"type": "queue_signal"}
    assert worker._migrate_queue_delivery_state(s) is False
    _cleanup()


def test_queue_retry_addresses_original_item(monkeypatch):
    _cleanup()
    s = _setup_session()
    task = _make_task()
    task.update({"nextAttemptAt": 9999999999, "lastDeliveryError": "closed"})
    s.queue_pending = [task]
    monkeypatch.setattr(_sess, "save_async", _noop_save)
    # retry 后无活 worker → 调度恢复；stub 防止 teardown 取消 spawn 中途的
    # recovery 任务在 Windows Proactor 上死锁（同 test_addressing_compat）。
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return "spawn suppressed by test"

    monkeypatch.setattr(worker, "create_worker", fake_create)

    async def scenario():
        result = await worker.retry_pending_item(s.id, task["id"])
        recovery = worker._recovery_tasks.get(s.id)
        if recovery is not None:
            await asyncio.wait_for(recovery, timeout=1)
        return result

    result = asyncio.run(scenario())
    assert result["item"] == task
    assert s.queue_pending == [task]
    assert "nextAttemptAt" not in task
    assert "lastDeliveryError" not in task
    assert spawned == ["ses_mgr"], "retry without live worker must schedule recovery"
    _cleanup()


def test_inherited_task_id_is_not_terminal_idempotency_key(monkeypatch):
    """An ordinary follow-up keeps report pairing but gets its own terminal key."""
    _cleanup()
    s = _setup_session()
    w = _make_worker(s.id)

    async def no_flush(_worker):
        return None

    monkeypatch.setattr(worker, "_flush_history_now", no_flush)

    async def scenario():
        w._current_seq = 1
        w._current_task_id = "T-045"
        w._current_task_idempotent = True
        first = await worker._persist_terminal_state(w, s, "done", "formal")

        # The next queue row is an agent_send_force follow-up.  It inherits
        # T-045 for report pairing, but taskIdSource=active means it is not an
        # assign retry and must not reuse task:T-045 as its terminal key.
        w._terminal_handled = False
        w._current_seq = 2
        w._current_task_id = "T-045"
        w._current_task_idempotent = False
        second = await worker._persist_terminal_state(w, s, "done", "follow-up")
        return first, second

    first, second = asyncio.run(scenario())
    assert s.history[0]["content"] == "formal"
    assert s.last_result["terminalKey"] == "seq:2"
    assert first["taskId"] == second["taskId"] == "T-045"
    assert worker._terminal_enrichment_key(
        "T-045", 1, w.worker_id, w.generation, task_idempotent=True
    ) == "task:T-045"
    assert worker._terminal_enrichment_key(
        "T-045", 2, w.worker_id, w.generation, task_idempotent=False
    ) == "seq:2"
    assert s.last_result["result"] == "follow-up"
    _cleanup()


def test_durable_duplicate_terminal_wakes_serial_consumer(monkeypatch):
    """Duplicate terminal suppression must not leave the queue consumer waiting."""
    _cleanup()
    s = _setup_session()
    w = _make_worker(s.id)
    s.last_result = {"status": "done", "terminalKey": "task:T-045"}
    w._current_task_id = "T-045"
    w._current_task_idempotent = True
    w._task_done.clear()

    result = asyncio.run(worker._persist_terminal_state(w, s, "done", "duplicate"))

    assert result is None
    assert w._terminal_handled is True
    assert w._task_done.is_set()
    _cleanup()


def test_recovery_reconciles_stale_ledger_reservation_to_queued():
    """A queued row plus stale reserved ledger is retryable, not permanently stuck."""
    _cleanup()
    s = _setup_session()
    item = _make_task()
    item["queueItemId"] = item["id"]
    s.queue_pending = [item]
    s.queue_delivery_ledger[item["id"]] = {
        **item,
        "deliveryState": "reserved",
        "reservedBy": "worker-2",
        "reservedGeneration": 3,
        "reservedAt": 123.0,
    }
    w = _make_worker(s.id)

    changed = worker._recover_pending_signals(w, s)

    assert changed is True
    assert s.queue_pending == [item]
    assert item["deliveryState"] == "queued"
    assert "reservedBy" not in item
    assert s.queue_delivery_ledger[item["id"]]["deliveryState"] == "queued"
    assert "reservedBy" not in s.queue_delivery_ledger[item["id"]]
    _cleanup()


def test_recovery_drops_pending_row_when_sent_ledger_is_durable():
    """A persisted sent receipt wins over a stale queue row and prevents replay."""
    _cleanup()
    s = _setup_session()
    item = _make_task()
    item["queueItemId"] = item["id"]
    s.queue_pending = [item]
    s.queue_delivery_ledger[item["id"]] = {
        **item,
        "deliveryState": "sent_to_cli",
        "dispatchState": "sent_to_cli",
    }
    w = _make_worker(s.id)

    changed = worker._recover_pending_signals(w, s)

    assert changed is True
    assert s.queue_pending == []
    assert s.queue_delivery_ledger[item["id"]]["deliveryState"] == "sent_to_cli"
    assert w.pending_signal.empty()
    _cleanup()
