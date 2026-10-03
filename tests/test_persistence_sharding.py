"""T-062.6a per-Session persistence ordering and contention regressions."""

import asyncio
import contextvars
import json
import sys
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
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


def test_sync_save_after_async_reservation_does_not_deadlock(monkeypatch):
    """A sync API save can arrive before a scheduled async writer Task runs."""
    session = _sess.create(name="sync-after-async")
    session.history.append({"role": "user", "content": "persist-once"})
    state = _sess._SAVE_STATES[session.id]
    original_wait = state.condition.wait

    def bounded_wait(timeout=None):
        # Bound the old implementation's deadlock without relying on an
        # asyncio timeout (the event loop itself is the blocked thread).
        if not original_wait(timeout=2):
            raise TimeoutError("save ticket depended on the blocked event loop")
        return True

    monkeypatch.setattr(state.condition, "wait", bounded_wait)

    async def scenario():
        async_save = asyncio.create_task(_sess.save_async(session))
        await asyncio.sleep(0)
        # The async save has reserved its ticket.  Do not yield again before
        # the synchronous API save; deferred to_thread submission deadlocks.
        try:
            _sess.save(session)
        finally:
            await async_save

    asyncio.run(scenario())
    assert _jsonl(session.id) == [{"role": "user", "content": "persist-once"}]
    assert state.pending == 0
    assert not state.active
    assert state.serving_ticket == state.next_ticket


def test_executor_submission_failure_retires_ticket(monkeypatch):
    """An executor rejection must not strand all later saves of the Session."""
    session = _sess.create(name="submit-failure")
    session.history.append({"role": "user", "content": "retry-after-rejection"})

    async def scenario():
        with monkeypatch.context() as patch:
            def reject_submission(*args, **kwargs):
                raise RuntimeError("executor rejected submission")

            patch.setattr(_sess._SAVE_EXECUTOR, "submit", reject_submission)
            with pytest.raises(RuntimeError, match="executor rejected"):
                await _sess.save_async(session)
        await _sess.save_async(session)

    asyncio.run(scenario())
    assert _jsonl(session.id) == [
        {"role": "user", "content": "retry-after-rejection"}]
    state = _sess._SAVE_STATES[session.id]
    assert state.pending == 0
    assert state.serving_ticket == state.next_ticket


def test_same_session_backlog_leaves_threads_for_other_sessions(monkeypatch):
    """Waiting saves of A cannot exhaust the pool and strand Session B."""
    first = _sess.create(name="backlog-a")
    second = _sess.create(name="backlog-b")
    entered = threading.Event()
    release = threading.Event()
    original_body = _sess._save_body

    def blocked_body(s, force_full=False):
        if s.id == first.id:
            entered.set()
            assert release.wait(3), "blocked Session was not released"
        return original_body(s, force_full=force_full)

    monkeypatch.setattr(_sess, "_save_body", blocked_body)
    with ThreadPoolExecutor(max_workers=2) as executor:
        monkeypatch.setattr(_sess, "_SAVE_EXECUTOR", executor, raising=False)

        async def scenario():
            # Also bound the legacy default executor, so this regression
            # exercises the old starvation rather than hiding it with spare threads.
            asyncio.get_running_loop().set_default_executor(executor)
            active = asyncio.create_task(_sess.save_async(first))
            assert await asyncio.to_thread(entered.wait, 2)
            backlog = [asyncio.create_task(_sess.save_async(first)) for _ in range(8)]
            other = asyncio.create_task(_sess.save_async(second))
            try:
                await asyncio.wait_for(asyncio.shield(other), timeout=1)
                assert not active.done()
                assert _sess.save_diagnostics(first.id)["queueDepth"] == 8
            finally:
                release.set()
                await asyncio.gather(active, other, *backlog, return_exceptions=True)

        asyncio.run(scenario())
    assert _sess.save_diagnostics(first.id)["queueDepth"] == 0


def test_queued_save_survives_repeated_cancellation(monkeypatch):
    """An awaiting task must keep its ticket until the queued write retires."""
    session = _sess.create(name="cancel-queued-twice")
    entered = threading.Event()
    release = threading.Event()
    original_body = _sess._save_body

    def blocked_body(s, force_full=False):
        entered.set()
        assert release.wait(3), "active writer was not released"
        return original_body(s, force_full=force_full)

    monkeypatch.setattr(_sess, "_save_body", blocked_body)

    async def scenario():
        active = asyncio.create_task(_sess.save_async(session))
        assert await asyncio.to_thread(entered.wait, 2)
        queued = asyncio.create_task(_sess.save_async(session))
        await asyncio.sleep(0)
        try:
            for _ in range(2):
                queued.cancel()
                await asyncio.sleep(0)
            assert not queued.done()
        finally:
            release.set()
            await active
            with pytest.raises(asyncio.CancelledError):
                await queued
        await _sess.save_async(session)

    asyncio.run(scenario())
    state = _sess._SAVE_STATES[session.id]
    assert state.serving_ticket == state.next_ticket
    assert state.async_jobs == {}


def test_async_writer_preserves_context_and_does_not_use_default_executor(monkeypatch):
    session = _sess.create(name="context-write")
    marker = contextvars.ContextVar("persistence_test_marker", default=None)
    seen = []
    original_body = _sess._save_body

    def capture(s, force_full=False):
        seen.append(marker.get())
        return original_body(s, force_full=force_full)

    monkeypatch.setattr(_sess, "_save_body", capture)

    async def scenario():
        marker.set("caller-context")
        with monkeypatch.context() as patch:
            def reject_default_executor(*args, **kwargs):
                raise AssertionError("save depends on the provider/default executor")
            patch.setattr(asyncio.get_running_loop(), "run_in_executor", reject_default_executor)
            await _sess.save_async(session)

    asyncio.run(scenario())
    assert seen == ["caller-context"]


def test_async_backlog_advances_while_event_loop_is_in_a_sync_save(monkeypatch):
    """The active writer, rather than an event-loop callback, starts the next save."""
    session = _sess.create(name="async-async-sync")
    entered = threading.Event()
    release = threading.Event()
    original_body = _sess._save_body

    def blocked_body(s, force_full=False):
        entered.set()
        assert release.wait(2), "writer was not released"
        return original_body(s, force_full=force_full)

    monkeypatch.setattr(_sess, "_save_body", blocked_body)
    state = _sess._SAVE_STATES[session.id]
    original_wait = state.condition.wait

    def bounded_wait(timeout=None):
        if not original_wait(timeout=2):
            raise TimeoutError("queued writer needed the blocked event loop")
        return True

    monkeypatch.setattr(state.condition, "wait", bounded_wait)

    async def scenario():
        first = asyncio.create_task(_sess.save_async(session))
        assert await asyncio.to_thread(entered.wait, 2)
        second = asyncio.create_task(_sess.save_async(session))
        await asyncio.sleep(0)
        timer = threading.Timer(0.05, release.set)
        timer.start()
        try:
            _sess.save(session)
        finally:
            release.set()
            timer.join()
            await asyncio.gather(first, second)

    asyncio.run(scenario())
    assert state.serving_ticket == state.next_ticket


def test_queued_submission_rejection_releases_following_tickets(monkeypatch):
    """Submission can fail when a writer thread advances a previously queued save."""
    session = _sess.create(name="reject-queued")
    entered = threading.Event()
    release = threading.Event()
    original_body = _sess._save_body

    def blocked_body(s, force_full=False):
        entered.set()
        assert release.wait(2), "first writer was not released"
        return original_body(s, force_full=force_full)

    monkeypatch.setattr(_sess, "_save_body", blocked_body)

    async def scenario():
        first = asyncio.create_task(_sess.save_async(session))
        assert await asyncio.to_thread(entered.wait, 2)
        with monkeypatch.context() as patch:
            def reject(*args, **kwargs):
                raise RuntimeError("queued submission rejected")

            patch.setattr(_sess._SAVE_EXECUTOR, "submit", reject)
            second = asyncio.create_task(_sess.save_async(session))
            third = asyncio.create_task(_sess.save_async(session))
            await asyncio.sleep(0)
            release.set()
            await first
            for rejected in (second, third):
                with pytest.raises(RuntimeError, match="queued submission rejected"):
                    await rejected
        await _sess.save_async(session)

    asyncio.run(scenario())
    state = _sess._SAVE_STATES[session.id]
    assert state.serving_ticket == state.next_ticket
    assert state.async_jobs == {}


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
