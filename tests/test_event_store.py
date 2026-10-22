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


def fact(eid='e', payload=None):
    return dict(id=eid, type='workers.all_idle', schemaVersion=1, source={'type':'core','id':'workers'},
                occurredAt='2026-10-22T00:00:00Z',payload=payload or {'runningCount':0})


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


@async_test
async def test_commit_precedes_fanout_schema_and_write_failure(tmp_path,monkeypatch):
    svc,calls=await setup(tmp_path)
    seen=[]
    h=svc.bus.activate('plugin','listener')
    async def listener(e):
        assert any(r['event']['id']==e['id'] for r in svc.events.page()['events'])
        seen.append(e['id'])
    svc.bus.subscribe(h,'listener',listener)
    append=svc.events.append
    def fail(*args):
        raise OSError('Event Store fsync injected failure')
    monkeypatch.setattr(svc.events,'append',fail)
    try:
        with pytest.raises(OSError):
            await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'disk-fail')
        assert not calls and not seen and not svc.events.page()['events']
        monkeypatch.setattr(svc.events,'append',append)
        with pytest.raises(Exception):
            await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':'bad'},'bad')
        assert not svc.events.page()['events']
        reply=await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'good')
        assert reply['persisted'] and reply['accepted']
        await terminal(svc)
        await until(lambda:asyncio.sleep(0,result=seen==['good']))
        assert calls==['selected']
        duplicate=await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'good')
        assert duplicate['duplicate'] and len(svc.ledger.read()['runs'])==1
    finally:
        await svc.close()


@async_test
async def test_crash_after_commit_before_delivery_and_frozen_trigger_set(tmp_path):
    svc,calls=await setup(tmp_path)
    svc.dispatcher.cancel();await asyncio.gather(svc.dispatcher,return_exceptions=True)
    await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'accepted')
    svc.ledger.put(trigger('new-after-accept'),0,True)
    assert not calls
    await svc.close()
    recovered,calls=await setup_existing(tmp_path)
    try:
        rows=await terminal(recovered)
        assert rows[0]['triggerId']=='t' and calls==['selected']
        await until(lambda:asyncio.sleep(0,result=recovered.events.stats()['pending']==0))
    finally:
        await recovered.close()


async def setup_existing(root,pause=None):
    calls=[]
    async def effect(sid,args):
        calls.append(sid);return {'ok':True}
    svc=Service(root,SimpleNamespace(workers={},set_running_observer=lambda cb:None,
        _sess=SimpleNamespace(SESSION_DIR=root/'sessions')),pause or effect)
    svc.engine.actions=Store(root/'actions')
    await svc.start()
    return svc,calls


@async_test
async def test_partial_run_write_recovery_dedup_and_no_automatic_run_retry(tmp_path,monkeypatch):
    svc,calls=await setup(tmp_path)
    svc.ledger.put(trigger('second'),0,True)
    append=svc.ledger.append
    def partial(run):
        if run['triggerId']=='second':
            raise OSError('second Run disk failure')
        return append(run)
    monkeypatch.setattr(svc.ledger,'append',partial)
    try:
        await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'partial')
        await terminal(svc)
        await until(lambda:asyncio.sleep(0,result=bool(svc.events.pending()[0]['lastError'])))
        assert len(calls)==1
        monkeypatch.setattr(svc.ledger,'append',append)
        svc.wake.set()
        rows=await terminal(svc,2)
        assert calls==['selected','selected']
        await svc.replay(['partial'])
        await until(lambda:asyncio.sleep(0,result=svc.events.stats()['pending']==0))
        assert calls==['selected','selected']
        assert all(r['attemptCount']==1 and len(r['attempts'])==1 for r in rows)
    finally:
        await svc.close()


@async_test
async def test_uncertain_preserves_attempt_and_refuses_user_replay(tmp_path):
    entered=asyncio.Event()
    async def effect(*args):
        entered.set();await asyncio.Event().wait()
    svc,_=await setup(tmp_path,effect)
    await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'uncertain')
    await asyncio.wait_for(entered.wait(),3)
    await svc.close()
    recovered,calls=await setup_existing(tmp_path)
    try:
        row=(await terminal(recovered))[0]
        assert row['status']=='uncertain' and row['attempts'][0]['status']=='uncertain'
        with pytest.raises(ValueError,match='reconciliation'):
            await recovered.replay(['uncertain'])
        assert not calls
    finally:
        await recovered.close()


@async_test
async def test_api_bounded_pages_replay_authorization_and_dedup(tmp_path):
    svc,calls=await setup(tmp_path)
    app=__import__('fastapi').FastAPI();app.state.automation=svc;install(SimpleNamespace(app=app))
    try:
        await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'api')
        await terminal(svc)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
            assert (await client.post('/api/automation/events/replay',json={'eventIds':['api']})).status_code==401
            token=(await client.post('/api/automation/login',json={'key':svc.auth.key()})).json()['token']
            auth={'Authorization':'Bearer '+token}
            assert (await client.get('/api/automation/events?limit=101')).status_code==400
            page=(await client.get('/api/automation/events?limit=1')).json()
            assert page['events'][0]['event']['id']=='api'
            assert (await client.post('/api/automation/events/replay',json={'eventIds':['api']},headers=auth)).status_code==200
            assert (await client.post('/api/automation/events/replay',json={'eventIds':['missing']},headers=auth)).status_code==409
            assert (await client.post('/api/automation/events',json={'source':'core'})).status_code==405
            storage=(await client.get('/api/automation')).json()['storage']
            assert storage['events']['events']==1
        await until(lambda:asyncio.sleep(0,result=svc.events.stats()['pending']==0))
        assert calls==['selected']
    finally:
        await svc.close()


def test_run_retention_protects_pending_uncertain_and_retained_event(tmp_path):
    ledger=Ledger(tmp_path,max_runs=5)
    ledger.put(trigger(),0,True)
    old=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
    for i,status in enumerate(['succeeded','queued','running','uncertain','failed']):
        ledger.append(dict(id=str(i),triggerId='t',triggerRevision=1,event=fact(str(i)),status=status,finishedAt=old))
    with pytest.raises(CapacityError):
        ledger.append(dict(id='full',triggerId='t',triggerRevision=1,event=fact('full'),status='queued'))
    result=ledger.maintenance({'4'})
    assert result=={'deletedRuns':1} and set(ledger.read()['runs'])=={'1','2','3','4'}
    ledger.recover()
    assert ledger.read()['runs']['2']['attempts'][0]['legacy']


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

@async_test
async def test_pending_old_plugin_events_do_not_starve_independent_event(tmp_path):
    svc,calls=await setup(tmp_path)
    # Stop only dispatcher while arranging a deterministic >one-page backlog.
    svc.dispatcher.cancel();await asyncio.gather(svc.dispatcher,return_exceptions=True)
    waiting={**trigger('wait'),'eventTypes':['workers.all_idle','missing.fact']}
    svc.ledger.put(waiting,0,True)
    waiting=svc.ledger.read()['triggers']['wait']
    for i in range(65):
        svc.events.append(fact('waiting-'+str(i)),[waiting])
    await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'independent')
    svc.dispatcher=asyncio.create_task(svc.dispatch())
    try:
        rows=await asyncio.wait_for(terminal(svc),3)
        assert rows[0]['event']['id']=='independent' and calls==['selected']
        assert svc.events.stats()['pending']>=65
    finally:
        await svc.close()


@async_test
async def test_slow_commit_cancellation_settles_and_restart_delivers(tmp_path,monkeypatch):
    import threading
    svc,calls=await setup(tmp_path)
    entered,release=threading.Event(),threading.Event()
    original=svc.events.append
    def blocked(*args):
        entered.set();assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(svc.events,'append',blocked)
    publish_task=asyncio.create_task(svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'cancel-persist'))
    try:
        assert await asyncio.to_thread(entered.wait,2)
        # Unrelated loop work can complete while fsync/commit is blocked.
        await asyncio.wait_for(asyncio.sleep(0),.1)
        assert not calls
        publish_task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await publish_task
        assert svc.events.page()['events'][0]['event']['id']=='cancel-persist'
    finally:
        release.set();await svc.close()
    recovered,calls=await setup_existing(tmp_path)
    try:
        await terminal(recovered)
        assert calls==['selected']
    finally:
        await recovered.close()


@async_test
async def test_disabled_after_accept_does_not_resurrect_and_attempt_deferral_reuses_id(tmp_path):
    svc,calls=await setup(tmp_path)
    svc.dispatcher.cancel();await asyncio.gather(svc.dispatcher,return_exceptions=True)
    await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'disabled')
    svc.ledger.put({**trigger(),'enabled':False},1,True)
    svc.dispatcher=asyncio.create_task(svc.dispatch())
    try:
        rows=await terminal(svc)
        assert rows[0]['status']=='cancelled' and not calls and rows[0]['attempts']==[]
    finally:
        await svc.close()
    ledger=Ledger(tmp_path/'attempt')
    ledger.put(trigger(),0,True)
    ledger.append(dict(id='r',triggerId='t',triggerRevision=1,event=fact(),status='queued'))
    first=ledger.claim('r')
    ledger.defer_unavailable('r',first['revision'])
    second=ledger.claim('r')
    assert first['activeAttemptId']==second['activeAttemptId'] and second['attemptCount']==1
    ledger.update('r',status='failed',error='ordinary error')
    assert ledger.claim('r') is None


@async_test
async def test_capacity_alarm_visible_without_evicting_uncertain(tmp_path):
    svc,calls=await setup(tmp_path)
    svc.events.max_events=1
    try:
        await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'one')
        await terminal(svc)
        with pytest.raises(CapacityError):
            await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'two')
        assert svc.events.stats()['warning'] and svc.events.stats()['full']
        assert any(d['phase']=='event-admission' for d in svc.bus.diagnostics)
        assert len(svc.events.page()['events'])==1 and calls==['selected']
    finally:
        await svc.close()


@async_test
async def test_legacy_running_migrates_to_one_uncertain_attempt_without_event_replay(tmp_path):
    ledger=Ledger(tmp_path)
    ledger.put(trigger(),0,True)
    state=ledger.read()
    state['runs']['legacy']={'id':'legacy','event':fact('old'),'triggerId':'t','triggerRevision':1,
                            'actionId':'pause','arguments':{},'status':'running','revision':1,'targetIds':['original']}
    from packages.actions.store import atomic
    atomic(ledger.path,state)
    before=ledger.path.read_bytes()
    assert ledger.read()['runs']['legacy']['status']=='running' and ledger.path.read_bytes()==before
    recovered,calls=await setup_existing(tmp_path)
    try:
        row=recovered.ledger.read()['runs']['legacy']
        assert row['status']=='uncertain' and row['attempts'][0]['legacy'] and row['targetIds']==['original']
        assert not recovered.events.page()['events'] and not calls
    finally:
        await recovered.close()

@async_test
async def test_plugin_revocation_while_waiting_for_admission_rejects_old_handle(tmp_path):
    svc,calls=await setup(tmp_path)
    handle=svc.bus.activate('plugin','demo')
    svc.bus.register(handle,'demo.tick',{'type':'object'})
    await svc.admission.acquire()
    publisher=asyncio.create_task(svc.bus.publish_async(handle,'demo.tick',{},'old-generation'))
    try:
        await asyncio.sleep(0)  # prepare completed; awaiting the owned admission lock
        await svc.plugin_disabled(handle)
        svc.admission.release()
        with pytest.raises(ValueError,match='revoked'):
            await publisher
        assert not svc.events.page()['events'] and not calls
    finally:
        if svc.admission.locked():svc.admission.release()
        await asyncio.gather(publisher,return_exceptions=True)
        await svc.close()


@async_test
async def test_many_concurrent_same_event_ids_have_one_run_and_attempt(tmp_path):
    svc,calls=await setup(tmp_path)
    try:
        replies=await asyncio.gather(*(svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'same') for _ in range(16)))
        rows=await terminal(svc)
        assert sum(not r['duplicate'] for r in replies)==1
        assert svc.events.stats()['events']==1 and len(rows)==1 and rows[0]['attemptCount']==1
        assert calls==['selected']
    finally:
        await svc.close()
