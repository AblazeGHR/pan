"""T-062.6a per-Session persistence ordering and contention regressions."""

import asyncio
import json
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CbcAdapter


def _jsonl(session_id: str) -> list[dict]:
    path = _sess._history_path(session_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_different_sessions_flush_without_cross_session_blocking(monkeypatch):
    """A blocked history write in A must not hold up an independent B write."""
    first = _sess.create(name="shard-a")
    second = _sess.create(name="shard-b")
    first.history.append({"role": "user", "content": "a"})
    second.history.append({"role": "user", "content": "b"})

    entered = threading.Event()
    release = threading.Event()
    original_append = _sess._append_jsonl
    first_path = _sess._history_path(first.id)

    def blocked_append(path, items):
        if path == first_path:
            entered.set()
            assert release.wait(2), "first Session writer was not released"
        return original_append(path, items)

    monkeypatch.setattr(_sess, "_append_jsonl", blocked_append)

    async def scenario():
        first_task = asyncio.create_task(_sess.save_async(first))
        assert await asyncio.to_thread(entered.wait, 2)
        async def save_second():
            await _sess.save_async(second)

        second_task = asyncio.create_task(save_second())
        try:
            await asyncio.wait_for(asyncio.shield(second_task), timeout=2)
            assert not first_task.done(), "Session A unexpectedly completed"
            diagnostics = _sess.save_diagnostics(first.id)
            assert diagnostics["active"] is True
            assert diagnostics["queueDepth"] == 0
        finally:
            release.set()
            await asyncio.gather(first_task, second_task, return_exceptions=True)

    asyncio.run(scenario())
    assert _jsonl(first.id)[-1]["content"] == "a"
    assert _jsonl(second.id)[-1]["content"] == "b"


def test_same_session_flushes_are_fifo_and_do_not_duplicate_history(monkeypatch):
    """Tickets preserve same-Session order and the T-062.2 end cursor."""
    session = _sess.create(name="fifo")
    first = {"role": "user", "content": "first"}
    second = {"role": "assistant", "content": "second"}
    session.history.append(first)
    entered = threading.Event()
    release = threading.Event()
    batches = []
    original_append = _sess._append_jsonl

    def record_append(path, items):
        batch = list(items)
        batches.append(batch)
        if batch == [first]:
            entered.set()
            assert release.wait(2), "first FIFO write was not released"
        return original_append(path, items)

    monkeypatch.setattr(_sess, "_append_jsonl", record_append)

    async def scenario():
        first_task = asyncio.create_task(_sess.save_async(session))
        assert await asyncio.to_thread(entered.wait, 2)
        session.history.append(second)
        second_task = asyncio.create_task(_sess.save_async(session))
        release.set()
        await asyncio.gather(first_task, second_task)

    asyncio.run(scenario())
    assert batches == [[first], [second]]
    assert session._hist_persisted == 2
    assert _jsonl(session.id) == [first, second]


def test_history_replace_keeps_append_after_blocked_full_flush(monkeypatch):
    """A full history replacement and a racing append retain both batches."""
    session = _sess.create(name="replace")
    old = {"role": "user", "content": "old"}
    session.history.append(old)
    _sess.save(session)

    replacement = {"role": "user", "content": "replacement"}
    live_append = {"role": "assistant", "content": "live-after-replace"}
    session.history = [replacement]
    entered = threading.Event()
    release = threading.Event()
    full_batches = []
    original_write = _sess._write_jsonl

    def blocked_full_write(path, items):
        full_batches.append(list(items))
        entered.set()
        assert release.wait(2), "full replacement was not released"
        return original_write(path, items)

    monkeypatch.setattr(_sess, "_write_jsonl", blocked_full_write)

    async def scenario():
        full_task = asyncio.create_task(asyncio.to_thread(_sess.save_full, session))
        assert await asyncio.to_thread(entered.wait, 2)
        session.history.append(live_append)
        append_task = asyncio.create_task(_sess.save_async(session))
        release.set()
        await asyncio.gather(full_task, append_task)

    asyncio.run(scenario())
    assert full_batches == [[replacement]]
    assert _jsonl(session.id) == [replacement, live_append]


def test_save_failure_releases_ticket_and_reports_bounded_diagnostics(monkeypatch):
    """A failed writer can be retried and diagnostics contain no message body."""
    session = _sess.create(name="failure")
    failed = {"role": "user", "content": "do-not-log-this"}
    session.history.append(failed)
    original_append = _sess._append_jsonl
    attempts = 0

    def fail_once(path, items):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("synthetic persistence failure")
        return original_append(path, items)

    monkeypatch.setattr(_sess, "_append_jsonl", fail_once)

    async def scenario():
        with pytest.raises(OSError):
            await _sess.save_async(session)
        await _sess.save_async(session)

    asyncio.run(scenario())
    diagnostics = _sess.save_diagnostics(session.id)
    assert diagnostics["active"] is False
    assert diagnostics["queueDepth"] == 0
    assert diagnostics["failureCount"] == 1
    assert diagnostics["lastErrorType"] is None
    assert "do-not-log-this" not in json.dumps(diagnostics)
    assert _jsonl(session.id) == [failed]


def test_cancelled_save_waits_for_writer_and_releases_ticket(monkeypatch):
    """Cancellation cannot strand the per-Session writer gate."""
    session = _sess.create(name="cancel")
    message = {"role": "user", "content": "cancel-me"}
    session.history.append(message)
    entered = threading.Event()
    release = threading.Event()
    original_append = _sess._append_jsonl

    def blocked_append(path, items):
        entered.set()
        assert release.wait(2), "cancelled writer was not released"
        return original_append(path, items)

    monkeypatch.setattr(_sess, "_append_jsonl", blocked_append)

    async def scenario():
        task = asyncio.create_task(_sess.save_async(session))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "save cancellation abandoned the active writer"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The cancelled await did not cancel the underlying durable write.
        await _sess.save_async(session)

    asyncio.run(scenario())
    diagnostics = _sess.save_diagnostics(session.id)
    assert diagnostics["active"] is False
    assert diagnostics["queueDepth"] == 0
    assert _jsonl(session.id) == [message]


def test_queue_receipt_uses_sharded_session_persistence(monkeypatch):
    """A durable queue receipt survives cache reload through the shard."""
    session = _sess.create(name="queue-receipt")

    async def no_wake(*_args, **_kwargs):
        return None

    monkeypatch.setattr(worker, "_wake_worker", no_wake)

    async def scenario():
        result = await worker.enqueue_notice(
            session.id, "receipt-body", source="automation",
        )
        assert result["ok"] is True

    asyncio.run(scenario())
    _sess._cache.clear()
    loaded = _sess.get(session.id)
    assert loaded is not None
    assert loaded.queue_pending[0]["result"] == "receipt-body"
    assert _sess.save_diagnostics(session.id)["queueDepth"] == 0


def test_usage_merge_and_terminal_events_keep_durable_order(monkeypatch):
    """Usage merge stays after base save; terminal result is persisted before events."""
    session = _sess.create(name="terminal-order", model="initial")
    session.cli_session_id = "cli-terminal-order"
    session.queue_pending = [{"type": "task", "text": "queued"}]
    _sess.save(session)
    events = []

    async def broadcast(event):
        events.append(event)

    worker.set_broadcaster(broadcast)
    worker._usage_enrichment_adapters.clear()
    worker._usage_enrichment_tasks.clear()
    worker._usage_enrichment_locks.clear()

    class Adapter:
        name = "cbc"

        def enrich_after_result(self, snapshot):
            snapshot.model = "provider-model"
            return [{"model": "provider-model",
                     "rawUsage": {"completion_tokens": 3}}]

    worker._queue_usage_enrichment(
        session, Adapter(), task_id="terminal-task", task_seq=1,
        worker_id="terminal-worker", generation=1,
    )
    monkeypatch.setattr(worker, "_schedule_usage_enrichment", lambda _sid: None)
    task_worker = worker.Worker(
        worker_id="terminal-worker", session_id=session.id,
        adapter=CbcAdapter(), status="idle", process=MagicMock(),
        pending_signal=asyncio.Queue(),
    )

    async def scenario():
        # Run the provider merge through its detached snapshot path first.  It
        # must persist usage without removing the unrelated durable queue row.
        await worker._run_usage_enrichment(session.id)
        assert session.model == "provider-model"
        terminal = await worker._persist_terminal_state(
            task_worker, session, "done", "answer",
        )
        await worker._publish_terminal_events(task_worker, terminal, session)

    try:
        asyncio.run(scenario())
        assert [event["type"] for event in events] == [
            "worker.result", "worker.status",
        ]
        _sess._cache.clear()
        loaded = _sess.get(session.id)
        assert loaded.last_result["status"] == "done"
        assert loaded.history[-1]["content"] == "answer"
        assert loaded.queue_pending == [{"type": "task", "text": "queued"}]
        assert loaded.total_usage["completion_tokens"] == 3
    finally:
        worker.set_broadcaster(None)
        worker._usage_enrichment_adapters.clear()
        worker._usage_enrichment_tasks.clear()
        worker._usage_enrichment_locks.clear()
