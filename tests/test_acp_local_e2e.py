"""Owned Pan process, dedicated Windows pipe, stdio CLI and synthetic provider."""
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading

import httpx
import pytest

from packages.acp.local import StateStore, initialize, request, _pipe_name, _read_frame, _write_frame
from packages.acp.policy import validate
from packages.acp.service import Service
from packages.automation.auth import UserAuth
from tests import test_real_queue_http_e2e as h


@pytest.mark.skipif(os.name != 'nt', reason='DPAPI state store is Windows-only')
def test_detached_receipts_survive_restart_without_occupying_pool(tmp_path):
    import asyncio
    async def run():
        data, workspace = tmp_path / 'data', tmp_path / 'workspace'
        data.mkdir(); workspace.mkdir()
        initialize(data, 'editor', 'default', workspace)
        profile = validate('editor', {'enabled':True, 'template':'default', 'workspaceRoots':[str(workspace)]})
        store = StateStore(data)
        calls = []
        async def create(*_): return 's' + str(len(calls))
        async def enqueue(*_): calls.append(1); return 'q'
        def recover(*_): return {'state':'terminal', 'status':'done'}
        def make():
            return Service(lambda _:profile, create, enqueue, recover=recover,
                           save=store.save, remove=store.remove, load=store.load)
        service, handles = make(), []
        for n in range(130):
            handle = service.prepare('editor', str(workspace))
            await service.submit('editor', handle, str(n), 'synthetic')
            service.abandon('editor', handle)
            handles.append(handle)
        assert not service.contexts and len(calls) == 130
        restarted = make()
        for n, handle in enumerate(handles):
            assert restarted.recover('editor', handle, str(n))['state'] == 'terminal'
        assert not restarted.contexts and len(calls) == 130
        restarted.prepare('editor', str(workspace))
    asyncio.run(run())


@pytest.mark.skipif(os.name != 'nt', reason='dedicated ACP transport currently Windows-only')
def test_local_stdio_two_turns(tmp_path, monkeypatch):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    assert port not in (8767, 8768)
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', h.ROOT / 'tests/support/automation_http_server.py')
    root, workspace = tmp_path / 'owned', tmp_path / 'workspace'
    data = root / 'data'
    data.mkdir(parents=True)
    workspace.mkdir()
    (data / 'config.json').write_text(json.dumps({'qq': {'enabled': False}, 'wechat': {'enabled': False},
        'remote': {'enabled': False}, 'tailscale': {'startupEnabled': False}, 'lan': {'startupEnabled': False}}))
    auth = UserAuth(data / 'automation')
    password = 'Aa1!' + secrets.token_urlsafe(24)
    auth.initialize(password)
    admin_token = auth.login(password)
    initialize(data, 'editor', 'default', workspace)
    initialize(data, 'other', 'default', workspace)
    runtime = h._PanRuntime(data)
    child = None
    try:
        runtime.start()
        early = request(data, 'editor', 'prepare', {'cwd':str(workspace)})['handle']
        request(data, 'editor', 'cancel', {'handle':early, 'turnId':'before-submit'})
        request(data, 'editor', 'submit', {'handle':early, 'turnId':'before-submit', 'text':'never-enqueue'})
        assert request(data, 'editor', 'poll', {'handle':early, 'turnId':'before-submit', 'cursor':0})['status'] == 'cancelled'
        request(data, 'editor', 'finish', {'handle':early, 'turnId':'before-submit', 'cursor':0})
        request(data, 'editor', 'release', {'handle':early})
        child = subprocess.Popen([sys.executable, '-m', 'packages.core.cli', '--root', str(root),
                                  'acp', '--profile', 'editor'], cwd=str(h.ROOT),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = queue.Queue()
        def read():
            for line in child.stdout:
                output.put(json.loads(line))
        threading.Thread(target=read, daemon=True).start()
        def send(message):
            child.stdin.write((json.dumps(message) + '\n').encode())
            child.stdin.flush()
        def recv():
            return output.get(timeout=25)
        send({'jsonrpc':'2.0', 'id':1, 'method':'initialize', 'params':{'protocolVersion':1}})
        assert recv()['result']['protocolVersion'] == 1
        send({'jsonrpc':'2.0', 'id':2, 'method':'session/new',
              'params':{'cwd':str(workspace), 'mcpServers':[]}})
        session = recv()['result']['sessionId']
        assert not list((data / 'sessions').glob('*.json'))
        for n in (1, 2):
            send({'jsonrpc':'2.0', 'id':2+n, 'method':'session/prompt',
                  'params':{'sessionId':session, 'prompt':[{'type':'text', 'text':f'owned-{n}'}]}})
            chunks = []
            while True:
                item = recv()
                if item.get('method') == 'session/update':
                    chunks.append(item['params']['update']['content']['text'])
                else:
                    assert item == {'jsonrpc':'2.0', 'id':2+n, 'result':{'stopReason':'end_turn'}}
                    break
            assert ''.join(chunks) == f'ack:owned-{n}'
        assert len(list((data / 'sessions').glob('*.json'))) == 1
        if os.environ.get('ACP_SDK_TEST') == '1':
            env = dict(os.environ, UV_CACHE_DIR='D:/uv/cache', PYTHONPATH=str(h.ROOT))
            result = subprocess.run(['uv', 'run', '--no-project', '--with', 'agent-client-protocol==1.0.0',
                                     'python', str(h.ROOT / 'tests/support/acp_sdk_client.py'),
                                     sys.executable, str(root), str(workspace)],
                                    cwd=workspace, env=env, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, result.stderr
            assert 'ACP Python SDK two-turn fixture passed' in result.stdout
        if os.environ.get('ACP_TCK_TEST') == '1':
            env = dict(os.environ, UV_CACHE_DIR='D:/uv/cache', PYTHONPATH=str(h.ROOT),
                       TMP=str(workspace), TEMP=str(workspace))
            cmd = ['uvx', '--from',
                   'git+https://github.com/agentclientprotocol/acp-tck@7c71c4d1af0994e73bdc100c9f7f6b98f2319cda',
                   'acp-tck', '--agent-cwd', str(workspace), '--timeout', '8', '--test-timeout', '15',
                   '-k', 'initialize or test_text_only_prompt_resolves or test_updates_validate',
                   '--', sys.executable, '-m', 'packages.core.cli', '--root', str(root),
                   'acp', '--profile', 'editor']
            tck = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True, timeout=100)
            print('ACP TCK selected initialize and prompt exit:', tck.returncode)
            print(tck.stdout[-12000:])
            print(tck.stderr[-2000:])
            assert tck.returncode == 1  # Scoped TCK runs remain NOT CONFORMANT.
            assert '9 passed, 141 deselected' in tck.stdout
        recovery_handle = request(data, 'editor', 'prepare', {'cwd':str(workspace)})['handle']
        request(data, 'editor', 'submit', {'handle':recovery_handle, 'turnId':'receipt-check', 'text':'receipt-check'})
        state_bytes = (data / 'acp' / ('state-' + recovery_handle + '.bin')).read_bytes()
        assert b'receipt-check' not in state_bytes and b'credential' not in state_bytes
        def recovered():
            result = request(data, 'editor', 'recover', {'handle':recovery_handle, 'turnId':'receipt-check'})
            return result if result.get('state') == 'terminal' else None
        terminal = h._wait_for(recovered, timeout=25, label='matching durable ACP receipt')
        assert terminal['status'] == 'done' and terminal['executionSeq'] > 0
        assert 'result' not in terminal
        runtime.stop()
        runtime.start()
        assert recovered() == terminal
        # Output was not durably acknowledged across the service restart;
        # a matching terminal must not be promoted to a complete ACP stream.
        with pytest.raises(RuntimeError):
            request(data, 'editor', 'poll', {'handle':recovery_handle, 'turnId':'receipt-check', 'cursor':0})
        assert len(h._received(runtime, cwd=str(workspace), text='receipt-check')) == 1
        cancel_handle = request(data, 'editor', 'prepare', {'cwd':str(workspace)})['handle']
        request(data, 'editor', 'submit', {'handle':cancel_handle, 'turnId':'warmup', 'text':'warmup'})
        def finished():
            page = request(data, 'editor', 'poll', {'handle':cancel_handle, 'turnId':'warmup', 'cursor':0})
            return page if page['status'] else None
        page = h._wait_for(finished, timeout=25, label='ACP warmup completion')
        request(data, 'editor', 'finish', {'handle':cancel_handle, 'turnId':'warmup', 'cursor':page['cursor']})
        store = StateStore(data)
        state = json.loads(store.protector.unprotect(store.path(cancel_handle).read_bytes()))
        with httpx.Client(base_url=runtime.base_url, headers={'Authorization':'Bearer ' + admin_token},
                          trust_env=False, timeout=10) as admin:
            paused = admin.patch('/api/sessions/' + state['sessionId'] + '/queue/paused', json={'paused':True})
            assert paused.status_code == 200 and paused.json()['ok'] is True
        request(data, 'editor', 'submit', {'handle':cancel_handle, 'turnId':'withdraw', 'text':'not-delivered'})
        request(data, 'editor', 'cancel', {'handle':cancel_handle, 'turnId':'withdraw'})
        assert request(data, 'editor', 'poll', {'handle':cancel_handle, 'turnId':'withdraw', 'cursor':0})['status'] == 'cancelled'
        assert not h._received(runtime, cwd=str(workspace), text='not-delivered')
        runtime.stop()
        runtime.start()
        assert request(data, 'editor', 'recover', {'handle':cancel_handle, 'turnId':'withdraw'})['state'] == 'withdrawn'
        assert request(data, 'editor', 'recover', {'handle':cancel_handle, 'turnId':'warmup'})['status'] == 'done'
        # Exercise the real OS pipe with missing/wrong credentials, including
        # a valid profile name. Neither request reaches the operation.
        from packages.core.terminal.win_pipe import PipeClient
        for credential in ('', '0' * 64):
            conn = PipeClient(_pipe_name(data)).connect()
            try:
                _write_frame(conn, {'profile':'editor', 'credential':credential,
                                    'operation':'prepare', 'args':{'cwd':str(workspace)}})
                assert _read_frame(conn)['ok'] is False
            finally:
                conn.close()
        handle = request(data, 'editor', 'prepare', {'cwd':str(workspace)})['handle']
        with pytest.raises(RuntimeError):
            request(data, 'other', 'poll', {'handle':handle, 'turnId':'foreign', 'cursor':0})
        (data / 'acp' / 'editor.profile').rename(data / 'acp' / 'editor.revoked')
        with pytest.raises(Exception):
            request(data, 'editor', 'prepare', {'cwd':str(workspace)})
        with pytest.raises(Exception):
            request(data, 'missing', 'prepare', {'cwd': str(workspace)})
    finally:
        if child:
            child.stdin.close()
            child.wait(timeout=10)
        runtime.stop()
    assert h._port_is_free(port)
