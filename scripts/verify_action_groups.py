"""Owned real HTTP + production browser + stdio MCP probe; no model or deployed service.

Run after the web production build with scripts/run_tests_isolated.py.
Only the plugin runtime lifespan is started: no existing gateways/controllers.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


async def main():
    import httpx
    import uvicorn
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from packages.core import config, session, workspace, background_jobs, session_drafts
    from packages.core.terminal import registry
    from packages.scheduler import store as scheduler

    runtime = REPO / '.validation' / 'action-groups' / ('live-' + __import__('uuid').uuid4().hex[:10])
    runtime.mkdir(parents=True)
    config.CONFIG_FILE = runtime / 'config.json'
    config.CONFIG_FILE.write_text(json.dumps({'plugin_manifests': [], 'scheduler': {'enabled': False}}), encoding='utf-8')
    session.SESSION_DIR = runtime / 'data' / 'sessions'
    session_drafts.DRAFT_DIR = runtime / 'data' / 'drafts'
    workspace.WORKSPACE_DIR = runtime / 'data' / 'workspaces'
    background_jobs.DEFAULT_ROOT = runtime / 'data' / 'background_jobs'
    scheduler.DEFAULT_ROOT = background_jobs.DEFAULT_ROOT
    scheduler.LEGACY_TASKS_ROOT = runtime / 'data' / 'scheduler'
    registry.DEFAULT_REGISTRY_ROOT = runtime / 'data' / 'terminals'
    from packages.web import server as web
    from packages.core.character import CharacterManager
    from packages.core.adapters.mcp import build_mcp_servers
    from packages.plugins.ipc import plugin_lifespan

    web.DATA_DIR = runtime / 'data'
    web.WORKDIRS_DIR = runtime / 'workdirs'
    web.ATTACHMENTS_DIR = runtime / 'attachments'
    for path in (web.DATA_DIR, web.WORKDIRS_DIR, web.ATTACHMENTS_DIR):
        path.mkdir(parents=True)
    manager = CharacterManager(str(web.DATA_DIR))
    manager.load_manifest([])
    web._character_manager = manager

    @asynccontextmanager
    async def lifespan(app):
        async with plugin_lifespan(web._plugin_manifest, session.get, web.DATA_DIR):
            yield
    web.app.router.lifespan_context = lifespan
    ready = asyncio.Event()

    class OwnedServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            ready.set()

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    base = f'http://127.0.0.1:{port}'
    os.environ['PAN_API_URL'] = base
    os.environ['PAN_PORT'] = str(port)
    server = OwnedServer(uvicorn.Config(web.app, log_level='warning', access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    await asyncio.wait_for(ready.wait(), 15)
    proof = {'checkout': str(REPO), 'pid': os.getpid(), 'port': port, 'dataRoot': str(web.DATA_DIR),
             'runtime': str(runtime), 'mcp': [], 'browser': None}
    try:
        async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=15) as client:
            async def request(method, path, value=None):
                result = await client.request(method, path, **({'json': value} if value is not None else {}))
                result.raise_for_status()
                return result.json()

            read = {'id': 'list_sessions', 'name': 'List Sessions', 'description': 'List isolated Sessions', 'enabled': True,
                'effect': 'read', 'output_format': 'json', 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                'execution': {'kind': 'api', 'method': 'GET', 'path': '/api/sessions'}}
            echo = runtime / 'echo.py'
            echo.write_text('import sys,json\nprint(json.dumps({"value":sys.argv[1]}))\n', encoding='utf-8')
            command = {**read, 'id': 'echo', 'name': 'Echo', 'description': 'Echo an argv value', 'effect': 'write_local',
                'input_schema': {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value'], 'additionalProperties': False},
                'execution': {'kind': 'command', 'executable': sys.executable, 'cwd': str(runtime), 'args': [str(echo), '{value}']}}
            for action in (read, command):
                await request('PUT', '/api/actions/actions/' + action['id'], {'item': action})
            await request('PUT', '/api/actions/groups/probe', {'item': {'id': 'probe', 'name': 'Probe Tools', 'action_ids': ['list_sessions', 'echo']}})
            built = await request('POST', '/api/actions/groups/probe/build', {})
            pid = built['groups'][0]['build']['plugin_id']
            for mode, view in [('standalone', 'standalone'), ('pan_native', 'native'), ('gateway', 'gateway')]:
                owner = session.create(name='Probe ' + mode, adapter='codex', workdir=str(runtime))
                await request('PATCH', '/api/sessions/' + owner.id, {'pluginBindings': [{'plugin_id': pid, 'enabled': True, 'mode': mode}]})
                # Launch exactly the generic adapter descriptor a Worker receives.
                descriptors = build_mcp_servers(owner)
                server_name = pid if view == 'standalone' else 'pan' if view == 'native' else 'pan-tools'
                entry = descriptors[server_name]
                assert entry['env']['PAN_AGENT_SESSION_ID'] == owner.id
                params = StdioServerParameters(command=entry['command'], args=entry.get('args', []),
                    cwd=entry.get('cwd'), env={**os.environ, **entry.get('env', {})})
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as mcp:
                        await mcp.initialize()
                        tools = await mcp.list_tools()
                        if view == 'gateway':
                            assert {t.name for t in tools.tools} == {'api_discover', 'api_call'}
                            discovered = await mcp.call_tool('api_discover', {'include_schema': True})
                            assert not discovered.isError
                        for aid, args in [('list_sessions', {}), ('echo', {'value': 'one & two; $(no-shell)'})]:
                            canonical = pid + '.' + aid
                            name = ('pan_' + pid.replace('-', '_') + '_' + aid) if view == 'native' else canonical
                            result = await mcp.call_tool('api_call' if view == 'gateway' else name,
                                {'name': canonical, 'arguments': args} if view == 'gateway' else args)
                            assert not result.isError, result
                            if aid == 'echo':
                                assert result.structuredContent == {'value': args['value']}
                            else:
                                assert any(s['id'] == owner.id for s in result.structuredContent['sessions'])
                            proof['mcp'].append({'mode': mode, 'action': aid, 'ok': True})
                proof['browserSessionId'] = owner.id

            browser = await asyncio.create_subprocess_exec('node', str(REPO / 'packages/web/e2e/action-groups.e2e.mjs'),
                cwd=str(REPO / 'packages/web'), env={**os.environ, 'ACTION_PROBE_URL': base,
                    'ACTION_PROBE_RUNTIME': str(runtime), 'ACTION_PROBE_SESSION': proof['browserSessionId']},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            output, _ = await asyncio.wait_for(browser.communicate(), 90)
            (runtime / 'browser.log').write_bytes(output)
            assert browser.returncode == 0, output.decode(errors='replace')
            proof['browser'] = json.loads((runtime / 'browser.json').read_text())
            await request('POST', '/api/actions/groups/probe/unbuild', {})
            assert not (await request('GET', '/api/actions'))['groups'][0].get('build')
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        sock.close()
        proof['serverStopped'] = True
        (runtime / 'result.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
        (REPO / '.validation/action-groups/latest-live.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
