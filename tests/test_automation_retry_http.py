"""Owned Node -> Event -> bounded parallel command retries and actual HTTP recovery."""
from dataclasses import asdict
import json
from pathlib import Path
import sys

import httpx
import psutil

from tests import test_real_queue_http_e2e as h
from tests.test_plugin_events import manifest, publish, records
from tests.test_automation_core import trigger


def test_node_parallel_finite_retries_and_uncertain_confirmation_http(tmp_path,monkeypatch):
    assert h.PORT not in {8767,8768}
    monkeypatch.setattr(h,'SERVER_LAUNCHER',Path(__file__).parent/'support/automation_http_server.py')
    rt=h._PanRuntime(tmp_path/'owned-profile');rt.data_root.mkdir()
    m=manifest();mf=tmp_path/'plugins.json'
    mf.write_text(json.dumps({'mcp_servers':[{k:v for k,v in asdict(s).items() if v is not None} for s in m.mcp_servers],'plugins':m.plugins}),encoding='utf-8')
    (rt.data_root/'config.json').write_text(json.dumps({'plugin_manifests':[str(mf)],'scheduler':{'enabled':False}}),encoding='utf-8')
    cli=tmp_path/'owned-action-cli';cli.mkdir();(cli/'fixture.marker').write_text('retry-fixture',encoding='utf-8')
    evidence={'port':h.PORT,'root':str(rt.data_root),'processes':[]}
    def capture():
        if rt.process and rt.process.poll() is None:
            for p in [psutil.Process(rt.process.pid),*psutil.Process(rt.process.pid).children(recursive=True)]:
                evidence['processes'].append(dict(pid=p.pid,created=p.create_time(),command=p.cmdline()))
    def stopped():
        return all(not psutil.pid_exists(p['pid']) or psutil.Process(p['pid']).create_time()!=p['created'] for p in evidence['processes'])
    def view():return rt.request('GET','/api/automation')
    def login():
        key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        return {'Authorization':'Bearer '+rt.request('POST','/api/automation/login',json={'key':key})['token']}
    def trace():
        path=cli/'trace.jsonl'
        return [json.loads(r) for r in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []
    def send(i):
        before=sum(r['kind']=='publication' for r in records(rt.data_root,'producer'))
        publish(rt.data_root,type='producer.ping',payload={'value':i},event_id='retry-'+str(i))
        # The example producer has one atomic command mailbox, not a queue.
        # Wait for consumption before replacing it with the next publication.
        h._wait_for(lambda:sum(r['kind']=='publication' for r in records(rt.data_root,'producer'))>before,label='owned publication receipt')
    try:
        rt.start()
        def create(name):return rt.request('POST','/api/sessions',json={'name':name,'adapter':'cbc','model':'deepseek-v4-flash','mcpServers':[]})
        owner,control=[create(n) for n in ('retry-owner','retry-control')]
        assert 'error' not in h._spawn(rt,control['id'])
        a={'id':'retry_command','name':'Owned idempotent retry fixture','enabled':True,'idempotent':True,'effect':'write_local',
           'input_schema':{'type':'object','properties':{'key':{'type':'integer'}},'required':['key'],'additionalProperties':False},
           'execution':{'kind':'command','executable':str(Path(sys.executable).resolve()),'args':[str(Path(__file__).parent/'support/retry_action.py'),str(cli),'{key}'],'cwd':str(cli),'timeout_sec':30}}
        rt.request('POST','/api/actions/actions',json={'item':a})
        t={**trigger(),'actionId':'retry_command','eventTypes':['producer.ping'],'executionMode':'parallel','maxConcurrency':2,
           'retry':{'enabled':True},'arguments':{'key':{'from':'event','path':'payload.value'}}}
        auth=login();rt.request('POST','/api/automation/triggers',json={'item':t,'expectedRevision':0},headers=auth)
        rt.request('PATCH',f"/api/sessions/{owner['id']}",json={'pluginBindings':[{'plugin_id':'producer','enabled':True,'mode':'gateway'}]})
        h._wait_for(lambda:any(d['type']=='producer.ping' and d['available'] for d in view()['definitions']),label='owned real Node ready')
        for i in (1,2,3):send(i)
        def full():
            state=view()
            return state if len(state['runs'])==3 and sum(r['status']=='running' and r['attemptCount']==2 for r in state['runs'].values())==2 else None
        held=h._wait_for(full,label='two blocked real retry attempts at Trigger cap')
        assert sum(r['status']=='running' for r in held['runs'].values())==2
        assert all(r['attemptCount']<=2 for r in held['runs'].values())
        capture()
        h._enqueue(rt,control['id'],'retry-independent-fake-cli','control')
        h._wait_for(lambda:h._received(rt,text='retry-independent-fake-cli'),label='independent real queue while Action slots blocked')
        rt.request('POST','/api/automation/logout',headers=auth)
        (cli/'release').write_text('go',encoding='utf-8')
        h._wait_for(lambda:all(r['status']=='succeeded' for r in view()['runs'].values()),label='automatic retries finish after token logout')
        succeeded=view()
        assert all(r['attemptCount']==2 and [a['status'] for a in r['attempts']]==['failed','succeeded'] for r in succeeded['runs'].values())
        for i in (1,2,3):send(i)
        h._wait_for(lambda:sum(r['kind']=='publication' for r in records(rt.data_root,'producer'))>=6,label='old Event resend receipts')
        assert len(view()['runs'])==3 and len(trace())==12
        # Interrupt a fourth owned command via service shutdown. Recovery must
        # retain uncertain until an independent user confirmation, not replay.
        (cli/'release').unlink()
        send(4)
        h._wait_for(lambda:any(r['event']['id']=='retry-4' and r['status']=='running' and r['attemptCount']==2 for r in view()['runs'].values()),label='owned active command for shutdown')
        capture();rt.stop();h._wait_for(stopped,label='owned shutdown kills blocked command tree')
        rt.start();capture()
        r=next(r for r in view()['runs'].values() if r['event']['id']=='retry-4')
        assert r['status']=='uncertain' and r['attemptCount']==2 and r['attempts'][-1]['status']=='uncertain'
        url=rt.base_url+f"/api/automation/runs/{r['id']}/confirm-recovery"
        body={'expectedRevision':r['revision'],'confirm':True}
        assert httpx.post(url,json=body,trust_env=False).status_code==401
        auth=login()
        assert httpx.post(url,json={**body,'expectedRevision':r['revision']-1},headers=auth,trust_env=False).status_code==409
        (cli/'release').write_text('go',encoding='utf-8')
        rt.request('POST',f"/api/automation/runs/{r['id']}/confirm-recovery",json=body,headers=auth)
        h._wait_for(lambda:view()['runs'][r['id']]['status']=='succeeded',label='explicit uncertain confirmation attempt completes')
        final=view()['runs'][r['id']]
        assert final['attemptCount']==3 and [a['status'] for a in final['attempts']]==['failed','uncertain','succeeded']
        assert len({a['id'] for a in final['attempts']})==3
        evidence.update(held=held,succeeded=succeeded,confirmed=final,trace=trace(),final=view())
    finally:
        if rt.process and rt.process.poll() is None:
            evidence.update(lastView=view(),traceAtCleanup=trace())
        capture();rt.stop();h._wait_for(stopped,label='final owned cleanup')
        evidence.update(ownedProcessesRemaining=[],portReleased=h._port_is_free(h.PORT))
        out=Path('artifacts/retry-parallel');out.mkdir(parents=True,exist_ok=True)
        (out/'http-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        (out/'http-server.log').write_text((rt.data_root/'pan.log').read_text(encoding='utf-8',errors='replace'),encoding='utf-8')
