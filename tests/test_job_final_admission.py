"""WP03 FA01-FA03 after owner cutover: canonical admission, no Trigger runner."""
import asyncio
import inspect
import copy

import pytest

from packages.automation import chain_engine, engine, service
from packages.automation.store import Ledger
from packages.jobs.final_service import FinalService, definition
from packages.jobs.final_contracts import ContractError
from tests.test_automation_canonical import (
    canonical, subscription, fact, admit, run, deliver, job_def, enabled, control, USER,
)


def test_fa01_two_triggers_same_job_have_independent_deduplicated_runs(canonical):
    h = canonical
    job, first = subscription(h, enabled(h, job_def(h, steps=2)))
    _, second = subscription(h, job, key='second')
    ev = fact(h)
    asyncio.run(deliver(h, ev))
    asyncio.run(deliver(h, ev))
    before = h.service.final.get(job['id'])
    assert len(before['admissions']) == len(before['runs']) == 2
    assert {r['cause']['triggerId'] for r in before['runs'].values()} == {first['id'], second['id']}
    assert not h.provider.calls
    asyncio.run(h.service.dispatch_once())
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert all(r['state'] == 'completed' for r in state['runs'].values())
    assert len(state['effects']) == 4
    assert {o['runId'] for o in state['outbox'].values()} == set(state['runs'])
    assert len({(o['runId'], o['transitionSeq'], o['recipientId'], o['channel'])
                for o in state['outbox'].values()}) == len(state['outbox'])


def test_fa01_explicit_target_reused_across_events_and_restart(canonical):
    h = canonical
    job, target = subscription(h)
    events = [fact(h, 'one'), fact(h, 'two')]
    receipts = [admit(h, ev, target) for ev in events]
    asyncio.run(h.service.dispatch_once())
    asyncio.run(h.service.dispatch_once())
    restored = FinalService(h.root, h.automation.auth, executor=h.service.executor, owner=h.service.owner)
    assert restored.admit_event(events[0], target)['runId'] == receipts[0]['runId']
    third = restored.admit_event(fact(h, 'three'), target)
    asyncio.run(restored.dispatch_once())
    assert len(restored.list('job', actor=USER)['items']) == 1
    assert len(restored.final.runs(job['id'])) == 3
    assert restored.get_run(third['runId'], actor=USER)['state'] == 'completed'
    assert len(h.provider.calls) == 3
    assert h.automation.ledger.read()['runs'] == {}


def test_fa02_trigger_revision_gates_new_admission_but_not_original_lookup(canonical):
    h = canonical
    job, target = subscription(h)
    ev = fact(h)
    first = admit(h, ev, target)
    response = h.client.put('/api/triggers/' + target['id'], json={
        'definition':{**definition('trigger', target), 'name':'edited'},
        'expectedRevision':1, 'requestKey':'edit-trigger'})
    assert response.status_code == 200, response.text
    changed = response.json()['result']['object']
    assert changed['revision'] == 2
    assert admit(h, ev, changed)['runId'] == first['runId']
    assert admit(h, fact(h, 'stale'), target)['status'] == 'skipped'
    second = admit(h, fact(h, 'next'), changed)
    assert second['runId'] != first['runId'] and len(h.service.final.runs(job['id'])) == 2


@pytest.mark.parametrize('state', ['disable', 'pause', 'retire'])
def test_fa03_trigger_lifecycle_only_gates_future_admission(canonical, state):
    h = canonical
    job, target = subscription(h)
    first = admit(h, fact(h), target)
    response = control(h, 'triggers', target, state)
    assert response.status_code == 200, response.text
    changed = response.json()['result']['object']
    assert admit(h, fact(h, 'future'), changed)['status'] == 'skipped'
    asyncio.run(h.service.dispatch_once())
    assert run(h, first['runId'])['state'] == 'completed'
    assert len(h.service.final.runs(job['id'])) == 1 and len(h.provider.calls) == 1


@pytest.mark.parametrize('committed', [False, True])
def test_fa01_interrupted_intent_queries_original_operation_without_resend(canonical, committed):
    h = canonical
    job, target = subscription(h)
    event = fact(h)
    original = admit(h, event, target) if committed else None
    restored = FinalService(h.root, h.automation.auth, executor=h.service.executor, owner=h.service.owner)
    if committed:
        result = restored.operation(original['operationId'], actor=USER)
        assert result['runId'] == original['runId']
        assert len(restored.final.runs(job['id'])) == 1
    else:
        with pytest.raises(KeyError):
            restored.operation('missing-original-operation', actor=USER)
        assert restored.final.runs(job['id']) == []
    # Querying an unknown outcome is read-only. No fallback request is made.
    assert not h.provider.calls


def test_fa03_retained_legacy_subscription_is_read_only_after_cutover(canonical):
    h = canonical
    svc = h.automation
    ev = fact(h)
    svc.events.append(ev, [{'id':'old-trigger', 'revision':1, 'eventTypes':['workers.all_idle'], 'enabled':True}])
    row = svc.events.lookup(ev['id'])
    asyncio.run(svc.deliver(row))
    receipt = svc.events.subscription_receipts(ev['id'])['trigger:old-trigger']
    assert receipt['status'] == 'unavailable'
    assert not h.service.list('job', actor=USER)['items'] and not h.provider.calls
    assert svc.ledger.read()['runs'] == {}


@pytest.mark.parametrize('outcome,status', [('succeeded', 'acked'), ('unknown', 'uncertain')])
def test_fa03_notification_ack_uses_original_outbox_identity_and_never_resends_unknown(canonical, outcome, status):
    h = canonical
    job, target = subscription(h)
    receipt = admit(h, fact(h), target)
    asyncio.run(h.service.dispatch_once())
    before = copy.deepcopy(h.service.final.get(job['id'])['outbox'])
    calls = []
    async def sender(row):
        calls.append(copy.deepcopy(row))
        return {'outcome':outcome}
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    asyncio.run(h.service.deliver_outbox())
    after = h.service.final.get(job['id'])['outbox']
    assert len(calls) == len(before) > 0
    assert {row['outboxId'] for row in calls} == set(before) == set(after)
    assert all(row['status'] == status and row['runId'] == receipt['runId'] for row in after.values())
    assert len(h.provider.calls) == 1


def test_fa03_retired_trigger_hosts_are_absent():
    assert not hasattr(engine.Engine, 'run_attempt') and not hasattr(engine.Engine, 'drain')
    assert not hasattr(chain_engine, 'run_chain')
    for symbol in ('claim', 'finish', 'prepare_action', 'sync_attempt', 'recover'):
        assert not hasattr(Ledger, symbol)
    source = inspect.getsource(service)
    assert 'execute_dynamic' not in source and '.event_targets(' not in source and '.match_event(' not in source
