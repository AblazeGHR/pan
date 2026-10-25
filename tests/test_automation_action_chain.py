"""Existing direct Trigger admission/executor with explicit serial steps."""
import asyncio
import copy

import pytest

from packages.automation.store import Conflict
from packages.jobs import chains
from tests.test_automation_core import async_test, setup, trigger, event, settled, until
from tests.test_job_action_chain import _action, _steps


async def chain_engine(tmp_path, execute):
    engine, _, _ = await setup(tmp_path, execute)
    for aid in ('one', 'two'):
        engine.actions.put('actions', _action(aid))
    item = {**trigger(), 'actionId': 'one', 'arguments': {'source': {'from': 'literal', 'value': 'input'}}, 'steps': _steps()}
    engine.ledger.put(item, 0, True)
    return engine, item


@async_test
async def test_trigger_chain_mapping_freezes_on_event_acceptance(tmp_path):
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(action, args, ev, progress):
        calls.append((action['id'], action['name'], args['value']))
        if action['id'] == 'one':entered.set(); await release.wait()
        return {'ok': True, 'structuredContent': {'answer': args['value'] + '!'}}
    engine, item = await chain_engine(tmp_path, execute)
    item['steps'] = [
        {'stepId': 'first', 'actionId': 'one', 'arguments': {'value': {'from': 'input', 'path': 'source'}}},
        {'stepId': 'second', 'actionId': 'two', 'arguments': {'value': {'from': 'step', 'stepId': 'first', 'path': 'answer'}}},
        {'stepId': 'third', 'actionId': 'two', 'arguments': {'value': {'from': 'event', 'path': 'payload.text'}}},
    ]
    engine.ledger.put(item, 1, True)
    try:
        ev = event(); ev['payload']['text'] = 'event'
        await engine.match(ev); await entered.wait()
        engine.actions.put('actions', {**_action('two'), 'name': 'changed definition'})
        changed = copy.deepcopy(item); changed['steps'][1]['arguments']['value'] = 'changed mapping'
        engine.ledger.put(changed, 2, True)
        release.set()
        rows = await settled(engine, 1)
        assert calls == [('one', 'one', 'input'), ('two', 'two', 'input!'), ('two', 'two', 'event')]
        assert rows[0]['status'] == 'succeeded' and rows[0]['triggerRevision'] == 2
        assert [s['status'] for s in rows[0]['stepExecutions']] == ['completed'] * 3
        assert 'chainSnapshot' not in rows[0]
    finally:
        release.set(); await engine.close()


@pytest.mark.parametrize('result,status', [({'ok': False}, 'failed'), ({'status': 'unknown'}, 'uncertain'),
                                        ({'status': 'uncertain', 'ok': True}, 'uncertain')])
@async_test
async def test_trigger_chain_failure_and_unknown_stop(tmp_path, result, status):
    calls = []
    async def execute(action, *args):calls.append(action['id']); return result
    engine, _ = await chain_engine(tmp_path, execute)
    try:
        await engine.match(event()); rows = await settled(engine, 1)
        await engine.kick()
        assert calls == ['one'] and rows[0]['status'] == status
        assert rows[0]['stepExecutions'][1]['status'] == 'pending'
        if status == 'uncertain':
            with pytest.raises(Conflict, match='reconciliation'):
                engine.ledger.confirm_uncertain(rows[0]['id'], rows[0]['revision'])
    finally:
        await engine.close()


@async_test
async def test_trigger_cancel_and_restart_running_step_never_replays(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event(); calls = []
    async def execute(action, *args):
        calls.append(action['id']); entered.set(); await release.wait(); return {'ok': True}
    engine, _ = await chain_engine(tmp_path, execute)
    try:
        await engine.match(event()); await entered.wait()
        row = next(iter(engine.ledger.read()['runs'].values()))
        cancelled = engine.ledger.cancel(row['id'], row['revision'])
        assert cancelled['cancellationRequested']
        engine.chain_tasks[row['id']].cancel()
        rows = await settled(engine, 1)
        assert rows[0]['status'] == 'cancelled' and calls == ['one']
        assert rows[0]['stepExecutions'][0]['attempts'][0]['status'] == 'cancelled'
    finally:
        release.set(); await engine.close()


@async_test
async def test_trigger_boundary_recovers_same_run_without_effect_retry(tmp_path):
    calls = []
    async def execute(action, *args):calls.append(action['id']); return {'ok': True}
    engine, _ = await chain_engine(tmp_path, execute)
    try:
        # Persist an admitted Run without starting the runner, then reconstruct
        # the durable boundary as a previous process would have left it.
        original = engine.kick
        async def no_kick():pass
        engine.kick = no_kick
        await engine.match(event())
        row = next(iter(engine.ledger.read()['runs'].values()))
        claimed = engine.ledger.claim(row['id'])
        steps = claimed['stepExecutions']
        chains.start_step(steps[0]); chains.finish_step(steps[0], 'completed', result={'ok': True}, output={'value': 'done'})
        engine.ledger.save_chain(row['id'], claimed['activeAttemptId'], steps, 'boundary')
        engine.ledger.recover()
        recovered = engine.ledger.read()['runs'][row['id']]
        assert recovered['status'] == 'queued' and recovered['chainContinuation']
        engine.kick = original
        await engine.kick(); rows = await settled(engine, 1)
        assert calls == ['two'] and rows[0]['status'] == 'succeeded'
        assert len(rows[0]['attempts']) == 1
        assert [len(s['attempts']) for s in rows[0]['stepExecutions']] == [1, 1]
    finally:
        await engine.close()


@async_test
async def test_trigger_failed_retry_only_current_idempotent_step(tmp_path):
    calls = []
    async def execute(action, *args):
        calls.append(action['id'])
        return {'ok': not (action['id'] == 'two' and calls.count('two') == 1)}
    engine, item = await chain_engine(tmp_path, execute)
    engine.actions.put('actions', {**_action('two'), 'idempotent': True})
    item['retry'] = {'enabled': True, 'maxAttempts': 2}
    engine.ledger.put(item, 1, True)
    # Advance the existing retry clock after first failure instead of waiting
    # for wall time. The retry policy and total budget remain real.
    clock = engine.ledger.clock
    engine.ledger.clock = lambda: clock() + 600
    try:
        await engine.match(event())
        async def waiting():return any(r['status'] == 'retry_wait' for r in engine.ledger.read()['runs'].values())
        await until(waiting)
        engine.ledger.clock = lambda: clock() + 1200
        await engine.kick()
        async def terminal():return all(r['status'] not in {'queued', 'running', 'retry_wait'} for r in engine.ledger.read()['runs'].values())
        await until(terminal)
        rows = list(engine.ledger.read()['runs'].values())
        assert calls == ['one', 'two', 'two'] and rows[0]['status'] == 'succeeded'
        assert [len(s['attempts']) for s in rows[0]['stepExecutions']] == [1, 2]
    finally:
        await engine.close()


@async_test
async def test_trigger_running_step_restart_never_replays(tmp_path):
    calls = []
    async def execute(action, *unused):calls.append(action['id']); return {'ok': True}
    engine, _ = await chain_engine(tmp_path, execute)
    original = engine.kick
    async def no_kick(*unused):pass
    engine.kick = no_kick
    try:
        await engine.match(event())
        queued = next(iter(engine.ledger.read()['runs'].values()))
        run = engine.ledger.claim(queued['id'])
        steps = run['stepExecutions']; chains.start_step(steps[0])
        engine.ledger.save_chain(run['id'], run['activeAttemptId'], steps, 'step_running')
        engine.ledger.recover()
        stopped = engine.ledger.read()['runs'][run['id']]
        assert stopped['status'] == 'uncertain' and 'chainSnapshot' not in stopped
        assert [s['status'] for s in stopped['stepExecutions']] == ['uncertain', 'pending']
        assert stopped['stepExecutions'][0]['attempts'][0]['status'] == 'uncertain'
        engine.kick = original; await engine.kick()
        assert calls == []
    finally:
        await engine.close()


@async_test
async def test_trigger_post_effect_write_failure_blocks_chain(tmp_path, monkeypatch):
    calls = []
    async def execute(action, *unused):calls.append(action['id']); return {'ok': True}
    engine, _ = await chain_engine(tmp_path, execute)
    original = engine.ledger.save_chain
    failed = False
    def save(rid, attempt, steps, phase, eligible=False):
        nonlocal failed
        if phase == 'boundary' and not failed:
            failed = True
            raise OSError('owned one-time boundary failure')
        return original(rid, attempt, steps, phase, eligible)
    monkeypatch.setattr(engine.ledger, 'save_chain', save)
    try:
        await engine.match(event()); rows = await settled(engine, 1)
        assert calls == ['one'] and rows[0]['status'] == 'uncertain'
        assert [s['status'] for s in rows[0]['stepExecutions']] == ['uncertain', 'pending']
        assert 'chainSnapshot' not in rows[0]
    finally:
        await engine.close()


@async_test
async def test_trigger_action_revoked_between_steps(tmp_path):
    calls = []
    async def execute(action, *unused):
        calls.append(action['id'])
        engine.actions.set_enabled('two', False, engine.actions.read()['revision'])
        return {'ok': True}
    engine, _ = await chain_engine(tmp_path, execute)
    try:
        await engine.match(event()); rows = await settled(engine, 1)
        assert calls == ['one'] and rows[0]['status'] == 'cancelled'
        assert rows[0]['stepExecutions'][1]['attempts'] == []
    finally:
        await engine.close()
