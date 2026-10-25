"""WP03 FA01-FA03: one Event admission path, no Trigger effect host."""
import inspect

import pytest

from packages.automation import chain_engine, engine, service
from packages.automation.store import Conflict, Ledger
from tests.test_automation_core import async_test, event, trigger, setup, settled
from tests.test_automation_job_links import setup as links_setup, close as links_close
from tests.test_job_action_chain import _config, _steps


@async_test
async def test_fa01_two_triggers_same_job_have_independent_deduplicated_runs(tmp_path):
    svc, jobs, _ = await links_setup(tmp_path)
    try:
        job = jobs.create(_config(_steps(), trigger={'kind':'manual'},
            overlap={'mode':'queue', 'parallelLimit':1}), True)
        for tid in ('a', 'b'):
            svc.ledger.put(dict(id=tid, name=tid, actionId='one', enabled=True,
                eventTypes=['workers.all_idle'], targetJobId=job['jobId'], arguments={}), 0, True)
        ev = svc.bus.prepare(svc.core, 'workers.all_idle', {'runningCount':0}, 'broadcast')
        await svc.accept(ev)
        row = svc.events.pending()[0]
        assert len(row['targets']) == 2 and not any(t.get('_job') for t in row['targets'])
        await svc.deliver(row)
        await svc.deliver(row)
        await settled(svc.engine, 2)
        state = jobs.final.get(job['jobId'])
        assert len(state['admissions']) == len(state['runs']) == 2
        assert {r['cause']['triggerId'] for r in state['runs'].values()} == {'a', 'b'}
        assert all(r['state'] == 'completed' for r in state['runs'].values())
        assert len(state['effects']) == 4
        assert {o['runId'] for o in state['outbox'].values()} == set(state['runs'])
        assert len({(o['runId'], o['transitionSeq'], o['recipientId'], o['channel']) for o in state['outbox'].values()}) == len(state['outbox'])
    finally:
        await links_close(svc, jobs)


@async_test
async def test_fa01_direct_trigger_reuses_visible_managed_job_across_events_and_restart(tmp_path):
    eng, calls, _ = await setup(tmp_path)
    eng.ledger.put(trigger(), 0, True)
    try:
        for eid in ('one', 'two'):
            await eng.match(event(eid))
        await settled(eng, 2)
        jobs = eng.job_runtime.list()
        assert len(jobs) == 1 and calls == ['selected', 'selected']
        linked = eng.ledger.read()['triggers']['t']
        assert linked['targetJobId'] == jobs[0]['jobId']
        assert jobs[0]['triggerProvenance']['triggerRevision'] == 1
        assert jobs[0]['config']['steps'][0]['stepId'] == 'main'
        identity = jobs[0]['jobId']
    finally:
        await eng.close()
    eng, calls, _ = await setup(tmp_path)
    try:
        await eng.match(event('one'))
        await eng.match(event('three'))
        await settled(eng, 3)
        assert [j['jobId'] for j in eng.job_runtime.list()] == [identity]
        assert calls == ['selected']
        assert len(eng.job_runtime.final.runs(identity)) == 3
    finally:
        await eng.close()


@async_test
async def test_fa02_revision_is_provenance_not_event_dedup_identity(tmp_path):
    eng, calls, _ = await setup(tmp_path)
    eng.ledger.put(trigger(), 0, True)
    try:
        await eng.match(event())
        await settled(eng, 1)
        eng.ledger.put({**trigger(), 'name':'edited'}, 1, True)
        await eng.match(event())
        await settled(eng, 1)
        assert calls == ['selected'] and len(eng.job_runtime.list()) == 1
        await eng.match(event('next'))
        rows = await settled(eng, 2)
        assert calls == ['selected', 'selected']
        assert [r['triggerRevision'] for r in rows] == [1, 2]
        plans = eng.job_runtime.get(rows[0]['targetJobId'])['finalRuntime']['plans']
        assert {p['triggerProvenance']['triggerRevision'] for p in plans.values()} == {1, 2}
    finally:
        await eng.close()


@async_test
@pytest.mark.parametrize('observation,expected', [(0, 'admitted'), (1, 'skipped'), ('error', 'failed')])
async def test_fa02_state_cached_false_and_read_error_never_execute(tmp_path, observation, expected):
    eng, calls, _ = await setup(tmp_path)
    reads = []
    async def reader(key):
        reads.append(key)
        if observation == 'error':
            raise OSError('owned State reader fault')
        return observation
    eng.state_reader = reader
    predicate = {'field':'count', 'op':'eq', 'value':0}
    eng.ledger.put(trigger(condition={'and':[predicate, predicate]}), 0, True)
    try:
        await eng.match(event())
        row = (await settled(eng, 1))[0]
        assert row['status'] == expected and reads == ['count']
        assert calls == (['selected'] if observation == 0 else [])
        assert row['state'] == ({} if observation == 'error' else {'count':observation})
        assert len(eng.job_runtime.list()) == int(observation == 0)
    finally:
        await eng.close()


@async_test
@pytest.mark.parametrize('lifecycle', ['disabled', 'paused'])
async def test_fa03_trigger_lifecycle_only_gates_admission(tmp_path, lifecycle):
    eng, calls, _ = await setup(tmp_path)
    eng.ledger.put({**trigger(), 'enabled':lifecycle != 'disabled', 'paused':lifecycle == 'paused'}, 0, True)
    try:
        before = eng.job_runtime.list()
        await eng.match(event())
        row = (await settled(eng, 1))[0]
        assert row['status'] == 'skipped' and not calls
        assert eng.job_runtime.list() == before == []
        assert not row.get('attempts')
    finally:
        await eng.close()


@async_test
@pytest.mark.parametrize('lifecycle', ['disabled', 'paused'])
async def test_fa03_target_job_lifecycle_blocks_effects(tmp_path, lifecycle):
    eng, calls, _ = await setup(tmp_path)
    eng.ledger.put(trigger(), 0, True)
    try:
        await eng.match(event('first'))
        row = (await settled(eng, 1))[0]
        runtime = eng.job_runtime
        if lifecycle == 'disabled':
            runtime.set_enabled(row['targetJobId'], False, 1, {'type':'user'})
        else:
            runtime.set_paused(row['targetJobId'], True, 1)
        await eng.match(event('blocked'))
        assert (await settled(eng, 2))[-1]['status'] == 'skipped'
        assert calls == ['selected'] and len(runtime.final.runs(row['targetJobId'])) == 1
    finally:
        await eng.close()


@async_test
async def test_fa03_direct_chain_has_one_managed_job_and_no_automation_attempts(tmp_path):
    svc, jobs, _ = await links_setup(tmp_path)
    try:
        svc.ledger.put(dict(id='chain', name='chain', actionId='one', steps=_steps(), enabled=True,
            eventTypes=['workers.all_idle'], arguments={}), 0, True)
        await svc.engine.match(event())
        row = (await settled(svc.engine, 1))[0]
        assert row['status'] == 'admitted' and not row.get('attempts')
        assert 'steps' not in svc.ledger.read()['triggers']['chain']
        job = jobs.get(row['targetJobId'])
        assert len(jobs.list()) == 1 and job['lastStatus'] == 'completed'
        assert len(jobs.final.get(job['jobId'])['effects']) == 2
    finally:
        await links_close(svc, jobs)


@async_test
@pytest.mark.parametrize('committed', [False, True])
async def test_fa01_interrupted_intent_queries_original_identity_without_resend(tmp_path, committed):
    eng, calls, _ = await setup(tmp_path)
    eng.ledger.put(trigger(), 0, True)
    try:
        # Persist an intent and simulate interruption precisely at the cross-store boundary.
        runtime = eng.job_runtime
        eng.job_runtime = None
        await eng.match(event())
        eng.job_runtime = runtime
        row = next(iter(eng.ledger.read()['runs'].values()))
        target = await eng.admission_port.target(eng.ledger.read()['triggers']['t'], row)
        eng.ledger.update(row['id'], status='admitting', targetJobId=target['jobId'])
        if committed:
            await eng.admission_port.admit(row['event'], target)
        async def forbidden(*unused):
            raise AssertionError('Interrupted admission must not be resent')
        eng.admission_port.admit = forbidden
        await eng.start()
        await eng.match(event())
        row = (await settled(eng, 1))[0]
        assert row['status'] == ('admitted' if committed else 'uncertain')
        assert calls == (['selected'] if committed else [])
        assert len(runtime.final.runs(target['jobId'])) == int(committed)
        with pytest.raises(Conflict):
            eng.ledger.confirm_uncertain(row['id'], row['revision'])
    finally:
        await eng.close()


@async_test
async def test_fa03_embedded_event_subscription_enters_only_canonical_trigger_path(tmp_path, monkeypatch):
    svc, jobs, _ = await links_setup(tmp_path)
    try:
        job = jobs.create(_config(_steps(), trigger={'kind':'event', 'eventTypes':['workers.all_idle'],
            'query':{'field':'name', 'op':'eq', 'value':'owned'}}), True)
        def forbidden(*unused):
            raise AssertionError('Retired event scan/delivery host called')
        monkeypatch.setattr(jobs, 'event_targets', forbidden)
        monkeypatch.setattr(jobs, 'match_event', forbidden)
        ev = svc.bus.prepare(svc.core, 'workers.all_idle', {'runningCount':0}, 'embedded')
        await svc.accept(ev)
        row = svc.events.pending()[0]
        assert [t['id'] for t in row['targets']] == ['trigger:' + job['jobId']]
        await svc.deliver(row)
        await svc.deliver(row)
        await settled(svc.engine, 1)
        assert len(jobs.final.runs(job['jobId'])) == 1
        assert jobs.get(job['jobId'])['runCount'] == 1
    finally:
        await links_close(svc, jobs)


def test_fa03_retired_trigger_hosts_are_absent():
    assert not hasattr(engine.Engine, 'run_attempt') and not hasattr(engine.Engine, 'drain')
    assert not hasattr(chain_engine, 'run_chain')
    for symbol in ('claim', 'finish', 'prepare_action', 'sync_attempt', 'recover'):
        assert not hasattr(Ledger, symbol)
    source = inspect.getsource(service)
    assert 'execute_dynamic' not in source and '.event_targets(' not in source and '.match_event(' not in source
