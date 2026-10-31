"""Original native Session providers with runtime choice at canonical reservation."""
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from packages.core import session
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, definition, object_ref
from packages.actions.typed_executor import WorkerAssignProvider, WorkerSessionProvider
from concurrent.futures import ThreadPoolExecutor
import httpx
from tests.job_retirement_native_fixture import h as native_h
from tests.test_retirement_rev16_behavior import h
from tests.test_job_final_http_service import enabled, control
from tests.test_session_product_closure import config


@pytest.mark.parametrize('op', ['assign', 'session-message', 'session-broadcast'])
def test_actual_local_shutdown_gate_preserves_unsent_same_attempt_until_queue_returns(h, op):
    from packages.core import worker
    job, rid, frozen = admitted(h, op)
    before = worker._shutdown_started
    workers = set(worker.workers)
    try:
        worker._shutdown_started = True
        asyncio.run(h.service.dispatch_once())
        pending = h.service.final.get(job['id'])
        attempt = next(iter(pending['attempts'].values()))
        assert attempt['state'] == 'running' and len(pending['attempts']) == 1
        assert not pending['effects']
        assert pending['runs'][rid]['state'] == 'paused_at_boundary'
        assert not session.get('ses_a').queue_pending and not session.get('ses_b').queue_pending
        plan = h.service.final.store.get(job['id'])['finalRuntime']['plans'][rid]
        children = plan['runtimeDelivery']['send']['children']
        assert all(c['status'] == 'known-no-attempt' and c['attempted'] is False for c in children)
        assert all('shutting down' in str(c['receipt']) for c in children)
        worker._shutdown_started = False
        asyncio.run(h.service.dispatch_once())
        done = h.service.final.get(job['id'])
        assert done['runs'][rid]['state'] == 'completed'
        assert list(done['attempts']) == list(pending['attempts'])
        assert len(session.get('ses_a').queue_pending) == 1
        if op == 'session-broadcast':
            assert len(session.get('ses_b').queue_pending) == 1
        assert h.service.final.store.get(job['id'])['finalRuntime']['plans'][rid]['frozen'] == frozen
        asyncio.run(h.service.dispatch_once())
        assert h.service.final.get(job['id'])['effects'] == done['effects']
        assert set(worker.workers) == workers
    finally:
        worker._shutdown_started = before


def admitted(h, op='assign', *, actor=None):
    targets = ['ses_a', 'ses_b'] if op == 'session-broadcast' else 'ses_a'
    target_key = 'sessionIds' if op == 'session-broadcast' else 'sessionId'
    cfg = config(h, op, **{target_key: targets, 'text': 'original', 'source': 'automation'})
    runtime_action = next(a for a in h.service.actions(actor={'type': 'user', 'id': 'local'})['items']
                          if a['id'] == 'product_' + op.replace('-', '_') + '_runtime_v1')
    cfg['steps'][0]['actionRef'] = object_ref('action', runtime_action)
    cfg['inputSchema'] = {'type': 'object', 'properties': {'delivery': {'type': 'object',
        'properties': {'target': {'type': ['array', 'null'], 'items': {'type': 'string'}} if isinstance(targets, list)
                       else {'type': ['string', 'null']}, 'text': {'type': 'string'}},
        'required': ['target', 'text'], 'additionalProperties': False}}, 'required': ['delivery'], 'additionalProperties': False}
    cfg['arguments'] = {'delivery': {'target': targets, 'text': 'original'}}
    cfg['steps'][0]['arguments'][target_key] = {'from': 'input', 'path': 'delivery.target'}
    cfg['steps'][0]['arguments']['text'] = {'from': 'input', 'path': 'delivery.text'}
    cfg['deliveryRuntime'] = {'owner': 'delivery-manager', 'targetMode': 'runtime',
        'inputFields': ['delivery.target', 'delivery.text'], 'allowedStates': ['pending', 'reserved'],
        'concurrency': 'job-config-cas+event-receipt', 'audit': 'final_operations',
        'phase': 'before-reservation', 'unknownPolicy': 'query-original'}
    job = enabled(h, cfg)
    body = {'input': cfg['arguments'], 'cause': {'type': 'manual'},
        'expectedRevision': job['configRevision'], 'requestKey': 'run'}
    if actor:
        rid = h.service.create_run(job['id'], body, actor=actor)['runId']
    else:
        response = h.client.post('/api/jobs/' + job['id'] + '/runs', json=body)
        assert response.status_code in {201, 202}, response.text
        rid = response.json()['runId']
    return job, rid, copy.deepcopy(h.service.final.store.get(job['id'])['finalRuntime']['plans'][rid]['frozen'])


def edit(h, job, target, text, key):
    cfg = definition('job', job)
    cfg['arguments']['delivery'] = {'target': target, 'text': text}
    response = h.client.put('/api/jobs/' + job['id'], json={'definition': cfg,
        'expectedRevision': job['configRevision'], 'requestKey': key})
    assert response.status_code == 200, response.text
    audit = response.json()['result']['runtimeAudit']
    assert audit['before']['inputs']['delivery.target'] == job['arguments']['delivery']['target']
    assert audit['before']['inputs']['delivery.text'] == job['arguments']['delivery']['text']
    assert audit['after']['inputs'] == {'delivery.target': target, 'delivery.text': text}
    return response.json()['result']['object']


@pytest.mark.parametrize('op', ['assign', 'session-message', 'session-broadcast'])
def test_queued_missing_target_cold_current_target_text_same_run_attempt_effect(h, op):
    job, rid, frozen = admitted(h, op)
    job = edit(h, job, None, '  待发送\n raw  ', 'clear')
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'queued' and not state['attempts'] and not state['effects']
    assert not session.get('ses_a').queue_pending and not session.get('ses_b').queue_pending
    cold = FinalService(h.root, h.automation.auth)
    try:
        cold.recover()  # queued no-attempt backlog survives actual recovery, not only reads
        target = ['ses_b', 'ses_notice'] if op == 'session-broadcast' else 'ses_b'
        job = edit(h, job, target, '  生效正文\n raw  ', 'restore')
        asyncio.run(cold.dispatch_once())
        state = cold.final.get(job['id'])
        assert state['runs'][rid]['state'] == 'completed'
        assert len(state['runs']) == len(state['attempts']) == len(state['effects']) == 1
        receipt = state['effects'][0]['receipt']
        runtime = receipt['deliveryRuntime']
        assert runtime['runtimeRevision'] == job['configRevision']
        assert runtime['textDigest'] == fc.digest('  生效正文\n raw  ')
        assert runtime['effectKey'] == state['effects'][0]['effectKey']
        assert runtime['attemptId'] == state['effects'][0]['attemptId']
        assert all(item['outcome'] == 'succeeded' for item in runtime['acceptance'])
        assert session.get('ses_b').queue_pending[0]['text'] == '  生效正文\n raw  '
        if op == 'session-broadcast':
            assert session.get('ses_notice').queue_pending[0]['text'] == '  生效正文\n raw  '
        assert cold.final.store.get(job['id'])['finalRuntime']['plans'][rid]['frozen'] == frozen
        changed = edit(h, job, ['ses_a'] if op == 'session-broadcast' else 'ses_a', 'after accepted', 'later')
        asyncio.run(cold.dispatch_once())
        assert cold.final.get(job['id'])['effects'] == state['effects']
        assert not session.get('ses_a').queue_pending and changed['configRevision'] > job['configRevision']
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('gate', ['pause', 'revoke', 'cancel'])
def test_current_gate_after_first_real_acceptance_prevents_next_child_and_no_new_attempt(h, gate):
    grants = h.automation.auth._default_permissions()
    grants['job']['execute'] = True
    grants['action']['manageSystemActions'] = True
    h.automation.auth.change_permissions(grants, 0)
    job, rid, _ = admitted(h, 'session-broadcast', actor={'type': 'agent', 'id': 'owned-author', 'revision': 1})
    class GateAfterFirst(WorkerSessionProvider):
        calls = []
        controls = []
        async def send_session(self, sid, text, **kwargs):
            result = await super().send_session(sid, text, **kwargs)
            self.calls.append(sid)
            if gate == 'pause':
                response = control(h, 'jobs', job, 'pause', 'pause-after-acceptance')
                self.controls.append(response)
                assert response.status_code == 200, response.text
            elif gate == 'revoke':
                h.automation.auth.change_permissions(h.automation.auth._default_permissions(), 1)
            else:
                run = h.client.get('/api/job-runs/' + rid).json()
                response = h.client.post('/api/job-runs/' + rid + '/operations', json={
                    'operation': 'cancel',
                    'expectedStateRevision': run['stateRevision'],
                    'requestKey': 'cancel-after-acceptance'})
                self.controls.append(response)
                assert response.status_code in {200, 202}, response.text
            return result
    provider = GateAfterFirst()
    h.service.executor.session_provider = provider
    asyncio.run(h.service.dispatch_once())
    assert all(r.status_code in {200, 202} for r in provider.controls), [r.text for r in provider.controls]
    assert provider.calls == ['ses_a']
    assert len(session.get('ses_a').queue_pending) == 1 and not session.get('ses_b').queue_pending
    cold = FinalService(h.root, h.automation.auth)
    try:
        cold.recover()
        asyncio.run(cold.dispatch_once())
        assert not session.get('ses_b').queue_pending
        assert len(cold.final.get(job['id'])['attempts']) == 1
        assert provider.calls == ['ses_a']
    finally:
        asyncio.run(cold.close())


def test_lost_native_assign_ack_queries_effective_reservation_not_later_target(h):
    job, rid, frozen = admitted(h)
    job = edit(h, job, 'ses_b', 'effective original receipt', 'retarget')
    class Lost(WorkerAssignProvider):
        calls = 0
        async def assign(self, *args, **kwargs):
            result = await super().assign(*args, **kwargs)
            assert result['status'] in {'queued', 'pending'}
            self.calls += 1
            raise ConnectionError('owned loss after real queue acceptance')
    provider = Lost()
    h.service.executor.assign_provider = provider
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(job['id'])['runs'][rid]['state'] == 'uncertain'
    edit(h, job, 'ses_notice', 'must not be sent', 'after-unknown')
    cold = FinalService(h.root, h.automation.auth)
    try:
        asyncio.run(cold.dispatch_once())  # actual original local query only
        assert cold.final.get(job['id'])['runs'][rid]['state'] == 'completed'
        assert len(session.get('ses_b').queue_pending) == 1
        assert not session.get('ses_notice').queue_pending and provider.calls == 1
        receipt = cold.final.get(job['id'])['effects'][0]['receipt']['deliveryRuntime']
        assert receipt['effectiveTargets'] == ['ses_b'] and receipt['acceptance'][0]['outcome'] == 'succeeded'
        assert cold.final.store.get(job['id'])['finalRuntime']['plans'][rid]['frozen'] == frozen
    finally:
        asyncio.run(cold.close())


def test_mixed_accepted_and_proven_unsent_cold_retarget_same_live_attempt(h):
    job, rid, _ = admitted(h, 'session-broadcast')
    class RemoveNext(WorkerSessionProvider):
        calls = []
        async def send_session(self, sid, text, **kwargs):
            result = await super().send_session(sid, text, **kwargs)
            self.calls.append(sid)
            if sid == 'ses_a': session.delete('ses_b')
            return result
    provider = RemoveNext()
    h.service.executor.session_provider = provider
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'paused_at_boundary' and not state['effects']
    reserved = h.service.final.store.get(job['id'])['finalRuntime']['plans'][rid]['runtimeDelivery']['send']
    assert [c['status'] for c in reserved['children']] == ['accepted', 'known-no-attempt']
    original_attempt = copy.deepcopy(next(iter(state['attempts'].values())))
    edit(h, job, ['ses_a', 'ses_notice'], 'only unsent new body', 'unsent-edit')
    cold = FinalService(h.root, h.automation.auth)
    try:
        cold.recover()
        asyncio.run(cold.dispatch_once())
        final = cold.final.get(job['id'])
        assert final['runs'][rid]['state'] == 'completed'
        assert len(final['attempts']) == len(final['effects']) == 1
        attempt = next(iter(final['attempts'].values()))
        assert all(attempt[k] == original_attempt[k] for k in ('attemptId', 'ownerEpoch', 'generation', 'leaseId'))
        assert session.get('ses_a').queue_pending[0]['text'] == 'original'
        assert session.get('ses_notice').queue_pending[0]['text'] == 'only unsent new body'
        asyncio.run(cold.dispatch_once())
        assert cold.final.get(job['id']) == final
    finally:
        asyncio.run(cold.close())
    assert len(state['attempts']) == 1 and provider.calls == ['ses_a']  # absent target never entered provider
    assert len(session.get('ses_a').queue_pending) == len(session.get('ses_notice').queue_pending) == 1


@pytest.mark.parametrize('phase,expected,count', [
    ('before-reservation', 'completed', 1), ('before-invoke', 'uncertain', 0),
    ('after-enqueue', 'completed', 1), ('after-receipt', 'completed', 1),
])
def test_actual_process_crash_at_committed_fences_cold_original_identity(h, phase, expected, count):
    job, rid, frozen = admitted(h)
    root = Path(__file__).resolve().parents[1]
    path = h.tmp / 'crash-input.json'
    barrier = h.tmp / 'committed-barrier.txt'
    path.write_text(json.dumps({'root': str(h.root), 'auth': str(h.automation.auth.path.parent),
        'sessions': str(session.SESSION_DIR), 'job': job['id'], 'run': rid,
        'barrier': str(barrier), 'phase': phase}), encoding='utf-8')
    result = subprocess.run([sys.executable, str(root / 'scripts/run_tests_isolated.py'), '--',
        sys.executable, str(root / 'tests/retirement_runtime_crash_child.py'), str(path)],
        cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 71, result.stdout + result.stderr
    assert barrier.read_text() == phase
    session._cache.pop('ses_a', None)  # cold actual on-disk queue, not stale fixture cache
    session._all_loaded = False
    session.list_all(load_history=False)  # same metadata-only production bootstrap
    cold = FinalService(h.root, h.automation.auth)
    old_owner = cold.owner.status()
    assert old_owner['epoch'] == 1
    cold.owner.claim(old_owner['epoch'])
    assert cold.owner.token[0] == 2
    try:
        with pytest.raises(fc.ContractError) as stale_owner:
            asyncio.run(h.service.dispatch_once())
        assert stale_owner.value.code == 'revision_conflict'
        cold.recover()
        asyncio.run(cold.dispatch_once())
        state = cold.final.get(job['id'])
        assert state['runs'][rid]['state'] == expected, state
        assert len(state['attempts']) == len(state['effects']) == 1
        assert next(iter(state['attempts'].values()))['generation'] == 1
        assert len(session.get('ses_a').queue_pending) == count
        assert cold.final.store.get(job['id'])['finalRuntime']['plans'][rid]['frozen'] == frozen
        asyncio.run(cold.dispatch_once())
        assert len(session.get('ses_a').queue_pending) == count
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('corruption', ['missing-children', 'wrong-lease', 'reserved-is-not-unsent'])
def test_missing_or_corrupt_child_facts_never_resume_an_assumed_unsent_effect(h, corruption):
    job, rid, _ = admitted(h)
    root = Path(__file__).resolve().parents[1]
    path = h.tmp / 'crash-negative.json'
    path.write_text(json.dumps({'root': str(h.root), 'auth': str(h.automation.auth.path.parent),
        'sessions': str(session.SESSION_DIR), 'job': job['id'], 'run': rid,
        'barrier': str(h.tmp / 'negative-barrier'), 'phase': 'before-reservation'}), encoding='utf-8')
    result = subprocess.run([sys.executable, str(root / 'scripts/run_tests_isolated.py'), '--',
        sys.executable, str(root / 'tests/retirement_runtime_crash_child.py'), str(path)],
        cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 71, result.stdout + result.stderr
    cold = FinalService(h.root, h.automation.auth)
    cold.owner.claim(1)
    try:
        with cold.transaction() as db:
            stored = cold._get(db, 'job', job['id'])
            children = stored['finalRuntime']['plans'][rid]['runtimeDelivery']['send']['children']
            if corruption == 'missing-children': children.clear()
            elif corruption == 'wrong-lease': children[0]['parentFence']['leaseId'] = 'lease_unrelated'
            else: children[0]['status'] = 'reserved'
            cold._save(db, 'job', job['id'], stored)
        cold.recover()
        asyncio.run(cold.dispatch_once())
        state = cold.final.get(job['id'])
        assert state['runs'][rid]['state'] == 'uncertain'
        assert len(state['attempts']) == len(state['effects']) == 1
        assert not session.get('ses_a').queue_pending
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('ack_lost_after_actual_enqueue', [True, False])
def test_mixed_accepted_unknown_and_unsent_only_unsent_retargets(h, ack_lost_after_actual_enqueue):
    session.save(session.Session(id='ses_c', name='owned third target', workdir=str(h.tmp), queue_paused=True))
    job, rid, _ = admitted(h, 'session-broadcast')
    job = edit(h, job, ['ses_a', 'ses_b', 'ses_c'], 'before acceptance', 'three-targets')
    class Mixed(WorkerSessionProvider):
        calls = []
        async def send_session(self, sid, text, **kwargs):
            self.calls.append(sid)
            if sid != 'ses_b' or ack_lost_after_actual_enqueue:
                result = await super().send_session(sid, text, **kwargs)
            if sid == 'ses_b':
                session.delete('ses_c')
                return {'status': 'unknown'}
            return result
    provider = Mixed()
    h.service.executor.session_provider = provider
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.store.get(job['id'])['finalRuntime']
    children = state['plans'][rid]['runtimeDelivery']['send']['children']
    assert [c['status'] for c in children] == ['accepted', 'unknown', 'known-no-attempt']
    assert state['runs'][rid]['state'] == 'paused_at_boundary' and not state['effects']
    fence = copy.deepcopy(children[0]['parentFence'])
    edit(h, job, ['ses_a', 'ses_b', 'ses_notice'], 'only current unsent', 'switch-unsent')
    cold = FinalService(h.root, h.automation.auth)
    try:
        cold.recover()
        asyncio.run(cold.dispatch_once())
        state = cold.final.get(job['id'])
        assert state['runs'][rid]['state'] == ('completed' if ack_lost_after_actual_enqueue else 'uncertain')
        assert len(state['attempts']) == len(state['effects']) == 1
        assert len(session.get('ses_a').queue_pending) == 1
        assert len(session.get('ses_b').queue_pending) == int(ack_lost_after_actual_enqueue)
        assert len(session.get('ses_notice').queue_pending) == 1
        assert session.get('ses_notice').queue_pending[0]['text'] == 'only current unsent'
        original = cold.final.store.get(job['id'])['finalRuntime']['plans'][rid]['runtimeDelivery']['send']['children']
        assert all(c['parentFence'] == fence for c in original)
        assert original[0]['arguments']['text'] == original[1]['arguments']['text'] == 'before acceptance'
        asyncio.run(cold.dispatch_once())
        assert len(session.get('ses_notice').queue_pending) == 1
        assert provider.calls == ['ses_a', 'ses_b']
    finally:
        asyncio.run(cold.close())


def test_additive_runtime_release_does_not_change_v2_and_api_marks_frozen_vs_runtime(h):
    actions = h.service.actions(actor={'type': 'user', 'id': 'local'})['items']
    for op in ('assign', 'session-message', 'session-broadcast'):
        base = 'product_' + op.replace('-', '_')
        old = next(a for a in actions if a['id'] == base + '_v2')
        runtime = next(a for a in actions if a['id'] == base + '_runtime_v1')
        assert 'runtimeBeforeAcceptance' not in old
        value = definition('action', runtime)
        value.pop('runtimeBeforeAcceptance')
        value['providerRef'] = old['providerRef']
        assert value == definition('action', old)
    job, rid, frozen = admitted(h)
    asyncio.run(h.service.dispatch_once())
    body = h.client.get('/api/job-runs/' + rid).json()
    fc.validate_record('run', body)
    declaration = body['deliveryRuntime']['declaration']
    assert declaration == frozen['deliveryRuntime']
    child = body['deliveryRuntime']['steps']['send']['children'][0]
    assert child['status'] == 'accepted' and child['runtimeRevision'] == job['configRevision']
    assert child['textDigest'] == fc.digest('original') and 'arguments' not in child


def test_old_release_cannot_gain_runtime_from_job_declaration_and_rejection_has_no_write(h):
    job, _, _ = admitted(h)
    old = next(a for a in h.service.actions(actor={'type': 'user', 'id': 'local'})['items'] if a['id'] == 'product_assign_v2')
    cfg = definition('job', job)
    cfg['steps'][0]['actionRef'] = object_ref('action', old)
    with h.service.transaction(readonly=True) as db:
        before = db.execute('SELECT count(*) FROM final_operations').fetchone()[0]
    response = h.client.put('/api/jobs/' + job['id'], json={'definition': cfg,
        'expectedRevision': job['configRevision'], 'requestKey': 'invalid-runtime-release'})
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'reference_conflict'
    with h.service.transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM final_operations').fetchone()[0] == before
    assert not session.get('ses_a').queue_pending


def test_equal_no_attempt_dictionary_after_actual_enqueue_never_authorizes_resend(h):
    job, rid, _ = admitted(h)
    class Spoof(WorkerAssignProvider):
        calls = 0
        async def assign(self, *args, **kwargs):
            await super().assign(*args, **kwargs)
            self.calls += 1
            return {'status': 'rejected', 'attempted': False, 'phase': 'before-enqueue'}
    provider = Spoof()
    h.service.executor.assign_provider = provider
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'uncertain'
    edit(h, job, 'ses_b', 'not new proof', 'spoof-retarget')
    cold = FinalService(h.root, h.automation.auth)
    try:
        asyncio.run(cold.dispatch_once())
        assert cold.final.get(job['id'])['runs'][rid]['state'] == 'completed'  # original actual queue lookup only
        assert provider.calls == 1 and len(session.get('ses_a').queue_pending) == 1
        assert not session.get('ses_b').queue_pending
    finally:
        asyncio.run(cold.close())


def test_real_http_concurrent_cas_during_first_acceptance_selects_only_winning_unsent_values(h):
    job, rid, frozen = admitted(h, 'session-broadcast')
    class ConcurrentEdit(WorkerSessionProvider):
        calls = []
        async def send_session(self, sid, text, **kwargs):
            result = await super().send_session(sid, text, **kwargs)
            self.calls.append(sid)
            if sid == 'ses_a':
                def update(i):
                    cfg = definition('job', job)
                    cfg['arguments']['delivery'] = {'target': ['ses_a', 'ses_notice'], 'text': 'winner-' + str(i)}
                    with httpx.Client(base_url=str(h.client.base_url), trust_env=False,
                            headers={'Authorization': 'Bearer ' + h.token}) as client:
                        return client.put('/api/jobs/' + job['id'], json={'definition': cfg,
                            'expectedRevision': job['configRevision'], 'requestKey': 'race-' + str(i)})
                with ThreadPoolExecutor(2) as pool:
                    responses = list(pool.map(update, range(2)))
                assert sorted(r.status_code for r in responses) == [200, 409]
                self.winner = next(r.json()['result']['object']['arguments']['delivery']['text'] for r in responses if r.status_code == 200)
            return result
    provider = ConcurrentEdit()
    h.service.executor.session_provider = provider
    cold = FinalService(h.root, h.automation.auth)
    cold.executor.session_provider = provider
    try:
        async def dispatch():
            await asyncio.gather(h.service.dispatch_once(), cold.dispatch_once())
        asyncio.run(dispatch())
        state = cold.final.get(job['id'])
        assert state['runs'][rid]['state'] == 'completed'
        assert len(state['attempts']) == len(state['effects']) == 1
        assert provider.calls == ['ses_a', 'ses_notice']
        assert session.get('ses_notice').queue_pending[0]['text'] == provider.winner
        assert not session.get('ses_b').queue_pending
        assert len(session.get('ses_a').queue_pending) == 1
        assert cold.final.store.get(job['id'])['finalRuntime']['plans'][rid]['frozen'] == frozen
    finally:
        asyncio.run(cold.close())
