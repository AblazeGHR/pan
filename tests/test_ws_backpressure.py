"""T-062.4 bounded WebSocket sender and delta coalescing tests."""

import asyncio
import time

import packages.web.server as srv


class _GateWS:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.sent: list[dict] = []
        self.closed: list[tuple[int | None, str | None]] = []

    async def send_json(self, data: dict):
        self.started.set()
        await self.release.wait()
        self.sent.append(data)

    async def close(self, code=None, reason=None):
        self.closed.append((code, reason))


class _FastWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed: list[tuple[int | None, str | None]] = []

    async def send_json(self, data: dict):
        self.sent.append(data)

    async def close(self, code=None, reason=None):
        self.closed.append((code, reason))


def _reset():
    for client in tuple(srv._ws_outbound.values()):
        if client._sender_task is not None:
            client._sender_task.cancel()
    srv._ws_outbound.clear()
    srv.ws_clients.clear()
    srv.agent_clients.clear()
    srv.agent_subscriptions.clear()


async def _drain_until(predicate, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("sender did not drain before timeout")
        await asyncio.sleep(0)


def _delta(text: str, cumulative: str, *, session="s1", worker="w1", task=1, item="item-1"):
    return {
        "type": "worker.stream",
        "sessionId": session,
        "workerId": worker,
        "generation": 3,
        "taskSeq": task,
        "event": {
            "type": "content.part",
            "role": "assistant",
            "delta": True,
            "item_id": item,
            "stream_text": cumulative,
            "part": {"type": "text", "text": text},
        },
    }


def test_slow_client_does_not_block_worker_broadcast_and_coalesces_final_text():
    async def scenario():
        _reset()
        ws = _GateWS()
        srv.ws_clients.add(ws)

        started = time.monotonic()
        await srv.broadcast(_delta("a", "a"))
        elapsed = time.monotonic() - started
        await ws.started.wait()
        assert elapsed < 0.1, f"broadcast waited for socket: {elapsed:.3f}s"

        await srv.broadcast(_delta("b", "ab"))
        await srv.broadcast(_delta("c", "abc"))
        client = srv._ws_outbound[ws]
        assert client.queue_depth == 1
        assert client.coalesced_deltas == 1

        ws.release.set()
        await _drain_until(lambda: len(ws.sent) == 2)
        assert ws.sent[0]["event"]["part"]["text"] == "a"
        merged = ws.sent[1]
        assert merged["event"]["part"]["text"] == "abc"
        assert merged["event"]["replace"] is True
        assert merged["event"]["stream_text"] == "abc"

        await client.close_now()
        _reset()

    asyncio.run(scenario())


def test_control_event_gets_priority_and_overflow_is_explicit_resync():
    async def scenario():
        _reset()
        old_max = srv._WS_OUTBOUND_QUEUE_MAX
        srv._WS_OUTBOUND_QUEUE_MAX = 4
        try:
            ws = _GateWS()
            srv.ws_clients.add(ws)
            await srv.broadcast(_delta("a", "a"))
            await ws.started.wait()
            await srv.broadcast(_delta("b", "ab"))
            await srv.broadcast(_delta("c", "ac", item="item-2"))
            await srv.broadcast({
                "type": "worker.status", "sessionId": "s1", "workerId": "w1",
                "generation": 3, "status": "idle", "taskSeq": 1,
            })
            await srv.broadcast({
                "type": "queue.snapshot", "sessionId": "s1", "queueRevision": 7,
                "items": [],
            })
            result = {
                "type": "worker.result", "sessionId": "s1", "workerId": "w1",
                "generation": 3, "status": "done", "taskSeq": 1, "result": "abc",
            }
            await srv.broadcast(result)
            client = srv._ws_outbound[ws]
            assert client.resync_required is True
            # The incoming result is retained after queued delta eviction;
            # the control snapshot and marker make the close/resync boundary
            # explicit instead of silently treating a partial stream as good.
            queued_types = [item.data["type"] for item in client._queue]
            assert queued_types == [
                "worker.status", "queue.snapshot", "worker.result", "resync_required",
            ]
            assert srv.websocket_diagnostics()["totals"]["resyncRequired"] >= 1

            ws.release.set()
            await _drain_until(lambda: ws.closed)
            assert [event["type"] for event in ws.sent] == [
                "worker.stream", "worker.status", "queue.snapshot",
                "worker.result", "resync_required",
            ]
            assert ws.closed[-1] == (1013, "resync_required")
            assert ws not in srv.ws_clients
        finally:
            srv._WS_OUTBOUND_QUEUE_MAX = old_max
            _reset()

    asyncio.run(scenario())


def test_result_then_idle_control_order_is_fifo():
    async def scenario():
        _reset()
        ws = _GateWS()
        srv.ws_clients.add(ws)
        await srv.broadcast({
            "type": "worker.result", "sessionId": "s1", "workerId": "w1",
            "generation": 3, "status": "done", "taskSeq": 4, "result": "final",
        })
        await ws.started.wait()
        await srv.broadcast({
            "type": "worker.status", "sessionId": "s1", "workerId": "w1",
            "generation": 3, "status": "idle", "taskSeq": 4,
        })
        ws.release.set()
        await _drain_until(lambda: len(ws.sent) == 2)
        assert [event["type"] for event in ws.sent] == [
            "worker.result", "worker.status",
        ]
        await srv._ws_outbound[ws].close_now()
        _reset()

    asyncio.run(scenario())


def test_delta_identity_isolated_by_session_worker_generation_task_and_item():
    async def scenario():
        _reset()
        ws = _GateWS()
        srv.ws_clients.add(ws)
        await srv.broadcast(_delta("a", "a", session="s1", worker="w1", task=1, item="i1"))
        await ws.started.wait()
        for event in (
            _delta("b", "b", session="s2", worker="w1", task=1, item="i1"),
            _delta("c", "c", session="s1", worker="w2", task=1, item="i1"),
            _delta("d", "d", session="s1", worker="w1", task=2, item="i1"),
            _delta("e", "e", session="s1", worker="w1", task=1, item="i2"),
        ):
            await srv.broadcast(event)
        client = srv._ws_outbound[ws]
        assert client.coalesced_deltas == 0
        assert client.queue_depth == 4
        ws.release.set()
        await _drain_until(lambda: len(ws.sent) == 5)
        assert [event["event"]["part"]["text"] for event in ws.sent] == [
            "a", "b", "c", "d", "e",
        ]
        await client.close_now()
        _reset()

    asyncio.run(scenario())


def test_agent_result_cursor_advances_only_after_sender_success():
    async def scenario():
        _reset()
        ws = _GateWS()
        srv.agent_clients.add(ws)
        sub = srv.AgentSubscription()
        srv.agent_subscriptions[ws] = sub
        await srv.broadcast({
            "type": "worker.result", "sessionId": "s1", "workerId": "w1",
            "taskSeq": 9, "status": "done", "result": "final",
        })
        assert sub.consumed_seq == {}
        await ws.started.wait()
        ws.release.set()
        await _drain_until(lambda: sub.consumed_seq == {"s1": 9})
        await srv._ws_outbound[ws].close_now()
        _reset()

    asyncio.run(scenario())


def test_sender_task_is_cleaned_when_connection_closes():
    async def scenario():
        _reset()
        ws = _FastWS()
        srv.ws_clients.add(ws)
        await srv.broadcast({"type": "session.updated", "sessionId": "s1"})
        client = srv._ws_outbound[ws]
        await client.close_now()
        assert client.closed is True
        assert client._sender_task is None or client._sender_task.done()
        assert ws not in srv.ws_clients
        assert ws not in srv._ws_outbound
        _reset()

    asyncio.run(scenario())
