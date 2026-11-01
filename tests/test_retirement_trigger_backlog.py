"""Published immutable Event targets survive temporary Trigger lifecycle gates."""
import asyncio
import copy

import pytest

from packages.core import session
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, definition
from tests.job_retirement_native_fixture import h as native_h
from tests.test_retirement_rev16_behavior import h
from tests.test_retirement_rev17_backlog import deferred, update
from tests.test_job_final_http_service import control


@pytest.mark.parametrize('operation,resume', [('pause', 'resume'), ('disable', 'enable')])
def test_published_event_multitick_cold_resume_same_target_id_once(h, operation, resume):
    job, trigger, original = deferred(h)
    response = control(h, 'triggers', trigger, operation, 'hold-trigger')
    assert response.status_code == 200, response.text
    trigger = response.json()['result']['object']
    for _ in range(2): asyncio.run(h.service.tick())
    with h.service.transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM final_event_receipts').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM final_event_operations').fetchone()[0] == 0
    # Lifecycle changes only stateRevision in today's contract. A real config
    # edit additionally proves the persisted Event's old revision is harmless.
    trigger_definition = definition('trigger', trigger)
    trigger_definition['description'] = 'owned revision while inactive'
    revised = h.client.put('/api/triggers/' + trigger['id'], json={'definition': trigger_definition,
        'expectedRevision': trigger['revision'], 'requestKey': 'inactive-config-revision'})
    assert revised.status_code == 200, revised.text
    trigger = revised.json()['result']['object']
    value = definition('job', job)
    value['arguments']['target'] = 'ses_b'
    update(h, job, value, 'restore-target')
    response = control(h, 'triggers', trigger, resume, 'resume-trigger')
    assert response.status_code == 200, response.text
    assert response.json()['result']['object']['revision'] != original['targets'][0]['revision']
    cold = FinalService(h.root, h.automation.auth)
    try:
        cold.recover()
        async def concurrent():
            await asyncio.gather(cold.tick(), h.service.tick())
        asyncio.run(concurrent())
        state = cold.final.get(job['id'])
        assert len(state['runs']) == len(state['attempts']) == len(state['effects']) == 1
        rid = next(iter(state['runs']))
        assert state['runs'][rid]['cause'] == {'type': 'event', 'eventId': original['event']['eventId'], 'triggerId': trigger['id']}
        assert len(session.get('ses_b').queue_pending) == 1
        assert cold.events.get(original['event']['eventId']) == original
        accepted = cold.admit_event(original['event'], original['targets'][0])
        assert accepted['replayed'] is True and accepted['runId'] == rid
        changed_fact = copy.deepcopy(original['event'])
        changed_fact['payload']['forged'] = True
        with pytest.raises(fc.ContractError) as caught:
            cold.admit_event(changed_fact, original['targets'][0])
        assert caught.value.code == 'idempotency_conflict'
        asyncio.run(cold.tick())
        assert len(session.get('ses_b').queue_pending) == 1
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('change', ['selector', 'filter', 'retire'])
def test_true_nonmatch_or_retire_stays_terminal_skip_without_unrelated_target(h, change):
    job, trigger, original = deferred(h)
    if change == 'retire':
        response = control(h, 'triggers', trigger, 'retire', 'retire-trigger')
    else:
        value = definition('trigger', trigger)
        if change == 'selector':
            value['creatorSelector'] = {'mode': 'registeredType', 'providerType': 'other.owned'}
        else:
            value['eventFilter'] = {'type': 'other.owned'}
        response = h.client.put('/api/triggers/' + trigger['id'], json={'definition': value,
            'expectedRevision': trigger['revision'], 'requestKey': 'nonmatch'})
    assert response.status_code == 200, response.text
    asyncio.run(h.service.tick())
    with h.service.transaction(readonly=True) as db:
        rows = db.execute('SELECT * FROM final_event_receipts').fetchall()
        assert len(rows) == 1 and rows[0]['status'] == 'skipped'
        assert db.execute('SELECT count(*) FROM final_event_operations').fetchone()[0] == 0
    assert h.service.final.history(job['id'])['runs'] == [] and not session.get('ses_a').queue_pending
    assert h.service.events.get(original['event']['eventId']) == original
