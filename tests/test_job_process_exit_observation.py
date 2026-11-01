"""Synthetic negative probes for original-supervisor exit observations."""
import asyncio
import copy

import pytest

from packages.jobs import final_contracts as fc
from tests.test_job_automatic_recovery import env, seed, job_definition


def pending(h, monkeypatch):
    row = seed(h, monkeypatch, value=job_definition(h, recovery={'automatic': False}))
    row.doc.update(result=None, phase='running', supervisorIdentity={
        'schema': 'pan.process-identity/1', 'platform': 'owned-fixture', 'pid': 123,
        'kind': 'fixture-birth', 'birth': 'original'})
    return row


@pytest.mark.parametrize('proof', [None, {}, {'confirmed': False}])
def test_missing_or_unconfirmed_exit_keeps_original_running(env, monkeypatch, proof):
    row = pending(env, monkeypatch)
    if proof is None: row.doc.pop('supervisorExitProof', None)
    else: row.doc['supervisorExitProof'] = proof
    before = env.service.final.store.get(row.jid)
    for _ in range(2): asyncio.run(env.service.recover_external_receipts())
    assert env.service.final.store.get(row.jid) == before
    assert not row.original.calls


@pytest.mark.parametrize('change', ['missing_identity', 'birth', 'pid'])
def test_exit_proof_must_name_original_supervisor(env, monkeypatch, change):
    row = pending(env, monkeypatch)
    identity = copy.deepcopy(row.doc['supervisorIdentity'])
    if change == 'birth': identity['birth'] = 'different'
    elif change == 'pid': identity['pid'] += 1
    row.doc['supervisorExitProof'] = {'confirmed': True}
    if change != 'missing_identity': row.doc['supervisorExitProof']['identity'] = identity
    before = env.service.final.store.get(row.jid)
    with pytest.raises(fc.ContractError, match='supervisor exit proof'):
        asyncio.run(env.service.recover_external_receipts())
    assert env.service.final.store.get(row.jid) == before and not row.original.calls


@pytest.mark.parametrize('field,value', [('pid', 0), ('pid', True), ('birth', None),
                                        ('birth', ''), ('birth', 1), ('schema', 'unknown')])
def test_matching_but_invalid_identity_cannot_prove_exit(env, monkeypatch, field, value):
    row = pending(env, monkeypatch)
    row.doc['supervisorIdentity'][field] = value
    row.doc['supervisorExitProof'] = {'confirmed': True, 'identity': copy.deepcopy(row.doc['supervisorIdentity'])}
    before = env.service.final.store.get(row.jid)
    with pytest.raises(fc.ContractError, match='supervisor exit proof'):
        asyncio.run(env.service.recover_external_receipts())
    assert env.service.final.store.get(row.jid) == before and not row.original.calls


def test_confirmed_exit_without_receipt_is_unknown_once_without_native_result_write(env, monkeypatch):
    row = pending(env, monkeypatch)
    row.doc['supervisorExitProof'] = {'confirmed': True,
        'identity': copy.deepcopy(row.doc['supervisorIdentity']), 'reason': 'owned-fixture-exit'}
    original = copy.deepcopy(row.doc)
    asyncio.run(env.service.recover_external_receipts())
    state = env.service.final.get(row.jid)
    assert state['runs'][row.rid]['state'] == state['attempts'][row.attempt['attemptId']]['state'] == 'uncertain'
    assert len(state['attempts']) == len(state['effects']) == len(state['outbox']) == 1
    effect = state['effects'][0]
    assert effect['effectKey'] == row.key and effect['outcome'] == 'unknown'
    assert effect['receipt']['cleanupProof'] == {'confirmed': False}
    for _ in range(2):
        asyncio.run(env.service.recover_external_receipts())
        asyncio.run(env.service.dispatch_once())
    assert env.service.final.get(row.jid) == state
    assert row.doc == original and not row.original.calls
