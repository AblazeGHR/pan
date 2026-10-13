"""Original-ID own-queue gates, tickets and real provider handoff contracts."""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from packages.core import session as sess, worker, self_queue
from packages.core.adapters import CodexAdapter, CbcAdapter
from packages.mcp import server as mcp_server


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    sess._cache.clear()
    worker.workers.clear()
    worker._queue_locks.clear()
    worker._spawn_locks.clear()
    worker.set_broadcaster(None)
    monkeypatch.setattr(worker, "_spawn_process", AsyncMock(
        side_effect=AssertionError("unit test attempted a real provider spawn")))
    yield
    sess._cache.clear()
    worker.workers.clear()


def setup():
    s = sess.Session(id="ses-own", name="own", adapter="codex")
    sess._cache[s.id] = s
    item = {"id": "original", "queueItemId": "original", "type": "task",
            "source": "agent", "sourceSessionId": "ses-origin", "taskId": "T-one",
            "text": "complete text " * 100, "deliveryState": "queued", "revision": 1,
            "messageId": "msg-origin", "envelope": {"identity": "preserved"},
            "parts": [{"type": "text", "text": "complete text " * 100}]}
    s.queue_pending = [item]
    return s, item


@pytest.mark.parametrize("field", ["queueLockManual", "queueLockAutoReport", "queueSteerPending",
                                   "selfConsumeUncertain", "pause", "edit"])
def test_hidden_guessed_stale_ids_and_restore(field):
    async def run():
        s, item = setup()
        assert (await self_queue.read(s.id))["items"][0]["text"] == item["text"]
        if field == "pause":
            s.queue_paused = True
        elif field == "edit":
            worker.acquire_queue_edit_lock(s, item["id"], "lease")
        else:
            item[field] = True
        assert await self_queue.read(s.id) == {"ok": True, "items": []}
        assert await self_queue.consume(s.id, item["id"]) == await self_queue.consume(s.id, "guessed")
        s.queue_paused = False
        s.queue_edit_locks.clear()
        item.pop(field, None)
        assert len((await self_queue.read(s.id))["items"]) == 1
    asyncio.run(run())


def test_stop_reports_uses_actual_auto_locks_not_future_rule(monkeypatch):
    from packages.web import server
    async def run():
        s, item = setup()
        report = {"id": "report", "type": "report", "source": "report", "result": "R" * 900,
                  "deliveryState": "queued"}
        channel = {"id": "qq", "type": "qq", "text": "channel full text", "deliveryState": "queued"}
        s.queue_pending += [report, channel]
        monkeypatch.setattr(worker, "_wake_worker", AsyncMock())
        await server.api_session_queue_reports_paused(s.id, {"paused": True})
        visible = (await self_queue.read(s.id))["items"]
        assert [it["id"] for it in visible] == [item["id"], "qq"]
        await server.api_session_queue_item_lock(s.id, "report", {"locked": False})
        visible = (await self_queue.read(s.id))["items"]
        assert s.agent_reports_paused
        assert "R" * 900 in visible[1]["content"]
        await server.api_session_queue_locks(s.id, {"locked": True})
        assert (await self_queue.read(s.id))["items"] == []
    asyncio.run(run())


@pytest.mark.parametrize("gate", ["pause", "lock", "edit"])
def test_final_handoff_rechecks_after_reservation(gate):
    async def run():
        s, item = setup()
        item["deliveryState"] = "writing"
        if gate == "pause":
            s.queue_paused = True
        elif gate == "lock":
            item["queueLockManual"] = True
        else:
            worker.acquire_queue_edit_lock(s, item["id"], "lease")
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CbcAdapter(), status="running")
        w._current_queue_item = item
        operation = AsyncMock()
        with pytest.raises(worker._QueuePausedBeforeHandoff):
            await worker._queue_handoff(w, s, operation, None)
        operation.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("receipt", [None, "provider rejected", "Steer outcome unknown: missing receipt"])
def test_actual_native_chain_metadata_and_duplicate(receipt):
    async def run():
        s, item = setup()
        original = copy.deepcopy(item)
        stdin = Mock()
        proc = SimpleNamespace(returncode=None, stdin=stdin)
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CodexAdapter(),
                          status="running", process=proc)
        worker.workers[w.worker_id] = w
        async def drain():
            wire = json.loads(stdin.write.call_args.args[0])
            assert wire["text"] == item["text"]
            w._steer_receipts[wire["requestId"]].set_result(receipt)
        stdin.drain = drain
        result = await self_queue.consume(s.id, item["id"])
        assert result["ok"] is (receipt is None)
        if receipt is None:
            assert not s.queue_pending
            ledger = s.queue_delivery_ledger[item["id"]]
            for field in ["source", "taskId", "sourceSessionId", "messageId", "envelope", "parts"]:
                assert ledger[field] == original[field]
            assert (await self_queue.consume(s.id, item["id"]))["alreadyConsumed"]
            assert stdin.write.call_count == 1
        elif receipt.startswith("Steer outcome unknown"):
            assert (await self_queue.read(s.id))["items"] == []
            assert await self_queue.consume(s.id, item["id"]) == self_queue.UNAVAILABLE
            # An authoritative late receipt reconciles the exact old request.
            await worker._settle_queue_steer(w, s, item, item["text"], None,
                                            item["queueSteerRequestId"], item["queueSteerGeneration"])
            assert not s.queue_pending
        else:
            assert not item.get("queueSteerPending")
            assert item in s.queue_pending
    asyncio.run(run())


def test_priority_intent_is_not_consumption_and_duplicate_does_not_restart(monkeypatch):
    async def run():
        s, item = setup()
        earlier = {**item, "id": "earlier", "queueItemId": "earlier"}
        s.queue_pending.insert(0, earlier)
        restart = AsyncMock(return_value=object())
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        monkeypatch.setattr(worker, "create_worker", restart)
        result = await self_queue.consume(s.id, item["id"])
        assert result == {"ok": False, "status": "pending", "retryable": False}
        assert s.queue_pending == [item, earlier]
        assert await self_queue.consume(s.id, item["id"]) == result
        assert restart.await_count == 1
        worker._set_delivery_state(s, item, worker._DELIVERY_SENT)
        worker._remove_queue_items_by_identity(s, [item])
        assert (await self_queue.consume(s.id, item["id"]))["ok"]
        assert restart.await_count == 1
    asyncio.run(run())


def test_failed_ticket_rolls_back_before_later_writer(monkeypatch):
    async def run():
        s, item = setup()
        before = copy.deepcopy(s.queue_pending)
        observed = []
        real_save = sess._save_body
        def fail(candidate, **kwargs):
            if item.get("selfConsumeRequested"):
                observed.append(True)
                raise OSError("disk failure")
            observed.append(False)
            return real_save(candidate, **kwargs)
        monkeypatch.setattr(sess, "_save_body", fail)
        restart = AsyncMock()
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        monkeypatch.setattr(worker, "create_worker", restart)
        result = await self_queue.consume(s.id, item["id"])
        await sess.save_async(s)
        assert result["error"] == "persist_failed"
        assert observed == [True, False]
        assert s.queue_pending == before
        restart.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("fail", [False, True])
def test_cancelled_request_keeps_server_owner_and_ticket_outcome(monkeypatch, fail):
    async def run():
        s, item = setup()
        entered = threading.Event()
        release = threading.Event()
        real_save = sess._save_body
        def save(candidate, **kwargs):
            if item.get("selfConsumeRequested"):
                entered.set()
                assert release.wait(5)
                if fail:
                    raise OSError("failed while requester cancelled")
            return real_save(candidate, **kwargs)
        monkeypatch.setattr(sess, "_save_body", save)
        restart = AsyncMock(return_value=object())
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        monkeypatch.setattr(worker, "create_worker", restart)
        request = asyncio.create_task(self_queue.consume(s.id, item["id"]))
        assert await asyncio.to_thread(entered.wait, 5)
        owner = self_queue._attempts[(s.id, item["id"])]
        request.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        await owner
        assert bool(item.get("selfConsumeRequested")) is (not fail)
        assert restart.await_count == (0 if fail else 1)
    asyncio.run(run())


def test_unknown_normal_handoff_is_not_automatically_replayed():
    s, item = setup()
    item.update(selfConsumeRequested=True, selfConsumeAttempted=True, deliveryState="reserved")
    worker._migrate_queue_delivery_state(s)
    assert item["selfConsumeUncertain"]
    assert worker._select_queue_unit(s) is None
    assert item in s.queue_pending


def test_mcp_no_arbitrary_identity_or_remote_identity_lending(monkeypatch):
    import inspect
    assert list(inspect.signature(mcp_server.self_queue_read).parameters) == []
    assert list(inspect.signature(mcp_server.self_queue_consume).parameters) == ["item_id"]
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses-own")
    monkeypatch.setattr(mcp_server, "_terminal_transport", "sse")
    assert mcp_server.self_queue_read()["error"] == "caller_unavailable"
    assert mcp_server.self_queue_consume("guessed")["error"] == "caller_unavailable"


def test_native_waiting_for_spawn_lock_cannot_bypass_new_item_lock():
    async def run():
        s, item = setup()
        stdin = Mock(drain=AsyncMock())
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CodexAdapter(),
                          status="running", process=SimpleNamespace(returncode=None, stdin=stdin))
        worker.workers[w.worker_id] = w
        lock = await worker._session_spawn_lock(s.id)
        await lock.acquire()
        request = asyncio.create_task(self_queue.consume(s.id, item["id"]))
        # The owner is queued behind the existing lifecycle lock.
        for _ in range(10):
            await asyncio.sleep(0)
        item["queueLockManual"] = True
        lock.release()
        assert await request == self_queue.UNAVAILABLE
        stdin.write.assert_not_called()
    asyncio.run(run())


def test_generation_fence_does_not_restart_new_worker(monkeypatch):
    async def run():
        s, _ = setup()
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CbcAdapter(),
                          status="idle", process=SimpleNamespace(returncode=None))
        w.generation = 2
        worker.workers[w.worker_id] = w
        restart = AsyncMock()
        monkeypatch.setattr(worker, "_restart_worker_unlocked", restart)
        assert await worker.restart_or_start_worker(s.id, expected_worker_id="w", expected_generation=1) is w
        restart.assert_not_called()
    asyncio.run(run())


def test_concurrent_duplicates_share_one_server_attempt(monkeypatch):
    async def run():
        s, item = setup()
        entered, release = asyncio.Event(), asyncio.Event()
        async def restart(*args, **kwargs):
            entered.set()
            await release.wait()
            return object()
        create = AsyncMock(side_effect=restart)
        monkeypatch.setattr(worker, "create_worker", create)
        first = asyncio.create_task(self_queue.consume(s.id, item["id"]))
        await entered.wait()
        second = asyncio.create_task(self_queue.consume(s.id, item["id"]))
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(first, second)
        assert results[0] == results[1]
        assert create.await_count == 1
    asyncio.run(run())


def test_before_and_after_writers_never_save_failed_candidate(monkeypatch):
    async def run():
        s, item = setup()
        entered, release = threading.Event(), threading.Event()
        captured = []
        def preceding():
            captured.append(bool(item.get("selfConsumeRequested")))
            entered.set()
            assert release.wait(5)
        before = asyncio.create_task(sess._persist_async_outcome(s.id, preceding))
        assert await asyncio.to_thread(entered.wait, 5)
        def fail(candidate, **kwargs):
            if item.get("selfConsumeRequested"):
                raise OSError("disk failed")
            captured.append(False)
        monkeypatch.setattr(sess, "_save_body", fail)
        request = asyncio.create_task(self_queue.consume(s.id, item["id"]))
        for _ in range(10):
            await asyncio.sleep(0)
        after = asyncio.create_task(sess.save_async(s))
        release.set()
        await before
        assert (await request)["error"] == "persist_failed"
        await after
        assert captured == [False, False]
        assert not item.get("selfConsumeRequested")
    asyncio.run(run())


def test_native_reservation_failure_does_not_leave_failed_candidate(monkeypatch):
    async def run():
        s, item = setup()
        original = copy.deepcopy(item)
        stdin = Mock(drain=AsyncMock())
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CodexAdapter(), status="running",
                          process=SimpleNamespace(returncode=None, stdin=stdin))
        worker.workers[w.worker_id] = w
        real_save = sess._save_body
        def fail(candidate, **kwargs):
            if item.get("queueSteerPending"):
                raise OSError("reservation failed")
            real_save(candidate, **kwargs)
        monkeypatch.setattr(sess, "_save_body", fail)
        with pytest.raises(OSError, match="reservation failed"):
            await self_queue.consume(s.id, item["id"])
        assert item == original
        stdin.write.assert_not_called()
        await sess.save_async(s)
        persisted = json.loads((sess.SESSION_DIR / f"{s.id}.json").read_text(encoding="utf-8"))
        assert not persisted["queue_pending"][0].get("queueSteerPending")
    asyncio.run(run())


@pytest.mark.parametrize("fail", [False, True])
def test_cancelled_ticket_owner_uses_durable_outcome(monkeypatch, fail):
    async def run():
        s, item = setup()
        entered, release = threading.Event(), threading.Event()
        real_save = sess._save_body
        def save(candidate, **kwargs):
            entered.set()
            assert release.wait(5)
            if fail:
                raise OSError("cancelled writer failed")
            real_save(candidate, **kwargs)
        monkeypatch.setattr(sess, "_save_body", save)
        create = AsyncMock(return_value=object())
        monkeypatch.setattr(worker, "create_worker", create)
        owner = asyncio.create_task(self_queue._consume(s.id, item["id"]))
        assert await asyncio.to_thread(entered.wait, 5)
        owner.cancel()
        release.set()
        if fail:
            with pytest.raises(asyncio.CancelledError):
                await owner
            assert not item.get("selfConsumeRequested")
            create.assert_not_called()
        else:
            assert (await owner)["status"] == "pending"
            persisted = json.loads((sess.SESSION_DIR / f"{s.id}.json").read_text(encoding="utf-8"))
            assert persisted["queue_pending"][0]["selfConsumeRequested"]
            assert create.await_count == 1
    asyncio.run(run())


def test_selected_stream_write_failure_remains_unknown_without_retry(monkeypatch):
    async def run():
        s, item = setup()
        item["selfConsumeRequested"] = True
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CbcAdapter(), status="idle",
                          process=SimpleNamespace(returncode=None), pending_signal=asyncio.Queue())
        worker.workers[w.worker_id] = w
        operation = AsyncMock(side_effect=BrokenPipeError("write uncertain"))
        async def consume(w, text, source, s, *, on_handoff):
            await worker._queue_handoff(w, s, operation, on_handoff)
        monkeypatch.setattr(worker, "_consumer_stream", consume)
        await worker._deliver_queue_unit(w, s, [item])
        assert item["selfConsumeUncertain"] and item in s.queue_pending
        assert worker._select_queue_unit(s) is None
        assert await self_queue.read(s.id) == {"ok": True, "items": []}
        assert await self_queue.consume(s.id, item["id"]) == self_queue.UNAVAILABLE
        assert operation.await_count == 1
        persisted = json.loads((sess.SESSION_DIR / f"{s.id}.json").read_text(encoding="utf-8"))
        assert persisted["queue_pending"][0]["selfConsumeUncertain"]
    asyncio.run(run())


def test_hidden_unknown_intent_does_not_change_visible_request_response(monkeypatch):
    async def run():
        s, item = setup()
        hidden = {**item, "id": "hidden", "queueItemId": "hidden", "queueLockManual": True,
                  "selfConsumeRequested": True, "selfConsumeUncertain": True}
        s.queue_pending.append(hidden)
        create = AsyncMock(return_value=object())
        monkeypatch.setattr(worker, "create_worker", create)
        assert (await self_queue.consume(s.id, item["id"]))["status"] == "pending"
        assert create.await_count == 1
    asyncio.run(run())


def test_native_other_row_edit_lease_hides_actual_blocked_rows():
    async def run():
        s, item = setup()
        other = {**item, "id": "edited", "queueItemId": "edited"}
        s.queue_pending.append(other)
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CodexAdapter(), status="running",
                          process=SimpleNamespace(returncode=None))
        worker.workers[w.worker_id] = w
        worker.acquire_queue_edit_lock(s, other["id"], "lease")
        assert await self_queue.read(s.id) == {"ok": True, "items": []}
        assert await self_queue.consume(s.id, item["id"]) == await self_queue.consume(s.id, "guessed")
    asyncio.run(run())


def test_http_bridge_reuses_local_identity_gate_and_rejects_session_arguments():
    import httpx
    from packages.web.server import app
    async def run():
        s, item = setup()
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://local") as client:
            bound = {"X-Pan-Queue-Caller": s.id}
            assert (await client.post("/api/self-queue/read", headers=bound)).json()["ok"]
            for headers in [{}, {**bound, "Origin": "https://browser"},
                            {**bound, "Sec-Fetch-Site": "same-origin"}, {"X-Pan-Queue-Caller": "ses-missing"}]:
                result = (await client.post("/api/self-queue/read", headers=headers)).json()
                assert result == {"ok": False, "error": "caller_unavailable"}
            result = (await client.post("/api/self-queue/consume", headers=bound,
                      json={"itemId": item["id"], "sessionId": "ses-other"})).json()
            assert result["error"] == "invalid_request"
        foreign = httpx.ASGITransport(app=app, client=("192.0.2.1", 12345))
        async with httpx.AsyncClient(transport=foreign, base_url="http://local") as client:
            assert (await client.post("/api/self-queue/read", headers=bound)).json()["error"] == "caller_unavailable"
    asyncio.run(run())
