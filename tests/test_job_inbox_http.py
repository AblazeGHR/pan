from tests.support.authorization import authenticate_user_requests
"""Real owned HTTP: Job receipts and Locker delivery share safe Session Queue."""
import json
from pathlib import Path
import subprocess
import os

import httpx

from tests import test_real_queue_http_e2e as h
from packages.jobs.window import command_window_options

OUT=Path('artifacts/job-inbox-followup/http')


def test_jobs_templates_and_inbox_share_queue_without_consumption_or_permission_leak(tmp_path,monkeypatch):
    assert h.PORT not in (8767,8768)
    monkeypatch.setattr(h,'SERVER_LAUNCHER',Path(__file__).parent/'support/job_inbox_http_server.py')
    rt=h._PanRuntime(tmp_path/'data'); authenticate_user_requests(rt);rt.data_root.mkdir()
    root=Path(__file__).resolve().parents[1]
    (rt.data_root/'config.json').write_text(json.dumps({'scheduler':{'enabled':True},'memory':{'enabled':False},
        'plugin_manifests':[str(root/'manifest.json'),str(root/'packages/mcp/manifest.json')]}),encoding='utf-8')
    OUT.mkdir(parents=True,exist_ok=True)
    evidence={'sourceSha':subprocess.check_output(['git','rev-parse','HEAD'],encoding='utf-8').strip(),'port':h.PORT}
    try:
        rt.start()
        evidence['identity']=rt.request('GET','/__test/job-inbox-identity')
        assert Path(evidence['identity']['sessions']).parent==rt.data_root
        assert Path(evidence['identity']['inbox']).is_relative_to(rt.data_root)
        key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        auth={'Authorization':'Bearer '+rt.request('POST','/api/automation/login',json={'key':key})['token']}
        def create(name):return rt.request('POST','/api/sessions',json={'name':name,'adapter':'cbc','model':'deepseek-v4-flash','mcpServers':[]})
        target=create('combined-paused-target');control=create('combined-control')
        for s in (target,control):assert 'error' not in h._spawn(rt,s['id'])
        rt.request('PATCH',f"/api/sessions/{target['id']}/queue/paused",json={'paused':True})
        def route(mid,text,peer='7'):
            return rt.request('POST','/api/plugins/qq/route',json={'source':{'instanceId':'owned','botId':'synthetic','scope':'user','peerId':peer},
                'sourceMessageId':mid,'content':{'text':text},'target':{'kind':'agent_inbox'}})
        seed=route('seed','OLD_SILENT');lid=seed['message']['lockerId']
        rt.request('POST','/api/agent-inbox/subscribe',json={'lockerId':lid,'sessionId':target['id'],'mode':'full'})
        full=route('full','INBOX_FULL_BODY');route('full','INBOX_FULL_BODY')
        notice_seed=route('notice-seed','SILENT_NOTICE_SEED','8');notice_lid=notice_seed['message']['lockerId']
        rt.request('POST','/api/agent-inbox/subscribe',json={'lockerId':notice_lid,'sessionId':target['id'],'mode':'notification'})
        notice=route('notice','INBOX_NOTICE_SECRET','8')
        cfg={'name':'Job beside Locker','actionId':'system_session_message','arguments':{'sessionId':target['id'],'text':'JOB_BUSINESS'},
             'schedule':[],'trigger':{'kind':'immediate'},'enabled':True,
             'notification':{'target':'job','on':['completed'],'sessionIds':[target['id']]}}
        response=httpx.post(rt.base_url+'/api/jobs/action-jobs',json={'config':cfg},trust_env=False)
        assert response.status_code==401
        nested=httpx.post(rt.base_url+'/api/jobs/job-templates',json={'item':{'name':'nested enable','enabled':False,'config':cfg},'expectedRevision':0},trust_env=False)
        assert nested.status_code==401
        job=rt.request('POST','/api/jobs/action-jobs',json={'config':cfg},headers=auth)['job']
        def disk():return json.loads((rt.data_root/'sessions'/f"{target['id']}.json").read_text(encoding='utf-8'))
        def notices():return [r for r in disk()['queue_pending'] if r.get('noticeKind')=='job_result']
        h._wait_for(lambda:len(notices())==1,label='durable Job result beside Inbox deliveries')
        rows=disk()['queue_pending']
        assert sum(r.get('messageId')==full['message']['id'] for r in rows)==1
        assert sum(r.get('messageId')==notice['message']['id'] for r in rows)==1
        assert 'INBOX_NOTICE_SECRET' not in json.dumps(rows)
        assert all('JOB_BUSINESS' not in r.get('result','') for r in notices())
        assert rt.request('POST','/api/agent-inbox/read',json={'lockerId':lid})['messages'][0]['sourceMessageId']=='seed'
        assert len(rt.request('POST','/api/agent-inbox/read',json={'lockerId':notice_lid})['messages'])==2
        assert not h._received(rt,text='JOB_BUSINESS') and not h._received(rt,text=lambda text:'INBOX_FULL_BODY' in text)
        # Template grant can be enabled while copied Jobs stay disabled.
        template=rt.request('POST','/api/jobs/job-templates',json={'item':{'name':'disabled instance',
            'config':{**cfg,'enabled':False}},'expectedRevision':0},headers=auth)['item']
        body={'expectedRevision':template['revision'],'idempotencyKey':'owned-fixed'}
        copied=rt.request('POST',f"/api/jobs/job-templates/{template['id']}/instantiate",json=body,headers=auth)['job']
        assert not copied['enabled']
        # Repeated recovery does not consume silent messages or duplicate receipts.
        rt.request('POST','/api/agent-inbox/recover',json={'lockerId':lid})
        assert len(notices())==1
        consumed=rt.request('POST','/api/agent-inbox/consume',json={'lockerId':lid,'messageIds':[seed['message']['id']]})
        assert consumed['consumed']==[seed['message']['id']] and len(notices())==1
        evidence.update(queueIds=[r['id'] for r in rows],jobId=job['jobId'],lockerId=lid,
                        templateRevision=template['revision'],disabledCopyId=copied['jobId'],pauseBlocked=True)
        rt.request('PATCH',f"/api/sessions/{target['id']}/queue/paused",json={'paused':False})
        h._wait_for(lambda:h._received(rt,text=lambda text:'INBOX_FULL_BODY' in text and 'user:7' in text) and h._received(rt,text='JOB_BUSINESS'),label='both ordinary paths reach owned fakeCLI')
        assert not h._received(rt,text=lambda text:'INBOX_NOTICE_SECRET' in text)
        input_file=OUT/'browser-input.json';input_file.write_text(json.dumps({'controlId':control['id']}),encoding='utf-8')
        browser=subprocess.run(['node','packages/web/e2e/job-unified-live.mjs',rt.base_url,str(input_file.resolve()),str(OUT.resolve())],
            env={**os.environ,'PAN_TEST_JOB_KEY':key},capture_output=True,text=True,encoding='utf-8',timeout=90,**command_window_options())
        (OUT/'browser.log').write_text(browser.stdout+'\n'+browser.stderr,encoding='utf-8')
        assert browser.returncode==0,browser.stderr
    finally:
        import psutil
        identities={pid:born for pid,born,_ in rt._owned_processes}
        if rt.process and rt.process.poll() is None:
            for process in [psutil.Process(rt.process.pid),*psutil.Process(rt.process.pid).children(recursive=True)]:
                try:identities[process.pid]=process.create_time()
                except psutil.Error:pass
        rt.stop()
        live=[]
        for pid,born in identities.items():
            try:
                process=psutil.Process(pid)
                if abs(process.create_time()-born)<.01:live.append(process)
            except psutil.Error:pass
        for process in live:
            try:process.terminate()
            except psutil.Error:pass
        _,live=psutil.wait_procs(live,timeout=3)
        for process in live:
            try:process.kill()
            except psutil.Error:pass
        _,live=psutil.wait_procs(live,timeout=3)
        evidence.update(ownedProcessesRemaining=[p.pid for p in live],portReleased=h._port_is_free(h.PORT))
        (OUT/'evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        (OUT/'server.log').write_text((rt.data_root/'pan.log').read_text(encoding='utf-8',errors='replace'),encoding='utf-8')
        assert not live and evidence['portReleased']
