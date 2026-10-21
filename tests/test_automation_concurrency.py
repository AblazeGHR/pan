"""Deterministic scheduler interleavings requested by core acceptance review."""
import asyncio

from tests.test_automation_core import async_test, setup, trigger, event, settled


async def signal(ev):
    await asyncio.wait_for(ev.wait(), 2)


@async_test
async def test_empty_snapshot_then_match_cannot_lose_wakeup(tmp_path, monkeypatch):
    engine, calls, _ = await setup(tmp_path)
    engine.ledger.put(trigger(), 0, True)
    captured, release = asyncio.Event(), asyncio.Event()
    original = engine.io
    frozen = False
    async def io(fn, *args, **kw):
        nonlocal frozen
        result = await original(fn, *args, **kw)
        if (not frozen and fn == engine.ledger.read
                and asyncio.current_task() is engine.tasks.get('t')
                and calls and not any(r['status'] == 'queued' for r in result['runs'].values())):
            frozen = True
            captured.set()
            await release.wait()
        return result
    monkeypatch.setattr(engine, 'io', io)
    try:
        await engine.match(event('first'))
        await signal(captured)
        old = engine.tasks['t']
        await engine.match(event('second'))
        assert engine.tasks['t'] is old
        release.set()
        await asyncio.wait_for(settled(engine, 2), 2)
        assert calls == ['selected', 'selected']
    finally:
        release.set()
        await engine.close()


@async_test
async def test_disable_between_claim_and_dependency_deferral_cancels(tmp_path, monkeypatch):
    engine, calls, _ = await setup(tmp_path)
    engine.ledger.put(trigger(), 0, True)
    captured, release = asyncio.Event(), asyncio.Event()
    original = engine.io
    async def io(fn, *args, **kw):
        result = await original(fn, *args, **kw)
        if fn == engine.ledger.claim and result:
            engine.bus.definitions['workers.all_idle']['available'] = False
            captured.set()
            await release.wait()
        return result
    monkeypatch.setattr(engine, 'io', io)
    try:
        await engine.match(event())
        await signal(captured)
        task = engine.tasks['t']
        engine.ledger.put({**trigger(), 'enabled': False}, 1, True)
        release.set()
        await asyncio.wait_for(asyncio.shield(task), 2)
        row = next(iter(engine.ledger.read()['runs'].values()))
        assert row['status'] == 'cancelled'
        assert not calls
    finally:
        release.set()
        await engine.close()


@async_test
async def test_kick_read_inflight_at_close_must_not_spawn(tmp_path, monkeypatch):
    engine, _, _ = await setup(tmp_path)
    engine.ledger.put(trigger(), 0, True)
    engine.ledger.append(dict(id='queued', event=event(), status='queued', triggerId='t', triggerRevision=1, actionId='pause', arguments={}))
    captured, release = asyncio.Event(), asyncio.Event()
    original = engine.io
    kick_task = None
    async def io(fn, *args, **kw):
        result = await original(fn, *args, **kw)
        if fn == engine.ledger.read and asyncio.current_task() is kick_task:
            captured.set()
            await release.wait()
        return result
    monkeypatch.setattr(engine, 'io', io)
    kick_task = asyncio.create_task(engine.kick())
    try:
        await signal(captured)
        await engine.close()
        release.set()
        await kick_task
        assert not engine.tasks
    finally:
        release.set()
        await kick_task
        await engine.close()


@async_test
async def test_start_recovery_inflight_at_close_must_not_reopen(tmp_path, monkeypatch):
    engine, _, _ = await setup(tmp_path)
    captured, release = asyncio.Event(), asyncio.Event()
    original = engine.io
    start_task = None
    async def io(fn, *args, **kw):
        result = await original(fn, *args, **kw)
        if fn == engine.ledger.recover and asyncio.current_task() is start_task:
            captured.set()
            await release.wait()
        return result
    monkeypatch.setattr(engine, 'io', io)
    start_task = asyncio.create_task(engine.start())
    try:
        await signal(captured)
        await engine.close()
        release.set()
        await start_task
        assert engine.closed
        assert not engine.tasks
    finally:
        release.set()
        await start_task
        await engine.close()

@async_test
async def test_concurrent_stale_kicks_keep_one_runner_and_close_cancels_it(tmp_path, monkeypatch):
    entered, release_action = asyncio.Event(), asyncio.Event()
    active, maximum, invocations = 0, 0, 0
    async def execute(*args):
        nonlocal active, maximum, invocations
        active += 1
        invocations += 1
        maximum = max(maximum, active)
        entered.set()
        try:
            await release_action.wait()
            return {'ok': True}
        finally:
            active -= 1
    engine, _, _ = await setup(tmp_path, execute)
    engine.ledger.put(trigger(), 0, True)
    engine.ledger.append(dict(id='queued', event=event(), status='queued', triggerId='t', triggerRevision=1, actionId='pause', arguments={}))
    snapshots, release_kicks = asyncio.Event(), asyncio.Event()
    original = engine.io
    callers, reads = set(), 0
    async def io(fn, *args, **kw):
        nonlocal reads
        result = await original(fn, *args, **kw)
        if fn == engine.ledger.read and asyncio.current_task() in callers:
            reads += 1
            if reads == 8:
                snapshots.set()
            await release_kicks.wait()
        return result
    monkeypatch.setattr(engine, 'io', io)
    for _ in range(8):
        callers.add(asyncio.create_task(engine.kick()))
    try:
        await signal(snapshots)
        release_kicks.set()
        await asyncio.gather(*callers)
        await signal(entered)
        assert len(engine.tasks) == 1
        assert maximum == invocations == 1
        await engine.close()
        assert not engine.tasks and not engine.wakes
        assert active == 0
        assert next(iter(engine.ledger.read()['runs'].values()))['status'] == 'uncertain'
        await engine.kick()
        assert not engine.tasks
    finally:
        release_kicks.set()
        release_action.set()
        await asyncio.gather(*callers)
        await engine.close()


def test_deferral_disable_order_and_stale_revision_never_resurrect(tmp_path):
    from packages.automation.store import Ledger
    for order in ('disable_first', 'defer_first', 'cancel_then_stale_defer'):
        ledger = Ledger(tmp_path / order)
        ledger.put(trigger(), 0, True)
        ledger.append(dict(id='q', event=event(), status='queued', triggerId='t', triggerRevision=1))
        claimed = ledger.claim('q')
        if order == 'disable_first':
            ledger.put({**trigger(), 'enabled': False}, 1, True)
            ledger.defer_unavailable('q', claimed['revision'])
        elif order == 'defer_first':
            ledger.defer_unavailable('q', claimed['revision'])
            ledger.put({**trigger(), 'enabled': False}, 1, True)
        else:
            waiting = ledger.defer_unavailable('q', claimed['revision'])
            ledger.cancel('q', waiting['revision'])
            before = ledger.read()['runs']['q']
            ledger.defer_unavailable('q', claimed['revision'])
            assert ledger.read()['runs']['q'] == before
        assert ledger.read()['runs']['q']['status'] == 'cancelled'


@async_test
async def test_kick_after_owner_retirement_starts_successor(tmp_path):
    engine, calls, _ = await setup(tmp_path)
    engine.ledger.put(trigger(), 0, True)
    try:
        await engine.match(event('first'))
        owner = engine.tasks['t']
        await asyncio.wait_for(asyncio.shield(owner), 2)
        assert 't' not in engine.tasks
        await engine.match(event('second'))
        successor = engine.tasks['t']
        assert successor is not owner
        await asyncio.wait_for(asyncio.shield(successor), 2)
        assert calls == ['selected', 'selected']
    finally:
        await engine.close()

@async_test
async def test_close_before_new_runner_first_step_retires_registration(tmp_path, monkeypatch):
    engine, calls, _ = await setup(tmp_path)
    engine.ledger.put(trigger(), 0, True)
    engine.ledger.append(dict(id='queued', event=event(), status='queued', triggerId='t', triggerRevision=1, actionId='pause', arguments={}))
    # Return a preloaded snapshot without yielding: kick creates its task but
    # close cancels it before its coroutine can enter the ownership finally.
    snapshot = engine.ledger.read()
    original = engine.io
    async def io(fn, *args, **kw):
        if fn == engine.ledger.read:
            return snapshot
        return await original(fn, *args, **kw)
    monkeypatch.setattr(engine, 'io', io)
    await engine.kick()
    assert 't' in engine.tasks
    await engine.close()
    assert not engine.tasks and not engine.wakes
    assert not calls
    assert engine.ledger.read()['runs']['queued']['status'] == 'queued'
