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


class FutureSteerAdapter:
    """A new adapter needs only the existing capability/control contract."""
    name = "future-steer-provider"
    execution_modes = ["stream"]
    supports_native_steer = True
    supports_steer_receipt = True

    def encode_control_message(self, control):
        return json.dumps(control).encode()


@pytest.mark.parametrize("disposition", ["accepted", "rejected", "unknown"])
def test_future_adapter_capability_routes_self_consume_to_steer_without_restart(monkeypatch, disposition):
    async def run():
        s, item = setup()
        s.adapter = FutureSteerAdapter.name
        stdin = Mock(drain=AsyncMock())
        w = worker.Worker(worker_id="future-worker", session_id=s.id, adapter=FutureSteerAdapter(),
                          status="running", process=SimpleNamespace(returncode=None, stdin=stdin))
        worker.workers[w.worker_id] = w
        restart = AsyncMock(side_effect=AssertionError("Steer must not fall back to restart"))
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        monkeypatch.setattr(worker, "create_worker", restart)
        def write(data):
            control = json.loads(data)
            assert control["type"] == "steer" and control["text"] == item["text"]
            worker._route_steer_receipt(w, {"type": "pan.steer_receipt", "requestId": control["requestId"],
                "ok": disposition == "accepted", "outcome": disposition,
                "error": None if disposition == "accepted" else "fixture disposition"})
        stdin.write.side_effect = write
        result = await self_queue.consume(s.id, item["id"])
        assert result["ok"] is (disposition == "accepted")
        assert stdin.write.call_count == 1
        restart.assert_not_called()
        if disposition == "accepted":
            assert s.queue_delivery_ledger[item["id"]]["deliveryState"] == "sent_to_cli"
            assert (await self_queue.consume(s.id, item["id"]))["alreadyConsumed"]
        elif disposition == "rejected":
            assert result["status"] == "rejected" and item in s.queue_pending
        else:
            assert item["queueSteerPending"]
            assert await self_queue.consume(s.id, item["id"]) == self_queue.UNAVAILABLE
    asyncio.run(run())


@pytest.mark.parametrize("status", ["idle", "queued"])
def test_future_steer_adapter_without_active_run_prioritizes_original_consumer_item(monkeypatch, status):
    async def run():
        s, item = setup(); s.adapter = FutureSteerAdapter.name
        earlier = {**item, "id": "earlier", "queueItemId": "earlier"}
        s.queue_pending.insert(0, earlier)
        w = worker.Worker(worker_id="future-worker", session_id=s.id, adapter=FutureSteerAdapter(),
                          status=status, process=SimpleNamespace(returncode=None), pending_signal=asyncio.Queue())
        worker.workers[w.worker_id] = w
        restart = AsyncMock(side_effect=AssertionError("idle capable adapter uses consumer"))
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        steer = AsyncMock(side_effect=AssertionError("no active run to Steer"))
        monkeypatch.setattr(worker, "steer_queue_item", steer)
        wake = AsyncMock()
        monkeypatch.setattr(worker, "_wake_worker", wake)
        assert (await self_queue.consume(s.id, item["id"]))["status"] == "pending"
        assert s.queue_pending == [item, earlier] and item["selfConsumeRequested"]
        wake.assert_awaited_once_with(s.id)
        restart.assert_not_called(); steer.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("gate", ["queueLockManual", "queueLockAutoReport", "pause", "edit"])
def test_future_steer_adapter_hidden_queue_items_never_dispatch(monkeypatch, gate):
    async def run():
        s, item = setup(); s.adapter = FutureSteerAdapter.name
        w = worker.Worker(worker_id="future-worker", session_id=s.id, adapter=FutureSteerAdapter(),
                          status="running", process=SimpleNamespace(returncode=None))
        worker.workers[w.worker_id] = w
        if gate == "pause": s.queue_paused = True
        elif gate == "edit": worker.acquire_queue_edit_lock(s, item["id"], "editor")
        else: item[gate] = True
        dispatch = AsyncMock(side_effect=AssertionError("hidden queue item dispatched"))
        monkeypatch.setattr(worker, "steer_queue_item", dispatch)
        monkeypatch.setattr(worker, "restart_or_start_worker", dispatch)
        monkeypatch.setattr(worker, "create_worker", dispatch)
        assert await self_queue.read(s.id) == {"ok": True, "items": []}
        assert await self_queue.consume(s.id, item["id"]) == self_queue.UNAVAILABLE
        dispatch.assert_not_called()
    asyncio.run(run())


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


@pytest.mark.parametrize("receipt", [None,
    pytest.param({"ok": False, "outcome": "rejected", "error": "provider rejected"}, id="provider rejected"),
    pytest.param({"ok": False, "outcome": "unknown", "error": "missing receipt"}, id="Steer outcome unknown: missing receipt")])
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
        elif receipt["outcome"] == "unknown":
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
        assert await self_queue.consume(s.id, item["id"]) == {
            "ok": False, "status": "rejected", "retryable": True}
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


def test_native_explicit_rejection_allows_original_id_retry_even_with_unknown_words():
    async def run():
        s, item = setup()
        stdin = Mock()
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CodexAdapter(), status="running",
                          process=SimpleNamespace(returncode=None, stdin=stdin))
        worker.workers[w.worker_id] = w
        async def drain():
            wire = json.loads(stdin.write.call_args.args[0])
            result = ({"ok": False, "outcome": "rejected", "error": "Steer outcome unknown: provider's literal rejection"}
                      if stdin.write.call_count == 1 else {"ok": True, "outcome": "accepted"})
            w._steer_receipts[wire["requestId"]].set_result(result)
        stdin.drain = drain
        rejected = await self_queue.consume(s.id, item["id"])
        assert rejected == {"ok": False, "status": "rejected", "retryable": True}
        disk = json.loads((sess.SESSION_DIR / f"{s.id}.json").read_text(encoding="utf-8"))
        assert disk["queue_pending"][0]["id"] == item["id"]
        assert not disk["queue_pending"][0].get("queueSteerPending")
        assert (await self_queue.consume(s.id, item["id"]))["status"] == "consumed"
        assert stdin.write.call_count == 2
        disk = json.loads((sess.SESSION_DIR / f"{s.id}.json").read_text(encoding="utf-8"))
        assert disk["queue_delivery_ledger"][item["id"]]["deliveryState"] == "sent_to_cli"
    asyncio.run(run())


def test_native_structured_unknown_stays_blocked_without_magic_error_prefix():
    async def run():
        s, item = setup()
        stdin = Mock()
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CodexAdapter(), status="running",
                          process=SimpleNamespace(returncode=None, stdin=stdin))
        worker.workers[w.worker_id] = w
        async def drain():
            wire = json.loads(stdin.write.call_args.args[0])
            w._steer_receipts[wire["requestId"]].set_result({"ok": False, "outcome": "unknown", "error": "socket gone"})
        stdin.drain = drain
        assert await self_queue.consume(s.id, item["id"]) == self_queue.UNAVAILABLE
        disk = json.loads((sess.SESSION_DIR / f"{s.id}.json").read_text(encoding="utf-8"))
        assert disk["queue_pending"][0]["queueSteerPending"]
        assert await self_queue.consume(s.id, item["id"]) == await self_queue.consume(s.id, "guessed")
        assert stdin.write.call_count == 1
    asyncio.run(run())


def test_native_prewrite_encoding_failure_is_retryable(monkeypatch):
    async def run():
        s, item = setup()
        stdin = Mock(drain=AsyncMock())
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CodexAdapter(), status="running",
                          process=SimpleNamespace(returncode=None, stdin=stdin))
        worker.workers[w.worker_id] = w
        monkeypatch.setattr(w.adapter, "encode_control_message", Mock(side_effect=ValueError("bad encoding")))
        assert await self_queue.consume(s.id, item["id"]) == {"ok": False, "status": "rejected", "retryable": True}
        stdin.write.assert_not_called()
        assert not item.get("queueSteerPending")
    asyncio.run(run())


def test_native_capable_idle_worker_uses_normal_handoff_without_restart(monkeypatch):
    async def run():
        s, item = setup()
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CodexAdapter(), status="idle",
                          process=SimpleNamespace(returncode=None), pending_signal=asyncio.Queue())
        worker.workers[w.worker_id] = w
        restart = AsyncMock(return_value=w)
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        assert (await self_queue.consume(s.id, item["id"]))["status"] == "pending"
        restart.assert_not_called()
        assert w.pending_signal.qsize() == 1
        async def write(w, text, source, s, *, on_handoff):
            await worker._queue_handoff(w, s, AsyncMock(), on_handoff)
        monkeypatch.setattr(worker, "_consumer_stream", write)
        await worker._deliver_queue_unit(w, s, worker._select_queue_unit(s))
        assert (await self_queue.consume(s.id, item["id"]))["alreadyConsumed"]
        assert w.generation == 0
    asyncio.run(run())


@pytest.mark.parametrize("fail", [False, True])
def test_native_real_ticket_cancel_then_disk_reload_and_original_id_retry(monkeypatch, fail):
    async def run():
        s, item = setup()
        await sess.save_async(s)
        path = sess.SESSION_DIR / f"{s.id}.json"
        entered, release = threading.Event(), threading.Event()
        real_save = sess._save_body
        attempts = [0]
        def save(candidate, **kwargs):
            if item.get("queueSteerPending") and attempts[0] == 0:
                attempts[0] += 1
                entered.set()
                assert release.wait(5)
                if fail:
                    raise OSError("cancelled native candidate failed")
            real_save(candidate, **kwargs)
        monkeypatch.setattr(sess, "_save_body", save)
        stdin = Mock()
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CodexAdapter(), status="running",
                          process=SimpleNamespace(returncode=None, stdin=stdin))
        worker.workers[w.worker_id] = w
        async def drain():
            wire = json.loads(stdin.write.call_args.args[0])
            w._steer_receipts[wire["requestId"]].set_result({"ok": True, "outcome": "accepted"})
        stdin.drain = drain
        request = asyncio.create_task(self_queue.consume(s.id, item["id"]))
        assert await asyncio.to_thread(entered.wait, 5)
        owner = worker._queue_steer_tasks[(s.id, item["id"])]
        owner.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        stdin.write.assert_not_called()
        disk = json.loads(path.read_text(encoding="utf-8"))
        restored = disk["queue_pending"][0]
        assert restored["id"] == item["id"] and restored["text"] == item["text"]
        assert not restored.get("queueSteerPending")
        assert restored["parts"] == item["parts"]
        assert restored["envelope"] == item["envelope"]
        assert (await self_queue.consume(s.id, item["id"]))["status"] == "consumed"
        assert stdin.write.call_count == 1
        assert json.loads(path.read_text(encoding="utf-8"))["queue_delivery_ledger"][item["id"]]["deliveryState"] == "sent_to_cli"
    asyncio.run(run())


@pytest.mark.parametrize("provider_error", [None, {"message": "Steer outcome unknown: this is an explicit RPC rejection"}])
def test_wrapper_classifies_rpc_response_without_parsing_error_text(monkeypatch, provider_error):
    from packages.core.adapters.codex import app_server_wrapper
    app = app_server_wrapper.AppServer("node", "codex.js", "C:/fake", [])
    emitted = []
    monkeypatch.setattr(app_server_wrapper, "_write_stdout", emitted.append)
    app._steer_requests[7] = "original-request"
    message = {"id": 7, "result": {}}
    if provider_error is not None:
        message["error"] = provider_error
    assert app._emit_steer_receipt(message)
    assert emitted[0]["outcome"] == ("accepted" if provider_error is None else "rejected")
    assert emitted[0]["requestId"] == "original-request"


def test_wrapper_rpc_write_failure_is_structured_unknown(monkeypatch):
    from queue import Queue
    from packages.core.adapters.codex import app_server_wrapper
    app = app_server_wrapper.AppServer("node", "codex.js", "C:/fake", [])
    emitted = []
    monkeypatch.setattr(app_server_wrapper, "_write_stdout", emitted.append)
    monkeypatch.setattr(app_server_wrapper, "_write_stderr", lambda text: None)
    monkeypatch.setattr(app, "_request", Mock(side_effect=OSError("transport failure")))
    controls = Queue()
    controls.put({"type": "steer", "requestId": "request", "text": "original"})
    app._drain_controls({"turn_id": "turn", "done": False}, controls)
    assert emitted[0]["ok"] is False and emitted[0]["outcome"] == "unknown"


def test_idle_native_existing_delayed_restart_keeps_priority(monkeypatch):
    async def run():
        s, item = setup()
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CodexAdapter(), status="idle",
                          process=SimpleNamespace(returncode=None), pending_signal=asyncio.Queue())
        w.pending_restart = True
        worker.workers[w.worker_id] = w
        restart = AsyncMock()
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        assert (await self_queue.consume(s.id, item["id"]))["status"] == "pending"
        restart.assert_not_called()
        assert w.pending_restart
        assert not await worker._reserve_queue_unit(w, s, [item], item["text"])
        assert item["deliveryState"] == "queued"
    asyncio.run(run())


def test_real_worker_stdout_preserves_structured_rejection(monkeypatch):
    async def run():
        s, _ = setup()
        reader = asyncio.StreamReader()
        w = worker.Worker(worker_id="w", session_id=s.id, adapter=CodexAdapter(), status="running",
                          process=SimpleNamespace(returncode=None, stdout=reader))
        worker.workers[w.worker_id] = w
        receipt = asyncio.get_running_loop().create_future()
        w._steer_receipts["request"] = receipt
        event = {"type": "pan.steer_receipt", "requestId": "request", "ok": False,
                 "outcome": "rejected", "error": "Steer outcome unknown: literal rejected detail"}
        reader.feed_data(json.dumps(event).encode() + b"\n")
        task = asyncio.create_task(worker._read_stdout(w))
        try:
            assert await asyncio.wait_for(asyncio.shield(receipt), 3) == event
            assert worker._steer_receipt_result(receipt.result())[0] == "rejected"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
