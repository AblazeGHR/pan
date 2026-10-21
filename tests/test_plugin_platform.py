"""Plugin compatibility and real stdio/Named Pipe integration, no model or QQ sends."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from packages.core import session as sess
from packages.core.manifest_loader import ManifestConfig, McpServer, REPO_ROOT, load_manifests
from packages.plugins.catalog import Catalog, session_bindings
from packages.plugins.ipc import Bridge, request, plugin_lifespan, ENV
from packages.plugins.runtime import Runtime


def binding(plugin="sample", mode="gateway", enabled=True):
    return {"plugin_id": plugin, "enabled": enabled, "mode": mode}


def test_plugin_lifespan_restores_environment_and_drains_owned_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, "previous-runtime-address")
    session = sess.Session(id="ses_lifecycle", name="lifecycle", adapter_config={"plugin_bindings": []})

    async def run():
        async with plugin_lifespan(lambda: ManifestConfig(), lambda _: session, tmp_path) as runtime:
            address = os.environ[ENV]
            assert address != "previous-runtime-address"
            response = await asyncio.to_thread(request, {"session_id": session.id, "operation": "core_enabled"})
            assert response == {"enabled": False}
            watcher = runtime.watcher
            assert not watcher.done()
        assert os.environ[ENV] == "previous-runtime-address"
        assert watcher.done()
        assert runtime.closing and not runtime.providers and not runtime.requests and not runtime.invocations
        with pytest.raises((OSError, EOFError)):
            await asyncio.to_thread(request, {"session_id": session.id, "operation": "core_enabled"}, address)

    asyncio.run(run())


@pytest.fixture
def platform(tmp_path):
    state = tmp_path / "directory.json"
    state.write_text(json.dumps({"tools": ["echo", "broken"]}), encoding="utf-8")
    manifest = ManifestConfig(mcp_servers=[McpServer("sample", command=sys.executable,
        args=[str(REPO_ROOT / "tests/fixtures/plugin_provider.py"), str(state)],
        cwd=str(REPO_ROOT), env={"PAN_AGENT_SESSION_ID": "replaced-at-launch"})])
    sessions = {sid: sess.Session(id=sid, name=sid, adapter_config={"plugin_bindings": [binding()]})
                for sid in ("ses_a", "ses_b")}
    return SimpleNamespace(state=state, manifest=manifest, sessions=sessions,
                           runtime=Runtime(lambda: manifest, sessions.get))


def test_qq_alias_and_physical_configuration_are_separate():
    catalog = Catalog(load_manifests(["manifest.json", "packages/mcp/manifest.json"]))
    assert catalog.get("pan-qq").id == "qq"
    bindings = catalog.bindings([binding("pan-qq", "pan_native"), binding("qq", "pan_native")])
    assert bindings == [binding("qq", "pan_native")]
    physical = catalog.compile(bindings)
    assert [p["name"] for p in physical] == ["pan"]
    assert "--core" not in physical[0]["args"]
    physical_core = catalog.compile([*bindings, binding("pan", "standalone")])
    assert "--core" in physical_core[0]["args"]
    assert catalog.compile([binding("qq", "standalone")])[0]["name"] == "qq"
    assert catalog.compile([binding("qq")])[0]["name"] == "pan-tools"
    with pytest.raises(ValueError, match="Conflicting"):
        catalog.bindings([binding("pan-qq"), binding("qq", "standalone")])


def test_legacy_binding_reads_without_mutating_qq_data():
    catalog = Catalog(load_manifests(["manifest.json"]))
    old = sess.Session(id="ses_old", name="old", adapter_config={"mcp_servers": [{"name": "pan-qq"}]},
        qq_subscriptions={"user:12345", "user:12345@99999"}, qq_report_targets={"group:54321@99999"},
        qq_report_outbox={"original": {"state": "attempted", "target": "group:54321@99999"}})
    before = copy.deepcopy(old.to_dict())
    assert session_bindings(old, catalog) == [binding("qq", "standalone")]
    assert old.to_dict() == before


def test_old_manifest_qq_override_keeps_its_precedence_and_connection(tmp_path):
    override = tmp_path / "old-qq-manifest.json"
    override.write_text(json.dumps({"mcp_servers": [{"name": "pan-qq", "url": "https://legacy.example/qq",
        "headers": {"X-Account": "legacy-account"}, "transport": "http"}]}), encoding="utf-8")
    manifest = load_manifests(["manifest.json", "packages/mcp/manifest.json", str(override)])
    catalog = Catalog(manifest)
    physical = catalog.compile(catalog.legacy_bindings(["pan-qq"]))
    assert [s.name for s in manifest.mcp_servers].count("qq") == 1
    assert physical[0]["url"] == "https://legacy.example/qq"
    assert physical[0]["headers"] == {"X-Account": "legacy-account"}
    legacy_only = Catalog(load_manifests([str(override)]))
    assert legacy_only.get("pan-qq").id == "qq"
    assert legacy_only.get("qq").tool_exports["qq_send_message"] == "send_message"


def test_concurrent_stdio_sessions_have_different_schema_and_context(platform):
    async def run():
        runtime = platform.runtime
        try:
            first, second = await asyncio.gather(runtime.directory("ses_a", "gateway"), runtime.directory("ses_b", "gateway"))
            assert first[0]["tool"]["inputSchema"]["required"] == ["text"]
            assert second[0]["tool"]["inputSchema"]["required"] == ["count"]
            one, two = await asyncio.gather(
                runtime.invoke("ses_a", "gateway", "sample.echo", {"text": "hello"}),
                runtime.invoke("ses_b", "gateway", "sample.echo", {"count": 3}))
            assert one["structuredContent"]["session"] == "ses_a"
            assert two["structuredContent"]["session"] == "ses_b"
            assert one["content"][1]["type"] == "image"
            bad = await runtime.invoke("ses_b", "gateway", "sample.echo", {"text": "wrong-session-schema"})
            assert bad["isError"] is True
            rejected = await runtime.invoke("ses_a", "gateway", "sample.broken", {"text": "x"})
            assert rejected["isError"] is True
            assert rejected["content"][0]["text"] == "business rejected"
        finally:
            await runtime.close()
    asyncio.run(run())


def test_gateway_updates_directory_and_rejects_stale_contract_cursor(platform):
    async def run():
        runtime = platform.runtime
        try:
            old = await runtime.handle({"session_id": "ses_a", "operation": "discover", "limit": 1})
            task = runtime.providers[("ses_a", "sample")][1].task
            old_contract = old["tools"][0]["contract_digest"]
            platform.state.write_text(json.dumps({"tools": ["new_tool"]}), encoding="utf-8")
            fresh = await runtime.handle({"session_id": "ses_a", "operation": "discover", "include_schema": True})
            assert [t["name"] for t in fresh["tools"]] == ["sample.new_tool"]
            assert runtime.providers[("ses_a", "sample")][1].task is task
            assert fresh["generation"] != old["generation"]
            with pytest.raises(ValueError, match="cursor expired"):
                await runtime.handle({"session_id": "ses_a", "operation": "discover", "cursor": old["next_cursor"]})
            missing = await runtime.invoke("ses_a", "gateway", "sample.echo", {"text": "x"}, old_contract)
            assert missing["isError"] is True
            platform.sessions["ses_a"].adapter_config["plugin_bindings"] = [binding(enabled=False)]
            assert await runtime.directory("ses_a", "gateway") == []
            assert (await runtime.invoke("ses_a", "gateway", "sample.new_tool", {"text": "x"}))["isError"]
        finally:
            await runtime.close()
    asyncio.run(run())


def test_native_directory_change_requests_existing_idle_restart(platform):
    async def run():
        runtime = platform.runtime
        changed = AsyncMock()
        runtime.on_native_change = changed
        platform.sessions["ses_a"].adapter_config["plugin_bindings"] = [binding(mode="pan_native")]
        try:
            first = await runtime.directory("ses_a", "native")
            assert first[0]["native_name"] == "pan_sample_echo"
            changed.assert_not_awaited()
            platform.state.write_text(json.dumps({"tools": ["replacement"]}), encoding="utf-8")
            await runtime.directory("ses_a", "native")
            changed.assert_awaited_once_with("ses_a")
        finally:
            await runtime.close()
    asyncio.run(run())


def test_cancelled_caller_does_not_replay_or_close_an_admitted_side_effect(platform):
    async def run():
        runtime = platform.runtime
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Provider:
            closed = False
            async def request(self, operation, payload):
                calls.append((operation, payload))
                entered.set()
                await release.wait()
                assert not self.closed
                return {"content": [{"type": "text", "text": "confirmed"}], "isError": False}
            async def close(self):
                self.closed = True
        provider = Provider()
        runtime.providers[("ses_a", "sample")] = ("owned", provider)
        runtime.directory = AsyncMock(return_value=[{"name": "sample.echo", "plugin": "sample", "source_name": "echo",
            "contract_digest": "one", "tool": {"inputSchema": {"type": "object"}}}])
        caller = asyncio.create_task(runtime.handle({"session_id": "ses_a", "operation": "call", "name": "sample.echo", "arguments": {}}))
        await entered.wait()
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        closing = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        assert not closing.done() and not provider.closed
        release.set()
        await closing
        assert provider.closed and len(calls) == 1
    asyncio.run(run())


def test_real_stdio_projection_over_local_ipc(platform, tmp_path):
    async def run():
        runtime = platform.runtime
        bridge = Bridge(runtime, tmp_path)
        platform.sessions["ses_b"].adapter_config["plugin_bindings"] = [binding(mode="pan_native")]
        try:
            async with AsyncExitStack() as stack:
                clients = []
                for sid, view in [("ses_a", "gateway"), ("ses_b", "native")]:
                    streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                        command=sys.executable, args=["-m", "packages.plugins.mcp", "--view", view],
                        cwd=str(REPO_ROOT), env={**os.environ, "PAN_PLUGIN_IPC": bridge.address,
                                               "PAN_AGENT_SESSION_ID": sid})))
                    client = await stack.enter_async_context(ClientSession(*streams))
                    await client.initialize()
                    clients.append(client)
                gateway, native = clients
                assert [t.name for t in (await gateway.list_tools()).tools] == ["api_discover", "api_call"]
                tools = (await native.list_tools()).tools
                assert {t.name for t in tools} == {"pan_sample_echo", "pan_sample_broken"}
                assert all("count" in t.inputSchema["required"] for t in tools)
                response = await native.call_tool("pan_sample_echo", {"count": 5})
                assert response.structuredContent["session"] == "ses_b"
                discover = await gateway.call_tool("api_discover", {"name": "sample.echo"})
                assert discover.structuredContent["tools"][0]["inputSchema"]["required"] == ["text"]
                platform.state.write_text(json.dumps({"tools": ["live_added"]}), encoding="utf-8")
                new = await gateway.call_tool("api_discover", {})
                assert new.structuredContent["tools"][0]["name"] == "sample.live_added"
                assert [t.name for t in (await gateway.list_tools()).tools] == ["api_discover", "api_call"]
                result = await gateway.call_tool("api_call", {"name": "sample.live_added", "arguments": {"text": "works"}})
                assert result.structuredContent["session"] == "ses_a"
                forbidden = await native.call_tool("session_list", {})
                assert forbidden.isError
        finally:
            await bridge.close()
    asyncio.run(run())


def test_pan_core_can_share_connection_and_be_disabled_independently(platform, tmp_path):
    async def run():
        platform.manifest.mcp_servers.append(McpServer("pan", command=sys.executable,
            args=["-m", "packages.mcp.server"], cwd=str(REPO_ROOT)))
        session = platform.sessions["ses_a"]
        session.adapter_config["plugin_bindings"] = [binding(mode="pan_native"), binding("pan", "standalone")]
        bridge = Bridge(platform.runtime, tmp_path)
        try:
            async with stdio_client(StdioServerParameters(command=sys.executable,
                args=["-m", "packages.plugins.mcp", "--view", "native", "--core"], cwd=str(REPO_ROOT),
                env={**os.environ, "PAN_PLUGIN_IPC": bridge.address, "PAN_AGENT_SESSION_ID": session.id})) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    names = {t.name for t in (await client.list_tools()).tools}
                    assert {"pan_sample_echo", "session_create", "pan_handbook"} <= names
                    result = await client.call_tool("pan_handbook", {})
                    assert not result.isError
                    assert "Pan" in str(result.content)
                    session.adapter_config["plugin_bindings"] = [binding(mode="pan_native")]
                    assert (await client.call_tool("pan_handbook", {})).isError
                    assert {t.name for t in (await client.list_tools()).tools} == {"pan_sample_echo", "pan_sample_broken"}
        finally:
            await bridge.close()
    asyncio.run(run())


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_existing_http_and_sse_mcp_are_upstream_sources(tmp_path, transport):
    async def run():
        import socket
        import uvicorn
        from mcp.server.fastmcp import FastMCP
        upstream = FastMCP("http-source")
        @upstream.tool()
        def lookup(key: str) -> str:
            return "looked up: " + key
        app = upstream.streamable_http_app() if transport == "http" else upstream.sse_app()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}" + ("/mcp" if transport == "http" else "/sse")
        service = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        serving = asyncio.create_task(service.serve(sockets=[sock]))
        runtime = None
        try:
            for _ in range(200):
                if service.started:
                    break
                if serving.done():
                    await serving
                await asyncio.sleep(.01)
            assert service.started
            manifest = ManifestConfig(mcp_servers=[McpServer("remote", url=endpoint, transport=transport)])
            session = sess.Session(id="ses_http", name="http", adapter_config={"plugin_bindings": [binding("remote")]})
            runtime = Runtime(lambda: manifest, lambda _: session)
            assert (await runtime.directory(session.id, "gateway"))[0]["name"] == "remote.lookup"
            result = await runtime.invoke(session.id, "gateway", "remote.lookup", {"key": "source"})
            assert not result.get("isError")
            assert "looked up: source" in str(result["content"])
        finally:
            if runtime:
                await runtime.close()
            service.should_exit = True
            await serving
    asyncio.run(run())


@pytest.mark.parametrize("mode,view", [("gateway", "gateway"), ("pan_native", "native"), ("standalone", "standalone")])
def test_external_api_document_provider_all_modes(tmp_path, mode, view):
    async def run():
        doc = tmp_path / "api.json"
        doc.write_text(json.dumps({"protocol": "pan-capability/v1", "plugin_id": "lookup",
            "provider": {"transport": "stdio_jsonrpc", "command_ref": "lookup-process"},
            "capabilities": [{"id": "lookup.item", "export_key": "item", "description": "fixture lookup",
                "contract_version": "1.0.0", "input_schema": {"type": "object", "properties": {
                    "key": {"type": "string"}}, "required": ["key"], "additionalProperties": False},
                "execution": {"operation": "find"}}]}), encoding="utf-8")
        manifest = ManifestConfig(mcp_servers=[McpServer("lookup-process", command=sys.executable,
            args=[str(REPO_ROOT / "tests/fixtures/plugin_api_provider.py")])], plugins=[{
                "protocol": "pan-plugin/v1", "id": "lookup", "source": {"kind": "pan_api", "capabilities_file": str(doc)}}])
        session = sess.Session(id="ses_api", name="api", adapter_config={"plugin_bindings": [binding("lookup", mode)]})
        runtime = Runtime(lambda: manifest, lambda _: session)
        try:
            tools = await runtime.directory(session.id, view)
            assert tools[0]["name"] == "lookup.item"
            name = "pan_lookup_item" if view == "native" else "lookup.item"
            result = await runtime.invoke(session.id, view, name, {"key": "abc"}, plugin_id="lookup")
            assert result["structuredContent"] == {"session": "ses_api", "operation": "find", "arguments": {"key": "abc"}}
        finally:
            await runtime.close()
    asyncio.run(run())


def test_real_api_provider_disconnect_is_unknown_and_never_replayed(tmp_path):
    """An effect happened, but the process exited before confirming its result."""
    state = tmp_path / 'owned-effects.jsonl'
    doc = tmp_path / 'api.json'
    doc.write_text(json.dumps({'protocol': 'pan-capability/v1', 'plugin_id': 'disconnect',
        'provider': {'transport': 'stdio_jsonrpc', 'command_ref': 'owned-process'},
        'capabilities': [{'id': 'disconnect.effect', 'export_key': 'effect', 'description': 'owned effect',
            'contract_version': '1.0.0', 'input_schema': {'type': 'object'},
            'execution': {'operation': 'effect'}}]}), encoding='utf-8')
    manifest = ManifestConfig(mcp_servers=[McpServer('owned-process', command=sys.executable,
        args=[str(REPO_ROOT / 'tests/fixtures/provider_disconnect_probe.py'), str(state)])], plugins=[{
            'protocol': 'pan-plugin/v1', 'id': 'disconnect',
            'source': {'kind': 'pan_api', 'capabilities_file': str(doc)}}])
    session = sess.Session(id='ses_owned_disconnect', name='owned',
        adapter_config={'plugin_bindings': [binding('disconnect')]})

    async def run():
        runtime = Runtime(lambda: manifest, lambda _: session, data_root=tmp_path / 'runtime')
        request = {'session_id': session.id, 'operation': 'call', 'name': 'disconnect.effect', 'arguments': {}}
        try:
            result = await runtime.handle(request)
            assert result['isError'] and 'execution_unconfirmed' in str(result), result
            assert 'Do not automatically replay' in str(result)
            provider = runtime.providers[(session.id, 'disconnect')][1]
            process = provider.process
            assert await asyncio.wait_for(process.wait(), 5) == 23
            for _ in range(2):
                result = await runtime.handle(request)
                assert result['isError'] and 'execution_unconfirmed' in str(result), result
                assert 'explicit reconnect required' in str(result)
                assert provider.process is process
            assert len(state.read_text().splitlines()) == 1
            assert len(state.with_suffix('.starts').read_text().splitlines()) == 1
            print(json.dumps({'provider_pid': process.pid, 'exitcode': process.returncode,
                'effects': 1, 'starts': 1, 'unconfirmed': True, 'replay_blocked': True}))
        finally:
            await runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_existing_http_api_document_uses_only_its_declared_route(tmp_path, monkeypatch, method):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.reply({k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()})
        def do_POST(self):
            self.reply(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        def reply(self, arguments):
            calls.append((urlsplit(self.path).path, arguments, self.headers.get("X-Fixture")))
            raw = json.dumps({"found": arguments["key"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("PAN_TEST_API_KEY", "test-only-upstream-header")
    document = tmp_path / "http-capabilities.json"
    document.write_text(json.dumps({"protocol": "pan-capability/v1", "plugin_id": "lookup",
        "provider": {"transport": "http", "endpoint": f"http://127.0.0.1:{server.server_port}",
                     "headers_env": {"X-Fixture": "PAN_TEST_API_KEY"}},
        "capabilities": [{"id": "lookup.find", "export_key": "find", "description": "HTTP lookup fixture",
            "contract_version": "1.0.0", "input_schema": {"type": "object", "properties": {"key": {"type": "string"}},
                "required": ["key"], "additionalProperties": False}, "execution": {"method": method, "path": "/lookup"}}]}), encoding="utf-8")
    manifest = ManifestConfig(plugins=[{"protocol": "pan-plugin/v1", "id": "lookup",
        "source": {"kind": "pan_api", "capabilities_file": str(document)}}])
    session = sess.Session(id="ses_api_http", name="http", adapter_config={"plugin_bindings": [binding("lookup")]})
    async def run():
        runtime = Runtime(lambda: manifest, lambda _: session)
        try:
            result = await runtime.invoke(session.id, "gateway", "lookup.find", {"key": "abc"})
            assert result["structuredContent"] == {"found": "abc"}
            assert calls == [("/lookup", {"key": "abc"}, "test-only-upstream-header")]
            invalid = await runtime.invoke(session.id, "gateway", "lookup.find", {"key": "abc", "url": "https://different.example/"})
            assert invalid["isError"] and len(calls) == 1
        finally:
            await runtime.close()
    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
