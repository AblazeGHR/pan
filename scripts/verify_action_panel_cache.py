"""Owned isolated HTTP + production browser probe for the Action Panel cache.

Run after `pnpm build` with `scripts/run_tests_isolated.py`. It starts only an
owned uvicorn instance on an ephemeral port (never 8767/8768), writes only under
a task-owned `.validation` runtime, creates idle Sessions without Workers, and
launches the production React bundle in Chromium.

It does not call a model, build a real Worker, touch user data or operate any
existing Pan service.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import sys
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


async def main():
    import httpx
    import uvicorn
    from packages.core import config, session, workspace, background_jobs, session_drafts
    from packages.core.terminal import registry
    from packages.scheduler import store as scheduler

    runtime = REPO / '.validation' / 'action-panel-cache' / ('live-' + uuid.uuid4().hex[:10])
    runtime.mkdir(parents=True)
    config.CONFIG_FILE = runtime / 'config.json'
    config.CONFIG_FILE.write_text(
        json.dumps({'plugin_manifests': [], 'scheduler': {'enabled': False}}), encoding='utf-8')
    session.SESSION_DIR = runtime / 'data' / 'sessions'
    session_drafts.DRAFT_DIR = runtime / 'data' / 'drafts'
    workspace.WORKSPACE_DIR = runtime / 'data' / 'workspaces'
    background_jobs.DEFAULT_ROOT = runtime / 'data' / 'background_jobs'
    scheduler.DEFAULT_ROOT = background_jobs.DEFAULT_ROOT
    scheduler.LEGACY_TASKS_ROOT = runtime / 'data' / 'scheduler'
    registry.DEFAULT_REGISTRY_ROOT = runtime / 'data' / 'terminals'

    from packages.web import server as web
    from packages.core.character import CharacterManager
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

    proof = {
        'checkout': str(REPO),
        'pid': os.getpid(),
        'port': port,
        'dataRoot': str(web.DATA_DIR),
        'runtime': str(runtime),
        'browser': None,
    }
    try:
        async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=15) as client:
            async def request(method, path, value=None):
                result = await client.request(method, path, **({'json': value} if value is not None else {}))
                result.raise_for_status()
                return result.json()

            read = {
                'id': 'list_sessions', 'name': 'List Sessions',
                'description': 'List isolated Sessions with adapter and workspace metadata',
                'enabled': True, 'effect': 'read', 'output_format': 'json',
                'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                'execution': {'kind': 'api', 'method': 'GET', 'path': '/api/sessions'},
            }
            echo_file = runtime / 'echo.py'
            echo_file.write_text('import sys,json\nprint(json.dumps({"value":sys.argv[1]}))\n', encoding='utf-8')
            command = {
                **read, 'id': 'echo', 'name': 'Echo', 'description': 'Echo an argv value',
                'effect': 'write_local',
                'input_schema': {'type': 'object', 'properties': {'value': {'type': 'string'}},
                                 'required': ['value'], 'additionalProperties': False},
                'execution': {'kind': 'command', 'executable': sys.executable, 'cwd': str(runtime),
                              'args': [str(echo_file), '{value}'], 'timeout_sec': 15},
            }
            draft_probe = {
                **read, 'id': 'draft_probe', 'name': 'Draft Probe',
                'description': 'A disabled draft for build-gate review', 'enabled': False,
            }
            for action in (read, command, draft_probe):
                await request('PUT', '/api/actions/actions/' + action['id'], {'item': action})

            await request('PUT', '/api/actions/groups/probe', {
                'item': {'id': 'probe', 'name': 'Probe Tools', 'action_ids': ['list_sessions', 'echo']},
            })
            built = await request('POST', '/api/actions/groups/probe/build', {})
            probe_build = built['groups'][0]['build']
            await request('PUT', '/api/actions/groups/release_tools', {
                'item': {'id': 'release_tools', 'name': 'Release Tools', 'action_ids': ['list_sessions']},
            })
            await request('PUT', '/api/actions/groups/draft_tools', {
                'item': {'id': 'draft_tools', 'name': 'Draft Tools', 'action_ids': ['list_sessions', 'draft_probe']},
            })

            first = session.create(name='First', adapter='codex', workdir=str(runtime))
            second = session.create(name='Second', adapter='codex', workdir=str(runtime))
            # Existing binding used by the UI to prove that unrelated bindings are preserved.
            await request('PATCH', '/api/sessions/' + first.id, {
                'pluginBindings': [{'plugin_id': probe_build['plugin_id'], 'enabled': True, 'mode': 'pan_native'}],
            })

            env = {
                **os.environ,
                'PANEL_PROBE_URL': base,
                'PANEL_PROBE_RUNTIME': str(runtime),
                'PANEL_PROBE_SESSION1': first.id,
                'PANEL_PROBE_SESSION2': second.id,
                'PANEL_PROBE_PLUGIN': probe_build['plugin_id'],
            }
            browser = await asyncio.create_subprocess_exec(
                'node', str(REPO / 'packages/web/e2e/action-panel-cache.e2e.mjs'),
                cwd=str(REPO / 'packages/web'), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            output, _ = await asyncio.wait_for(browser.communicate(), 240)
            (runtime / 'browser.log').write_bytes(output)
            if browser.returncode != 0:
                raise RuntimeError(output.decode(errors='replace'))
            proof['browser'] = json.loads((runtime / 'cache.json').read_text(encoding='utf-8'))
            proof['probePlugin'] = probe_build['plugin_id']
            proof['sessions'] = {'first': first.id, 'second': second.id}
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        sock.close()
        proof['serverStopped'] = True
        (runtime / 'result.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
        (REPO / '.validation/action-panel-cache/latest.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
