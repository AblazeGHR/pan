"""R6-C production routing, tools/list/call and source-removal acceptance."""
import asyncio
import ast
import json
import os
from pathlib import Path
import sys

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

from packages.mcp import server as mcp_server
from scripts.audit_scheduler_surface import effective_routes, snapshot
from tests.test_scheduler_api import contents, scheduler_env  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
REMOVED_TOOLS = (
    'scheduler_create', 'scheduler_update', 'scheduler_delete',
    'scheduler_pause', 'scheduler_resume', 'scheduler_run_now',
)
READ_TOOLS = {'scheduler_list', 'scheduler_get', 'scheduler_runs'}


def test_production_route_and_catalog_snapshot():
    assert snapshot() == json.loads((ROOT / 'tests/fixtures/scheduler_surface_catalog.json').read_text())
    from packages.web.server import app
    routes = list(effective_routes(app))
    assert not any('owner-switch' in getattr(r, 'path', '') for r in routes)
    for path, methods in app.openapi()['paths'].items():
        assert 'owner-switch' not in path
        if path.startswith('/api/scheduler/'):
            assert set(methods) <= {'get', 'post', 'patch', 'put', 'delete'}


@pytest.mark.parametrize('identity', [None, 'ses_owned'])
@pytest.mark.parametrize('name', REMOVED_TOOLS)
def test_removed_mcp_names_are_absent_and_unknown_before_any_transport(monkeypatch, name, identity):
    if identity:
        monkeypatch.setenv('PAN_AGENT_SESSION_ID', identity)
    else:
        monkeypatch.delenv('PAN_AGENT_SESSION_ID', raising=False)
    calls = []
    monkeypatch.setattr(mcp_server, '_api', lambda *a, **kw: calls.append((a, kw)))
    assert name not in vars(mcp_server)
    assert '_scheduler_task_call' not in vars(mcp_server)
    async def check():
        tools = {t.name for t in await mcp_server.mcp.list_tools()}
        assert tools & set(REMOVED_TOOLS) == set()
        assert {n for n in tools if n.startswith('scheduler_')} == READ_TOOLS
        with pytest.raises(ToolError, match='Unknown tool: ' + name):
            await mcp_server.mcp.call_tool(name, {'task_id': 'sch_old'})
    asyncio.run(check())
    assert calls == []


@pytest.mark.parametrize('method', ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
def test_removed_owner_switch_is_404_with_no_data_or_broadcast_effect(scheduler_env, method):
    from packages.scheduler import api
    client, task, root, headers = scheduler_env
    before, events = contents(root), []
    api.bind(events.append)
    for auth in ({}, headers):
        response = client.request(method, '/api/scheduler/tasks/' + task['id'] + '/owner-switch',
                                  headers=auth, json={})
        assert response.status_code == 404, response.text
    assert contents(root) == before and events == []
    assert 'switch_owner' not in vars(api) and '_switch' not in vars(api)


def test_production_owner_switch_is_not_a_catchall_or_an_alias(tmp_path):
    from packages.web.server import app
    from packages.automation.authorization import _MARKER, _SCOPE_KEY
    before = contents(tmp_path)
    async def scoped(scope, receive, send):
        scope[_SCOPE_KEY] = _MARKER
        await app(scope, receive, send)
    async def check():
        for target in (app, scoped):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url='http://ipc') as client:
                response = await client.post('/api/scheduler/tasks/sch_unknown/owner-switch', json={})
                assert response.status_code == 404, response.text
    asyncio.run(check())
    assert contents(tmp_path) == before


def test_no_removed_function_can_register_during_import():
    # Deleting a registry entry after import would leave a callable write body.
    # Pin actual source deletion as well as runtime tools/list/call.
    tree = ast.parse((ROOT / 'packages/mcp/server.py').read_text(encoding='utf-8'))
    definitions = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not definitions & set(REMOVED_TOOLS)
    assert '_scheduler_task_call' not in definitions


@pytest.mark.parametrize('projection', ['core', 'native'])
def test_real_stdio_tools_list_and_unknown_calls_without_http(projection, tmp_path):
    from packages.core.manifest_loader import ManifestConfig, McpServer
    from packages.core.session import Session
    from packages.plugins.ipc import Bridge
    from packages.plugins.runtime import Runtime
    args = ['-m', 'packages.mcp.server'] if projection == 'core' else [
        '-m', 'packages.plugins.mcp', '--view', 'native', '--core']
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PAN_')}
    # The API endpoint is intentionally unreachable. Only owned local IPC is
    # available to the native catalog; no HTTP listener or lifespan is started.
    env['PAN_API_URL'] = 'http://127.0.0.1:1'
    parameters = StdioServerParameters(command=sys.executable, args=args, cwd=str(ROOT), env=env)
    async def check():
        bridge = None
        if projection == 'native':
            session = Session(id='ses_r6c', name='owned test', adapter_config={
                'plugin_bindings': [{'plugin_id': 'pan', 'mode': 'standalone', 'enabled': True}]})
            manifest = ManifestConfig(mcp_servers=[McpServer('pan', command=sys.executable,
                                                            args=['-m', 'packages.mcp.server'])])
            runtime = Runtime(lambda: manifest, lambda sid: session if sid == session.id else None,
                              data_root=tmp_path / 'plugins')
            bridge = Bridge(runtime, tmp_path)
            parameters.env.update(PAN_PLUGIN_IPC=bridge.address, PAN_AGENT_SESSION_ID=session.id)
        try:
            async with stdio_client(parameters) as streams, ClientSession(*streams) as client:
                await client.initialize()
                names = [t.name for t in (await client.list_tools()).tools]
                expected = json.loads((ROOT / 'tests/fixtures/scheduler_surface_catalog.json').read_text())['tools']
                assert sorted(names) == expected
                assert len(names) == len(set(names))
                before = contents(tmp_path)
                for name in REMOVED_TOOLS:
                    result = await client.call_tool(name, {'task_id': 'sch_old'})
                    assert result.isError is True
                    if projection == 'core':
                        assert 'Unknown tool: ' + name in result.content[0].text
                    else:
                        # Native projection rejects it in its exact directory
                        # before any plugin or HTTP handler can execute.
                        detail = json.loads(result.content[0].text)
                        assert detail['error']['code'] == 'tool_unavailable'
                        assert detail['error']['message'] == 'Tool is not enabled in this Session projection'
                assert contents(tmp_path) == before
        finally:
            if bridge:
                await bridge.close()
    asyncio.run(check())
