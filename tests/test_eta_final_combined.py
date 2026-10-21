"""Cross-line Node -> query -> real Pause -> production Chromium plus restart fencing."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import time
import asyncio
import sys

import httpx
import psutil

from packages.jobs.window import command_window_options
from packages.mcp import server as mcp_server
from tests import test_real_queue_http_e2e as h
from tests.test_plugin_events import manifest, records, publish
from tests.test_session_query import query_action, leaf
from tests.test_automation_core import trigger

OUT = Path('artifacts/eta-final')


def test_node_query_pause_production_ui_restart_and_delay_mcp(tmp_path, monkeypatch):
    assert h.PORT not in {8767, 8768}
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', Path(__file__).parent / 'support/automation_http_server.py')
    rt = h._PanRuntime(tmp_path / 'combined-profile')
    rt.data_root.mkdir()
    m = manifest()
    mf = tmp_path / 'plugins.json'
    mf.write_text(json.dumps({'mcp_servers': [{k:v for k,v in asdict(s).items() if v is not None} for s in m.mcp_servers], 'plugins':m.plugins}))
    (rt.data_root / 'config.json').write_text(json.dumps({'plugin_manifests':[str(mf)],'scheduler':{'enabled':False}}))
    evidence = {'port':h.PORT,'root':str(rt.data_root),'processes':[],
                'testedSha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                'buildIndexSha256':__import__('hashlib').sha256((h.ROOT/'packages/web/dist/index.html').read_bytes()).hexdigest()}
    def capture():
        if rt.process and rt.process.poll() is None:
            for p in [psutil.Process(rt.process.pid), *psutil.Process(rt.process.pid).children(recursive=True)]:
                evidence['processes'].append({'pid':p.pid,'created':p.create_time(),'command':p.cmdline()})
    def stopped():
        return all(not psutil.pid_exists(p['pid']) or psutil.Process(p['pid']).create_time()!=p['created'] for p in evidence['processes'])
    def view():
        return rt.request('GET','/api/automation')
    def binding(enabled):
        return {'pluginBindings':[{'plugin_id':'producer','enabled':enabled,'mode':'gateway'}]}
    def ready():
        return any(d['type']=='producer.ping' and d['available'] for d in view()['definitions'])
    def create(name):
        return rt.request('POST','/api/sessions',json={'name':name,'adapter':'cbc','model':'deepseek-v4-flash','mcpServers':[]})
    try:
        rt.start()
        target, control, owner = [create(n) for n in ('combined-target','combined-control','combined-owner')]
        for s in (target,control):
            assert 'error' not in h._spawn(rt,s['id'])
        item = rt.request('POST',f"/api/sessions/{target['id']}/queue",json={'text':'combined-blocked','locked':True,'clientMessageId':'combined'})['item']
        a = query_action(leaf('name','eq','combined-target'))
        a['name'] = 'Combined query pause'
        rt.request('POST','/api/actions/actions',json={'item':a})
        token = rt.request('POST','/api/automation/login',json={'key':(rt.data_root/'automation/user.key').read_text()})['token']
        t = {**trigger(),'eventTypes':['producer.ping']}
        rt.request('POST','/api/automation/triggers',json={'item':t,'expectedRevision':0},headers={'Authorization':'Bearer '+token})
        rt.request('PATCH',f"/api/sessions/{owner['id']}",json=binding(True))
        h._wait_for(ready,label='Node ready')
        capture()
        publish(rt.data_root,type='producer.ping',payload={'value':1},event_id='combined-event')
        def done():
            return next((r for r in view()['runs'].values() if r['event']['id']=='combined-event' and r['status']=='succeeded'),None)
        run = h._wait_for(done,label='Node -> query -> Pause')
        assert run['targetIds']==[target['id']] and run['selection']['matched']==1
        assert run['event']['source']=={'type':'plugin','id':'producer'}
        rt.request('PATCH',f"/api/sessions/{target['id']}/queue/{item['queueItemId']}/lock",json={'locked':False})
        h._enqueue(rt,control['id'],'combined-control-delivered','control')
        h._wait_for(lambda:h._received(rt,text='combined-control-delivered'),label='control actual CLI handoff')
        queue=rt.request('GET',f"/api/sessions/{target['id']}/queue")
        assert queue['queuePaused'] and any(i['queueItemId']==item['queueItemId'] for i in queue['items'])
        assert not h._received(rt,text='combined-blocked')
        OUT.mkdir(parents=True,exist_ok=True)
        browser_input=OUT/'browser-input.json'
        browser_input.write_text(json.dumps({'runId':run['id'],'targetId':target['id']}))
        browser=subprocess.run(['node','packages/web/e2e/eta-final-combined.mjs',rt.base_url,str(browser_input.resolve()),str(OUT.resolve())],capture_output=True,text=True,encoding='utf-8',timeout=70,**command_window_options())
        (OUT/'browser.log').write_text(browser.stdout+'\n'+browser.stderr)
        assert browser.returncode==0, browser.stderr
        # Same shared server keeps delayed-restart MCP management gates intact.
        monkeypatch.setattr(mcp_server,'_pan_api_url',rt.base_url)
        monkeypatch.setattr(mcp_server,'_caller_identity',lambda:{'id':owner['id'],'managed':[],'panAccess':{'restrictToManaged':True}})
        denied=mcp_server.agent_restart_delayed(target['id'])
        assert not denied['ok'] and denied['error']['code']=='permission_denied'
        monkeypatch.setattr(mcp_server,'_caller_identity',lambda:None)
        # Actual MCP stdio client on the same plugin/query/UI HTTP instance.
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def delay_stdio():
            params=StdioServerParameters(command=sys.executable,args=['-m','packages.mcp.server'],
                env={**os.environ,'PAN_API_URL':rt.base_url,'PAN_AGENT_SESSION_ID':control['id']},cwd=str(h.ROOT))
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    response=await client.call_tool('agent_restart_delayed',{'session_id':control['id']})
                    assert not response.isError
                    return response.structuredContent or json.loads(response.content[0].text)
        delayed=asyncio.run(delay_stdio())
        assert delayed['ok'],delayed
        h._wait_for(lambda:rt.request('GET',f"/api/sessions/{control['id']}/worker/delayed-restart")['delayedRestart']['status']=='completed',label='delayed restart coexists')
        oldgen=[r for r in records(rt.data_root,'producer') if r['kind']=='ready'][-1]['generation']
        rt.request('PATCH',f"/api/sessions/{owner['id']}",json=binding(False))
        assert not ready() and view()['triggers']['t']['enabled']
        rt.request('PATCH',f"/api/sessions/{owner['id']}",json=binding(True))
        h._wait_for(ready,label='re-enabled Node')
        h._wait_for(lambda:[r for r in records(rt.data_root,'producer') if r['kind']=='ready'][-1]['generation']!=oldgen,label='generation replaced')
        before=len([r for r in records(rt.data_root,'producer') if r['kind']=='publication'])
        publish(rt.data_root,type='producer.ping',payload={'value':2},event_id='stale',generation=oldgen)
        h._wait_for(lambda:len([r for r in records(rt.data_root,'producer') if r['kind']=='publication'])>before,label='stale generation receipt')
        assert [r for r in records(rt.data_root,'producer') if r['kind']=='publication'][-1].get('error')
        assert len(view()['runs'])==1
        # Stop own instance, then inject persisted crash fixtures into own ledger.
        capture();rt.stop();h._wait_for(stopped,label='first process cleanup')
        path=rt.data_root/'automation/registry.json'
        state=json.loads(path.read_text())
        for rid,status in [('restart-queued','queued'),('restart-running','running')]:
            state['runs'][rid]={**run,'id':rid,'status':status,'revision':1,'event':{**run['event'],'id':rid}}
        state['runs']['restart-queued'].pop('targetIds',None)
        path.write_text(json.dumps(state))
        rt.start();capture();h._wait_for(ready,label='restart Node admission')
        h._wait_for(lambda:view()['runs']['restart-queued']['status']=='succeeded',label='queued resumed once')
        state=view()
        assert state['runs']['restart-running']['status']=='uncertain'
        assert state['runs']['restart-running']['targetIds']==run['targetIds']
        assert len(state['runs'])==3
        publication_count=len([r for r in records(rt.data_root,'producer') if r['kind']=='publication'])
        publish(rt.data_root,type='producer.ping',payload={'value':1},event_id='combined-event')
        h._wait_for(lambda:len([r for r in records(rt.data_root,'producer') if r['kind']=='publication'])>publication_count,label='new redelivery receipt')
        assert len(view()['runs'])==3
        recovery_out=OUT/'recovery'
        recovery_out.mkdir(exist_ok=True)
        browser_input.write_text(json.dumps({'runId':run['id'],'targetId':target['id'],'uncertainId':'restart-running','queuedId':'restart-queued'}))
        browser=subprocess.run(['node','packages/web/e2e/eta-final-combined.mjs',rt.base_url,str(browser_input.resolve()),str(recovery_out.resolve())],capture_output=True,text=True,encoding='utf-8',timeout=70,**command_window_options())
        (OUT/'browser-recovery.log').write_text(browser.stdout+'\n'+browser.stderr)
        assert browser.returncode==0,browser.stderr
        evidence.update(run=run,queue=queue,state=view(),delayed=delayed,denied=denied,records=records(rt.data_root,'producer'),fake=rt.fake_records())
    finally:
        capture();rt.stop();h._wait_for(stopped,label='final owned Node/CLI/server cleanup')
        evidence['ownedProcessesRemaining']=[]
        evidence['portReleased']=h._port_is_free(h.PORT)
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'combined-http.json').write_text(json.dumps(evidence,indent=2))
        (OUT/'server.log').write_text((rt.data_root/'pan.log').read_text(encoding='utf-8',errors='replace'))
