"""Owned authenticated random-port HTTP -> canonical Run -> original Wake."""
import asyncio
import copy
import json
import sqlite3

import pytest

from packages.actions import session_resume
from packages.actions.typed_executor import TypedExecutor
from packages.core import session as sessions, worker, legal_offline
from packages.jobs.final_service import FinalService, definition
from tests.test_session_product_closure import h
from tests.test_job_final_http_service import enabled, launch, control


def config(h):
    data = h.client.get('/api/jobs/actions').json()
    ref = data['references']['product_resume_legal_running_v1']
    return {'name': 'Resume', 'steps': [{'stepId': 'resume', 'actionRef': ref, 'arguments': {},
        'completion': {'contract': 'durable-queue-accepted'}}], 'retention': {'policyRef': 'test'}}


def illegal(s):
    # Use the original authority, not last_legal_worker_state projections.
    identity = {'token': s.id, 'generation': 1, 'executionSeq': 1}
    legal_offline.begin(s, identity)
    legal_offline.detect(s, 'crash', eligible=lambda _: True, remember=lambda _: None)
    sessions.save(s)


def run(h, rid):
    return h.client.get('/api/job-runs/' + rid).json()


def internal(host, job_id):
    with host.transaction(readonly=True) as db:
        return host._get(db, 'job', job_id)['finalRuntime']


def test_http_fresh_query_per_fire_original_wake_pause_and_identity(h, monkeypatch, record_property):
    job = enabled(h, config(h))
    a, live, idle, unknown = (h.owned[k] for k in ('ses_a', 'ses_b', 'ses_source', 'ses_new'))
    illegal(a)
    illegal(live)
    idle.last_legal_worker_state = 'running'  # obsolete projection must not select it
    monkeypatch.setattr(worker, 'find_alive_worker_by_session', lambda sid: object() if sid == live.id else None)
    calls, disk = [], []
    original = worker.wake_session
    async def record(sid, **kwargs):
        # Independent SQLite connection proves the selection committed before
        # any Wake, including the canonical effect admission identity.
        with sqlite3.connect(h.service.creators.path) as db:
            stored = json.loads(db.execute("SELECT document FROM final_documents WHERE kind='job' AND id=?", (job['id'],)).fetchone()[0])
        state = stored['finalRuntime']
        disk.append(copy.deepcopy(state))
        assert next(iter(state['plans'].values()))['resumeSelections']['resume'] == [a.id]
        assert state['effectAdmissions']
        record_property('beforeEffectDiskProof', json.dumps({'selected': next(iter(state['plans'].values()))['resumeSelections'],
            'effectKeys': [v['effectKey'] for v in state['effectAdmissions'].values()]}))
        calls.append((sid, kwargs['client_message_id']))
        return await original(sid, **kwargs)
    monkeypatch.setattr(worker, 'wake_session', record)
    first = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    assert run(h, first['runId'])['state'] == 'completed'
    assert [sid for sid, _ in calls] == [a.id]
    row = a.queue_pending[0]
    assert row['text'] == '继续' and row['source'] == 'automation' and a.queue_paused
    assert calls[0][1].startswith('job-resume:')
    assert not live.queue_pending and not idle.queue_pending and not unknown.queue_pending
    receipt = h.service.final.history(job['id'])['effects'][0]['receipt']
    child = receipt['receipt']['results'][0]['effectKey']
    assert child['runId'] == first['runId'] and child['targetId'] == a.id
    assert calls[0][1] == session_resume.client_message_id(child)
    record_property('originalIdentity', json.dumps({'effectKey': child, 'clientMessageId': calls[0][1], 'queueItemId': row['id']}))
    for identity in (calls[0][1], row['id']):
        assert h.service.final.query_original(identity)[0]['runs'][0]['runId'] == first['runId']
    # Repeated public admission key and dispatch cannot create another Wake.
    replay = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
        'expectedRevision': job['configRevision'], 'requestKey': 'run', 'input': {}, 'cause': {'type': 'manual'}})
    assert replay.status_code == 200 and replay.json()['runId'] == first['runId']
    asyncio.run(h.service.dispatch_once())
    assert len(calls) == 1
    monkeypatch.setattr(worker, 'wake_session', original)
    a.execution_operations.clear()
    sessions.save(a)
    illegal(unknown)
    second = launch(h, job, key='next-fire')
    asyncio.run(h.service.dispatch_once())
    assert run(h, second['runId'])['state'] == 'completed'
    second_receipt = h.service.final.history(job['id'])['effects'][-1]['receipt']
    assert second_receipt['targetSelection']['sessionIds'] == [unknown.id]
    assert session_resume.client_message_id(second_receipt['receipt']['results'][0]['effectKey']) != calls[0][1]
    assert disk


@pytest.mark.parametrize('mode', ['lost', 'cancelled', 'before-wake'])
def test_cold_reopen_original_selection_and_config_change_never_resends(h, monkeypatch, mode):
    job = enabled(h, config(h))
    illegal(h.owned['ses_a'])
    original = worker.wake_session
    calls = []
    async def interrupted(sid, **kwargs):
        calls.append(kwargs['client_message_id'])
        if mode != 'before-wake':
            await original(sid, **kwargs)
        if mode == 'cancelled':
            raise asyncio.CancelledError('owned interrupted effect')
        return {}  # Missing status never proves success.
    monkeypatch.setattr(worker, 'wake_session', interrupted)
    first = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    assert run(h, first['runId'])['state'] == 'uncertain'
    illegal(h.owned['ses_b'])
    monkeypatch.setattr(worker, 'configured_wake_message', lambda: 'new configured message')
    def no_query():
        pytest.fail('Cold reconciliation must never rescan')
    monkeypatch.setattr(session_resume, 'select', no_query)
    async def no_wake(*args, **kwargs):
        pytest.fail('Cold reconciliation must never Wake')
    monkeypatch.setattr(worker, 'wake_session', no_wake)
    cold = FinalService(h.root, h.automation.auth)
    try:
        cold.recover()
        asyncio.run(cold.recover_session_receipts())
        asyncio.run(cold.dispatch_once())
        state = cold.final.get(job['id'])
        assert state['runs'][first['runId']]['state'] == ('uncertain' if mode == 'before-wake' else 'completed')
        assert internal(cold, job['id'])['plans'][first['runId']]['resumeSelections']['resume'] == ['ses_a']
        assert len(calls) == 1 and not h.owned['ses_b'].queue_pending
        assert len(h.owned['ses_a'].queue_pending) == (0 if mode == 'before-wake' else 1)
        if mode != 'before-wake':
            assert h.owned['ses_a'].queue_pending[0]['text'] == '继续'
            assert cold.final.query_original(calls[0])[0]['runs'][0]['runId'] == first['runId']
            assert cold.final.query_original(h.owned['ses_a'].queue_pending[0]['id'])[0]['runs'][0]['runId'] == first['runId']
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('after_effect', [False, True])
@pytest.mark.parametrize('empty', [False, True])
def test_host_death_after_committed_selection_recovers_without_query(h, monkeypatch, after_effect, empty):
    job = enabled(h, config(h))
    if not empty:
        illegal(h.owned['ses_a'])
    selected = [] if empty else ['ses_a']
    execute = h.service.executor.execute
    async def die(**kwargs):
        assert internal(h.service, job['id'])['plans'][kwargs['effect_key']['runId']]['resumeSelections']['resume'] == selected
        if after_effect:
            await execute(**kwargs)
        raise SystemExit('injected host death after committed effect admission')
    monkeypatch.setattr(h.service.executor, 'execute', die)
    first = launch(h, job)
    with pytest.raises(SystemExit):
        asyncio.run(h.service.dispatch_once())
    assert run(h, first['runId'])['state'] == 'running'
    illegal(h.owned['ses_b'])
    monkeypatch.setattr(session_resume, 'select', lambda: pytest.fail('Recovered Run must not rescan'))
    monkeypatch.setattr(worker, 'configured_wake_message', lambda: 'configuration changed')
    cold = FinalService(h.root, h.automation.auth)
    try:
        cold.recover()
        asyncio.run(cold.dispatch_once())
        assert cold.final.get(job['id'])['runs'][first['runId']]['state'] == ('completed' if after_effect or empty else 'uncertain')
        assert internal(cold, job['id'])['plans'][first['runId']]['resumeSelections']['resume'] == selected
        assert len(h.owned['ses_a'].queue_pending) == int(after_effect and not empty)
        assert not h.owned['ses_b'].queue_pending
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('bad_selection', [None, 'unreadable', 'bad-container'])
def test_missing_selection_stays_unknown(h, monkeypatch, bad_selection):
    job = enabled(h, config(h))
    illegal(h.owned['ses_a'])
    async def unknown(*args, **kwargs):
        return {}
    monkeypatch.setattr(worker, 'wake_session', unknown)
    first = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    with h.service.transaction() as db:
        stored = h.service._get(db, 'job', job['id'])
        plan = stored['finalRuntime']['plans'][first['runId']]
        if bad_selection is None:
            plan.pop('resumeSelections')
        elif bad_selection == 'bad-container':
            plan['resumeSelections'] = 'unreadable'
        else:
            plan['resumeSelections']['resume'] = bad_selection
        h.service._save(db, 'job', job['id'], stored)
    monkeypatch.setattr(session_resume, 'select', lambda: pytest.fail('Missing proof must not rescan'))
    asyncio.run(h.service.recover_session_receipts())
    assert run(h, first['runId'])['state'] == 'uncertain'
    assert not h.owned['ses_a'].queue_pending


def test_partial_receipt_does_not_retry_or_replace_original_targets(h, monkeypatch):
    job = enabled(h, config(h))
    illegal(h.owned['ses_a'])
    illegal(h.owned['ses_b'])
    original = worker.wake_session
    calls = []
    async def mixed(sid, **kwargs):
        calls.append(sid)
        return await original(sid, **kwargs) if sid == 'ses_a' else {'status': 'rejected', 'error': 'owned rejection'}
    monkeypatch.setattr(worker, 'wake_session', mixed)
    first = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    before = h.service.final.history(job['id'])['effects']
    assert before[0]['receipt']['outcome'] == 'partial'
    asyncio.run(h.service.dispatch_once())
    assert run(h, first['runId'])['state'] == 'uncertain'
    assert h.service.final.history(job['id'])['effects'] == before
    assert calls == ['ses_a', 'ses_b']


def test_uses_original_configured_wake_and_preserves_pause(h, monkeypatch):
    job = enabled(h, config(h))
    illegal(h.owned['ses_a'])
    monkeypatch.setattr(worker, 'configured_wake_message', lambda: 'configured Wake message')
    first = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    assert run(h, first['runId'])['state'] == 'completed'
    assert h.owned['ses_a'].queue_pending[0]['text'] == 'configured Wake message'
    assert h.owned['ses_a'].queue_paused and legal_offline.is_illegal(h.owned['ses_a'])


def test_existing_agent_authority_gate_rechecked_before_scan_or_wake(h, monkeypatch):
    from types import SimpleNamespace
    from packages.automation.authorization import bind_objects
    runtime = SimpleNamespace()
    bind_objects(runtime, h.app)
    job = enabled(h, config(h))
    illegal(h.owned['ses_a'])
    request = {'expectedRevision': job['configRevision'], 'requestKey': 'agent', 'input': {}, 'cause': {'type': 'manual'}}
    denied = asyncio.run(runtime.authorization_objects('POST', '/api/jobs/' + job['id'] + '/runs', request))
    assert 'error' in denied
    grants = h.automation.auth._default_permissions()
    for group in grants.values():
        if isinstance(group, dict):
            for capability in group:
                group[capability] = True
    h.automation.auth.change_permissions(grants, 0)
    accepted = asyncio.run(runtime.authorization_objects('POST', '/api/jobs/' + job['id'] + '/runs', request))
    assert 'error' not in accepted
    h.automation.auth.change_permissions(h.automation.auth._default_permissions(), 1)
    monkeypatch.setattr(session_resume, 'select', lambda: pytest.fail('Revoked authority must not scan'))
    asyncio.run(h.service.dispatch_once())
    assert run(h, accepted['runId'])['state'] == 'failed'
    assert not h.owned['ses_a'].queue_pending


def test_empty_manual_is_completed_no_effect_and_dynamic_schema_edit(h):
    job = enabled(h, config(h))
    first = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    assert run(h, first['runId'])['state'] == 'completed'
    result = h.service.final.history(job['id'])['effects'][0]['receipt']['receipt']
    assert result == {'matchedCount': 0, 'results': []}
    assert all(not s.queue_pending for s in h.owned.values())
    value = config(h)
    value['name'] = 'Edited resume'
    response = h.client.put('/api/jobs/' + job['id'], json={'definition': value, 'expectedRevision': job['configRevision'], 'requestKey': 'edit'})
    assert response.status_code == 200
    current = response.json()['result']['object']
    for field, v in [('text', 'other'), ('sessionId', 'ses_a')]:
        bad = copy.deepcopy(value)
        bad['steps'][0]['arguments'][field] = {'from': 'literal', 'value': v}
        denied = h.client.put('/api/jobs/' + job['id'], json={'definition': bad, 'expectedRevision': current['configRevision'], 'requestKey': 'bad-' + field})
        assert denied.status_code in {400, 422}, denied.text


def test_immutable_publications_and_frozen_plan_preserved(h):
    actions = h.client.get('/api/jobs/actions').json()
    job = enabled(h, config(h))
    first = launch(h, job)
    frozen = copy.deepcopy(internal(h.service, job['id'])['plans'][first['runId']]['frozen'])
    h.service._install_product_actions()
    assert h.client.get('/api/jobs/actions').json() == actions
    assert internal(h.service, job['id'])['plans'][first['runId']]['frozen'] == frozen


@pytest.mark.parametrize('foreign', ['runId', 'stepId', 'operationOrdinal', 'targetId', 'business', 'targets-priority'])
def test_original_query_rejects_foreign_results_linkage(h, foreign):
    job = enabled(h, config(h))
    illegal(h.owned['ses_a'])
    first = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    with h.service.transaction() as db:
        stored = h.service._get(db, 'job', job['id'])
        state = stored['finalRuntime']
        receipt = state['effects'][0]['receipt']
        child = copy.deepcopy(receipt['receipt']['results'][0])
        child['receipt']['queueItemId'] = 'foreign-business-id'
        if foreign in {'runId', 'stepId', 'targetId'}:
            child['effectKey'][foreign] = 'foreign'
        elif foreign == 'operationOrdinal':
            child['effectKey'][foreign] += 1
        elif foreign == 'business':
            plan = state['plans'][first['runId']]
            action = plan['frozen']['actions']['product_resume_legal_running_v1']
            action['execution']['operation'] = 'business-operation'
        else:
            receipt['receipt']['targets'] = []  # explicit original targets wins
        receipt['receipt']['results'].append(child)
        h.service._save(db, 'job', job['id'], stored)
    assert h.service.final.query_original('foreign-business-id') == []
    if foreign not in {'business', 'targets-priority'}:
        assert h.service.final.query_original(session_resume.client_message_id(child['effectKey'])) == []


def test_archived_original_client_and_queue_identity(h):
    job = enabled(h, config(h))
    illegal(h.owned['ses_a'])
    first = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    receipt = h.service.final.history(job['id'])['effects'][0]['receipt']['receipt']['results'][0]
    identities = [session_resume.client_message_id(receipt['effectKey']), receipt['receipt']['queueItemId']]
    h.service.final.settle_notification(job['id'], 'canonical:' + first['runId'], receipt={'outcome': 'succeeded'})
    disabled = control(h, 'jobs', job, 'disable')
    assert disabled.status_code == 200, disabled.text
    h.service.final.delete(job['id'], expected_revision=job['configRevision'])
    cold = FinalService(h.root, h.automation.auth)
    try:
        for identity in identities:
            result = cold.final.query_original(identity)
            assert result[0]['archived'] and result[0]['runs'][0]['runId'] == first['runId']
    finally:
        asyncio.run(cold.close())


def test_scheduled_passive_template_matching_empty_fire_not_backlogged(h):
    from packages.scheduler.providers import time_definition
    from tests.test_job_final_creators import config as time_config
    from datetime import datetime, timedelta, timezone
    fire = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat().replace('+00:00', 'Z')
    creator = time_definition('Owned once', time_config([{'ruleId': 'once', 'type': 'once', 'at': fire}]))
    value = session_resume.time_template(config(h)['steps'][0]['actionRef'], creator)
    response = h.client.post('/api/job-templates', json={'definition': value, 'expectedRevision': 0, 'requestKey': 'template'})
    assert response.status_code == 201, response.text
    item = response.json()['result']['object']
    response = h.client.post('/api/job-templates/' + item['id'] + '/operations', json={'operation': 'publish',
        'expectedRevision': 1, 'expectedStateRevision': 0, 'parameters': {}, 'requestKey': 'publish'})
    assert response.status_code == 200, response.text
    item = response.json()['result']['object']
    response = h.client.post('/api/job-templates/' + item['id'] + '/instantiate', json={'expectedRevision': item['revision'], 'requestKey': 'instantiate', 'activate': True})
    assert response.status_code == 201, response.text
    ids = response.json()['result']['objectIds']
    assert h.service.final.get(ids['job'])['runs'] == {}
    h.service.refresh_subscriptions()
    asyncio.run(h.service.creator_runtime.tick(at=fire))
    asyncio.run(h.service.tick())
    state = h.service.final.get(ids['job'])
    assert len(state['runs']) == 1 and next(iter(state['runs'].values()))['state'] == 'completed'
    receipt = state['effects'][0]['receipt']['receipt']
    assert receipt == {'matchedCount': 0, 'results': []}
    asyncio.run(h.service.tick())
    assert len(h.service.final.get(ids['job'])['runs']) == 1
    assert h.service.creators.get(ids['creators']['clock'])['pendingPublication'] is None
    assert all(not s.queue_pending for s in h.owned.values())
