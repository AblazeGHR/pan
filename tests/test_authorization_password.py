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
        body['newPassword'] = body['confirmNewPassword'] = ''
        assert (await client.put('/api/automation/authorization', json=body, headers=headers)).status_code == 422
        body['newPassword'] = body['confirmNewPassword'] = 'weak'
        changed = await client.put('/api/automation/authorization', json=body, headers=headers)
        assert changed.status_code == 200 and changed.json()['revoked'] is True
        assert (await client.get('/api/automation/authorization', headers=headers)).status_code == 401
        assert (await client.post('/api/automation/login', json={'key': 'weak'})).status_code == 200


def test_password_policy_has_no_length_class_or_ascii_requirement(tmp_path):
    auth = UserAuth(tmp_path)
    current = auth.key()
    for value in ('a', 'password', '密码', 'x' * 129, 'long password with spaces'):
        revision = auth.authorization_status()['revision']
        token = auth.login(current)
        auth.change_password(current, value, value, revision)
        assert auth.key() == value
        assert auth.authorization_status()['revision'] == UserAuth._revision(value)
        assert not auth.authorized('Bearer ' + token)
        assert auth.login(value)
        current = value


@pytest.mark.parametrize('value', ['', ' ', '   ', ' padded', 'padded ', '\npassword', 'password\n', 'a\rb', 'a\tb', 'a\x00b'])
def test_empty_or_whitespace_padded_or_control_passwords_are_rejected(tmp_path, value):
    auth = UserAuth(tmp_path)
    old = auth.key()
    revision = auth.authorization_status()['revision']
    with pytest.raises(ValueError):
        auth.change_password(old, value, value, revision)
    assert auth.path.read_text(encoding='utf-8') == old
    assert auth.login(old)


def test_confirmation_current_password_cas_and_difference_are_still_enforced(tmp_path):
    auth = UserAuth(tmp_path)
    old = auth.key()
    revision = auth.authorization_status()['revision']
    with pytest.raises(ValueError, match='confirmation'):
        auth.change_password(old, 'a', 'b', revision)
    with pytest.raises(PermissionError):
        auth.change_password('not-the-password', 'a', 'a', revision)
    with pytest.raises(RuntimeError):
        auth.change_password(old, 'a', 'a', 'stale')
    with pytest.raises(ValueError, match='differ'):
        auth.change_password(old, old, old, revision)
    assert auth.path.read_text(encoding='utf-8') == old
    assert auth.login(old)


def test_blank_stored_password_never_authenticates(tmp_path):
    auth = UserAuth(tmp_path)
    auth.key()
    auth.path.write_text('   \n', encoding='utf-8')
    with pytest.raises(ValueError):
        auth.key()
    with pytest.raises(ValueError):
        auth.login('')
    with pytest.raises(ValueError):
        auth.login('   ')
