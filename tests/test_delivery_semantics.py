"""Regression tests for the durable FIFO queue hand-off contract."""

import asyncio
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

    result = asyncio.run(worker.retry_pending_item(s.id, task["id"]))
    assert result is task
    assert s.queue_pending == [task]
    assert "nextAttemptAt" not in task
    assert "lastDeliveryError" not in task
    _cleanup()
