"""Six regression barriers against the integrated product; no real profile/provider."""
import asyncio
import copy
import json
import shutil
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from packages.automation.api import install
from packages.automation.service import Service
from packages.automation.event_store import RETENTION_SECONDS
from packages.plugins.event_adapter import EventConnection
from tests.test_automation_core import action, trigger, event, setup, async_test
from tests.test_event_store import fact


async def wait(signal):
    await asyncio.wait_for(signal.wait(), 3)


def local_service(root):
    # Deliberately only the Event/Engine layer. Real lifespan is covered by HTTP.
    svc = Service(root, SimpleNamespace(workers={}), None)
    svc.events.initialize()
    svc.started = True
    return svc


@async_test
@pytest.mark.parametrize('stage', ['action_read', 'target_checkpoint'])
async def test_revoke_during_action_preparation_defers_same_attempt(tmp_path, monkeypatch, stage):
    engine, calls, handle = await setup(tmp_path)
    engine.ledger.put(trigger(), 0, True)
    entered, release, retired = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = engine.io
    blocked = False
    async def io(fn, *args, **kw):
        nonlocal blocked
        value = await original(fn, *args, **kw)
        if not blocked and ((stage == 'action_read' and fn == engine.actions.read) or
                            (stage == 'target_checkpoint' and fn == engine.ledger.update and 'targetIds' in kw)):
            blocked = True
            entered.set()
            await release.wait()
        return value
    monkeypatch.setattr(engine, 'io', io)
    try:
        await engine.match(event())
        await wait(entered)
        owner = engine.tasks['t']
        owner.add_done_callback(lambda _: retired.set())
        await engine.bus.deactivate(handle)
        release.set()
        await wait(retired)
        row = next(iter(engine.ledger.read()['runs'].values()))
        assert calls == []
        assert row['status'] == 'queued' and row['attemptCount'] == 1
        attempt_id = row['activeAttemptId']
        fresh = engine.bus.activate('core', 'workers')
        engine.bus.register(fresh, 'workers.all_idle', {'type': 'object'})
        done = asyncio.Event()
        finish = engine.ledger.finish
        def finished(*args, **kw):
            result = finish(*args, **kw)
            asyncio_loop.call_soon_threadsafe(done.set)
            return result
        asyncio_loop = asyncio.get_running_loop()
        monkeypatch.setattr(engine.ledger, 'finish', finished)
        await engine.kick()
        await wait(done)
        row = next(iter(engine.ledger.read()['runs'].values()))
        assert calls == ['selected'] and row['status'] == 'succeeded'
        assert row['activeAttemptId'] == attempt_id and row['attemptCount'] == 1
    finally:
        release.set()
        await engine.close()


@async_test
async def test_publication_does_not_block_same_peer_delivery_ack(tmp_path):
    blocked, release = asyncio.Event(), asyncio.Event()
    async def publish(*args):
        blocked.set()
        await release.wait()
        return {'persisted': True}
    async def eligible(c):
        return True
    diagnostics = []
    manager = SimpleNamespace(service=SimpleNamespace(bus=SimpleNamespace(publish_async=publish)),
        eligible=eligible, stopping=False, diagnostic=lambda *x: diagnostics.append(x))
    c = EventConnection(manager, SimpleNamespace(id='dual', events={'subscriptions':['x']}), None, {}, 'r')
    c.handle, c.ready = object(), True
    stream = asyncio.StreamReader()
    c.process = SimpleNamespace(stdout=stream)
    writes = []
    sent = asyncio.Event()
    async def write(message):
        writes.append(message)
        sent.set()
    c.write = write
    # Avoid OS cleanup: this test owns only in-memory transport tasks.
    c.retire = lambda **kw: c.invalidate()
    reader = asyncio.create_task(c.read())
    delivery = asyncio.create_task(c.deliver({'type':'x','id':'delivery'}))
    try:
        await wait(sent)
        rid = writes[0]['id']
        stream.feed_data((json.dumps({'jsonrpc':'2.0','id':'pub','method':'events.publish',
            'params':{'generation':c.generation,'type':'dual.x','payload':{}}})+'\n').encode())
        await wait(blocked)
        stream.feed_data((json.dumps({'jsonrpc':'2.0','id':rid,'result':{'accepted':True}})+'\n').encode())
        # This timeout is a watchdog, not a scheduling sleep. The release remains held.
        await asyncio.wait_for(asyncio.shield(delivery), .5)
        assert c.ready and not release.is_set()
    finally:
        release.set()
        reader.cancel()
        await asyncio.gather(reader, delivery, return_exceptions=True)
        publications = getattr(c, 'publisher', None)
        if publications:
            publications.cancel()
            await asyncio.gather(publications, return_exceptions=True)


@async_test
async def test_publication_queue_is_bounded_and_cancel_settles_commit(tmp_path, monkeypatch):
    svc = local_service(tmp_path)
    handle = svc.bus.activate('plugin', 'dual')
    svc.bus.register(handle, 'dual.x', {'type':'object'})
    entered, release = threading.Event(), threading.Event()
    append = svc.events.append
    def held(*args):
        entered.set()
        assert release.wait(5)
        return append(*args)
    monkeypatch.setattr(svc.events, 'append', held)
    async def eligible(c):
        return True
    diagnostics, writes = [], []
    full = asyncio.Event()
    manager = SimpleNamespace(service=svc, eligible=eligible, stopping=False,
        diagnostic=lambda *x: diagnostics.append(x))
    c = EventConnection(manager, SimpleNamespace(id='dual'), None, {}, 'r')
    c.handle, c.ready = handle, True
    stream = asyncio.StreamReader()
    c.process = SimpleNamespace(stdout=stream)
    async def write(message):
        writes.append(message)
        if 'queue full' in message.get('error',{}).get('message',''):
            full.set()
    c.write, c.retire = write, lambda **kw: c.invalidate()
    def frame(n):
        return (json.dumps({'jsonrpc':'2.0','id':n,'method':'events.publish','params':{
            'generation':c.generation,'type':'dual.x','payload':{},'event_id':str(n)}})+'\n').encode()
    reader = asyncio.create_task(c.read())
    try:
        stream.feed_data(frame(0))
        assert await asyncio.to_thread(entered.wait, 2)
        for i in range(1,130):
            stream.feed_data(frame(i))
        await wait(full)
        assert c.publications.qsize() == 128 and len(writes) == 1
        assert diagnostics and 'queue full' in diagnostics[-1][1]
        c.publisher.cancel()
        # Observe cancellation delivered while the disk writer is still held.
        await asyncio.sleep(0)
        assert not c.publisher.done()
        release.set()
        await asyncio.gather(c.publisher, return_exceptions=True)
        assert svc.events.page()['events'][0]['event']['id'] == '0'
        assert len(svc.events.page()['events']) == 1
    finally:
        release.set()
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        if c.publisher:
            c.publisher.cancel()
            await asyncio.gather(c.publisher, return_exceptions=True)


@async_test
@pytest.mark.parametrize('operation', ['read', 'claim'])
async def test_one_scheduler_io_failure_recovers_without_external_kick(tmp_path, monkeypatch, operation):
    completed, failed = asyncio.Event(), asyncio.Event()
    async def execute(*args):
        completed.set()
        return {'ok':True}
    engine, _, _ = await setup(tmp_path, execute)
    engine.ledger.put(trigger(), 0, True)
    # Admission happened already. Only scheduling is faulted, not matching.
    engine.ledger.append(dict(id='r', triggerId='t', triggerRevision=1, actionId='pause',
        event=event(), status='queued', admitted=True, arguments={}))
    original = engine.io
    injected = False
    async def io(fn, *args, **kw):
        nonlocal injected
        if not injected and asyncio.current_task() is engine.tasks.get('t') and fn == getattr(engine.ledger, operation):
            injected = True
            failed.set()
            raise OSError('one-shot scheduler disk fault')
        return await original(fn, *args, **kw)
    monkeypatch.setattr(engine, 'io', io)
    try:
        await engine.kick()
        await wait(failed)
        await wait(completed)
        assert injected
        assert next(iter(engine.ledger.read()['runs'].values()))['attemptCount'] == 1
    finally:
        await engine.close()


@async_test
async def test_maintenance_and_readmission_preserve_current_event_dedup(tmp_path, monkeypatch):
    svc = local_service(tmp_path)
    svc.ledger.put(trigger(), 0, True)
    clock = [datetime.now(timezone.utc).timestamp() - RETENTION_SECONDS - 10]
    svc.events.clock = lambda: clock[0]
    e = fact()
    svc.events.append(e, [svc.ledger.read()['triggers']['t']])
    svc.events.delivery(e['id'], done=True)
    svc.ledger.append(dict(id='old', triggerId='t', triggerRevision=1, actionId='pause',
        event=e, status='succeeded', finishedAt=datetime.fromtimestamp(clock[0],timezone.utc).isoformat()))
    clock[0] += RETENTION_SECONDS + 10
    entered, release, admission_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = svc.engine.io
    async def io(fn, *args, **kw):
        value = await original(fn, *args, **kw)
        if fn == svc.events.maintenance:
            entered.set()
            await release.wait()
        return value
    monkeypatch.setattr(svc.engine, 'io', io)
    maintenance = asyncio.create_task(svc.maintenance())
    await wait(entered)
    maintenance_owns_admission = svc.admission.locked()
    async def admit():
        admission_entered.set()
        return await svc.accept(e, svc.core)
    publication = asyncio.create_task(admit())
    try:
        await wait(admission_entered)
        # Yield to the publication's first IO boundary; no wall-clock race.
        # Fixed product holds admission across prune; baseline does not.
        if not maintenance_owns_admission:
            await publication
            svc.engine.closed = True
            await svc.engine.match(e, targets=svc.events.lookup(e['id'])['targets'], strict=True)
            svc.events.delivery(e['id'], done=True)
        release.set()
        await maintenance
        await publication
        if svc.events.lookup(e['id'])['status'] == 'delivered':
            assert 'old' in svc.ledger.read()['runs'], 'retained Event lost its matched dedup record'
        svc.engine.closed = True  # matching only, no business Action in this store test
        await svc.engine.match(e, targets=svc.events.lookup(e['id'])['targets'], strict=True)
        assert len(svc.ledger.read()['runs']) == 1
        before = copy.deepcopy(svc.ledger.read()['runs'])
        await svc.replay([e['id']])
        await svc.engine.match(e, targets=svc.events.lookup(e['id'])['targets'], strict=True)
        assert set(svc.ledger.read()['runs']) == set(before)
    finally:
        release.set()
        await asyncio.gather(maintenance, publication, return_exceptions=True)
        await svc.engine.close()


@async_test
@pytest.mark.parametrize('status', ['queued', 'retry_wait', 'uncertain', 'succeeded'])
async def test_retained_run_rejects_different_fact_after_event_expiry(tmp_path, status):
    svc = local_service(tmp_path)
    svc.ledger.put(trigger(), 0, True)
    clock = [1000.0]
    svc.events.clock = lambda: clock[0]
    e = fact()
    svc.events.append(e, [])
    svc.events.delivery(e['id'], done=True)
    svc.ledger.append(dict(id='r', triggerId='t', triggerRevision=1, event=e, status=status,
        finishedAt='unknown'))
    clock[0] += RETENTION_SECONDS + 1
    await svc.maintenance()
    before = svc.ledger.path.read_bytes()
    with pytest.raises(ValueError, match='different fact'):
        await svc.accept({**e,'payload':{'runningCount':1}}, svc.core)
    assert svc.ledger.path.read_bytes() == before
    receipt = await svc.accept({**e,'occurredAt':'new-time'}, svc.core)
    assert receipt['event'] == e and receipt['duplicate'] is True


@async_test
async def test_cancel_backoff_head_wakes_serial_tail(tmp_path, monkeypatch):
    tail, timer = asyncio.Event(), asyncio.Event()
    async def execute(*args):
        tail.set()
        return {'ok':True}
    engine, _, _ = await setup(tmp_path, execute)
    engine.ledger.put(trigger(), 0, True)
    engine.ledger.clock = lambda: 1000.0
    for rid in ['head','tail']:
        engine.ledger.append(dict(id=rid, triggerId='t', triggerRevision=1, actionId='pause',
            event=event(rid), status='queued', admitted=True, arguments={}))
    engine.ledger.update('head', status='retry_wait', nextAttemptAt=datetime.fromtimestamp(1060,timezone.utc).isoformat())
    original = engine.io
    async def io(fn, *args, **kw):
        result = await original(fn, *args, **kw)
        if fn == engine.ledger.read and asyncio.current_task() is engine.tasks.get('t'):
            timer.set()
        return result
    monkeypatch.setattr(engine, 'io', io)
    app = FastAPI()
    svc = SimpleNamespace(engine=engine, ledger=engine.ledger, auth=SimpleNamespace(authorized=lambda _:True))
    app.state.automation = svc
    install(SimpleNamespace(app=app))
    try:
        await engine.kick()
        await wait(timer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response = await client.post('/api/automation/runs/head/cancel',json={'expectedRevision':2})
        assert response.status_code == 200
        await wait(tail)
        assert engine.ledger.read()['runs']['head']['attemptCount'] == 0
    finally:
        await engine.close()


@async_test
async def test_old_profile_copy_reopen_failure_recovery_and_repeat_maintenance(tmp_path, monkeypatch):
    from packages.automation.store import Ledger
    from packages.automation.event_store import EventStore
    from packages.actions.store import atomic
    source = tmp_path/'old-profile'
    events, ledger = EventStore(source), Ledger(source)
    events.initialize()
    ledger.put(trigger(), 0, True)
    old = fact('legacy-running')
    events.append(old, [ledger.read()['triggers']['t']])
    events.delivery(old['id'], done=True)
    state = ledger.read()
    # v0.1 legacy Run, no attempts; preserve unrelated extension fields.
    state['runs']['legacy'] = dict(id='legacy', triggerId='t', triggerRevision=1,
        actionId='pause', event=old, arguments={}, revision=1, status='running',
        targetIds=['original'], targetResults=[{'sessionId':'original','ok':True}],
        vendorExtension={'preserve':True})
    atomic(ledger.path, state)
    original = {p.name:p.read_bytes() for p in source.iterdir()}
    copy_root = tmp_path/'copy'
    shutil.copytree(source, copy_root)
    svc = local_service(copy_root)
    svc.events.clock = lambda: 2_000_000_000.0
    # Failure between the separate SQLite/JSON commits must remain repairable.
    maintenance = svc.ledger.maintenance
    def fail(*args):
        raise OSError('injected prune commit failure')
    monkeypatch.setattr(svc.ledger, 'maintenance', fail)
    with pytest.raises(OSError, match='prune'):
        await svc.maintenance()
    monkeypatch.setattr(svc.ledger, 'maintenance', maintenance)
    svc.ledger.recover()
    first = svc.ledger.read()['runs']['legacy']
    assert first['status'] == 'uncertain' and first['attempts'][0]['legacy']
    for _ in range(2):
        svc.events.initialize()
        svc.ledger.recover()
        await svc.maintenance()
        row = svc.ledger.read()['runs']['legacy']
        assert row['activeAttemptId'] == first['activeAttemptId']
        assert row['attemptCount'] == 1 and row['status'] == 'uncertain'
        assert row['targetIds'] == ['original'] and row['targetResults'] == first['targetResults']
        assert row['vendorExtension'] == {'preserve':True}
        with pytest.raises(ValueError, match='different fact'):
            await svc.accept({**old,'payload':{'runningCount':1}}, svc.core)
        assert (await svc.accept(old, svc.core))['duplicate']
    with svc.events.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
    assert {p.name:p.read_bytes() for p in source.iterdir()} == original


@async_test
async def test_revocation_between_targets_preserves_success_and_never_auto_replays(tmp_path):
    from functools import partial
    from packages.actions.executor import execute_dynamic
    engine, _, handle = await setup(tmp_path)
    calls = []
    async def pause(sid, args):
        calls.append(sid)
        await engine.bus.deactivate(handle)
        return {'ok':True}
    engine.execute = partial(execute_dynamic, pause=pause)
    engine.actions.put('actions', action(['first','second']))
    engine.ledger.put(trigger(), 0, True)
    try:
        await engine.match(event())
        await asyncio.wait_for(asyncio.gather(*tuple(engine.tasks.values())), 3)
        row = next(iter(engine.ledger.read()['runs'].values()))
        assert calls == ['first'] and row['status'] == 'uncertain'
        assert row['targetResults'] == [{'sessionId':'first','ok':True}]
        await engine.kick()
        assert calls == ['first'] and row['attemptCount'] == 1
    finally:
        await engine.close()


@async_test
async def test_locker_trusted_binding_still_verified_before_each_target(tmp_path, monkeypatch):
    from functools import partial
    from packages.actions.executor import execute_dynamic
    from packages.core import session
    from packages.interactions.store import InteractionStore, UserPrincipal
    from packages.interactions.trusted import create
    from tests.test_locker_trusted_binding import setup as locker_setup
    svc, adapter, owner, first, _, request = await locker_setup(tmp_path, monkeypatch)
    second = session.create('second-approved', workdir=str(tmp_path))
    svc.engine.actions.put('actions', action([first.id, second.id]))
    calls = []
    async def pause(sid, args):
        calls.append(sid)
        # This is after effect one, before its checkpoint and next verification.
        changed = action([first.id, second.id])
        changed['name'] = 'new definition requires a new approval'
        svc.engine.actions.put('actions', changed)
        return {'ok':True}
    svc.engine.execute = partial(execute_dynamic, pause=pause)
    store = InteractionStore(tmp_path/'cards.sqlite')
    try:
        card = create(svc, store, request)
        user = UserPrincipal('user')
        store.respond(user, card['id'], {'requestId':'once','expectedRevision':0,'choiceId':'go'})
        await store.dispatch(user, card['id'], adapter)
        # The durable dispatcher is independent; observe its completed Run.
        from tests.test_automation_core import until
        async def done():
            runs = svc.ledger.read()['runs']
            return bool(runs) and all(r['status'] == 'failed' for r in runs.values())
        await until(done)
        row = next(iter(svc.ledger.read()['runs'].values()))
        assert calls == [first.id] and row['attemptCount'] == 1
        assert row['targetResults'] == [{'sessionId':first.id,'ok':True}]
        assert 'changed' in row['error']
    finally:
        await svc.close()
