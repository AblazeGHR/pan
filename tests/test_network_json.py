import asyncio
import gzip
import threading

import httpx
import pytest
from fastapi import FastAPI
from starlette.responses import JSONResponse

from packages.web.network_json import NetworkJSONResponse
from packages.web import server


@pytest.mark.parametrize("accepted,compressed", [
    ("gzip, br", True), ("gzip;q=0.5", True), ("*", True),
    ("gzip;q=0, *;q=1", False), ("identity", False),
    ("br", False), ("gzip;q=invalid", False), ("gzip;q=2", False),
])
def test_negotiated_list_response_preserves_json(accepted, compressed):
    app = FastAPI()
    payload = {"sessions": [{"id": str(i), "name": "会话" * 60} for i in range(60)]}

    @app.get("/list", response_class=NetworkJSONResponse)
    async def listing():
        return payload

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            result = await client.get("/list", headers={"Accept-Encoding": accepted})
        assert result.json() == payload
        assert (result.headers.get("content-encoding") == "gzip") is compressed
        assert result.headers["vary"] == "Accept-Encoding"
        if compressed:
            assert int(result.headers["content-length"]) < len(result.content)
        else:
            assert int(result.headers["content-length"]) == len(result.content)
    asyncio.run(run())


def test_compression_does_not_block_event_loop(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original = gzip.compress

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(gzip, "compress", blocked)

    async def run():
        app = FastAPI()
        app.add_api_route("/list", lambda: {"text": "x" * 10000}, response_class=NetworkJSONResponse)
        app.add_api_route("/health", lambda: {"ok": True})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            pending = asyncio.create_task(client.get("/list"))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                result = await asyncio.wait_for(client.get("/health"), 1)
                assert result.json() == {"ok": True}
            finally:
                release.set()
                await pending
    asyncio.run(run())


def test_small_response_stays_plain_and_other_routes_are_not_compressed():
    async def run():
        app = FastAPI()
        app.add_api_route("/small", lambda: {"sessions": []}, response_class=NetworkJSONResponse)
        app.add_api_route("/other", lambda: {"text": "x" * 10000})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            small = await client.get("/small")
            other = await client.get("/other")
        assert small.json() == {"sessions": []}
        assert "content-encoding" not in small.headers
        assert "content-encoding" not in other.headers
        assert "vary" not in other.headers
    asyncio.run(run())
    listing = next(route for route in server.app.routes if getattr(route, "path", None) == "/api/sessions")
    assert listing.response_class is NetworkJSONResponse
    assert issubclass(NetworkJSONResponse, JSONResponse)


def test_real_summary_revalidates_current_preview_status_and_membership(monkeypatch):
    from packages.core import session as sess
    from types import SimpleNamespace
    target = sess.create("conditional-summary", adapter="codex")
    active = {}
    monkeypatch.setattr(server.worker, "find_alive_workers_by_session", lambda: active)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(server.app), base_url="http://test") as client:
            first = await client.get("/api/sessions?summary=1")
            assert first.status_code == 200
            assert first.headers["cache-control"] == "private, no-cache"
            full = await client.get("/api/sessions")
            assert full.headers["cache-control"] == "no-cache, no-store, must-revalidate"
            assert "etag" not in full.headers
            etag = first.headers["etag"]
            same = await client.get("/api/sessions?summary=1", headers={"If-None-Match": etag})
            assert same.status_code == 304
            assert same.content == b""
            assert "content-length" not in same.headers
            # Weak tags also match the identity representation.
            identity = await client.get("/api/sessions?summary=1", headers={
                "If-None-Match": f'"unrelated", {etag[2:]}', "Accept-Encoding": "identity"})
            assert identity.status_code == 304
            active[target.id] = SimpleNamespace(status="running", worker_id="test-worker", generation=1)
            running = await client.get("/api/sessions?summary=1", headers={"If-None-Match": etag})
            assert running.status_code == 200
            assert running.json()["sessions"][0]["workerStatus"] == "running"
            etag = running.headers["etag"]
            active.clear()
            sess.append_history(target, {"role": "assistant", "content": "已完成，新的摘要"})
            sess.save(target)
            done = await client.get("/api/sessions?summary=1", headers={"If-None-Match": etag})
            assert done.status_code == 200
            assert done.json()["sessions"][0]["workerStatus"] is None
            assert done.json()["sessions"][0]["lastMessage"] == "已完成，新的摘要"
            etag = done.headers["etag"]
            target.queue_paused = True
            sess.save(target)
            paused = await client.get("/api/sessions?summary=1", headers={"If-None-Match": etag})
            assert paused.status_code == 200
            assert paused.json()["sessions"][0]["queuePaused"] is True
            etag = paused.headers["etag"]
            sess.delete(target.id)
            deleted = await client.get("/api/sessions?summary=1", headers={"If-None-Match": etag})
            assert deleted.status_code == 200
            assert deleted.json() == {"sessions": []}
            detail = await client.get(f"/api/sessions/{target.id}?view=metadata")
            assert detail.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    asyncio.run(run())
