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
from tests.test_automation_canonical import canonical, enabled, job_def, subscription, fact, control, USER

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


async def service(tmp_path,execute=None,with_runtime=True):
    from unittest.mock import patch
    from packages.jobs.action_runtime import Runtime as JobRuntime
    worker=SimpleNamespace(workers={})
    svc=Service(tmp_path/'automation',worker,None)
    svc.engine.actions=Store(tmp_path/'actions');svc.engine.actions.put('actions',action())
    svc.events.initialize();svc.started=True
    if not with_runtime:return svc
    runtime=JobRuntime(tmp_path/'jobs-runtime',svc.engine.actions,None);await runtime.start()
    svc.job_runtime=runtime;svc.engine.job_runtime=runtime;runtime.event_available=svc.bus.available
    patched=None
    if execute:
        async def invoke(definition,arguments,event,progress,pause):
            return await execute(definition,arguments,event,progress)
        patched=patch('packages.actions.executor.execute_dynamic',invoke);patched.start()
    close_engine=svc.engine.close
    async def close():
        await close_engine();await runtime.close()
        if patched:patched.stop()
    svc.engine.close=close
    return svc


def test_broadcast_independent_failure_skip_disable_unavailable_and_mutation(canonical, monkeypatch):
    h = canonical
    targets, jobs = {}, {}
    for key in ('first','second','skipped','disabled','unavailable','filtered'):
        job = enabled(h, job_def(h), key=key + '-job')
        fields = {}
        if key == 'skipped': fields['condition'] = {'field':'workers.runningCount','op':'gt','value':5}
        if key == 'unavailable': fields['condition'] = {'field':'plugin.ready','op':'eq','value':True}
        if key == 'filtered': fields['eventFilter'] = {'payload.runningCount':9}
        _, target = subscription(h, job, key=key, **fields)
        if key == 'disabled':
            response = control(h, 'triggers', target, 'disable', 'disable-broadcast-trigger')
            assert response.status_code == 200, response.text
        targets[key], jobs[key] = target, job
    async def reader(key):
        if key == 'workers.runningCount': return 0
        raise ValueError('owned unavailable State provider')
    h.service.state_reader = reader
    h.provider.fail_once = '0'
    original = h.service.admission.admit
    observed = []
    async def mutable(event, target):
        observed.append(event['payload']['runningCount'])
        try:
            return await original(event, target)
        finally:
            event['payload']['runningCount'] = 999
    monkeypatch.setattr(h.service.admission, 'admit', mutable)
    async def scenario():
        svc = h.automation
        svc.started = True
        event = fact(h, 'broadcast')
        await svc.accept(event, svc.core)
        row = svc.events.lookup('broadcast')
        with pytest.raises(ValueError) as unavailable:
            await svc.deliver(row)
        assert 'trigger_temporarily_inactive' in str(unavailable.value)
        assert 'owned unavailable State provider' in str(unavailable.value)
        await h.service.dispatch_once()
        assert observed == [0] * 6 and event['payload']['runningCount'] == 0
        receipts = svc.events.subscription_receipts('broadcast')
        assert len(receipts) == 6
        assert sorted(h.service.final.runs(jobs[k]['id'])[0]['state'] for k in ('first','second')) == ['completed','failed']
        for key in ('disabled','unavailable'):
            assert receipts['trigger:' + targets[key]['id']]['status'] == 'error'
            assert h.service.final.runs(jobs[key]['id']) == []
        for key in ('skipped','filtered'):
            assert receipts['trigger:' + targets[key]['id']]['admissionResult'] == 'skipped'
            assert h.service.final.runs(jobs[key]['id']) == []
        assert [text for text,_ in h.provider.calls] == ['0','0']
        with pytest.raises(ValueError):
            await svc.deliver(row)
        await h.service.dispatch_once()
        assert [text for text,_ in h.provider.calls] == ['0','0']
        page = await svc.event_page(0,20,True)
        by_id = {entry['event']['id']:entry for entry in page['events']}
        for key in ('first','second'):
            link = by_id['broadcast']['subscriptions']['trigger:' + targets[key]['id']]
            assert link['status'] == 'admitted'
            assert h.service.get_run(link['runId'], actor=USER)['jobId'] == jobs[key]['id']
        assert svc.ledger.read()['runs'] == {}
    asyncio.run(scenario())


def test_one_subscription_persistence_failure_does_not_block_another(canonical, monkeypatch):
    h = canonical
    targets, jobs = {}, {}
    for key in ('broken','healthy'):
        jobs[key] = enabled(h, key=key + '-job')
        _, targets[key] = subscription(h, jobs[key], key=key)
    save = h.service._save
    def fault(db, kind, identity, item):
        save(db, kind, identity, item)
        if kind == 'job' and identity == jobs['broken']['id'] and item.get('finalRuntime',{}).get('plans'):
            raise OSError('owned single subscription write fault')
    monkeypatch.setattr(h.service, '_save', fault)
    async def scenario():
        svc = h.automation
        svc.started = True
        await svc.accept(fact(h,'partial'), svc.core)
        row = svc.events.lookup('partial')
        with pytest.raises(ValueError, match='owned single subscription write fault'):
            await svc.deliver(row)
        await h.service.dispatch_once()
        healthy = h.service.final.runs(jobs['healthy']['id'])
        assert len(healthy) == 1 and healthy[0]['state'] == 'completed'
        assert h.service.final.runs(jobs['broken']['id']) == [] and len(h.provider.calls) == 1
        first = copy.deepcopy(h.service.final.get(jobs['healthy']['id']))
        receipts = svc.events.subscription_receipts('partial')
        assert receipts['trigger:' + targets['healthy']['id']]['status'] == 'admitted'
        assert receipts['trigger:' + targets['broken']['id']]['status'] == 'error'
        monkeypatch.setattr(h.service, '_save', save)
        await svc.deliver(row)
        await h.service.dispatch_once()
        assert len(h.provider.calls) == 2
        assert h.service.final.get(jobs['healthy']['id']) == first
        assert h.service.final.runs(jobs['broken']['id'])[0]['state'] == 'completed'
        await svc.deliver(row)
        await h.service.dispatch_once()
        assert len(h.provider.calls) == 2 and svc.ledger.read()['runs'] == {}
    asyncio.run(scenario())


def test_time_event_to_frozen_chain_end_to_end_without_service(canonical):
    h = canonical
    from tests.test_retirement_rev16_behavior import instant
    from packages.scheduler.providers import time_definition
    from tests.test_job_final_creators import config
    from tests.test_action_unification_p4 import plans
    value = job_def(h, steps=2)
    value['steps'][0]['arguments']['text'] = {'from':'event','path':'creatorId'}
    value['steps'][1]['arguments']['text'] = {'from':'step','stepId':'s0','path':'messageId'}
    job = enabled(h, value)
    due = instant(30)
    response = h.client.post('/api/event-creators', json={'definition':time_definition('timer',
        config([{'ruleId':'original','type':'once','at':due}])), 'expectedRevision':0, 'requestKey':'canonical-time-source'})
    assert response.status_code == 201, response.text
    creator = response.json()['result']['object']
    response = control(h, 'event-creators', creator, 'enable', 'enable-canonical-time-source')
    assert response.status_code == 200, response.text
    creator = response.json()['result']['object']
    _, target = subscription(h, job, eventTypes=['scheduler.time.v2'],
        creatorSelector={'mode':'ids','ids':[creator['id']]})
    h.service.refresh_subscriptions()
    asyncio.run(h.service.creator_runtime.tick(at=due))
    assert h.service.final.runs(job['id']) == [] and h.provider.calls == []
    published_event, = h.service.events.page()
    h.service.admit_event(published_event, target)
    runs = h.service.final.runs(job['id'])
    assert len(runs) == 1 and runs[0]['state'] == 'queued' and h.provider.calls == []
    rid = runs[0]['runId']
    event_id = runs[0]['cause']['eventId']
    event = h.service.events.get(event_id)['event']
    assert event['creatorId'] == creator['id'] and event['payload']['scheduledAt'] == due
    assert h.service.creators.get(creator['id'])['producedCount'] == 1
    frozen = copy.deepcopy(plans(h,job['id'])[rid]['frozen'])
    changed = h.client.put('/api/jobs/' + job['id'], json={'definition':job_def(h,steps=1),
        'expectedRevision':job['configRevision'], 'requestKey':'edit-time-chain-after-admission'})
    assert changed.status_code == 200, changed.text
    asyncio.run(h.service.dispatch_once())
    assert h.service.get_run(rid, actor=USER)['state'] == 'completed'
    assert [text for text,_ in h.provider.calls] == [creator['id'],'message_1']
    state = h.service.final.get(job['id'])
    assert len(state['attempts']) == len(state['effects']) == 2
    assert plans(h,job['id'])[rid]['frozen'] == frozen
    asyncio.run(h.service.creator_runtime.tick(at=due))
    asyncio.run(h.service.tick())
    assert h.service.final.get(job['id'])['effects'] == state['effects']
    assert len(h.provider.calls) == 2 and h.automation.ledger.read()['runs'] == {}


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


def test_old_engine_cannot_drive_legacy_or_event_owner():
    import importlib
    with pytest.raises(ModuleNotFoundError) as exc:
        importlib.import_module('packages.scheduler.engine')
    assert exc.value.name == 'packages.scheduler.engine'


def test_unavailable_after_admission_retries_only_waiting_subscription(canonical):
    h = canonical
    jobs = {key:enabled(h,key=key + '-job') for key in ('healthy','waiting')}
    _, healthy = subscription(h,jobs['healthy'],key='healthy')
    _, waiting = subscription(h,jobs['waiting'],key='waiting',
        condition={'field':'plugin.ready','op':'eq','value':True})
    readiness = {'available':False}
    async def reader(key):
        assert key == 'plugin.ready'
        if not readiness['available']:
            raise ValueError('owned unavailable State provider')
        return True
    h.service.state_reader = reader
    async def scenario():
        svc = h.automation
        svc.started = True
        await svc.accept(fact(h,'availability'),svc.core)
        row = svc.events.lookup('availability')
        with pytest.raises(ValueError,match='owned unavailable State provider'):
            await svc.deliver(row)
        await h.service.dispatch_once()
        original = copy.deepcopy(h.service.final.get(jobs['healthy']['id']))
        assert len(original['runs']) == 1 and next(iter(original['runs'].values()))['state'] == 'completed'
        assert h.service.final.runs(jobs['waiting']['id']) == [] and len(h.provider.calls) == 1
        readiness['available'] = True
        await svc.deliver(row)
        await h.service.dispatch_once()
        assert len(h.provider.calls) == 2
        assert h.service.final.get(jobs['healthy']['id']) == original
        waited = h.service.final.runs(jobs['waiting']['id'])
        assert len(waited) == 1 and waited[0]['state'] == 'completed'
        assert waited[0]['cause']['eventId'] == 'availability'
        for target in (healthy,waiting):
            assert svc.events.subscription_receipts('availability')['trigger:' + target['id']]['status'] == 'admitted'
        await svc.deliver(row)
        await h.service.dispatch_once()
        assert len(h.provider.calls) == 2 and svc.ledger.read()['runs'] == {}
    asyncio.run(scenario())


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


def test_event_execution_results_require_existing_read_authorization(canonical):
    import httpx
    h = canonical
    job = enabled(h)
    _, target = subscription(h,job)
    async def scenario():
        svc = h.automation
        svc.started = True
        await svc.accept(fact(h,'authorized-history'),svc.core)
        await svc.deliver(svc.events.lookup('authorized-history'))
        await h.service.dispatch_once()
        receipt = svc.events.subscription_receipts('authorized-history')['trigger:' + target['id']]
        rid = receipt['runId']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(h.app),base_url='http://owned') as client:
            public = await client.get('/api/automation/events')
            assert public.status_code == 200
            assert (await client.get('/api/automation/events?includeExecution=true')).status_code == 401
            assert (await client.get('/api/job-runs/' + rid)).status_code == 401
            headers = {'Authorization':'Bearer ' + h.token}
            history = await client.get('/api/automation/events?includeExecution=true',headers=headers)
            assert history.status_code == 200
            rows = {row['event']['id']:row for row in history.json()['events']}
            assert rows['authorized-history']['subscriptions']['trigger:' + target['id']]['runId'] == rid
            run = await client.get('/api/job-runs/' + rid,headers=headers)
            assert run.status_code == 200 and run.json()['state'] == 'completed'
            assert run.json()['jobId'] == job['id']
        assert len(h.provider.calls) == 1 and svc.ledger.read()['runs'] == {}
    asyncio.run(scenario())
