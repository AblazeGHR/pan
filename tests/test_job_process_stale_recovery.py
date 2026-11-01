"""A stale terminal lookup is visible and cannot block independent recovery."""
import asyncio

from tests.test_job_automatic_recovery import env, seed, job_definition


def test_stale_unknown_attempt_is_unchanged_while_other_original_receipt_settles(env, monkeypatch):
    stale = seed(env, monkeypatch)
    asyncio.run(env.service.dispatch_once())
    before = env.service.final.store.get(stale.jid)
    assert len(before['finalRuntime']['attempts']) == 2
    assert before['finalRuntime']['runs'][stale.rid]['state'] == 'uncertain'
    other = seed(env, monkeypatch, value=job_definition(env, recovery={'automatic': False}))
    adapters = {stale.jid: stale.original, other.jid: other.original}
    monkeypatch.setattr(env.service, '_external_adapter', lambda jid: adapters[jid])
    asyncio.run(env.service.recover_external_receipts())
    assert env.service.final.store.get(stale.jid) == before
    assert len(stale.original.calls) == 1 and not other.original.calls
    state = env.service.final.get(other.jid)
    assert state['runs'][other.rid]['state'] == 'failed'
    assert len(state['effects']) == len(state['attempts']) == 1
    assert state['effects'][0]['effectKey'] == other.key
    assert any(d['code'] == 'external_receipt_unavailable' and d.get('errorCode') == 'revision_conflict'
               and d.get('runId') == stale.rid for d in env.service.diagnostics)
