"""The Event inbox must not settle a temporary canonical admission gate."""
import asyncio
import copy

import pytest

from packages.jobs.final_service import definition, FinalService
from packages.automation.service import Service
from tests.test_automation_canonical import canonical, subscription, fact, control


@pytest.mark.parametrize('hold,resume', [('pause', 'resume'), ('disable', 'enable')])
def test_inbox_deferred_keeps_original_event_until_same_trigger_resumes(canonical, hold, resume):
    h = canonical
    job, target = subscription(h)
    svc = h.automation
    svc.started = True
    event = fact(h)
    asyncio.run(svc.accept(event, svc.core))
    original = copy.deepcopy(svc.events.lookup(event['id']))
    response = control(h, 'triggers', target, hold)
    assert response.status_code == 200, response.text
    held = response.json()['result']['object']
    for _ in range(2):
        with pytest.raises(ValueError, match='trigger_temporarily_inactive'):
            asyncio.run(svc.deliver(original))
        receipt = svc.events.subscription_receipts(event['id'])['trigger:' + target['id']]
        assert receipt['status'] == 'error' and not receipt.get('runId')
        assert not h.service.final.runs(job['id']) and not h.provider.calls
        with h.service.transaction(readonly=True) as db:
            assert db.execute('SELECT count(*) FROM final_event_operations').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM final_event_receipts').fetchone()[0] == 0
    # Re-read the same published target ID after a configuration revision too.
    value = definition('trigger', held)
    value['description'] = 'same target after temporary gate'
    response = h.client.put('/api/triggers/' + target['id'], json={
        'definition': value, 'expectedRevision': held['revision'], 'requestKey': 'revise-held'})
    assert response.status_code == 200, response.text
    held = response.json()['result']['object']
    response = control(h, 'triggers', held, resume)
    assert response.status_code == 200, response.text
    cold = Service(h.root.parent / 'automation', svc.worker, None)
    cold.final_jobs = FinalService(h.root, h.automation.auth, executor=h.service.executor, owner=h.service.owner)
    cold.events.owner_fence = cold.final_jobs.owner
    asyncio.run(cold.deliver(cold.events.lookup(event['id'])))
    receipt = cold.events.subscription_receipts(event['id'])['trigger:' + target['id']]
    assert receipt['status'] == 'admitted' and receipt['runId']
    asyncio.run(cold.deliver(original))
    asyncio.run(h.service.dispatch_once())
    runs = h.service.final.runs(job['id'])
    assert len(runs) == len(h.provider.calls) == 1
    assert runs[0]['runId'] == receipt['runId']
    assert runs[0]['cause'] == {'type': 'event', 'eventId': event['id'], 'triggerId': target['id']}
    after = svc.events.lookup(event['id'])
    assert after['event'] == original['event'] and after['targets'] == original['targets']


@pytest.mark.parametrize('change', ['retire', 'event_type', 'selector', 'filter', 'missing'])
def test_current_target_policy_disposes_only_original_subscription(canonical, change):
    h = canonical
    job, first = subscription(h)
    _, other = subscription(h, job, key='independent')
    svc = h.automation
    svc.started = True
    event = fact(h)
    asyncio.run(svc.accept(event, svc.core))
    original = copy.deepcopy(svc.events.lookup(event['id']))
    held = control(h, 'triggers', first, 'pause').json()['result']['object']
    with pytest.raises(ValueError, match='trigger_temporarily_inactive'):
        asyncio.run(svc.deliver(original))
    accepted = svc.events.subscription_receipts(event['id'])['trigger:' + other['id']]
    assert accepted['status'] == 'admitted'
    if change == 'retire':
        response = control(h, 'triggers', held, 'retire')
        assert response.status_code == 200, response.text
    elif change == 'missing':
        # An absent original target is historical data, never a new target.
        with h.service.transaction() as db:
            db.execute("DELETE FROM final_documents WHERE kind='trigger' AND id=?", (first['id'],))
    else:
        value = definition('trigger', held)
        if change == 'event_type': value['eventTypes'] = ['runtime.issue.v1']
        elif change == 'selector': value['creatorSelector'] = {'mode': 'registeredType', 'providerType': 'different'}
        else: value['eventFilter'] = {'type': 'different'}
        response = h.client.put('/api/triggers/' + first['id'], json={
            'definition': value, 'expectedRevision': held['revision'], 'requestKey': 'change-policy'})
        assert response.status_code == 200, response.text
        response = control(h, 'triggers', response.json()['result']['object'], 'resume')
        assert response.status_code == 200, response.text
    cold = Service(h.root.parent / 'automation', svc.worker, None)
    cold.final_jobs = FinalService(h.root, h.automation.auth, executor=h.service.executor, owner=h.service.owner)
    cold.events.owner_fence = cold.final_jobs.owner
    for _ in range(2): asyncio.run(cold.deliver(original))
    receipts = cold.events.subscription_receipts(event['id'])
    assert receipts['trigger:' + other['id']] == accepted
    assert receipts['trigger:' + first['id']]['admissionResult'] == 'skipped'
    asyncio.run(h.service.dispatch_once())
    runs = h.service.final.runs(job['id'])
    assert len(runs) == len(h.provider.calls) == 1
    assert runs[0]['runId'] == accepted['runId']
    after = cold.events.lookup(event['id'])
    assert after['event'] == original['event'] and after['targets'] == original['targets']
