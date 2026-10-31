"""Owned current Event backlog and terminal-notice parity, without a new ledger.

The dynamic-input experiment is a canonical API mapping investigation, not a
claim that the production clear-target form is wired. Red assertions express
retained business requirements; no shared implementation is changed here.
"""
import asyncio
import copy
import json

import pytest

from packages.core import session, worker
from packages.jobs.final_service import FinalService, definition
from tests.job_retirement_native_fixture import h as native_h
from tests.test_retirement_rev16_behavior import h, schedule, fire, instant
from tests.test_job_final_http_service import enabled, launch, control
from tests.test_session_product_closure import config


def update(h, job, value, key):
    response = h.client.put('/api/jobs/' + job['id'], json={
        'definition': value, 'expectedRevision': job['configRevision'], 'requestKey': key})
    assert response.status_code == 200, response.text
    return response.json()['result']['object']


def deferred(h, *, agent=False):
    at = instant(30)
    job, creator, trigger = schedule(h, [{'ruleId': 'pending', 'type': 'once', 'at': at}])
    value = definition('job', job)
    value['inputSchema'] = {'type': 'object', 'properties': {'target': {'type': ['string', 'null']}},
                            'required': ['target'], 'additionalProperties': False}
    value['arguments'] = {'target': None}
    value['deliveryRuntime'] = {'owner': 'delivery-manager', 'targetMode': 'runtime',
        'inputFields': ['target'], 'allowedStates': ['pending'],
        'concurrency': 'job-config-cas+event-receipt', 'audit': 'final_operations',
        'phase': 'before-admission', 'unknownPolicy': 'query-original'}
    value['steps'][0]['arguments']['sessionId'] = {'from': 'input', 'path': 'target'}
    job = update(h, job, value, 'clear-target')
    if agent:
        stopped = control(h, 'triggers', trigger, 'disable', 'replace-user-trigger')
        assert stopped.status_code == 200, stopped.text
        grants = h.automation.auth._default_permissions()
        for permissions in grants.values():
            if isinstance(permissions, dict):
                for capability in permissions:
                    permissions[capability] = True
        h.automation.auth.change_permissions(grants, 0)
        actor = {'type': 'agent', 'id': 'owned-backlog', 'revision': 1}
        result = h.service.write('trigger', None, {'definition': definition('trigger', trigger),
            'expectedRevision': 0, 'requestKey': 'agent-trigger'}, actor=actor)
        trigger = result['result']['object']
        result = h.service.control('trigger', trigger['id'], {'operation': 'enable',
            'expectedRevision': trigger['revision'], 'expectedStateRevision': trigger['stateRevision'],
            'parameters': {}, 'requestKey': 'agent-enable'}, actor=actor)
        trigger = result['result']['object']
    fire(h, at)
    assert h.service.final.history(job['id'])['runs'] == []
    assert not session.get('ses_a').queue_pending
    events = h.service.events.page()
    assert len(events) == 1
    event = events[0]
    snapshot = copy.deepcopy(h.service.events.get(event['eventId']))
    assert event['payload']['scheduledAt'] == at
    # A published fact survived source advancement, with no effect reservation.
    assert h.service.creators.get(creator['id'])['cursor']['rules']['pending']['nextFireAt'] is None
    with h.service.transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM final_event_receipts WHERE triggerId=?',
                          (trigger['id'],)).fetchone()[0] == 0
    return job, trigger, snapshot


def test_pending_event_cold_retarget_preserves_original_identity_exactly_once(h):
    job, trigger, original = deferred(h)
    runtime = definition('job', job)['deliveryRuntime']
    assert runtime == {'owner': 'delivery-manager', 'targetMode': 'runtime',
        'inputFields': ['target'], 'allowedStates': ['pending'],
        'concurrency': 'job-config-cas+event-receipt', 'audit': 'final_operations',
        'phase': 'before-admission', 'unknownPolicy': 'query-original'}
    cold = FinalService(h.root, h.automation.auth)
    try:
        asyncio.run(cold.tick())
        assert cold.final.history(job['id'])['runs'] == []
        value = definition('job', job)
        value['arguments']['target'] = 'ses_b'
        job = update(h, job, value, 'retarget')
        asyncio.run(cold.tick())
        history = cold.final.history(job['id'])
        assert len(history['runs']) == len(history['effects']) == 1
        assert history['runs'][0]['cause'] == {'type': 'event', 'eventId': original['event']['eventId'],
                                             'triggerId': trigger['id']}
        assert cold.events.get(original['event']['eventId']) == original
        assert len(session.get('ses_b').queue_pending) == 1
        notice = next(row for row in history['outbox'] if row['channel'] == 'ws')
        assert notice['summary']['deliveryRuntime'] == runtime
        assert not session.get('ses_a').queue_pending
        asyncio.run(cold.tick())
        assert cold.final.history(job['id']) == history
        assert len(session.get('ses_b').queue_pending) == 1
    finally:
        asyncio.run(cold.close())


def test_job_disabled_pending_event_remains_then_resume_delivers(h):
    job, trigger, original = deferred(h)
    response = control(h, 'jobs', job, 'disable', 'pause-job')
    assert response.status_code == 200, response.text
    job = response.json()['result']['object']
    value = definition('job', job)
    value['arguments']['target'] = 'ses_b'
    job = update(h, job, value, 'paused-retarget')
    asyncio.run(h.service.tick())
    assert not h.service.final.history(job['id'])['runs']
    assert not session.get('ses_b').queue_pending
    response = control(h, 'jobs', job, 'enable', 'resume-job')
    assert response.status_code == 200, response.text
    asyncio.run(h.service.tick())
    history = h.service.final.history(job['id'])
    assert len(history['runs']) == len(history['effects']) == 1
    assert history['runs'][0]['cause']['eventId'] == original['event']['eventId']
    assert len(session.get('ses_b').queue_pending) == 1


def test_retarget_after_event_admission_never_changes_frozen_effect(h):
    job, trigger, original = deferred(h)
    value = definition('job', job)
    value['arguments']['target'] = 'ses_a'
    job = update(h, job, value, 'first-target')
    admitted = asyncio.run(h.service.admit_event_async(original['event'], original['targets'][0]))
    rid = admitted['runId']
    value = definition('job', job)
    value['arguments']['target'] = 'ses_b'
    update(h, job, value, 'later-target')
    asyncio.run(h.service.tick())
    history = h.service.final.history(job['id'])
    assert len(history['runs']) == len(history['effects']) == 1
    assert history['runs'][0]['runId'] == rid
    assert len(session.get('ses_a').queue_pending) == 1
    assert not session.get('ses_b').queue_pending
    asyncio.run(h.service.tick())
    assert h.service.final.history(job['id']) == history


def test_pending_backlog_authority_revoked_no_admission_or_send(h):
    job, trigger, original = deferred(h, agent=True)
    value = definition('job', job)
    value['arguments']['target'] = 'ses_b'
    update(h, job, value, 'valid-after-revoke')
    h.automation.auth.change_permissions(h.automation.auth._default_permissions(), 1)
    asyncio.run(h.service.tick())
    assert not h.service.final.history(job['id'])['runs']
    assert not session.get('ses_a').queue_pending and not session.get('ses_b').queue_pending
    with h.service.transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM final_event_receipts WHERE triggerId=?',
                          (trigger['id'],)).fetchone()[0] == 0
    assert h.service.events.get(original['event']['eventId']) == original


def notice_job(h):
    value = config(h, 'assign', sessionId='ses_a', text='completed work')
    value['notification'] = {'on': ['completed'], 'recipients': [
        {'recipientId': 'notice', 'channelId': 'session', 'targetRef': 'ses_notice'}]}
    job = enabled(h, value)
    run = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.history(job['id'])['runs'][0]['state'] == 'completed'
    return job, run


def actual_notice(row):
    return worker.enqueue_notice(row['targetRef'], json.dumps(row['summary']), source='automation',
        event_id=row['outboxId'], notice_kind='job_terminal', job_id=row['jobId'])


def test_known_missing_notice_target_retains_pending_then_cold_restoration_delivers(h):
    job, run = notice_job(h)
    original = next(r for r in h.service.final.history(job['id'])['outbox'] if r['channel'] == 'session')
    saved = session.get('ses_notice')
    session.delete('ses_notice')
    calls = []
    async def sender(row):
        if row['channel'] == 'ws': return {'outcome': 'succeeded'}
        calls.append(row['outboxId'])
        from packages.jobs.notification_delivery import enqueue_session_notice
        return await enqueue_session_notice(row)
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    row = next(r for r in h.service.final.history(job['id'])['outbox'] if r['channel'] == 'session')
    assert row['status'] == 'pending', row
    session.save(saved)
    cold = FinalService(h.root, h.automation.auth)
    cold.outbox_sender = sender
    try:
        asyncio.run(cold.deliver_outbox())
        rows = cold.final.history(job['id'])['outbox']
        row = next(r for r in rows if r['channel'] == 'session')
        assert row['outboxId'] == original['outboxId'] and row['status'] == 'acked'
        assert len(session.get('ses_notice').queue_pending) == 1
        asyncio.run(cold.deliver_outbox())
        assert len(calls) == 2 and len(session.get('ses_notice').queue_pending) == 1
    finally:
        asyncio.run(cold.close())


def test_notice_enqueued_response_lost_is_unknown_cold_never_resends(h):
    job, _ = notice_job(h)
    calls = []
    async def lost(row):
        if row['channel'] == 'ws': return {'outcome': 'succeeded'}
        calls.append(row['outboxId'])
        assert (await actual_notice(row))['ok']
        raise ConnectionError('owned acknowledgement lost AFTER durable real enqueue')
    h.service.outbox_sender = lost
    asyncio.run(h.service.deliver_outbox())
    history = h.service.final.history(job['id'])
    assert next(r for r in history['outbox'] if r['channel'] == 'session')['status'] == 'uncertain'
    assert len(session.get('ses_notice').queue_pending) == 1
    cold = FinalService(h.root, h.automation.auth)
    cold.outbox_sender = lost
    try:
        asyncio.run(cold.deliver_outbox())
        assert cold.final.history(job['id']) == history and len(calls) == 1
        assert len(session.get('ses_notice').queue_pending) == 1
    finally:
        asyncio.run(cold.close())


def test_pending_event_pause_does_not_consume_original_backlog(h):
    job, trigger, original = deferred(h)
    disabled = control(h, 'triggers', trigger, 'disable', 'pause')
    assert disabled.status_code == 200, disabled.text
    trigger = disabled.json()['result']['object']
    asyncio.run(h.service.tick())
    assert h.service.final.history(job['id'])['runs'] == []
    value = definition('job', job)
    value['arguments']['target'] = 'ses_b'
    update(h, job, value, 'retarget')
    resumed = control(h, 'triggers', trigger, 'enable', 'resume')
    assert resumed.status_code == 200, resumed.text
    asyncio.run(h.service.tick())
    history = h.service.final.history(job['id'])
    assert len(history['runs']) == 1, 'Paused pre-effect backlog must survive resume'
    assert history['runs'][0]['cause']['eventId'] == original['event']['eventId']
    assert len(session.get('ses_b').queue_pending) == 1
