"""LAN lifecycle and real owned HTTP/WS sockets, without app lifespan/providers."""
import asyncio
import json
import socket
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request
from starlette.websockets import WebSocket
import websockets

from packages.remote import lan_runtime as lan
from packages.core import session as sess, worker
from packages.web import server, terminal_api, terminal_ws


def free_port(host):
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def controller(monkeypatch, host, port):
    raw = {"lan": {"host": host, "port": port, "startupEnabled": False}, "unrelated": {"keep": True}}
    def write(value):
        raw.clear()
        raw.update(value)
    ctl = lan.Controller(server.app, read_config=lambda: dict(raw), write_config=write)
    monkeypatch.setattr(server.app.state, "lan_controller", ctl, raising=False)
    return ctl, raw


@pytest.mark.parametrize("value", [None, {"port": True}, {"port": 0}, {"port": 65536},
    {"startupEnabled": 1}, {"host": "localhost"}, {"host": "127.0.0.1"},
    {"host": "::1"}, {"host": "8.8.8.8"}, {"unknown": True}])
def test_invalid_config(value):
    with pytest.raises(ValueError):
        lan.validate(value)


def test_defaults_and_stale_address(monkeypatch):
    monkeypatch.setattr(lan, "addresses", lambda: ["192.168.2.3"])
    assert lan.validate({}) == lan.DEFAULT
    assert lan.validate({"host": "0.0.0.0"})["host"] == "0.0.0.0"
    assert lan.validate({"host": "192.168.2.4"}, check_local=False)["host"] == "192.168.2.4"


def test_conflict_save_and_startup_recovery(monkeypatch):
    async def run():
        host = lan.addresses()[0]
        with socket.socket() as owner:
            owner.bind((host, 0))
            owner.listen()
            ctl, raw = controller(monkeypatch, host, owner.getsockname()[1])
            failed = await ctl.operation("start")
            assert not failed["running"] and failed["error"] and not failed["urls"]
            assert ctl.task is None and not ctl.origins
            # Failed startup never kills the other port owner.
            assert owner.getsockname()[1] == raw["lan"]["port"]
        raw["lan"]["startupEnabled"] = True
        started = await ctl.operation("startup")
        assert started["running"] and not started["error"]
        old = dict(started["activeConfig"])
        try:
            changed = {**old, "port": free_port(host)}
            saved = await ctl.operation("save", changed)
            assert saved["running"] and saved["activeConfig"] == old
            assert saved["config"] == changed and raw["unrelated"] == {"keep": True}
            rejected = await ctl.operation("start")
            assert rejected["running"] and rejected["error"]
            await ctl.operation("stop")
            assert (await ctl.operation("startup"))["running"]
        finally:
            assert not (await ctl.operation("stop"))["running"]
        ctl2 = lan.Controller(server.app, read_config=lambda: raw, write_config=lambda _: None)
        try:
            assert (await ctl2.operation("startup"))["running"]
        finally:
            await ctl2.operation("stop")
        raw["lan"]["startupEnabled"] = False
        assert not (await ctl2.operation("startup"))["running"]
    asyncio.run(run())


def test_corrupt_config_is_not_overwritten(monkeypatch):
    writes = []
    def corrupt():
        raise ValueError("invalid existing JSON")
    ctl = lan.Controller(server.app, read_config=corrupt, write_config=writes.append)
    async def run():
        value = await ctl.operation("save", {})
        assert value["config"] is None and "invalid existing JSON" in value["error"]
        assert not writes
    asyncio.run(run())


def test_failed_listener_startup_releases_socket(monkeypatch):
    async def broken(self, sockets=None):
        raise RuntimeError("injected listener startup failure")
    monkeypatch.setattr(lan.LanServer, "serve", broken)
    async def run():
        host = lan.addresses()[0]
        port = free_port(host)
        ctl, _ = controller(monkeypatch, host, port)
        result = await ctl.operation("start")
        assert not result["running"] and "injected listener startup failure" in result["error"]
        assert ctl.task is None and not ctl.origins
        with socket.socket() as probe:
            probe.bind((host, port))
    asyncio.run(run())


def test_all_interfaces_preserves_local_process_boundary(monkeypatch):
    async def run():
        host = lan.addresses()[0]
        ctl, _ = controller(monkeypatch, "0.0.0.0", free_port("0.0.0.0"))
        started = await ctl.operation("start")
        assert started["running"], started
        port = started["activeConfig"]["port"]
        try:
            # A local process reaching this *remote* entry cannot lend its identity.
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False,
                                         headers={"Host": f"{host}:{port}"}) as client:
                assert (await client.get("/api/health")).status_code == 200
                assert (await client.post("/api/terminal-tools/list", json={})).status_code == 403
                assert (await client.post("/api/self-queue/read", json={})).status_code == 403
        finally:
            await ctl.operation("stop")
    asyncio.run(run())


def test_real_lan_http_ws_and_private_boundaries(monkeypatch):
    async def run():
        host = lan.addresses()[0]
        ctl, _ = controller(monkeypatch, host, free_port(host))
        chat = sess.Session(id="lan_chat", name="LAN isolated chat", queue_paused=True)
        sess._cache[chat.id] = chat
        def no_provider(*args, **kwargs):
            pytest.fail("Paused isolated chat must not start a provider")
        monkeypatch.setattr(worker, "_schedule_session_recovery", no_provider)
        started = await ctl.operation("start")
        assert started["running"], started
        url = started["urls"][0]
        try:
            async with httpx.AsyncClient(base_url=url, trust_env=False) as client:
                assert (await client.get("/api/health")).json()["status"] == "ok"
                assert (await client.get("/api/health", headers={"Host": "evil.example"})).status_code == 403
                assert (await client.get("/api/health", headers={"Origin": "http://evil.example"})).status_code == 403
                # Remote identity/admin calls stay denied even with spoofed proxy/caller headers.
                spoof = {"X-Forwarded-For": "127.0.0.1", "X-Forwarded-Host": "localhost",
                         "X-Pan-Queue-Caller": "fake", "X-Pan-Terminal-Caller": "fake"}
                for path in ["/api/self-queue/read", "/api/terminal-tools/list",
                             "/api/internal/main/shutdown", "/api/main/restart", "/api/main/exit",
                             "/api/remote/tailscale/start", "/api/remote/mcp/start",
                             "/api/remote/lan/stop", "/api/settings/ui", "/api/config/reload"]:
                    assert (await client.post(path, headers=spoof, json={})).status_code == 403, path
                assert (await client.get("/api/mcp/servers", headers=spoof)).status_code == 403
                assert ctl.status()["running"]
                assert (await client.post("/api/readonly", json={"managerId": "fake", "sessionId": chat.id, "readonlySession": False})).status_code == 403
            ws_url = url.replace("http://", "ws://") + "/ws"
            async with websockets.connect(ws_url, origin=url, proxy=None) as ws:
                await ws.send(json.dumps({"type": "ping"}))
                assert json.loads(await ws.recv()) == {"type": "pong"}
                await ws.send(json.dumps({"type": "user_inject", "sessionId": chat.id,
                                          "text": "isolated LAN message", "clientMessageId": "lan-msg-1"}))
                accepted = None
                for _ in range(6):
                    event = json.loads(await asyncio.wait_for(ws.recv(), 3))
                    if event["type"] == "user_inject.accepted":
                        accepted = event
                        break
                assert accepted is not None
                assert accepted["type"] == "user_inject.accepted", accepted
                persisted = json.loads((sess.SESSION_DIR / f"{chat.id}.json").read_text(encoding="utf-8"))
                assert persisted["queue_pending"][0]["text"] == "isolated LAN message"
                assert persisted["queue_paused"] is True
                await ctl.operation("stop")
                await ws.wait_closed()
                with pytest.raises(websockets.ConnectionClosed):
                    await ws.send(json.dumps({"type": "ping"}))
            assert not ctl.origins and not ctl.status()["running"]
            with socket.socket() as reuse:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    reuse.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                reuse.bind((host, started["activeConfig"]["port"]))
        finally:
            await ctl.operation("stop")
    asyncio.run(run())


def test_terminal_gate_unions_active_lan_without_widening(monkeypatch):
    host = "192.168.2.3"
    monkeypatch.setattr(lan, "addresses", lambda: [host])
    ctl, _ = controller(monkeypatch, host, 52341)
    runtime = terminal_api.build_runtime(env={}, host="127.0.0.1", port=52342, platform="win32")
    runtime.state = terminal_api.STATE_READY
    runtime.service = object()
    monkeypatch.setattr(server.app.state, "tailscale_controller", SimpleNamespace(origins=frozenset({"http://100.64.1.2:52343"})), raising=False)
    origin = f"http://{host}:52341"
    def request(headers):
        return Request({"type": "http", "method": "POST", "path": "/", "scheme": "http",
                        "app": server.app, "headers": [(key.encode(), value.encode()) for key, value in headers.items()]})
    headers = {"host": f"{host}:52341", "origin": origin, "content-type": "application/json"}
    with pytest.raises(terminal_api.GateRejected):
        runtime.check_gate(request(headers))
    ctl.origins = frozenset({origin})
    runtime.check_gate(request(headers))
    malformed = terminal_api.build_runtime(env={"PAN_TERMINAL_ALLOWED_ORIGINS": "not-an-origin"},
                                          host="127.0.0.1", port=52342, platform="win32")
    assert not malformed.request_origins(server.app)
    ws = WebSocket({"type": "websocket", "path": "/", "scheme": "ws", "app": server.app,
                    "headers": [(key.encode(), value.encode()) for key, value in headers.items()]}, None, None)
    terminal_ws.check_ws_gate(ws, runtime)
    get = request({"host": f"{host}:52341"})
    get.scope.update(method="GET", pan_lan_entry=True)
    runtime.check_gate(get)
    get.scope["pan_lan_entry"] = False
    with pytest.raises(terminal_api.GateRejected):
        runtime.check_gate(get)
    assert "http://100.64.1.2:52343" in runtime.request_origins(server.app)
    for bad in [{**headers, "host": "evil.example"}, {**headers, "origin": "http://evil.example"},
                {**headers, "origin": "http://evil.example", "x-forwarded-host": f"{host}:52341"}]:
        with pytest.raises(terminal_api.GateRejected):
            runtime.check_gate(request(bad))
    ctl.origins = frozenset()
    with pytest.raises(terminal_api.GateRejected):
        runtime.check_gate(request(headers))


def test_management_requires_local_primary_origin(monkeypatch):
    def request(peer="127.0.0.1", host="127.0.0.1:52342", origin=None, entry=False):
        headers = [(b"host", host.encode())]
        if origin:
            headers.append((b"origin", origin.encode()))
        return Request({"type": "http", "method": "POST", "path": "/", "scheme": "http",
                        "client": (peer, 1), "headers": headers, "pan_lan_entry": entry})
    server._require_lan_management(request(origin="http://127.0.0.1:52342"))
    for bad in [request(peer="192.168.2.4"), request(host="evil.example"),
                request(origin="http://evil.example"), request(entry=True)]:
        with pytest.raises(server.HTTPException) as exc:
            server._require_lan_management(bad)
        assert exc.value.status_code == 403


def test_local_lan_http_handlers(monkeypatch):
    async def run():
        host = lan.addresses()[0]
        ctl, raw = controller(monkeypatch, host, free_port(host))
        primary_url = f"http://127.0.0.1:{free_port('127.0.0.1')}"
        transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 23456))
        async with httpx.AsyncClient(transport=transport, base_url=primary_url) as client:
            assert not (await client.get("/api/remote/lan")).json()["running"]
            assert (await client.put("/api/remote/lan", content="{broken", headers={"Origin": primary_url})).status_code == 422
            invalid = await client.put("/api/remote/lan", json={"port": 0})
            assert invalid.json()["error"] and raw["lan"]["port"] > 0
            saved = await client.put("/api/remote/lan", json=raw["lan"], headers={"Origin": primary_url})
            assert not saved.json()["error"] and not saved.json()["running"]
            assert (await client.post("/api/remote/lan/reset")).status_code == 400
            assert (await client.post("/api/remote/lan/start", headers={"Origin": "http://evil.example"})).status_code == 403
            try:
                assert (await client.post("/api/remote/lan/start", headers={"Origin": primary_url})).json()["running"]
                assert not (await client.post("/api/remote/lan/stop", headers={"Origin": primary_url})).json()["running"]
            finally:
                await ctl.operation("stop")
    asyncio.run(run())


def test_real_lan_terminal_rest_and_ws(monkeypatch):
    # Reuse the existing terminal protocol fake: no provider/native user data.
    from test_terminal_ws import FakeService
    async def run():
        host = lan.addresses()[0]
        ctl, _ = controller(monkeypatch, host, free_port(host))
        runtime = terminal_api.build_runtime(env={}, host="127.0.0.1", port=free_port("127.0.0.1"), platform="win32")
        runtime.state = terminal_api.STATE_READY
        runtime.service = FakeService()
        monkeypatch.setattr(server.app.state, "terminal_runtime", runtime, raising=False)
        manager = await terminal_ws.start_ws(server.app, runtime=runtime)
        started = await ctl.operation("start")
        assert started["running"], started
        url = started["urls"][0]
        try:
            async with httpx.AsyncClient(base_url=url, trust_env=False) as client:
                # Exact insecure browser GET shape: no Origin/Fetch Metadata.
                response = await client.get("/api/terminals/term_1")
                assert response.status_code == 200, response.text
                assert response.json()["ok"] is True
            ws_url = url.replace("http://", "ws://") + "/ws/terminal/term_1"
            async with websockets.connect(ws_url, origin=url, proxy=None) as ws:
                hello = json.loads(await asyncio.wait_for(ws.recv(), 3))
                assert hello["type"] == "hello" and hello["role"] == "observer"
            calls = len(runtime.service.calls)
            for origin in [None, "http://evil.example"]:
                with pytest.raises(websockets.exceptions.InvalidStatus):
                    async with websockets.connect(ws_url, origin=origin, proxy=None):
                        pass
            assert len(runtime.service.calls) >= calls
            with pytest.raises(websockets.exceptions.InvalidStatus):
                async with websockets.connect(url.replace("http://", "ws://") + "/ws/agent", origin=url, proxy=None):
                    pass
        finally:
            await ctl.operation("stop")
            await terminal_ws.stop_ws(server.app, manager=manager)
    asyncio.run(run())
