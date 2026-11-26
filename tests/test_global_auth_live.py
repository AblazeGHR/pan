"""Owned production app HTTP/WS/MCP/Job acceptance, no real profile/providers."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import secrets
import shutil
import socket

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus, ConnectionClosed

from packages.automation.auth import UserAuth
from packages.jobs.window import command_window_options
from tests import test_real_queue_http_e2e as h

PASSWORD = 'Aa1!' + secrets.token_urlsafe(24)
NEW_PASSWORD = 'Bb2!' + secrets.token_urlsafe(24)
OUT = Path('artifacts/global-auth')


def structured(result):
    return result.structuredContent if result.structuredContent is not None else json.loads(result.content[0].text)


def test_owned_production_http_ws_stdio_and_job(tmp_path, monkeypatch):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        monkeypatch.setattr(h, 'PORT', probe.getsockname()[1])
    assert h.PORT not in (8767, 8768)
    root = tmp_path / 'owned-profile'
    root.mkdir()
    config = {'plugin_manifests': [str(h.ROOT / 'manifest.json'), str(h.ROOT / 'packages/mcp/manifest.json')],
              'qq': {'enabled': False}, 'wechat': {'enabled': False}, 'remote': {'enabled': False},
              'tailscale': {'startupEnabled': False}, 'lan': {'startupEnabled': False}}
    (root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
    auth = UserAuth(root / 'automation')
    auth.initialize(PASSWORD)
    auth.change_session_days(30)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', h.ROOT / 'tests/support/automation_http_server.py')
    monkeypatch.setenv('PAN_TEST_AUTH_CHANNEL_FIXTURE', '1')
    runtime = h._PanRuntime(root)
    evidence = {'port': h.PORT, 'sourceSha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
    client = httpx.Client(base_url=runtime.base_url, trust_env=False, timeout=20)
    bearer = {}
    def login():
        response = client.post('/api/automation/login', json={'key': PASSWORD}, headers={'Origin': runtime.base_url})
        assert response.status_code == 200, response.text
        assert 'HttpOnly' in response.headers['set-cookie'] and 'Max-Age=2592000' in response.headers['set-cookie']
        return {'Authorization': 'Bearer ' + response.json()['token']}
    try:
        runtime.start()
        for path in ['/api/sessions', '/api/settings/ui', '/api/main/identity', '/api/platform',
                     '/api/jobs', '/api/plugins/panels', '/api/files?path=.', '/static/owned',
                     '/attachments/owned', '/api/automation/authorization', '/docs', '/openapi.json']:
            response = client.get(path, headers={'X-Pan-Queue-Caller': 'ses_forged',
                                                'X-Forwarded-For': '127.0.0.1', 'Host': 'localhost'})
            assert response.status_code == 401, (path, response.status_code)
        assert client.get('/react/').status_code == 200
        assert client.post('/api/automation/login', json={'key': PASSWORD},
                           headers={'Origin': 'http://attacker.test'}).status_code == 403
        bearer = login()
        assert client.get('/api/sessions').status_code == 200
        assert client.put('/api/settings/ui', json={}).status_code == 403
        owner = client.post('/api/sessions', headers=bearer, json={'name': 'Auth-owned-machine',
            'adapter': 'cbc', 'model': 'deepseek-v4-flash', 'mcpServers': ['pan'],
            'panAccess': {'restrictToManaged': True, 'canClaimUnmanaged': False}}).json()
        assert owner.get('id'), owner
        outsider = client.post('/api/sessions', headers=bearer, json={'name': 'Auth-outsider',
            'adapter': 'cbc', 'model': 'deepseek-v4-flash', 'mcpServers': []}).json()
        assert outsider.get('id'), outsider
        qq_session = client.post('/api/sessions', headers=bearer, json={'name': 'qq-owned',
            'adapter': 'cbc', 'model': 'deepseek-v4-flash', 'mcpServers': []}).json()
        channel_token = json.loads((root / 'owned-channel-credentials.json').read_text())['qq']
        channel_headers = {'X-Pan-Channel-Credential': channel_token}
        assert client.get('/api/sessions/' + qq_session['id'], headers=channel_headers).status_code == 200
        assert client.get('/api/sessions/' + outsider['id'], headers=channel_headers).status_code == 403
        assert client.get('/api/settings/ui', headers=channel_headers).status_code == 403
        channel_sessions = client.get('/api/sessions', headers=channel_headers)
        assert qq_session['id'] in channel_sessions.text and outsider['id'] not in channel_sessions.text
        evidence['channelScope'] = True

        address = json.loads((root / 'core-ipc.json').read_text())['address']

        async def exercise():
            uri = runtime.base_url.replace('http:', 'ws:') + '/ws/agent'
            async with connect(uri, additional_headers=channel_headers) as channel_ws:
                await channel_ws.send(json.dumps({'type': 'subscribe', 'eventTypes': ['worker.result']}))
                assert json.loads(await channel_ws.recv())['type'] == 'subscribed'
                await channel_ws.send(json.dumps({'type': 'task', 'sessionId': outsider['id'], 'text': 'forbidden'}))
                with pytest.raises(ConnectionClosed):
                    await channel_ws.recv()

            with pytest.raises(InvalidStatus):
                async with connect(uri, origin=runtime.base_url):
                    pass
            with pytest.raises(InvalidStatus):
                async with connect(uri, origin='http://attacker.test', additional_headers=bearer):
                    pass
            async with connect(uri, origin=runtime.base_url, additional_headers=bearer) as websocket:
                response = await asyncio.to_thread(client.post, '/api/automation/logout', headers=bearer)
                assert response.status_code == 200
                with pytest.raises(ConnectionClosed):
                    while True:
                        await asyncio.wait_for(websocket.recv(), 4)
            assert client.get('/api/sessions', headers=bearer).status_code == 401
            params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.mcp.server'],
                cwd=str(h.ROOT), env={**{k: v for k, v in os.environ.items() if not k.startswith('PAN_')},
                    'PAN_PLUGIN_IPC': address, 'PAN_AGENT_SESSION_ID': owner['id'],
                    'PAN_API_URL': runtime.base_url, 'PYTHONPATH': str(h.ROOT)})
            async with stdio_client(params) as streams, ClientSession(*streams) as mcp:
                await mcp.initialize()
                listed = structured(await mcp.call_tool('session_list', {}))
                assert owner['id'] in json.dumps(listed)
                assert outsider['id'] not in json.dumps(listed)
                refused = structured(await mcp.call_tool('session_get', {'session_id': outsider['id']}))
                assert refused['error']['code'] == 'permission_denied', refused
                objects = structured(await mcp.call_tool('authorization_objects', {'kind': 'action', 'operation': 'read'}))
                assert objects['error']['code'] == 'authorization_denied', objects
                # Explicit user grant in this owned profile; no machine bypass.
                permissions = auth._default_permissions()
                for row in permissions.values():
                    for key in row:
                        row[key] = True
                auth.change_permissions(permissions, 0)
                await asyncio.to_thread(client.patch, '/api/sessions/' + owner['id'] + '/queue/paused',
                                        json={'paused': True}, headers=login())
                async with connect(uri, additional_headers=login()) as notices:
                    await notices.send(json.dumps({'type': 'subscribe', 'eventTypes': ['queue.item_added'],
                                                   'sessionIds': [owner['id']]}))
                    assert json.loads(await notices.recv())['type'] == 'subscribed'
                    actions = structured(await mcp.call_tool('job_action_list', {}))
                    action = next(item for item in actions['items'] if item['id'] == 'product_process')
                    from packages.jobs.final_service import object_ref
                    ref = object_ref('action', action)
                    definition = {'name': 'Owned auth canonical process',
                        'steps': [{'stepId': 'owned', 'actionRef': ref,
                            'arguments': {key: {'from': 'literal', 'value': value} for key, value in {
                                'argv': [sys.executable, '-c', 'print("owned-auth-job")'],
                                'cwd': str(root), 'hideWindow': True}.items()},
                            'completion': {'contract': 'process-exit'}}],
                        'retention': {'policyRef': 'default'},
                        'notification': {'on': ['completed'], 'recipients': [
                            {'recipientId': 'owner', 'channelId': 'session', 'targetRef': owner['id']}]}}
                    created = structured(await mcp.call_tool('job_create', {'definition': definition,
                                                                          'request_key': 'auth-create'}))
                    assert created.get('status') == 'completed', created
                    job = created['result']['object']
                    enabled = structured(await mcp.call_tool('job_control', {'object_id': job['id'], 'operation': 'enable',
                        'expected_revision': job['configRevision'], 'expected_state_revision': job['stateRevision'],
                        'request_key': 'auth-enable'}))
                    assert enabled.get('status') == 'completed', enabled
                    job = enabled['result']['object']
                    launched = structured(await mcp.call_tool('job_run', {'job_id': job['id'],
                        'expected_revision': job['configRevision'], 'request_key': 'auth-run'}))
                    assert launched.get('status') == 'accepted', launched
                    evidence['jobId'] = job['id']
                    evidence['runId'] = launched['runId']
                    notice = json.loads(await asyncio.wait_for(notices.recv(), 25))
                    assert notice['type'] == 'queue.item_added' and notice['sessionId'] == owner['id']
                    queued = await asyncio.to_thread(client.get, '/api/sessions/' + owner['id'] + '/queue', headers=login())
                    assert job['id'] in queued.text and 'job_terminal' in queued.text
                    evidence['originalJobNotice'] = True
                terminal = structured(await mcp.call_tool('job_run_get', {'run_id': launched['runId']}))
                assert terminal.get('state') == 'completed', terminal
                evidence['jobTerminal'] = {'state': terminal['state']}
        asyncio.run(exercise())
        bearer = login()
        def browser(phase):
            env = {k: v for k, v in os.environ.items() if not k.startswith('PAN_')}
            env['PAN_TEST_AUTH_PASSWORD'] = PASSWORD
            result = subprocess.run([shutil.which('node') or 'node', str(h.ROOT / 'packages/web/e2e/global-auth-live.mjs'),
                runtime.base_url, str((OUT / 'browser').resolve()), phase], cwd=h.ROOT / 'packages/web',
                env=env, capture_output=True, text=True, encoding='utf-8', timeout=90, **command_window_options())
            (OUT / f'browser-{phase}.log').write_text(result.stdout + result.stderr, encoding='utf-8')
            assert result.returncode == 0, result.stdout + result.stderr
        browser('login')
        persisted_cookie = client.cookies.get('pan_automation_session')
        assert persisted_cookie
        response = client.post('/__test/stop-owned-runtime', headers=bearer)
        assert response.status_code == 200
        runtime.process.wait(timeout=20)
        runtime.stop()
        runtime.start()
        browser('restart')
        assert client.get('/api/sessions').status_code == 200
        evidence['coldRestartCookie'] = True
        status = client.get('/api/automation/authorization').json()
        changed = client.put('/api/automation/authorization', headers={'Origin': runtime.base_url}, json={
            'currentPassword': PASSWORD, 'newPassword': NEW_PASSWORD,
            'confirmNewPassword': NEW_PASSWORD, 'expectedRevision': status['revision']})
        assert changed.status_code == 200, changed.text
        assert client.get('/api/sessions', headers=bearer).status_code == 401
        evidence['passwordRevoked'] = True
    finally:
        # Restore only this owned fixture to obtain a cleanup credential.
        try:
            local = UserAuth(root / 'automation')
            token = local.login(NEW_PASSWORD if evidence.get('passwordRevoked') else PASSWORD)
            client.post('/__test/stop-owned-runtime', headers={'Authorization': 'Bearer ' + token})
            if runtime.process:
                runtime.process.wait(timeout=20)
        finally:
            client.close()
            runtime.stop()
            evidence['portReleased'] = h._port_is_free(h.PORT)
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / 'live.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
