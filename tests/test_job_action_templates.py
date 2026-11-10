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
from tests.test_job_final_http_service import h
from tests.job_retirement_native_fixture import h as native_h  # noqa: F401
from tests.test_retirement_rev16_behavior import h as canonical_h  # noqa: F401


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
def test_job_fields_are_declared_only(h, extra):
    from tests.test_job_final_http_service import job_def
    from tests.test_action_groups import canonical_state
    before = canonical_state(h.service)
    value = job_def(h)
    value['inputSchema'] = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    response = h.client.post('/api/jobs', json={
        'definition': {**value, **extra}, 'expectedRevision': 0,
        'requestKey': 'invalid-template-job'})
    assert response.status_code == 400, response.text
    assert response.json()['error']['code'] == 'invalid_schema'
    assert canonical_state(h.service) == before
    assert h.provider.calls == []


def test_complete_fields_defaults_and_template_revision(tmp_path):
    store=Store(tmp_path/'actions')
    a=action();a['input_schema']={'type':'object','properties':{'count':{'type':'integer'},'tag':{'type':'string','default':'fixed'}},'required':['count'],'additionalProperties':False}
    store.put('actions',a);templates=Templates(store)
    with pytest.raises(ValueError):templates.put({'name':'hole','config':config()},0)
    t=templates.put({'name':'complete','config':{**config(),'enabled':False,'arguments':{'count':2}}},0)
    assert 'enabled' not in t and t['config']['arguments']=={'count':2,'tag':'fixed'}
    with pytest.raises(ValueError, match='Unknown or invalid Job Template'):
        templates.put({k:v for k,v in {**t,'enabled':True}.items() if k in {'id','name','enabled','config'}},1)
    with pytest.raises(Conflict):templates.put({'id':t['id'],'name':'stale','config':t['config']},0,True)


def test_instantiate_copy_revision_display_only_and_idempotence(h):
    from tests.test_job_final_http_service import create, control, job_def, USER
    from packages.jobs.final_contracts import ContractError
    value = {'name': 'complete', 'jobDefinition': job_def(h), 'composition': {'creators': [], 'triggers': []}}
    template = create(h, 'job-templates', value)
    response = control(h, 'job-templates', template, 'publish', 'publish-copy-template')
    assert response.status_code == 200, response.text
    template = response.json()['result']['object']
    body = {'expectedRevision': template['revision'], 'requestKey': 'stable-copy-key',
            'overrides': {'name': 'instance'}}
    def instantiate(_):
        return h.service.instantiate_template(template['id'], body, actor=USER)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(instantiate, range(8)))
    assert len({r['operationId'] for r in receipts}) == 1
    ids = {r['result']['objectIds']['job'] for r in receipts}
    assert len(ids) == 1
    jid, = ids
    child = h.service.get('job', jid, actor=USER)
    assert child['name'] == 'instance' and child['steps'] == value['jobDefinition']['steps']
    # Canonical publication is immutable; edits cannot mutate this source or
    # the already instantiated copy. Display overrides never replace config.
    with pytest.raises(ContractError) as immutable:
        h.service.write('template', template['id'], {'definition': {**value, 'name': 'edited'},
            'expectedRevision': template['revision'], 'requestKey': 'edit-published'}, actor=USER)
    assert immutable.value.code == 'reference_conflict'
    assert h.service.get('job', jid, actor=USER) == child
    with pytest.raises(ContractError) as conflict:
        h.service.instantiate_template(template['id'], {**body, 'expectedRevision': template['revision'] + 1}, actor=USER)
    assert conflict.value.code == 'idempotency_conflict'
    with pytest.raises(ContractError) as config_override:
        h.service.instantiate_template(template['id'], {**body, 'requestKey': 'unsafe-override',
            'overrides': {'arguments': {}}}, actor=USER)
    assert config_override.value.code == 'invalid_schema'
    response = control(h, 'job-templates', template, 'retire', 'retire-copy-template')
    assert response.status_code == 200, response.text
    original = instantiate(0)
    assert original['operationId'] == receipts[0]['operationId'] and original['replayed'] is True
    with pytest.raises(ContractError) as retired:
        h.service.instantiate_template(template['id'], {**body, 'requestKey': 'retired-new-copy'}, actor=USER)
    assert retired.value.code == 'reference_conflict'
    assert [j['id'] for j in h.service.list('job', actor=USER)['items']] == [jid]
    assert h.service.get('job', jid, actor=USER) == child


@async_test
async def test_reserved_creation_recovers_after_template_delete_and_write_failure(tmp_path,monkeypatch):
    rt,_=await setup(tmp_path)
    t=rt.templates.put({'name':'complete','config':config()},0,True)
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
    t=rt.templates.put({'name':'complete','config':config()},0,True)
    original=jobs._create
    def blocked(*args,**kwargs):entered.set();assert release.wait(3);return original(*args,**kwargs)
    monkeypatch.setattr(jobs,'_create',blocked)
    task=asyncio.create_task(asyncio.to_thread(rt.instantiate,t['id'],1,'interleaved'))
    try:
        assert await asyncio.to_thread(entered.wait,2)
        changed=rt.templates.put({'id':t['id'],'name':'edited','config':{**config(),'name':'new'}},1,True)
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
        other=await asyncio.wait_for(asyncio.to_thread(jobs.create_service_job,
            request_id='unrelated-io-proof', operation='exit', root=str(tmp_path),
            port=48321, registry_root=rt.root),1)
        assert other['requestId']=='unrelated-io-proof' and other['operation']=='exit'
        assert other['phase']=='requested' and not calls
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
        t=rt.templates.put({'name':'fixed','config':{**config(),'enabled':False}},0,True)
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
        rt.templates.put({'id':t['id'],'name':'new intent','config':{**config(),'name':'new','enabled':False}},1,True)
        progress.checkpoint={'id':'a-later-run'}
        with pytest.raises(Conflict):await execute_dynamic(a,{}, {},progress,rt.pause)
        with pytest.raises(ValueError):validate_action({**a,'input_schema':{'type':'object','properties':{'x':{'type':'string'}},'additionalProperties':False}})
    finally:await rt.close()


@async_test
async def test_running_template_instantiation_uses_private_context_after_template_delete(tmp_path,monkeypatch):
    import packages.actions.executor as executor
    rt,_=await setup(tmp_path)
    t=rt.templates.put({'name':'fixed','config':{**config(),'enabled':False}},0,True)
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
        t=rt.templates.put({'name':'fixed','config':config()},0,True)
        loop={**action(),'execution':{'kind':'job_template','templateId':t['id'],'templateRevision':1}}
        with pytest.raises(ValueError,match='cycle'):rt.actions.put('actions',loop,authorized=True)
        rt.actions.delete('actions','pause',authorized=True,confirm_references=True)
        rt.actions.put('actions',action())
        view=rt.templates.list()[0]
        assert view['integrity']['status']=='complete' and view['integrity']['approvalRequired'] and not view['usable']
        fixed=rt.templates.put({'id':t['id'],'name':'fixed','config':config()},1,True)
        assert fixed['revision']==2 and fixed['referenceGeneration']==rt.actions.read()['actionGenerations']['pause']
        enabled=rt.templates.put({'id':t['id'],'name':'fixed','config':config()},2,True)
        assert rt.templates.view(enabled, rt.actions.read())['usable']
    finally:await rt.close()


@async_test
async def test_api_permissions_unknown_fields_legacy_aliases_and_repair(tmp_path,monkeypatch):
    """R5 successor: the old template/Action-Job HTTP surface only refuses.

    The previous version exercised the whole draft -> repair -> instantiate ->
    run-now flow.  That flow no longer exists, so it is replaced by the two
    properties that still matter: authentication is checked first (an
    anonymous caller cannot probe), and an authenticated caller gets an
    explicit retirement with no persisted Job, template or receipt.
    """
    from fastapi import FastAPI
    from packages.automation.auth import UserAuth
    from packages.automation.api import install as install_auth
    from packages.actions.api import install as install_actions
    from packages.jobs import action_api,api as legacy
    rt,_=await setup(tmp_path);app=FastAPI();auth=UserAuth(tmp_path/'auth');auth.key()
    from packages.plugins.lifecycle import io
    app.state.automation=SimpleNamespace(job_runtime=rt,auth=auth,engine=SimpleNamespace(io=io))
    server=SimpleNamespace(app=app,sess=SimpleNamespace(list_all=lambda **kw:[]))
    install_auth(server);install_actions(server)
    # Mount through the production installer: that is what applies the R5
    # retirement boundary, so the assertions below describe the shipped app.
    from packages.jobs import final_api
    final_api.install(app, action_api.router, legacy.router)
    monkeypatch.setattr('packages.actions.api.Store',lambda:rt.actions)
    monkeypatch.setattr(legacy,'_job_root',lambda:rt.root)
    monkeypatch.setattr('packages.jobs.pan_bridge.request',lambda *args:None)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned') as client:
            retired=[
                ('post','/api/jobs/action-jobs',{'config':config()}),
                ('post','/api/jobs/job-templates',{'item':{'name':'x','config':config()},'expectedRevision':0}),
                ('delete','/api/jobs/job-templates/jtpl_x',None),
                ('post','/api/jobs/job-templates/jtpl_x/instantiate',{'expectedRevision':1,'idempotencyKey':'http-key'}),
                ('post','/api/jobs/job_x/run-now',{'expectedRevision':1}),
            ]
            # ``/api/jobs/{id}`` is owned by the canonical object route now, so
            # PATCH/DELETE there validate a canonical body instead of reaching
            # the old facade handler.  They must never fall through to it.
            canonical_owned=[
                ('patch','/api/jobs/job_x',{'enabled':True}),
                ('delete','/api/jobs/job_x',None),
            ]
            # Authentication precedes the retirement answer, so an anonymous
            # caller cannot distinguish a retired path from a missing one.
            # ``PATCH /api/jobs/{id}`` is now owned by the canonical object
            # route, which answers 405: also impossible to mutate, and it
            # reveals nothing about the retired legacy handler.
            for method,path,body in retired+canonical_owned:
                anonymous=await client.request(method,path,json=body)
                assert anonymous.status_code in (401,405,400),(method,path,anonymous.text)
                assert 'retired' not in anonymous.text
            token=(await client.post('/api/automation/login',json={'key':auth.key()})).json()['token']
            headers={'Authorization':'Bearer '+token}
            before_jobs=list(rt.list());before_templates=rt.templates.list()
            for method,path,body in retired:
                refused=await client.request(method,path,json=body,headers=headers)
                if path.startswith('/api/jobs/job-templates'):
                    # P6 keeps the Template compatibility adapter mounted;
                    # this intentionally unbound host fails before payload
                    # validation with the canonical provider error.
                    assert refused.status_code in {400, 503}, (method, path, refused.text)
                    assert refused.json().get('error', {}).get('code') in {'provider_unavailable', 'invalid_schema'}
                else:
                    assert refused.status_code==410,(method,path,refused.text)
                    assert refused.json()['detail']['code']=='retired',(method,path)
            for method,path,body in canonical_owned:
                refused=await client.request(method,path,json=body,headers=headers)
                # Canonical owns this address and rejects the old shape; it
                # must never fall through to the retired facade handler.
                assert refused.status_code in (400,404,405,410),(method,path,refused.text)
                if refused.status_code != 410:
                    assert 'retired' not in refused.text,(method,path)
            # Nothing was created, enabled, run or deleted by trying.
            assert list(rt.list())==before_jobs
            assert rt.templates.list()==before_templates
            # The Action registry itself is not part of the Job retirement.
            client.cookies.clear()  # anonymous, not a cookie-authenticated cross-origin write
            assert (await client.delete('/api/actions/actions/pause')).status_code==401
            assert (await client.delete('/api/actions/actions/pause?confirmReferences=true',headers=headers)).status_code==200
            # Reads of the retired surface still work for existing records.
            assert (await client.get('/api/jobs/action-jobs',headers=headers)).status_code==200
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


def test_notification_retry_is_independent_of_effect_and_snapshot(h):
    from tests.test_job_final_http_service import enabled, launch
    from tests.test_action_unification_p4 import plans
    job = enabled(h)
    rid = launch(h, job, 'notification-independent')['runId']
    asyncio.run(h.service.dispatch_once())
    done = h.service.final.get(job['id'])
    assert done['runs'][rid]['state'] == 'completed' and len(h.provider.calls) == 1
    effects = copy.deepcopy(done['effects'])
    frozen = copy.deepcopy(plans(h, job['id'])[rid]['frozen'])
    original_ids = set(done['outbox'])
    assert original_ids and all(row['status'] == 'pending' for row in done['outbox'].values())
    # No transport is available: no sending reservation or effect retry.
    asyncio.run(h.service.deliver_outbox())
    assert h.service.final.get(job['id']) == done
    seen = []
    async def sender(row):
        seen.append(copy.deepcopy(row))
        return {'outcome': 'succeeded', 'receiptId': row['outboxId']}
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    current = h.service.final.get(job['id'])
    assert {row['outboxId'] for row in seen} == original_ids
    assert all(row['status'] == 'acked' for row in current['outbox'].values())
    assert current['effects'] == effects and plans(h, job['id'])[rid]['frozen'] == frozen
    assert len(current['attempts']) == len(h.provider.calls) == 1
    asyncio.run(h.service.deliver_outbox())
    asyncio.run(h.service.dispatch_once())
    assert len(seen) == len(original_ids) and len(h.provider.calls) == 1
    assert h.service.final.get(job['id']) == current


def test_shared_scheduler_entry_and_misfire_do_not_replay_effects(canonical_h):
    from tests.test_retirement_rev16_behavior import schedule, fire, instant
    from packages.jobs.final_service import definition
    from packages.core import session
    h = canonical_h
    missed, due = instant(30), instant(35)
    job, creator, _ = schedule(h, [{'ruleId':'original', 'type':'interval', 'seconds':'60',
        'anchor':missed, 'advanceMode':'grid'}])
    cfg = definition('event_creator', creator)
    cfg['config']['misfirePolicy'] = 'skip'
    cfg['config']['graceSeconds'] = '0'
    changed = h.client.put('/api/event-creators/' + creator['id'], json={
        'definition':cfg, 'expectedRevision':creator['revision'], 'requestKey':'skip-misfire'})
    assert changed.status_code == 200, changed.text
    creator = changed.json()['result']['object']
    fire(h, due)
    skipped = h.service.creators.get(creator['id'])
    assert skipped['drives'][-1]['outcome'] == 'skipped'
    assert skipped['producedCount'] == 0
    state = h.service.final.get(job['id'])
    assert not state['runs'] and not state['attempts'] and not state['effects']
    assert not session.get('ses_a').queue_pending
    # Replace the exhausted rule through the same public CAS API. The new
    # occurrence has its own Event; repeating its tick must not resend it.
    next_at = instant(60)
    cfg = definition('event_creator', creator)
    cfg['config']['rules'] = [{'ruleId':'replacement', 'type':'once', 'at':next_at}]
    cfg['config']['misfirePolicy'] = 'fire_now'
    changed = h.client.put('/api/event-creators/' + creator['id'], json={
        'definition':cfg, 'expectedRevision':creator['revision'], 'requestKey':'fire-now'})
    assert changed.status_code == 200, changed.text
    fire(h, next_at)
    history = h.service.final.history(job['id'])
    assert len(history['runs']) == len(h.service.final.get(job['id'])['attempts']) == len(history['effects']) == 1
    event = h.service.events.get(history['runs'][0]['cause']['eventId'])['event']
    assert event['payload']['ruleId'] == 'replacement'
    assert event['payload']['scheduledAt'] == next_at
    assert len(session.get('ses_a').queue_pending) == 1
    fire(h, next_at)
    assert h.service.final.history(job['id']) == history
    assert len(session.get('ses_a').queue_pending) == 1


@pytest.mark.parametrize('gate', ['disabled', 'high_risk'])
def test_disabled_and_high_risk_action_gates_block_job_effects(h, gate):
    from tests.test_job_final_http_service import enabled, job_def, launch, USER
    from packages.jobs.final_service import definition, object_ref
    if gate == 'high_risk':
        action = h.service.get('action', h.action['id'], actor=USER)
        value = definition('action', action)
        value['effectClass'] = 'high_risk'
        ref = object_ref('action', h.service.publish_action('unapproved_high_risk', value))
        cfg = job_def(h)
        cfg['steps'][0]['actionRef'] = ref
    else:
        cfg = job_def(h)
    job = enabled(h, cfg)
    rid = launch(h, job, 'gated-run')['runId']
    if gate == 'disabled':
        action = h.service.get('action', h.action['id'], actor=USER)
        response = h.client.post('/api/jobs/actions/' + action['id'] + '/operations', json={
            'operation': 'disable', 'expectedRevision': action['revision'],
            'expectedStateRevision': action['stateRevision'], 'requestKey': 'disable-before-effect', 'parameters': {}})
        assert response.status_code == 200, response.text
        before = copy.deepcopy(h.service.final.get(job['id']))
        blocked = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
            'expectedRevision': job['configRevision'], 'requestKey': 'disabled-new-admission',
            'input': {}, 'cause': {'type': 'manual'}})
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()['error']['code'] == 'reference_conflict'
        assert h.service.final.get(job['id']) == before
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'failed'
    assert state['attempts'] == {} and state['effects'] == [] and h.provider.calls == []
    assert state['steps'][rid]['s0']['attemptCount'] == 0
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(job['id']) == state and h.provider.calls == []
