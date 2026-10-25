"""Owned-profile composition, lifecycle, retry and real downstream admission."""
import asyncio
import copy
from datetime import datetime

import pytest

from packages.automation.job_links import Links
from packages.jobs.action_runtime import Runtime as Jobs
from packages.scheduler.time_events import Sources,Runtime as Timers
from packages.jobs.action_templates import Conflict
from packages.automation.store import Conflict as LedgerConflict
from tests.test_scheduler_time_events import service
from tests.test_job_action_chain import _action,_config,_steps
from tests.test_automation_core import async_test,settled,until


async def setup(tmp_path):
    svc=await service(tmp_path)
    for aid in ('one','two'):svc.engine.actions.put('actions',_action(aid))
    async def pause(*args):return {'ok':True}
    jobs=Jobs(tmp_path/'jobs',svc.engine.actions,pause);await jobs.start()
    sources=Sources(tmp_path/'jobs')
    svc.job_runtime=jobs;svc.engine.job_runtime=jobs
    svc.links=Links(jobs,svc.ledger,sources)
    jobs.time_events=Timers(sources,svc.accept_time,[],source_ready=svc.links.source_ready)
    return svc,jobs,sources


def request(key='operation_first',**kw):
    return {'key':key,'config':_config(_steps()),'schedulers':[{'linkId':'time1','mode':'new','config':{
        'name':'Daily review','schedule':{'kind':'interval','intervalSec':3600},'enabled':True}}],**kw}


async def close(svc,jobs):
    await svc.engine.close();await jobs.close();await svc.bus.close()


@async_test
async def test_new_flow_idempotency_and_existing_flow_do_not_merge_sources(tmp_path):
    svc,jobs,sources=await setup(tmp_path)
    try:
        raw=request();first=await svc.links.compose(raw)
        second=await svc.links.compose(raw)
        assert first['job']['jobId']==second['job']['jobId'] and len(sources.list())==1
        source=sources.list()[0]
        shared=request('operation_existing',schedulers=[{'linkId':'shared','mode':'existing','sourceId':source['id'],'sourceRevision':source['revision']}])
        other=await svc.links.compose(shared)
        assert other['job']['jobId']!=first['job']['jobId'] and len(sources.list())==1
        assert len(svc.links.references(source['id']))==2
        await svc.links.compose(request('operation_separate'))
        assert len(sources.list())==2  # Same rule/name is not a merge key.
    finally:await close(svc,jobs)


@async_test
@pytest.mark.parametrize('failure',['trigger','activation'])
async def test_failure_quarantines_drafts_and_retry_reuses_objects(tmp_path,monkeypatch,failure):
    svc,jobs,sources=await setup(tmp_path)
    raw=request()
    original=svc.ledger.put if failure=='trigger' else sources.composition_enabled
    once=False
    def fail(*args,**kw):
        nonlocal once
        if not once and (failure=='trigger' or args[1] is True):
            once=True;raise OSError('owned partial setup fault')
        return original(*args,**kw)
    monkeypatch.setattr(svc.ledger,'put',fail) if failure=='trigger' else monkeypatch.setattr(sources,'composition_enabled',fail)
    try:
        with pytest.raises(OSError):await svc.links.compose(raw)
        assert len(jobs.list())==1 and len(sources.list())==1
        assert not any(j['enabled'] for j in jobs.list())
        assert not any(t['enabled'] for t in svc.ledger.read()['triggers'].values())
        assert not any(s['enabled'] for s in sources.list())
        await svc.links.recover()
        result=await svc.links.compose(raw)
        assert result['job']['enabled'] and len(jobs.list())==1 and len(sources.list())==1
        assert len(svc.ledger.read()['triggers'])==1 and sources.list()[0]['enabled']
    finally:await close(svc,jobs)


@async_test
async def test_job_disable_and_delete_preserve_shared_source_and_owner_is_protected(tmp_path):
    svc,jobs,sources=await setup(tmp_path)
    try:
        result=await svc.links.compose(request());job=result['job'];source=sources.list()[0]
        t=next(iter(svc.ledger.read()['triggers'].values()))
        assert t['ownerJobId']==job['jobId'] and t['targetJobId']==job['jobId']
        with pytest.raises(LedgerConflict):svc.ledger.set_enabled(t['id'],False,t['revision'],{'type':'user'})
        changed=request('operation_disable',jobId=job['jobId'],expectedRevision=job['configRevision'],
            config={**job['config'],'enabled':False},schedulers=[{'linkId':'time1','mode':'existing','sourceId':source['id'],'sourceRevision':source['revision']}])
        result=await svc.links.compose(changed)
        assert sources.list()[0]['enabled'] and not result['job']['enabled']
        with pytest.raises(Conflict):sources.delete(source['id'],source['revision'])
        jobs.delete(job['jobId'],result['job']['configRevision'])
        assert not svc.ledger.read()['triggers'] and sources.list()[0]['id']==source['id']
        assert not sources.references(source['id'])
    finally:await close(svc,jobs)


@async_test
async def test_job_and_source_revision_conflicts_are_explicit(tmp_path):
    svc,jobs,sources=await setup(tmp_path)
    try:
        result=await svc.links.compose(request());job=result['job'];source=sources.list()[0]
        with pytest.raises(Conflict,match='Job revision'):
            await svc.links.compose(request('operation_stale_job',jobId=job['jobId'],expectedRevision=0))
        with pytest.raises(Conflict,match='Scheduler revision'):
            await svc.links.compose(request('operation_stale_source',schedulers=[{'linkId':'time1','mode':'existing','sourceId':source['id'],'sourceRevision':0}]))
        assert sources.list()[0]['enabled']
    finally:await close(svc,jobs)


@async_test
async def test_managed_trigger_launches_chain_twice_without_direct_event_subscription(tmp_path):
    svc,jobs,sources=await setup(tmp_path)
    try:
        result=await svc.links.compose(request());job=result['job'];source=sources.list()[0]
        assert jobs.get(job['jobId'])['config']['trigger']['kind']=='manual'
        assert jobs.event_targets('scheduler.time.v1')==[]
        for number in (1,2):
            source=sources.list()[0]
            await jobs.time_events.tick({},at=datetime.fromisoformat(source['nextFireAt']))
            row=svc.events.pending()[-1]
            await svc.deliver(row)
            await settled(svc.engine,number)
            async def done():return jobs.get(job['jobId'])['runCount']==number and not jobs.active(jobs.get(job['jobId']))
            await until(done)
            assert jobs.get(job['jobId'])['lastStatus']=='completed' and jobs.get(job['jobId'])['enabled']
            svc.events.delivery(row['event']['id'],done=True)
    finally:await close(svc,jobs)


@async_test
async def test_persistent_rollback_write_failure_cannot_publish_or_run_half_setup(tmp_path,monkeypatch):
    svc,jobs,sources=await setup(tmp_path)
    original_checkpoint=svc.links.checkpoint
    original_mutate=svc.ledger.mutate
    failed=False
    def checkpoint(key,**fields):
        nonlocal failed
        if fields.get('status')=='complete':failed=True;raise OSError('owned final receipt write fault')
        return original_checkpoint(key,**fields)
    def mutate(change):
        if failed:raise OSError('owned persistent rollback write fault')
        return original_mutate(change)
    monkeypatch.setattr(svc.links,'checkpoint',checkpoint);monkeypatch.setattr(svc.ledger,'mutate',mutate)
    try:
        with pytest.raises(OSError):await svc.links.compose(request())
        job=jobs.list()[0];assert not job['enabled']
        with pytest.raises(Conflict,match='setup incomplete'):await jobs.launch(job['jobId'])
        source=sources.list()[0]
        await jobs.time_events.tick({},at=datetime.fromisoformat(source['nextFireAt']))
        assert svc.events.stats()['events']==0
        monkeypatch.setattr(svc.links,'checkpoint',original_checkpoint);monkeypatch.setattr(svc.ledger,'mutate',original_mutate)
        await svc.links.recover();result=await svc.links.compose(request())
        assert result['job']['enabled'] and len(jobs.list())==1 and len(sources.list())==1
    finally:await close(svc,jobs)


@async_test
async def test_multiple_links_retain_trigger_ids_and_only_remove_detached_link(tmp_path):
    svc,jobs,sources=await setup(tmp_path)
    try:
        raw=request();raw['schedulers'].append({**copy.deepcopy(raw['schedulers'][0]),'linkId':'time2'})
        result=await svc.links.compose(raw);job=result['job']
        before=svc.ledger.read()['triggers'];assert len(before)==2 and len(sources.list())==2
        first=next(t for t in before.values() if t['ownerLinkId']=='time1')
        source=next(s for s in sources.list() if s['id']==first['eventFilter']['payload.scheduleId'])
        changed=request('operation_detach',jobId=job['jobId'],expectedRevision=job['configRevision'],config=job['config'],schedulers=[{'linkId':'time1','mode':'existing','sourceId':source['id'],'sourceRevision':source['revision']}])
        await svc.links.compose(changed)
        after=svc.ledger.read()['triggers'];assert list(after)==[first['id']] and len(sources.list())==2
    finally:await close(svc,jobs)


@async_test
async def test_disabling_source_does_not_cancel_admitted_job_chain(tmp_path):
    svc,jobs,sources=await setup(tmp_path)
    entered,release=asyncio.Event(),asyncio.Event()
    async def effect(*args):entered.set();await release.wait();return {'ok':True}
    jobs.pause=effect
    try:
        result=await svc.links.compose(request());source=sources.list()[0]
        await jobs.time_events.tick({},at=datetime.fromisoformat(source['nextFireAt']))
        await svc.deliver(svc.events.pending()[0]);await asyncio.wait_for(entered.wait(),3)
        current=sources.list()[0]
        sources.put({k:current[k] for k in ('name','schedule','misfirePolicy','graceSec')}|{'enabled':False},current['revision'],current['id'])
        release.set()
        async def done():return jobs.get(result['job']['jobId']).get('lastStatus')=='completed'
        await until(done)
        assert not sources.list()[0]['enabled']
    finally:release.set();await close(svc,jobs)


@async_test
async def test_composition_http_auth_owner_protection_impact_and_creation_retry(tmp_path,monkeypatch):
    import httpx
    from types import SimpleNamespace
    from fastapi import FastAPI
    from packages.automation.api import install
    from packages.scheduler.api import router
    svc,jobs,sources=await setup(tmp_path)
    monkeypatch.setenv('PAN_SCHEDULER_DIR',str(jobs.root))
    app=FastAPI();app.state.automation=svc;install(SimpleNamespace(app=app));app.include_router(router)
    token=svc.auth.login(svc.auth.key());headers={'Authorization':'Bearer '+token}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://owned') as client:
            assert (await client.post('/api/automation/jobs/compose',json=request())).status_code==401
            created=await client.post('/api/automation/jobs/compose',json=request(),headers=headers)
            assert created.status_code==200,created.text
            assert (await client.post('/api/automation/jobs/compose',json={**request(),'config':{**request()['config'],'name':'different'}},headers=headers)).status_code==409
            t=next(iter(svc.ledger.read()['triggers'].values()))
            assert (await client.post('/api/automation/triggers/'+t['id']+'/lifecycle',json={'enabled':False,'expectedRevision':t['revision']},headers=headers)).status_code==409
            listing=(await client.get('/api/scheduler/event-sources',headers=headers)).json()['sources'][0]
            assert listing['references'][0]['jobId']==created.json()['job']['jobId']
            svc.ledger.put(dict(id='another',name='Other reader',enabled=False,eventTypes=['scheduler.time.v1'],eventFilter={'payload.scheduleId':listing['id']},actionId='one',arguments={}),0,True)
            config={k:listing[k] for k in ('name','schedule','enabled','misfirePolicy','graceSec')}
            edit=await client.put('/api/scheduler/event-sources/'+listing['id'],json={'config':config,'expectedRevision':listing['revision'],'expectedReferences':listing['referencesVersion']},headers=headers)
            assert edit.status_code==409
            standalone={'config':{**config,'enabled':False},'expectedRevision':0,'creationKey':'source_retry_key'}
            a=await client.post('/api/scheduler/event-sources',json=standalone,headers=headers)
            b=await client.post('/api/scheduler/event-sources',json=standalone,headers=headers)
            assert a.status_code==b.status_code==200 and a.json()['source']['id']==b.json()['source']['id']
            snapshot=(await client.get('/api/automation',headers=headers)).json()
            assert 'linkOperations' not in snapshot
    finally:await close(svc,jobs)


@async_test
async def test_existing_source_failure_retry_does_not_disable_shared_source(tmp_path,monkeypatch):
    svc,jobs,sources=await setup(tmp_path)
    try:
        await svc.links.compose(request());shared=sources.list()[0]
        raw=request('existing_retry_key',schedulers=[{'linkId':'shared','mode':'existing','sourceId':shared['id'],'sourceRevision':shared['revision']}])
        original=svc.ledger.put
        def fail(*args,**kwargs):raise OSError('owned managed Trigger draft failure')
        monkeypatch.setattr(svc.ledger,'put',fail)
        with pytest.raises(OSError):await svc.links.compose(raw)
        assert sources.list()[0]['enabled'] and len(sources.list())==1
        monkeypatch.setattr(svc.ledger,'put',original)
        result=await svc.links.compose(raw)
        assert result['job']['enabled'] and len(jobs.list())==2 and len(sources.list())==1
    finally:await close(svc,jobs)


@async_test
async def test_existing_action_job_timing_is_preserved_and_not_silently_migrated(tmp_path):
    svc,jobs,sources=await setup(tmp_path)
    try:
        original=jobs.create(_config(_steps(),enabled=False),True)
        cfg={**original['config'],'name':'Edited chain'}
        result=await svc.links.compose(request('preserve_owner_key',jobId=original['jobId'],expectedRevision=1,config=cfg,schedulers=[]))
        assert result['job']['config']['trigger']==original['config']['trigger']
        assert result['job']['config']['schedule']==original['config']['schedule']
        assert not sources.list() and not svc.ledger.read()['triggers']
        with pytest.raises(Conflict,match='ownership'):
            await svc.links.compose(request('do_not_migrate_key',jobId=original['jobId'],expectedRevision=result['job']['configRevision'],config=result['job']['config']))
    finally:await close(svc,jobs)


@async_test
async def test_job_target_mapping_is_frozen_per_admission_and_authorization_still_applies(tmp_path,monkeypatch):
    from packages.actions import executor
    svc,jobs,sources=await setup(tmp_path)
    calls=[]
    async def execute(action,args,*unused):calls.append(args['value']);return {'ok':True}
    monkeypatch.setattr(executor,'execute_dynamic',execute)
    cfg=_config([{'stepId':'one','actionId':'one','arguments':{'value':{'from':'input','path':'message'}}}],arguments={'message':'base'})
    cfg['trigger']={'kind':'manual'}
    try:
        target=jobs.create(cfg,True)
        svc.ledger.put(dict(id='mapped',name='Mapped Job',eventTypes=['workers.all_idle'],enabled=True,actionId='one',targetJobId=target['jobId'],arguments={'message':{'from':'literal','value':'mapped'}}),0,True)
        ev=svc.bus.prepare(svc.core,'workers.all_idle',{'runningCount':0},'mapped_event')
        await svc.accept(ev);await svc.deliver(svc.events.pending()[0]);await settled(svc.engine,1)
        async def done():return jobs.get(target['jobId']).get('lastStatus')=='completed'
        await until(done)
        assert calls==['mapped'] and jobs.get(target['jobId'])['config']['arguments']=={'message':'base'}
        # A revoked delegated target never obtains user-owned Job authority.
        svc.ledger.put(dict(id='revoked',name='Revoked target',eventTypes=['workers.all_idle'],enabled=True,actionId='one',targetJobId=target['jobId'],arguments={}),0,True,authority={'type':'agent'})
        svc.engine.authorization=lambda *args:(False,2)
        await svc.engine.match(svc.bus.prepare(svc.core,'workers.all_idle',{'runningCount':0},'denied_event'))
        await settled(svc.engine,3)
        async def both_jobs_done():return jobs.get(target['jobId'])['runCount']==2 and not jobs.active(jobs.get(target['jobId']))
        await until(both_jobs_done)
        assert len(calls)==2  # Only the independently authorized mapped Trigger ran.
        assert next(r for r in svc.ledger.read()['runs'].values() if r['triggerId']=='revoked')['status']=='failed'
    finally:await close(svc,jobs)


@async_test
async def test_setup_resumes_with_rebuilt_stores_after_profile_owner_restart(tmp_path,monkeypatch):
    svc,jobs,sources=await setup(tmp_path)
    original=svc.ledger.put
    def fail(*args,**kwargs):raise OSError('owned pre-Trigger commit interruption')
    monkeypatch.setattr(svc.ledger,'put',fail)
    raw=request('disk_restart_key')
    with pytest.raises(OSError):await svc.links.compose(raw)
    jid=jobs.list()[0]['jobId'];sid=sources.list()[0]['id']
    monkeypatch.setattr(svc.ledger,'put',original)
    await close(svc,jobs)
    svc,jobs,sources=await setup(tmp_path)
    try:
        await svc.links.recover()
        assert not jobs.view(jobs.get(jid))['enabled']
        result=await svc.links.compose(raw)
        assert result['job']['jobId']==jid and result['job']['enabled']
        assert [s['id'] for s in sources.list()]==[sid]
        assert len(svc.ledger.read()['triggers'])==1
    finally:await close(svc,jobs)
