"""Complete templates, atomic reference admission and ephemeral snapshot recovery."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import threading
from types import SimpleNamespace

import httpx
import pytest

from packages.actions.store import Store, validate_action
from packages.core import background_jobs as jobs
from packages.jobs.action_templates import Templates, Conflict, CreationPending, config_value
from packages.jobs.action_runtime import Runtime, RUNTIMES
from tests.test_automation_core import async_test, action, until


def config(**fields):
    return dict(name='Reference Job',actionId='pause',arguments={},enabled=True,
                schedule=[{'kind':'interval','intervalSec':3600}],**fields)


async def setup(root,pause=None):
    calls=[]
    async def effect(sid,args):calls.append(sid);return {'ok':True}
    store=Store(root/'actions');store.put('actions',action())
    rt=Runtime(root/'jobs',store,pause or effect);await rt.start()
    return rt,calls


async def finished(rt,jid,status='completed'):
    async def ready():
        job=await asyncio.to_thread(rt.get,jid)
        task=rt.tasks.get(jid)
        return (job.get('lastExecution',{}).get('status')==status and not job.get('execution')
                and (task is None or task.done()))
    await until(ready)
    return await asyncio.to_thread(rt.get,jid)


@pytest.mark.parametrize('extra',[
    {'text':'extra'}, {'target':{'sessionId':'other'}}, {'api':'/arbitrary'},
    {'arguments':{'text':'extra'}}, {'arguments':[]}, {'enabled':'true'},
    {'schedule':[]}, {'schedule':[{'kind':'interval','intervalSec':1,'unknown':True}]},
    {'schedule':[{'kind':'once'}]}, {'maxRuns':True}])
def test_job_fields_are_declared_only(tmp_path,extra):
    store=Store(tmp_path/'actions');store.put('actions',action())
    with pytest.raises(ValueError):config_value({**config(),**extra},store.read())


def test_complete_fields_defaults_and_template_revision(tmp_path):
    store=Store(tmp_path/'actions')
    a=action();a['input_schema']={'type':'object','properties':{'count':{'type':'integer'},'tag':{'type':'string','default':'fixed'}},'required':['count'],'additionalProperties':False}
    store.put('actions',a);templates=Templates(store)
    with pytest.raises(ValueError):templates.put({'name':'hole','config':config()},0)
    t=templates.put({'name':'complete','config':{**config(),'enabled':False,'arguments':{'count':2}}},0)
    assert not t['enabled'] and t['config']['arguments']=={'count':2,'tag':'fixed'}
    with pytest.raises(PermissionError):templates.put({k:v for k,v in {**t,'enabled':True}.items() if k in {'id','name','enabled','config'}},1)
    with pytest.raises(Conflict):templates.put({'id':t['id'],'name':'stale','config':t['config']},0,True)


@async_test
async def test_instantiate_copy_revision_display_only_and_idempotence(tmp_path):
    rt,_=await setup(tmp_path)
    try:
        t=rt.templates.put({'name':'complete','enabled':True,'config':config()},0,True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:rt.instantiate(t['id'],1,'key',{'name':'instance'}),range(8)))
        assert len({r['jobId'] for r in results})==1
        job=results[0];assert job['templateRevision']==1 and job['config']['name']=='instance'
        updated=rt.templates.put({'id':t['id'],'name':'edited','enabled':True,'config':{**config(),'name':'changed'}},1,True)
        assert updated['revision']==2 and rt.get(job['jobId'])['config']['name']=='instance'
        assert rt.instantiate(t['id'],1,'key',{'name':'instance'})['jobId']==job['jobId']
        with pytest.raises(Conflict):rt.instantiate(t['id'],2,'key')
        with pytest.raises(ValueError):rt.instantiate(t['id'],2,'other',{'arguments':{}})
        rt.templates.delete(t['id'],2)
        rt.delete(job['jobId'],1)
        with pytest.raises(KeyError):rt.instantiate(t['id'],1,'key',{'name':'instance'})
        assert not jobs.list_jobs(rt.root)
    finally:await rt.close()


@async_test
async def test_reserved_creation_recovers_after_template_delete_and_write_failure(tmp_path,monkeypatch):
    rt,_=await setup(tmp_path)
    t=rt.templates.put({'name':'complete','enabled':True,'config':config()},0,True)
    original=jobs._create
    def fail(*args,**kwargs):raise OSError('injected Job registry write failure')
    monkeypatch.setattr(jobs,'_create',fail)
    try:
        with pytest.raises(CreationPending):rt.instantiate(t['id'],1,'durable-intent')
        assert list(rt.actions.read()['jobInstantiations'].values())[0]['phase']=='reserved'
        rt.templates.delete(t['id'],1)
        monkeypatch.setattr(jobs,'_create',original)
        rt.recover()
        created=rt.instantiate(t['id'],1,'durable-intent')
        assert created['templateRevision']==1 and len(jobs.list_jobs(rt.root))==1
        receipt=list(rt.actions.read()['jobInstantiations'].values())[0]
        assert receipt['phase']=='created' and 'config' not in receipt
    finally:monkeypatch.setattr(jobs,'_create',original);await rt.close()


@async_test
async def test_template_edit_delete_while_job_write_blocked_uses_admitted_copy(tmp_path,monkeypatch):
    rt,_=await setup(tmp_path);entered,release=threading.Event(),threading.Event()
    t=rt.templates.put({'name':'complete','enabled':True,'config':config()},0,True)
    original=jobs._create
    def blocked(*args,**kwargs):entered.set();assert release.wait(3);return original(*args,**kwargs)
    monkeypatch.setattr(jobs,'_create',blocked)
    task=asyncio.create_task(asyncio.to_thread(rt.instantiate,t['id'],1,'interleaved'))
    try:
        assert await asyncio.to_thread(entered.wait,2)
        changed=rt.templates.put({'id':t['id'],'name':'edited','enabled':True,'config':{**config(),'name':'new'}},1,True)
        rt.templates.delete(t['id'],changed['revision'])
        rt.actions.put('actions',{**action(),'id':'unrelated'})
        release.set();job=await task
        assert job['config']['name']=='Reference Job' and job['templateRevision']==1
    finally:release.set();await asyncio.gather(task,return_exceptions=True);await rt.close()


@async_test
async def test_delete_before_snapshot_atomic_admission_blocks_all_effects(tmp_path,monkeypatch):
    rt,calls=await setup(tmp_path);job=rt.create(config(),True)
    entered,release=threading.Event(),threading.Event();original=rt.templates.snapshot
    def blocked(*args):entered.set();assert release.wait(3);return original(*args)
    monkeypatch.setattr(rt.templates,'snapshot',blocked)
    try:
        await rt.launch(job['jobId'],1);assert await asyncio.to_thread(entered.wait,2)
        with pytest.raises(PermissionError):rt.actions.delete('actions','pause')
        rt.actions.delete('actions','pause',authorized=True,confirm_references=True)
        view=rt.view(rt.get(job['jobId']))
        assert view['status']=='running' and view['integrity']['status']=='incomplete'
        assert view['integrity']['issues'][0]['position']=='/config/actionId'
        release.set();await finished(rt,job['jobId'],'failed')
        assert not calls and not rt.actions.read().get('jobExecutions')
    finally:release.set();await rt.close()


@pytest.mark.parametrize('mutation',['edit','delete'])
@async_test
async def test_running_snapshot_stable_terminal_cleanup_and_no_auto_restore(tmp_path,mutation):
    entered,release=asyncio.Event(),asyncio.Event();calls=[]
    async def pause(sid,args):calls.append(sid);entered.set();await release.wait();return {'ok':True}
    rt,_=await setup(tmp_path,pause);job=rt.create(config(),True)
    try:
        source_revision=rt.actions.read()['revision']
        await rt.launch(job['jobId'],1);await asyncio.wait_for(entered.wait(),2)
        snap=next(iter(rt.actions.read()['jobExecutions'].values()))
        assert snap['definition']['execution']['selector']['ids']==['selected']
        assert rt.actions.read()['revision']==source_revision
        if mutation=='delete':rt.actions.delete('actions','pause',authorized=True,confirm_references=True)
        rt.actions.put('actions',action(['changed']))
        release.set();done=await finished(rt,job['jobId'])
        assert calls==['selected'] and not rt.actions.read().get('jobExecutions')
        assert done['lastExecution']['digest']==snap['digest'] and 'definition' not in done['lastExecution']
        if mutation=='delete':
            view=rt.view(done);assert view['integrity']['status']=='complete' and view['integrity']['approvalRequired'] and not view['enabled']
            with pytest.raises(Conflict):await rt.launch(job['jobId'],1)
            with pytest.raises(PermissionError):rt.update(job['jobId'],config(),1)
            repaired=rt.update(job['jobId'],config(),1,True)
            assert not repaired['enabled'] and repaired['configRevision']==2
            rt.update(job['jobId'],config(),2,True)
        await rt.launch(job['jobId'],rt.get(job['jobId'])['configRevision'])
        await until(lambda:asyncio.sleep(0,result=len(calls)==2))
        assert calls==['selected','changed']
    finally:release.set();await rt.close()


@async_test
async def test_cancel_exception_shutdown_and_recovery_remove_snapshots(tmp_path):
    for mode in ('cancel','error','shutdown','crash'):
        entered=asyncio.Event();calls=[]
        async def pause(*args):
            calls.append(1);entered.set()
            if mode=='error':raise ValueError('owned failure')
            await asyncio.Event().wait()
        rt,_=await setup(tmp_path/mode,pause);job=rt.create(config(),True)
        try:
            if mode=='crash':
                admitted=rt.claim(job['jobId'],'jexec_crash',1)
                rt.templates.snapshot(admitted,admitted['execution'])
                rt.closed=True;RUNTIMES.pop(str(rt.root));rt.ownership.release();rt.owned=False
                rt=Runtime(rt.root,rt.actions,pause);await rt.start()
                expected='uncertain'
            else:
                await rt.launch(job['jobId'],1);await asyncio.wait_for(entered.wait(),2)
                if mode=='cancel':await rt.cancel(job['jobId'],1);expected='cancelled'
                elif mode=='shutdown':await rt.close();expected='uncertain'
                else:expected='failed'
            await finished(rt,job['jobId'],expected)
            assert not rt.actions.read().get('jobExecutions')
            record=jobs.list_run_records(registry_root=rt.root)[0]
            assert record['status']==expected and 'definition' not in record['result']
        finally:await rt.close()


@async_test
async def test_cleanup_failure_is_visible_cleaning_and_retries_cleanup_only(tmp_path,monkeypatch):
    rt,calls=await setup(tmp_path);job=rt.create(config(),True);original=rt.templates.clear_snapshot
    def fail(*args):raise OSError('injected snapshot cleanup failure')
    monkeypatch.setattr(rt.templates,'clear_snapshot',fail)
    try:
        await rt.launch(job['jobId'],1);await rt.tasks[job['jobId']]
        pending=rt.get(job['jobId'])
        assert pending['status']=='cleaning' and pending['executionFinalization']['status']=='completed'
        assert rt.actions.read()['jobExecutions'] and rt.diagnostics[-1]['phase']=='cleanup'
        monkeypatch.setattr(rt.templates,'clear_snapshot',original)
        await rt.tick({'enabled':False});done=await finished(rt,job['jobId'])
        assert calls==['selected'] and not rt.actions.read()['jobExecutions'] and done['runCount']==1
    finally:monkeypatch.setattr(rt.templates,'clear_snapshot',original);await rt.close()


@async_test
async def test_snapshot_io_does_not_hold_job_registry_or_block_event_loop(tmp_path,monkeypatch):
    rt,calls=await setup(tmp_path);job=rt.create(config(),True)
    entered,release=threading.Event(),threading.Event();original=rt.templates.snapshot
    def blocked(*args):entered.set();assert release.wait(3);return original(*args)
    monkeypatch.setattr(rt.templates,'snapshot',blocked)
    try:
        await rt.launch(job['jobId'],1);assert await asyncio.to_thread(entered.wait,2)
        await asyncio.wait_for(asyncio.sleep(0),.1)
        session=await asyncio.wait_for(asyncio.to_thread(jobs._sessions.create,'unrelated'),1)
        other=await asyncio.wait_for(asyncio.to_thread(jobs.start_message,session.id,'hello',{'type':'once','delaySeconds':60},registry_root=rt.root),1)
        assert other['kind']==jobs.SESSION_MESSAGE_KIND and not calls
        release.set();await finished(rt,job['jobId'])
    finally:release.set();await rt.close()



@async_test
async def test_snapshot_write_or_post_effect_checkpoint_failure_never_replays(tmp_path,monkeypatch):
    import packages.actions.store as module
    original=module.atomic
    for mode in ('snapshot','checkpoint'):
        rt,calls=await setup(tmp_path/mode);job=rt.create(config(),True)
        def atomic(path,state):
            snapshots=state.get('jobExecutions',{})
            bad=bool(snapshots) if mode=='snapshot' else any(s['checkpoint'].get('targetResults') for s in snapshots.values())
            if bad:raise OSError('injected '+mode+' persistence failure')
            return original(path,state)
        monkeypatch.setattr(module,'atomic',atomic)
        try:
            await rt.launch(job['jobId'],1)
            done=await finished(rt,job['jobId'],'failed' if mode=='snapshot' else 'uncertain')
            assert not rt.actions.read().get('jobExecutions')
            assert calls==([] if mode=='snapshot' else ['selected'])
            if mode=='checkpoint':assert not done['enabled']
        finally:monkeypatch.setattr(module,'atomic',original);await rt.close()


@async_test
async def test_terminal_job_write_failure_after_cleanup_recovers_same_run_once(tmp_path,monkeypatch):
    rt,calls=await setup(tmp_path);job=rt.create(config(),True);original=jobs._write_job
    def write(path,value,**kwargs):
        if value.get('lastExecution') and not value.get('execution'):raise OSError('injected terminal Job write failure')
        return original(path,value,**kwargs)
    monkeypatch.setattr(jobs,'_write_job',write)
    try:
        await rt.launch(job['jobId'],1);await rt.tasks[job['jobId']]
        assert rt.get(job['jobId'])['status']=='cleaning' and not rt.actions.read()['jobExecutions']
        monkeypatch.setattr(jobs,'_write_job',original)
        await rt.tick({'enabled':False});await finished(rt,job['jobId'])
        assert calls==['selected'] and len(jobs.list_run_records(registry_root=rt.root))==1
    finally:monkeypatch.setattr(jobs,'_write_job',original);await rt.close()


@async_test
async def test_fixed_template_action_capability_idempotence_and_group_gate(tmp_path):
    from packages.actions.executor import execute_dynamic
    rt,_=await setup(tmp_path)
    try:
        t=rt.templates.put({'name':'fixed','enabled':True,'config':{**config(),'enabled':False}},0,True)
        a={**action(),'id':'instantiate','execution':{'kind':'job_template','templateId':t['id'],'templateRevision':1}}
        with pytest.raises(PermissionError):rt.actions.put('actions',a)
        rt.actions.put('actions',a,authorized=True)
        async def progress(**fields):pass
        progress.checkpoint={'id':'one-logical-run'};progress.instantiate_template=rt.instantiate_action
        one=await execute_dynamic(a,{}, {},progress,rt.pause)
        two=await execute_dynamic(a,{}, {},progress,rt.pause)
        assert one['jobId']==two['jobId'] and not one['jobCompleted'] and len(jobs.list_jobs(rt.root))==1
        with pytest.raises(Exception):await execute_dynamic(a,{'text':'override'}, {},progress,rt.pause)
        rt.actions.put('groups',{'id':'g','name':'g','action_ids':['instantiate']})
        with pytest.raises(ValueError):rt.actions.build('g')
        rt.templates.put({'id':t['id'],'name':'new intent','enabled':True,'config':{**config(),'name':'new','enabled':False}},1,True)
        progress.checkpoint={'id':'a-later-run'}
        with pytest.raises(Conflict):await execute_dynamic(a,{}, {},progress,rt.pause)
        with pytest.raises(ValueError):validate_action({**a,'input_schema':{'type':'object','properties':{'x':{'type':'string'}},'additionalProperties':False}})
    finally:await rt.close()


@async_test
async def test_running_template_instantiation_uses_private_context_after_template_delete(tmp_path,monkeypatch):
    import packages.actions.executor as executor
    rt,_=await setup(tmp_path)
    t=rt.templates.put({'name':'fixed','enabled':True,'config':{**config(),'enabled':False}},0,True)
    a={**action(),'id':'instantiate','execution':{'kind':'job_template','templateId':t['id'],'templateRevision':1}}
    rt.actions.put('actions',a,authorized=True)
    parent=rt.create({**config(),'actionId':'instantiate'},True)
    entered,release=asyncio.Event(),asyncio.Event();original=executor.execute_dynamic
    async def blocked(*args,**kwargs):entered.set();await release.wait();return await original(*args,**kwargs)
    monkeypatch.setattr(executor,'execute_dynamic',blocked)
    try:
        await rt.launch(parent['jobId'],1);await asyncio.wait_for(entered.wait(),2)
        rt.templates.delete(t['id'],1);release.set();done=await finished(rt,parent['jobId'])
        child=rt.get(done['lastExecution']['createdJobId'])
        assert child['templateRevision']==1 and not child['enabled']
        assert not rt.actions.read().get('jobExecutions')
        assert rt.view(done)['integrity']['status']=='incomplete'
    finally:release.set();await rt.close()


@async_test
async def test_reference_graph_cycle_rejected_and_template_restore_does_not_enable(tmp_path):
    rt,_=await setup(tmp_path)
    try:
        t=rt.templates.put({'name':'fixed','enabled':True,'config':config()},0,True)
        loop={**action(),'execution':{'kind':'job_template','templateId':t['id'],'templateRevision':1}}
        with pytest.raises(ValueError,match='cycle'):rt.actions.put('actions',loop,authorized=True)
        rt.actions.delete('actions','pause',authorized=True,confirm_references=True)
        rt.actions.put('actions',action())
        view=rt.templates.list()[0]
        assert view['integrity']['status']=='complete' and view['integrity']['approvalRequired'] and not view['enabled']
        fixed=rt.templates.put({'id':t['id'],'name':'fixed','enabled':True,'config':config()},1,True)
        assert not fixed['enabled'] and fixed['revision']==2
        enabled=rt.templates.put({'id':t['id'],'name':'fixed','enabled':True,'config':config()},2,True)
        assert enabled['enabled']
    finally:await rt.close()


@async_test
async def test_api_permissions_unknown_fields_legacy_aliases_and_repair(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from packages.automation.auth import UserAuth
    from packages.automation.api import install as install_auth
    from packages.actions.api import install as install_actions
    from packages.jobs import action_api,api as legacy
    rt,_=await setup(tmp_path);app=FastAPI();auth=UserAuth(tmp_path/'auth');auth.key()
    # The existing login router uses engine.io; reuse the production settle helper.
    from packages.plugins.lifecycle import io
    app.state.automation=SimpleNamespace(job_runtime=rt,auth=auth,engine=SimpleNamespace(io=io))
    server=SimpleNamespace(app=app,sess=SimpleNamespace(list_all=lambda **kw:[]))
    install_auth(server);install_actions(server);app.include_router(action_api.router);app.include_router(legacy.router)
    monkeypatch.setattr('packages.actions.api.Store',lambda:rt.actions)
    monkeypatch.setattr(legacy,'_job_root',lambda:rt.root)
    monkeypatch.setattr('packages.jobs.pan_bridge.request',lambda *args:None)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned') as client:
            assert (await client.post('/api/jobs/action-jobs',json={'config':config()})).status_code==401
            assert (await client.post('/api/jobs/action-jobs',json={'config':{**config(),'text':'inject'}})).status_code==400
            draft=(await client.post('/api/jobs/action-jobs',json={'config':{**config(),'enabled':False}})).json()['job']
            assert not draft['enabled']
            base='/api/jobs/action-jobs/'+draft['jobId']
            assert (await client.put(base,json={'config':config(),'expectedRevision':1})).status_code==401
            assert (await client.post('/api/jobs/job-templates',json={'item':{'name':'x','enabled':True,'config':config()},'expectedRevision':0})).status_code==401
            token=(await client.post('/api/automation/login',json={'key':auth.key()})).json()['token'];headers={'Authorization':'Bearer '+token}
            t=(await client.post('/api/jobs/job-templates',json={'item':{'name':'fixed','enabled':True,'config':config()},'expectedRevision':0},headers=headers)).json()['item']
            endpoint='/api/jobs/job-templates/'+t['id']+'/instantiate'
            body={'expectedRevision':1,'idempotencyKey':'http-key'}
            assert (await client.post(endpoint,json=body)).status_code==401
            assert (await client.post(endpoint,json={**body,'overrides':{'arguments':{}}},headers=headers)).status_code==400
            first=(await client.post(endpoint,json=body,headers=headers)).json()['job']
            second=(await client.post(endpoint,json=body,headers=headers)).json()['job']
            assert first['jobId']==second['jobId']
            legacy_patch=(await client.patch('/api/jobs/'+first['jobId'],json={'enabled':True})).json()
            assert not legacy_patch['ok']
            assert not (await client.post('/api/jobs/'+first['jobId']+'/run-now')).json()['ok']
            assert not (await client.delete('/api/jobs/'+first['jobId'])).json()['ok']
            assert (await client.delete('/api/actions/actions/pause')).status_code==401
            assert (await client.delete('/api/actions/actions/pause?confirmReferences=true',headers=headers)).status_code==200
            incomplete=(await client.get('/api/jobs/action-jobs')).json()['jobs']
            assert all(j['integrity']['status']=='incomplete' for j in incomplete)
            rt.actions.put('actions',action())
            repaired=(await client.put(base,json={'config':config(),'expectedRevision':1},headers=headers)).json()['job']
            assert not repaired['enabled'] and repaired['configRevision']==2
            assert (await client.put(base,json={'config':config(),'expectedRevision':1},headers=headers)).status_code==409
            enabled=(await client.put(base,json={'config':config(),'expectedRevision':2},headers=headers)).json()['job']
            assert enabled['enabled']
            assert (await client.post(base+'/run-now',json={'expectedRevision':3})).status_code==401
            assert (await client.post(base+'/run-now',json={'expectedRevision':3,'text':'extra'},headers=headers)).status_code==400
            assert (await client.post(base+'/run-now',json={'expectedRevision':3},headers=headers)).status_code==200
            await finished(rt,draft['jobId'])
    finally:await rt.close()


@async_test
async def test_reference_replacement_failed_publication_protects_old_and_new(tmp_path,monkeypatch):
    rt,_=await setup(tmp_path)
    entered=threading.Event();release=threading.Event();original=jobs._write_job
    try:
        other={**action(),'id':'other'};rt.actions.put('actions',other)
        job=rt.create(config(),True);jid=job['jobId']
        def blocked(path,value):
            if value.get('jobId')==jid and value.get('configRevision')==2:
                entered.set();assert release.wait(3);raise OSError('replacement commit failed')
            return original(path,value)
        monkeypatch.setattr(jobs,'_write_job',blocked)
        update=asyncio.create_task(asyncio.to_thread(rt.update,jid,{**config(),'actionId':'other'},1,True))
        assert await asyncio.to_thread(entered.wait,2)
        from packages.jobs.action_templates import references
        assert references(rt.actions.read(),'pause') and references(rt.actions.read(),'other')
        with pytest.raises(PermissionError):rt.actions.delete('actions','pause')
        # Deleting the old definition must not wait for the Job file writer.
        await asyncio.wait_for(asyncio.to_thread(rt.actions.delete,'actions','pause',authorized=True,confirm_references=True),1)
        release.set()
        with pytest.raises(OSError,match='replacement'):await update
        assert rt.get(jid)['config']['actionId']=='pause'
        assert rt.view(rt.get(jid))['integrity']['status']=='incomplete'
        monkeypatch.setattr(jobs,'_write_job',original)
        await rt.close()
        fresh=Runtime(rt.root,rt.actions,rt.pause);await fresh.start()
        try:
            assert references(fresh.actions.read(),'pause')
            assert not references(fresh.actions.read(),'other')
        finally:await fresh.close()
    finally:release.set();await rt.close()


@async_test
async def test_cancel_while_snapshot_commit_inflight_settles_before_cleanup(tmp_path,monkeypatch):
    rt,calls=await setup(tmp_path);entered=threading.Event();release=threading.Event()
    original=rt.templates.snapshot
    def blocked(*args):
        entered.set();assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(rt.templates,'snapshot',blocked)
    try:
        job=rt.create(config(),True);await rt.launch(job['jobId'],1)
        assert await asyncio.to_thread(entered.wait,2)
        cancel=asyncio.create_task(rt.cancel(job['jobId'],1))
        await asyncio.sleep(0);assert not cancel.done()
        release.set();await cancel
        assert not calls and not rt.actions.read().get('jobExecutions')
        assert rt.get(job['jobId'])['lastExecution']['status']=='cancelled'
    finally:release.set();await rt.close()


@async_test
async def test_close_during_claim_never_starts_effect(tmp_path,monkeypatch):
    rt,calls=await setup(tmp_path);entered=threading.Event();release=threading.Event();original=rt.claim
    def blocked(*args):
        value=original(*args);entered.set();assert release.wait(3);return value
    monkeypatch.setattr(rt,'claim',blocked)
    try:
        job=rt.create(config(),True);launch=asyncio.create_task(rt.launch(job['jobId'],1))
        assert await asyncio.to_thread(entered.wait,2)
        close=asyncio.create_task(rt.close());await asyncio.sleep(0)
        release.set();await close;await asyncio.gather(launch,return_exceptions=True)
        assert not calls and not rt.actions.read().get('jobExecutions')
        assert rt.get(job['jobId'])['lastExecution']['status']=='uncertain'
        with pytest.raises(Conflict,match='closed'):await rt.launch(job['jobId'])
    finally:release.set();await rt.close()


@async_test
async def test_duplicate_profile_owner_cannot_recover_or_close_active_execution(tmp_path):
    entered=asyncio.Event();release=asyncio.Event()
    async def pause(*args):entered.set();await release.wait();return {'ok':True}
    rt,_=await setup(tmp_path,pause)
    duplicate=Runtime(rt.root,rt.actions,pause)
    try:
        job=rt.create(config(),True);await rt.launch(job['jobId'],1);await asyncio.wait_for(entered.wait(),2)
        with pytest.raises(Conflict,match='owns'):await duplicate.start()
        await duplicate.close()
        assert RUNTIMES[str(rt.root)] is rt
        assert rt.actions.read()['jobExecutions'] and rt.get(job['jobId'])['status']=='running'
        release.set();await finished(rt,job['jobId'])
        assert not rt.actions.read()['jobExecutions']
    finally:release.set();await rt.close();await duplicate.close()


@async_test
async def test_notification_retry_is_independent_of_effect_and_snapshot(tmp_path,monkeypatch):
    rt,calls=await setup(tmp_path);seen=[];available=False
    async def emit(event):seen.append(event['type']);return available
    monkeypatch.setattr(jobs,'_scheduled_task_emit_strict',emit)
    try:
        job=rt.create(config(),True);await rt.launch(job['jobId'],1);done=await finished(rt,job['jobId'])
        assert calls==['selected'] and not rt.actions.read()['jobExecutions']
        assert done['creationEventPending'] and 'terminalEventEmittedFor' not in done
        available=True;await rt.publish_events()
        done=rt.get(job['jobId'])
        assert not done['creationEventPending'] and done['terminalEventEmittedFor']==done['lastExecution']['id']
        count=len(seen);await rt.publish_events();assert len(seen)==count and calls==['selected']
    finally:await rt.close()


@async_test
async def test_shared_scheduler_entry_and_misfire_do_not_replay_effects(tmp_path,monkeypatch):
    rt,calls=await setup(tmp_path)
    monkeypatch.setattr(jobs,'_scheduled_task_root',lambda:rt.root)
    monkeypatch.setattr(jobs,'_scheduled_task_config',lambda:{'enabled':True,'graceSec':0})
    try:
        job=rt.create(config(),True)
        current=rt.get(job['jobId']);current['schedule'][0]['nextFireAt']=(datetime.now()-timedelta(seconds=5)).isoformat()
        current['schedule'][0]['misfirePolicy']='skip';current['schedule'][0]['graceSec']=0
        jobs._write_job(jobs._job_path(job['jobId'],rt.root),current)
        await jobs.run_due_scheduled_tasks()
        assert rt.get(job['jobId'])['lastExecution']['status']=='skipped' and not calls
        current=rt.get(job['jobId']);current['schedule'][0]['nextFireAt']=datetime.now().replace(microsecond=0).isoformat()
        current['schedule'][0]['misfirePolicy']='fire_now';current['schedule'][0]['graceSec']=60
        jobs._write_job(jobs._job_path(job['jobId'],rt.root),current)
        await jobs.run_due_scheduled_tasks();await finished(rt,job['jobId'])
        assert calls==['selected'] and not rt.actions.read()['jobExecutions']
        await jobs.run_due_scheduled_tasks();assert calls==['selected']
    finally:await rt.close()


@async_test
async def test_disabled_and_high_risk_action_gates_block_job_effects(tmp_path):
    rt,calls=await setup(tmp_path)
    try:
        job=rt.create(config(),True)
        import sys
        for patch in ({'enabled':False},{'effect':'high_risk','execution':{'kind':'command','executable':sys.executable,'args':['-c','raise RuntimeError("must never execute")'],'cwd':str(tmp_path)}}):
            rt.actions.put('actions',{**action(),**patch})
            await rt.launch(job['jobId'],1);await finished(rt,job['jobId'],'failed')
            assert not calls and not rt.actions.read().get('jobExecutions')
    finally:await rt.close()
