"""Legacy Job surface over the canonical single authority (isolated HTTP)."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from packages.automation import service as automation_service
from packages.jobs import final_api
from tests.test_action_unification_p1 import bind


@pytest.fixture
def job_http(tmp_path, monkeypatch):
    app = FastAPI()
    automation = automation_service.Service(tmp_path / 'automation', SimpleNamespace(workers={}), None)
    automation.auth = final_api.CanonicalUserAuth(tmp_path / 'automation')
    service = bind(app, automation, tmp_path / 'canonical')
    app.state.automation = automation
    final_api.install(app)
    # Pin metadata is the only sidecar; keep it inside the isolated runtime.
    from packages.core.platform import data_paths
    monkeypatch.setattr(data_paths, 'job_root', lambda: tmp_path / 'jobs')
    token = automation.auth.login(automation.auth.key())
    client = TestClient(app, raise_server_exceptions=False)
    client.headers['Authorization'] = 'Bearer ' + token
    from packages.core import session as session_store
    session_store.SESSION_DIR = tmp_path / 'sessions'
    session_store._cache.clear()
    session_store._all_loaded = False
    owned = session_store.create('Owned session', adapter='cbc', workdir=str(tmp_path))
    yield client, service, owned.id
    client.close()


def create_process(client, session_id, **extra):
    body = {'kind': 'background-process', 'name': 'Owned process', 'label': 'owned',
            'target': {'sessionId': session_id}, 'argv': ['cmd', '/c', 'echo', 'hi'],
            'cwd': 'C:/owned', 'hideWindow': True, **extra}
    return client.post('/api/jobs/legacy-create', json=body)


def test_create_list_detail_pin_delete_use_the_canonical_authority(job_http):
    client, service, session_id = job_http
    created = create_process(client, session_id)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body['ok'] is True
    job = body['job']
    assert job['jobId'].startswith('job_')
    assert job['kind'] == 'background-process'
    assert job['status'] == 'scheduled' and job['enabled'] is True
    assert job['target'] == {'sessionId': session_id}
    # The record is canonical, not a legacy JSON job.
    canonical = service.get('job', job['jobId'], actor={'type': 'user', 'id': 'local'})
    assert canonical['name'] == 'Owned process'

    listed = client.get('/api/jobs/legacy-list').json()
    assert [item['jobId'] for item in listed['jobs']].count(job['jobId']) == 1

    detail = client.get(f"/api/jobs/legacy/{job['jobId']}").json()
    assert detail['job']['argv'] == ['cmd', '/c', 'echo', 'hi']

    pinned = client.put(f"/api/jobs/legacy/{job['jobId']}/pin", json={'pinned': True}).json()
    assert pinned['job']['pinned'] is True
    assert pinned['job']['status'] == 'scheduled'  # pin never changes status

    renamed = client.patch(f"/api/jobs/legacy/{job['jobId']}", json={'name': 'Renamed process'})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()['job']['name'] == 'Renamed process'
    assert service.get('job', job['jobId'], actor={'type': 'user', 'id': 'local'})['name'] == 'Renamed process'

    deleted = client.delete(f"/api/jobs/{job['jobId']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()['retired'] is True
    assert 'deleted' not in deleted.json()
    assert service.get('job', job['jobId'], actor={'type': 'user', 'id': 'local'})['state'] == 'retired'
    assert job['jobId'] not in [item['jobId'] for item in client.get('/api/jobs/legacy-list').json()['jobs']]


def test_manual_run_is_idempotent_and_never_advances_a_schedule(job_http):
    client, service, session_id = job_http
    job = create_process(client, session_id, name='Run me').json()['job']
    key = 'manual-run-1'
    first = client.post(f"/api/jobs/{job['jobId']}/run-now",
                        headers={'x-pan-request-key': key})
    assert first.status_code == 200, first.text
    runs = client.get(f"/api/jobs/{job['jobId']}/runs").json()['items']
    assert len(runs) == 1
    # The same request key replays the receipt instead of starting a second Run.
    again = client.post(f"/api/jobs/{job['jobId']}/run-now",
                        headers={'x-pan-request-key': key})
    assert again.status_code == 200
    assert len(client.get(f"/api/jobs/{job['jobId']}/runs").json()['items']) == 1
    # A background process has no schedule cursor at all; the manual Run must
    # not fabricate one on the Job either.
    detail = client.get(f"/api/jobs/legacy/{job['jobId']}").json()['job']
    assert detail.get('nextRunAt') in (None, '')
    assert detail['kind'] == 'background-process'
    # An active Run blocks retirement; cancel it with its own CAS first.
    blocked = client.delete(f"/api/jobs/{job['jobId']}")
    assert blocked.status_code == 409, blocked.text
    cancelled = client.post(f"/api/jobs/{job['jobId']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    # Cancellation is asynchronous: until the Run settles, retirement stays
    # blocked and the history remains readable.
    still = client.delete(f"/api/jobs/{job['jobId']}")
    assert still.status_code == 409
    assert client.get(f"/api/jobs/legacy/{job['jobId']}").json()['job']['retired'] is False


def test_unknown_kind_and_legacy_kind_gaps_are_explicit(job_http):
    client, _, session_id = job_http
    unsupported = client.post('/api/jobs/legacy-create', json={'kind': 'scheduled-task', 'name': 'x'})
    assert unsupported.status_code == 400
    assert unsupported.json()['error']['code'] == 'unsupported_operation'

    missing = client.post('/api/jobs/legacy-create', json={'kind': 'session-message',
                                                    'target': {'sessionId': session_id}, 'text': 'hi'})
    assert missing.status_code == 200, missing.text
    assert missing.json()['job']['kind'] == 'session-message'


def test_legacy_rows_keep_their_pin_and_are_listed_once(job_http, monkeypatch):
    client, service, session_id = job_http
    from packages.jobs import api as legacy_api
    legacy_row = {'jobId': 'job_legacy_owned', 'kind': 'background-process', 'name': 'Legacy owned',
                  'status': 'completed', 'pinned': True, 'runCount': 1}

    async def fake_list(kind=None, status=None, includeCompleted=True, request=None):
        return {'ok': True, 'jobs': [dict(legacy_row)], 'pinMetadataStatus': 'available'}

    monkeypatch.setattr(legacy_api, 'list_jobs', fake_list)
    job = create_process(client, session_id, name='Canonical owned').json()['job']
    client.put(f"/api/jobs/legacy/{job['jobId']}/pin", json={'pinned': True})
    rows = client.get('/api/jobs/legacy-list').json()['jobs']
    assert [row['jobId'] for row in rows].count('job_legacy_owned') == 1
    legacy = next(row for row in rows if row['jobId'] == 'job_legacy_owned')
    assert legacy['pinned'] is True, 'the old pin fact must not be lost'
    canonical = next(row for row in rows if row['jobId'] == job['jobId'])
    assert canonical['pinned'] is True
    assert len([row for row in rows if row['jobId'] == job['jobId']]) == 1


def _broadcast_body(session_id, text='broadcast hello'):
    from datetime import datetime, timedelta, timezone
    at = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    return {'kind': 'session-broadcast', 'name': 'Broadcast', 'target': {'sessionIds': [session_id]},
            'text': text, 'schedule': {'type': 'once', 'at': at}}


def test_broadcast_same_key_with_different_input_conflicts(job_http):
    client, service, session_id = job_http
    body = _broadcast_body(session_id)
    first = client.post('/api/jobs/legacy-create', json=body,
                        headers={'x-pan-request-key': 'bc-key'})
    assert first.status_code == 200, first.text
    job_id = first.json()['job']['jobId']
    conflict = client.post('/api/jobs/legacy-create', json={**body, 'text': 'other text'},
                           headers={'x-pan-request-key': 'bc-key'})
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()['error']['code'] == 'idempotency_conflict'
    replay = client.post('/api/jobs/legacy-create', json=body,
                         headers={'x-pan-request-key': 'bc-key'})
    assert replay.status_code == 200
    assert replay.json()['job']['jobId'] == job_id


def test_broadcast_failure_rolls_back_the_whole_graph(job_http, monkeypatch):
    client, service, session_id = job_http
    before = {item['id'] for item in service.list('job', actor={'type': 'user', 'id': 'local'})['items']}

    def boom(*_args, **_kwargs):
        raise RuntimeError('creator store unavailable')

    monkeypatch.setattr(service.creators, 'create', boom)
    response = client.post('/api/jobs/legacy-create', json=_broadcast_body(session_id, 'rollback'),
                           headers={'x-pan-request-key': 'bc-rollback'})
    assert response.status_code == 500
    after = {item['id'] for item in service.list('job', actor={'type': 'user', 'id': 'local'})['items']}
    assert after == before, 'a failed composition must not leave a partial Job'
    rows = []
    with service.transaction(readonly=True) as db:
        rows = db.execute("SELECT id FROM final_documents WHERE kind IN ('trigger','event_creator')").fetchall()
        bindings = db.execute("SELECT id FROM final_documents WHERE kind='legacy_job_binding' AND id LIKE 'job_%'").fetchall()
    assert not [row for row in rows if row[0].startswith(('trg_', 'creator_'))]
    assert not bindings
