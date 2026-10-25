"""Persistent user sessions, transport cookies and CSRF contract; no listeners."""
import asyncio
import hashlib
import json
import os
import stat
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request

from packages.automation.auth import UserAuth
from packages.automation.api import install
from packages.automation.authorization import require


def test_restart_expiry_hashes_and_bounded_renewal(tmp_path, monkeypatch):
    clock = [1000000.0]
    monkeypatch.setattr('packages.automation.auth.time.time', lambda: clock[0])
    auth = UserAuth(tmp_path)
    auth.change_session_days(7)
    token = auth.login(auth.key())
    digest = hashlib.sha256(token.encode()).hexdigest()
    raw = auth.sessions_path.read_text()
    assert token not in raw and digest in raw
    if os.name != 'nt':
        assert stat.S_IMODE(auth.sessions_path.stat().st_mode) == 0o600
    restarted = UserAuth(tmp_path)
    assert restarted.authorized('Bearer ' + token)
    assert restarted.session(token)['expiresAt'] == clock[0] + 7 * 86400
    before = restarted.sessions_path.read_bytes()
    clock[0] += 3 * 86400
    assert not restarted.session(token)['renewed']
    assert restarted.sessions_path.read_bytes() == before
    clock[0] += 86401
    assert restarted.session(token)['renewed']
    renewed = UserAuth(tmp_path)
    assert renewed.session(token)['expiresAt'] == clock[0] + 7 * 86400
    clock[0] += 7 * 86400 + 1
    assert not renewed.authorized('Bearer ' + token)
    assert json.loads(renewed.sessions_path.read_text())['sessions'] == {}
    assert not UserAuth(tmp_path).authorized('Bearer ' + token)


def test_global_setting_preserves_permissions_and_zero_has_fixed_ttl(tmp_path, monkeypatch):
    clock = [1000000.0]
    monkeypatch.setattr('packages.automation.auth.time.time', lambda: clock[0])
    auth = UserAuth(tmp_path)
    auth.change_permissions(auth._default_permissions(), 0)
    auth.change_session_days(90)
    token = auth.login(auth.key())
    auth.change_permissions(auth._default_permissions(), 1)
    assert UserAuth(tmp_path).session_days == 90
    auth.change_session_days(0)
    assert auth.permissions_status()['revision'] == 2
    clock[0] += 30000
    assert not auth.session(token)['renewed']
    clock[0] += 13201
    assert not auth.session(token)['authenticated']


def test_failed_session_replace_is_not_published(tmp_path, monkeypatch):
    clock = [1000000.0]
    monkeypatch.setattr('packages.automation.auth.time.time', lambda: clock[0])
    auth = UserAuth(tmp_path)
    auth.change_session_days(7)
    token = auth.login(auth.key())
    before = auth.sessions_path.read_bytes()
    expires = auth.session(token)['expiresAt']
    clock[0] += 4 * 86400
    def fail_replace(*args):
        raise OSError('synthetic replace failure')
    monkeypatch.setattr('packages.automation.auth.os.replace', fail_replace)
    with pytest.raises(OSError):
        auth.session(token)
    assert auth.session(token, False)['expiresAt'] == expires
    with pytest.raises(OSError):
        auth.logout('Bearer ' + token)
    assert auth.session(token, False)['authenticated']
    assert auth.sessions_path.read_bytes() == before
    assert not list(tmp_path.glob('sessions.json.tmp-*'))


def test_global_duration_reapplies_cookie_policy_to_other_browsers(tmp_path):
    async def run():
        auth = UserAuth(tmp_path)
        auth.change_session_days(90)
        async def io(fn, *args):
            return fn(*args)
        app = FastAPI()
        app.state.automation = SimpleNamespace(auth=auth, engine=SimpleNamespace(io=io))
        install(SimpleNamespace(app=app))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://example.test') as first, httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://example.test') as second:
            await first.post('/api/automation/login', json={'key': auth.key()})
            await second.post('/api/automation/login', json={'key': auth.key()})
            assert 'set-cookie' not in (await second.get('/api/automation/session')).headers
            changed = await first.put('/api/automation/settings', json={'sessionDays': 0}, headers={'Origin': 'http://example.test'})
            assert changed.status_code == 200
            assert 'Max-Age' not in changed.headers['set-cookie']
            other = await second.get('/api/automation/session')
            assert other.json()['authenticated'] and other.json()['sessionDays'] == 0
            assert 'Max-Age' not in other.headers['set-cookie']
            before = auth.sessions_path.read_bytes()
            assert 'set-cookie' not in (await second.get('/api/automation/session')).headers
            assert auth.sessions_path.read_bytes() == before
    asyncio.run(run())


@pytest.mark.parametrize('days', [0, 7, 30, 90])
@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_cookie_api_lifecycle(tmp_path, monkeypatch, days, scheme):
    clock = [1000000.0]
    monkeypatch.setattr('packages.automation.auth.time.time', lambda: clock[0])
    async def run():
        auth = UserAuth(tmp_path)
        auth.change_session_days(days)
        async def io(fn, *args):
            return fn(*args)
        app = FastAPI()
        app.state.automation = SimpleNamespace(auth=auth, engine=SimpleNamespace(io=io))
        install(SimpleNamespace(app=app))
        @app.post('/api/shared-object')
        async def shared_object(request: Request):
            return {'principal': await require(request, 'job', 'execute')}
        base = scheme + '://example.test'
        origin = {'Origin': base}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=base) as client:
            assert (await client.get('/api/automation/session')).json() == {
                'authenticated': False, 'expiresAt': None, 'sessionDays': days}
            assert (await client.get('/api/automation/settings')).status_code == 401
            login = await client.post('/api/automation/login', json={'key': auth.key()})
            token = login.json()['token']
            cookie = login.headers['set-cookie']
            assert 'HttpOnly' in cookie and 'SameSite=lax' in cookie and 'Path=/' in cookie
            assert ('Secure' in cookie) == (scheme == 'https')
            assert ('Max-Age=' in cookie) == bool(days)
            assert login.json()['expiresIn'] == (days * 86400 or 43200)
            if days:
                assert f'Max-Age={days * 86400}' in cookie
            # Simulate a process restart without losing the browser's cookie.
            app.state.automation.auth = auth = UserAuth(tmp_path)
            assert (await client.get('/api/automation/session')).json()['authenticated']
            assert (await client.get('/api/automation/authorization')).status_code == 200
            for bad_origin in (None, 'null', 'http://evil.test', base + '.evil', base + '/path', base + ':0'):
                headers = {} if bad_origin is None else {'Origin': bad_origin}
                for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                    assert (await client.request(method, '/api/shared-object', headers=headers)).status_code == 403
            assert (await client.post('/api/shared-object', headers=origin)).json() == {'principal': 'user'}
            assert (await client.post('/api/shared-object', headers={'Authorization': 'Bearer ' + token, 'Origin': 'http://evil.test'})).status_code == 200
            for invalid in (True, 1, -1, 365, '7', 7.0, None):
                assert (await client.put('/api/automation/settings', json={'sessionDays': invalid}, headers=origin)).status_code == 400
            assert (await client.get('/api/automation/settings')).json() == {'sessionDays': days}
            if days:
                assert 'set-cookie' not in (await client.get('/api/automation/session')).headers
                clock[0] += days * 86400 / 2 + 1
                renewed = await client.get('/api/automation/session')
                assert 'set-cookie' in renewed.headers
                assert renewed.json()['expiresAt'] == clock[0] + days * 86400
                # Active Bearer browser clients must refresh their Cookie too.
                clock[0] += days * 86400 / 2 + 1
                assert 'set-cookie' in (await client.get('/api/automation/session', headers={'Authorization': 'Bearer ' + token})).headers
            saved = await client.put('/api/automation/settings', json={'sessionDays': 30}, headers=origin)
            assert saved.json() == {'sessionDays': 30} and 'Max-Age=2592000' in saved.headers['set-cookie']
            assert UserAuth(tmp_path).session_days == 30
            logout = await client.post('/api/automation/logout', headers=origin)
            assert 'Max-Age=0' in logout.headers['set-cookie']
            assert not UserAuth(tmp_path).authorized('Bearer ' + token)
            assert not (await client.get('/api/automation/session')).json()['authenticated']
            login = await client.post('/api/automation/login', json={'key': auth.key()})
            old_token = login.json()['token']
            other_token = auth.login(auth.key())
            status = (await client.get('/api/automation/authorization')).json()
            changed = await client.put('/api/automation/authorization', headers=origin, json={
                'currentPassword': auth.key(), 'newPassword': 'NewPassword!234567',
                'confirmNewPassword': 'NewPassword!234567', 'expectedRevision': status['revision']})
            assert changed.status_code == 200 and 'Max-Age=0' in changed.headers['set-cookie']
            assert not UserAuth(tmp_path).authorized('Bearer ' + old_token)
            assert not UserAuth(tmp_path).authorized('Bearer ' + other_token)
            await client.post('/api/automation/login', json={'key': auth.key()})
            revoked = await client.post('/api/automation/revoke', headers=origin)
            assert revoked.status_code == 200 and 'Max-Age=0' in revoked.headers['set-cookie']
            assert json.loads(auth.sessions_path.read_text())['sessions'] == {}
    asyncio.run(run())
