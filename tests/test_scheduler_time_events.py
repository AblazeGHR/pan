"""Owned data only: two durable stores, exact crash boundaries and broadcast."""
import asyncio
import copy
from datetime import datetime,timedelta
from types import SimpleNamespace

import pytest

from packages.scheduler.time_events import Sources,Runtime,EVENT_TYPE,OWNER
from packages.automation.event_store import EventStore
from packages.automation.service import Service
from packages.actions.store import Store
from packages.jobs.action_templates import Conflict
from tests.test_automation_core import async_test,action,trigger,settled
from tests.test_job_action_chain import _action,_steps

NOW=datetime(2026,10,25,10)


def source(sources,**kw):
    return sources.put({'name':'timer','enabled':True,
        'schedule':{'kind':'interval','intervalSec':60},**kw},0,at=NOW)


async def store_admit(events,event):
    return {**events.append(event,[]),'persisted':True}


@async_test
async def test_admission_commit_precedes_cursor_and_restart_emits_once(tmp_path,monkeypatch):
    sources=Sources(tmp_path/'scheduler'); item=source(sources)
    events=EventStore(tmp_path/'automation');events.initialize()
    original=sources.advance
    admitted=[]
    async def admit(event):
        assert sources.list()[0]['publishCursor'] is None
        receipt=await store_admit(events,event);admitted.append(receipt)
        return receipt
    def crash(*args):raise OSError('owned crash after Event COMMIT before source cursor COMMIT')
    monkeypatch.setattr(sources,'advance',crash)
    runtime=Runtime(sources,admit,[])
    await runtime.tick({},at=NOW+timedelta(seconds=60))
    assert events.stats()['events']==1 and sources.list()[0]['publishCursor'] is None
    pending=sources.list()[0]['pending']
    assert pending['event']['payload']['scheduleId']==item['id']
    with pytest.raises(Conflict):sources.put({'name':'edit','schedule':item['schedule']},1,item['id'],at=NOW)
    # New instances model disk-only restart, including frozen emittedAt.
    fresh=Sources(tmp_path/'scheduler'); reopened=EventStore(tmp_path/'automation')
    fresh_runtime=Runtime(fresh,lambda event:store_admit(reopened,event),[],lambda eid:asyncio.to_thread(reopened.release_pin,eid))
    await fresh_runtime.tick({},at=NOW+timedelta(seconds=120))
    after=fresh.list()[0]
    assert after['publishCursor']['eventId']==pending['event']['id']
    assert after['nextFireAt']==pending['nextFireAt'] and 'pending' not in after
    assert reopened.stats()['events']==1
    # Before the next due instant, restart cannot emit again.
    await Runtime(fresh,lambda event:store_admit(reopened,event),[]).tick({},at=NOW+timedelta(seconds=119))
    assert reopened.stats()['events']==1
    assert admitted[0]['event']==reopened.page()['events'][0]['event']


@async_test
async def test_admission_failure_does_not_advance_and_pin_survives_retention(tmp_path):
    sources=Sources(tmp_path/'s');source(sources)
    events=EventStore(tmp_path/'e',retention=1,clock=lambda:0);events.initialize()
    async def unavailable(event):raise OSError('Event Store unavailable')
    await Runtime(sources,unavailable,[]).tick({},at=NOW+timedelta(seconds=60))
    assert sources.list()[0]['publishCursor'] is None and events.stats()['events']==0
    async def admit_then_crash(event):
        await store_admit(events,event)
        raise OSError('receipt lost after commit')
    await Runtime(sources,admit_then_crash,[]).tick({},at=NOW+timedelta(seconds=61))
    event=events.page()['events'][0]['event'];events.delivery(event['id'],done=True)
    events.clock=lambda:1000
    assert events.maintenance()['deletedEvents']==0
    await Runtime(sources,lambda event:store_admit(events,event),[],lambda eid:asyncio.to_thread(events.release_pin,eid)).tick({},at=NOW+timedelta(seconds=62))
    assert events.stats()['events']==1
    assert events.maintenance()['deletedEvents']==1


@async_test
async def test_two_contenders_share_one_occurrence_identity(tmp_path):
    sources=Sources(tmp_path/'s');source(sources)
    events=EventStore(tmp_path/'e');events.initialize()
    receipts=[]
    async def admit(event):
        await asyncio.sleep(.01)
        receipt=await store_admit(events,event);receipts.append(receipt)
        return receipt
    await asyncio.gather(*(Runtime(Sources(tmp_path/'s'),admit,[]).tick({},at=NOW+timedelta(seconds=60)) for _ in range(2)))
    assert events.stats()['events']==1 and sum(not r['duplicate'] for r in receipts)==1
    assert sources.list()[0]['owner']==OWNER


@async_test
@pytest.mark.parametrize('kind,policy,expected',[('once','fire_now','expired'),('interval','skip','skipped'),('interval','fire_now','published')])
async def test_scheduler_missed_policy_not_trigger_retry(tmp_path,kind,policy,expected):
    sources=Sources(tmp_path/'s')
    spec={'kind':'once','at':(NOW+timedelta(seconds=60)).isoformat()} if kind=='once' else {'kind':'interval','intervalSec':60}
    source(sources,schedule=spec,misfirePolicy=policy,graceSec=1)
    events=EventStore(tmp_path/'e');events.initialize()
    await Runtime(sources,lambda event:store_admit(events,event),[]).tick({},at=NOW+timedelta(seconds=600))
    item=sources.list()[0]
    assert item['lastOutcome']==expected
    assert events.stats()['events']==int(expected=='published')
    if kind=='interval':assert item['nextFireAt']==(NOW+timedelta(seconds=660)).isoformat()


async def service(tmp_path):
    worker=SimpleNamespace(workers={})
    svc=Service(tmp_path/'automation',worker,None)
    svc.engine.actions=Store(tmp_path/'actions');svc.engine.actions.put('actions',action())
    svc.events.initialize();svc.started=True
    return svc


@async_test
async def test_broadcast_independent_failure_skip_disable_unavailable_and_mutation(tmp_path,monkeypatch):
    svc=await service(tmp_path)
    calls=[]
    async def execute(action,args,event,progress):
        calls.append(event['payload']['runningCount']);event['payload']['runningCount']=999
        if len(calls)==1:raise RuntimeError('independent effect failure')
        return {'ok':True}
    svc.engine.execute=execute
    for tid in ('first','second','skipped','disabled','unavailable','filtered'):
        item=trigger(tid)
        if tid=='disabled':item['enabled']=False
        if tid=='unavailable':item['eventTypes'].append('missing.dependency')
        if tid=='skipped':item['condition']={'field':'workers.runningCount','op':'gt','value':5}
        if tid=='filtered':item['eventFilter']={'payload.runningCount':9}
        svc.ledger.put(item,0,True)
    try:
        event=svc.bus.prepare(svc.core,'workers.all_idle',{'runningCount':0},'broadcast')
        await svc.accept(event)
        await svc.deliver(svc.events.pending()[0])
        rows=await settled(svc.engine,4)
        assert sorted(r['status'] for r in rows)==['failed','skipped','skipped','succeeded']
        assert calls==[0,0] and event['payload']['runningCount']==0
        receipts=svc.events.subscription_receipts('broadcast')
        assert len(receipts)==6 and receipts['trigger:disabled']['status']=='disabled'
        assert receipts['trigger:unavailable']['status']=='unavailable'
        await svc.deliver(svc.events.pending()[0])
        assert calls==[0,0]
        page=await svc.event_page(0,20,True)
        assert page['events'][0]['subscriptions']['trigger:first']['execution']['status']=='failed'
    finally:
        await svc.engine.close();await svc.bus.close()


@async_test
async def test_one_subscription_persistence_failure_does_not_block_another(tmp_path,monkeypatch):
    svc=await service(tmp_path)
    for tid in ('broken','healthy'):svc.ledger.put(trigger(tid),0,True)
    calls=[]
    async def execute(*args):calls.append(1);return {'ok':True}
    svc.engine.execute=execute
    original=svc.ledger.append
    def append(run):
        if run['triggerId']=='broken':raise OSError('owned single subscription write fault')
        return original(run)
    monkeypatch.setattr(svc.ledger,'append',append)
    try:
        await svc.accept(svc.bus.prepare(svc.core,'workers.all_idle',{'runningCount':0},'partial'))
        with pytest.raises(ValueError):await svc.deliver(svc.events.pending()[0])
        await settled(svc.engine,1)
        assert calls==[1]
        assert svc.events.subscription_receipts('partial')['trigger:healthy']['status']=='admitted'
        monkeypatch.setattr(svc.ledger,'append',original)
        await svc.deliver(svc.events.pending()[0]);await settled(svc.engine,2)
        assert calls==[1,1]
    finally:
        await svc.engine.close();await svc.bus.close()


@async_test
async def test_time_event_to_frozen_chain_end_to_end_without_service(tmp_path):
    svc=await service(tmp_path);sources=Sources(tmp_path/'scheduler');item=source(sources)
    for aid in ('one','two'):svc.engine.actions.put('actions',_action(aid))
    steps=_steps();steps[0]['arguments']['value']={'from':'event','path':'payload.scheduleId'}
    steps[1]['arguments']['value']={'from':'step','stepId':'first','path':'answer'}
    t={**trigger(),'eventTypes':[EVENT_TYPE],'eventFilter':{'payload.scheduleId':item['id']},'actionId':'one','steps':steps}
    svc.ledger.put(t,0,True)
    calls=[]
    async def execute(action,args,*unused):
        calls.append((action['id'],args['value']));return {'ok':True,'structuredContent':{'answer':args['value']+'!'}}
    svc.engine.execute=execute
    try:
        await Runtime(sources,svc.accept_time,[]).tick({},at=NOW+timedelta(seconds=60))
        row=svc.events.pending()[0]
        assert row['event']['id']==row['event']['payload']['eventId']
        assert sources.list()[0]['publishCursor']['eventId']==row['event']['id']
        await svc.deliver(row);runs=await settled(svc.engine,1)
        assert runs[0]['status']=='succeeded' and calls==[('one',item['id']),('two',item['id']+'!')]
    finally:
        await svc.engine.close();await svc.bus.close()


def test_source_revision_disable_and_identity_are_explicit(tmp_path):
    sources=Sources(tmp_path);item=source(sources)
    next_fire=item['nextFireAt']
    updated=sources.put({'name':'renamed','schedule':item['schedule'],'enabled':False},1,item['id'],at=NOW)
    assert updated['nextFireAt']==next_fire and updated['revision']==2
    assert sources.reserve(item['id'],NOW+timedelta(seconds=60)) is None
    with pytest.raises(Conflict):sources.put({'name':'stale','schedule':item['schedule']},1,item['id'],at=NOW)
    with pytest.raises(ValueError):sources.put({'name':'forged','schedule':item['schedule'],'owner':'legacy'},0,at=NOW)
    sources.delete(item['id'],2)
    assert not sources.list()


@async_test
async def test_unified_pass_has_separate_legacy_and_event_owners(tmp_path,monkeypatch):
    from packages.core import background_jobs as jobs
    from packages.jobs.action_runtime import Runtime as JobRuntime
    from packages.scheduler import store,engine
    root=tmp_path/'jobs'
    monkeypatch.setenv('PAN_SCHEDULER_DIR',str(root))
    monkeypatch.setattr(jobs,'DEFAULT_ROOT',root)
    monkeypatch.setattr(engine,'scheduler_config',lambda:{'enabled':True,'max_concurrent_dispatch':5})
    legacy=store.create_task({'name':'legacy','text':'old','target_session_id':'selected',
        'enabled':True,'schedule':{'kind':'once','at':(datetime.now()+timedelta(seconds=60)).isoformat()}})
    old=store._job_for_task(legacy['id'])
    old['schedule'][0]['nextFireAt']=(datetime.now()-timedelta(seconds=1)).isoformat(timespec='seconds')
    jobs._write_job(jobs._job_path(old['jobId'],root),old)
    svc=await service(tmp_path)
    runtime=JobRuntime(root,svc.engine.actions,None);await runtime.start()
    sources=Sources(root)
    item=source(sources,schedule={'kind':'once','at':(NOW+timedelta(seconds=60)).isoformat()})
    # Make its real next tick due without changing production clock functions.
    pending=sources.reserve(item['id'],NOW+timedelta(seconds=60))
    runtime.time_events=Runtime(sources,svc.accept_time,[])
    calls=[]
    async def execute(job,*args,**kw):calls.append(job['jobId']);return {'status':'completed'}
    monkeypatch.setattr(jobs,'_run_job_action',execute)
    try:
        await engine.tick_once();await engine.tick_once()
        assert len(calls)==1 and calls[0]==store._job_for_task(legacy['id'])['jobId']
        assert svc.events.stats()['events']==1
        assert svc.events.page()['events'][0]['event']['id']==pending['event']['id']
        assert all(j.get('jobId')!=item['id'] for j in jobs.list_jobs(root))
    finally:
        await runtime.close();await svc.engine.close();await svc.bus.close()


@async_test
async def test_source_http_user_auth_and_revision_without_listening(tmp_path,monkeypatch):
    import httpx
    from fastapi import FastAPI
    from packages.scheduler.event_api import router
    from packages.automation.auth import UserAuth
    monkeypatch.setenv('PAN_SCHEDULER_DIR',str(tmp_path/'scheduler'))
    auth=UserAuth(tmp_path/'auth');token=auth.login(auth.key())
    app=FastAPI();app.state.automation=SimpleNamespace(auth=auth);app.include_router(router,prefix='/api/scheduler')
    cfg={'name':'draft','schedule':{'kind':'interval','intervalSec':60}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://owned') as client:
        assert (await client.post('/api/scheduler/event-sources',json={'config':cfg,'expectedRevision':0})).status_code==401
        headers={'Authorization':'Bearer '+token}
        created=await client.post('/api/scheduler/event-sources',json={'config':cfg,'expectedRevision':0},headers=headers)
        assert created.status_code==200,created.text
        item=created.json()['source'];assert not item['enabled'] and item['owner']==OWNER
        path='/api/scheduler/event-sources/'+item['id']
        assert (await client.put(path,json={'config':cfg,'expectedRevision':0},headers=headers)).status_code==409
        assert (await client.delete(path+'?expectedRevision=1',headers=headers)).status_code==200


@async_test
async def test_unavailable_after_admission_retries_only_waiting_subscription(tmp_path):
    svc=await service(tmp_path)
    plugin=svc.bus.activate('plugin','extra');svc.bus.register(plugin,'extra.ready',{'type':'object'})
    for tid in ('healthy','waiting'):
        t=trigger(tid)
        if tid=='waiting':t['eventTypes'].append('extra.ready')
        svc.ledger.put(t,0,True)
    calls=[]
    async def execute(*args):calls.append(1);return {'ok':True}
    svc.engine.execute=execute
    try:
        await svc.accept(svc.bus.prepare(svc.core,'workers.all_idle',{'runningCount':0},'availability'))
        svc.bus.definitions['extra.ready']['available']=False
        with pytest.raises(ValueError):await svc.deliver(svc.events.pending()[0])
        await settled(svc.engine,1);assert len(calls)==1
        svc.bus.definitions['extra.ready']['available']=True
        await svc.deliver(svc.events.pending()[0]);await settled(svc.engine,2)
        assert len(calls)==2
    finally:
        await svc.engine.close();await svc.bus.close()


@async_test
async def test_cursor_commit_ack_failure_is_recoverable_and_blocks_deletion(tmp_path):
    sources=Sources(tmp_path/'s')
    item=source(sources,schedule={'kind':'once','at':(NOW+timedelta(seconds=60)).isoformat()})
    events=EventStore(tmp_path/'e');events.initialize()
    async def lost_ack(eid):raise OSError('owned acknowledgement write fault')
    await Runtime(sources,lambda event:store_admit(events,event),[],lost_ack).tick({},at=NOW+timedelta(seconds=60))
    assert sources.list()[0]['publishCursor'] and not sources.list()[0]['enabled']
    with pytest.raises(Conflict):sources.delete(item['id'],1)
    await Runtime(sources,lambda event:store_admit(events,event),[],lambda eid:asyncio.to_thread(events.release_pin,eid)).tick({},at=NOW+timedelta(seconds=61))
    assert events.stats()['events']==1 and sources.list()[0]['publishCursor']['eventStoreReleased']
    sources.delete(item['id'],1)


def test_source_revisions_and_due_points_determine_identity(tmp_path):
    sources=Sources(tmp_path)
    item=source(sources)
    pending=sources.reserve(item['id'],NOW+timedelta(seconds=60))
    assert sources.reserve(item['id'],NOW+timedelta(days=1))==pending
    sources.advance(item['id'],pending['event']['id'])
    edited=sources.put({'name':'edited','enabled':True,'schedule':item['schedule']},1,item['id'],at=NOW+timedelta(seconds=61))
    second=sources.reserve(item['id'],NOW+timedelta(seconds=120))
    assert pending['event']['id']!=second['event']['id']
    assert second['event']['payload']['scheduleRevision']==edited['revision']==2
    assert second['event']['payload']['scheduledAt']==(NOW+timedelta(seconds=120)).isoformat()


@async_test
async def test_scheduler_storage_failure_does_not_escape_unified_pass(tmp_path,monkeypatch):
    sources=Sources(tmp_path)
    def broken():raise OSError('owned source database failure')
    monkeypatch.setattr(sources,'list',broken)
    diagnostics=[]
    await Runtime(sources,None,diagnostics).tick({})
    assert diagnostics[0]['phase']=='scheduler-event-store'


@async_test
async def test_event_execution_results_require_existing_read_authorization(tmp_path):
    import httpx
    from fastapi import FastAPI
    from packages.automation.api import install
    svc=await service(tmp_path)
    app=FastAPI();app.state.automation=svc
    install(SimpleNamespace(app=app))
    token=svc.auth.login(svc.auth.key())
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://owned') as client:
            assert (await client.get('/api/automation/events')).status_code==200
            assert (await client.get('/api/automation/events?includeExecution=true')).status_code==401
            assert (await client.get('/api/automation/events?includeExecution=true',headers={'Authorization':'Bearer '+token})).status_code==200
    finally:
        await svc.engine.close();await svc.bus.close()
