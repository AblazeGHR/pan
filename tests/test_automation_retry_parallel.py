"""Bounded scheduler and attempt transitions, with owned deterministic barriers."""
import asyncio
import copy
from datetime import datetime, timezone

import pytest

from packages.actions.store import validate_action
from packages.automation.store import Ledger, Conflict
from tests.test_automation_core import async_test, action, trigger, event, setup, until
from tests.test_automation_canonical import canonical, subscription, fact, admit, run, operation, job_def, enabled
from packages.jobs.final_service import FinalService, definition


def test_parallel_trigger_contract(tmp_path):
    ledger=Ledger(tmp_path)
    t=ledger.put(trigger(executionMode='parallel'),0,True)
    assert t['maxConcurrency']==3


def test_action_explicit_idempotence():
    assert validate_action({**action(),'idempotent':True})['idempotent'] is True
    assert validate_action(action())['idempotent'] is False


@pytest.mark.parametrize('fields',[
    {'executionMode':'unknown'}, {'executionMode':True}, {'executionMode':[]},
    {'executionMode':'parallel','maxConcurrency':0}, {'executionMode':'parallel','maxConcurrency':33},
    {'executionMode':'parallel','maxConcurrency':True}, {'executionMode':'parallel','maxConcurrency':1.5},
    {'maxConcurrency':2}, {'retry':None}, {'retry':True}, {'retry':{'enabled':1}},
    {'retry':{'maxAttempts':0}}, {'retry':{'maxAttempts':11}}, {'retry':{'maxAttempts':True}},
    {'retry':{'jitter':True}}, {'queuePolicy':'ignore'}])
def test_unknown_types_and_bounds_rejected_without_write(tmp_path,fields):
    ledger=Ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.put(trigger(**fields),0,True)
    assert not ledger.path.exists()


@pytest.mark.parametrize('value',[None,1,'true',{},[]])
def test_action_idempotent_strict_boolean(value):
    with pytest.raises(ValueError):
        validate_action({**action(),'idempotent':value})


def test_defaults_and_concurrency_change_need_user_even_disabled(tmp_path):
    ledger=Ledger(tmp_path)
    draft={**trigger(),'enabled':False}
    t=ledger.put(draft,0)
    assert t['executionMode']=='serial' and t['maxConcurrency']==1
    assert t['retry']=={'enabled':False,'maxAttempts':3}
    with pytest.raises(PermissionError,match='concurrency'):
        ledger.put({**draft,'executionMode':'parallel'},1)
    t=ledger.put({**draft,'executionMode':'parallel','maxConcurrency':32},1,True)
    assert t['maxConcurrency']==32
    with pytest.raises(PermissionError):
        ledger.put({**trigger(),'executionMode':'parallel'},2)


def test_retry_defaults_cap_and_schema_matches_parser():
    import jsonschema
    from packages.automation.policy import backoff,SCHEDULING_SCHEMA
    assert [backoff(i) for i in range(1,11)]==[1,2,4,8,16,32,60,60,60,60]
    for value in [{'executionMode':'parallel'},{'executionMode':'parallel','maxConcurrency':32},{'retry':{'enabled':True,'maxAttempts':3}}]:
        jsonschema.validate(value,SCHEDULING_SCHEMA)
    for value in [{'executionMode':'serial','maxConcurrency':3},{'retry':{'enabled':'true'}},{'retry':{'maxAttempts':11}},{'mode':'parallel'}]:
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(value,SCHEDULING_SCHEMA)


def test_older_client_updates_preserve_explicit_scheduler_policy(tmp_path):
    ledger=Ledger(tmp_path)
    ledger.put(trigger(executionMode='parallel',maxConcurrency=4,retry={'enabled':True,'maxAttempts':5}),0,True)
    t=ledger.put({**trigger(),'name':'old-client-name-edit'},1,True)
    assert t['executionMode']=='parallel' and t['maxConcurrency']==4
    assert t['retry']=={'enabled':True,'maxAttempts':5}


def test_canonical_retry_keeps_run_and_effect_key_only_failed_step_repeats(canonical):
    h = canonical
    job, target = subscription(h, enabled(h, job_def(h, steps=2)))
    h.provider.fail_once = '1'
    receipt = admit(h, fact(h), target)
    rid = receipt['runId']
    asyncio.run(h.service.dispatch_once())
    assert run(h, rid)['state'] == 'failed'
    asyncio.run(h.service.dispatch_once())
    assert [text for text, _ in h.provider.calls] == ['0', '1']
    assert admit(h, fact(h), target)['runId'] == rid
    response = operation(h, rid, 'retry', stepId='s1')
    assert response.status_code == 202, response.text
    retried = response.json()
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'completed'
    assert state['steps'][rid]['s0']['attemptCount'] == 1
    assert state['steps'][rid]['s1']['attemptCount'] == 2
    assert [text for text, _ in h.provider.calls] == ['0', '1', '1']
    assert h.provider.calls[1][1] == h.provider.calls[2][1]
    assert len({a['attemptId'] for a in state['attempts'].values()}) == 3
    assert h.client.get('/api/operations/' + retried['operationId']).json()['status'] == 'completed'
    assert operation(h, rid, 'retry', 'retry-completed').status_code == 409


@pytest.mark.parametrize('provider_outcome', ['unknown', 'exception', 'partial'])
def test_unknown_or_cleanup_unconfirmed_never_auto_retries_or_confirms(canonical, provider_outcome):
    h = canonical
    job, target = subscription(h)
    calls = []
    async def execute(**kwargs):
        calls.append(kwargs['effect_key'])
        if provider_outcome == 'exception':
            from packages.jobs.process_wait import CleanupUnconfirmed
            raise CleanupUnconfirmed('descendant ownership unknown')
        return {'outcome':provider_outcome, 'receipt':{}}
    h.service.executor = type('OwnedExecutor', (), {'execute':staticmethod(execute)})()
    receipt = admit(h, fact(h), target)
    asyncio.run(h.service.dispatch_once())
    h.service.recover()
    asyncio.run(h.service.dispatch_once())
    assert run(h, receipt['runId'])['state'] == 'uncertain'
    assert operation(h, receipt['runId'], 'retry').status_code == 409
    assert admit(h, fact(h), target)['runId'] == receipt['runId']
    assert len(calls) == 1
    assert len(h.service.final.get(job['id'])['attempts']) == 1


def test_canonical_recovery_does_not_retry_failed_or_unknown_original_run(canonical):
    h = canonical
    job, target = subscription(h)
    h.provider.fail_once = '0'
    receipt = admit(h, fact(h), target)
    asyncio.run(h.service.dispatch_once())
    restored = FinalService(h.root, h.automation.auth, executor=h.service.executor, owner=h.service.owner)
    restored.recover()
    asyncio.run(restored.dispatch_once())
    assert restored.get_run(receipt['runId'], actor={'type':'user'})['state'] == 'failed'
    assert len(h.provider.calls) == 1
    assert len(restored.final.runs(job['id'])) == 1


@pytest.mark.parametrize('idempotent,same_key,proof,allowed', [
    (False, False, False, False), (True, False, False, True),
    (False, True, False, True), (True, True, True, False),
])
def test_retry_requires_frozen_idempotence_and_cleanup_contract(canonical, idempotent, same_key, proof, allowed):
    h = canonical
    with h.service.transaction() as db:
        action = h.service._get(db, 'action', h.action['id'])
    value = definition('action', action)
    value['retry'] = {'idempotent':idempotent, 'sameKeySupported':same_key, 'requiresProof':proof}
    updated = h.service.publish_action('retry_contract', value)
    from packages.jobs.final_service import object_ref
    h.action = object_ref('action', updated)
    job, target = subscription(h)
    h.provider.fail_once = '0'
    receipt = admit(h, fact(h), target)
    asyncio.run(h.service.dispatch_once())
    response = operation(h, receipt['runId'], 'retry', stepId='s0')
    assert response.status_code == (202 if allowed else 409), response.text
    if not allowed:
        assert response.json()['error']['code'] == 'not_retryable'
        # Publishing a more permissive Action cannot promote a frozen Run.
        value['retry'] = {'idempotent':True, 'sameKeySupported':True, 'requiresProof':False}
        from packages.jobs.final_contracts import ContractError
        with pytest.raises(ContractError, match='immutable'):
            h.service.publish_action(updated['id'], value)
        assert operation(h, receipt['runId'], 'retry', 'promote').status_code == 409
        assert len(h.provider.calls) == 1


def test_retry_operation_has_exact_revision_auth_and_replays_one_attempt(canonical):
    h = canonical
    job, target = subscription(h)
    h.provider.fail_once = '0'
    receipt = admit(h, fact(h), target)
    asyncio.run(h.service.dispatch_once())
    url = '/api/job-runs/' + receipt['runId'] + '/operations'
    body = {'operation':'retry', 'expectedStateRevision':run(h, receipt['runId'])['stateRevision'],
        'requestKey':'retry', 'stepId':'s0'}
    assert h.client.post(url, json={**body, 'expectedStateRevision':-1}).status_code in {400, 409}
    assert h.client.post(url, json=body, headers={'Authorization':'Bearer invalid'}).status_code == 401
    first = h.client.post(url, json=body)
    assert first.status_code == 202
    second = h.client.post(url, json=body)
    assert second.status_code in {200, 202}
    assert second.json()['operationId'] == first.json()['operationId']
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(job['id'])['steps'][receipt['runId']]['s0']['attemptCount'] == 2


def test_pause_resume_and_cancel_are_canonical_run_controls(canonical):
    h = canonical
    _, target = subscription(h)
    receipt = admit(h, fact(h), target)
    rid = receipt['runId']
    assert operation(h, rid, 'pause').status_code == 202
    asyncio.run(h.service.dispatch_once())
    assert run(h, rid)['state'] == 'paused_at_boundary' and not h.provider.calls
    assert operation(h, rid, 'resume').status_code == 202
    assert operation(h, rid, 'cancel').status_code == 202
    asyncio.run(h.service.dispatch_once())
    assert run(h, rid)['state'] == 'cancelled' and not h.provider.calls
