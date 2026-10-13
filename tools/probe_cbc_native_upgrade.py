"""Opt-in real hy3 acceptance: ordinary persistent CBC -> native TUI -> ordinary.

Only a new official CODEBUDDY_CONFIG_DIR is used for history/configuration.
Normal CLI authentication discovery is retained; credentials are never read or
copied by this probe. HOME and user trustedDirectories are untouched. Three
model turns, no tools, no full Pan service lifespan, no external MCP.
"""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import uuid
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.core import session, worker
from packages.core.adapters.cbc.adapter import CbcAdapter
from packages.core.adapters.cbc.native_adapter import CbcNativeAdapter
from packages.web import native_control_api
from tools.probe_cbc_native_worker import kernel_identity, until
from fastapi import FastAPI
import httpx
import uvicorn


async def probe():
    for key in list(os.environ):
        if key.startswith('PAN_'):
            os.environ.pop(key, None)
    owned = Path(tempfile.mkdtemp(prefix='pan-cbc-upgrade-real-'))
    profile = owned / 'cli-profile'
    profile.mkdir()
    cwd = owned / 'work'
    cwd.mkdir()
    os.environ.update(CODEBUDDY_CONFIG_DIR=str(profile),
        CODEBUDDY_PROJECT_CONFIG_DIR=str(cwd / 'project-config'),
        CODEBUDDY_DISABLE_SESSION_HISTORY_CLEANUP='1', XDG_CACHE_HOME=str(owned / 'cache'))
    session.SESSION_DIR = owned / 'pan-sessions'
    session._cache.clear()
    session._all_loaded = True
    worker._DEFAULTS_INITIALIZED = True
    worker._WORKER_IDLE_SEC = 600
    # Test-only isolation arguments. Production build_spawn_args/resume_args,
    # configure, idle coordinator, and create_worker are used unchanged.
    CbcAdapter.mcp_args = lambda self, s: ['--strict-mcp-config', '--mcp-config',
        '{"mcpServers":{}}', '--setting-sources', '', '--tools', '', '--settings',
        '{"trustAll":true,"autoUpdates":false,"alwaysThinkingEnabled":false}']
    CbcAdapter.enrich_after_result = lambda self, s: None
    native_control_api._enter = lambda request: (None, None)
    events = []
    async def broadcast(event):
        events.append(event)
    worker._bcast = broadcast
    original_marker = 'CBC-ORIGINAL-' + uuid.uuid4().hex
    native_marker = 'CBC-NATIVE-' + uuid.uuid4().hex
    sid = 'ses-cbc-upgrade-' + uuid.uuid4().hex
    s = session.Session(id=sid, name='isolated persistent CBC upgrade proof', adapter='cbc',
        model='hy3', permission_mode='bypassPermissions', workdir=str(cwd),
        adapter_config={'output_mode': 'stream'})
    session._cache[sid] = s
    session.save(s)
    app = FastAPI()
    app.include_router(native_control_api.router)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    assert port not in (8767, 8768)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
        lifespan='off', access_log=False, log_level='warning'))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    facts = {'candidate': __import__('subprocess').check_output(['git', 'rev-parse', 'HEAD'],
        cwd=ROOT, text=True).strip(), 'profile': str(profile), 'evidence_root': str(owned),
        'model': 'hy3', 'planned_provider_turns': 3, 'credits': 'not independently measured'}
    processes = []

    def track(pid):
        process = psutil.Process(pid)
        processes.append((process, process.create_time()))

    def live():
        return worker.find_alive_worker_by_session(sid)

    async def idle():
        await until(lambda: live() is not None and live().status in ('idle', 'queued')
            and not live()._queue_unit_active and not live()._terminal_finishing)

    def own_history(cli_id):
        # Installed 2.164.0 persists the legacy flat project transcript;
        # newer session storage also has a sessions/ layout. Both are confined
        # to this probe's official config root and exact CLI Session identity.
        files = list((profile / 'projects').glob('*/' + cli_id + '.jsonl'))
        files += list((profile / 'projects').glob('*/sessions/' + cli_id + '.jsonl'))
        assert len(files) == 1, 'CLI did not persist in the official dedicated profile'
        return files[0]

    async def configure(client, enabled):
        old = live()
        response = await client.post(f'/api/sessions/{sid}/native-control/configuration',
                                     json={'enabled': enabled})
        assert response.status_code == 200, response.text
        await until(lambda: worker.delayed_restart_state(sid)['status'] in ('completed', 'failed'), limit=40)
        state = worker.delayed_restart_state(sid)
        assert state['status'] == 'completed', state
        replacement = live()
        assert replacement is not old and old.process.returncode is not None
        assert s.cli_session_id == cli_id
        assert s.history[:len(history_before)] == history_before
        assert s.queue_pending == queue_before and s.queue_paused
        track(replacement.process.pid)
        return replacement

    try:
        print(json.dumps({'phase': 'isolated_profile_ready', **facts}), flush=True)
        await until(lambda: server.started, limit=10)
        first = await worker.create_worker(sid)
        assert isinstance(first, worker.Worker) and type(first.adapter) is CbcAdapter
        track(first.process.pid)
        response = await worker.send(first.worker_id,
            f'Remember original_marker={original_marker} for later. Reply exactly STORED. Do not use tools.', source='user')
        assert response['status'] == 'queued', response
        await until(lambda: s.result_cursor == 1, limit=70)
        await idle()
        assert s.last_result['status'] == 'done' and s.last_result['result'].strip() == 'STORED', s.last_result
        cli_id = s.cli_session_id
        assert cli_id and original_marker in own_history(cli_id).read_text(encoding='utf-8')
        facts.update(cli_session_id=cli_id, initial_worker=first.worker_id, initial_pid=first.process.pid,
                     initial_persistent_history=str(own_history(cli_id)), completed_turns=s.result_cursor)
        print(json.dumps({'phase': 'ordinary_history_persisted', **facts}), flush=True)
        # Pausing/queuing modifies only this dedicated Pan Session. Conversion
        # and execution then run the actual production coordinator/consumer.
        s.queue_paused = True
        await session.save_async(s)
        queued = await worker.send(first.worker_id,
            'Return original_marker and native_marker from earlier conversation, separated by one space. '
            'Reply their values only. Do not use tools or read files.', source='user')
        assert queued['status'] == 'queued', queued
        await idle()
        queue_before = copy.deepcopy(s.queue_pending)
        history_before = copy.deepcopy(s.history)
        assert len(queue_before) == 1
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=40) as client:
            native = await configure(client, True)
            assert type(native.adapter) is CbcNativeAdapter
            await until(lambda: any(e.get('workerId') == native.worker_id and
                e.get('generation') == native.generation and
                e.get('type') == 'worker.stream' and e.get('event', {}).get('subtype') == 'init'
                for e in events), limit=35)
            argv = json.loads(native.adapter.build_spawn_args(s)[-1])
            assert argv[argv.index('--resume') + 1] == cli_id
            assert original_marker not in json.dumps(argv)
            identity = kernel_identity(native.process.pid)
            track(identity[0])
            response = await client.post(f'/api/sessions/{sid}/native-control/mode', json={'mode': 'native'})
            assert response.status_code == 200, response.text
            state = response.json()['result']
            prompt = ('Reply only the exact original_marker I supplied in the earlier conversation. '
                      f'Also remember native_marker={native_marker} for the next turn. Do not use tools.')
            async def write(text):
                result = await client.post(f'/api/sessions/{sid}/native-control/write', json={
                    'workerId': native.worker_id, 'generation': state['generation'], 'text': text})
                assert result.status_code == 200, result.text
            await write('\x1b[200~' + prompt + '\x1b[201~')
            await asyncio.sleep(.2)  # Existing native paste/Enter scheduling contract.
            await write('\r')
            await until(lambda: s.result_cursor == 2, limit=70)
            assert s.last_result['status'] == 'done' and s.last_result['result'].strip() == original_marker, s.last_result
            assert s.cli_session_id == cli_id and s.queue_pending == queue_before
            assert native_marker in own_history(cli_id).read_text(encoding='utf-8')
            for mode in ['pan', 'native', 'pan']:
                result = await client.post(f'/api/sessions/{sid}/native-control/mode', json={'mode': mode})
                assert result.status_code == 200, result.text
                assert kernel_identity(native.process.pid) == identity
            facts.update(native_context_retained=True, native_kernel_pid=identity[0],
                         same_kernel_after_switch=True, history_and_queue_preserved=True,
                         completed_turns=s.result_cursor)
            print(json.dumps({'phase': 'native_resume_and_switch_verified', **facts}), flush=True)
            await idle()
            ordinary = await configure(client, False)
            assert type(ordinary.adapter) is CbcAdapter and not ordinary.native_control_enabled
            assert s.cli_session_id == cli_id
            s.queue_paused = False
            await session.save_async(s)
            ordinary.pending_signal.put_nowait({'type': 'queue_signal'})
            await until(lambda: s.result_cursor == 3, limit=70)
            await idle()
            assert s.last_result['status'] == 'done'
            assert s.last_result['result'].strip() == original_marker + ' ' + native_marker, s.last_result
            assert not s.queue_pending and s.cli_session_id == cli_id
            facts.update(passed=True, downgrade_context_retained=True, queued_task_consumed_once=True,
                         terminal_results=s.result_cursor, completed_turns=s.result_cursor,
                         final_worker=ordinary.worker_id, final_pid=ordinary.process.pid)
            print(json.dumps({'phase': 'upgrade_downgrade_acceptance_complete', **facts}), flush=True)
    finally:
        worker._shutdown_started = True
        try:
            current = worker.find_worker_by_session(sid)
            if current:
                await worker.kill_worker(current.worker_id)
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(serving, timeout=10)
            finally:
                listener.close()
        alive = []
        for process, born in processes:
            try:
                if process.is_running() and process.create_time() == born:
                    alive.append(process.pid)
            except psutil.NoSuchProcess:
                pass
        assert not alive, f'owned runtime cleanup unconfirmed: {alive}'
        facts['owned_processes_retired'] = True
        (owned / 'evidence.json').write_text(json.dumps(facts, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-hy3', action='store_true', required=True)
    parser.parse_args()
    asyncio.run(probe())
