"""MA regression gates for asynchronous ownership and stream generations."""
import asyncio

import pytest

from packages.web import terminal_ws as ws
from test_terminal_ws import FakeService, FakeWebSocket, _Gate, make_runtime, wait_ready, wait_until


async def connection(service):
    runtime = make_runtime(service)
    await runtime.startup()
    await wait_ready(runtime)
    return ws._Connection(FakeWebSocket(), "term_1", runtime, cursor=0)


def test_cancelled_attach_retains_and_reclaims_real_token():
    async def run():
        service = FakeService()
        service.attach_gate = gate = _Gate()
        conn = await connection(service)
        task = asyncio.create_task(conn._await_lease(lambda: service.attach("term_1", conn.connection_id)))
        try:
            assert await wait_until(gate.entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            gate.release.set()
        await wait_until(lambda: conn.runtime.inflight == 0)
        report = await conn.close()
        assert report["lease_released"] and len(service.released) == 1
    asyncio.run(run())


def test_failed_release_retains_actual_token_for_retry():
    async def run():
        service = FakeService()
        conn = await connection(service)
        token = conn._token = service.attach("term_1", conn.connection_id)
        original = service.release_attachment
        service.release_attachment = lambda token: (_ for _ in ()).throw(OSError("injected"))
        assert not (await conn.close())["lease_released"]
        service.release_attachment = original
        assert (await conn.close())["lease_released"]
        assert service.released == [token]
    asyncio.run(run())


def test_resume_discards_read_started_in_previous_epoch():
    async def run():
        service = FakeService()
        service.read_gate = gate = _Gate()
        service.read_pages = [{"data": b"OLD", "seq": 0, "next_cursor": 3}]
        conn = await connection(service)
        reader = asyncio.create_task(ws._reader_loop(conn))
        try:
            assert await wait_until(gate.entered.is_set)
            await ws._cmd_resume(conn, {"cursor": "100"})
            gate.release.set()
            await asyncio.sleep(0.1)
            assert conn.cursor == 100
            assert not any('"output"' in f.text for f in conn.outbound._queue)
        finally:
            gate.release.set()
            conn._tasks = [reader]
            await conn.close()
    asyncio.run(run())


def test_inflight_frame_counts_towards_item_cap():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class Socket:
            async def send_text(self, text):
                entered.set()
                await release.wait()
        outbound = ws._Outbound(Socket(), max_items=1)
        outbound.enqueue(ws._Frame("first"))
        sender = asyncio.create_task(outbound.sender_loop())
        try:
            await entered.wait()
            with pytest.raises(ws.SlowClient):
                outbound.enqueue(ws._Frame("second"))
        finally:
            release.set()
            outbound.close()
            await sender
    asyncio.run(run())


def test_shutdown_closes_admission_and_pending_counts_capacity():
    async def run():
        conn = await connection(FakeService())
        manager = ws._Manager(conn.runtime, max_connections=1)
        key = manager.reserve()
        manager.commit(key, conn)
        manager.track_pending(conn, asyncio.create_task(asyncio.sleep(0)))
        manager.remove(conn)
        assert manager.reserve() is None
        await manager.shutdown()
        assert manager.reserve() is None
    asyncio.run(run())


def test_claim_can_retake_after_another_connection_takes_control():
    async def run():
        service = FakeService()
        conn = await connection(service)
        await ws._cmd_claim(conn)
        first = conn._token
        service.attach("term_1", "other", role="control")
        await ws._cmd_claim(conn)
        assert conn._token.generation > first.generation
        assert service.control_holder("term_1")["client_id"] == conn.connection_id
        await conn.close()
    asyncio.run(run())


def test_shutdown_shared_budget_retains_and_consumes_late_releases(monkeypatch):
    async def run():
        service = FakeService()
        service.release_gate = gate = _Gate(target=2)
        conn = await connection(service)
        manager = ws._Manager(conn.runtime)
        for _ in range(2):
            item = ws._Connection(FakeWebSocket(), "term_1", conn.runtime, cursor=0)
            item._token = service.attach("term_1", item.connection_id)
            manager.commit(manager.reserve(), item)
        try:
            before = asyncio.get_running_loop().time()
            await manager.shutdown()
            assert asyncio.get_running_loop().time() - before < 0.25
            assert manager.snapshot()["reserved"] == 2
            assert manager.reserve() is None
        finally:
            gate.release.set()
        assert await wait_until(lambda: manager.snapshot()["reserved"] == 0)
        assert len(service.released) == 2
    monkeypatch.setattr(ws, "CONNECTION_CLOSE_BUDGET", 0.05)
    asyncio.run(run())


def test_resume_waits_for_inflight_output_and_resets_ack_frontier():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class Socket(FakeWebSocket):
            async def send_text(self, text):
                entered.set()
                await release.wait()
                await super().send_text(text)
        conn = await connection(FakeService())
        conn.websocket = Socket()
        conn.outbound._ws = conn.websocket
        conn.send_event("output", epoch=0, marker=90, data_b64="")
        sender = asyncio.create_task(conn.outbound.sender_loop(lambda: conn.stream_epoch))
        await entered.wait()
        resume = asyncio.create_task(ws._cmd_resume(conn, {"cursor": "5"}))
        await asyncio.sleep(0)
        assert not resume.done()
        release.set()
        await resume
        assert conn.sent_end == 5
        with pytest.raises(ws.ProtocolError):
            await ws._cmd_ack(conn, {"next_seq": "6"})
        conn.outbound.close()
        await sender
    asyncio.run(run())


def test_shutdown_before_commit_or_attach_never_issues_lease():
    async def run():
        service = FakeService()
        conn = await connection(service)
        manager = ws._Manager(conn.runtime)
        reservation = manager.reserve()
        await manager.shutdown()
        assert not manager.commit(reservation, conn)
        await conn.close()
        with pytest.raises(asyncio.CancelledError):
            await conn._await_lease(lambda: service.attach("term_1", conn.connection_id))
        assert service.lease_calls() == []
    asyncio.run(run())


def test_browser_get_without_origin_requires_protected_same_origin_metadata():
    async def run():
        import httpx
        from test_terminal_ws import start_app
        service = FakeService()
        service.list = lambda: []
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        headers = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8768") as client:
            assert (await client.get("/api/terminals", headers=headers)).status_code == 200
            assert (await client.get("/api/terminals")).status_code == 403
            assert (await client.get("/api/terminals", headers={**headers, "Sec-Fetch-Site": "cross-site"})).status_code == 403
            assert (await client.get("/api/terminals", headers={**headers, "Host": "evil.invalid"})).status_code == 403
            assert (await client.post("/api/terminals", json={}, headers=headers)).status_code == 403
        await runtime.shutdown()
    asyncio.run(run())
