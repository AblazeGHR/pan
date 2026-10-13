"""Causal Send/Steer regressions with blocked I/O and an idle consumer."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from packages.core import session as sess, worker
from packages.core.adapters import CbcAdapter, CodexAdapter


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    worker.workers.clear()
    worker._queue_locks.clear()
    worker._spawn_locks.clear()
    worker._queue_retry_tasks.clear()
    worker.set_broadcaster(None)
    monkeypatch.setattr(worker, "_spawn_process", AsyncMock(
        side_effect=AssertionError("latency regression attempted a provider spawn")))
    yield
    worker.workers.clear()
    worker._queue_locks.clear()
    worker._spawn_locks.clear()
    worker._queue_retry_tasks.clear()


def live_session(adapter=None, *, status="idle"):
    s = sess.Session(id="ses-latency", name="latency")
    sess._cache[s.id] = s
    stdin = Mock()
    w = worker.Worker(worker_id="w-latency", session_id=s.id,
                      adapter=adapter or CbcAdapter(), status=status,
                      process=SimpleNamespace(returncode=None, stdin=stdin),
                      pending_signal=asyncio.Queue())
    worker.workers[w.worker_id] = w
    return s, w, stdin


def test_idle_consumer_wakes_when_edit_lease_expires_without_another_send(monkeypatch):
    async def run():
        s, w, _ = live_session()
        first = await worker.enqueue_user_message(s.id, "head", "head-client")
        second = await worker.enqueue_user_message(s.id, "tail", "tail-client")
        monkeypatch.setattr(worker, "_QUEUE_EDIT_LOCK_TTL_SECONDS", 0.08)
        lease, conflict = worker.acquire_queue_edit_lock(s, first["queueItemId"], "editor")
        assert not conflict
        consumed = []
        complete = asyncio.Event()

        async def deliver(current, session, unit):
            assert time.time() >= lease["expiresAt"]
            consumed.extend(item["id"] for item in unit)
            worker._remove_queue_items_by_identity(session, unit)
            worker._wake_after_queue_unit(current, session)
            if not session.queue_pending:
                complete.set()

        monkeypatch.setattr(worker, "_deliver_queue_unit", deliver)
        consumer = asyncio.create_task(worker._consumer(w))
        started = time.perf_counter()
        try:
            await asyncio.sleep(0.02)
            assert consumed == []  # The tail cannot pass the edit lease.
            await asyncio.wait_for(complete.wait(), 0.4)
            print(f"edit-expiry-to-fifo-ms={(time.perf_counter()-started)*1000:.1f}")
            assert consumed == [first["queueItemId"], second["queueItemId"]]
        finally:
            consumer.cancel()
            for task in worker._queue_retry_tasks.values():
                task.cancel()
            await asyncio.gather(consumer, *worker._queue_retry_tasks.values(),
                                 return_exceptions=True)

    asyncio.run(run())


def test_send_ack_does_not_wait_for_direct_steer_stdin_drain():
    async def run():
        adapter = CbcAdapter()
        adapter.supports_native_steer = True
        adapter.encode_control_message = lambda value: json.dumps(value).encode()
        s, w, stdin = live_session(adapter, status="running")
        entered, release = asyncio.Event(), asyncio.Event()

        async def drain():
            entered.set()
            await release.wait()

        stdin.drain = drain
        steer = asyncio.create_task(worker.steer_worker(w.worker_id, "steer"))
        enqueue = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            started = time.perf_counter()
            enqueue = asyncio.create_task(worker.enqueue_user_message(
                s.id, "append", "append-client"))
            result = await asyncio.wait_for(asyncio.shield(enqueue), 0.2)
            print(f"send-during-steer-drain-ms={(time.perf_counter()-started)*1000:.1f}")
            assert result["status"] == "queued"
            assert not release.is_set()
            assert not steer.done()
            receipt = s.queue_delivery_ledger[result["queueItemId"]]
            assert receipt["deliveryState"] == "queued"
            assert len(stdin.write.call_args_list) == 1
        finally:
            release.set()
            await asyncio.gather(steer, *([enqueue] if enqueue else []))

    asyncio.run(run())


def test_direct_steer_acceptance_receipt_completes_before_stdin_drain():
    async def run():
        s, w, stdin = live_session(CodexAdapter(), status="running")
        entered, release = asyncio.Event(), asyncio.Event()

        async def drain():
            entered.set()
            await release.wait()

        stdin.drain = drain
        steer = asyncio.create_task(worker.steer_worker(w.worker_id, "steer", "steer:latency"))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            control = json.loads(stdin.write.call_args.args[0])
            started = time.perf_counter()
            w._steer_receipts[control["requestId"]].set_result(
                {"ok": True, "outcome": "accepted"})
            result = await asyncio.wait_for(asyncio.shield(steer), 0.2)
            print(f"steer-receipt-to-ack-ms={(time.perf_counter()-started)*1000:.1f}")
            assert result is None
            assert not release.is_set()
            assert sum(row.get("clientMessageId") == "steer:latency" for row in s.history) == 1
            assert len(stdin.write.call_args_list) == 1
            assert not w._direct_steer_pending
        finally:
            release.set()
            await steer

    asyncio.run(run())


def test_cancelled_direct_steer_keeps_original_receipt_owner(monkeypatch):
    async def run():
        s, w, stdin = live_session(CodexAdapter(), status="running")
        written, persisted = asyncio.Event(), asyncio.Event()
        stdin.drain = AsyncMock(side_effect=lambda: written.set())

        async def broadcast(event):
            if event.get("type") == "queue.item_delivered":
                persisted.set()

        monkeypatch.setattr(worker, "_bcast", broadcast)
        steer = asyncio.create_task(worker.steer_worker(w.worker_id, "original", "steer:cancelled"))
        await asyncio.wait_for(written.wait(), 1)
        request = json.loads(stdin.write.call_args.args[0])["requestId"]
        steer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await steer
        assert w._direct_steer_pending
        assert "awaiting" in await worker.steer_worker(w.worker_id, "resend")
        w._steer_receipts[request].set_result({"ok": True, "outcome": "accepted"})
        await asyncio.wait_for(persisted.wait(), 0.2)
        assert sum(row.get("clientMessageId") == "steer:cancelled" for row in s.history) == 1
        assert await worker.steer_worker(w.worker_id, "original", "steer:cancelled") is None
        assert stdin.write.call_count == 1

    asyncio.run(run())


def test_unknown_direct_receipt_does_not_clear_resend_block():
    async def run():
        s, w, stdin = live_session(CodexAdapter(), status="running")

        async def drain():
            request = json.loads(stdin.write.call_args.args[0])["requestId"]
            w._steer_receipts[request].set_result({"ok": False, "outcome": "unknown"})

        stdin.drain = drain
        assert (await worker.steer_worker(w.worker_id, "uncertain")).startswith("Steer outcome unknown")
        assert "awaiting" in await worker.steer_worker(w.worker_id, "do not replay")
        assert stdin.write.call_count == 1
        assert s.history == []

    asyncio.run(run())


def test_direct_steer_dead_process_is_a_prewrite_rejection():
    async def run():
        _, w, stdin = live_session(CodexAdapter(), status="running")
        w.process.returncode = 1
        assert await worker.steer_worker(w.worker_id, "not written") == "Worker process is not running"
        assert not w._direct_steer_pending
        assert not w._steer_receipts
        stdin.write.assert_not_called()

    asyncio.run(run())


def test_renewed_edit_lease_still_blocks_fifo_until_new_expiry(monkeypatch):
    async def run():
        s, w, _ = live_session()
        first = await worker.enqueue_user_message(s.id, "head", "head-client")
        monkeypatch.setattr(worker, "_QUEUE_EDIT_LOCK_TTL_SECONDS", 0.06)
        worker.acquire_queue_edit_lock(s, first["queueItemId"], "editor")
        selected, complete = [], asyncio.Event()
        renewed = None

        async def deliver(current, session, unit):
            assert renewed is not None and time.time() >= renewed["expiresAt"]
            selected.extend(it["id"] for it in unit)
            worker._remove_queue_items_by_identity(session, unit)
            complete.set()

        monkeypatch.setattr(worker, "_deliver_queue_unit", deliver)
        consumer = asyncio.create_task(worker._consumer(w))
        try:
            await asyncio.sleep(0.02)
            monkeypatch.setattr(worker, "_QUEUE_EDIT_LOCK_TTL_SECONDS", 0.16)
            renewed, conflict = worker.acquire_queue_edit_lock(s, first["queueItemId"], "editor")
            assert not conflict
            await asyncio.wait_for(complete.wait(), 0.4)
            assert selected == [first["queueItemId"]]
        finally:
            consumer.cancel()
            for task in worker._queue_retry_tasks.values():
                task.cancel()
            await asyncio.gather(consumer, *worker._queue_retry_tasks.values(), return_exceptions=True)

    asyncio.run(run())
