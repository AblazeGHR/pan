"""Real stdio -> existing private IPC -> ASGI object routes, with no user credential in MCP."""
import asyncio
import copy
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from packages.automation.authorization import bind_objects, object_request, object_path
from packages.automation.api import install as install_automation
from packages.actions.api import install as install_actions
from packages.automation.service import Service
from packages.core.manifest_loader import ManifestConfig, McpServer
from packages.core.session import Session
from packages.jobs.action_api import router
from packages.plugins.ipc import Bridge
from packages.plugins.runtime import Runtime as PluginRuntime
from tests.test_automation_core import async_test, action, trigger
from tests.test_authorization_permissions import full_permissions
from tests.test_job_action_templates import config, finished, setup


async def harness(root, monkeypatch):
    rt, calls = await setup(root)
    svc = Service(root/'automation', SimpleNamespace(workers={}), None)
    svc.job_runtime=rt; svc.engine.actions=rt.actions
    rt.authorization=svc.auth.permission_allowed
    app=FastAPI();app.state.automation=svc
    server=SimpleNamespace(app=app,sess=SimpleNamespace(list_all=lambda **kw:[]))
    monkeypatch.setattr('packages.actions.api.Store', lambda:rt.actions)
    install_automation(server);install_actions(server);app.include_router(router)
    manifest=ManifestConfig(mcp_servers=[McpServer('pan', command=sys.executable,args=['-m','packages.mcp.server'])])
    session=Session(id='ses_authorization_test',name='Authorization',adapter_config={
        'plugin_bindings':[{'plugin_id':'pan','mode':'standalone','enabled':True}]})
    runtime=PluginRuntime(lambda:manifest,lambda sid:session if sid==session.id else None)
    bind_objects(runtime,app)
    bridge=Bridge(runtime,root)
    return SimpleNamespace(rt=rt,calls=calls,svc=svc,app=app,bridge=bridge,session=session)


async def close(h):
    await h.bridge.close();await h.svc.engine.close();await h.svc.bus.close();await h.rt.close()


def result(value):
    if value.structuredContent is not None:
        return value.structuredContent
    return json.loads(value.content[0].text)


@pytest.mark.parametrize('projection',['core','native'])
@async_test
async def test_real_stdio_grants_no_user_bearer_and_four_object_operations(tmp_path,monkeypatch,projection):
    h=await harness(tmp_path,monkeypatch)
    args=['-m','packages.mcp.server'] if projection=='core' else ['-m','packages.plugins.mcp','--view','native','--core']
    params=StdioServerParameters(command=sys.executable,args=args,cwd=str(Path.cwd()),env={
        **os.environ,'PAN_PLUGIN_IPC':h.bridge.address,'PAN_AGENT_SESSION_ID':h.session.id,
        # No HTTP operation for these tools, never touch an application port.
        'PAN_API_URL':'http://127.0.0.1:1'})
    try:
        async with stdio_client(params) as streams, ClientSession(*streams) as client:
            await client.initialize()
            async def call(kind,operation,**args):
                return result(await client.call_tool('authorization_objects',dict(kind=kind,operation=operation,**args)))
            assert (await call('action','read'))['error']['code']==403
            h.svc.auth.change_permissions(full_permissions(),0)
            assert (await call('action','read'))['actions']
            a=action();a.update(id='owned',enabled=False)
            created=await call('action','create',item=a)
            assert created['item']['id']=='owned'
            rev=created['revision']
            edited=await call('action','updateConfig',item_id='owned',expected_revision=rev,item={**a,'name':'Edited'})
            assert edited['actions'][-1]['name']=='Edited'
            enabled=await call('action','enable',item_id='owned',expected_revision=edited['revision'])
            assert next(a for a in enabled['actions'] if a['id']=='owned')['enabled']
            disabled=await call('action','disable',item_id='owned',expected_revision=enabled['revision'])
            removed=await call('action','delete',item_id='owned',expected_revision=disabled['revision'])
            assert not any(a['id']=='owned' for a in removed['actions'])
            t={**trigger(),'enabled':False}
            new=await call('trigger','create',item=t,expected_revision=0)
            assert new['item']['authorization']['type']=='agent'
            tid=new['item']['id']
            changed=await call('trigger','updateConfig',item_id=tid,expected_revision=1,item={**t,'name':'Changed'})
            assert changed['item']['revision']==2
            on=await call('trigger','enable',item_id=tid,expected_revision=2)
            assert on['item']['enabled']
            off=await call('trigger','disable',item_id=tid,expected_revision=3)
            assert not off['item']['enabled']
            assert (await call('trigger','delete',item_id=tid,expected_revision=4))['ok']
            cfg={**config(),'enabled':False}
            template=await call('jobTemplate','create',item={'name':'Passive','config':cfg},expected_revision=0)
            tpl=template['item'];assert 'enabled' not in tpl
            assert (await call('jobTemplate','enable',item_id=tpl['id'],expected_revision=1))['error']['code']=='invalid_operation'
            assert (await call('jobTemplate','execute',item_id=tpl['id'],expected_revision=1))['error']['code']=='invalid_operation'
            tpl2=(await call('jobTemplate','updateConfig',item_id=tpl['id'],expected_revision=1,
                             item={'id':tpl['id'],'name':'Revised','config':cfg}))['item']
            j=(await call('jobTemplate','use',item_id=tpl['id'],expected_revision=2,idempotency_key='stable'))['job']
            assert j['templateId']==tpl['id'] and j['authorization']['type']=='agent'
            one=(await call('job','create',item=cfg))['job'];assert one['templateId'] is None
            jid=one['jobId']
            one=(await call('job','updateConfig',item_id=jid,expected_revision=1,item={**cfg,'name':'Changed'}))['job']
            one=(await call('job','enable',item_id=jid,expected_revision=2))['job'];assert one['enabled']
            one=(await call('job','pause',item_id=jid,expected_revision=3))['job'];assert one['paused']
            one=(await call('job','resume',item_id=jid,expected_revision=4))['job'];assert not one['paused']
            launched=await call('job','execute',item_id=jid,expected_revision=5)
            assert launched.get('job'),launched
            await finished(h.rt,jid)
            assert h.calls==['selected']
            one=(await call('job','disable',item_id=jid,expected_revision=5))['job'];assert not one['enabled']
            assert (await call('job','delete',item_id=jid,expected_revision=6))['deleted']
            assert (await call('jobTemplate','delete',item_id=tpl['id'],expected_revision=2))['deleted']
            h.svc.auth.change_permissions(full_permissions(False),1)
            assert (await call('action','read'))['error']['code']==403
            # MCP never acquires a user's password or Bearer session.
            assert not h.svc.auth.sessions
    finally:await close(h)


@async_test
async def test_user_management_ignores_grants_forged_http_rejected_system_definitions_immutable(tmp_path,monkeypatch):
    h=await harness(tmp_path,monkeypatch)
    try:
        token=h.svc.auth.login(h.svc.auth.key());headers={'Authorization':'Bearer '+token}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(h.app),base_url='http://owned') as client:
            for forged in ({}, {'X-Pan-Agent':'true'}, {'X-Pan-Caller':h.session.id}, {'X-Pan-Plugin-IPC':h.bridge.address}):
                r=await client.post('/api/actions/actions',headers=forged,json={'item':action()})
                assert r.status_code==401
            for path in ('/api/actions','/api/automation','/api/jobs/action-jobs','/api/jobs/job-templates'):
                assert (await client.get(path,headers=headers)).status_code==200
            assert (await client.post('/api/actions/actions',headers=headers,json={'item':{**action(),'id':'user_created'}})).status_code==200
            assert (await client.post('/api/automation/triggers',headers=headers,json={'item':{**trigger(),'enabled':False},'expectedRevision':0})).status_code==200
            assert (await client.post('/api/jobs/action-jobs',headers=headers,json={'config':config()})).status_code==200
            assert (await client.post('/api/jobs/job-templates',headers=headers,json={'item':{'name':'User passive','config':config()},'expectedRevision':0})).status_code==200
            state=(await client.get('/api/automation/authorization',headers=headers)).json()
            assert not any(bit for row in state['permissions'].values() for bit in row.values())
            assert (await client.put('/api/automation/authorization/permissions',headers=headers,
                     json={'permissions':full_permissions(),'expectedRevision':0})).status_code==200
            assert (await client.put('/api/automation/authorization/permissions',headers=headers,
                     json={'permissions':full_permissions(False),'expectedRevision':0})).status_code==409
            system=copy.deepcopy(h.rt.actions.read()['actions']['system_session_message'])
            before=json.loads(h.rt.actions.path.read_text(encoding='utf-8'))['actions'][system['id']]
            for a in (system,{**system,'description':'tampered'},{**system,'system':False}):
                r=await client.put('/api/actions/actions/'+system['id'],headers=headers,json={'item':a})
                assert r.status_code in (400,401)
            assert (await client.delete('/api/actions/actions/'+system['id'],headers=headers)).status_code==401
            current=h.rt.actions.read()['revision']
            off=await client.post('/api/actions/actions/'+system['id']+'/lifecycle',headers=headers,json={'enabled':False,'expectedRevision':current})
            assert off.status_code==200
            physical=json.loads(h.rt.actions.path.read_text(encoding='utf-8'))
            assert physical['actions'][system['id']]==before
            assert h.rt.actions.read()['actions'][system['id']]['enabled'] is False
            assert not object_path('/api/automation/authorization/permissions')
            assert not object_path('/api/jobs')
            assert not object_path('/api/action-drafts/groups/put')
    finally:await close(h)


@pytest.mark.parametrize('kind,operations',[
    ('trigger',['read','create','updateConfig','enable','disable','delete']),
    ('action',['read','create','updateConfig','enable','disable','delete']),
    ('jobTemplate',['read','create','updateConfig','use','delete']),
    ('job',['read','create','updateConfig','enable','disable','pause','resume','cancel','execute','retry','confirmRecovery','delete'])])
def test_four_object_operation_mapping_is_allowlisted(kind,operations):
    for operation in operations:
        method,path,body=object_request(kind,operation,{},'object_id',1,'stable')
        assert object_path(path), (kind,operation,path)
    if kind=='jobTemplate':
        for operation in ('enable','disable','pause','execute'):
            with pytest.raises(ValueError):object_request(kind,operation,{},'jtpl_one',1,'key')


CAPABILITIES=[(kind,cap) for kind,names in full_permissions().items() for cap in names]

@pytest.mark.parametrize('kind,capability',CAPABILITIES)
@async_test
async def test_every_capability_maps_to_denial_on_existing_ipc(tmp_path,monkeypatch,kind,capability):
    from packages.plugins.ipc import request
    h=await harness(tmp_path,monkeypatch)
    try:
        h.rt.actions.put('actions',{**action(),'id':'managed','enabled':False})
        h.svc.ledger.put({**trigger(),'enabled':False},0,True)
        j=h.rt.create({**config(),'enabled':False},True)
        t=h.rt.templates.put({'name':'Template','config':{**config(),'enabled':False}},0,True)
        operations={'read':'read','create':'create','updateConfig':'updateConfig','lifecycle':'enable',
                    'delete':'delete','use':'use','execute':'execute','manageSystemActions':'enable'}
        ids={'action':'managed','trigger':'t','job':j['jobId'],'jobTemplate':t['id']}
        items={'action':{**action(),'id':'managed','enabled':False,'name':'New'},
               'trigger':{**trigger(),'enabled':False,'name':'New'},
               'job':{**config(),'enabled':False,'name':'New'},
               'jobTemplate':{'id':t['id'],'name':'New','config':{**config(),'enabled':False}}}
        grants=full_permissions();grants[kind][capability]=False
        h.svc.auth.change_permissions(grants,0)
        item=items[kind]
        if capability=='create':
            item={k:v for k,v in item.items() if k!='id'}
        oid=ids[kind]
        rev=h.rt.actions.read()['revision'] if kind=='action' else 1
        if capability=='manageSystemActions':oid='system_session_message'
        method,path,body=object_request(kind,operations[capability],item,oid,rev,'stable')
        response=await asyncio.to_thread(request,{'session_id':h.session.id,'operation':'authorization_objects',
            'method':method,'path':path,'body':body},h.bridge.address)
        assert response['error']['code']==403,response
        assert f'{kind}.{capability}' in response['error']['message']
        assert not h.svc.auth.sessions
    finally:await close(h)


@async_test
async def test_job_reads_do_not_emit_mutations_and_writes_have_complete_gui_projection(tmp_path,monkeypatch):
    from packages.core import background_jobs
    h=await harness(tmp_path,monkeypatch)
    emitted=[];monkeypatch.setattr(background_jobs,'_scheduled_task_emit',emitted.append)
    try:
        job=h.rt.create(config(),True)
        emitted.clear()
        headers={'Authorization':'Bearer '+h.svc.auth.login(h.svc.auth.key())}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(h.app),base_url='http://owned') as client:
            assert (await client.get('/api/jobs/action-jobs/'+job['jobId']+'/runs',headers=headers)).status_code==200
            assert not emitted
            changed=await client.put('/api/jobs/action-jobs/'+job['jobId'],headers=headers,
                json={'config':{**config(),'name':'Updated'},'expectedRevision':1})
            assert changed.status_code==200
            assert len(emitted)==1 and emitted[0]['type']=='job.updated'
            assert emitted[0]['job']['target']=={'sessionId':None}
            assert emitted[0]['job']['source'] and emitted[0]['job']['unified']['config']['name']=='Updated'
    finally:await close(h)
