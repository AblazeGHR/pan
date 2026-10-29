"""Actual scheduled assign/message and manual source-cursor parity."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone

import pytest

from tests.job_retirement_native_fixture import h  # noqa: F401
from tests.test_session_product_closure import config
from tests.test_job_final_http_service import launch
from tests.test_job_final_http_service import enabled
from tests.test_job_final_creators import config as time_config
from packages.scheduler.providers import time_definition


@pytest.fixture(autouse=True)
def owned_paused_targets(h):
    from packages.core import session
    h.owned = {}
    for sid in ('ses_a', 'ses_b'):
        target = session.Session(id=sid, name='owned ' + sid, queue_paused=True)
        session.save(target)
        h.owned[sid] = session.get(sid)


@pytest.mark.parametrize('operation', ['assign', 'session-message'])
def test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor(h, operation):
    fire = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace('+00:00', 'Z')
    source = time_definition('owned schedule', time_config([{'ruleId': 'once', 'type': 'once', 'at': fire}]))
    job = config(h, operation, sessionId='ses_a', text='original scheduled text', source='automation')
    value = {'name': 'owned field template', 'jobDefinition': job, 'composition': {
        'creators': [{'mode': 'create', 'key': 'clock', 'definition': source}],
        'triggers': [{'key': 'tick', 'targetJobKey': 'job', 'creatorKeys': ['clock'], 'definition': {
            'name': 'owned tick', 'targetJobId': 'job', 'creatorSelector': {'mode': 'ids', 'ids': ['clock']},
            'eventTypes': ['scheduler.time.v2']}}]}}
    result = h.client.post('/api/job-templates', json={'definition': value, 'expectedRevision': 0, 'requestKey': 'template'})
    assert result.status_code == 201, result.text
    template = result.json()['result']['object']
    assert not h.owned['ses_a'].queue_pending
    result = h.client.post('/api/job-templates/' + template['id'] + '/operations', json={
        'operation': 'publish', 'expectedRevision': 1, 'expectedStateRevision': 0,
        'parameters': {}, 'requestKey': 'publish'})
    assert result.status_code == 200, result.text
    template = result.json()['result']['object']
    result = h.client.post('/api/job-templates/' + template['id'] + '/instantiate', json={
        'expectedRevision': template['revision'], 'requestKey': 'instantiate', 'activate': True})
    assert result.status_code == 201, result.text
    ids = result.json()['result']['objectIds']
    assert not h.owned['ses_a'].queue_pending
    cursor = copy.deepcopy(h.service.creators.get(ids['creators']['clock'])['cursor'])
    item = h.client.get('/api/jobs/' + ids['job']).json()
    manual = launch(h, item, 'owned-manual')
    asyncio.run(h.service.dispatch_once())
    assert h.service.creators.get(ids['creators']['clock'])['cursor'] == cursor
    assert h.client.get('/api/job-runs/' + manual['runId']).json()['cause'] == {'type': 'manual'}
    h.service.refresh_subscriptions()
    asyncio.run(h.service.creator_runtime.tick(at=fire))
    asyncio.run(h.service.tick())
    state = h.service.final.get(ids['job'])
    assert len(state['runs']) == len(state['attempts']) == len(state['effects']) == 2
    assert all(row['state'] == 'completed' for row in state['runs'].values())
    event_run, = [r for r in state['runs'].values() if r['cause']['type'] == 'event']
    assert event_run['cause']['triggerId'] == ids['triggers']['tick']
    original_event = h.service.events.get(event_run['cause']['eventId'])['event']
    assert original_event['creatorId'] == ids['creators']['clock']
    assert original_event['payload']['ruleId'] == 'once'
    assert original_event['payload']['scheduledAt'] == fire
    queue = h.owned['ses_a'].queue_pending
    assert len(queue) == 2 and len({r['id'] for r in queue}) == 2
    assert all(r['text'] == 'original scheduled text' and r['source'] == 'automation' for r in queue)
    if operation == 'assign':
        assert all(r['taskIdSource'] == 'assign' for r in queue)
        assert len({r['taskId'] for r in queue}) == 2
    asyncio.run(h.service.tick())
    assert h.service.final.get(ids['job']) == state
    assert len(h.owned['ses_a'].queue_pending) == 2


def test_one_actual_time_event_independently_admits_two_original_worker_effects(h):
    from tests.test_job_final_http_service import create, control
    jobs = []
    for sid in ('ses_a', 'ses_b'):
        item = create(h, value=config(h, 'session-message', sessionId=sid, text=sid, source='automation'),
                      key='create-' + sid)
        response = control(h, 'jobs', item, 'enable', 'enable-' + sid)
        assert response.status_code == 200, response.text
        jobs.append(response.json()['result']['object'])
    fire = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace('+00:00', 'Z')
    source = time_definition('broadcast fact', time_config([{'ruleId': 'once', 'type': 'once', 'at': fire}]))
    # Real canonical API for creator and both independent Trigger admissions.
    response = h.client.post('/api/event-creators', json={
        'definition': source, 'expectedRevision': 0, 'requestKey': 'source'})
    assert response.status_code == 201, response.text
    creator = response.json()['result']['object']
    creator = control(h, 'event-creators', creator, 'enable', 'enable-source').json()['result']['object']
    for i, job in enumerate(jobs):
        trigger = {'name': 'independent ' + str(i), 'targetJobId': job['id'],
            'creatorSelector': {'mode': 'ids', 'ids': [creator['id']]}, 'eventTypes': ['scheduler.time.v2']}
        result = h.client.post('/api/triggers', json={
            'definition': trigger, 'expectedRevision': 0, 'requestKey': 'trigger-' + str(i)})
        assert result.status_code == 201, result.text
        assert control(h, 'triggers', result.json()['result']['object'], 'enable', 'enable-trigger-' + str(i)).status_code == 200
    h.service.refresh_subscriptions()
    asyncio.run(h.service.creator_runtime.tick(at=fire))
    asyncio.run(h.service.tick())
    states = [h.service.final.get(job['id']) for job in jobs]
    causes = [next(iter(s['runs'].values()))['cause'] for s in states]
    assert causes[0]['eventId'] == causes[1]['eventId']
    assert causes[0]['triggerId'] != causes[1]['triggerId']
    assert all(len(s['runs']) == len(s['attempts']) == len(s['effects']) == 1 for s in states)
    assert all(next(iter(s['runs'].values()))['state'] == 'completed' for s in states)
    for sid in ('ses_a', 'ses_b'):
        assert len(h.owned[sid].queue_pending) == 1 and h.owned[sid].queue_pending[0]['text'] == sid
    asyncio.run(h.service.tick())
    assert [h.service.final.get(job['id']) for job in jobs] == states
