"""Ownership failures expose diagnostics without admitting any runtime effects."""
from contextlib import asynccontextmanager
import hashlib
import json

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

from packages.core import session
from packages.jobs.runtime_owner import OwnerFence


@pytest.fixture
def administrator():
    from packages.automation.auth import UserAuth
    from tests.support.auth_credentials import provision
    auth = UserAuth(session.SESSION_DIR.parent / 'automation')
    return {'Authorization': 'Bearer ' + auth.login(provision(auth))}


def test_unknown_legacy_lease_starts_only_maintenance_web(tmp_path, monkeypatch, administrator):
    from packages.web import server
    profile = session.SESSION_DIR.parent
    owner = OwnerFence(profile)
    with owner.transaction() as db:
        db.execute("INSERT INTO legacy_leases VALUES('original-unknown-lease')")
    before = hashlib.sha256(owner.path.read_bytes()).hexdigest()
    calls = []

    @asynccontextmanager
    async def forbidden(*args):
        calls.append('runtime-effects-started')
        raise AssertionError('watchdog/provider/recovery startup must not run')
        yield

    monkeypatch.setattr(server, '_owned_lifespan', forbidden)
    with TestClient(server.app, headers=administrator) as client:
        state = client.get('/api/job-maintenance/status')
        assert state.status_code == 200
        assert state.json()['mode'] == 'maintenance'
        assert state.json()['ready'] is False
        assert state.json()['error']['reason'] == 'legacy_leases_unverified'
        assert client.get('/api/health').status_code == 503
        denied = client.post('/api/jobs', json={'definition': {}, 'requestKey': 'never'})
        assert denied.status_code == 503
        assert denied.json()['error']['code'] == 'runtime_ownership_unverified'
        assert client.get('/api/job-maintenance/records', headers={'Authorization': ''}).status_code == 401
        assert client.get('/api/job-maintenance/records', headers={'Authorization': 'Bearer incorrect'}).status_code == 401
        assert client.get('/api/job-maintenance/records').status_code == 200
        from packages.jobs.maintenance import authority
        maintenance = server.app.state.job_maintenance
        assert authority(maintenance) is maintenance['auth']
        assert authority(maintenance).path.parent == profile / 'automation'
        assert client.post('/api/sessions', json={}).status_code == 503
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect('/ws'):
                pytest.fail('maintenance must not attach an effectful websocket')
        assert closed.value.code == 1013
    assert server.app.state.job_maintenance is None
    assert calls == []
    assert hashlib.sha256(owner.path.read_bytes()).hexdigest() == before


def test_non_ownership_failure_is_not_hidden(tmp_path, monkeypatch):
    from packages.web import server

    @asynccontextmanager
    async def broken(*args):
        raise RuntimeError('unrelated startup failure')
        yield

    monkeypatch.setattr(server, '_owned_lifespan', broken)
    with pytest.raises(RuntimeError, match='unrelated startup failure'):
        with TestClient(server.app):
            pass


def test_existing_runtime_lock_is_not_taken_over(tmp_path, administrator):
    from packages.web import server
    owner = OwnerFence(session.SESSION_DIR.parent)
    owner.claim(0)
    owner.set_state('active')
    before = owner.status()
    try:
        with TestClient(server.app, headers=administrator) as client:
            assert client.get('/api/job-maintenance/status').json()['error']['reason'] == 'owner_busy'
            assert client.post('/api/jobs', json={}).status_code == 503
        assert owner.status() == before
        with owner.writer(canonical=True):
            pass
    finally:
        owner.release()


def test_disposition_manifest_is_nonexecuting_and_deletion_stays_blocked(tmp_path, monkeypatch, administrator):
    from packages.web import server
    from packages.automation.auth import UserAuth
    profile = session.SESSION_DIR.parent
    owner = OwnerFence(profile)
    with owner.transaction() as db:
        db.execute("INSERT INTO legacy_leases VALUES('original-unknown-lease')")
    raw = b'{"jobId":"old-job","status":"uncertain","api":"never execute"}'
    source = profile / 'background_jobs/jobs/old-job.json'
    source.parent.mkdir(parents=True)
    source.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (profile / 'job-dispositions.json').write_text(json.dumps({
        'schemaVersion': 1, 'records': [{'id': 'record-1', 'originalId': 'old-job',
        'sourceFile': 'background_jobs/jobs/old-job.json', 'sha256': digest,
        'revision': 1, 'reasonCodes': ['uncertain_effect'], 'executable': False}]}))
    auth = UserAuth(profile / 'automation')
    with TestClient(server.app, headers=administrator) as client:
        headers = administrator
        rows = client.get('/api/job-maintenance/records', headers=headers).json()
        assert rows['items'][0]['originalId'] == 'old-job'
        assert rows['items'][0]['executable'] is False
        assert rows['items'][0]['deleteBlocked'] is True
        response = client.delete('/api/job-maintenance/records/record-1', headers=headers,
                                 params={'expectedRevision': 1, 'expectedSha256': digest})
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'runtime_ownership_unverified'
    assert source.read_bytes() == raw


@pytest.mark.parametrize('change', ['path', 'executable', 'duplicate'])
def test_invalid_manifest_fails_closed_without_touching_source(tmp_path, change):
    from packages.jobs.maintenance import dispositions
    row = {'id': 'one', 'originalId': 'old', 'sourceFile': 'background_jobs/jobs/old.json',
           'sha256': '0' * 64, 'revision': 1, 'reasonCodes': [], 'executable': False}
    if change == 'path':
        row['sourceFile'] = '../outside.json'
    if change == 'executable':
        row['executable'] = True
    (tmp_path / 'job-dispositions.json').write_text(json.dumps({'schemaVersion': 1,
        'records': [row, row] if change == 'duplicate' else [row]}))
    with pytest.raises(ValueError):
        dispositions(tmp_path)


def test_maintenance_real_auth_transport_preserves_login_and_revocation(tmp_path):
    from packages.web import server
    from packages.automation.auth import UserAuth
    from tests.support.auth_credentials import provision
    profile = session.SESSION_DIR.parent
    auth = UserAuth(profile / 'automation')
    password = provision(auth)
    owner = OwnerFence(profile)
    with owner.transaction() as db:
        db.execute("INSERT INTO legacy_leases VALUES('unknown-owner')")
    with TestClient(server.app) as client:
        assert client.get('/api/job-maintenance/status').status_code == 401
        assert not client.get('/api/automation/session').json()['authenticated']
        assert client.post('/api/automation/login', json={'key': password},
                           headers={'Origin': 'http://attacker.test'}).status_code == 403
        bad = client.post('/api/automation/login', json={'key': 'incorrect'}, headers={'Origin': 'http://testserver'})
        assert bad.status_code == 401
        logged = client.post('/api/automation/login', json={'key': password}, headers={'Origin': 'http://testserver'})
        assert logged.status_code == 200
        assert 'HttpOnly' in logged.headers['set-cookie']
        token = logged.json()['token']
        assert client.get('/api/automation/session').json()['authenticated']
        assert client.get('/api/job-maintenance/status').status_code == 200
        assert client.post('/api/sessions', json={}, headers={'Origin': 'http://testserver'}).status_code == 503
        assert client.post('/api/automation/logout', headers={'Origin': 'http://attacker.test'}).status_code == 403
        assert client.post('/api/automation/logout', headers={'Origin': 'http://testserver'}).status_code == 200
        assert not client.get('/api/automation/session').json()['authenticated']
        assert client.get('/api/job-maintenance/status', headers={'Authorization': 'Bearer ' + token}).status_code == 401
    assert server.app.state.job_maintenance is None
