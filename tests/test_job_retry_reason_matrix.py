"""Six configured reasons; only proven original outcomes can consume a retry.

orphaned/interrupted positives here are trusted-port contracts, not native
emitter or production-profile evidence.
"""
import copy
import asyncio

import pytest

from tests.test_job_automatic_recovery import env, seed, policy, job_definition, observe, marker

REASONS = ('failed', 'timeout', 'orphaned', 'interrupted', 'cancelled', 'dependency-unavailable')


def original_reason(row, reason):
    row.doc['result']['receipt'] = ({'exitCode': 7} if reason == 'failed' else {'code': reason, 'exitCode': 7})
    identity = {'schema': 'pan.process-identity/1', 'platform': 'owned-fixture',
                'pid': 123, 'birth': 'original', 'kind': 'fixture-birth'}
    row.doc['supervisorIdentity'] = identity
    row.doc['supervisorExitProof'] = {'confirmed': True, 'identity': copy.deepcopy(identity)}


@pytest.mark.parametrize('reason', REASONS)
@pytest.mark.parametrize('match', REASONS)
def test_six_reason_configuration_and_original_outcome_matrix(env, monkeypatch, reason, match):
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=[match])))
    original_reason(row, reason)
    observe(env)
    expected = reason == match and reason in {'failed', 'timeout', 'orphaned', 'interrupted'}
    assert bool(marker(env, row)) is expected
    state = env.service.final.get(row.jid)
    assert len(state['attempts']) == 1 and not row.original.calls
    assert state['runs'][row.rid]['state'] == ('retry_wait' if expected else 'failed')


@pytest.mark.parametrize('reason', ['orphaned', 'interrupted'])
@pytest.mark.parametrize('missing', ['exitCode', 'zeroExit', 'cleanup', 'supervisorExit', 'birth'])
def test_abnormal_reason_without_complete_original_proof_never_retries(env, monkeypatch, reason, missing):
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=[reason])))
    original_reason(row, reason)
    if missing == 'exitCode': row.doc['result']['receipt'].pop('exitCode')
    elif missing == 'zeroExit': row.doc['result']['receipt']['exitCode'] = 0
    elif missing == 'cleanup': row.doc['result']['cleanupProof']['confirmed'] = False
    elif missing == 'supervisorExit': row.doc['supervisorExitProof']['confirmed'] = False
    else: row.doc['supervisorExitProof']['identity']['birth'] = 'reused'
    observe(env)
    assert marker(env, row) is None and not row.original.calls
    state = env.service.final.get(row.jid)
    assert len(state['attempts']) == 1
    assert state['runs'][row.rid]['state'] == 'uncertain'
    assert state['effects'][0]['outcome'] == 'unknown'
    before = env.service.final.store.get(row.jid)
    original = copy.deepcopy(row.doc)
    for _ in range(2): observe(env)
    assert env.service.final.store.get(row.jid) == before
    assert row.doc == original and not row.original.calls


@pytest.mark.parametrize('reason', ['orphaned', 'interrupted'])
def test_uncertain_original_can_reconcile_only_after_complete_matching_receipt(env, monkeypatch, reason):
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=[reason])))
    original_reason(row, reason)
    row.doc['result']['receipt'].pop('exitCode')
    observe(env)
    assert env.service.final.get(row.jid)['runs'][row.rid]['state'] == 'uncertain'
    original_reason(row, reason)
    original = copy.deepcopy(row.doc)
    observe(env)
    state = env.service.final.get(row.jid)
    assert state['runs'][row.rid]['state'] == 'failed'
    assert len(state['attempts']) == len(state['effects']) == 1
    assert state['effects'][0]['effectKey'] == row.key and state['effects'][0]['outcome'] == 'rejected'
    assert marker(env, row) is None and not row.original.calls
    for _ in range(2): observe(env)
    assert env.service.final.get(row.jid) == state and row.doc == original


def test_mixed_policy_does_not_block_proven_failed(env, monkeypatch):
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=['failed', 'orphaned'])))
    observe(env)
    assert marker(env, row)['reason'] == 'failed' and not row.original.calls


def test_unavailable_original_lookup_waits_without_spending_attempt_budget(env, monkeypatch):
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=['failed', 'dependency-unavailable'])))
    lookup = row.original.lookup_execution
    async def unavailable(**kwargs):
        raise OSError('owned provider temporarily unavailable')
    monkeypatch.setattr(row.original, 'lookup_execution', unavailable)
    before = env.service.final.store.get(row.jid)
    for _ in range(2): observe(env)
    assert env.service.final.store.get(row.jid) == before and not row.original.calls
    assert any(d['code'] == 'external_receipt_unavailable' for d in env.service.diagnostics)
    monkeypatch.setattr(row.original, 'lookup_execution', lookup)
    observe(env)
    assert marker(env, row)['reason'] == 'failed'
    assert len(env.service.final.get(row.jid)['attempts']) == 1
    asyncio.run(env.service.dispatch_once())
    assert len(env.service.final.get(row.jid)['attempts']) == 2 and len(row.original.calls) == 1
