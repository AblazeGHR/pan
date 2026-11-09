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
    canonical_root = tmp_path / 'canonical'
    service = bind(app, automation, canonical_root)
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
    yield client, service, owned.id, canonical_root
    client.close()


def create_process(client, session_id, **extra):
    body = {'kind': 'background-process', 'name': 'Owned process', 'label': 'owned',
            'target': {'sessionId': session_id}, 'argv': ['cmd', '/c', 'echo', 'hi'],
            'cwd': 'C:/owned', 'hideWindow': True, **extra}
    return client.post('/api/jobs/legacy-create', json=body)


def test_create_list_detail_pin_delete_use_the_canonical_authority(job_http):
    client, service, session_id, canonical_root = job_http
    # Keep this lifecycle test free of an immediate Run so delete can retire.
    created = create_process(client, session_id, enabled=False)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body['ok'] is True
    job = body['job']
    assert job['jobId'].startswith('job_')
    assert job['kind'] == 'background-process'
    assert job['status'] == 'draft' and job['enabled'] is False
    assert job['target'] == {'type': 'session', 'sessionId': session_id}
    # The record is canonical, not a legacy JSON job.
    canonical = service.get('job', job['jobId'], actor={'type': 'user', 'id': 'local'})
    assert canonical['name'] == 'Owned process'

    listed = client.get('/api/jobs/legacy-list').json()
    assert [item['jobId'] for item in listed['jobs']].count(job['jobId']) == 1

    detail = client.get(f"/api/jobs/legacy/{job['jobId']}").json()
    assert detail['job']['argv'] == ['cmd', '/c', 'echo', 'hi']

    pinned = client.put(f"/api/jobs/legacy/{job['jobId']}/pin", json={'pinned': True}).json()
    assert pinned['job']['pinned'] is True
    assert pinned['job']['status'] == 'draft'  # pin never changes status

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
    client, service, session_id, canonical_root = job_http
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
    client, _, session_id, canonical_root = job_http
    unsupported = client.post('/api/jobs/legacy-create', json={'kind': 'scheduled-task', 'name': 'x'})
    assert unsupported.status_code == 400
    assert unsupported.json()['error']['code'] == 'unsupported_operation'

    missing = client.post('/api/jobs/legacy-create', json={'kind': 'session-message',
                                                    'target': {'sessionId': session_id}, 'text': 'hi'})
    assert missing.status_code == 200, missing.text
    assert missing.json()['job']['kind'] == 'session-message'


def test_legacy_rows_keep_their_pin_and_are_listed_once(job_http, monkeypatch):
    client, service, session_id, canonical_root = job_http
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
    client, service, session_id, canonical_root = job_http
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
    assert replay.status_code == 200, replay.text
    assert replay.json()['job']['jobId'] == job_id


def test_broadcast_failure_rolls_back_the_whole_graph(job_http, monkeypatch):
    client, service, session_id, canonical_root = job_http
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


def test_background_process_starts_once_and_replays_the_same_run(job_http):
    client, service, session_id, canonical_root = job_http
    body = {'kind': 'background-process', 'name': 'Immediate', 'target': {'sessionId': session_id},
            'argv': ['cmd', '/c', 'echo', 'hi'], 'cwd': 'C:/owned', 'enabled': True}
    first = client.post('/api/jobs/legacy-create', json=body, headers={'x-pan-request-key': 'bp-1'})
    assert first.status_code == 200, first.text
    assert first.json()['phase'] == 'started'
    job_id = first.json()['job']['jobId']
    runs = client.get(f'/api/jobs/{job_id}/runs').json()['items']
    assert len(runs) == 1
    # A replayed creation returns the same Job and never admits a second Run.
    replay = client.post('/api/jobs/legacy-create', json=body, headers={'x-pan-request-key': 'bp-1'})
    assert replay.status_code == 200, replay.text
    assert replay.json()['job']['jobId'] == job_id
    assert len(client.get(f'/api/jobs/{job_id}/runs').json()['items']) == 1
    conflict = client.post('/api/jobs/legacy-create', json={**body, 'argv': ['cmd', '/c', 'echo', 'bye']},
                           headers={'x-pan-request-key': 'bp-1'})
    assert conflict.status_code == 409
    assert conflict.json()['error']['code'] == 'idempotency_conflict'


def test_rejected_start_reports_the_enabled_phase_without_claiming_a_run(job_http, monkeypatch):
    client, service, session_id, canonical_root = job_http
    body = {'kind': 'background-process', 'name': 'No start', 'target': {'sessionId': session_id},
            'argv': ['cmd', '/c', 'echo', 'hi'], 'cwd': 'C:/owned', 'enabled': True}

    def refuse(*_args, **_kwargs):
        from packages.jobs import final_contracts as fc
        raise fc.ContractError('reference_conflict', 'start refused by owner fence')

    monkeypatch.setattr(service, 'create_run', refuse)
    response = client.post('/api/jobs/legacy-create', json=body, headers={'x-pan-request-key': 'bp-2'})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['phase'] == 'enabled'
    assert payload['start'] == {'status': 'rejected', 'code': 'reference_conflict'}
    job_id = payload['job']['jobId']
    assert client.get(f'/api/jobs/legacy/{job_id}').json()['job']['enabled'] is True
    assert client.get(f'/api/jobs/{job_id}/runs').json()['items'] == []


def _cold_client(canonical_root):
    """A second app/service over the same canonical root (true cold reader)."""
    from fastapi import FastAPI
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from packages.automation import service as automation_service
    from packages.jobs import final_api
    from tests.test_action_unification_p1 import bind
    app = FastAPI()
    automation = automation_service.Service(canonical_root.parent / 'automation', SimpleNamespace(workers={}), None)
    automation.auth = final_api.CanonicalUserAuth(canonical_root.parent / 'automation')
    service = bind(app, automation, canonical_root)
    app.state.automation = automation
    final_api.install(app)
    client = TestClient(app, raise_server_exceptions=False)
    client.headers['Authorization'] = 'Bearer ' + automation.auth.login(automation.auth.key())
    return client, service


def _process_body(session_id, name='Cold process'):
    return {'kind': 'background-process', 'name': name, 'target': {'sessionId': session_id},
            'argv': ['cmd', '/c', 'echo', 'hi'], 'cwd': 'C:/owned', 'enabled': True}


def test_cold_service_shares_scope_and_replays_the_original_run(job_http):
    client, service, session_id, canonical_root = job_http
    body = _process_body(session_id)
    first = client.post('/api/jobs/legacy-create', json=body, headers={'x-pan-request-key': 'cold-1'})
    assert first.status_code == 200, first.text
    job_id = first.json()['job']['jobId']
    cold, cold_service = _cold_client(canonical_root)
    # The scope identity must match before any replay claim is meaningful.
    assert cold_service.creators.root == service.creators.root
    assert cold_service.creators.profile_id == service.creators.profile_id
    replay = cold.post('/api/jobs/legacy-create', json=body, headers={'x-pan-request-key': 'cold-1'})
    assert replay.status_code == 200, replay.text
    assert replay.json()['job']['jobId'] == job_id
    assert len(cold.get(f'/api/jobs/{job_id}/runs').json()['items']) == 1


def test_cold_service_does_not_duplicate_a_run_after_a_rejected_start(job_http, monkeypatch):
    client, service, session_id, canonical_root = job_http
    body = _process_body(session_id, 'Cold rejected')

    def refuse(*_args, **_kwargs):
        from packages.jobs import final_contracts as fc
        raise fc.ContractError('reference_conflict', 'start refused')

    monkeypatch.setattr(service, 'create_run', refuse)
    first = client.post('/api/jobs/legacy-create', json=body, headers={'x-pan-request-key': 'cold-2'})
    assert first.status_code == 200, first.text
    assert first.json()['phase'] == 'enabled'
    job_id = first.json()['job']['jobId']
    cold, _ = _cold_client(canonical_root)
    replay = cold.post('/api/jobs/legacy-create', json=body, headers={'x-pan-request-key': 'cold-2'})
    assert replay.status_code == 200, replay.text
    assert replay.json()['job']['jobId'] == job_id
    assert len(cold.get(f'/api/jobs/{job_id}/runs').json()['items']) <= 1


def test_cold_recovery_after_only_the_job_write(job_http):
    """Response lost after write: a cold replay completes the same chain."""
    client, service, session_id, canonical_root = job_http
    body = _process_body(session_id, 'Lost after write')
    from packages.jobs import legacy_job_compat
    definition, legacy = legacy_job_compat.definition_for(service, {'type': 'user', 'id': 'local'}, body)
    written = service.write('job', None, {'definition': definition, 'expectedRevision': 0,
                                          'requestKey': 'lost-write'}, actor={'type': 'user', 'id': 'local'})
    job_id = written['result']['object']['id']
    cold, _ = _cold_client(canonical_root)
    replay = cold.post('/api/jobs/legacy-create', json=body, headers={'x-pan-request-key': 'lost-write'})
    assert replay.status_code == 200, replay.text
    assert replay.json()['job']['jobId'] == job_id
    assert replay.json()['phase'] == 'started'
    assert len(cold.get(f'/api/jobs/{job_id}/runs').json()['items']) == 1


def test_cold_recovery_after_enable(job_http):
    """Response lost after enable: the replay starts exactly one Run."""
    client, service, session_id, canonical_root = job_http
    body = _process_body(session_id, 'Lost after enable')
    who = {'type': 'user', 'id': 'local'}
    from packages.jobs import legacy_job_compat
    definition, _ = legacy_job_compat.definition_for(service, who, body)
    job = service.write('job', None, {'definition': definition, 'expectedRevision': 0,
                                      'requestKey': 'lost-enable'}, actor=who)['result']['object']
    job = service.control('job', job['id'], {
        'operation': 'enable', 'expectedRevision': job['configRevision'],
        'expectedStateRevision': job['stateRevision'], 'parameters': {},
        'requestKey': legacy_job_compat.stage_key('lost-enable', 'enable')}, actor=who)['result']['object']
    cold, _ = _cold_client(canonical_root)
    replay = cold.post('/api/jobs/legacy-create', json=body, headers={'x-pan-request-key': 'lost-enable'})
    assert replay.status_code == 200, replay.text
    assert replay.json()['job']['jobId'] == job['id']
    runs = cold.get(f"/api/jobs/{job['id']}/runs").json()['items']
    assert len(runs) == 1


# ── Authorization / owner fencing ────────────────────────────────────────

class _Delegated:
    """The trusted local bridge marker, as used by the MCP transport.

    Test-only wrapper: it exercises the same ``actor()`` boundary the real
    bridge uses instead of patching permission checks in the product.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from packages.automation import authorization
        scope['pan.authorization.transport'] = authorization._MARKER
        await self.app(scope, receive, send)


def _delegated_client(client):
    from fastapi.testclient import TestClient
    return TestClient(_Delegated(client.app), raise_server_exceptions=False)


def _grant(auth, **job):
    state = auth.permissions_status()
    grants = state['permissions']
    grants['job'] = {key: True for key in ('read', 'create', 'updateConfig', 'lifecycle', 'execute', 'delete')}
    grants['job'].update(job)
    grants['action']['read'] = True
    grants['action']['manageSystemActions'] = True
    return auth.change_permissions(grants, state['revision'])


def _enabled_process(session_id, name='Fenced process'):
    return {'kind': 'background-process', 'name': name, 'target': {'sessionId': session_id},
            'argv': ['cmd', '/c', 'echo', 'hi'], 'cwd': 'C:/owned', 'enabled': True}


def test_agent_capabilities_gate_create_start_cancel_and_logs(job_http):
    client, service, session_id, canonical_root = job_http
    delegated = _delegated_client(client)
    _grant(service.auth)
    created = delegated.post('/api/jobs/legacy-create', json=_enabled_process(session_id, 'Permission process'),
                              headers={'x-pan-request-key': 'perm-1'})
    assert created.status_code == 200, created.text
    job = created.json()['job']
    assert created.json()['phase'] == 'started'
    run_id = delegated.get(f"/api/jobs/{job['jobId']}/runs").json()['items'][0]['runId']

    # Revoked create: no new Job may appear.
    _grant(service.auth, create=False)
    refused = delegated.post('/api/jobs/legacy-create', json=_enabled_process(session_id, 'No create'),
                             headers={'x-pan-request-key': 'perm-2'})
    assert refused.status_code == 403
    assert refused.json()['error']['code'] == 'authorization_denied'

    # Revoked execute: the Job can be written/enabled, but start is refused and
    # the response never claims the process started.
    _grant(service.auth)
    _grant(service.auth, execute=False)
    phase = delegated.post('/api/jobs/legacy-create', json=_enabled_process(session_id, 'No start'),
                           headers={'x-pan-request-key': 'perm-3'})
    assert phase.status_code == 200, phase.text
    assert phase.json()['phase'] == 'enabled'
    assert phase.json()['start']['status'] == 'rejected'
    assert phase.json()['start']['code'] == 'authorization_denied'
    no_start_job = phase.json()['job']['jobId']
    assert delegated.get(f'/api/jobs/{no_start_job}/runs').json()['items'] == []
    assert delegated.post(f'/api/jobs/{job["jobId"]}/cancel', json={}).status_code == 403

    # Revoked read: list and log reads are denied before any execution lookup.
    _grant(service.auth)
    _grant(service.auth, read=False)
    assert delegated.get('/api/jobs/legacy-list').status_code == 403
    log = delegated.get(f'/api/job-runs/{run_id}/attempts/attempt_missing/log')
    assert log.status_code == 403
    assert delegated.get(f'/api/job-runs/{run_id}/logs').status_code == 403

    # Restored read: the same log address reaches the execution boundary (404
    # for an unknown attempt, not 403).
    _grant(service.auth)
    assert delegated.get(f'/api/job-runs/{run_id}/attempts/attempt_missing/log').status_code == 404
    assert delegated.post(f'/api/jobs/{job["jobId"]}/cancel', json={}).status_code == 200


def test_owner_epoch_blocks_writes_and_cancel_but_not_reads(job_http):
    client, service, session_id, canonical_root = job_http
    delegated = _delegated_client(client)
    _grant(service.auth)
    created = delegated.post('/api/jobs/legacy-create', json=_enabled_process(session_id, 'Owner process'),
                              headers={'x-pan-request-key': 'owner-1'})
    assert created.status_code == 200, created.text
    job_id = created.json()['job']['jobId']
    run_id = delegated.get(f'/api/jobs/{job_id}/runs').json()['items'][0]['runId']

    from packages.jobs.runtime_owner import OwnerFence
    # The runtime owner lives one level above the canonical store (profile root).
    other = OwnerFence(canonical_root.parent)
    epoch = (service.owner.status() or {'epoch': 0})['epoch']
    other.claim(epoch)
    other.set_state('active')
    try:
        # A different owner epoch fences every canonical write, with no partial
        # Job/Run/cleanup left behind.
        write = delegated.post('/api/jobs/legacy-create', json=_enabled_process(session_id, 'No owner'),
                               headers={'x-pan-request-key': 'owner-2'})
        assert write.status_code == 409
        assert write.json()['error']['code'] == 'revision_conflict'
        assert delegated.post(f'/api/jobs/{job_id}/run-now', json={}).status_code == 409
        cancel = delegated.post(f'/api/jobs/{job_id}/cancel', json={})
        assert cancel.status_code == 409
        assert cancel.json()['failed'][0]['code'] == 'revision_conflict'
        runs = delegated.get(f'/api/jobs/{job_id}/runs').json()['items']
        assert runs[0]['state'] == 'queued', 'a fenced cancel must not change the Run'
        # Reads (list, history, log discovery) stay available: the owner fence
        # excludes writers, it is not a read permission.
        assert delegated.get('/api/jobs/legacy-list').status_code == 200
        refs = delegated.get(f'/api/job-runs/{run_id}/logs')
        assert refs.status_code == 200
    finally:
        other.release()
