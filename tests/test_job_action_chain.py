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


def test_chain_mapping_order_and_step_audit(tmp_path, monkeypatch):
    async def run():
        runtime = await _runtime(tmp_path)
        calls = []

        async def execute(action, args, event, progress, pause):
            calls.append((action['id'], args['value']))
            return {'ok': True, 'structuredContent': {'value': args['value'] + '!'}}

        monkeypatch.setattr(executor, 'execute_dynamic', execute)
        try:
            job = runtime.create(_config([
                {'stepId': 'first', 'actionId': 'one', 'arguments': {'value': {'from': 'input', 'path': 'input'}}},
                {'stepId': 'second', 'actionId': 'two', 'arguments': {'value': {'from': 'step', 'stepId': 'first', 'path': 'value'}}},
            ]), True)
            await runtime.launch(job['jobId'], 1)
            await asyncio.gather(*runtime.tasks.values())
            current = runtime.get(job['jobId'])
            assert calls == [('one', 'seed'), ('two', 'seed!')]
            assert current['status'] == 'completed'
            assert [s['status'] for s in current['lastExecution']['stepExecutions']] == ['completed', 'completed']
            assert all(s['attempts'] for s in current['lastExecution']['stepExecutions'])
        finally:
            await runtime.close()

    asyncio.run(run())


def _steps():
    return [{'stepId': 'first', 'actionId': 'one', 'arguments': {'value': 'first'}},
            {'stepId': 'second', 'actionId': 'two', 'arguments': {'value': 'second'}}]


@async_test
async def test_legacy_single_step_equivalent_and_no_legacy_rewrite(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    calls = []
    async def execute(action, args, event, progress, pause):
        calls.append((action['id'], args)); return {'ok': True, 'structuredContent': {'accepted': True}}
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        legacy = _config(_steps(), arguments={'value': 'same'})
        legacy.pop('steps')
        chain = _config([{'stepId': 'fixed', 'actionId': 'one', 'arguments': {'value': 'same'}}])
        jobs = [runtime.create(cfg, True) for cfg in (legacy, chain)]
        for job in jobs:
            await runtime.launch(job['jobId'], 1)
        await asyncio.gather(*runtime.tasks.values())
        current = [runtime.get(j['jobId']) for j in jobs]
        assert calls == [('one', {'value': 'same'})] * 2
        assert [j['status'] for j in current] == ['completed'] * 2
        assert 'steps' not in current[0]['config'] and 'stepExecutions' not in current[0]['lastExecution']
        assert [n['status'] for n in current[0]['terminalOutbox'].values()] == [n['status'] for n in current[1]['terminalOutbox'].values()]
    finally:
        await runtime.close()


@async_test
async def test_chain_is_serial_and_cancel_stops_next_step(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def execute(action, *args):
        calls.append(action['id']); entered.set(); await release.wait(); return {'ok': True}
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        job = runtime.create(_config(_steps()), True)
        await runtime.launch(job['jobId'], 1)
        await asyncio.wait_for(entered.wait(), 2)
        assert calls == ['one']
        done = await runtime.cancel(job['jobId'], 1)
        assert done['status'] == 'cancelled' and calls == ['one']
        steps = done['lastExecution']['stepExecutions']
        assert [s['status'] for s in steps] == ['cancelled', 'pending']
        assert steps[0]['attempts'][0]['status'] == 'cancelled' and not steps[1]['attempts']
    finally:
        release.set(); await runtime.close()


@pytest.mark.parametrize('status', ['unknown', 'uncertain', 'unconfirmed'])
@async_test
async def test_unknown_step_blocks_and_never_replays(tmp_path, monkeypatch, status):
    runtime = await _runtime(tmp_path); calls = []
    async def execute(action, *args):
        calls.append(action['id']); return {'ok': True, 'status': status}
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        job = runtime.create(_config(_steps()), True)
        await runtime.launch(job['jobId'], 1); await asyncio.gather(*runtime.tasks.values())
        for _ in range(2):await runtime.tick({'enabled': True})
        current = runtime.get(job['jobId'])
        assert current['status'] == 'uncertain' and calls == ['one']
        assert [s['status'] for s in current['lastExecution']['stepExecutions']] == ['uncertain', 'pending']
        with pytest.raises(Conflict, match='reconciliation'):
            await runtime.confirm_recovery(job['jobId'], 1)
    finally:
        await runtime.close()


@async_test
async def test_admission_freezes_all_steps_inputs_and_definitions(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event(); calls = []
    async def execute(action, args, event, progress, pause):
        calls.append((action['id'], action['name'], args['value']))
        if action['id'] == 'one':entered.set(); await release.wait()
        return {'ok': True}
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        steps = _steps(); steps[1]['arguments']['value'] = {'from': 'input', 'path': 'input'}
        cfg = _config(steps)
        job = runtime.create(cfg, True); await runtime.launch(job['jobId'], 1); await entered.wait()
        # Neither a changed later definition nor a changed Job chain/input is
        # allowed into the admitted Run, even before step two has started.
        runtime.actions.put('actions', {**_action('two'), 'name': 'new definition'})
        changed = _config(_steps(), arguments={'input': 'changed'})
        runtime.update(job['jobId'], changed, 1, True)
        release.set(); await asyncio.gather(*runtime.tasks.values())
        current = runtime.get(job['jobId'])
        assert calls == [('one', 'one', 'first'), ('two', 'two', 'seed')]
        assert current['configRevision'] == 2 and current['lastExecution']['configRevision'] == 1
        assert current['lastExecution']['stepExecutions'][1]['actionRevision'] == 1
        assert not runtime.actions.read().get('jobExecutions')
        public = runtime.view(current)
        assert 'chainSnapshot' not in json.dumps(public) and 'chainRecovery' not in json.dumps(public)
    finally:
        release.set(); await runtime.close()


@async_test
async def test_event_and_input_and_output_mapping(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path); calls = []
    async def execute(action, args, event, progress, pause):
        calls.append(args['value']); return {'ok': True, 'structuredContent': {'value': args['value'] + '!'}}
    async def resolve(_):return {'ids': ['target'], 'candidateCount': 1}
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    monkeypatch.setattr('packages.actions.session_query.resolve', resolve)
    try:
        steps = [*_steps(), {'stepId': 'third', 'actionId': 'two', 'arguments': {'value': {'from': 'step', 'stepId': 'second', 'path': 'value'}}}]
        steps[0]['arguments']['value'] = {'from': 'input', 'path': 'input'}
        steps[1]['arguments']['value'] = {'from': 'event', 'path': 'payload.text'}
        cfg = _config(steps, trigger={'kind': 'event', 'eventTypes': ['owned.event'], 'query': {'field': 'name', 'op': 'eq', 'value': 'target'}})
        job = runtime.create(cfg, True)
        runtime.queue_event({'id': 'e', 'type': 'owned.event', 'payload': {'text': 'event'}}, runtime.event_targets('owned.event')[0])
        await runtime.tick({'enabled': True}); await asyncio.gather(*runtime.tasks.values())
        current = runtime.get(job['jobId'])
        assert calls == ['seed', 'event', 'event!']
        assert current['eventReceipts']['e']['status'] == 'completed'
        assert len(current['lastExecution']['stepExecutions']) == 3
    finally:
        await runtime.close()


@pytest.mark.parametrize('binding', [
    {'from': 'input', 'path': 'missing'}, {'from': 'input', 'path': ''},
])
@async_test
async def test_mapping_missing_or_type_error_fails_before_effect(tmp_path, monkeypatch, binding):
    runtime = await _runtime(tmp_path); calls = []
    async def execute(*args):calls.append(1); return {'ok': True}
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        steps = _steps(); steps[0]['arguments']['value'] = binding
        job = runtime.create(_config(steps), True)
        await runtime.launch(job['jobId'], 1); await asyncio.gather(*runtime.tasks.values())
        done = runtime.get(job['jobId'])
        assert done['status'] == 'failed' and not calls
        assert done['lastExecution']['stepExecutions'][0]['error']
        assert not done['lastExecution']['stepExecutions'][0]['attempts']
    finally:
        await runtime.close()


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
@async_test
async def test_invalid_chain_configuration_rejected(tmp_path, mutation):
    runtime = await _runtime(tmp_path)
    try:
        steps = _steps()
        if mutation == 'duplicate':steps[1]['stepId'] = 'first'
        if mutation == 'forward':steps[0]['arguments']['value'] = {'from': 'step', 'stepId': 'second', 'path': 'value'}
        if mutation == 'extra':steps[0]['arguments']['undeclared'] = 'value'
        if mutation == 'bad_literal':steps[0]['arguments']['value'] = 5
        with pytest.raises(ValueError):config_value(_config(steps), runtime.actions.read())
    finally:
        await runtime.close()


@async_test
async def test_parallel_runs_have_separate_step_attempts(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path); calls = []
    async def execute(action, args, event, progress, pause):
        await asyncio.sleep(.01)
        calls.append((progress.checkpoint['id'], action['id'])); return {'ok': True}
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        job = runtime.create(_config(_steps(), overlap={'mode': 'parallel', 'parallelLimit': 2}), True)
        await runtime.launch(job['jobId'], 1); await runtime.launch(job['jobId'], 1)
        await asyncio.gather(*runtime.tasks.values())
        from packages.core import background_jobs as jobs
        records = jobs.list_run_records(registry_root=runtime.root)
        assert len(records) == 2 and len({r['result']['runId'] for r in records}) == 2
        assert len({s['id'] for r in records for s in r['result']['stepExecutions']}) == 4
        assert all([s['status'] for s in r['result']['stepExecutions']] == ['completed'] * 2 for r in records)
        assert len(calls) == 4
    finally:
        await runtime.close()


@async_test
async def test_restart_boundary_reuses_original_run_and_never_reruns_completed(tmp_path):
    runtime = await _runtime(tmp_path)
    job = runtime.create(_config(_steps()), True)
    await runtime.close()
    # Owned child process writes an actual completed step and immediately exits
    # without runtime finalization. No HTTP server or production data involved.
    script = r'''
import asyncio,sys
from pathlib import Path
from packages.actions.store import Store
from packages.jobs.action_runtime import Runtime
from packages.jobs import chains
root,jid=Path(sys.argv[1]),sys.argv[2]
async def main():
    rt=Runtime(root/'jobs',Store(root/'actions'),None)
    await rt.start()
    claimed=rt.claim(jid,'jexec_owned_crash',1)
    steps=claimed['execution']['stepExecutions']
    chains.start_step(steps[0])
    rt.chain_save(jid,'jexec_owned_crash',steps,'step_running')
    with (root/'effects.txt').open('a') as f:f.write('one\n')
    chains.finish_step(steps[0],'completed',result={'ok':True},output={'value':'one'})
    rt.chain_save(jid,'jexec_owned_crash',steps,'boundary')
    import os;os._exit(23)
asyncio.run(main())
'''
    result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script, str(tmp_path), job['jobId']],
                                     cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True, timeout=20)
    assert result.returncode == 23, result.stderr
    store = Store(tmp_path / 'actions')
    calls = []
    async def pause(sid, args):
        calls.append(sid)
        with (tmp_path / 'effects.txt').open('a') as f:f.write('two\n')
        return {'ok': True}
    fresh = Runtime(tmp_path / 'jobs', store, pause)
    try:
        await fresh.start()
        recovering = fresh.get(job['jobId']); run_id = recovering['runId']
        assert recovering['status'] == 'restarting'
        await fresh.tick({'enabled': True}); await asyncio.gather(*fresh.tasks.values())
        done = fresh.get(job['jobId'])
        assert done['status'] == 'completed' and done['runId'] == run_id
        assert (tmp_path / 'effects.txt').read_text().splitlines() == ['one', 'two']
        steps = done['lastExecution']['stepExecutions']
        assert [len(s['attempts']) for s in steps] == [1, 1]
    finally:
        await fresh.close()


def test_chain_failure_and_uncertain_block_following_step(tmp_path, monkeypatch):
    async def run():
        runtime = await _runtime(tmp_path)
        calls = []

        async def execute(action, args, event, progress, pause):
            calls.append(action['id'])
            return {'ok': False, 'status': 'failed'} if action['id'] == 'one' else {'ok': True}

        monkeypatch.setattr(executor, 'execute_dynamic', execute)
        try:
            job = runtime.create(_config([
                {'stepId': 'first', 'actionId': 'one', 'arguments': {'value': 'literal'}},
                {'stepId': 'second', 'actionId': 'two', 'arguments': {'value': 'literal'}},
            ]), True)
            await runtime.launch(job['jobId'], 1)
            await asyncio.gather(*runtime.tasks.values())
            current = runtime.get(job['jobId'])
            assert calls == ['one']
            assert current['status'] == 'failed'
            assert current['lastExecution']['stepExecutions'][1]['status'] == 'pending'
        finally:
            await runtime.close()

    asyncio.run(run())


def test_chain_monitoring_recovery_reuses_completed_step(tmp_path, monkeypatch):
    async def run():
        runtime = await _runtime(tmp_path)
        # Replace the fixtures with monitoring-capable definitions before the Job
        # is admitted; the action IDs and config remain stable.
        runtime.actions.put('actions', _action('one', monitoring=True), expected=runtime.actions.read()['revision'])
        runtime.actions.put('actions', _action('two', monitoring=True), expected=runtime.actions.read()['revision'])
        calls = []

        async def execute(action, args, event, progress, pause):
            calls.append(action['id'])
            if action['id'] == 'two' and calls.count('two') == 1:
                return {'ok': True, 'processTreeEnded': False, 'panJobCompletion': {'type': 'done', 'completed': True}}
            return {'ok': True, 'processTreeEnded': True, 'panJobCompletion': {'type': 'done', 'completed': True}}

        monkeypatch.setattr(executor, 'execute_dynamic', execute)
        cfg = _config([
            {'stepId': 'first', 'actionId': 'one', 'arguments': {'value': 'literal'}},
            {'stepId': 'second', 'actionId': 'two', 'arguments': {'value': 'literal'}},
        ], recovery={'enabled': True, 'monitoring': True, 'maxAttempts': 2,
                     'backoffSec': [0, 0, 0], 'stableResetSec': 300},
        completion={'kind': 'monitoring', 'signalType': 'done'})
        try:
            job = runtime.create(cfg, True)
            await runtime.launch(job['jobId'], 1)
            await asyncio.gather(*runtime.tasks.values())
            assert runtime.get(job['jobId'])['status'] == 'restarting'
            await runtime.tick({'enabled': True})
            await asyncio.gather(*runtime.tasks.values())
            current = runtime.get(job['jobId'])
            assert calls == ['one', 'two', 'two']
            assert current['status'] == 'completed'
            assert current['lastExecution']['stepExecutions'][0]['attempts'][0]['status'] == 'completed'
        finally:
            await runtime.close()

    asyncio.run(run())


@async_test
async def test_known_failure_explicit_retry_preserves_completed_and_frozen_steps(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    calls = []
    async def execute(action, args, *unused):
        calls.append((action['id'], args['value']))
        return {'ok': not (action['id'] == 'two' and len(calls) == 2)}
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        job = runtime.create(_config(_steps()), True)
        await runtime.launch(job['jobId'], 1); await asyncio.gather(*runtime.tasks.values())
        assert runtime.get(job['jobId'])['lastStatus'] == 'failed'
        changed = _steps(); changed[1]['arguments']['value'] = 'edited'
        runtime.update(job['jobId'], _config(changed), 1, True)
        await runtime.confirm_recovery(job['jobId'], 2, failed=True)
        await asyncio.gather(*runtime.tasks.values())
        done = runtime.get(job['jobId'])
        assert calls == [('one', 'first'), ('two', 'second'), ('two', 'second')]
        assert done['lastStatus'] == 'completed' and 'chainRecovery' not in done
        assert [len(s['attempts']) for s in done['lastExecution']['stepExecutions']] == [1, 2]
    finally:
        await runtime.close()


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


@async_test
async def test_running_step_restart_is_uncertain_and_never_replayed(tmp_path):
    runtime = await _runtime(tmp_path)
    try:
        job = runtime.create(_config(_steps()), True)
        claimed = runtime.claim(job['jobId'], 'owned_interrupted', 1)
        steps = claimed['execution']['stepExecutions']; chains.start_step(steps[0])
        runtime.chain_save(job['jobId'], 'owned_interrupted', steps, 'step_running')
        runtime.recover()
        current = runtime.get(job['jobId'])
        assert current['lastStatus'] == 'uncertain'
        assert [s['status'] for s in current['lastExecution']['stepExecutions']] == ['uncertain', 'pending']
        assert current['lastExecution']['stepExecutions'][0]['attempts'][0]['status'] == 'uncertain'
        await runtime.tick({'enabled': True})
        assert not runtime.tasks and not runtime.actions.read().get('jobExecutions')
    finally:
        await runtime.close()


@async_test
@pytest.mark.parametrize('revoke', ['action', 'authorization'])
async def test_chain_checks_revocation_between_steps(tmp_path, monkeypatch, revoke):
    runtime = await _runtime(tmp_path)
    calls = []
    permitted = True
    runtime.authorization = lambda *unused: (permitted, 7)
    async def execute(action, *unused):
        nonlocal permitted
        calls.append(action['id'])
        if revoke == 'action':runtime.actions.set_enabled('two', False, runtime.actions.read()['revision'])
        else:permitted = False
        return {'ok': True}
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        job = runtime.create(_config(_steps()), True, {'type': 'agent'})
        await runtime.launch(job['jobId'], 1); await asyncio.gather(*runtime.tasks.values())
        current = runtime.get(job['jobId'])
        assert calls == ['one'] and current['lastStatus'] == 'cancelled'
        assert current['lastExecution']['stepExecutions'][1]['attempts'] == []
    finally:
        await runtime.close()




def test_public_projection_preserves_legacy_result_field_names():
    from packages.core import background_jobs as jobs
    original = {'jobId': 'legacy', 'kind': 'scheduled-task', 'status': 'completed',
                'lastDelivery': {'chainSnapshot': 'user result', 'chainRecovery': {'data': 'user'}}}
    assert jobs.job_public_view(original)['lastDelivery'] == original['lastDelivery']




@async_test
async def test_real_owned_commands_pass_structured_output_without_server(tmp_path):
    runtime = await _runtime(tmp_path)
    try:
        for aid, code in (
            ('one', 'import json;print(json.dumps(dict(answer="owned")))'),
            ('two', 'import json,sys;from pathlib import Path;Path("effect.txt").write_text(sys.argv[1]);print(json.dumps(dict(written=sys.argv[1])))'),
        ):
            runtime.actions.put('actions', {**_action(aid), 'output_format': 'json',
                'execution': {'kind': 'command', 'executable': sys.executable,
                              'args': ['-c', code, '{value}'], 'cwd': str(tmp_path)}})
        steps = _steps(); steps[1]['arguments']['value'] = {'from': 'step', 'stepId': 'first', 'path': 'answer'}
        job = runtime.create(_config(steps), True)
        await runtime.launch(job['jobId'], 1); await asyncio.gather(*runtime.tasks.values())
        done = runtime.get(job['jobId'])
        assert done['lastStatus'] == 'completed', done['lastExecution']
        assert (tmp_path / 'effect.txt').read_text() == 'owned'
        assert done['lastExecution']['stepExecutions'][1]['output'] == {'written': 'owned'}
    finally:
        await runtime.close()


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


@async_test
async def test_recovering_run_uses_frozen_chain_when_next_config_is_unavailable(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path, ('one', 'two', 'three'))
    calls = []
    for aid in ('one', 'two', 'three'):runtime.actions.put('actions', _action(aid, monitoring=True))
    async def execute(action, *unused):
        calls.append(action['id'])
        return {'ok': True, 'processTreeEnded': not (action['id'] == 'two' and calls.count('two') == 1),
                'panJobCompletion': {'type': 'done', 'completed': True}}
    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    cfg = _config(_steps(), recovery={'enabled': True, 'monitoring': True, 'maxAttempts': 3,
        'backoffSec': [0, 0, 0], 'stableResetSec': 300}, completion={'kind': 'monitoring', 'signalType': 'done'})
    try:
        job = runtime.create(cfg, True)
        await runtime.launch(job['jobId'], 1); await asyncio.gather(*runtime.tasks.values())
        assert runtime.get(job['jobId'])['status'] == 'restarting'
        changed = copy.deepcopy(cfg); changed['steps'][1]['actionId'] = 'three'
        runtime.update(job['jobId'], changed, 1, True)
        # A new reference set first requires explicit repair, as for single Jobs.
        runtime.update(job['jobId'], changed, 2, True)
        runtime.actions.set_enabled('three', False, runtime.actions.read()['revision'])
        await runtime.tick({'enabled': True}); await asyncio.gather(*runtime.tasks.values())
        done = runtime.get(job['jobId'])
        assert done['lastStatus'] == 'completed' and calls == ['one', 'two', 'two']
        assert done['lastExecution']['configRevision'] == 1
    finally:
        await runtime.close()
