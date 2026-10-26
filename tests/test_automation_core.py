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
from tests.test_automation_canonical import canonical, subscription, fact, admit, run, deliver, create, control, enabled, USER


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


def test_event_admits_once_and_only_job_owner_dispatches(canonical):
    h = canonical
    job, target = subscription(h)
    ev = fact(h)
    first = admit(h, ev, target)
    assert first['runId'] == admit(h, ev, target)['runId']
    assert h.provider.calls == []
    assert h.automation.ledger.read()['runs'] == {}
    assert len(h.service.final.runs(job['id'])) == 1
    asyncio.run(h.service.dispatch_once())
    assert run(h, first['runId'])['state'] == 'completed'
    assert [text for text, _ in h.provider.calls] == ['0']


@pytest.mark.parametrize('value,expected', [(0, 'accepted'), (2, 'skipped'), ('error', 'error')])
def test_state_cache_false_and_unavailable_never_execute(canonical, value, expected):
    h = canonical
    reads = []
    async def reader(key):
        reads.append(key)
        if value == 'error':
            raise OSError('State unavailable')
        return value
    h.service.state_reader = reader
    condition = {'and': [{'field':'count', 'op':'eq', 'value':0},
                         {'not':{'field':'count', 'op':'gt', 'value':1}}]}
    job, target = subscription(h, condition=condition)
    if expected == 'error':
        with pytest.raises(OSError, match='State unavailable'):
            admit(h, fact(h), target)
        assert h.service.final.runs(job['id']) == []
    else:
        result = admit(h, fact(h), target)
        assert result['status'] == expected
        assert len(h.service.final.runs(job['id'])) == (expected == 'accepted')
    assert reads == ['count']
    assert not h.provider.calls


@pytest.mark.parametrize('filters,payload,expected', [
    ({'payload.runningCount':0}, {'runningCount':0}, 'accepted'),
    ({'payload.runningCount':1}, {'runningCount':0}, 'skipped'),
    ({'payload.missing':0}, {'runningCount':0}, 'skipped'),
])
def test_event_filters_gate_canonical_admission(canonical, filters, payload, expected):
    h = canonical
    job, target = subscription(h, eventFilter=filters)
    result = admit(h, fact(h, payload=payload), target)
    assert result['status'] == expected
    assert len(h.service.final.runs(job['id'])) == (expected == 'accepted')
    assert h.provider.calls == []


@pytest.mark.parametrize('field', ['disabled', 'paused'])
def test_legacy_disabled_and_paused_remain_nonexecuting_admission_evidence(tmp_path, field):
    bus = Bus()
    handle = bus.activate('core', 'workers')
    bus.register(handle, 'workers.all_idle', {'type':'object'})
    ledger = Ledger(tmp_path / 'automation')
    item = trigger()
    item['enabled' if field == 'disabled' else 'paused'] = field == 'paused'
    ledger.put(item, 0, True)
    engine = Engine(ledger, bus, None, execute=lambda *args: pytest.fail('retired effect'))
    asyncio.run(engine.match(event()))
    row = next(iter(ledger.read()['runs'].values()))
    assert row['status'] == 'skipped' and 'disabled or paused' in row['error']
    assert not engine.tasks


@pytest.mark.parametrize('state', ['disabled', 'paused'])
def test_target_job_lifecycle_blocks_future_admission_without_cancelling_prior_run(canonical, state):
    h = canonical
    job, target = subscription(h)
    first = admit(h, fact(h, 'prior'), target)
    response = control(h, 'jobs', job, 'disable' if state == 'disabled' else 'pause')
    assert response.status_code == 200, response.text
    from packages.jobs.final_contracts import ContractError
    with pytest.raises(ContractError, match='Job is not enabled') as exc:
        admit(h, fact(h, 'future'), target)
    assert exc.value.code == 'reference_conflict'
    assert len(h.service.final.runs(job['id'])) == 1
    assert run(h, first['runId'])['state'] == 'queued'
    assert not h.provider.calls


def test_original_event_trigger_identity_survives_revision_and_retirement(canonical):
    h = canonical
    job, target = subscription(h)
    ev = fact(h)
    original = admit(h, ev, target)
    assert control(h, 'triggers', target, 'retire').status_code == 200
    replay = admit(h, ev, target)
    assert replay['replayed'] and replay['runId'] == original['runId']
    assert h.client.get('/api/operations/' + original['operationId']).status_code == 200
    assert h.client.get('/api/job-runs/' + original['runId']).json()['runId'] == original['runId']
    assert admit(h, fact(h, 'after-retire'), target)['status'] == 'skipped'
    assert len(h.service.final.runs(job['id'])) == 1


def test_trigger_lifecycle_grant_cannot_authorize_a_job_effect(canonical):
    from packages.jobs.final_contracts import ContractError
    h = canonical
    job = enabled(h)
    grants = h.automation.auth._default_permissions()
    grants['trigger'] = {key:True for key in grants['trigger']}
    h.automation.auth.change_permissions(grants, 0)
    actor = {'type':'agent', 'id':'owned-agent', 'revision':1}
    created = h.service.write('trigger', None, {'definition':{'name':'agent-trigger',
        'eventTypes':['workers.all_idle'], 'creatorSelector':{'mode':'registeredType', 'providerType':'core'},
        'targetJobId':job['id']}, 'expectedRevision':0, 'requestKey':'agent-trigger'}, actor=actor)['result']['object']
    target = h.service.control('trigger', created['id'], {'operation':'enable', 'expectedRevision':1,
        'expectedStateRevision':0, 'requestKey':'agent-enable', 'parameters':{}}, actor=actor)['result']['object']
    with pytest.raises(ContractError) as exc:
        admit(h, fact(h), target)
    assert exc.value.code == 'authorization_denied'
    assert not h.service.final.runs(job['id']) and not h.provider.calls
    disabled = control(h, 'triggers', target, 'disable').json()['result']['object']
    user_target = control(h, 'triggers', disabled, 'enable', 'user-enable').json()['result']['object']
    result = admit(h, fact(h, 'user-event'), user_target)
    asyncio.run(h.service.dispatch_once())
    assert run(h, result['runId'])['state'] == 'completed' and len(h.provider.calls) == 1


def test_delegated_execution_revision_rechecked_before_first_effect(canonical):
    h = canonical
    job, target = subscription(h)
    grants = h.automation.auth._default_permissions()
    grants['job']['execute'] = True
    grants['trigger']['lifecycle'] = True
    h.automation.auth.change_permissions(grants, 0)
    # Explicit agent lifecycle takes responsibility for this Trigger.
    disabled = control(h, 'triggers', target, 'disable').json()['result']['object']
    actor = {'type':'agent', 'id':'owned-agent', 'revision':1}
    target = h.service.control('trigger', disabled['id'], {'operation':'enable', 'expectedRevision':1,
        'expectedStateRevision':disabled['stateRevision'], 'requestKey':'agent-enable', 'parameters':{}},
        actor=actor)['result']['object']
    result = admit(h, fact(h), target)
    h.automation.auth.change_permissions(h.automation.auth._default_permissions(), 1)
    asyncio.run(h.service.dispatch_once())
    assert run(h, result['runId'])['state'] == 'failed'
    assert h.service.final.get(job['id'])['attempts'] == {} and not h.provider.calls

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
