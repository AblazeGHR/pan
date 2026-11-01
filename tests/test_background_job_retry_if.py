"""Job retry policy contracts; all registry/process state is test owned."""
import asyncio
import subprocess
import sys
from unittest.mock import Mock

import pytest

from packages.core import background_retry as retry
from tests.test_job_automatic_recovery import env


@pytest.mark.parametrize("reason", sorted(retry.REASONS))
@pytest.mark.parametrize("match", sorted(retry.REASONS))
def test_reason_match_matrix(env, monkeypatch, reason, match):
    from tests.test_job_automatic_recovery import seed, job_definition, policy, observe, marker
    from tests.test_job_retry_reason_matrix import original_reason
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=[match], maxAttempts=3)))
    original_reason(row, reason)
    observe(env)
    should_retry = match == reason and reason in {'failed', 'timeout', 'orphaned', 'interrupted'}
    assert bool(marker(env, row)) is should_retry
    assert len(env.service.final.get(row.jid)['attempts']) == 1
    asyncio.run(env.service.dispatch_once())
    state = env.service.final.get(row.jid)
    assert len(state['attempts']) == 1 + int(should_retry)
    assert len(row.original.calls) == int(should_retry)
    if should_retry:
        call = row.original.calls[0]
        assert call['effect_key'] == row.key
        assert call['attempt']['attemptId'] != row.attempt['attemptId']
        assert call['attempt']['generation'] == row.attempt['generation'] + 1
    else:
        assert not row.original.calls


def test_default_side_effects_never_retry(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, job_definition, observe
    row = seed(env, monkeypatch, value=job_definition(env, recovery={'automatic': False}))
    observe(env)
    before = env.service.final.get(row.jid)
    assert before['runs'][row.rid]['state'] == 'failed'
    for _ in range(2):
        asyncio.run(env.service.dispatch_once())
        observe(env)
    assert env.service.final.get(row.jid) == before
    assert len(before['attempts']) == 1 and not row.original.calls


def test_zero_budget_and_spawn_failure_consume_no_extra_attempt(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, job_definition, policy, observe, marker
    row = seed(env, monkeypatch, value=job_definition(env, policy(maxAttempts=1)))
    observe(env)
    asyncio.run(env.service.dispatch_once())
    assert len(env.service.final.get(row.jid)['attempts']) == 1 and not row.original.calls
    row = seed(env, monkeypatch, value=job_definition(env, policy(maxAttempts=2)))
    observe(env)
    calls = []
    async def fail(**kwargs):
        calls.append(kwargs)
        raise OSError('owned spawn failure')
    monkeypatch.setattr(row.original, 'execute', fail)
    asyncio.run(env.service.dispatch_once())
    state = env.service.final.get(row.jid)
    assert len(state['attempts']) == 2 and len(calls) == 1
    assert state['runs'][row.rid]['state'] == 'uncertain'
    assert marker(env, row) is None
    for _ in range(2): asyncio.run(env.service.dispatch_once())
    assert len(calls) == 1 and len(env.service.final.get(row.jid)['attempts']) == 2


def test_retry_does_not_swallow_registry_failure(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, observe
    row = seed(env, monkeypatch)
    observe(env)
    before = env.service.final.store.get(row.jid)
    original = env.service._save
    def fail(db, kind, identity, value):
        if kind == 'job' and len(value.get('finalRuntime', {}).get('attempts', {})) == 2:
            raise OSError('owned registry unavailable')
        return original(db, kind, identity, value)
    monkeypatch.setattr(env.service, '_save', fail)
    with pytest.raises(OSError, match='owned registry unavailable'):
        asyncio.run(env.service.dispatch_once())
    assert env.service.final.store.get(row.jid) == before
    assert not row.original.calls


@pytest.mark.parametrize("options", [
    {"retry_if": ["orphaned"]}, {"retry_if": ["unknown"]}, {"retry_if": "failed"},
    {"max_retries": True}, {"max_retries": -1}, {"max_retries": 1.5},
    {"backoff_sec": True}, {"backoff_sec": float("inf")}, {"backoff_sec": float("nan")},
    {"backoff_sec": -1}, {"idempotency_key": ""}, {"deduplication": " "},
])
def test_invalid_policy_has_no_effect(env, options):
    from tests.test_job_automatic_recovery import job_definition, policy
    recovery = policy()
    value = job_definition(env, recovery)
    if 'retry_if' in options:
        recovery['retryIf'] = options['retry_if']
    elif 'max_retries' in options:
        # Raw malformed types/ranges remain invalid; no truthy coercion.
        recovery['maxAttempts'] = options['max_retries']
    elif 'backoff_sec' in options:
        delay = options['backoff_sec']
        recovery['backoffSeconds'] = [delay if type(delay) is bool else str(delay)]
    else:
        # Caller metadata no longer asserts an Action's retry guarantee. The
        # canonical Action release owns that declaration; stale fields reject.
        key = 'idempotencyKey' if 'idempotency_key' in options else 'deduplication'
        value[key] = next(iter(options.values()))
    value['recovery'] = recovery
    response = env.client.post('/api/jobs', json={'definition': value,
        'expectedRevision': 0, 'requestKey': 'policy-input'})
    if options == {'retry_if': ['orphaned']}:
        # Current immutable process release already declares retry safety, so
        # this approved reason is valid configuration without admitting work.
        assert response.status_code == 201, response.text
        state = env.service.final.get(response.json()['result']['object']['id'])
        assert not state['runs'] and not state['attempts'] and not state['effects']
    else:
        from packages.jobs.final_contracts import ERROR_HTTP_STATUS
        assert response.status_code == ERROR_HTTP_STATUS['invalid_schema'], response.text
        assert response.json()['error']['code'] == 'invalid_schema'
        assert env.client.get('/api/jobs').json()['items'] == []


def test_restart_reconcile_persists_count_backoff_and_limit(env, monkeypatch):
    from decimal import Decimal
    from packages.jobs.final_service import FinalService
    from tests.test_job_automatic_recovery import seed, job_definition, policy, marker
    from tests.test_job_retry_reason_matrix import original_reason
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=['orphaned'],
        maxAttempts=3, backoffSeconds=['10'])))
    original_reason(row, 'orphaned')
    cold = FinalService(env.root, env.auth, owner=env.service.owner)
    monkeypatch.setattr(cold, '_external_adapter', lambda _: row.original)
    clock = [Decimal('1000')]
    monkeypatch.setattr(cold, '_recovery_time', lambda timestamp=None: clock[0])
    asyncio.run(cold.recover_external_receipts())
    pending = cold.final.store.get(row.jid)['finalRuntime']['plans'][row.rid]['autoRecovery']
    assert Decimal(pending['eligibleAt']) == Decimal('1010')
    clock[0] = Decimal('1009')
    asyncio.run(cold.dispatch_once())
    assert not row.original.calls and len(cold.final.get(row.jid)['attempts']) == 1
    clock[0] = Decimal('1010')
    asyncio.run(cold.dispatch_once())
    assert len(row.original.calls) == 1 and len(cold.final.get(row.jid)['attempts']) == 2
    # The new executor outcome is unknown: remaining budget is not replay permission.
    assert cold.final.get(row.jid)['runs'][row.rid]['state'] == 'uncertain'
    asyncio.run(cold.dispatch_once())
    assert len(row.original.calls) == 1


def test_cancel_during_backoff_prevents_retry(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, job_definition, policy, observe, USER
    row = seed(env, monkeypatch, value=job_definition(env, policy(backoffSeconds=['60'])))
    observe(env)
    run = env.service.final.get(row.jid)['runs'][row.rid]
    result = asyncio.run(env.service.control_run_postcommit(row.rid, {'operation': 'cancel',
        'expectedStateRevision': run['stateRevision'], 'requestKey': 'cancel-backoff'}, actor=USER))
    observe(env)
    for _ in range(2): asyncio.run(env.service.dispatch_once())
    state = env.service.final.get(row.jid)
    assert state['runs'][row.rid]['state'] == 'cancelled'
    assert len(state['attempts']) == 1 and not row.original.calls
    assert env.service.operation(result['operationId'], actor=USER)['status'] == 'completed'


def test_accepted_cancel_beats_orphan_recovery(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, job_definition, policy, observe, USER
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=list(retry.REASONS))))
    observe(env)
    run = env.service.final.get(row.jid)['runs'][row.rid]
    asyncio.run(env.service.control_run_postcommit(row.rid, {'operation': 'cancel',
        'expectedStateRevision': run['stateRevision'], 'requestKey': 'cancel-original'}, actor=USER))
    observe(env)  # Settle only the original confirmed cleanup receipt.
    asyncio.run(env.service.dispatch_once())
    before = env.service.final.store.get(row.jid)
    assert before['finalRuntime']['runs'][row.rid]['state'] == 'cancelled'
    row.doc.update(result=None, phase='running', supervisorExitProof={'confirmed': False})
    observe(env)
    asyncio.run(env.service.dispatch_once())
    assert env.service.final.store.get(row.jid) == before and not row.original.calls


def test_concurrent_recovery_and_late_runner_are_fenced(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, observe
    row = seed(env, monkeypatch)
    async def concurrent():
        await asyncio.gather(*(env.service.dispatch_once() for _ in range(4)))
    asyncio.run(concurrent())
    state = env.service.final.get(row.jid)
    assert len(row.original.calls) == 1 and len(state['attempts']) == 2
    current = row.original.calls[0]
    assert current['effect_key'] == row.key
    assert current['attempt']['attemptId'] != row.attempt['attemptId']
    # The retained old Attempt's receipt cannot settle/reexecute the new one.
    observe(env)
    assert any(d['code'] == 'external_receipt_unavailable' and d.get('errorCode') == 'revision_conflict'
               and d.get('runId') == row.rid for d in env.service.diagnostics)
    assert env.service.final.get(row.jid) == state and len(row.original.calls) == 1


def test_orphan_does_not_duplicate_live_task(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, job_definition, policy, observe
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=['orphaned'])))
    row.doc.update(result=None, phase='running', supervisorExitProof={'confirmed': False})
    before = env.service.final.store.get(row.jid)
    for _ in range(2):
        observe(env)
        asyncio.run(env.service.dispatch_once())
    assert env.service.final.store.get(row.jid) == before and not row.original.calls


def test_http_policy_and_public_status(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed, job_definition, policy, observe, marker
    from tests.test_job_retry_reason_matrix import original_reason
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=['failed'],
        maxAttempts=2, backoffSeconds=['30'])))
    original_reason(row, 'timeout')
    observe(env)
    job = env.client.get('/api/jobs/' + row.jid)
    run = env.client.get('/api/job-runs/' + row.rid)
    assert job.status_code == run.status_code == 200
    assert job.json()['recovery']['retryIf'] == ['failed']
    assert job.json()['recovery']['maxAttempts'] == 2
    assert run.json()['runId'] == row.rid and run.json()['state'] == 'failed'
    assert marker(env, row) is None and len(env.service.final.get(row.jid)['attempts']) == 1
    assert not row.original.calls


@pytest.mark.parametrize("error, reason", [
    (TimeoutError("runner timeout"), "timeout"),
    (subprocess.TimeoutExpired("owned command", 1), "timeout"),
    (KeyboardInterrupt(), "interrupted"), (OSError("spawn failed"), "failed"),
])
def test_runner_does_not_collapse_terminal_reasons(tmp_path, error, reason):
    from packages.actions.typed_executor import TypedExecutor
    from tests.test_job_final_executors import attempt, key
    class OriginalOwner:
        calls = 0
        def spawn(self, *args, **kwargs):
            self.calls += 1
            raise error
    owner = OriginalOwner()
    executor = TypedExecutor(process_owner=owner)
    plan = {'operation': 'process', 'effect': 'write_local',
        'arguments': {'argv': [sys.executable, '-c', 'pass'], 'cwd': str(tmp_path)}}
    receipt = asyncio.run(executor.execute(attempt=attempt(), effect_key=key(), frozen_plan=plan))
    # An exception is preserved as evidence, not invented process-exit proof.
    assert receipt['outcome'] == 'unknown' and receipt['effectKey'] == key()
    assert receipt['receipt']['code'] == 'process_exception'
    assert receipt['receipt']['message'] == (str(error) or type(error).__name__)
    assert receipt['cleanupProof'].get('confirmed') is not True
    again = asyncio.run(executor.execute(attempt=attempt(), effect_key=key(), frozen_plan=plan, retry=True))
    assert again == receipt and owner.calls == 1


def test_unavailable_independent_policy_fails_before_remote_start(env, monkeypatch):
    from packages.jobs import pan_bridge
    from tests.test_job_automatic_recovery import job_definition, policy
    remote = Mock()
    monkeypatch.setattr(pan_bridge, 'configured_client', lambda: object())
    monkeypatch.setattr(pan_bridge, 'request', remote)
    response = env.client.post('/api/jobs', json={'definition': job_definition(env, policy()),
        'expectedRevision': 0, 'requestKey': 'native-recovery'})
    assert response.status_code == 201, response.text
    state = env.service.final.get(response.json()['result']['object']['id'])
    assert not state['runs'] and not state['attempts'] and not state['effects']
    remote.assert_not_called()  # No old independent Job create/retry protocol.


def test_recovery_notifies_final_outcome_once(env):
    from tests.test_job_automatic_recovery import create, job_definition, policy, drive_until, state
    count = env.tmp / 'notice-count.txt'
    code = (f"from pathlib import Path; p=Path({str(count)!r}); n=int(p.read_text()) if p.exists() else 0; "
            "p.write_text(str(n+1)); raise SystemExit(7 if n == 0 else 0)")
    value = job_definition(env, policy(backoffSeconds=['0.3']), code)
    value['notification'] = {'on': ['completed'], 'recipients': [
        {'recipientId': 'final-only', 'channelId': 'session', 'targetRef': 'ses_retry'}]}
    jid, rid = create(env, value)
    notices = []
    async def sender(row):
        notices.append(row)
        return {'outcome': 'succeeded', 'receipt': {'originalOutboxId': row['outboxId']}}
    env.service.outbox_sender = sender
    async def run():
        await drive_until(env, jid, rid, 'retry_wait')
        await env.service.deliver_outbox()
        assert not notices and not state(env, jid)['outbox']
        await drive_until(env, jid, rid, 'completed')
        await env.service.deliver_outbox()
        await env.service.deliver_outbox()
    asyncio.run(run())
    final = state(env, jid)
    assert count.read_text(encoding='utf-8') == '2' and len(final['attempts']) == 2
    assert len(notices) == len(final['outbox']) == 2
    assert {row['channel'] for row in notices} == {'session', 'ws'}
    assert len({row['outboxId'] for row in notices}) == 2
    for row in notices:
        assert row['runId'] == rid and row['outboxId'] in final['outbox']
        assert final['outbox'][row['outboxId']]['status'] == 'acked'
    assert len(final['runs']) == 1


@pytest.mark.parametrize("recovery_reason", ["orphaned", "failed"])
def test_separate_recovery_process_after_runner_loss(env, monkeypatch, recovery_reason):
    """Cold lookup keeps unknown identity; only proven failed effects may retry."""
    import json
    from tests.test_job_automatic_recovery import create, seed, job_definition, policy, drive_until, observe
    count = env.tmp / 'cold-count.txt'
    if recovery_reason == 'failed':
        code = (f"from pathlib import Path; p=Path({str(count)!r}); n=int(p.read_text()) if p.exists() else 0; "
                "p.write_text(str(n+1)); raise SystemExit(7 if n == 0 else 0)")
        jid, rid = create(env, job_definition(env, policy(backoffSeconds=['0.3']), code))
        asyncio.run(drive_until(env, jid, rid, 'retry_wait'))
        before = env.service.final.get(jid)
        assert count.read_text(encoding='utf-8') == '1'
    else:
        # The old runner-dead fixture supplied no owned-tree exit proof.
        # Preserve that uncertainty; a fresh process is not retry authorization.
        row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=['orphaned'])))
        from tests.test_job_retry_reason_matrix import original_reason
        original_reason(row, 'orphaned')
        row.doc.update(result=None, phase='running')
        observe(env)
        jid, rid = row.jid, row.rid
        before = env.service.final.get(jid)
        assert before['runs'][rid]['state'] == 'uncertain' and not row.original.calls
    asyncio.run(env.service.close())
    script = """
import asyncio, json, sys, time
from pathlib import Path
from packages.jobs.final_service import FinalService
from packages.jobs.final_api import CanonicalUserAuth
root, authroot, jid, rid, reason = sys.argv[1:]
s = FinalService(Path(root), CanonicalUserAuth(Path(authroot)))
async def run():
    s.recover()
    if reason == 'failed':
        deadline = time.monotonic() + 30
        while s.final.get(jid)['runs'][rid]['state'] != 'completed':
            assert time.monotonic() < deadline, s.final.get(jid)
            await s.dispatch_once()
            await asyncio.sleep(.04)
    else:
        await s.recover_external_receipts()
        await s.dispatch_once()
        await s.recover_external_receipts()
    print(json.dumps(s.final.get(jid)))
    await s.close()
asyncio.run(run())
"""
    result = subprocess.run([sys.executable, '-c', script, str(env.root),
        str(env.tmp / 'automation'), jid, rid, recovery_reason],
        capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stderr
    final = json.loads(result.stdout.strip().splitlines()[-1])
    assert len(final['runs']) == 1 and final['runs'][rid]['runId'] == rid
    if recovery_reason == 'failed':
        assert final['runs'][rid]['state'] == 'completed'
        assert count.read_text(encoding='utf-8') == '2' and len(final['attempts']) == 2
        assert set(before['attempts']) < set(final['attempts'])
        assert final['steps'][rid]['s0']['attemptCount'] == 2
        assert len({json.dumps(x['effectKey'], sort_keys=True) for x in final['effects']}) == 1
    else:
        assert final == before and not count.exists()
        assert len(final['attempts']) == len(final['effects']) == 1
        assert final['effects'][0]['outcome'] == 'unknown'
