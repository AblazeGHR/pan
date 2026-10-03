"""Local MCP bridge gates and real stdio transport, not browser acceptance."""
import asyncio
import base64
import os
import socket
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from packages.web import terminal_tools_api as api
from packages.web.terminal_tools import TerminalTools, ToolRejected
from test_terminal_ws import FakeService, make_runtime, wait_ready, wait_until


class Service(FakeService):
    def create(self, **kwargs):
        self._record("create", **kwargs)
        return {"terminal_id": "term_1", "status": "running", "scope": {"session_id": "caller"}}

    def list(self):
        self._record("list")
        return [{"terminal_id": "term_1", "scope": {"session_id": "caller", "workspace_id": "w"}},
                {"terminal_id": "term_2", "scope": {"session_id": "foreign"}}]


async def setup():
    service = Service()
    runtime = make_runtime(service)
    await runtime.startup()
    await wait_ready(runtime)
    app = FastAPI()
    app.include_router(api.router)
    host = api.ToolsHost(runtime, resolver=lambda sid: {"id": sid} if sid == "caller" else None,
                         access=lambda caller, target: target == "caller")
    app.state.terminal_tools_host = host
    return app, host, service


@pytest.mark.parametrize("peer,headers,code", [
    ("192.0.2.1", {}, 403), ("127.0.0.1", {"Origin": "http://127.0.0.1"}, 403),
    ("127.0.0.1", {"Sec-Fetch-Site": "same-origin"}, 403),
    ("127.0.0.1", {"X-Pan-Terminal-Caller": "unknown"}, 403),
])
def test_untrusted_entry_never_calls_service(peer, headers, code):
    async def run():
        app, host, service = await setup()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 9)), base_url="http://test") as client:
            service.calls.clear()
            response = await client.post("/api/terminal-tools/create", json={},
                                         headers={"X-Pan-Terminal-Caller": "caller", **headers})
            assert response.status_code == code
            assert service.calls == []
        await host.close()
    asyncio.run(run())


def test_scope_filter_and_restricted_access_are_not_flat_metadata():
    service = Service()
    tools = TerminalTools(service, resolve_caller=lambda sid: {"id": sid, "restrictToManaged": True},
                          check_access=lambda caller, target: target == "caller")
    assert [row["terminal_id"] for row in tools.dispatch("caller", "list", {"workspace_id": "w"})] == ["term_1"]
    with pytest.raises(ToolRejected, match="session-scope-required"):
        tools.dispatch("caller", "create", {})
    with pytest.raises(ToolRejected, match="permission-denied"):
        tools.dispatch("caller", "input", {"terminal_id": "term_1", "take_control": True, "data_b64": "eA=="})
    assert service.written == []


def test_http_payload_gate_and_public_result():
    async def run():
        app, host, service = await setup()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 9)), base_url="http://test",
                                    headers={"X-Pan-Terminal-Caller": "caller"}) as client:
            assert (await client.post("/api/terminal-tools/create", json={"token": "FORGED"})).status_code == 422
            assert (await client.post("/api/terminal-tools/create", content=b"x" * (api.MAX_BODY+1),
                                      headers={"Content-Type": "application/json"})).status_code == 413
            response = await client.post("/api/terminal-tools/input", json={"terminal_id": "term_1", "take_control": True,
                "data_b64": base64.b64encode("中文".encode()).decode(), "seq": "9007199254740993"})
            assert response.status_code == 200
            assert response.json()["result"]["seq"] == "9007199254740993"
            assert service.written[0][1] == "中文".encode()
            assert len(service.released) == 1
            await host.close()
            assert (await client.post("/api/terminal-tools/create", json={})).status_code == 503
    asyncio.run(run())


def test_cancelled_input_keeps_worker_and_real_token_until_release():
    async def run():
        app, host, service = await setup()
        entered, release = threading.Event(), threading.Event()
        original = service.input
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(5)
            return original(*args, **kwargs)
        service.input = blocked
        task = asyncio.create_task(host.runtime.call(lambda: host.dispatch("caller", "input",
            {"terminal_id": "term_1", "take_control": True, "data_b64": "eA=="})))
        try:
            assert await wait_until(entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            report = await host.close(budget=0.03)
            assert report["cleanup_unconfirmed"] and report["active_workers"] == 1
            assert len(host.tools._retained) == 1
        finally:
            release.set()
        assert await wait_until(lambda: host.runtime.inflight == 0)
        assert (await host.close())["retained_tokens"] == 0
        assert len(service.released) == 1
    asyncio.run(run())


def test_real_stdio_mcp_reaches_local_bridge(monkeypatch):
    async def run():
        import uvicorn
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        app, host, service = await setup()
        @app.get("/api/sessions/{sid}")
        def caller(sid: str):
            return {"id": sid} if sid == "caller" else {"error": "missing"}
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            assert await wait_until(lambda: server.started)
            params = StdioServerParameters(command=sys.executable, args=["-m", "packages.mcp.server", "--pan-url", f"http://127.0.0.1:{port}"],
                                          env={**os.environ, "PAN_AGENT_SESSION_ID": "caller"})
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    names = {tool.name for tool in (await client.list_tools()).tools}
                    assert {"terminal_create", "terminal_input", "terminal_snapshot", "terminal_close"} <= names
                    result = await client.call_tool("terminal_create", {})
                    assert not result.isError
                    assert 'term_1' in str(result)
                    result = await client.call_tool("terminal_input", {"terminal_id": "term_1", "data_b64": "eA==", "take_control": True})
                    assert not result.isError and service.written[0][1] == b"x"
                    assert len(service.released) == 1
        finally:
            server.should_exit = True
            await serving
            sock.close()
            await host.close()
    asyncio.run(run())


def test_mcp_proxy_refuses_missing_caller_or_remote_transport(monkeypatch):
    from packages.mcp import server
    monkeypatch.setattr(server, "_terminal_transport", "sse")
    monkeypatch.setattr(server, "_caller_identity", lambda: pytest.fail("must not resolve remote caller"))
    assert server._terminal_api("create", {})["error"]["code"] == "local-stdio-required"
    monkeypatch.setattr(server, "_terminal_transport", "stdio")
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    assert server._terminal_api("create", {})["error"]["code"] == "caller-required"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ConPTY")
def test_real_stdio_mcp_conpty_roundtrip(tmp_path):
    async def run():
        import json
        import uvicorn
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from packages.core.terminal.service import TerminalService
        from test_terminal_api import _cleanup_owned_terminal, _process_alive_same_identity
        service = TerminalService(str(tmp_path / "terminals"), log_stderr=False)
        runtime = make_runtime(service)
        await runtime.startup()
        await wait_ready(runtime, timeout=30)
        app = FastAPI()
        app.include_router(api.router)
        host = api.ToolsHost(runtime, resolver=lambda sid: {"id": sid} if sid == "caller" else None,
                             access=lambda caller, target: target == "caller")
        app.state.terminal_tools_host = host
        @app.get("/api/sessions/{sid}")
        def caller(sid: str):
            return {"id": sid} if sid == "caller" else {"error": "missing"}
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        terminal_id = None
        try:
            assert await wait_until(lambda: server.started)
            params = StdioServerParameters(command=sys.executable, args=["-m", "packages.mcp.server", "--pan-url", f"http://127.0.0.1:{port}"],
                                          env={**os.environ, "PAN_AGENT_SESSION_ID": "caller"})
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    async def call(name, arguments):
                        result = await client.call_tool(name, arguments)
                        assert not result.isError, result
                        return result.structuredContent or json.loads(result.content[0].text)
                    created = await call("terminal_create", {})
                    assert created["ok"], created
                    terminal_id = created["result"]["terminal_id"]
                    sent = await call("terminal_input", {"terminal_id": terminal_id, "data_b64": base64.b64encode(b"echo PAN_MCP_REAL_MARKER\r").decode(), "take_control": True})
                    assert sent["ok"] and sent["result"]["accepted"] is True, sent
                    cursor, output = "0", b""
                    deadline = asyncio.get_running_loop().time() + 15
                    while asyncio.get_running_loop().time() < deadline:
                        page = await call("terminal_read", {"terminal_id": terminal_id, "cursor": cursor})
                        assert page["ok"], page
                        output += base64.b64decode(page["result"]["data_b64"])
                        cursor = page["result"]["next_cursor"]
                        if b"PAN_MCP_REAL_MARKER" in output:
                            break
                        await asyncio.sleep(0.1)
                    assert b"PAN_MCP_REAL_MARKER" in output
                    snap = await call("terminal_snapshot", {"terminal_id": terminal_id})
                    assert snap["ok"] and snap["result"]["recovery"] != "full", snap
                    for _ in range(20):
                        stopped = await call("terminal_close", {"terminal_id": terminal_id})
                        if stopped.get("ok"):
                            break
                        await asyncio.sleep(0.2)
                    assert stopped["ok"] and stopped["result"]["status"] == "exited", stopped
                    record = service.registry.get(terminal_id)
                    assert not _process_alive_same_identity(int(record.pid or 0), record.process_created_at_filetime)
                    assert not service._store().exists(terminal_id)
        finally:
            server.should_exit = True
            await serving
            sock.close()
            await host.close()
            if terminal_id:
                await asyncio.to_thread(_cleanup_owned_terminal, service, terminal_id)
            await runtime.shutdown()
    asyncio.run(run())
