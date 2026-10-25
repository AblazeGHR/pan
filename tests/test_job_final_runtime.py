"""WP02 FR01-FR05: canonical execution over the legacy JSON registry."""

import asyncio
import json

import pytest

from packages.jobs import pins
from packages.jobs.action_runtime import Conflict
from packages.jobs.final_contracts import ContractError
from tests.test_automation_core import async_test
from tests.test_job_action_chain import _config, _runtime, _steps
import packages.actions.executor as executor


async def settled(runtime):
    await asyncio.gather(*list(runtime.tasks.values()))


@async_test
async def test_fr01_retry_run_again_and_clone_have_distinct_identities(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    calls = []

    async def execute(action, args, *unused):
        calls.append((action['id'], args['value']))
        return {'ok': not (action['id'] == 'two' and len(calls) == 2)}

    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        job = runtime.create(_config(_steps(), trigger={'kind': 'manual'}), True)
        jid = job['jobId']
        await runtime.launch(jid, 1)
        await settled(runtime)
        original_run = runtime.get(jid)['runId']

        await runtime.confirm_recovery(jid, 1, failed=True)
        await settled(runtime)
        retried = runtime.final.get(jid)
        assert retried['runs'][original_run]['state'] == 'completed'
        assert retried['steps'][original_run]['first']['attemptCount'] == 1
        assert retried['steps'][original_run]['second']['attemptCount'] == 2
        assert calls == [('one', 'first'), ('two', 'second'), ('two', 'second')]

        again = await runtime.run_again(jid, 2, original_run, 'fr01-run-again')
        await settled(runtime)
        assert again['runId'] != original_run
        assert runtime.final.run(jid, again['runId'])['rerunOf'] == original_run

        cloned = await runtime.clone_and_run(jid, 2, 'fr01-clone', overrides={'name': 'cloned'})
        await settled(runtime)
        assert cloned['jobId'] != jid and cloned['runId'] not in {original_run, again['runId']}
        assert len(runtime.final.runs(jid)) == 2
        assert len(runtime.final.runs(cloned['jobId'])) == 1
        assert runtime.get(cloned['jobId'])['clonedFrom']['jobId'] == jid

        original_effects = runtime.final.get(jid)['effects']
        assert {item['effectKey']['runId'] for item in original_effects} == {original_run, again['runId']}
        second_keys = [item['effectKey'] for item in original_effects if item['effectKey']['runId'] == original_run and item['effectKey']['stepId'] == 'second']
        assert len(second_keys) == 2 and second_keys[0] == second_keys[1]
    finally:
        await runtime.close()


@async_test
async def test_fr01_parallel_retry_selects_original_run_not_last_summary(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    calls = []
    fail_run = None
    failed_once = set()

    async def execute(action, args, event, *unused):
        calls.append(action['id'])
        run_id = event['id'].split(':')[0]
        should_fail = action['id'] == 'two' and run_id == fail_run and run_id not in failed_once
        if should_fail:
            failed_once.add(run_id)
        return {'ok': not should_fail}

    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        job = runtime.create(_config(_steps(), trigger={'kind': 'manual'},
                                    overlap={'mode': 'parallel', 'parallelLimit': 2}), True)
        jid = job['jobId']
        first = await runtime.launch(jid, 1)
        fail_run = first['execution']['runId']
        second = await runtime.launch(jid, 1)
        await settled(runtime)
        first_run = first['execution']['runId']
        second_run = second['execution']['runId']
        summaries = runtime.get(jid)['runSummaries']
        failed_run = next(run_id for run_id, summary in summaries.items() if summary['status'] == 'failed')
        completed_run = next(run_id for run_id, summary in summaries.items() if summary['status'] == 'completed')
        assert {failed_run, completed_run} == {first_run, second_run}
        assert runtime.get(jid)['lastExecution']['runId'] != failed_run
        await runtime.confirm_recovery(jid, 1, failed=True, run_id=failed_run)
        await settled(runtime)
        state = runtime.final.get(jid)
        assert state['runs'][failed_run]['state'] == 'completed'
        assert state['runs'][completed_run]['state'] == 'completed'
        assert state['steps'][failed_run]['second']['attemptCount'] == 2
        assert state['steps'][completed_run]['second']['attemptCount'] == 1
    finally:
        await runtime.close()


@async_test
async def test_fr02_unknown_never_replays_and_fenced_late_receipt_is_rejected(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path, ('one', 'two'))
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(action, *unused):
        calls.append(action['id'])
        if action['id'] == 'two':
            entered.set()
            await release.wait()
            return {'ok': True}
        return {'ok': True, 'status': 'unknown'}

    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        unknown = runtime.create(_config([{'stepId': 'only', 'actionId': 'one', 'arguments': {'value': 'x'}}]), True)
        await runtime.launch(unknown['jobId'], 1)
        await settled(runtime)
        for _ in range(2):
            await runtime.tick({'enabled': True})
        state = runtime.final.get(unknown['jobId'])
        run_id = runtime.get(unknown['jobId'])['runId']
        attempt_id = next(iter(state['attempts']))
        assert state['runs'][run_id]['state'] == 'uncertain'
        assert state['effects'][0]['outcome'] == 'unknown'
        assert calls == ['one']
        with pytest.raises(Conflict, match='reconciliation'):
            await runtime.confirm_recovery(unknown['jobId'], 1)
        with pytest.raises(ContractError) as late:
            runtime.final.apply_receipt(unknown['jobId'], attempt_id, owner_epoch=1, generation=1, outcome='completed')
        assert late.value.code == 'revision_conflict'

        blocked = runtime.create(_config([{'stepId': 'only', 'actionId': 'two', 'arguments': {'value': 'y'}}]), True)
        await runtime.launch(blocked['jobId'], 1)
        await entered.wait()
        active = runtime.final.get(blocked['jobId'])
        active_attempt = next(iter(active['attempts']))
        with pytest.raises(ContractError) as fence:
            runtime.final.apply_receipt(blocked['jobId'], active_attempt, owner_epoch=2, generation=1, outcome='completed')
        assert fence.value.code == 'revision_conflict'
        release.set()
        await settled(runtime)
        assert calls == ['one', 'two']
    finally:
        release.set()
        await runtime.close()


@async_test
async def test_fr02_reconcile_settles_run_and_resumes_only_pending_steps(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    calls = []

    async def execute(action, *unused):
        calls.append(action['id'])
        return {'ok': True, 'status': 'unknown'} if action['id'] == 'one' else {'ok': True}

    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        job = runtime.create(_config(_steps(), trigger={'kind': 'manual'}), True)
        jid = job['jobId']
        await runtime.launch(jid, 1)
        await settled(runtime)
        run_id = runtime.get(jid)['runId']
        effect = runtime.final.get(jid)['effects'][0]
        runtime.final.reconcile_effect(jid, effect['effectKey'], 'succeeded', {'structuredContent': {'value': 'reconciled'}})
        reconciled = runtime.final.run(jid, run_id)
        assert reconciled['state'] == 'retry_wait'
        assert runtime.get(jid)['recoveryConfirmation']['runId'] == run_id

        await runtime.launch(jid, 1, confirmed=True)
        await settled(runtime)
        assert runtime.final.run(jid, run_id)['state'] == 'completed'
        assert calls == ['one', 'two']
        assert runtime.final.get(jid)['steps'][run_id]['first']['state'] == 'completed'
    finally:
        await runtime.close()


@async_test
async def test_fr03_event_trigger_identity_overlap_skip_and_queue_backpressure(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(action, *unused):
        calls.append(action['id'])
        return {'ok': True}

    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        parallel = _config(_steps(), trigger={'kind': 'event', 'eventTypes': ['owned.event'], 'query': {'field': 'name', 'op': 'eq', 'value': 'x'}},
                           overlap={'mode': 'parallel', 'parallelLimit': 2})
        job = runtime.create(parallel, True)
        jid = job['jobId']
        event = {'id': 'shared-event', 'type': 'owned.event', 'payload': {}}
        base = {'jobId': jid, 'configRevision': 1}
        runtime.queue_event(event, {**base, 'triggerId': 'trigger-a'})
        runtime.queue_event(event, {**base, 'triggerId': 'trigger-b'})
        runtime.queue_event(event, {**base, 'triggerId': 'trigger-a'})
        await runtime.tick({'enabled': True})
        await runtime.tick({'enabled': True})
        await settled(runtime)
        state = runtime.final.get(jid)
        assert len(state['admissions']) == 2 and len(state['runs']) == 2
        assert {item['cause']['triggerId'] for item in state['admissions'].values()} == {'trigger-a', 'trigger-b'}

        forbidden = runtime.create(_config(_steps(), trigger={'kind': 'event', 'eventTypes': ['owned.event'], 'query': {'field': 'name', 'op': 'eq', 'value': 'x'}}), True)
        other_id = forbidden['jobId']
        entered, release = asyncio.Event(), asyncio.Event()

        async def block(*unused):
            entered.set()
            await release.wait()
            return {'ok': True}

        monkeypatch.setattr(executor, 'execute_dynamic', block)
        await runtime.launch(other_id, 1)
        await entered.wait()
        runtime.queue_event({'id': 'overlap-event', 'type': 'owned.event', 'payload': {}},
                            {'jobId': other_id, 'configRevision': 1, 'triggerId': 'trigger-c'})
        overlap = runtime.final.get(other_id)
        admission = next(item for item in overlap['admissions'].values() if item['cause'].get('eventId') == 'overlap-event')
        assert admission['state'] == 'skipped' and admission['reason'] == 'overlap'
        assert len(overlap['runs']) == 1
        release.set()
        await runtime.cancel(other_id, 1)
        assert [run['state'] for run in runtime.final.runs(other_id)] == ['cancelled']

        queued = runtime.create(_config(_steps(), trigger={'kind': 'event', 'eventTypes': ['owned.event'], 'query': {'field': 'name', 'op': 'eq', 'value': 'x'}},
                                       overlap={'mode': 'queue', 'parallelLimit': 1}), True)
        full_id = queued['jobId']
        def fill(current):
            current['pendingRuns'] = [{'id': f'legacy-{index}', 'configRevision': 1} for index in range(128)]
        runtime.final.store.mutate(full_id, fill)
        with pytest.raises(Conflict, match='capacity'):
            runtime.queue_event({'id': 'full-event', 'type': 'owned.event', 'payload': {}},
                                {'jobId': full_id, 'configRevision': 1, 'triggerId': 'trigger-d'})
        rejected = runtime.final.get(full_id)['admissions']
        assert next(iter(rejected.values()))['state'] == 'rejected'
    finally:
        release.set()
        await runtime.close()


@async_test
async def test_fr04_same_operation_recovers_after_accepted_phase_crash(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)

    async def execute(*unused):
        return {'ok': True}

    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        job = runtime.create(_config(_steps(), trigger={'kind': 'manual'}), True)
        jid = job['jobId']
        await runtime.launch(jid, 1)
        await settled(runtime)
        original_run = runtime.get(jid)['runId']
        original_launch = runtime.launch
        failures = 0

        async def crash_once(*args, **kwargs):
            nonlocal failures
            failures += 1
            raise OSError('injected post-admission interruption')

        monkeypatch.setattr(runtime, 'launch', crash_once)
        with pytest.raises(OSError):
            await runtime.run_again(jid, 1, original_run, 'fr04-run-again')
        operation = runtime.final.operation_for_key(jid, 'create', 'fr04-run-again')
        assert operation['state'] == 'accepted'
        monkeypatch.setattr(runtime, 'launch', original_launch)
        recovered = await runtime.run_again(jid, 1, original_run, 'fr04-run-again')
        await settled(runtime)
        replayed = await runtime.run_again(jid, 1, original_run, 'fr04-run-again')
        assert recovered['runId'] == replayed['runId']
        assert len(runtime.final.runs(jid)) == 2
        assert runtime.final.operation(jid, operation['operationId'])['state'] == 'completed'

        monkeypatch.setattr(runtime, 'launch', crash_once)
        with pytest.raises(OSError):
            await runtime.clone_and_run(jid, 1, 'fr04-clone')
        monkeypatch.setattr(runtime, 'launch', original_launch)
        cloned = await runtime.clone_and_run(jid, 1, 'fr04-clone')
        await settled(runtime)
        assert len(runtime.final.runs(cloned['jobId'])) == 1
        assert runtime.final.operation_for_key(jid, 'clone-and-run', 'fr04-clone')['state'] == 'completed'
    finally:
        await runtime.close()


@async_test
async def test_fr04_run_again_queue_replay_preserves_operation_and_hides_plan(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def execute(action, *unused):
        calls.append(action['id'])
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return {'ok': True}

    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    try:
        job = runtime.create(_config(_steps(), trigger={'kind': 'manual'},
                                    overlap={'mode': 'queue', 'parallelLimit': 1}), True)
        jid = job['jobId']
        await runtime.launch(jid, 1)
        await entered.wait()
        original_run = runtime.get(jid)['runId']
        first = await runtime.run_again(jid, 1, original_run, 'fr04-queued-run-again')
        replay = await runtime.run_again(jid, 1, original_run, 'fr04-queued-run-again')
        assert first['runId'] == replay['runId'] != original_run
        state = runtime.final.get(jid)
        assert len(state['runs']) == 2 and len(runtime.get(jid)['pendingRuns']) == 1
        assert state['runs'][first['runId']]['rerunOf'] == original_run
        assert 'canonicalPlan' not in json.dumps(runtime.view(runtime.get(jid)))

        release.set()
        await settled(runtime)
        await runtime.tick({'enabled': True})
        await settled(runtime)
        assert len(runtime.final.runs(jid)) == 2
        assert runtime.final.operation(jid, first['operationId'])['state'] == 'completed'
    finally:
        release.set()
        await runtime.close()


@async_test
async def test_fr05_single_outbox_ack_delete_guard_and_pin_cleanup(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    allowed = False
    notices = []
    effects = []

    async def execute(*unused):
        effects.append(1)
        return {'ok': True}

    async def emit(*unused):
        return True

    async def notice(session_id, text, **kwargs):
        notices.append(kwargs['event_id'])
        return {'ok': allowed}

    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    monkeypatch.setattr('packages.core.background_jobs._scheduled_task_emit_strict', emit)
    monkeypatch.setattr('packages.core.background_jobs._worker.enqueue_notice', notice)
    config = _config([{'stepId': 'only', 'actionId': 'one', 'arguments': {'value': 'x'}}],
                     notification={'on': ['completed'], 'target': 'job', 'sessionIds': ['session-fr05']})
    try:
        job = runtime.create(config, True)
        jid = job['jobId']
        def legacy_outbox(current):
            current['terminalOutbox'] = {'legacy-pending': {'id': 'legacy-pending', 'status': 'completed'}}
        runtime.final.store.mutate(jid, legacy_outbox)
        with pytest.raises(Conflict, match='outbox'):
            runtime.delete(jid, 1)
        runtime.final.store.mutate(jid, lambda current: current.pop('terminalOutbox'))
        pins.set_pin(runtime.root, jid, True)
        await runtime.launch(jid, 1)
        await settled(runtime)
        state = runtime.final.get(jid)
        assert state['outbox'] and {item['status'] for item in state['outbox'].values()} == {'pending'}
        assert all(item['runId'] == runtime.get(jid)['runId'] for item in state['outbox'].values())
        with pytest.raises(Conflict, match='outbox'):
            runtime.delete(jid, 1)

        allowed = True
        await runtime.tick({'enabled': True})
        await runtime.tick({'enabled': True})
        assert effects == [1] and len(set(notices)) == 1
        assert {item['status'] for item in runtime.final.get(jid)['outbox'].values()} == {'acked'}
        runtime.delete(jid, 1)
        assert jid not in pins.read(runtime.root)
    finally:
        await runtime.close()


@async_test
async def test_fr05_revoked_recipient_is_suppressed_not_falsely_acked(tmp_path, monkeypatch):
    runtime = await _runtime(tmp_path)
    permitted = True
    notices = []
    runtime.authorization = lambda *unused: (permitted, 7)

    async def execute(*unused):
        nonlocal permitted
        permitted = False
        return {'ok': True}

    async def emit(*unused):
        return True

    async def notice(*args, **kwargs):
        notices.append(args[0])
        return {'ok': True}

    monkeypatch.setattr(executor, 'execute_dynamic', execute)
    monkeypatch.setattr('packages.core.background_jobs._scheduled_task_emit_strict', emit)
    monkeypatch.setattr('packages.core.background_jobs._worker.enqueue_notice', notice)
    config = _config([{'stepId': 'only', 'actionId': 'one', 'arguments': {'value': 'x'}}],
                     notification={'on': ['completed'], 'target': 'job', 'sessionIds': ['revoked-session']})
    try:
        job = runtime.create(config, True, {'type': 'agent'})
        await runtime.launch(job['jobId'], 1, authority={'type': 'agent'})
        await settled(runtime)
        await runtime.tick({'enabled': True})
        session_items = [item for item in runtime.final.get(job['jobId'])['outbox'].values() if item['channel'] == 'session']
        assert [item['status'] for item in session_items] == ['suppressed']
        assert not notices
        assert not runtime.get(job['jobId']).get('terminalOutbox')
    finally:
        await runtime.close()
