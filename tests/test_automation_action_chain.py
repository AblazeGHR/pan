"""Trigger admission freezes a canonical chain; Automation never owns steps."""
import asyncio
import copy

import pytest

from packages.jobs.final_service import FinalService, definition
from tests.test_automation_canonical import (
    canonical, subscription, job_def, enabled, fact, admit, run, operation, USER,
)


def chain(h):
    return subscription(h, enabled(h, job_def(h, steps=2)))


def test_chain_and_arguments_freeze_at_canonical_admission(canonical):
    h = canonical
    job, target = chain(h)
    receipt = admit(h, fact(h), target)
    frozen = h.service.final.store.get(job['id'])['finalRuntime']['plans'][receipt['runId']]['frozen']
    changed = job_def(h, steps=2)
    changed['steps'][1]['arguments']['text']['value'] = 'edited'
    response = h.client.put('/api/jobs/' + job['id'], json={'definition':changed,
        'expectedRevision':1, 'requestKey':'edit-job'})
    assert response.status_code == 200, response.text
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    rid = receipt['runId']
    assert [text for text, _ in h.provider.calls] == ['0', '1']
    assert run(h, rid)['state'] == 'completed'
    assert [s['attemptCount'] for s in state['steps'][rid].values()] == [1, 1]
    assert frozen['steps'][1]['arguments']['text']['value'] == '1'
    assert h.automation.ledger.read()['runs'] == {}


@pytest.mark.parametrize('outcome,expected', [('reject', 'failed'), ('unknown', 'uncertain')])
def test_chain_failure_or_unknown_stops_before_next_effect(canonical, outcome, expected):
    h = canonical
    job, target = chain(h)
    h.provider.fail_once = '0' if outcome == 'reject' else None
    h.provider.unknown = outcome == 'unknown'
    receipt = admit(h, fact(h), target)
    asyncio.run(h.service.dispatch_once())
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert run(h, receipt['runId'])['state'] == expected
    assert state['steps'][receipt['runId']]['s1']['attemptCount'] == 0
    assert len(h.provider.calls) == 1
    assert admit(h, fact(h), target)['runId'] == receipt['runId']
    if outcome == 'unknown':
        response = operation(h, receipt['runId'], 'retry', stepId='s0')
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'reconciliation_required'


def test_boundary_restart_queries_same_run_and_preserves_completed_step(canonical):
    h = canonical
    job, target = chain(h)
    receipt = admit(h, fact(h), target)
    def pause_after_first():
        assert operation(h, receipt['runId'], 'pause').status_code == 202
        h.provider.after_send = None
    h.provider.after_send = pause_after_first
    asyncio.run(h.service.dispatch_once())
    assert run(h, receipt['runId'])['state'] == 'paused_at_boundary'
    restored = FinalService(h.root, h.automation.auth, executor=h.service.executor, owner=h.service.owner)
    restored.recover()
    assert restored.get_run(receipt['runId'], actor=USER)['state'] == 'paused_at_boundary'
    assert operation(h, receipt['runId'], 'resume').status_code == 202
    asyncio.run(restored.dispatch_once())
    state = restored.final.get(job['id'])
    assert state['runs'][receipt['runId']]['state'] == 'completed'
    assert [text for text, _ in h.provider.calls] == ['0', '1']
    assert state['steps'][receipt['runId']]['s0']['attemptCount'] == 1


def test_action_revoked_between_steps_prevents_second_effect(canonical):
    h = canonical
    job, target = chain(h)
    receipt = admit(h, fact(h), target)
    def revoke():
        with h.service.transaction() as db:
            action = h.service._get(db, 'action', h.action['id'])
            action['enabled'] = False
            h.service._save(db, 'action', action['id'], action)
        h.provider.after_send = None
    h.provider.after_send = revoke
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert run(h, receipt['runId'])['state'] == 'failed'
    assert len(h.provider.calls) == 1
    assert state['steps'][receipt['runId']]['s0']['state'] == 'completed'
    assert state['steps'][receipt['runId']]['s1']['attemptCount'] == 0


def test_post_effect_checkpoint_failure_never_reexecutes_on_recovery(canonical, monkeypatch):
    h = canonical
    job, target = chain(h)
    receipt = admit(h, fact(h), target)
    original = h.service.final.ledger.record_effect
    def fail(*args, **kwargs):
        raise OSError('post-effect write fault')
    monkeypatch.setattr(h.service.final.ledger, 'record_effect', fail)
    with pytest.raises(OSError, match='post-effect'):
        asyncio.run(h.service.dispatch_once())
    monkeypatch.setattr(h.service.final.ledger, 'record_effect', original)
    h.service.recover()
    asyncio.run(h.service.dispatch_once())
    assert run(h, receipt['runId'])['state'] == 'uncertain'
    assert [text for text, _ in h.provider.calls] == ['0']
    assert h.service.final.get(job['id'])['steps'][receipt['runId']]['s1']['attemptCount'] == 0
