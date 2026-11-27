"""Owned-only full API transport tests; no real profile or external service."""
import json
import os
import secrets
import socket

import httpx
import pytest

from packages.web import script_auth, script_full_api


@pytest.mark.parametrize('method,path,body', [
    ('GET', '/react/', {}), ('GET', '/api/../react/', {}),
    ('GET', '/api/%2e%2e/react/', {}), ('GET', 'http://remote/api/list', {}),
    ('GET', '/api/%252e%252e/react/', {}), ('GET', '/api/\nlist', {}),
    ('GET', '/api%2flist', {}), ('GET', '/api/list#fragment', {}),
    ('GET', '/api/list', {'headers': {'Authorization': 'Bearer attacker'}}),
    ('GET', '/api/list', {'headers': {'X-Pan-Queue-Caller': 'forged'}}),
    ('GET', '/api/list', {'headers': {'Host': 'other'}}),
    ('GET', '/api/list', {'contentBase64': 'not-base64'}),
])
def test_invalid_envelopes_refused(method, path, body):
    with pytest.raises(ValueError):
        script_full_api._validate(method, path, body)


def test_full_profile_is_explicit_and_never_promotes_scoped():
    full = script_auth.validate_profile({'enabled': True, 'mode': 'full-api'})
    assert full == {'enabled': True, 'mode': 'full-api'}
    for policy in ({'enabled': True, 'mode': '*'}, {'enabled': True, 'mode': 'full-api', 'sessions': []},
                   {'enabled': True, 'mode': 'full-api', 'rules': []}):
        with pytest.raises(ValueError): script_auth.validate_profile(policy)
    scoped = script_auth.validate_profile({'enabled': True, 'sessions': ['s1'], 'rules': []})
    assert scoped.get('mode') != 'full-api'
    assert not script_auth.permit(scoped, 'GET', '/api/automation/authorization', {})


def test_asgi_exception_returns_actual_500(monkeypatch, tmp_path):
    import asyncio
    import base64
    from fastapi import FastAPI
    from packages.web import server
    app = FastAPI()
    @app.get('/api/fail')
    def fail(): raise RuntimeError('synthetic failure')
    monkeypatch.setattr(server, 'app', app)
    monkeypatch.setattr(script_full_api, '_user_token', lambda *_: 'synthetic')
    result = asyncio.run(script_full_api.dispatch(tmp_path, 'full', 'GET', '/api/fail', {}))
    assert result['status'] == 500 and base64.b64decode(result['contentBase64'])


@pytest.mark.skipif(os.name != 'nt', reason='script profile uses Windows owner-only DPAPI')
def test_owned_server_full_api_via_real_local_ipc(tmp_path, monkeypatch):
    from packages.automation.auth import UserAuth
    from packages.plugins.ipc import request as ipc_request
    from tests import test_real_queue_http_e2e as h
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', h.ROOT / 'tests/support/automation_http_server.py')
    root = tmp_path / 'owned'
    root.mkdir()
    (root / 'config.json').write_text(json.dumps({'qq': {'enabled': False}, 'wechat': {'enabled': False},
        'remote': {'enabled': False}, 'tailscale': {'startupEnabled': False}, 'lan': {'startupEnabled': False}}))
    password = 'Aa1!' + secrets.token_urlsafe(24)
    auth = UserAuth(root / 'automation')
    auth.initialize(password)
    runtime = h._PanRuntime(root)
    client = httpx.Client(base_url=runtime.base_url, trust_env=False, timeout=10)
    try:
        runtime.start()
        assert client.get('/api/automation/authorization').status_code == 401
        assert client.get('/api/automation/authorization', headers={'X-Pan-Script-Profile': 'full'}).status_code == 401
        with pytest.raises(PermissionError): script_full_api.provision(root, 'wrong-password', 'wrong')
        assert not (root / 'script-api/wrong-password').exists()
        script_full_api.provision(root, 'full', password)
        read = script_full_api.request(root, 'full', 'GET', '/api/automation/authorization')
        assert read['status'] == 200 and 'permissions' in json.loads(read['content'])
        created = script_full_api.request(root, 'full', 'POST', '/api/sessions',
            json.dumps({'name': 'Full-API-Fixture', 'adapter': 'codex', 'mcpServers': []}).encode(),
            {'Content-Type': 'application/json'})
        assert created['status'] == 200 and json.loads(created['content'])['id']
        sid = json.loads(created['content'])['id']
        payload = b'\x00\xfffixture\r\n'
        uploaded = script_full_api.request(root, 'full', 'POST', f'/api/sessions/{sid}/attachments', payload,
            {'Content-Type': 'application/octet-stream', 'X-Filename': 'fixture.bin'})
        assert uploaded['status'] == 200 and json.loads(uploaded['content'])['size'] == len(payload)
        downloaded = script_full_api.request(root, 'full', 'GET', json.loads(uploaded['content'])['href'])
        assert downloaded['status'] == 200 and downloaded['content'] == payload
        malformed = script_full_api.request(root, 'full', 'POST', '/api/automation/authorization', b'{}', {'Content-Type': 'application/json'})
        assert malformed['status'] == 405
        with pytest.raises(ValueError): script_full_api.request(root, 'full', 'GET', '/react/')
        secret = script_auth.load_client(root, 'full')
        locator = json.loads((root / 'script-ipc.json').read_text())
        message = {'session_id':'', 'operation':'script_http', 'profile':'full', 'credential':'0'*64,
                   'method':'GET', 'path':'/api/list', 'body':{}}
        assert ipc_request(message, address=locator['address'])['error']['code'] == 403
        message['credential'] = ''
        assert ipc_request(message, address=locator['address'])['error']['code'] == 403
        general = json.loads((root / 'core-ipc.json').read_text())
        message['credential'] = secret
        assert ipc_request(message, address=general['address'])['isError'] is True
        message['credential'] = secret
        message['profile'] = 'missing'
        assert ipc_request(message, address=locator['address'])['error']['code'] == 403
        script_auth.initialize(root, 'limited', {'enabled': True, 'sessions': [], 'rules': []})
        message['profile'] = 'limited'
        message['credential'] = script_auth.load_client(root, 'limited')
        message['path'] = '/api/automation/authorization'
        assert ipc_request(message, address=locator['address'])['error']['code'] == 403
        message['profile'] = 'full'
        message['path'] = '/api/list'
        message['credential'] = secret
        user_token = script_full_api._user_token(root, 'full')
        assert auth.authorized('Bearer ' + user_token)
        assert user_token.encode() not in (root / 'script-api/full/user.session').read_bytes()
        assert user_token not in json.dumps(read['headers']) and user_token.encode() not in read['content']
        script_auth.revoke(root, 'full')
        assert ipc_request(message, address=locator['address'])['error']['code'] == 403
        assert not auth.authorized('Bearer ' + user_token)
        with pytest.raises(RuntimeError): script_full_api.request(root, 'full', 'GET', '/api/list')
        assert secret not in (root / 'script-api/full/profile.json').read_text()
        assert secret.encode() not in (root / 'script-api/full/client.secret').read_bytes()
        login = client.post('/api/automation/login', json={'key': password}, headers={'Origin': runtime.base_url})
        assert login.status_code == 200
        assert client.get('/api/automation/authorization', headers={'Authorization': 'Bearer ' + login.json()['token']}).status_code == 200
        assert client.get('/api/automation/authorization', headers={'Authorization': 'Bearer invalid'}).status_code == 401
        script_full_api.provision(root, 'second', password)
        old = script_full_api._user_token(root, 'second')
        script_full_api.provision(root, 'second', password, renew=True)
        assert not auth.authorized('Bearer ' + old)
        assert script_full_api.request(root, 'second', 'GET', '/api/automation/authorization')['status'] == 200
        auth.logout('Bearer ' + script_full_api._user_token(root, 'second'))
        assert script_full_api.request(root, 'second', 'GET', '/api/automation/authorization')['status'] == 401
        assert client.get('/api/automation/authorization', headers={'Authorization': 'Bearer ' + login.json()['token']}).status_code == 200
    finally:
        client.close()
        runtime.stop()
    assert h._port_is_free(port)
