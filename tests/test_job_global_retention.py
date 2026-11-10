"""Global maintenance uses the real canonical service and isolated data only."""
import asyncio
import copy
import json
import time

import pytest

from packages.jobs import retention, final_contracts as fc
from packages.jobs.final_service import FinalService
from tests.test_job_final_http_service import h, USER, enabled, launch, job_def

URL = '/api/job-maintenance/retention-settings'
POINT = 2100000000


def configure(h, rules, key='settings'):
    current = h.client.get(URL)
    assert current.status_code == 200, current.text
    body = {'rules': rules, 'expectedRevision': current.json()['revision'], 'requestKey': key,
            'expectedRequestScope': current.json()['requestScope']}
    result = h.client.put(URL, json=body)
    assert result.status_code == 200, result.text
    return body, result.json()


def complete(h, *, age=10, key='run', item=None):
    if item is None:
        value = job_def(h)
        value['steps'][0]['arguments']['text']['value'] = 'owned-history-' * 4000
        item = enabled(h, value)
    operation = launch(h, item, key)
    asyncio.run(h.service.dispatch_once())
    rid = operation['runId']
    job = h.service.final.store.get(item['id'])
    for row in list(job['finalRuntime']['outbox'].values()):
        h.service.final.settle_notification(item['id'], row['legacyId'], receipt={'outcome': 'succeeded'})
    def age_run(job):
        job['finalRuntime']['runs'][rid]['finishedAt'] = POINT - age * retention.DAY
    h.service.final.store.mutate(item['id'], age_run)
    return item, operation


def test_settings_user_cas_original_key_and_legacy_tombstone(h):
    current = h.client.get(URL).json()
    assert set(current['rules']) == set(retention.RULES)
    assert all(row == {'enabled': False, 'days': None} for row in current['rules'].values())
    assert len(current['requestScope']) == 64
    body, response = configure(h, {k: {'enabled': True, 'days': i + 1} for i, k in enumerate(retention.RULES)})
    assert h.client.put(URL, json=body).json()['operationId'] == response['operationId']
    conflict = h.client.put(URL, json={**body, 'rules': {'logs': {'enabled': False}}})
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'idempotency_conflict'
    stale = h.client.put(URL, json={**body, 'requestKey': 'stale'})
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'revision_conflict'
    assert response['result']['settings']['revision'] == 1
    assert response['result']['settings']['requestScope'] == current['requestScope']
    assert h.client.get(response['links']['self']).json() == response
    assert h.client.put('/api/jobs/settings/completed-retention', json={'enabled': True, 'days': 1}).status_code == 410
    before = h.client.get(URL).json()
    h.client.headers.clear()
    denied_get = h.client.get(URL)
    assert denied_get.status_code == 401 and 'requestScope' not in denied_get.text
    assert h.client.put(URL, json=body).status_code == 401
    with pytest.raises(fc.ContractError) as denied:
        retention.update(h.service, body, actor={'type': 'agent', 'id': 'agent', 'revision': 0})
    assert denied.value.code == 'authorization_denied'
    assert retention.settings(h.service, actor=USER) == before


def test_request_scope_is_stable_opaque_and_bound_to_actual_principal_profile(h, tmp_path):
    first = retention.settings(h.service, actor=USER)['requestScope']
    cold = FinalService(h.root, h.automation.auth)
    other = FinalService(tmp_path / 'other-profile', h.automation.auth)
    try:
        assert retention.settings(cold, actor=USER)['requestScope'] == first
        assert retention.settings(h.service, actor={'type': 'user', 'id': 'another'})['requestScope'] != first
        assert retention.settings(other, actor=USER)['requestScope'] != first
        assert first == fc.digest(h.service._scope(USER, 'update'))
    finally:
        asyncio.run(cold.close())
        asyncio.run(other.close())


def test_invalid_imported_config_fails_closed_and_is_visible(h, monkeypatch):
    monkeypatch.setattr(retention.config, 'load_config', lambda: {'jobs': {
        'completedRetention': {'enabled': 'true', 'days': 1}}})
    item, operation = complete(h)
    current = h.client.get(URL).json()
    assert current['configValidity']['completed'] is False
    assert current['rules']['completed'] == {'enabled': False, 'days': 1}
    assert retention.scan(h.service, at=POINT) == {}
    assert operation['runId'] in h.service.final.store.get(item['id'])['finalRuntime']['runs']
    configure(h, {'completed': {'enabled': True}})
    assert retention.scan(h.service, at=POINT)['completed']['expired'] == 1
    assert h.client.get(URL).json()['configValidity']['completed'] is True


def test_cas_concurrency_owner_fence_and_atomic_rollback(h, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    item, operation = complete(h)
    def change(index):
        try:
            return retention.update(h.service, {'rules': {'completed': {'enabled': True, 'days': 1}},
                'expectedRevision': 0, 'requestKey': 'parallel-' + str(index),
                'expectedRequestScope': retention.request_scope(h.service, USER)}, actor=USER)['status']
        except fc.ContractError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(change, range(2))) == ['completed', 'revision_conflict']
    before = h.service.final.store.get(item['id'])
    save = h.service._save
    def broken(db, kind, identity, value):
        if kind == 'retention_settings' and value['lastRuns']['completed']:
            raise OSError('owned injected commit failure')
        return save(db, kind, identity, value)
    monkeypatch.setattr(h.service, '_save', broken)
    with pytest.raises(OSError, match='owned injected'):
        retention.scan(h.service, at=POINT)
    assert h.service.final.store.get(item['id']) == before
    assert h.client.get(URL).json()['lastRuns']['completed'] is None
    monkeypatch.setattr(h.service, '_save', save)
    h.service.owner.claim(0)
    cold = FinalService(h.root, h.automation.auth)
    try:
        with pytest.raises(fc.ContractError) as denied:
            retention.scan(cold, at=POINT)
        assert denied.value.code == 'revision_conflict' and 'owner' in str(denied.value).lower()
        assert retention.scan(h.service, at=POINT)['completed']['expired'] == 1
    finally:
        asyncio.run(cold.close())


def test_time_creator_trigger_survive_expiry_and_next_fire_is_canonical(h):
    from packages.scheduler.providers import time_definition
    from tests.test_job_final_http_service import create, control
    from tests.test_job_final_creators import config, interval
    item = enabled(h)
    source = create(h, 'event-creators', time_definition('clock', config([interval()])), 'creator')
    trigger = create(h, 'triggers', {'name': 'periodic', 'eventTypes': source['eventTypes'],
        'creatorSelector': {'mode': 'ids', 'ids': [source['id']]}, 'targetJobId': item['id']}, 'trigger')
    assert control(h, 'triggers', trigger, 'enable', 'trigger-enable').status_code == 200
    assert control(h, 'event-creators', source, 'enable', 'creator-enable').status_code == 200
    point = h.service.creators.get(source['id'])['cursor']['rules']['daily']['nextFireAt']
    assert asyncio.run(h.service.creator_runtime.tick(at=point)) == 1
    event = h.service.events.page()[0]
    target = h.service.targets({'type': event['eventType']})[0]
    operation = h.service.admit_event(event, target)
    asyncio.run(h.service.dispatch_once())
    job = h.service.final.store.get(item['id'])
    for row in job['finalRuntime']['outbox'].values():
        h.service.final.settle_notification(item['id'], row['legacyId'], receipt={'outcome': 'succeeded'})
    h.service.final.store.mutate(item['id'], lambda job:
        job['finalRuntime']['runs'][operation['runId']].update(finishedAt=POINT - 10 * retention.DAY))
    configure(h, {'completed': {'enabled': True, 'days': 1}})
    creator_before = h.service.creators.get(source['id'])
    trigger_before = h.client.get('/api/triggers/' + trigger['id']).json()
    assert retention.scan(h.service, at=POINT)['completed']['expired'] == 1
    assert h.service.creators.get(source['id']) == creator_before
    assert h.client.get('/api/triggers/' + trigger['id']).json() == trigger_before
    assert h.service.admit_event(event, target)['runId'] == operation['runId']
    assert h.service.final.query_original(event['eventId'])[0]['expiredRuns'][0]['runId'] == operation['runId']
    point2 = creator_before['cursor']['rules']['daily']['nextFireAt']
    assert asyncio.run(h.service.creator_runtime.tick(at=point2)) == 1
    events = h.service.events.page()
    second = next(row for row in events if row['eventId'] != event['eventId'])
    new = h.service.admit_event(second, target)
    assert new['runId'] != operation['runId']
    asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 2


@pytest.mark.parametrize('patch', [{'enabled': 1}, {'days': True}, {'days': 0}, {'days': 36501}, {'days': 1.1}, {}, {'x': 1}])
def test_invalid_rule_does_not_write(h, patch):
    before = retention.settings(h.service, actor=USER)
    result = h.client.put(URL, json={'rules': {'completed': patch}, 'expectedRevision': 0, 'requestKey': 'invalid',
        'expectedRequestScope': retention.request_scope(h.service, USER)})
    assert result.status_code == 400
    assert retention.settings(h.service, actor=USER) == before


def test_http_agent_global_denial_and_storage_failure_are_zero_write(h, monkeypatch):
    from fastapi.testclient import TestClient
    from packages.automation import authorization
    body = {'rules': {'logs': {'enabled': True, 'days': 1}}, 'expectedRevision': 0, 'requestKey': 'storage',
            'expectedRequestScope': retention.request_scope(h.service, USER)}
    async def trusted(scope, receive, send):
        scope[authorization._SCOPE_KEY] = authorization._MARKER
        await h.app(scope, receive, send)
    with TestClient(trusted) as client:
        for response in (client.get(URL), client.put(URL, json=body)):
            assert response.status_code == 403 and response.json()['error']['code'] == 'authorization_denied'
            assert 'requestScope' not in response.text
    before = h.client.get(URL).json()
    save = h.service._save
    def unavailable(db, kind, identity, value):
        if kind == 'retention_settings':
            raise OSError('owned storage unavailable')
        save(db, kind, identity, value)
    monkeypatch.setattr(h.service, '_save', unavailable)
    response = h.client.put(URL, json=body)
    assert response.status_code == 503 and response.json()['error']['code'] == 'storage_unavailable'
    assert h.client.get(URL).json() == before
    missing = h.client.get('/api/operations', params={'operation': 'update', 'requestKey': 'storage'})
    assert missing.status_code == 404 and missing.json()['error']['code'] == 'not_found'


@pytest.mark.parametrize('scope', [None, True, 3, '', 'a' * 63, 'A' * 64, 'g' * 64])
def test_invalid_expected_request_scope_has_no_operation_or_settings_write(h, scope):
    body = {'rules': {'logs': {'enabled': True, 'days': 1}}, 'expectedRevision': 0,
            'requestKey': 'bad-scope', 'expectedRequestScope': scope}
    before = h.client.get(URL).json()
    response = h.client.put(URL, json=body)
    assert response.status_code == 400 and response.json()['error']['code'] == 'invalid_schema'
    assert h.client.get(URL).json() == before
    assert h.client.get('/api/operations', params={'operation': 'update', 'requestKey': 'bad-scope'}).status_code == 404


def test_profile_and_principal_switch_before_put_are_zero_write_scope_conflicts(h, tmp_path):
    before = h.client.get(URL).json()
    body = {'rules': {'logs': {'enabled': True, 'days': 1}}, 'expectedRevision': 0,
            'requestKey': 'profile-switch', 'expectedRequestScope': before['requestScope']}
    def snapshot(service):
        with service.transaction(readonly=True) as db:
            return (db.execute('SELECT * FROM final_documents ORDER BY kind,id').fetchall(),
                    db.execute('SELECT * FROM final_operations ORDER BY id').fetchall())
    first = snapshot(h.service)
    with pytest.raises(fc.ContractError) as changed:
        retention.update(h.service, body, actor={'type': 'user', 'id': 'other-actual-user'})
    assert changed.value.code == 'request_scope_conflict'
    assert snapshot(h.service) == first
    other = FinalService(tmp_path / 'other-profile', h.automation.auth)
    second = snapshot(other)
    h.app.state.job_final_service = other
    try:
        assert h.client.get(URL).json()['requestScope'] != before['requestScope']
        response = h.client.put(URL, json=body)
        assert response.status_code == 409 and response.json()['error']['code'] == 'request_scope_conflict'
        assert snapshot(other) == second and snapshot(h.service) == first
        absent = h.client.get('/api/operations', params={'operation': 'update', 'requestKey': body['requestKey']})
        assert absent.status_code == 404 and absent.json()['error']['code'] == 'not_found'
    finally:
        h.app.state.job_final_service = h.service
        asyncio.run(other.close())


def test_real_history_reduction_definition_survives_next_run_and_cold_original_key(h):
    item, operation = complete(h)
    jid, rid = item['id'], operation['runId']
    before = h.service.final.store.get(jid)
    size = len(fc.canonical_json(before))
    configure(h, {'completed': {'enabled': True, 'days': 1}})
    result = retention.scan(h.service, at=POINT)
    assert result['completed']['expired'] == 1
    after = h.service.final.store.get(jid)
    assert after['canonical'] == before['canonical'] and after['canonical']['state'] == 'enabled'
    assert len(fc.canonical_json(after)) < size
    assert len(fc.canonical_json(after['finalRuntime'])) < len(fc.canonical_json(before['finalRuntime'])) / 2
    assert rid not in after['finalRuntime']['runs']
    assert rid not in after['finalRuntime']['plans']
    assert 'owned-history-' not in fc.canonical_json(after['finalRuntime'])
    assert h.client.get('/api/job-runs/' + rid).status_code == 410
    assert h.client.get('/api/job-runs/' + rid).json()['error']['code'] == 'history_expired'
    assert h.client.get('/api/jobs/' + jid + '/runs').json()['items'] == []
    cold = FinalService(h.root, h.automation.auth, automation_ledger=h.automation.ledger)
    assert cold.operation(operation['operationId'], actor=USER)['runId'] == rid
    assert cold.operation_for_key('create', 'run', actor=USER)['runId'] == rid
    assert retention.scan(h.service, at=POINT + retention.DAY - 1) == {}
    assert retention.settings(cold, actor=USER)['lastRuns']['completed']['at'] == POINT
    response = h.client.post('/api/jobs/' + jid + '/runs', json={
        'expectedRevision': item['configRevision'], 'requestKey': 'run', 'input': {}, 'cause': {'type': 'manual'}})
    assert response.status_code == 200, response.text
    replay = response.json()
    assert replay['replayed'] and replay['runId'] == rid
    assert len(h.provider.calls) == 1
    original = cold.final.query_original(rid)
    assert original[0]['expiredRuns'][0]['status'] == 'history_expired'
    next_run = launch(h, item, 'new-run')
    asyncio.run(h.service.dispatch_once())
    assert next_run['runId'] != rid and len(h.provider.calls) == 2


@pytest.mark.parametrize('rule,days', [('completed', 1), ('failed', 3), ('timed_out', 5), ('cancelled', 7)])
def test_each_rule_uses_original_status_and_own_days(h, rule, days):
    item, operation = complete(h, age=days - .1)
    rid = operation['runId']
    def outcome(job):
        state = job['finalRuntime']
        state['runs'][rid]['state'] = 'failed' if rule == 'timed_out' else rule
        if rule == 'timed_out':
            effect = state['effects'][0]
            effect['outcome'] = 'rejected'
            effect['receipt'] = {'effectKey': effect['effectKey'], 'outcome': 'rejected',
                'cleanupProof': {'confirmed': True}, 'receipt': {'code': 'timeout'}}
    h.service.final.store.mutate(item['id'], outcome)
    configure(h, {k: {'enabled': True, 'days': days if k == rule else 100} for k in retention.RULES})
    assert retention.scan(h.service, at=POINT)[rule]['expired'] == 0
    assert retention.scan(h.service, at=POINT + retention.DAY)[rule]['expired'] == 1
    assert rid in h.service.final.store.get(item['id'])['finalRuntime']['expiredRuns']


@pytest.mark.parametrize('timeout', [False, True])
def test_real_process_receipt_classifies_failed_versus_timeout(h, timeout, tmp_path):
    import sys
    from packages.core.platform.process import default_process_owner
    from packages.actions.typed_executor import TypedExecutor
    from packages.jobs.final_service import object_ref
    h.service.executor = TypedExecutor(process_owner=default_process_owner(),
        process_timeout=.2 if timeout else 10, terminate_grace=3)
    action = next(a for a in h.service.actions(actor=USER)['items'] if a['id'] == 'product_process_cleanup_v1')
    value = {'name': 'Original timeout proof', 'steps': [{'stepId': 'process',
        'actionRef': object_ref('action', action), 'arguments': {
            'argv': {'from': 'literal', 'value': [sys.executable, '-c',
                'import time; time.sleep(30)' if timeout else 'raise SystemExit(7)']},
            'cwd': {'from': 'literal', 'value': str(tmp_path)}},
        'completion': {'contract': 'process-exit'}}], 'retention': {'policyRef': 'owned'}}
    item = enabled(h, value)
    operation = launch(h, item)
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.store.get(item['id'])['finalRuntime']
    run = state['runs'][operation['runId']]
    assert run['state'] == 'failed'
    receipt = state['effects'][0]['receipt']
    assert receipt['outcome'] == 'rejected' and receipt['cleanupProof']['confirmed'] is True
    assert (receipt['receipt'].get('code') == 'timeout') is timeout
    assert retention.run_rule(state, run) == ('timed_out' if timeout else 'failed')
    damaged = copy.deepcopy(state)
    damaged['effects'][0]['receipt']['effectKey'] = {**state['effects'][0]['effectKey'], 'runId': 'other'}
    assert retention.run_rule(damaged, run) == 'failed'
    for row in state['outbox'].values():
        h.service.final.settle_notification(item['id'], row['legacyId'], receipt={'outcome': 'succeeded'})
    h.service.final.store.mutate(item['id'], lambda job:
        job['finalRuntime']['runs'][run['runId']].update(finishedAt=POINT - 10 * retention.DAY))
    rule = 'timed_out' if timeout else 'failed'
    configure(h, {rule: {'enabled': True, 'days': 1}})
    assert retention.scan(h.service, at=POINT)[rule]['expired'] == 1


@pytest.mark.parametrize('hold', ['pin', 'bad_pin', 'unknown', 'active', 'outbox', 'effect', 'cleanup', 'rerun',
                                 'profile', 'noncanonical', 'run_profile'])
def test_protected_resources_are_not_expired(h, hold):
    item, operation = complete(h)
    rid = operation['runId']
    def protect(job):
        state = job['finalRuntime']
        if hold in {'pin', 'bad_pin'}:
            job['runtimeMetadata'] = {'pinned': True if hold == 'pin' else 'unknown'}
        elif hold == 'unknown':
            state['runs'][rid]['state'] = 'uncertain'
        elif hold == 'active':
            state['runs']['active'] = {'runId': 'active', 'state': 'running'}
        elif hold == 'outbox':
            next(iter(state['outbox'].values()))['status'] = 'uncertain'
        elif hold == 'effect':
            state['effects'][0]['outcome'] = 'unknown'
        elif hold == 'cleanup':
            state['effects'][0]['receipt']['cleanupProof'] = {'confirmed': False}
        elif hold == 'profile':
            job['canonical']['profileId'] = 'unproven-profile'
        elif hold == 'noncanonical':
            job.pop('canonical')
        elif hold == 'run_profile':
            state['runs'][rid]['profileId'] = 'unproven-run-profile'
        else:
            state['runs']['child'] = {'runId': 'child', 'state': 'completed', 'rerunOf': rid}
    h.service.final.store.mutate(item['id'], protect)
    before = h.service.final.store.get(item['id'])
    configure(h, {'completed': {'enabled': True, 'days': 1}})
    retention.scan(h.service, at=POINT)
    assert h.service.final.store.get(item['id']) == before


def test_disabled_null_days_defaults_and_daily_gate_survive_owner_restart(h):
    h.service.owner.claim(0)
    item, operation = complete(h)
    configure(h, {'completed': {'enabled': True}})
    assert retention.scan(h.service, at=POINT) == {}
    configure(h, {'completed': {'days': 1}}, 'days')
    assert retention.scan(h.service, at=POINT)['completed']['expired'] == 1
    asyncio.run(h.service.close())
    cold = FinalService(h.root, h.automation.auth, automation_ledger=h.automation.ledger)
    cold.owner.claim(cold.owner.status()['epoch'])
    try:
        assert retention.scan(cold, at=POINT + 1) == {}
        configure_result = retention.update(cold, {'rules': {'completed': {'enabled': False}},
            'expectedRevision': 2, 'requestKey': 'disable', 'expectedRequestScope': retention.request_scope(cold, USER)}, actor=USER)
        assert configure_result['result']['settings']['rules']['completed'] == {'enabled': False, 'days': 1}
        assert retention.scan(cold, at=POINT + 2 * retention.DAY) == {}
    finally:
        asyncio.run(cold.close())


def test_production_pulse_cleans_history_without_resetting_run_limit(h):
    item = enabled(h, job_def(h, runLimit={'maxRuns': 1, 'counterDomain': 'run-admission'}))
    item, operation = complete(h, item=item)
    h.service.final.store.mutate(item['id'], lambda job:
        job['finalRuntime']['runs'][operation['runId']].update(finishedAt=time.time() - 10 * retention.DAY))
    configure(h, {'completed': {'enabled': True, 'days': 1}})
    asyncio.run(h.service.tick())
    assert h.client.get('/api/job-runs/' + operation['runId']).json()['error']['code'] == 'history_expired'
    observed = h.client.get(URL).json()
    assert observed['lastRuns']['completed']['expired'] == 1 and observed['revision'] == 1
    assert h.client.put(URL, json={'expectedRevision': observed['revision'], 'requestKey': 'after-pulse',
        'rules': {'logs': {'days': 3}}, 'expectedRequestScope': observed['requestScope']}).status_code == 200
    response = h.client.post('/api/jobs/' + item['id'] + '/runs', json={'expectedRevision': item['configRevision'],
        'requestKey': 'new', 'input': {}, 'cause': {'type': 'manual'}})
    assert response.status_code == 429 and response.json()['error']['code'] == 'capacity_exceeded'
    assert len(h.provider.calls) == 1
