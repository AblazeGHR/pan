"""Whole-ASGI authentication contract, owned fixtures only."""
import asyncio
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from packages.automation.auth import UserAuth
import secrets

PASSWORD = 'Aa1!' + secrets.token_urlsafe(24)


def test_local_initialize_hash_restart_and_password_revocation(tmp_path):
    auth = UserAuth(tmp_path)
    auth.initialize(PASSWORD)
    assert PASSWORD not in auth.path.read_text()
    token = auth.login(PASSWORD)
    assert UserAuth(tmp_path).session(token)['authenticated']
    revision = auth.authorization_status()['revision']
    auth.change_password(PASSWORD, 'OtherFixture!234567', 'OtherFixture!234567', revision)
    assert not auth.session(token)['authenticated']
    assert not UserAuth(tmp_path).session(token)['authenticated']
    assert 'OtherFixture!234567' not in auth.path.read_text()


def test_legacy_migration_preserves_sessions_and_rejects_verifier_login(tmp_path):
    import hashlib
    import json
    import time
    auth = UserAuth(tmp_path)
    auth.path.write_text(PASSWORD)
    token = 'owned-legacy-session'
    auth.sessions_path.write_text(json.dumps({'keyRevision': hashlib.sha256(PASSWORD.encode()).hexdigest(),
        'sessions': {hashlib.sha256(token.encode()).hexdigest(): time.time() + 3600}}))
    auth = UserAuth(tmp_path)
    auth.login(PASSWORD)
    assert PASSWORD not in auth.path.read_text()
    assert UserAuth(tmp_path).session(token)['authenticated']
    import pytest
    with pytest.raises(PermissionError):
        auth.login(auth.path.read_text())


def test_login_rate_limit_is_global_not_forwarded_ip(tmp_path):
    import pytest
    auth = UserAuth(tmp_path)
    auth.initialize(PASSWORD)
    for _ in range(10):
        with pytest.raises(PermissionError):
            auth.login('wrong')
    with pytest.raises(ValueError, match='rate limit'):
        auth.login(PASSWORD)


def test_durable_revocation_is_observed_and_corruption_fails_closed(tmp_path):
    import pytest
    auth = UserAuth(tmp_path)
    auth.initialize(PASSWORD)
    token = auth.login(PASSWORD)
    other = UserAuth(tmp_path)
    assert other.session(token)['authenticated']
    auth.revoke()
    assert not other.session(token)['authenticated']
    token = auth.login(PASSWORD)
    auth.sessions_path.write_text('broken-owned-fixture')
    with pytest.raises(ValueError):
        auth.session(token)


def test_machine_scope_never_grants_admin_or_unmanaged_targets():
    from packages.web.machine_ipc import permit
    caller = SimpleNamespace(id='ses_owner', restrict_to_managed=True,
                             managed=['ses_child'], can_claim_unmanaged=False)
    get = lambda _: None
    assert permit(caller, 'GET', '/api/sessions/ses_owner?view=metadata', {}, get)
    assert permit(caller, 'POST', '/api/send', {'sessionId': 'ses_child'}, get)
    message = 'pan%3A' + 'a' * 32
    assert permit(caller, 'GET', '/api/sessions/ses_child/messages/' + message, {}, get)
    for leaf in ['pan%253A' + 'a' * 32, 'pan%3A..%2fsettings', 'pan%3Anot-an-id', 'pan%3A' + 'a' * 32 + '%2fusage']:
        assert not permit(caller, 'GET', '/api/sessions/ses_child/messages/' + leaf, {}, get)
    assert not permit(caller, 'GET', '/api/sessions/ses_other/messages/' + message, {}, get)
    for method, path, body in [
        ('GET', '/api/settings', {}), ('POST', '/api/main/exit', {}),
        ('POST', '/api/automation/revoke', {}), ('POST', '/api/automation/login', {}),
        ('GET', '/api/sessions/ses_other', {}),
        ('POST', '/api/send', {'sessionId': 'ses_other'}),
        ('POST', '/api/send', {'sessionId': 'ses_child', 'sourceSessionId': 'ses_other'}),
        ('PATCH', '/api/sessions/ses_owner', {'panAccess': {'restrictToManaged': False}}),
        ('GET', '/api/sessions/%2e%2e/settings', {}), ('GET', '//attacker/api/sessions', {}),
    ]:
        assert not permit(caller, method, path, body, get), (method, path)


def test_hash_initialize_concurrency_and_storage_failure(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import pytest
    auth = UserAuth(tmp_path)
    def initialize(_):
        try:
            auth.initialize(PASSWORD)
            return 'ok'
        except FileExistsError:
            return 'exists'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(initialize, range(2))) == ['exists', 'ok']
    before = auth.path.read_bytes()
    monkeypatch.setattr('packages.automation.auth.os.replace', lambda *_: (_ for _ in ()).throw(OSError('owned failure')))
    with pytest.raises(OSError):
        auth.login(PASSWORD)
    assert auth.path.read_bytes() == before
    assert not auth.sessions


def test_unconfigured_login_never_creates_password(tmp_path):
    auth = UserAuth(tmp_path)
    try:
        auth.login('invalid-public-first-wins')
    except (PermissionError, ValueError):
        pass
    assert not auth.path.exists()


def test_default_deny_covers_dynamic_mount_and_non_api(tmp_path):
    from packages.web.global_auth import GlobalAuthMiddleware
    async def run():
        app = FastAPI()
        app.state.automation = SimpleNamespace(auth=UserAuth(tmp_path))
        app.add_middleware(GlobalAuthMiddleware, server=app)
        child = FastAPI()
        @child.get('/secret')
        async def secret():
            return {'secret': 'owned-fixture'}
        app.mount('/plugin', child)
        @app.get('/download/owned')
        async def download():
            return {'secret': 'owned-fixture'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://owned.test') as client:
            for path in ['/download/owned', '/plugin/secret', '/api/sessions', '/docs', '/openapi.json']:
                response = await client.get(path, headers={'X-Pan-Queue-Caller': 'ses_forged',
                    'X-Forwarded-For': '127.0.0.1', 'Forwarded': 'for=127.0.0.1'})
                assert response.status_code == 401, path
                assert 'owned-fixture' not in response.text
    asyncio.run(run())


def test_cookie_csrf_bearer_precedence_and_http_renewal(tmp_path, monkeypatch):
    from packages.web.global_auth import GlobalAuthMiddleware
    from starlette.requests import Request
    clock = [1000000.0]
    monkeypatch.setattr('packages.automation.auth.time.time', lambda: clock[0])
    async def run():
        auth = UserAuth(tmp_path)
        auth.initialize(PASSWORD)
        auth.change_session_days(7)
        token = auth.login(PASSWORD)
        app = FastAPI()
        app.state.automation = SimpleNamespace(auth=auth)
        app.add_middleware(GlobalAuthMiddleware, server=app)
        @app.api_route('/owned', methods=['GET', 'POST'])
        async def owned(request: Request):
            assert request.headers['authorization'] == 'Bearer ' + token
            return {'ok': True}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://owned.test') as client:
            client.cookies.set('pan_automation_session', token + '.7')
            assert (await client.get('/owned')).status_code == 200
            assert (await client.post('/owned')).status_code == 403
            assert (await client.post('/owned', headers={'Origin': 'https://attacker.test'})).status_code == 403
            assert (await client.post('/owned', headers={'Origin': 'https://owned.test'})).status_code == 200
            assert (await client.get('/owned', headers={'Authorization': 'Bearer wrong'})).status_code == 401
            clock[0] += 4 * 86400
            renewed = await client.get('/owned')
            cookie = renewed.headers['set-cookie']
            assert 'HttpOnly' in cookie and 'Secure' in cookie and 'Max-Age=604800' in cookie
            auth.logout('Bearer ' + token)
            assert (await client.get('/owned')).status_code == 401
    asyncio.run(run())


def test_websocket_handshake_origin_and_idle_revocation(tmp_path):
    from packages.web.global_auth import GlobalAuthMiddleware
    from fastapi import WebSocket
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    import pytest
    auth = UserAuth(tmp_path)
    auth.initialize(PASSWORD)
    token = auth.login(PASSWORD)
    app = FastAPI()
    app.state.automation = SimpleNamespace(auth=auth)
    app.add_middleware(GlobalAuthMiddleware, server=app)
    @app.websocket('/socket')
    async def socket(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text('owned')
        await websocket.receive_text()
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/socket', headers={'Origin': 'http://testserver'}):
                pass
        client.cookies.set('pan_automation_session', token + '.0')
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/socket', headers={'Origin': 'http://attacker.test'}):
                pass
        with client.websocket_connect('/socket', headers={'Origin': 'http://testserver'}) as websocket:
            assert websocket.receive_text() == 'owned'
            auth.revoke()
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_text()
            assert closed.value.code == 4401


def test_explicit_proxy_origin_ignores_forwarded_and_sets_secure_cookie(tmp_path):
    from packages.web.global_auth import GlobalAuthMiddleware, set_cookie
    from fastapi import Request, Response
    async def run():
        auth = UserAuth(tmp_path)
        auth.initialize(PASSWORD)
        token = auth.login(PASSWORD)
        app = FastAPI()
        app.state.automation = SimpleNamespace(auth=auth)
        app.state.global_auth_origins = ['https://owned.example']
        app.add_middleware(GlobalAuthMiddleware, server=app)
        @app.post('/api/automation/login')
        async def login(request: Request, response: Response):
            set_cookie(response, request, token, 7)
            return {'ok': True}
        @app.post('/owned')
        async def owned():
            return {'ok': True}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://owned.example') as client:
            response = await client.post('/api/automation/login', headers={'Origin': 'https://owned.example'})
            assert response.status_code == 200 and 'Secure' in response.headers['set-cookie']
            client.cookies.set('pan_automation_session', token + '.7')
            assert (await client.post('/owned', headers={'Origin': 'https://owned.example'})).status_code == 200
            for origin in ('https://evil.example', 'https://owned.example:0', 'null'):
                assert (await client.post('/owned', headers={'Origin': origin,
                    'X-Forwarded-Host': 'owned.example', 'X-Forwarded-Proto': 'https',
                    'Forwarded': 'host=owned.example;proto=https'})).status_code == 403
            assert (await client.post('/owned', headers={'Host': 'evil.example', 'Origin': 'https://owned.example'})).status_code == 403
    asyncio.run(run())


def test_concurrent_auth_instances_preserve_sessions_and_revocations(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    auth = UserAuth(tmp_path)
    auth.initialize(PASSWORD)
    first, second = UserAuth(tmp_path), UserAuth(tmp_path)
    with ThreadPoolExecutor(2) as pool:
        tokens = list(pool.map(lambda obj: obj.login(PASSWORD), [first, second]))
    reopened = UserAuth(tmp_path)
    assert all(reopened.session(token)['authenticated'] for token in tokens)
    first.logout('Bearer ' + tokens[0])
    second.login(PASSWORD)
    assert not UserAuth(tmp_path).session(tokens[0])['authenticated']
    assert UserAuth(tmp_path).session(tokens[1])['authenticated']


def test_every_production_route_defaults_to_deny(tmp_path):
    """Inventory is drawn from actual routers, including optional/plugin mounts."""
    import re
    from packages.web import server
    from packages.web.global_auth import PUBLIC
    async def run():
        app = server.app
        old = getattr(app.state, 'automation', None)
        app.state.automation = SimpleNamespace(auth=UserAuth(tmp_path))
        count = 0
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://owned.test') as client:
                for route in app.routes:
                    path = getattr(route, 'path', '')
                    if not path or not getattr(route, 'methods', None):
                        continue
                    concrete = re.sub(r'\{[^}]+\}', 'owned', path)
                    for method in route.methods:
                        if (method, concrete) in PUBLIC:
                            continue
                        response = await client.request(method, concrete)
                        assert response.status_code == 401, (method, concrete, response.status_code)
                        count += 1
            assert count > 200
        finally:
            app.state.automation = old
    asyncio.run(run())


def test_channel_credential_is_scoped_and_never_a_user_login(tmp_path, monkeypatch):
    from packages.web.global_auth import GlobalAuthMiddleware
    from packages.web.channel_auth import child_environment, ENV
    from packages.core import session
    fixtures = {'ses_qq': SimpleNamespace(name='qq-123456'),
                'ses_wx': SimpleNamespace(name='wx-owned'),
                'ses_other': SimpleNamespace(name='private-admin')}
    monkeypatch.setattr(session, 'get', lambda sid, **_: fixtures.get(sid))
    async def run():
        app = FastAPI()
        app.state.automation = SimpleNamespace(auth=UserAuth(tmp_path))
        app.add_middleware(GlobalAuthMiddleware, server=app)
        @app.get('/api/sessions/{sid}')
        async def detail(sid: str):
            return {'id': sid}
        @app.post('/api/qq/notify')
        async def notify():
            return {'ok': True}
        token = child_environment('qq')[ENV]
        headers = {'X-Pan-Channel-Credential': token}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://owned.test') as client:
            assert (await client.get('/api/sessions/ses_qq', headers=headers)).status_code == 200
            assert (await client.post('/api/qq/notify', json={}, headers=headers)).status_code == 200
            for method, path in [('GET', '/api/settings/ui'), ('GET', '/api/sessions/ses_wx'),
                                 ('GET', '/api/sessions/ses_other'), ('POST', '/api/automation/revoke'),
                                 ('POST', '/api/wechat/notify'), ('POST', '/api/main/exit')]:
                assert (await client.request(method, path, headers=headers)).status_code == 403
            assert (await client.get('/api/sessions/ses_qq', headers={'X-Pan-Channel-Credential': 'forged',
                'X-Forwarded-For': '127.0.0.1'})).status_code == 401
            assert (await client.get('/api/sessions/ses_qq', headers={**headers, 'Origin': 'http://owned.test'})).status_code == 403
    asyncio.run(run())
