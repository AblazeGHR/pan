"""Independent delayed-restart MCP authorization and real isolated transport proof."""
import asyncio
import json
import os
import socket
import sys
from types import SimpleNamespace

import pytest
from packages.mcp import server as mcp_server


@pytest.mark.parametrize('tool', [mcp_server.agent_restart_delayed, mcp_server.agent_restart_delayed_status])
@pytest.mark.parametrize('identity,target,allowed', [
    (None, 'target', True),
    ({'id': 'self', 'panAccess': {'restrictToManaged': True}}, 'self', True),
    ({'id': 'self', 'panAccess': {'restrictToManaged': True}, 'managed': ['child']}, 'child', True),
    ({'id': 'self', 'panAccess': {'restrictToManaged': True, 'canClaimUnmanaged': True}}, 'other', False),
    ({'id': 'self', 'panAccess': {'restrictToManaged': False}}, 'other', True),
])
def test_access(monkeypatch, tool, identity, target, allowed):
    monkeypatch.setattr(mcp_server, '_caller_identity', lambda: identity)
    calls = []
    monkeypatch.setattr(mcp_server, '_api', lambda *args: calls.append(args) or {'delayedRestart': None})
    result = tool(target)
    assert result['ok'] == allowed
    if allowed:
        assert len(calls) == 1
        assert calls[0][0] == ('GET' if tool is mcp_server.agent_restart_delayed_status else 'POST')
    else:
        assert result['error']['code'] == 'permission_denied'
        assert not calls  # no claim or mutation


@pytest.mark.parametrize('tool', [mcp_server.agent_restart_delayed, mcp_server.agent_restart_delayed_status])
def test_error_receipt(monkeypatch, tool):
    monkeypatch.setattr(mcp_server, '_caller_identity', lambda: None)
    monkeypatch.setattr(mcp_server, '_api', lambda *args: {'error': 'Session missing not found'})
    assert tool('missing') == {'ok': False, 'error': 'Session missing not found'}


def test_real_stdio_http_lifecycle(tmp_path, monkeypatch):
    import test_real_queue_http_e2e as real
    from test_delayed_restart_locked_queue_http import _stop_owned
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    monkeypatch.setattr(real, 'PORT', port)
    runtime = real._PanRuntime(tmp_path / 'runtime')
    runtime.start()
    try:
        s = real._create(runtime, 'real-fifo-delayed-mcp')
        sid = s['id']
        monkeypatch.setattr(mcp_server, '_pan_api_url', runtime.base_url)
        monkeypatch.delenv('PAN_AGENT_SESSION_ID', raising=False)
        endpoint = f'/api/sessions/{sid}/worker/delayed-restart'
        assert mcp_server.agent_restart_delayed_status(sid) == {'ok': True, 'delayedRestart': None}
        assert not mcp_server.agent_restart_delayed(sid)['ok']
        assert runtime.request('GET', endpoint)['delayedRestart'] is None
        assert not mcp_server.agent_restart_delayed('missing')['ok']
        assert not mcp_server.agent_restart_delayed_status('missing')['ok']
        real._enqueue(runtime, sid, 'hold-A', 'active')
        first = real._wait_for(lambda: real._received(runtime, cwd=s['workdir'], text='hold-A'))[0]
        runtime.request('PATCH', f'/api/sessions/{sid}/queue/paused', json={'paused': True})
        real._enqueue(runtime, sid, 'paused-next', 'paused')

        # Real MCP stdio subprocess -> real HTTP -> fake CLI, never a model.
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def exercise():
            env = dict(os.environ, PAN_API_URL=runtime.base_url, PAN_AGENT_SESSION_ID=sid)
            params = StdioServerParameters(command=sys.executable,
                args=['-m', 'packages.mcp.server'], env=env, cwd=str(real.ROOT))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = {t.name: t for t in (await client.list_tools()).tools}
                    for name in ('agent_restart_delayed', 'agent_restart_delayed_status'):
                        assert tools[name].inputSchema['required'] == ['session_id']
                        assert set(tools[name].inputSchema['properties']) == {'session_id'}
                    async def call(name):
                        response = await client.call_tool(name, {'session_id': sid})
                        assert not response.isError
                        return response.structuredContent or json.loads(response.content[0].text)
                    accepted = await call('agent_restart_delayed')
                    assert accepted['ok'] and accepted['delayedRestart']['status'] == 'pending'
                    assert await call('agent_restart_delayed') == accepted
                    assert await call('agent_restart_delayed_status') == accepted
                    return accepted
        accepted = asyncio.run(exercise())
        assert len(real._received(runtime, cwd=s['workdir'])) == 1
        runtime.gates['hold-A'].touch()
        finished = real._wait_for(lambda: (state if (state := runtime.request('GET', endpoint)['delayedRestart'])['status'] == 'completed' else None))
        assert finished['replacementGeneration'] > accepted['delayedRestart']['generation']
        assert len(real._received(runtime, cwd=s['workdir'])) == 1
        assert runtime.request('GET', f'/api/sessions/{sid}/queue')['queuePaused'] is True
        idle = mcp_server.agent_restart_delayed(sid)
        assert idle['ok'] and idle['delayedRestart']['status'] == 'restarting'
        again = real._wait_for(lambda: (state if (state := runtime.request('GET', endpoint)['delayedRestart'])['status'] == 'completed' and state['revision'] > finished['revision'] else None))
        after = runtime.request('GET', f'/api/sessions/{sid}')
        for key in ('model', 'permissionMode', 'effort', 'alwaysThinkingEnabled', 'maxThinkingTokens', 'mcpServers', 'outputMode', 'adapter'):
            assert after.get(key) == s.get(key), key
        assert len(real._received(runtime, cwd=s['workdir'])) == 1
        # A genuinely failed owned provider creates Illegal state. The new
        # request cannot turn that state into an implicit recovery/start.
        from test_legal_offline_http import kill_owned_provider, durable
        runtime.request('PATCH', f'/api/sessions/{sid}/queue/paused', json={'paused': False})
        real._wait_for(lambda: real._received(runtime, cwd=s['workdir'], text='paused-next'))
        runtime.request('PATCH', f'/api/sessions/{sid}/queue/paused', json={'paused': True})
        runtime.gates['hold-A'].unlink()
        real._enqueue(runtime, sid, 'hold-A', 'illegal-active')
        runtime.request('PATCH', f'/api/sessions/{sid}/queue/paused', json={'paused': False})
        active = real._wait_for(lambda: (rows[-1] if len(rows := real._received(runtime, cwd=s['workdir'], text='hold-A')) == 2 else None))
        runtime.request('PATCH', f'/api/sessions/{sid}/queue/paused', json={'paused': True})
        real._enqueue(runtime, sid, 'illegal-next', 'illegal-next')
        kill_owned_provider(runtime, active['pid'], s['workdir'])
        real._wait_for(lambda: durable(runtime, sid).get('illegal_batches'))
        rejected = mcp_server.agent_restart_delayed(sid)
        assert not rejected['ok'] and 'Illegal' in rejected['error']
        assert runtime.request('GET', f'/api/sessions/{sid}')['illegalOffline'] is True
        assert not real._received(runtime, cwd=s['workdir'], text='illegal-next')
        print('REAL_MCP_DELAYED ' + json.dumps({'base': runtime.base_url, 'dataRoot': str(runtime.data_root), 'panPids': runtime.pan_pids, 'oldProviderPid': first['pid'], 'accepted': accepted, 'completed': finished, 'idleCompleted': again}))
    finally:
        _stop_owned(runtime, SimpleNamespace(stop=lambda: None), locals().get('sid'))

@pytest.mark.parametrize('state', ['offline', 'illegal', 'held', 'error', 'missing', 'shutdown'])
def test_http_existing_worker_gates(monkeypatch, state):
    from packages.web import server
    from packages.core import worker
    session = SimpleNamespace(id='target')
    monkeypatch.setattr(server, '_summary_session_get', lambda sid: None if state == 'missing' else session)
    monkeypatch.setattr(server.legal_offline, 'is_illegal', lambda s: state == 'illegal')
    live = SimpleNamespace(status=state, generation=1, worker_id='worker', _terminal_finishing=False, _queue_unit_active=False)
    monkeypatch.setattr(worker, 'find_alive_worker_by_session', lambda sid: None if state == 'offline' else live)
    monkeypatch.setattr(worker, 'find_worker_by_session', lambda sid: live)
    monkeypatch.setattr(worker, '_get_session_shallow', lambda sid: session)
    monkeypatch.setattr(worker, '_shutdown_started', state == 'shutdown')
    monkeypatch.setattr(worker, '_delayed_restarts', {})
    result = asyncio.run(server.api_delayed_restart('target', existing_worker_only=True))
    assert result.get('error'), result
    assert not worker._delayed_restarts
    if state == 'illegal':
        assert 'Illegal' in result['error']
    if state == 'held':
        assert 'held' in result['error']


def test_original_http_offline_contract_preserved(monkeypatch):
    from packages.web import server
    calls = []
    monkeypatch.setattr(server.worker, 'request_delayed_restart', lambda sid: calls.append(sid) or {'action': 'start'})
    assert asyncio.run(server.api_delayed_restart('offline')) == {'delayedRestart': {'action': 'start'}}
    assert calls == ['offline']


def test_duplicate_during_retirement_gap_only_reads_existing_receipt(monkeypatch):
    from packages.web import server
    prior = {'sessionId': 'target', 'action': 'restart', 'status': 'restarting', 'revision': 2}
    monkeypatch.setattr(server, '_summary_session_get', lambda sid: SimpleNamespace(id=sid))
    monkeypatch.setattr(server.legal_offline, 'is_illegal', lambda s: False)
    monkeypatch.setattr(server.worker, 'find_alive_worker_by_session', lambda sid: None)
    monkeypatch.setattr(server.worker, 'delayed_restart_state', lambda sid: dict(prior))
    monkeypatch.setattr(server.worker, 'request_delayed_restart', lambda sid: pytest.fail('duplicate must not create/start'))
    assert asyncio.run(server.api_delayed_restart('target', existing_worker_only=True)) == {'delayedRestart': prior}
    prior['action'] = 'start'
    assert 'error' in asyncio.run(server.api_delayed_restart('target', existing_worker_only=True))
