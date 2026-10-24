from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from packages.automation.api import install
from packages.automation.auth import UserAuth
from packages.automation.service import Service


def test_password_change_revokes_bearers_and_preserves_old_on_conflict(tmp_path):
    auth = UserAuth(tmp_path)
    old = auth.key()
    token = auth.login(old)
    revision = auth.authorization_status()['revision']
    with pytest.raises(RuntimeError):
        auth.change_password(old, 'NewPassword!234567', 'NewPassword!234567', 'stale')
    assert auth.path.read_text() == old
    auth.change_password(old, 'NewPassword!234567', 'NewPassword!234567', revision)
    assert not auth.authorized('Bearer ' + token)
    with pytest.raises(PermissionError):
        auth.login(old)
    assert auth.login('NewPassword!234567')


def test_password_change_concurrent_cas_has_one_winner(tmp_path):
    auth = UserAuth(tmp_path)
    old = auth.key()
    revision = auth.authorization_status()['revision']
    def attempt(value):
        try:
            auth.change_password(old, value, value, revision)
            return 'ok'
        except RuntimeError:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        result = sorted(pool.map(attempt, ['NewPassword!234567', 'OtherPassword!234567']))
    assert result == ['conflict', 'ok']


def test_password_change_storage_failure_rolls_back(tmp_path, monkeypatch):
    auth = UserAuth(tmp_path)
    old = auth.key()
    revision = auth.authorization_status()['revision']
    monkeypatch.setattr('packages.automation.auth.os.replace', lambda *_: (_ for _ in ()).throw(OSError('denied')))
    with pytest.raises(OSError, match='storage failed'):
        auth.change_password(old, 'NewPassword!234567', 'NewPassword!234567', revision)
    assert auth.path.read_text() == old
    assert not list(tmp_path.glob('user.key.tmp-*'))


def async_test(fn):
    import asyncio
    from functools import wraps
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return run

@async_test
async def test_authorization_http_requires_bearer_and_distinguishes_errors(tmp_path):
    svc = Service(tmp_path, type('Worker', (), {'workers': {}})(), None)
    app = FastAPI(); app.state.automation = svc; install(type('Server', (), {'app': app})())
    old = svc.auth.key(); token = svc.auth.login(old)
    headers = {'Authorization': 'Bearer ' + token}
    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        assert (await client.get('/api/automation/authorization')).status_code == 401
        status = await client.get('/api/automation/authorization', headers=headers)
        assert status.status_code == 200 and status.json()['configured'] is True
        body = {'currentPassword': 'wrong', 'newPassword': 'NewPassword!234567', 'confirmNewPassword': 'NewPassword!234567', 'expectedRevision': status.json()['revision']}
        assert (await client.put('/api/automation/authorization', json=body, headers=headers)).status_code == 401
        body['currentPassword'] = old
        body['newPassword'] = 'weak'; body['confirmNewPassword'] = 'weak'
        assert (await client.put('/api/automation/authorization', json=body, headers=headers)).status_code == 422
        body['newPassword'] = body['confirmNewPassword'] = 'NewPassword!234567'
        changed = await client.put('/api/automation/authorization', json=body, headers=headers)
        assert changed.status_code == 200 and changed.json()['revoked'] is True
        assert (await client.get('/api/automation/authorization', headers=headers)).status_code == 401
