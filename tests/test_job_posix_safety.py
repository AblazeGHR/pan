"""CP-X2 real public POSIX successors. Windows skips are explicit, never success.

The Linux suite uses production canonical HTTP, executor HTTP, real child tree,
and original supervisor. macOS has a separate public fail-closed successor.
No success/cleanup proof is injected; only the first identity probe can deny.
"""
import asyncio
import copy
import json
import os
import subprocess
import sys
import pytest
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import definition
from packages.jobs.independent_executor import fence
from packages.jobs.window import command_window_options
from tests.job_posix_fixture import owned, canonical, launch, USER, wait_for, remaining, delete


def refresh(c):
    asyncio.run(c.service.recover_external_receipts())
    return c.client.get('/api/job-runs/' + c.run_id).json()

def cancel(c, key, revision=None):
    run = c.client.get('/api/job-runs/' + c.run_id).json()
    body = {'operation': 'cancel', 'expectedStateRevision': run['stateRevision'] if revision is None else revision, 'requestKey': key}
    response = c.client.post('/api/job-runs/' + c.run_id + '/operations', json=body)
    return (response, body)

def cold(c, expected):
    o = c.owned
    code = "import asyncio,json,sys\nfrom packages.jobs.final_service import FinalService\nfrom packages.jobs.final_api import CanonicalUserAuth\nfrom packages.actions.typed_executor import TypedExecutor\nfrom packages.jobs.independent_executor import IndependentExecutor\nfrom packages.jobs.independent_client import IndependentJobClient\ns=FinalService(sys.argv[1],CanonicalUserAuth(sys.argv[2]))\ns.executor=TypedExecutor(independent_executor=IndependentExecutor(job_id=sys.argv[3],client=IndependentJobClient(sys.argv[4])))\ns.recover()\nasyncio.run(s.recover_external_receipts())\nstate=s.final.store.get(sys.argv[3])['finalRuntime']\nrun=state['runs'][sys.argv[5]]\nassert run['state']==sys.argv[6],run\nprint(json.dumps({'state':run['state'],'attempts':list(state['attempts']),'runs':list(state['runs']),\n                 'effects':[{'attemptId':e['attemptId'],'effectKey':e['effectKey'],'outcome':e['outcome']} for e in state['effects']]}))\nasyncio.run(s.close())\n"
    result = subprocess.run([sys.executable, '-c', code, str(c.service.root), str(o.tmp_path / 'automation'), c.job['id'], o.client.endpoint, c.run_id, expected], capture_output=True, text=True, timeout=15, **command_window_options())
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)

@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='CP-X2 real Linux kernel/public HTTP path; macOS ownership explicitly unsupported')
def test_public_unknown_query_explicit_recheck_and_lost_response_identity(canonical):
    c, o = (canonical, canonical.owned)
    o.evidence['label'] = 'public-default-unknown-recheck'
    launch(c, default_pending=True)
    # Destructive fence rejection is tested over the independent public HTTP
    # endpoint, in addition to the ledger's original-receipt unit negatives.
    for field, value in [('ownerEpoch', o.attempt['ownerEpoch'] + 1),
                         ('generation', o.attempt['generation'] + 1),
                         ('leaseId', 'stale-lease'), ('attemptId', 'stale-attempt')]:
        with pytest.raises(RuntimeError, match='409'):
            o.client.cleanup_execution(o.identity, {**o.attempt, field: value})
    assert not (o.root / 'kills.jsonl').exists()
    assert 'cleanupRequest' not in o.client.get_execution(o.identity)
    (o.root / 'deny-probe').touch()
    response, original_body = cancel(c, 'original-public-cancel')
    assert response.status_code == 202, response.text
    op_id = response.json()['operationId']
    wait_for(lambda: o.client.get_execution(o.identity), lambda d: d.get('cleanupRequest', {}).get('status') == 'unknown')
    refresh(c)
    operation = c.client.get('/api/operations/' + op_id).json()
    assert operation['status'] == 'unknown' and operation['result']['cleanupStatus'] == 'unknown'
    query = c.client.get('/api/operations', params={'operation': 'cancel', 'requestKey': 'original-public-cancel'})
    assert query.status_code == 200 and query.json()['operationId'] == op_id
    replay = c.client.post('/api/job-runs/' + c.run_id + '/operations', json=original_body)
    assert replay.status_code == 202 and replay.json()['operationId'] == op_id
    assert cold(c, 'uncertain')['attempts'] == [o.attempt['attemptId']]
    assert not (o.root / 'kills.jsonl').exists() and len(remaining(o.identities)) == len(o.identities)
    (o.root / 'deny-probe').unlink()
    for _ in range(3):
        refresh(c)
        assert c.client.get('/api/operations/' + op_id).json()['status'] == 'unknown'
    assert not (o.root / 'kills.jsonl').exists()
    stale, _ = cancel(c, 'stale-request', revision=original_body['expectedStateRevision'])
    assert stale.status_code == 409, stale.text
    denied = c.client.post('/api/job-runs/' + c.run_id + '/operations', json={**original_body, 'requestKey': 'anonymous'}, headers={'Authorization': 'Bearer invalid'})
    assert denied.status_code == 401, denied.text
    assert o.client.get_execution(o.identity)['cleanupRequest']['revision'] == 1
    rechecked, recheck_body = cancel(c, 'explicit-public-recheck')
    assert rechecked.status_code == 202, rechecked.text
    second_id = rechecked.json()['operationId']
    assert second_id != op_id
    wait_for(lambda: refresh(c), lambda r: r['state'] == 'cancelled')
    o.children[0].wait(5)
    assert not remaining(o.identities)
    final_doc = o.client.get_execution(o.identity)
    proof = final_doc['result']['cleanupProof']
    assert proof['confirmed'] and proof['identityVerified'] and proof['treeTerminated']
    assert proof['ownedTree']['confirmed'] and proof['ownedTree']['remaining'] == []
    assert {record['pid'] for record in o.effect_identities} <= set(proof['ownedTree']['owned'])
    assert fence(final_doc['attempt']) == fence(o.attempt)
    for identity in (op_id, second_id):
        final = c.client.get('/api/operations/' + identity).json()
        assert final['status'] == 'completed' and final['result']['cleanupStatus'] == 'confirmed'
    duplicate = c.client.post('/api/job-runs/' + c.run_id + '/operations', json=recheck_body)
    assert duplicate.status_code == 200 and duplicate.json()['operationId'] == second_id
    assert o.client.get_execution(o.identity)['cleanupRequest']['revision'] == 2
    assert len((o.root / 'kills.jsonl').read_text().splitlines()) == 1
    assert o.effects.read_text().splitlines() == ['effect'] and len(o.children) == 1
    o.evidence.update(publicUnknownPersisted=True, lostResponseQuerySameOperation=True, replayNoCleanup=True, explicitPublicRecheck=True, casRejected=True, anonymousRejected=True, cleanupRevision=2, effects=1, cleanupCalls=1, originalAttemptPreserved=True, runState='cancelled')

@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='CP-X2 real Linux kernel/public HTTP path; macOS ownership explicitly unsupported')
def test_public_cancel_waits_for_original_supervisor_exit(canonical):
    c, o = (canonical, canonical.owned)
    o.evidence['label'] = 'public-supervisor-exit-gate'
    launch(c, default_pending=True)
    gate = o.root / 'hold-supervisor-exit'
    gate.touch()
    try:
        response, _ = cancel(c, 'wait-for-supervisor')
        assert response.status_code == 202
        op = response.json()['operationId']
        wait_for(lambda: o.client.get_execution(o.identity), lambda d: d['result'] is not None)
        for _ in range(3):
            run = refresh(c)
            assert run['state'] != 'cancelled'
            assert c.client.get('/api/operations/' + op).json()['status'] != 'completed'
        assert not remaining(o.effect_identities) and remaining(o.supervisor_identities)
    finally:
        gate.unlink(missing_ok=True)
    o.children[0].wait(5)
    assert refresh(c)['state'] == 'cancelled'
    assert c.client.get('/api/operations/' + op).json()['status'] == 'completed'
    o.evidence.update(supervisorAliveBlockedCompletion=True, supervisorExitConfirmed=True, effects=1, cleanupCalls=1)

@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='CP-X2 real Linux kernel/public HTTP path; macOS ownership explicitly unsupported')
def test_public_queued_cancel_does_not_create_attempt_or_process(canonical):
    c, o = (canonical, canonical.owned)
    o.evidence['label'] = 'public-queued-cancel'
    launch(c, default_pending=True, queued=True)
    response, _ = cancel(c, 'queued-cancel')
    assert response.status_code == 202, response.text
    asyncio.run(c.service.dispatch_once())
    assert c.client.get('/api/job-runs/' + c.run_id).json()['state'] == 'cancelled'
    assert c.client.get('/api/operations/' + response.json()['operationId']).json()['status'] == 'completed'
    state = c.service.final.store.get(c.job['id'])['finalRuntime']
    assert state['attempts'] == {} and state['effects'] == []
    assert o.children == [] and (not o.effects.exists())
    o.evidence.update(queuedCancelled=True, attempts=0, effects=0, supervisors=0)

@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='CP-X2 real Linux kernel/public HTTP path; macOS ownership explicitly unsupported')
def test_original_uncertain_reconcile_rejects_old_epoch_generation_and_lease(canonical):
    c, o = (canonical, canonical.owned)
    o.evidence['label'] = 'public-original-fence'
    launch(c, default_pending=True, action_id='product_process')
    frozen = copy.deepcopy(c.service.final.store.get(c.job['id'])['finalRuntime']['plans'][c.run_id]['frozen'])
    assert frozen['actions']['product_process']['cancel']['supported'] is False
    c.service._install_product_actions()
    assert c.service.final.store.get(c.job['id'])['finalRuntime']['plans'][c.run_id]['frozen'] == frozen
    o.release.touch()
    doc = wait_for(lambda: o.client.get_execution(o.identity), lambda d: d['result'] is not None)
    o.children[0].wait(5)
    before = c.service.final.store.get(c.job['id'])
    for field, value in [('ownerEpoch', o.attempt['ownerEpoch'] + 1), ('generation', o.attempt['generation'] + 1), ('leaseId', 'other-lease'), ('attemptId', 'other-attempt')]:
        expected = {**o.attempt, field: value}
        with pytest.raises((fc.ContractError, KeyError)):
            c.service.final.store.mutate(c.job['id'], lambda job: c.service.final.ledger.reconcile_attempt(job, c.service.root, expected_attempt=expected, effect_key=o.key, receipt=doc['result']))
        assert c.service.final.store.get(c.job['id']) == before
    with pytest.raises(fc.ContractError, match='active Attempt'):
        c.service.final.store.mutate(c.job['id'], lambda job: c.service.final.ledger.finish_attempt(job, c.service.root, o.attempt['attemptId'], 'completed', expected_epoch=o.attempt['ownerEpoch'], expected_generation=o.attempt['generation']))
    assert c.service.final.store.get(c.job['id']) == before
    assert doc['result']['outcome'] == 'unknown'
    assert refresh(c)['state'] == 'uncertain'
    assert c.service.final.store.get(c.job['id'])['finalRuntime']['plans'][c.run_id]['frozen'] == frozen
    assert len(o.children) == 1 and o.effects.read_text().splitlines() == ['effect']
    o.evidence.update(oldFrozenPlanUnchanged=True, newReleaseImmutable=True, staleFencesRejected=4, finishAttemptStillRejectsUncertain=True, originalAttemptReconciled=True, effects=1)

@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Requires real Linux ProcessOwner and public HTTP')
def test_late_unknown_terminal_does_not_rebuild_retired_parent(canonical):
    c, o = canonical, canonical.owned
    o.evidence['label'] = 'posix-late-unknown-terminal'
    launch(c)
    # A normal leader exit is not retained-tree proof on POSIX. The dedicated
    # effect reaps its child cooperatively; the product still must keep unknown.
    o.release.touch()
    doc = wait_for(lambda: o.client.get_execution(o.identity), lambda d: d['result'] is not None)
    assert doc['result']['outcome'] == 'unknown'
    assert doc['result']['cleanupProof']['confirmed'] is False
    o.children[0].wait(5)
    response = delete(c, 'retire-before-late-unknown')
    assert response.status_code == 200, response.text
    retired = c.client.get('/api/jobs/' + c.job['id']).json()
    assert retired['state'] == 'retired'
    c.release_receipt.set()
    c.dispatch.join(10)
    assert not c.dispatch.is_alive() and c.dispatch_errors == []
    for _ in range(2):
        refresh(c)
        asyncio.run(c.service.dispatch_once())
    assert c.client.get('/api/jobs/' + c.job['id']).json() == retired
    state = c.service.final.store.get(c.job['id'])['finalRuntime']
    assert len(state['runs']) == len(state['attempts']) == len(state['effects']) == 1
    assert c.client.get('/api/job-runs/' + c.run_id).json()['state'] == 'uncertain'
    assert o.effects.read_text().splitlines() == ['effect'] and len(o.children) == 1
    assert not (o.root / 'kills.jsonl').exists()
    # Platform refused before signalling. No terminal replay can turn unknown into confirmed cleanup.
    assert not remaining(o.identities)


@pytest.mark.skipif(sys.platform != 'darwin', reason='Requires real macOS public fail-closed admission')
def test_macos_public_execution_refuses_weak_birth_before_effect(canonical):
    c, o = canonical, canonical.owned
    o.evidence['label'] = 'macos-public-weak-identity-refused'
    launch(c, default_pending=True, queued=True)
    asyncio.run(c.service.dispatch_once())
    state = c.service.final.store.get(c.job['id'])['finalRuntime']
    effect, = state['effects']
    from packages.jobs.independent_executor import effect_id
    o.identity = effect_id(effect['effectKey'])
    doc = wait_for(lambda: o.client.get_execution(o.identity), lambda d: d['result'] is not None)
    assert doc['result']['outcome'] == 'unknown'
    assert doc['result']['cleanupProof']['confirmed'] is False
    assert not o.effects.exists() and not o.ready.exists()
    assert not (o.root / 'kills.jsonl').exists()
    o.children[0].wait(5)
    refresh(c)
    assert c.client.get('/api/job-runs/' + c.run_id).json()['state'] == 'uncertain'
    assert len(o.children) == 1

@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Requires real Linux detached executor')
def test_product_default_detached_executor_keeps_original_unknown_receipt(canonical):
    from packages.jobs.independent_executor import ExecutionStore, effect_id
    from packages.core.platform.execution_host import supervisor_exit_evidence
    c, o = canonical, canonical.owned
    o.evidence['label'] = 'posix-product-default-detached'
    launch(c, default_pending=True, queued=True)
    c.service.executor = c.service._product_executor
    o.release.touch()
    asyncio.run(c.service.dispatch_once())
    state = c.service.final.store.get(c.job['id'])['finalRuntime']
    aid, = state['attempts']
    effect, = state['effects']
    host = ExecutionStore(c.service.root / 'executions')
    identity = effect_id(effect['effectKey'])
    doc = wait_for(lambda: host.get(identity), lambda d: d['result'] is not None)
    assert doc['attempt']['attemptId'] == aid
    assert doc['result']['outcome'] == 'unknown'
    assert doc['result']['cleanupProof']['confirmed'] is False
    assert doc['bootstrapReceipt']['exited'] is True
    wait_for(lambda: supervisor_exit_evidence(doc['supervisorIdentity']), lambda p: p['confirmed'])
    for _ in range(2):
        refresh(c)
        asyncio.run(c.service.dispatch_once())
    after = c.service.final.store.get(c.job['id'])['finalRuntime']
    assert list(after['attempts']) == [aid] and len(after['effects']) == 1
    assert c.client.get('/api/job-runs/' + c.run_id).json()['state'] == 'uncertain'
    assert o.effects.read_text().splitlines() == ['effect']
    assert not (c.service.root / 'executions' / 'jobs.sqlite3').exists()
    import psutil
    for pid in {*json.loads(o.ready.read_text()).values(), doc['supervisorIdentity']['pid']}:
        assert not psutil.pid_exists(pid), 'owned detached process remains'

@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Requires real Linux leader-exit subtree/public HTTP')
def test_public_leader_exit_keeps_live_subtree_unknown_and_queryable(canonical):
    c, o = canonical, canonical.owned
    o.evidence['label'] = 'posix-leader-exit-subtree-unknown'
    launch(c, default_pending=True)
    original = copy.deepcopy(o.attempt)
    try:
        o.ready.with_name('leader-exit').touch()
        doc = wait_for(lambda: o.client.get_execution(o.identity), lambda d: d['result'] is not None)
        assert doc['result']['outcome'] == 'unknown'
        assert doc['result']['cleanupProof']['confirmed'] is False
        o.children[0].wait(5)
        child = json.loads(o.ready.read_text())['child']
        assert child in remaining(o.effect_identities)
        response, body = cancel(c, 'dead-leader-cancel')
        assert response.status_code == 202, response.text
        operation_id = response.json()['operationId']
        refresh(c)
        op = c.client.get('/api/operations/' + operation_id).json()
        assert op['status'] == 'unknown' and op['result']['cleanupStatus'] == 'unknown'
        query = c.client.get('/api/operations', params={'operation': 'cancel', 'requestKey': body['requestKey']})
        assert query.status_code == 200 and query.json()['operationId'] == operation_id
        persisted = o.client.get_execution(o.identity)
        assert fence(persisted['attempt']) == fence(original)
        assert persisted['evidence']['processIdentity'] == doc['evidence']['processIdentity']
        assert not (o.root / 'kills.jsonl').exists()
        assert child in remaining(o.effect_identities)
        assert o.effects.read_text().splitlines() == ['effect'] and len(o.children) == 1
    finally:
        # Cooperative fixture-owned orphan exit; the product was not authorized
        # to signal an old pgid after its original leader exited.
        o.release.touch()
        wait_for(lambda: remaining(o.identities), lambda pids: not pids)
