"""Phase 1 Action-chain contracts on the owned Job runtime."""

import asyncio
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import jsonschema
from packages.actions.store import Store
from packages.jobs.action_runtime import Runtime
from packages.jobs import chains
from packages.jobs.action_templates import Conflict, config_value, references
import packages.actions.executor as executor
from tests.test_automation_core import async_test
from tests.test_job_final_http_service import h
from tests.test_job_automatic_recovery import env


def _action(action_id, *, monitoring=False):
    return {
        'id': action_id, 'name': action_id, 'enabled': True, 'effect': 'write_local',
        'monitoring': monitoring, 'recoverable': monitoring,
        'input_schema': {'type': 'object', 'properties': {'value': {'type': 'string'}},
                         'required': ['value'], 'additionalProperties': False},
        'execution': {'kind': 'session_operation', 'operation': 'pause_everything',
                      'selector': {'mode': 'explicit', 'ids': ['selected']}},
    }


def _config(steps, **extra):
    return {
        'name': 'chain', 'actionId': steps[0]['actionId'], 'arguments': {'input': 'seed'},
        'steps': steps, 'enabled': True, 'schedule': [], 'trigger': {'kind': 'immediate'},
        'recovery': {'enabled': False, 'monitoring': False, 'maxAttempts': 3,
                     'backoffSec': [0, 0, 0], 'stableResetSec': 300},
        'overlap': {'mode': 'forbid', 'parallelLimit': 3},
        'notification': {'on': ['completed', 'failed', 'cancelled', 'uncertain'], 'target': 'job'},
        'completion': {'kind': 'ordinary'}, 'maxRuns': None, 'misfirePolicy': 'fire_now',
        **extra,
    }


async def _runtime(tmp_path, ids=('one', 'two')):
    store = Store(Path(tmp_path) / 'actions')
    for action_id in ids:
        store.put('actions', _action(action_id))
    runtime = Runtime(Path(tmp_path) / 'jobs', store, None)
    await runtime.start()
    return runtime


def test_chain_mapping_order_and_step_audit(h):
    from tests.test_job_final_http_service import create, control, job_def
    from packages.jobs.final_runtime.executor import TypedExecutor
    calls = []

    class Provider:
        async def send_session(self, session_id, text, *, effect_key, source):
            calls.append((effect_key['stepId'], text))
            return {'status': 'queued', 'messageId': text + '!'}

    h.service.executor = TypedExecutor(session_provider=Provider())
    value = job_def(h, steps=2)
    value['steps'][0]['arguments']['text'] = {'from': 'input', 'path': 'input'}
    value['steps'][1]['arguments']['text'] = {'from': 'step', 'stepId': 's0', 'path': 'messageId'}
    value['inputSchema'] = {'type': 'object', 'properties': {'input': {'type': 'string'}},
                            'required': ['input'], 'additionalProperties': False}
    value['arguments'] = {'input': 'seed'}
    job = create(h, value=value)
    response = control(h, 'jobs', job, 'enable')
    assert response.status_code == 200, response.text
    job = response.json()['result']['object']
    admitted = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
        'expectedRevision': job['configRevision'], 'requestKey': 'chain-mapping',
        'input': {'input': 'seed'}, 'cause': {'type': 'manual'}})
    assert admitted.status_code == 202, admitted.text
    rid = admitted.json()['runId']
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert calls == [('s0', 'seed'), ('s1', 'seed!')]
    assert state['runs'][rid]['state'] == 'completed'
    assert [state['steps'][rid][sid]['state'] for sid in ('s0', 's1')] == ['completed', 'completed']
    attempts = list(state['attempts'].values())
    assert len(attempts) == 2 and {a['stepId'] for a in attempts} == {'s0', 's1'}
    assert all(a['runId'] == rid and a['state'] == 'succeeded' for a in attempts)
    assert len(state['effects']) == 2
    assert {e['attemptId'] for e in state['effects']} == {a['attemptId'] for a in attempts}
    assert all(e['effectKey']['runId'] == rid and e['outcome'] == 'succeeded' for e in state['effects'])
    # Repeated dispatch observes terminal identity and never repeats providers.
    asyncio.run(h.service.dispatch_once())
    assert calls == [('s0', 'seed'), ('s1', 'seed!')]


def _steps():
    return [{'stepId': 'first', 'actionId': 'one', 'arguments': {'value': 'first'}},
            {'stepId': 'second', 'actionId': 'two', 'arguments': {'value': 'second'}}]


def test_legacy_single_step_equivalent_and_no_legacy_rewrite(h, tmp_path):
    from tests.test_job_final_http_service import enabled, job_def, launch
    from packages.core import background_jobs
    root = tmp_path / 'historical'
    cfg = _config(_steps(), arguments={'value': 'same'})
    cfg.pop('steps')
    historical = {'jobId': 'job_old_single', 'kind': 'scheduled-task',
                  'status': 'completed', 'config': cfg, 'lastExecution': {'result': {'accepted': True}}}
    path = root / 'jobs' / 'job_old_single.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(historical), encoding='utf-8')
    before = path.read_bytes()
    old = Runtime(root, Store(tmp_path / 'retired-actions'), None)
    with pytest.raises(PermissionError, match='retired'):
        old.create(cfg, True)
    restored = background_jobs.get('job_old_single', root)
    assert {key: restored[key] for key in historical} == historical
    assert 'steps' not in background_jobs.get('job_old_single', root)['config']
    # A fresh one-step Job executes through the same canonical chain machinery.
    value = job_def(h)
    value['steps'][0]['arguments']['text']['value'] = 'same'
    job = enabled(h, value)
    rid = launch(h, job, 'canonical-single')['runId']
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'completed'
    assert [text for text, _ in h.provider.calls] == ['same']
    assert len(state['attempts']) == len(state['effects']) == 1
    assert state['steps'][rid]['s0']['state'] == 'completed'
    asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 1 and path.read_bytes() == before


def test_chain_is_serial_and_cancel_stops_next_step(h, tmp_path):
    from tests.test_action_unification_p2 import install, py
    from tests.test_job_final_http_service import enabled, launch, USER
    install(h)
    commands = [py(tmp_path, "from pathlib import Path;import time;Path('started').write_text('yes');time.sleep(30)"),
                py(tmp_path, "from pathlib import Path;Path('forbidden').write_text('second')")]
    value = {'name': 'cancel serial chain', 'retention': {'policyRef': 'test'}, 'steps': [
        {'stepId': sid, 'actionRef': h.action,
         'arguments': {key: {'from': 'literal', 'value': val} for key, val in args.items()},
         'completion': {'contract': 'process-exit'}}
        for sid, args in zip(('first', 'second'), commands)]}
    job = enabled(h, value)
    rid = launch(h, job, 'cancel-chain')['runId']
    async def scenario():
        task = asyncio.create_task(h.service.dispatch_once())
        try:
            async with asyncio.timeout(5):
                while not (tmp_path / 'started').exists():
                    await asyncio.sleep(.01)
            assert not (tmp_path / 'forbidden').exists()
            run = h.service.get_run(rid, actor=USER)
            response = await h.service.control_run_postcommit(rid, {
                'operation': 'cancel', 'requestKey': 'cancel-original-chain',
                'expectedStateRevision': run['stateRevision']}, actor=USER)
            await asyncio.wait_for(task, 5)
            return response
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    response = asyncio.run(scenario())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'cancelled'
    # Persisted cancellation closes the command's running fence; its original
    # receipt retains that rejection while the Run carries cancelled state.
    assert [state['steps'][rid][sid]['state'] for sid in ('first', 'second')] == ['failed', 'pending']
    attempt, = state['attempts'].values()
    assert attempt['stepId'] == 'first' and attempt['state'] == 'failed'
    assert state['steps'][rid]['second']['attemptCount'] == 0
    assert not (tmp_path / 'forbidden').exists()
    effect, = state['effects']
    assert effect['outcome'] == 'rejected'
    command = effect['receipt']['receipt']['commands'][0]
    assert command['code'] == 'revision_conflict'
    assert command['error'] == 'Original Attempt is no longer executable'
    assert command['cleanupProof']['confirmed'] is True
    assert effect['receipt']['cleanupProof']['confirmed'] is True
    assert h.service.operation(response['operationId'], actor=USER)['status'] == 'completed'
    asyncio.run(h.service.dispatch_once())
    assert not (tmp_path / 'forbidden').exists() and h.service.final.get(job['id']) == state


@pytest.mark.parametrize('status', ['unknown', 'uncertain', 'unconfirmed'])
def test_unknown_step_blocks_and_never_replays(h, status):
    from tests.test_job_final_http_service import enabled, job_def, launch, run_control
    from packages.jobs.final_runtime.executor import TypedExecutor
    calls = []
    class Provider:
        async def send_session(self, session_id, text, *, effect_key, source):
            calls.append(effect_key['stepId'])
            return {'status': status}
    h.service.executor = TypedExecutor(session_provider=Provider())
    job = enabled(h, job_def(h, steps=2))
    rid = launch(h, job, 'unknown-chain')['runId']
    for _ in range(3):
        asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'uncertain' and calls == ['s0']
    assert [state['steps'][rid][sid]['state'] for sid in ('s0', 's1')] == ['uncertain', 'pending']
    attempt, = state['attempts'].values()
    effect, = state['effects']
    assert attempt['state'] == 'uncertain' and attempt['runId'] == rid
    assert effect['attemptId'] == attempt['attemptId'] and effect['outcome'] == 'unknown'
    response = run_control(h, rid, 'retry', 'no-replay-unknown')
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'reconciliation_required'
    asyncio.run(h.service.dispatch_once())
    assert calls == ['s0'] and h.service.final.get(job['id']) == state


def test_admission_freezes_all_steps_inputs_and_definitions(h):
    from tests.test_job_final_http_service import enabled, job_def, launch, USER
    from tests.test_action_unification_p4 import plans, upgrade
    from packages.jobs.final_runtime.executor import TypedExecutor

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Provider:
            async def send_session(self, session_id, text, *, effect_key, source):
                calls.append((effect_key['stepId'], text))
                if effect_key['stepId'] == 's0':
                    entered.set()
                    await release.wait()
                return {'status': 'queued', 'messageId': 'accepted'}
        h.service.executor = TypedExecutor(session_provider=Provider())
        value = job_def(h, steps=2)
        value['steps'][1]['arguments']['text'] = {'from': 'input', 'path': 'input'}
        value['inputSchema'] = {'type': 'object', 'properties': {'input': {'type': 'string'}},
                                'required': ['input'], 'additionalProperties': False}
        value['arguments'] = {'input': 'seed'}
        job = enabled(h, value)
        admitted = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
            'expectedRevision': job['configRevision'], 'requestKey': 'frozen-chain',
            'input': {'input': 'seed'}, 'cause': {'type': 'manual'}})
        assert admitted.status_code == 202, admitted.text
        rid = admitted.json()['runId']
        frozen = copy.deepcopy(plans(h, job['id'])[rid]['frozen'])
        dispatch = asyncio.create_task(h.service.dispatch_once())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            old_ref, new_ref = upgrade(h)
            assert new_ref != old_ref
            changed = copy.deepcopy(value)
            changed['arguments']['input'] = 'changed'
            changed['steps'][1]['actionRef'] = new_ref
            response = h.client.put('/api/jobs/' + job['id'], json={
                'definition': changed, 'expectedRevision': job['configRevision'], 'requestKey': 'edit-active-chain'})
            assert response.status_code == 200, response.text
            assert plans(h, job['id'])[rid]['frozen'] == frozen
            release.set()
            await asyncio.wait_for(dispatch, 5)
        finally:
            release.set()
            if not dispatch.done():
                dispatch.cancel()
                await asyncio.gather(dispatch, return_exceptions=True)
        state = h.service.final.get(job['id'])
        current = h.service.get('job', job['id'], actor=USER)
        assert calls == [('s0', '0'), ('s1', 'seed')]
        assert current['configRevision'] == 2 and state['runs'][rid]['configRevision'] == 1
        assert state['runs'][rid]['state'] == 'completed'
        assert frozen['steps'][1]['actionRef'] == old_ref
        assert len(state['attempts']) == len(state['effects']) == 2
        public = h.client.get('/api/jobs/' + job['id']).json()
        assert 'chainSnapshot' not in json.dumps(public) and 'chainRecovery' not in json.dumps(public)
        assert 'frozen' not in public
    asyncio.run(run())


def test_event_and_input_and_output_mapping(h):
    from tests.test_job_final_http_service import enabled, job_def
    from tests.test_action_unification_p4 import trigger, event
    from packages.jobs.final_runtime.executor import TypedExecutor
    calls = []
    class Provider:
        async def send_session(self, session_id, text, *, effect_key, source):
            calls.append(text)
            return {'status': 'queued', 'messageId': text + '!'}
    h.service.executor = TypedExecutor(session_provider=Provider())
    value = job_def(h, steps=3)
    value['inputSchema'] = {'type': 'object', 'properties': {'input': {'type': 'string'}},
                            'required': ['input'], 'additionalProperties': False}
    value['arguments'] = {'input': 'seed'}
    value['steps'][0]['arguments']['text'] = {'from': 'input', 'path': 'input'}
    value['steps'][1]['arguments']['text'] = {'from': 'event', 'path': 'payload.title'}
    value['steps'][2]['arguments']['text'] = {'from': 'step', 'stepId': 's1', 'path': 'messageId'}
    job = enabled(h, value)
    source = trigger(h, {'type': 'job', 'jobId': job['id'], 'configRevision': job['configRevision']})
    fact = event('chain-event')
    admitted = h.service.admit_event(fact, source)
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    run, = state['runs'].values()
    assert calls == ['seed', 'From event', 'From event!']
    assert run['state'] == 'completed' and len(state['steps'][run['runId']]) == 3
    assert len(state['attempts']) == len(state['effects']) == 3
    assert all(e['effectKey']['runId'] == run['runId'] for e in state['effects'])
    repeated = h.service.admit_event(fact, source)
    assert repeated['operationId'] == admitted['operationId'] and repeated['replayed'] is True
    asyncio.run(h.service.dispatch_once())
    assert calls == ['seed', 'From event', 'From event!']
    assert len(h.service.final.get(job['id'])['runs']) == 1


@pytest.mark.parametrize('binding', [
    {'from': 'input', 'path': 'missing'}, {'from': 'input', 'path': ''},
])
def test_mapping_missing_or_type_error_fails_before_effect(h, binding):
    from tests.test_job_final_http_service import job_def
    from tests.test_action_groups import canonical_state
    value = job_def(h, steps=2)
    value['inputSchema'] = {'type': 'object', 'properties': {'input': {'type': 'string'}},
                            'additionalProperties': False}
    value['steps'][0]['arguments']['text'] = binding
    before = canonical_state(h.service)
    response = h.client.post('/api/jobs', json={
        'definition': value, 'expectedRevision': 0, 'requestKey': 'invalid-mapping'})
    assert response.status_code == 400, response.text
    assert response.json()['error']['code'] == 'invalid_schema'
    assert canonical_state(h.service) == before and h.provider.calls == []
    assert h.client.get('/api/jobs').json()['items'] == []


@async_test
async def test_all_action_references_generations_and_template_chain(tmp_path):
    runtime = await _runtime(tmp_path)
    try:
        cfg = _config(_steps(), enabled=False)
        template = runtime.templates.put({'name': 'all steps', 'config': cfg}, 0)
        job = runtime.instantiate(template['id'], template['revision'], 'stable-key')
        assert job['config']['steps'] == _steps()
        assert references(runtime.actions.read(), 'two')
        with pytest.raises(PermissionError):runtime.actions.delete('actions', 'two')
        runtime.actions.delete('actions', 'two', authorized=True, confirm_references=True)
        runtime.actions.put('actions', _action('two'))
        assert runtime.view(runtime.get(job['jobId']))['integrity']['approvalRequired']
        assert not runtime.templates.view(template, runtime.actions.read())['usable']
    finally:
        await runtime.close()


@pytest.mark.parametrize('mutation', ['duplicate', 'forward', 'extra', 'bad_literal'])
def test_invalid_chain_configuration_rejected(h, mutation):
    from tests.test_job_final_http_service import job_def
    from tests.test_action_groups import canonical_state
    value = job_def(h, steps=2)
    steps = value['steps']
    if mutation == 'duplicate':
        steps[1]['stepId'] = 's0'
    elif mutation == 'forward':
        steps[0]['arguments']['text'] = {'from': 'step', 'stepId': 's1', 'path': 'messageId'}
    elif mutation == 'extra':
        steps[0]['arguments']['undeclared'] = {'from': 'literal', 'value': 'value'}
    else:
        steps[0]['arguments']['text'] = {'from': 'literal', 'value': 5}
    before = canonical_state(h.service)
    response = h.client.post('/api/jobs', json={
        'definition': value, 'expectedRevision': 0, 'requestKey': 'invalid-chain'})
    assert response.status_code == 400, response.text
    assert response.json()['error']['code'] == 'invalid_schema'
    assert canonical_state(h.service) == before and h.provider.calls == []


def test_parallel_runs_have_separate_step_attempts(h):
    from tests.test_job_final_http_service import enabled, job_def, launch
    from packages.jobs.final_runtime.executor import TypedExecutor
    calls = []
    class Provider:
        async def send_session(self, session_id, text, *, effect_key, source):
            await asyncio.sleep(0)
            calls.append(dict(effect_key))
            return {'status': 'queued', 'messageId': effect_key['runId'] + effect_key['stepId']}
    h.service.executor = TypedExecutor(session_provider=Provider())
    job = enabled(h, job_def(h, steps=2, overlap={'mode': 'parallel', 'maxActive': 2, 'maxPending': 0}))
    runs = {launch(h, job, key)['runId'] for key in ('parallel-1', 'parallel-2')}
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert set(state['runs']) == runs and len(runs) == 2
    assert len(state['attempts']) == len(state['effects']) == 4
    assert len({a['attemptId'] for a in state['attempts'].values()}) == 4
    for rid in runs:
        assert state['runs'][rid]['state'] == 'completed'
        assert [state['steps'][rid][sid]['state'] for sid in ('s0', 's1')] == ['completed'] * 2
        assert len([a for a in state['attempts'].values() if a['runId'] == rid]) == 2
        assert [c['stepId'] for c in calls if c['runId'] == rid] == ['s0', 's1']
    assert len(calls) == 4
    asyncio.run(h.service.dispatch_once())
    assert len(calls) == 4


@pytest.mark.parametrize('damage', [None, 'missing_receipt', 'wrong_key', 'wrong_output', 'pending_admission', 'missing_operation'])
def test_restart_boundary_reuses_original_run_and_never_reruns_completed(h, tmp_path, damage):
    from packages.jobs.final_service import FinalService
    from tests.test_job_final_http_service import enabled, job_def, launch, USER
    job = enabled(h, job_def(h, steps=2))
    admission = launch(h, job, 'crash-completed-boundary')
    rid = admission['runId']
    effects = tmp_path / 'effects.txt'
    script = r"""
import asyncio, sys, os
from pathlib import Path
from contextlib import contextmanager
from packages.jobs.final_service import FinalService
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_runtime.executor import TypedExecutor
root, jid, rid, output = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
class Provider:
    async def send_session(self, session_id, text, **kwargs):
        with output.open('a') as f: f.write(text+'\n')
        return {'status':'queued', 'messageId':'message_first'}
service = FinalService(root, CanonicalUserAuth(root.parent/'automation'), executor=TypedExecutor(session_provider=Provider()))
original = service.transaction
@contextmanager
def transaction(*, readonly=False):
    with original(readonly=readonly) as db:
        yield db
    if not readonly:
        state = service.final.get(jid)
        if state['steps'][rid]['s0']['state'] == 'completed' and state['steps'][rid]['s1']['state'] == 'pending':
            os._exit(23)
service.transaction = transaction
asyncio.run(service.dispatch_once())
raise AssertionError('crash boundary not reached')
"""
    child = subprocess.run([sys.executable, '-c', script, str(h.root), job['id'], rid, str(effects)],
        cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True, timeout=20)
    assert child.returncode == 23, child.stderr
    original_state = h.service.final.get(job['id'])
    first_attempt, = original_state['attempts'].values()
    first_effect, = original_state['effects']
    assert first_attempt['state'] == 'succeeded' and first_effect['outcome'] == 'succeeded'
    assert effects.read_text().splitlines() == ['0']
    if damage:
        with h.service.transaction() as db:
            stored = h.service._get(db, 'job', job['id'])
            from packages.jobs.final_runtime.ledger import aggregate
            ledger = aggregate(stored, h.root)
            if damage == 'missing_receipt':
                ledger['effects'].clear()
            elif damage == 'wrong_key':
                ledger['effects'][0]['receipt']['effectKey']['runId'] = 'run_wrong'
            elif damage == 'wrong_output':
                ledger['plans'][rid]['outputs']['s0'] = {'forged': True}
            elif damage == 'missing_operation':
                ledger['plans'][rid]['operationId'] = 'op_missing'
            elif damage == 'pending_admission':
                ledger['effectAdmissions']['unconfirmed'] = {
                    'attemptId': first_attempt['attemptId'], 'effectKey': first_effect['effectKey']}
            h.service._save(db, 'job', job['id'], stored)
    original_send = h.provider.send_session
    async def send(session_id, text, **kwargs):
        with effects.open('a') as f:
            f.write(text + '\n')
        return await original_send(session_id, text, **kwargs)
    h.provider.send_session = send
    cold = FinalService(h.root, h.automation.auth, executor=h.service.executor)
    try:
        cold.recover()
        asyncio.run(cold.dispatch_once())
        done = cold.final.get(job['id'])
        if damage:
            assert done['runs'][rid]['state'] == 'uncertain'
            assert done['attempts'][first_attempt['attemptId']] == first_attempt
            assert done['steps'][rid]['s1']['attemptCount'] == 0
            assert h.provider.calls == [] and effects.read_text().splitlines() == ['0']
            asyncio.run(cold.dispatch_once())
            assert cold.final.get(job['id']) == done
            return
        assert done['runs'][rid]['state'] == 'completed'
        assert set(done['runs']) == {rid}
        assert effects.read_text().splitlines() == ['0', '1']
        assert [done['steps'][rid][sid]['attemptCount'] for sid in ('s0', 's1')] == [1, 1]
        assert done['attempts'][first_attempt['attemptId']] == first_attempt
        assert first_effect in done['effects']
        assert cold.operation(admission['operationId'], actor=USER)['status'] == 'completed'
        asyncio.run(cold.dispatch_once())
        assert effects.read_text().splitlines() == ['0', '1']
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('outcome,terminal', [('rejected', 'failed'), ('unknown', 'uncertain')])
def test_chain_failure_and_uncertain_block_following_step(h, outcome, terminal):
    from tests.test_job_final_http_service import enabled, job_def, launch
    job = enabled(h, job_def(h, steps=2))
    if outcome == 'rejected':
        h.provider.fail_once = '0'
    else:
        h.provider.unknown = True
    rid = launch(h, job, 'stop-at-first-effect')['runId']
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert [text for text, _ in h.provider.calls] == ['0']
    assert state['runs'][rid]['state'] == terminal
    assert state['steps'][rid]['s0']['state'] == terminal
    assert state['steps'][rid]['s1']['state'] == 'pending'
    assert state['steps'][rid]['s1']['attemptCount'] == 0
    attempt, = state['attempts'].values()
    effect, = state['effects']
    assert attempt['stepId'] == 's0' and effect['attemptId'] == attempt['attemptId']
    assert effect['outcome'] == outcome
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(job['id']) == state and len(h.provider.calls) == 1


def test_chain_monitoring_recovery_reuses_completed_step(env):
    from tests.test_job_automatic_recovery import create, drive_until, state
    from tests.test_job_monitoring_completion import monitoring_definition
    first, second = env.tmp / 'first-monitor', env.tmp / 'second-monitor'
    second_code = (f'from pathlib import Path; import json; p=Path({str(second)!r}); '
        'n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1)); '
        'print(json.dumps({"panJobCompletion":{"type":"owned.done","completed": n > 0}}))')
    value = monitoring_definition(env, second_code)
    monitor = copy.deepcopy(value['steps'][0])
    monitor['stepId'] = 's1'
    first_step = copy.deepcopy(monitor)
    first_step['stepId'] = 's0'
    first_step['arguments']['argv']['value'] = [sys.executable, '-c',
        f'from pathlib import Path; import json; Path({str(first)!r}).open("a").write("first\\n"); '
        'print(json.dumps({"panJobCompletion":{"type":"owned.done","completed":True}}))']
    value['steps'] = [first_step, monitor]
    jid, rid = create(env, value)
    asyncio.run(drive_until(env, jid, rid, 'completed'))
    done = state(env, jid)
    assert first.read_text().splitlines() == ['first'] and second.read_text() == '2'
    assert set(done['runs']) == {rid}
    assert [done['steps'][rid][sid]['attemptCount'] for sid in ('s0', 's1')] == [1, 2]
    assert len(done['effects']) == len(done['attempts']) == 3
    original = next(a for a in done['attempts'].values() if a['stepId'] == 's0')
    assert original['state'] == 'succeeded'
    asyncio.run(env.service.dispatch_once())
    assert state(env, jid)['effects'] == done['effects']
    assert first.read_text().splitlines() == ['first'] and second.read_text() == '2'


def test_known_failure_explicit_retry_preserves_completed_and_frozen_steps(h):
    from tests.test_job_final_http_service import enabled, job_def, launch, run_control
    from tests.test_action_unification_p4 import plans
    value = job_def(h, steps=2)
    job = enabled(h, value)
    h.provider.fail_once = '1'
    rid = launch(h, job, 'failed-boundary')['runId']
    asyncio.run(h.service.dispatch_once())
    failed = h.service.final.get(job['id'])
    assert failed['runs'][rid]['state'] == 'failed'
    assert [text for text, _ in h.provider.calls] == ['0', '1']
    original_plan = copy.deepcopy(plans(h, job['id'])[rid]['frozen'])
    changed = copy.deepcopy(value)
    changed['steps'][1]['arguments']['text']['value'] = 'edited'
    response = h.client.put('/api/jobs/' + job['id'], json={
        'definition': changed, 'expectedRevision': job['configRevision'], 'requestKey': 'edit-before-retry'})
    assert response.status_code == 200, response.text
    response = run_control(h, rid, 'retry', 'retry-original-boundary', stepId='s1')
    assert response.status_code == 202, response.text
    asyncio.run(h.service.dispatch_once())
    done = h.service.final.get(job['id'])
    assert [text for text, _ in h.provider.calls] == ['0', '1', '1']
    assert set(done['runs']) == {rid} and done['runs'][rid]['state'] == 'completed'
    assert [len([a for a in done['attempts'].values() if a['stepId'] == sid]) for sid in ('s0', 's1')] == [1, 2]
    assert plans(h, job['id'])[rid]['frozen'] == original_plan
    assert len(done['effects']) == 3
    assert 'chainRecovery' not in h.client.get('/api/jobs/' + job['id']).json()


@async_test
@pytest.mark.parametrize('phase', ['step_running', 'boundary'])
async def test_chain_write_failure_never_advances_or_replays(tmp_path, monkeypatch, phase):
    runtime = await _runtime(tmp_path)
    calls = []
    async def execute(action, *unused):calls.append(action['id']); return {'ok': True}
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    original = runtime.chain_save
    failed = False
    def save(jid, eid, steps, current_phase):
        nonlocal failed
        if current_phase == phase and not failed:
            failed = True
            raise OSError('owned injected boundary failure')
        return original(jid, eid, steps, current_phase)
    monkeypatch.setattr(runtime, 'chain_save', save)
    try:
        job = runtime.create(_config(_steps()), True)
        await runtime.launch(job['jobId'], 1); await asyncio.gather(*runtime.tasks.values())
        current = runtime.get(job['jobId'])
        assert current['lastStatus'] == 'uncertain'
        assert calls == ([] if phase == 'step_running' else ['one'])
        assert current['lastExecution']['stepExecutions'][1]['status'] == 'pending'
        await runtime.tick({'enabled': True})
        assert calls == ([] if phase == 'step_running' else ['one'])
    finally:
        await runtime.close()


def test_running_step_restart_is_uncertain_and_never_replayed(h):
    from tests.test_job_final_http_service import enabled, job_def, launch, USER
    from packages.jobs.final_service import FinalService
    job = enabled(h, job_def(h, steps=2))
    admission = launch(h, job, 'host-loss-chain')
    rid = admission['runId']
    # Durable admission and running Attempt exist, but host loss occurs before
    # any receipt. Recovery may only preserve unknown, never send again.
    with h.service.transaction() as db:
        stored = h.service._get(db, 'job', job['id'])
        ledger = h.service.final.ledger
        ledger.start(stored, h.root, rid)
        attempt = ledger.begin_attempt(stored, h.root, rid, 's0')
        key = ledger.admit_effect(stored, h.root, attempt['attemptId'], h.action['id'])
        h.service._save(db, 'job', job['id'], stored)
    restored = FinalService(h.root, h.automation.auth, executor=h.service.executor)
    try:
        restored.recover()
        state = restored.final.get(job['id'])
        assert state['runs'][rid]['state'] == 'uncertain'
        assert [state['steps'][rid][sid]['state'] for sid in ('s0', 's1')] == ['uncertain', 'pending']
        assert state['attempts'][attempt['attemptId']]['state'] == 'uncertain'
        effect, = state['effects']
        assert effect['attemptId'] == attempt['attemptId'] and effect['outcome'] == 'unknown'
        assert effect['receipt'] == {'code': 'host_restarted'}
        assert restored.operation(admission['operationId'], actor=USER)['status'] == 'unknown'
        for _ in range(2):
            asyncio.run(restored.dispatch_once())
        assert restored.final.get(job['id']) == state and h.provider.calls == []
    finally:
        asyncio.run(restored.close())


@pytest.mark.parametrize('revoke', ['action', 'authorization'])
def test_chain_checks_revocation_between_steps(h, revoke):
    from tests.test_job_final_http_service import enabled, job_def, USER
    from tests.test_authorization_mcp import grants
    from packages.jobs.final_runtime.executor import TypedExecutor
    permissions = grants()
    h.automation.auth.change_permissions(permissions, 0)
    calls = []
    class Provider:
        async def send_session(self, session_id, text, *, effect_key, source):
            calls.append(effect_key['stepId'])
            if revoke == 'action':
                action = h.service.get('action', h.action['id'], actor=USER)
                h.service.control('action', action['id'], {'operation': 'disable',
                    'expectedRevision': action['revision'], 'expectedStateRevision': action['stateRevision'],
                    'parameters': {}, 'requestKey': 'revoke-at-step-boundary'}, actor=USER)
            else:
                denied = {kind: {capability: False for capability in row} for kind, row in permissions.items()}
                h.automation.auth.change_permissions(denied, 1)
            return {'status': 'queued', 'messageId': 'original-first-receipt'}
    h.service.executor = TypedExecutor(session_provider=Provider())
    job = enabled(h, job_def(h, steps=2))
    admitted = h.service.create_run(job['id'], {'expectedRevision': job['configRevision'],
        'requestKey': 'revoked-chain', 'input': {}, 'cause': {'type': 'manual'}},
        actor={'type': 'agent', 'id': 'owned-revocation', 'revision': 1})
    rid = admitted['runId']
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert calls == ['s0'] and state['runs'][rid]['state'] == 'failed'
    assert [state['steps'][rid][sid]['state'] for sid in ('s0', 's1')] == ['completed', 'failed']
    assert len(state['attempts']) == len(state['effects']) == 1
    attempt, = state['attempts'].values()
    assert attempt['stepId'] == 's0' and attempt['state'] == 'succeeded'
    assert state['steps'][rid]['s1']['attemptCount'] == 0
    assert state['effects'][0]['outcome'] == 'succeeded'
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(job['id']) == state and calls == ['s0']



def test_public_projection_preserves_legacy_result_field_names():
    from packages.core import background_jobs as jobs
    original = {'jobId': 'legacy', 'kind': 'scheduled-task', 'status': 'completed',
                'lastDelivery': {'chainSnapshot': 'user result', 'chainRecovery': {'data': 'user'}}}
    assert jobs.job_public_view(original)['lastDelivery'] == original['lastDelivery']




def test_real_owned_commands_pass_structured_output_without_server(h, tmp_path):
    from packages.actions.command_execution import product_definition
    from packages.jobs.final_service import object_ref
    from tests.test_job_final_http_service import enabled, launch
    refs = []
    for aid, code in (
        ('one', 'import json;print(json.dumps(dict(answer="owned")))'),
        ('two', 'import ast,json,sys;from pathlib import Path;commands=ast.literal_eval(sys.argv[1]);value=json.loads(commands[0]["stdout"])["answer"];Path("effect.txt").write_text(value);print(json.dumps(dict(written=value)))'),
    ):
        raw = {**_action(aid), 'output_format': 'json',
            'execution': {'kind': 'command', 'executable': sys.executable,
                          'args': ['-c', code, '{value}'], 'cwd': str(tmp_path)}}
        if aid == 'two':
            raw['input_schema']['properties']['value'] = {'type': 'array', 'items': {'type': 'object'}}
        action = product_definition('process.command')
        action.update(system=False, name=aid, inputSchema=raw['input_schema'])
        action['execution']['config'] = {key: val for key, val in raw['execution'].items() if key != 'kind'}
        refs.append(object_ref('action', h.service.publish_action(aid, action)))
    h.service.owner.claim(0)
    cfg = {'name': 'owned serial commands', 'retention': {'policyRef': 'test'}, 'steps': [
        {'stepId': 'first', 'actionRef': refs[0], 'arguments': {'value': {'from': 'literal', 'value': 'unused'}},
         'completion': {'contract': 'process-exit'}},
        {'stepId': 'second', 'actionRef': refs[1],
         'arguments': {'value': {'from': 'step', 'stepId': 'first', 'path': 'commands'}},
         'completion': {'contract': 'process-exit'}}]}
    job = enabled(h, cfg)
    rid = launch(h, job, 'owned-chain')['runId']
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'completed', json.dumps(state['effects'])
    assert (tmp_path / 'effect.txt').read_text() == 'owned'
    effects = {effect['effectKey']['stepId']: effect for effect in state['effects']}
    assert set(effects) == {'first', 'second'} and len(state['attempts']) == 2
    assert json.loads(effects['first']['receipt']['receipt']['commands'][0]['stdout']) == {'answer': 'owned'}
    assert json.loads(effects['second']['receipt']['receipt']['commands'][0]['stdout']) == {'written': 'owned'}
    assert all(effect['outcome'] == 'succeeded' for effect in effects.values())
    before = copy.deepcopy(state)
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(job['id']) == before


@async_test
async def test_admitted_fixed_template_step_uses_original_template_revision(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = executor.execute_dynamic
    async def execute(action, *args):
        if action['id'] == 'one':
            entered.set(); await release.wait(); return {'ok': True}
        return await original(action, *args)
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    child_config = {**_config(_steps()), 'enabled': False, 'actionId': 'one', 'arguments': {'value': 'original'}}
    child_config.pop('steps')
    try:
        template = runtime.templates.put({'name': 'fixed', 'config': child_config}, 0, True)
        runtime.actions.put('actions', {**_action('two'), 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'execution': {'kind': 'job_template', 'templateId': template['id'], 'templateRevision': 1}}, authorized=True)
        steps = _steps(); steps[1]['arguments'] = {}
        parent = runtime.create(_config(steps), True)
        await runtime.launch(parent['jobId'], 1); await entered.wait()
        runtime.templates.put({'name': 'fixed edited', 'config': {**child_config, 'arguments': {'value': 'edited'}}}, 1, True, template['id'])
        release.set(); await asyncio.gather(*runtime.tasks.values())
        done = runtime.get(parent['jobId'])
        assert done['lastStatus'] == 'completed', done['lastExecution']
        child = next(j for j in runtime.list() if j['jobId'] != parent['jobId'])
        assert child['templateRevision'] == 1 and child['config']['arguments'] == {'value': 'original'}
    finally:
        release.set(); await runtime.close()


def test_recovering_run_uses_frozen_chain_when_next_config_is_unavailable(env):
    import time
    from packages.jobs.final_service import definition, object_ref
    from tests.test_job_automatic_recovery import create, job_definition, drive_until, state
    first, second = env.tmp / 'first-frozen', env.tmp / 'second-frozen'
    value = job_definition(env, steps=2)
    value['steps'][0]['arguments']['argv']['value'] = [sys.executable, '-c',
        f'from pathlib import Path; Path({str(first)!r}).open("a").write("first\\n")']
    value['steps'][1]['arguments']['argv']['value'] = [sys.executable, '-c',
        f'from pathlib import Path; p=Path({str(second)!r}); n=int(p.read_text()) if p.exists() else 0; '
        'p.write_text(str(n+1)); raise SystemExit(7 if n == 0 else 0)']
    jid, rid = create(env, value)
    asyncio.run(env.service.dispatch_once())
    deadline = time.monotonic() + 12
    while True:
        asyncio.run(env.service.recover_external_receipts())
        # Drive the next step only until the original failed receipt has
        # scheduled recovery; do not consume that pending recovery yet.
        current = env.service.final.store.get(jid)['finalRuntime']['plans'][rid]
        if current.get('autoRecovery'):
            break
        assert time.monotonic() < deadline
        if state(env, jid)['steps'][rid]['s1']['attemptCount'] == 0:
            asyncio.run(env.service.dispatch_once())
        time.sleep(.025)
    frozen = copy.deepcopy(current['frozen'])
    original_action = env.service.get('action', value['steps'][1]['actionRef']['id'], actor={'type':'user','id':'local'})
    replacement_value = definition('action', original_action)
    replacement_value['system'] = False
    replacement = env.service.publish_action('unavailable_next_config', replacement_value)
    changed = copy.deepcopy(value)
    changed['steps'][1]['actionRef'] = object_ref('action', replacement)
    response = env.client.put('/api/jobs/' + jid, json={'definition':changed,
        'expectedRevision':1, 'requestKey':'change-next-config'})
    assert response.status_code == 200, response.text
    response = env.client.post('/api/jobs/actions/' + replacement['id'] + '/operations', json={
        'operation':'disable', 'expectedRevision':replacement['revision'],
        'expectedStateRevision':replacement['stateRevision'], 'parameters':{}, 'requestKey':'disable-next-config'})
    assert response.status_code == 200, response.text
    asyncio.run(drive_until(env, jid, rid, 'completed'))
    done = state(env, jid)
    assert first.read_text().splitlines() == ['first'] and second.read_text() == '2'
    assert set(done['runs']) == {rid} and done['runs'][rid]['configRevision'] == 1
    assert [done['steps'][rid][sid]['attemptCount'] for sid in ('s0','s1')] == [1,2]
    assert len(done['effects']) == 3
    assert env.service.final.store.get(jid)['finalRuntime']['plans'][rid]['frozen'] == frozen
    assert env.service.get('job', jid, actor={'type':'user','id':'local'})['configRevision'] == 2
    asyncio.run(env.service.dispatch_once())
    assert first.read_text().splitlines() == ['first'] and second.read_text() == '2'
