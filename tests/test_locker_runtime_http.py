"""Real product auth and Event/Trigger wiring, owned HTTP/fakeCLI only."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import httpx
import psutil

from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import action, trigger

OUT=Path('audit/locker-runtime/http')


def test_product_locker_login_choice_pause_restart(tmp_path,monkeypatch):
    assert h.PORT not in {8767,8768}, 'Use the isolated test runner random port'
    monkeypatch.setattr(h,'SERVER_LAUNCHER',Path(__file__).parent/'support/job_inbox_http_server.py')
    rt=h._PanRuntime(tmp_path/'data');rt.data_root.mkdir()
    root=Path(__file__).resolve().parents[1]
    (rt.data_root/'config.json').write_text(json.dumps({'scheduler':{'enabled':True},'memory':{'enabled':False},
        'plugin_manifests':[str(root/'manifest.json'),str(root/'packages/mcp/manifest.json')]}),encoding='utf-8')
    OUT.mkdir(parents=True,exist_ok=True)
    evidence={'port':h.PORT,'sourceSha':subprocess.check_output(['git','rev-parse','HEAD'],encoding='utf-8').strip()}
    identities={}
    def stop():
        if rt.process and rt.process.poll() is None:
            root_process=psutil.Process(rt.process.pid)
            for process in [root_process,*root_process.children(recursive=True)]:
                try:identities[process.pid]=process.create_time()
                except psutil.Error:pass
        rt.stop()
        live=[]
        for pid,born in identities.items():
            try:
                process=psutil.Process(pid)
                if abs(process.create_time()-born)<.01 and process.status() not in {'terminated',psutil.STATUS_ZOMBIE,psutil.STATUS_DEAD}:
                    process.terminate();live.append(process)
            except psutil.Error:pass
        _,live=psutil.wait_procs(live,timeout=3)
        for process in live:
            try:process.kill()
            except psutil.Error:pass
        _,live=psutil.wait_procs(live,timeout=3)
        return [p.pid for p in live]
    try:
        rt.start()
        evidence['identity']=rt.request('GET','/__test/job-inbox-identity')
        assert Path(evidence['identity']['sessions']).parent==rt.data_root
        key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        auth={'Authorization':'Bearer '+rt.request('POST','/api/automation/login',json={'key':key})['token']}
        owner=rt.request('POST','/api/sessions',json={'name':'source','adapter':'cbc','mcpServers':[]})
        rt.request('PATCH',f"/api/sessions/{owner['id']}/queue/paused",json={'paused':True})
        target=rt.request('POST','/api/sessions',json={'name':'pause-target','adapter':'cbc','mcpServers':[]})
        assert 'error' not in h._spawn(rt,target['id'])
        rt.request('POST','/api/actions/actions',json={'item':action([target['id']])})
        for tid in ('selected','never-selected'):
            rt.request('POST','/api/automation/triggers',headers=auth,json={'item':{**trigger(tid),
                'eventTypes':['pan.interaction.responded']},'expectedRevision':0})
        async def create_mcp():
            from mcp import ClientSession,StdioServerParameters
            from mcp.client.stdio import stdio_client
            params=StdioServerParameters(command=os.sys.executable,args=['-m','packages.mcp.server'],cwd=str(root),
                env={**os.environ,'PAN_API_URL':rt.base_url,'PAN_AGENT_SESSION_ID':owner['id']})
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    result=await client.call_tool('interaction_card',{'operation':'create','recipient_id':'user',
                        'request_key':'product','card':{'title':'Production wiring','body':'Never forwarded body',
                        'summaryBody':'Safe summary','choices':[{'id':'pause','label':'Pause',
                        'triggerRef':{'id':'selected','revision':1}}]}})
                    assert not result.isError
                    return json.loads(result.content[0].text)['card']
        card=asyncio.run(create_mcp())
        assert httpx.get(rt.base_url+'/api/user-lockers',trust_env=False).status_code==401
        browser=subprocess.run(['node','packages/web/e2e/locker-runtime.mjs',rt.base_url,str(OUT.resolve()),owner['id']],
            env={**os.environ,'PAN_TEST_JOB_KEY':key},capture_output=True,text=True,encoding='utf-8',timeout=90)
        (OUT/'browser.log').write_text(browser.stdout+'\n'+browser.stderr,encoding='utf-8')
        assert browser.returncode==0,browser.stderr
        result=rt.request('GET',f"/api/user-inbox/{card['id']}",headers=auth)['card']
        assert result['delivery']['state']=='accepted'
        payload={'expectedRevision':0,'requestId':result['response']['requestId'],'choiceId':'pause'}
        rt.request('POST',f"/api/user-inbox/{card['id']}/submit",headers=auth,json=payload)
        def complete():
            runs=rt.request('GET','/api/automation')['runs']
            return list(runs.values()) if len(runs)==1 and next(iter(runs.values()))['status']=='succeeded' else None
        runs=h._wait_for(complete,label='selected real Run completes Pause')
        assert runs[0]['triggerId']=='selected'
        assert rt.request('GET',f"/api/sessions/{target['id']}/queue")['queuePaused']
        rt.request('POST','/api/send',json={'sessionId':target['id'],'text':'blocked-after-pause','source':'user'})
        blocked=rt.request('GET',f"/api/sessions/{target['id']}/queue")
        assert blocked['queuePaused'] and any(n.get('text')=='blocked-after-pause' for n in blocked['items'])
        notices=rt.request('GET',f"/api/sessions/{owner['id']}/queue")['items']
        submitted = [json.loads(n['text'].removeprefix('Interaction submitted: '))
                     for n in notices if n.get('text', '').startswith('Interaction submitted: ')]
        assert len(submitted)==1
        assert submitted[0] == {'cardId':card['id'], 'responseKind':'submit',
                                'summaryBody':'Safe summary', 'choiceId':'pause'}
        assert 'Never forwarded body' not in json.dumps(notices)
        blocked=rt.request('GET',f"/api/sessions/{target['id']}/queue")
        assert blocked['queuePaused'] and any(n.get('text')=='blocked-after-pause' for n in blocked['items'])
        assert len(rt.request('GET','/api/automation')['runs'])==1, 'Reject must not execute choice'
        notices=rt.request('GET',f"/api/sessions/{owner['id']}/queue")['items']
        assert len(notices)==2, 'Submit and reject only; delete must not notify'
        rejected = [json.loads(n['text'].removeprefix('Interaction rejected: '))
                    for n in notices if n.get('text', '').startswith('Interaction rejected: ')]
        assert len(rejected)==1
        assert rejected[0]['summaryBody']=='Safe reject'
        assert rejected[0]['responseKind']=='reject'
        evidence.update(runId=runs[0]['id'],cardId=card['id'],pause=True,rejectNoRun=True,deleteNoNotice=True)
        assert stop()==[]
        rt.start()
        assert httpx.get(rt.base_url+'/api/user-lockers',headers=auth,trust_env=False).status_code==401
        auth={'Authorization':'Bearer '+rt.request('POST','/api/automation/login',json={'key':key})['token']}
        assert rt.request('GET',f"/api/user-inbox/{card['id']}",headers=auth)['card']['delivery']['state']=='accepted'
        assert len(rt.request('GET','/api/automation')['runs'])==1
        assert len(rt.request('GET',f"/api/sessions/{owner['id']}/queue")['items'])==2
        evidence['restartNoReplay']=True
    finally:
        evidence['ownedProcessesRemaining']=stop()
        evidence['portReleased']=h._port_is_free(h.PORT)
        evidence['ownedPidBirths']=identities
        (OUT/'evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        assert evidence['portReleased'] and not evidence['ownedProcessesRemaining']
