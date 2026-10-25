"""Core contracts; all stores and processes are owned temporary fixtures."""
import asyncio
import copy
from functools import partial, wraps
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from packages.actions.executor import execute_dynamic
from packages.actions.store import Store
from packages.automation.api import install
from packages.automation.auth import UserAuth
from packages.automation.engine import Engine
from packages.automation.events import Bus
from packages.automation.service import Service
from packages.automation.store import Conflict, Ledger
from packages.core import worker


def async_test(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return run


def action(ids=None):
    return dict(id='pause', name='Pause selected', enabled=True, effect='write_local', input_schema={'type': 'object', 'properties': {}, 'additionalProperties': False}, execution={'kind': 'session_operation', 'operation': 'pause_everything', 'selector': {'mode': 'explicit', 'ids': ids or ['selected']}})


def trigger(tid='t', **fields):
    return dict(id=tid, name=tid, eventTypes=['workers.all_idle'], actionId='pause', arguments={}, enabled=True, **fields)


def event(eid='e'):
    return dict(id=eid, type='workers.all_idle', payload={'runningCount': 0})


async def until(predicate):
    for _ in range(500):
        if await predicate():
            return
        await asyncio.sleep(.01)
    raise AssertionError('timed out')


async def settled(engine, count):
    async def done():
        runs = (await engine.io(engine.ledger.read))['runs']
        runtime = getattr(engine, 'job_runtime', None)
        if runtime:
            await runtime.tick({'enabled': True})
            busy = any(runtime.active(j) or j.get('pendingRuns') for j in runtime.list())
        else:
            busy = False
        return len(runs) == count and not busy and all(r['status'] not in {'admitting', 'queued'} for r in runs.values())
    await until(done)
    return list((await engine.io(engine.ledger.read))['runs'].values())


async def setup(tmp_path, execute=None):
    bus = Bus()
    handle = bus.activate('core', 'workers')
    bus.register(handle, 'workers.all_idle', {'type': 'object'})
    ledger, store = Ledger(tmp_path / 'ledger'), Store(tmp_path / 'actions')
    store.put('actions', action())
    calls = []
    async def pause(sid, args):
        calls.append(sid)
        return {'ok': True}
    async def read_state(key):
        return 0
    engine = Engine(ledger, bus, store, execute or partial(execute_dynamic, pause=pause), read_state)
    from packages.jobs.action_runtime import Runtime
    from unittest.mock import patch
    runtime = Runtime(tmp_path / 'jobs', store, pause)
    await runtime.start()
    engine.job_runtime = runtime
    patched = None
    if execute:
        async def invoke(a, args, ev, progress, unused_pause):
            return await execute(a, args, {**ev, 'id':ev.get('eventId', ev['id'])}, progress)
        patched = patch('packages.actions.executor.execute_dynamic', invoke)
        patched.start()
    close_engine = engine.close
    async def close():
        await close_engine()
        await runtime.close()
        if patched:patched.stop()
    engine.close = close
    await engine.start()
    return engine, calls, handle


@async_test
async def test_running_edge_durable_pause_and_dedupe(tmp_path, monkeypatch):
    engine, calls, _ = await setup(tmp_path)
    ledger = engine.ledger
    ledger.put(trigger(), 0, True)
    svc = Service(tmp_path / 'service', worker, None)
    svc.engine, svc.ledger = engine, ledger
    engine.bus = svc.bus
    await engine.job_runtime.close()
    svc.pause = engine.job_runtime.pause
    await svc.start()
    monkeypatch.setattr(worker, 'workers', {})
    worker.set_running_observer(svc.observe)
    w = worker.Worker('own', 'source', None)
    worker.workers[w.worker_id] = w
    worker._register_worker(w)
    try:
        w.status = 'running'
        w.status = 'idle'
        rows = await settled(engine, 1)
        assert calls == ['selected']
        assert rows[0]['status'] == 'admitted'
        job = engine.job_runtime.get(rows[0]['targetJobId'])
        assert job['lastExecution']['stepExecutions'][0]['checkpoint']['targetIds'] == ['selected']
        await engine.match(rows[0]['event'])
        await settled(engine, 1)
        assert calls == ['selected']
        w.status = 'idle'
        assert len(ledger.read()['runs']) == 1
        w.status = 'running'
        w.status = 'error'
        await settled(engine, 2)
        assert calls == ['selected', 'selected']
    finally:
        worker.workers.pop('own')
        worker._unregister_worker(w)
        worker.set_running_observer(None)
        await svc.close()


@async_test
async def test_serial_cross_trigger_dynamic_and_disable(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def execute(a, args, ev, progress):
        calls.append((ev['id'], a['name']))
        if ev['id'] == 'one':
            entered.set()
            await release.wait()
        return {'ok': True}
    engine, _, _ = await setup(tmp_path, execute)
    engine.ledger.put(trigger(), 0, True)
    try:
        await engine.match(event('one'))
        await entered.wait()
        await engine.match(event('two'))
        engine.actions.put('actions', {**action(), 'name': 'Latest'})
        # Different Trigger completes while first is blocked.
        engine.ledger.put(trigger('other'), 0, True)
        await engine.match(event('three'))
        async def other_done():
            return ('three', 'Latest') in calls
        await until(other_done)
        assert ('three', 'Latest') in calls
        assert not any(e == 'two' for e, _ in calls)
        release.set()
        rows = await settled(engine, 4)
        assert [n for e, n in calls if e == 'two'] == ['Pause selected']  # Definitions freeze at Job admission.
        assert all(r['result']['admissionId'] for r in rows)
        assert all(engine.job_runtime.get(j['jobId'])['finalRuntime']['plans'] for j in engine.job_runtime.list())
    finally:
        release.set()
        await engine.close()


@async_test
async def test_disable_cancels_queued_running_continues(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(*args):
        entered.set()
        await release.wait()
        return {'ok': True}
    engine, _, _ = await setup(tmp_path, execute)
    engine.ledger.put(trigger(), 0, True)
    try:
        await engine.match(event('one'))
        await entered.wait()
        await engine.match(event('two'))
        engine.ledger.put({**trigger(), 'enabled': False}, 1, True)
        release.set()
        assert {r['status'] for r in await settled(engine, 2)} == {'admitted'}  # Pause/disable gates future admissions only.
        assert engine.job_runtime.list()[0]['runCount'] == 2
    finally:
        await engine.close()


@async_test
async def test_state_cache_mapping_errors_and_skip(tmp_path):
    engine, calls, _ = await setup(tmp_path)
    reads = []
    async def reader(key):
        reads.append(key)
        return 0
    engine.state_reader = reader
    cond = {'and': [{'field': 'count', 'op': 'eq', 'value': 0}, {'not': {'field': 'count', 'op': 'gt', 'value': 1}}]}
    engine.ledger.put(trigger(condition=cond), 0, True)
    engine.ledger.put(trigger('skip', condition={'field': 'count', 'op': 'gt', 'value': 1}), 0, True)
    engine.ledger.put({**trigger('bad'), 'arguments': {'missing': {'from': 'event', 'path': 'payload.missing'}}}, 0, True)
    try:
        await engine.match(event())
        rows = await settled(engine, 3)
        assert [r['status'] for r in rows] == ['admitted', 'skipped', 'failed']
        assert reads == ['count', 'count']
        assert calls == ['selected']
    finally:
        await engine.close()


@async_test
async def test_append_failure_no_effect_and_loop_responsive(tmp_path, monkeypatch):
    engine, calls, _ = await setup(tmp_path)
    engine.ledger.put(trigger(), 0, True)
    def fail(_):
        raise OSError('disk full injected')
    monkeypatch.setattr(engine.ledger, 'append', fail)
    try:
        await engine.match(event())
        assert not calls and not engine.ledger.read()['runs']
        assert engine.diagnostics[-1]['phase'] == 'persist'
    finally:
        await engine.close()


@async_test
async def test_recover_queued_uncertain_and_unavailable(tmp_path):
    engine, calls, handle = await setup(tmp_path)
    engine.ledger.put(trigger(), 0, True)
    base = dict(triggerId='t', triggerRevision=1, actionId='pause', event=event(), arguments={})
    engine.ledger.append(dict(base, id='running', status='running'))
    engine.ledger.append(dict(base, id='queued', event=event('next'), status='queued'))
    before = engine.ledger.read()['runs']
    await engine.bus.deactivate(handle)
    await engine.start()
    handle = engine.bus.activate('core', 'workers')
    engine.bus.register(handle, 'workers.all_idle', {'type':'object'})
    try:
        await engine.kick()
        assert engine.ledger.read()['runs'] == before
        await engine.match(event('new'))
        rows = engine.ledger.read()['runs']
        assert rows['running'] == before['running'] and rows['queued'] == before['queued']
        assert list(rows.values())[-1]['status'] == 'failed'
        assert 'handoff/reconciliation' in list(rows.values())[-1]['error']
        assert not calls and not engine.job_runtime.list()
    finally:
        await engine.close()


@async_test
async def test_bus_plugin_identity_fanout_full_and_deactivate():
    bus = Bus(capacity=1)
    plugin, other = bus.activate('plugin', 'demo'), bus.activate('plugin', 'other')
    bus.register(plugin, 'demo.tick', {'type': 'integer'})
    seen = []
    async def callback(e):
        seen.append(e)
    bus.subscribe(plugin, 'one', callback)
    bus.subscribe(other, 'two', callback)
    try:
        result = bus.publish(plugin, 'demo.tick', 1)
        assert result['accepted']
        assert bus.publish(plugin, 'demo.tick', 2)['rejected'] == ['one', 'two']
        with pytest.raises(ValueError):
            bus.publish(other, 'demo.tick', 1)
        with pytest.raises(Exception):
            bus.publish(plugin, 'demo.tick', 'wrong')
        await asyncio.gather(*(q.join() for _, q, _ in bus.subscribers.values()))
        assert len(seen) == 2
        await bus.deactivate(plugin)
        assert not bus.available('demo.tick') and 'one' not in bus.subscribers
        with pytest.raises(ValueError):
            bus.publish(plugin, 'demo.tick', 1)
    finally:
        await bus.close()


@async_test
async def test_session_targets_checkpoint_partial_and_event_selector():
    results, frozen = [], []
    async def pause(sid, args):
        return {'ok': sid != 'deleted'}
    async def progress(**kw):
        frozen.append(copy.deepcopy(kw))
    a = action()
    a['execution']['selector'] = {'mode': 'event', 'path': 'payload.ids'}
    result = await execute_dynamic(a, {}, {'payload': {'ids': ['a', 'deleted', 'a']}}, progress, pause)
    assert result['matched'] == 2 and result['failed'] == 1
    assert frozen[0] == {'targetIds': ['a', 'deleted'], 'targetResults': []}
    assert len(frozen[-1]['targetResults']) == 2
    from packages.actions.provider import execute
    with pytest.raises(ValueError):
        await execute(a, {})  # Legacy provider cannot gain Session Operation privileges.


@async_test
async def test_http_user_boundary_revision_token_expiry(tmp_path):
    svc = Service(tmp_path, worker, None)
    app = FastAPI()
    app.state.automation = svc
    install(SimpleNamespace(app=app))
    key = svc.auth.key()
    transport = httpx.ASGITransport(app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        draft = {**trigger(), 'enabled': False}
        assert (await client.post('/api/automation/triggers', json={'item': trigger(), 'expectedRevision': 0})).status_code == 401
        headers = {'Authorization': 'Bearer ' + svc.auth.login(key)}
        created = await client.post('/api/automation/triggers', json={'item': draft, 'expectedRevision': 0, 'agentId': 'agent'}, headers=headers)
        assert created.status_code == 200
        assert created.json()['item']['createdBy'] == {'type': 'user'}
        assert created.json()['item']['authorization'] == {'type': 'user'}
        login = await client.post('/api/automation/login', json={'key': key})
        assert login.json()['expiresIn'] == 43200
        headers = {'Authorization': 'Bearer ' + login.json()['token']}
        assert (await client.post('/api/automation/triggers', json={'item': trigger(), 'expectedRevision': 1}, headers=headers)).status_code == 200
        assert (await client.post('/api/automation/triggers', json={'item': draft, 'expectedRevision': 2})).status_code == 401
        assert (await client.post('/api/automation/triggers', json={'item': draft, 'expectedRevision': 1}, headers=headers)).status_code == 409
        assert (await client.delete('/api/automation/triggers/t?expectedRevision=2')).status_code == 401
        svc.auth.sessions.clear()
        assert svc.ledger.read()['triggers']['t']['enabled']
        assert (await client.post('/api/automation/triggers', json={'item': draft, 'expectedRevision': 2}, headers=headers)).status_code == 401
        assert (await client.post('/api/automation/events', json={'source': {'type': 'core'}})).status_code == 405  # read-only discovery, no public publisher
    await svc.bus.close()
    await svc.engine.close()


@async_test
async def test_shutdown_interrupted_is_uncertain(tmp_path):
    entered = asyncio.Event()
    async def execute(*args):
        entered.set()
        await asyncio.Event().wait()
    engine, _, _ = await setup(tmp_path, execute)
    engine.ledger.put(trigger(), 0, True)
    await engine.match(event())
    await entered.wait()
    await engine.close()
    row = next(iter(engine.ledger.read()['runs'].values()))
    assert row['status'] == 'admitted'
    assert engine.job_runtime.get(row['targetJobId'])['lastStatus'] == 'uncertain'

@async_test
async def test_admission_fence_partial_persist_and_slow_disk(tmp_path, monkeypatch):
    import threading
    engine, calls, _ = await setup(tmp_path)
    engine.ledger.put(trigger(), 0, True)
    engine.ledger.put(trigger('second'), 0, True)
    original = engine.ledger.append
    blocked, release = threading.Event(), threading.Event()
    def append(run):
        if run['triggerId'] == 'second':
            blocked.set()
            assert release.wait(5)
            raise OSError('second match disk failure')
        return original(run)
    monkeypatch.setattr(engine.ledger, 'append', append)
    task = asyncio.create_task(engine.match(event()))
    try:
        await until(lambda: asyncio.to_thread(blocked.is_set))
        # A management kick and other loop work remain responsive while disk waits.
        await asyncio.wait_for(engine.kick(), .5)
        await asyncio.sleep(.02)
        assert not calls
        release.set()
        await task
        rows = await settled(engine, 1)
        assert calls == ['selected'] and rows[0]['admitted']
        assert engine.diagnostics[-1]['phase'] == 'persist'
    finally:
        release.set()
        await task
        await engine.close()


@async_test
async def test_target_checkpoint_failure_stops_and_marks_uncertain(tmp_path, monkeypatch):
    engine, calls, _ = await setup(tmp_path)
    engine.actions.put('actions', action(['first', 'second']))
    engine.ledger.put(trigger(), 0, True)
    runtime = engine.job_runtime
    original = runtime.chain_save
    def update(jid, eid, steps, phase):
        if steps[0]['checkpoint'].get('targetResults'):
            raise OSError('checkpoint disk failure')
        return original(jid, eid, steps, phase)
    monkeypatch.setattr(runtime, 'chain_save', update)
    try:
        await engine.match(event())
        rows = await settled(engine, 1)
        assert calls == ['first']
        assert rows[0]['status'] == 'admitted'
        job = runtime.get(rows[0]['targetJobId'])
        assert job['lastStatus'] == 'uncertain'
        checkpoint = job['lastExecution']['stepExecutions'][0]['checkpoint']
        assert checkpoint['targetIds'] == ['first', 'second']
        assert checkpoint['targetResults'] == []
    finally:
        await engine.close()


@async_test
async def test_dynamic_action_disabled_deleted_and_contract_failure(tmp_path):
    engine, calls, _ = await setup(tmp_path)
    engine.ledger.put(trigger(), 0, True)
    try:
        engine.actions.put('actions', {**action(), 'enabled': False})
        await engine.match(event('disabled'))
        await settled(engine, 1)
        engine.actions.delete('actions', 'pause')
        await engine.match(event('deleted'))
        await settled(engine, 2)
        a = action()
        a['input_schema']['required'] = ['new']
        a['input_schema']['properties']['new'] = {'type': 'integer'}
        engine.actions.put('actions', a)
        await engine.match(event('contract-changed'))
        rows = await settled(engine, 3)
        assert all(r['status'] == 'failed' for r in rows) and not calls
    finally:
        await engine.close()


@async_test
async def test_backlog_full_visible_and_zero_targets(tmp_path):
    engine, calls, _ = await setup(tmp_path)
    engine.max_pending = 0
    engine.ledger.put(trigger(), 0, True)
    try:
        await engine.match(event())
        row = (await settled(engine, 1))[0]
        assert row['status'] == 'failed' and row['error'] == 'Trigger backlog full' and not calls
        a = action()
        a['execution']['selector']['ids'] = []
        progress_calls = []
        async def progress(**kw):
            progress_calls.append(kw)
        async def pause(*args):
            raise AssertionError('no targets')
        result = await execute_dynamic(a, {}, {}, progress, pause)
        assert result == {'ok': True, 'matched': 0, 'succeeded': 0, 'failed': 0, 'targets': []}
    finally:
        await engine.close()


def test_trigger_concurrent_revision_and_atomic_failure(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import packages.automation.store as module
    ledger = Ledger(tmp_path)
    ledger.put({**trigger(), 'enabled': False}, 0)
    def update():
        try:
            ledger.put({**trigger(), 'enabled': False}, 1)
            return 'ok'
        except Conflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: update(), range(2))) == ['conflict', 'ok']
    before = ledger.path.read_bytes()
    def fail(*args):
        raise OSError('atomic replace failure')
    monkeypatch.setattr(module, 'atomic', fail)
    with pytest.raises(OSError):
        ledger.put({**trigger(), 'enabled': False}, 2)
    assert ledger.path.read_bytes() == before


def test_auth_expiry_and_restart(tmp_path, monkeypatch):
    import packages.automation.auth as module
    auth = UserAuth(tmp_path)
    clock = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    token = auth.login(auth.key())
    assert auth.authorized('Bearer ' + token)
    assert not UserAuth(tmp_path).authorized('Bearer ' + token)
    clock[0] += 43201
    assert not auth.authorized('Bearer ' + token)

@async_test
async def test_run_cancel_api_revision_and_business_state(tmp_path):
    svc = Service(tmp_path, worker, None)
    app = FastAPI()
    app.state.automation = svc
    install(SimpleNamespace(app=app))
    svc.ledger.put(trigger(), 0, True)
    svc.ledger.append(dict(id='queued', recordType='triggerAdmission', event=event(), status='queued', triggerId='t', triggerRevision=1))
    token = svc.auth.login(svc.auth.key())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        path = '/api/automation/runs/queued/cancel'
        headers = {'Authorization': 'Bearer ' + token}
        assert (await client.post(path, json={'expectedRevision': 1})).status_code == 401
        assert (await client.post(path, json={'expectedRevision': 0}, headers=headers)).status_code == 409
        result = await client.post(path, json={'expectedRevision': 1}, headers=headers)
        assert result.json()['run']['revision'] == 2
        assert result.json()['run']['status'] == 'cancelled'
        assert (await client.post(path, json={'expectedRevision': 2}, headers=headers)).status_code == 409
    await svc.engine.close()
    await svc.bus.close()

@async_test
async def test_example_internal_plugin_runtime_lifecycle(tmp_path):
    from packages.automation.example_plugin import ExamplePlugin
    svc = Service(tmp_path, worker, None)
    plugin = ExamplePlugin(svc)
    await svc.start()
    await plugin.enable()
    handle = plugin.handle
    result = await plugin.tick(3)
    await until(lambda: asyncio.sleep(0, result=bool(plugin.received)))
    await asyncio.gather(*(q.join() for _, q, _ in svc.bus.subscribers.values()))
    assert plugin.received == [result['event']['id']]
    await plugin.disable()
    assert not svc.bus.available('example.tick')
    with pytest.raises(ValueError):
        svc.bus.publish(handle, 'example.tick', {'value': 4})
    await svc.bus.close()
    await svc.engine.close()

@async_test
async def test_redelivery_does_not_spend_backlog_capacity(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(*args):
        entered.set()
        await release.wait()
        return {'ok': True}
    engine, _, _ = await setup(tmp_path, execute)
    engine.max_pending = 2
    engine.ledger.put(trigger(), 0, True)
    try:
        await engine.match(event())
        await entered.wait()
        engine.ledger.put(trigger('new'), 0, True)
        await engine.match(event())
        rows = list(engine.ledger.read()['runs'].values())
        assert len(rows) == 2 and not any(r['status'] == 'failed' for r in rows)
        release.set()
        await settled(engine, 2)
    finally:
        release.set()
        await engine.close()


@async_test
async def test_dependency_revocation_during_durable_claim_waits(tmp_path, monkeypatch):
    engine, calls, _ = await setup(tmp_path)
    engine.ledger.put(trigger(), 0, True)
    original = engine.ledger.release_event
    def release(eid):
        engine.bus.definitions['workers.all_idle']['available'] = False
        return original(eid)
    monkeypatch.setattr(engine.ledger, 'release_event', release)
    try:
        await engine.match(event())
        row = next(iter(engine.ledger.read()['runs'].values()))
        assert row['status'] == 'queued' and not calls
        engine.bus.definitions['workers.all_idle']['available'] = True
        await engine.kick()
        assert (await settled(engine, 1))[0]['status'] == 'admitted'
        assert calls == ['selected']
    finally:
        await engine.close()
