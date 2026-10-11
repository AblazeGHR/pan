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


def test_real_history_compression_preserves_window_and_never_revalidates_summary():
    from packages.core import session as sess
    target = sess.create("compressed-history", adapter="codex", history=[
        {"role": "assistant", "content": f"row {i}: " + "正文和代码片段\n" * 300}
        for i in range(60)
    ])

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(server.app), base_url="http://test") as client:
            url = f"/api/sessions/{target.id}/history?before=40&limit=20&summary=1"
            plain = await client.get(url, headers={"Accept-Encoding": "identity"})
            compressed = await client.get(url, headers={"Accept-Encoding": "gzip", "If-None-Match": "*"})
            assert plain.status_code == compressed.status_code == 200
            assert compressed.json() == plain.json()
            page = compressed.json()
            assert page["start"] == 20 and page["total"] == 60 and page["hasMore"]
            assert len(page["history"]) == 20
            assert compressed.headers["content-encoding"] == "gzip"
            assert int(compressed.headers["content-length"]) < len(plain.content) / 10
            assert compressed.headers["cache-control"] == "no-cache, no-store, must-revalidate"
            assert "etag" not in compressed.headers
            sess.append_history(target, {"role": "assistant", "content": "new tail"})
            sess.save(target)
            tail = await client.get(f"/api/sessions/{target.id}/history?limit=1")
            assert tail.json()["total"] == 61
            assert tail.json()["history"][-1]["content"] == "new tail"
            missing = await client.get("/api/sessions/missing/history")
            assert missing.json() == {"error": "Session not found"}
    asyncio.run(run())


@pytest.mark.parametrize('view', ['full', 'summary', 'history'])
def test_http_history_encoding_yields_to_other_requests_and_freezes_metadata(monkeypatch, view):
    from packages.core import session as sess
    target = sess.create('encoding-snapshot', adapter='codex', history=[
        {'role': 'assistant', 'content': 'before', 'parts': [{'type': 'text', 'text': 'before'}]},
    ])
    target.raw_usage = {'nested': {'value': 'before'}}
    target.last_result = {'status': 'done', 'result': 'before', 'nested': {'value': 'before'}}
    entered = threading.Event()
    release = threading.Event()
    original = server.jsonable_encoder
    encoding_threads = []

    def blocked(payload):
        encoding_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return original(payload)

    monkeypatch.setattr(server, 'jsonable_encoder', blocked)

    async def run():
        loop_thread = threading.get_ident()
        app = FastAPI()
        app.add_api_route('/api/sessions', server.api_list_sessions)
        app.add_api_route('/api/sessions/{session_id}/history', server.api_session_history)

        @app.get('/health')
        async def health():
            return {'ok': True}

        path = (f'/api/sessions/{target.id}/history' if view == 'history' else
                '/api/sessions?summary=1' if view == 'summary' else '/api/sessions')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            pending = asyncio.create_task(client.get(path, headers={'Accept-Encoding': 'identity'}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                assert len(encoding_threads) == 1
                assert encoding_threads[0] != loop_thread
                result = await asyncio.wait_for(client.get('/health'), 1)
                assert result.json() == {'ok': True}
                # The list must own nested live Session fields before encoding
                # yields; a Worker update cannot mutate its in-flight response.
                target.raw_usage['nested']['value'] = 'after'
                target.last_result['nested']['value'] = 'after'
            finally:
                release.set()
                response = await pending
            assert response.status_code == 200
            if view == 'full':
                item = next(row for row in response.json()['sessions'] if row['id'] == target.id)
                assert item['rawUsage']['nested']['value'] == 'before'
                assert item['lastResult']['nested']['value'] == 'before'
            elif view == 'history':
                assert response.json()['history'][0]['parts'][0]['text'] == 'before'
    asyncio.run(run())
