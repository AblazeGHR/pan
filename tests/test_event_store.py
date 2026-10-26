"""Durable acceptance, bounded recovery/replay and attempt foundation contracts."""
import asyncio
import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from packages.automation.service import Service
from packages.automation.event_store import EventStore, CapacityError
from packages.automation.store import Ledger
from packages.automation.api import install
from tests.test_automation_core import async_test, action, trigger, event, until
from packages.actions.store import Store
from tests.test_automation_canonical import canonical, subscription, fact as canonical_fact, deliver, run
from tests.test_automation_canonical import control


def fact(eid='e', payload=None):
    return dict(id=eid, type='workers.all_idle', schemaVersion=1, source={'type':'core','id':'workers'},
                occurredAt='2026-10-22T00:00:00Z',payload=payload or {'runningCount':0})


def test_store_duplicate_conflict_retention_capacity_and_paging(tmp_path):
    clock=[1000.0]
    store=EventStore(tmp_path,max_events=2,max_bytes=100000,retention=100,clock=lambda:clock[0])
    store.initialize()
    with store.connection() as db:
        assert db.execute('PRAGMA max_page_count').fetchone()[0]==81920
    assert not store.append(fact(),[])['duplicate']
    assert store.append({**fact(),'occurredAt':'later'},[])['duplicate']
    with pytest.raises(ValueError,match='different fact'):
        store.append(fact(payload={'runningCount':1}),[])
    store.append(fact('pending'),[])
    assert store.stats()['full']
    with pytest.raises(CapacityError):
        store.append(fact('overflow'),[])
    assert store.page(limit=1)['nextAfter']==1
    assert store.page(after=1)['events'][0]['event']['id']=='pending'
    for after,limit in [(-1,1),(0,0),(0,101)]:
        with pytest.raises(ValueError):
            store.page(after,limit)
    clock[0]+=101
    store.delivery('e',done=True)
    assert store.stats()['overduePending']==1
    with pytest.raises(ValueError,match='expired'):
        store.replay(['e'])
    assert store.replay(['pending'])==['pending']
    cleanup=store.maintenance()
    assert cleanup['deletedEvents']==1 and cleanup['retainedIds']=={'pending'}
    store.append(fact('new'),[])
    with pytest.raises(ValueError,match='missing or expired'):
        store.replay(['e'])
    for ids in [[],['new']*2,['x']*101,{}]:
        with pytest.raises(ValueError):
            store.replay(ids)


def test_payload_and_bytes_capacity_fail_closed(tmp_path):
    store=EventStore(tmp_path,max_bytes=10);store.initialize()
    with pytest.raises(CapacityError):
        store.append(fact(),[])
    with pytest.raises(ValueError,match='64 KiB'):
        store.append(fact(payload={'blob':'x'*65537}),[])
    assert not store.page()['events']
    ledger=Ledger(tmp_path/'ledger',max_bytes=300)
    with pytest.raises(CapacityError):
        ledger.put(trigger(),0,True)
    assert not ledger.path.exists()


def test_failed_delivery_page_preserves_each_count_and_rolls_back_together(tmp_path):
    store = EventStore(tmp_path)
    store.initialize()
    for event_id in ('a', 'b', 'untouched'):
        store.append(fact(event_id), [])
    store.delivery_errors([('a', 'dependency unavailable'), ('b', 'x' * 2500)])
    assert store.lookup('a')['status'] == store.lookup('b')['status'] == 'pending'
    assert store.lookup('a')['deliveryCount'] == store.lookup('b')['deliveryCount'] == 1
    assert store.lookup('a')['lastError'] == 'dependency unavailable'
    assert store.lookup('b')['lastError'] == 'x' * 2000
    assert store.lookup('untouched')['deliveryCount'] == 0
    with store.connection() as db:
        db.execute("CREATE TRIGGER refuse_b BEFORE UPDATE ON events WHEN OLD.id='b' BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match='injected failure'):
        store.delivery_errors([('a', 'second'), ('b', 'second')])
    assert store.lookup('a')['deliveryCount'] == store.lookup('b')['deliveryCount'] == 1
    assert store.lookup('a')['lastError'] == 'dependency unavailable'
    assert store.stats()['pending'] == 3


def test_durable_event_commit_precedes_any_admission_or_fanout(canonical, monkeypatch):
    h = canonical
    job, target = subscription(h)
    svc = h.automation
    svc.started = True
    ev = canonical_fact(h)
    original = svc.events.append
    def fail(*args, **kwargs):
        raise OSError('Event commit failed')
    monkeypatch.setattr(svc.events, 'append', fail)
    with pytest.raises(OSError, match='commit failed'):
        asyncio.run(svc.accept(ev, svc.core))
    assert not svc.events.page()['events']
    assert not h.service.final.runs(job['id']) and not h.provider.calls
    monkeypatch.setattr(svc.events, 'append', original)
    receipt = asyncio.run(svc.accept(ev, svc.core))
    assert receipt['persisted'] and not receipt['duplicate']
    assert not h.service.final.runs(job['id'])
    asyncio.run(svc.deliver(svc.events.lookup(ev['id'])))
    assert len(h.service.final.runs(job['id'])) == 1 and not h.provider.calls


def test_crash_after_commit_preserves_frozen_subscription_set_and_original_id(canonical):
    h = canonical
    job, first = subscription(h)
    svc = h.automation
    svc.started = True
    ev = canonical_fact(h)
    asyncio.run(svc.accept(ev, svc.core))
    _, late = subscription(h, job, key='late')
    stored = svc.events.lookup(ev['id'])
    assert [t['id'] for t in stored['targets']] == [first['id']]
    reopened = EventStore(svc.ledger.path.parent)
    reopened.owner_fence = h.service.owner
    assert reopened.lookup(ev['id']) == stored
    asyncio.run(svc.deliver(stored))
    asyncio.run(svc.deliver(reopened.lookup(ev['id'])))
    assert len(h.service.final.runs(job['id'])) == 1
    receipt = svc.events.subscription_receipts(ev['id'])['trigger:' + first['id']]
    assert receipt['runId'] == h.service.final.runs(job['id'])[0]['runId']
    assert 'trigger:' + late['id'] not in svc.events.subscription_receipts(ev['id'])
    assert not h.provider.calls


def test_partial_subscription_failure_retries_only_missing_identity(canonical, monkeypatch):
    h = canonical
    job, first = subscription(h)
    _, second = subscription(h, job, key='second')
    svc = h.automation
    svc.started = True
    ev = canonical_fact(h)
    asyncio.run(svc.accept(ev, svc.core))
    row = svc.events.lookup(ev['id'])
    original = h.service.admission.admit
    async def failed(event, target):
        if target['id'] == first['id']:
            raise OSError('one subscription unavailable')
        return await original(event, target)
    monkeypatch.setattr(h.service.admission, 'admit', failed)
    with pytest.raises(ValueError, match='one subscription'):
        asyncio.run(svc.deliver(row))
    before = svc.events.subscription_receipts(ev['id'])['trigger:' + second['id']]
    assert len(h.service.final.runs(job['id'])) == 1
    monkeypatch.setattr(h.service.admission, 'admit', original)
    asyncio.run(svc.deliver(row))
    after = svc.events.subscription_receipts(ev['id'])
    assert after['trigger:' + second['id']] == before
    assert after['trigger:' + first['id']]['runId'] != before['runId']
    assert len(h.service.final.runs(job['id'])) == 2 and not h.provider.calls


def test_lost_subscription_receipt_queries_original_admission_never_resends(canonical, monkeypatch):
    h = canonical
    job, target = subscription(h)
    svc = h.automation
    svc.started = True
    ev = canonical_fact(h)
    asyncio.run(svc.accept(ev, svc.core))
    row = svc.events.lookup(ev['id'])
    original = svc.events.subscription
    def lost(*args, **kwargs):
        raise OSError('receipt commit lost')
    monkeypatch.setattr(svc.events, 'subscription', lost)
    with pytest.raises(ValueError, match='receipt commit lost'):
        asyncio.run(svc.deliver(row))
    first = h.service.final.runs(job['id'])[0]['runId']
    monkeypatch.setattr(svc.events, 'subscription', original)
    asyncio.run(svc.deliver(row))
    assert svc.events.subscription_receipts(ev['id'])['trigger:' + target['id']]['runId'] == first
    assert len(h.service.final.runs(job['id'])) == 1 and not h.provider.calls


def test_replay_of_uncertain_canonical_run_preserves_effect_identity(canonical):
    h = canonical
    job, target = subscription(h)
    ev = canonical_fact(h)
    asyncio.run(deliver(h, ev))
    rid = h.service.final.runs(job['id'])[0]['runId']
    h.provider.unknown = True
    asyncio.run(h.service.dispatch_once())
    before = copy.deepcopy(h.service.final.get(job['id'])['attempts'])
    assert run(h, rid)['state'] == 'uncertain'
    asyncio.run(h.automation.replay([ev['id']]))
    asyncio.run(h.automation.deliver(h.automation.events.lookup(ev['id'])))
    h.automation.events.delivery(ev['id'], done=True)
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(job['id'])['attempts'] == before
    assert len(h.provider.calls) == 1 and len(h.service.final.runs(job['id'])) == 1


def test_legacy_uncertain_event_replay_remains_refused(tmp_path):
    svc = Service(tmp_path / 'automation', SimpleNamespace(workers={}), None)
    svc.ledger.put(trigger(), 0, True)
    svc.ledger.append(dict(id='old', triggerId='t', triggerRevision=1, event=fact(), status='uncertain'))
    svc.events.initialize()
    svc.events.append(fact(), [])
    before = svc.ledger.path.read_bytes()
    with pytest.raises(ValueError, match='reconciliation'):
        asyncio.run(svc.replay(['e']))
    assert svc.ledger.path.read_bytes() == before


@pytest.mark.parametrize('state', ['disabled', 'retired'])
def test_trigger_disabled_after_accept_never_resurrects(canonical, state):
    h = canonical
    job, target = subscription(h)
    svc = h.automation
    svc.started = True
    ev = canonical_fact(h)
    asyncio.run(svc.accept(ev, svc.core))
    response = control(h, 'triggers', target, 'disable' if state == 'disabled' else 'retire')
    assert response.status_code == 200, response.text
    asyncio.run(svc.deliver(svc.events.lookup(ev['id'])))
    assert svc.events.subscription_receipts(ev['id'])['trigger:' + target['id']]['admissionResult'] == 'skipped'
    assert not h.service.final.runs(job['id']) and not h.provider.calls


def test_retention_preserves_all_legacy_history_and_unsettled_admissions(tmp_path):
    ledger = Ledger(tmp_path / 'automation')
    ledger.put(trigger(), 0, True)
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    for status in ('succeeded', 'running', 'uncertain', 'failed'):
        ledger.append(dict(id=status, triggerId='t', triggerRevision=1, event=fact(status),
                           status=status, finishedAt=old))
    for status in ('queued', 'uncertain', 'admitted', 'failed'):
        ledger.append(dict(id='admission-' + status, recordType='triggerAdmission', triggerId='t',
            triggerRevision=1, event=fact('new-' + status), status=status, finishedAt=old))
    before = copy.deepcopy(ledger.read()['runs'])
    assert ledger.maintenance({'new-failed'}) == {'deletedRuns':1}
    after = ledger.read()['runs']
    assert set(before) - set(after) == {'admission-admitted'}
    assert all(after[key] == value for key, value in before.items() if key != 'admission-admitted')


def test_concurrent_event_redelivery_persists_one_envelope_and_run(canonical):
    h = canonical
    job, target = subscription(h)
    svc = h.automation
    svc.started = True
    ev = canonical_fact(h)
    async def check():
        replies = await asyncio.gather(*(svc.accept(ev, svc.core) for _ in range(16)))
        assert sum(not r['duplicate'] for r in replies) == 1
        await svc.deliver(svc.events.lookup(ev['id']))
    asyncio.run(check())
    assert svc.events.stats()['events'] == 1
    assert len(h.service.final.runs(job['id'])) == 1
    assert not h.provider.calls


def test_capacity_alarm_refuses_new_event_without_evicting_history(canonical):
    h = canonical
    job, target = subscription(h)
    svc = h.automation
    svc.events.max_events = 1
    asyncio.run(deliver(h, canonical_fact(h, 'kept')))
    with pytest.raises(CapacityError):
        asyncio.run(svc.accept(canonical_fact(h, 'refused'), svc.core))
    assert svc.events.stats()['full'] and svc.events.stats()['warning']
    assert [r['event']['id'] for r in svc.events.page()['events']] == ['kept']
    assert len(h.service.final.runs(job['id'])) == 1 and not h.provider.calls


def test_slow_event_commit_cancellation_settles_before_return(canonical, monkeypatch):
    import threading
    h = canonical
    job, _ = subscription(h)
    svc = h.automation
    svc.started = True
    entered, release = threading.Event(), threading.Event()
    original = svc.events.append
    def held(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(svc.events, 'append', held)
    async def check():
        task = asyncio.create_task(svc.accept(canonical_fact(h), svc.core))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.wait_for(asyncio.sleep(0), .5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert svc.events.lookup('event')['status'] == 'pending'
            await svc.deliver(svc.events.lookup('event'))
            assert len(h.service.final.runs(job['id'])) == 1 and not h.provider.calls
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())


def test_revoked_plugin_handle_waiting_for_admission_cannot_commit(canonical):
    h = canonical
    svc = h.automation
    svc.started = True
    handle = svc.bus.activate('plugin', 'owned')
    svc.bus.register(handle, 'owned.tick', {'type':'object'})
    async def check():
        await svc.admission.acquire()
        task = asyncio.create_task(svc.bus.publish_async(handle, 'owned.tick', {}, 'revoked'))
        try:
            await asyncio.sleep(0)
            await svc.plugin_disabled(handle)
            svc.admission.release()
            with pytest.raises(ValueError, match='revoked'):
                await task
            assert not svc.events.page()['events']
        finally:
            if svc.admission.locked():
                svc.admission.release()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())


def test_original_time_event_pin_ack_never_releases_another_event(tmp_path):
    clock = [100.0]
    store = EventStore(tmp_path, clock=lambda:clock[0], retention=10)
    store.initialize()
    for eid in ('first', 'second'):
        ev = {**fact(eid), 'type':'scheduler.time.v1', 'source':{'type':'core', 'id':'scheduler'}}
        store.append(ev, [])
        store.delivery(eid, done=True)
    clock[0] = 200.0
    assert store.maintenance()['deletedEvents'] == 0
    store.release_pin('first')
    assert store.maintenance()['deletedEvents'] == 1
    assert store.lookup('first') is None and store.lookup('second')['event']['id'] == 'second'
    store.release_pin('first')  # Original-ID ack is idempotent.
    assert store.lookup('second') is not None


# Compatibility fixtures still imported by tests owned by other work packages.
async def setup(root, pause=None):
    calls=[]
    async def effect(sid, args):
        calls.append(sid)
        return {'ok':True}
    # The current service host projects its disabled quota adapter from the
    # worker's owned Session root. Keep that identity explicitly inside fixture.
    svc=Service(root, SimpleNamespace(workers={},set_running_observer=lambda cb:None,
        _sess=SimpleNamespace(SESSION_DIR=root/'sessions')), pause or effect)
    svc.engine.actions=Store(root/'actions')
    svc.engine.actions.put('actions',action())
    svc.ledger.put(trigger(),0,True)
    await svc.start()
    return svc,calls


async def terminal(svc, count=1):
    async def done():
        runs=(await svc.engine.io(svc.ledger.read))['runs']
        return len(runs)==count and all(r['status'] not in {'queued','running'} for r in runs.values())
    await until(done)
    return list((await svc.engine.io(svc.ledger.read))['runs'].values())


async def setup_existing(root,pause=None):
    calls=[]
    async def effect(sid,args):
        calls.append(sid);return {'ok':True}
    svc=Service(root,SimpleNamespace(workers={},set_running_observer=lambda cb:None,
        _sess=SimpleNamespace(SESSION_DIR=root/'sessions')),pause or effect)
    svc.engine.actions=Store(root/'actions')
    await svc.start()
    return svc,calls
