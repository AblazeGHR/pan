"""Actual owned HTTP/Worker/fakeCLI/template capability/command/restart proof."""
from datetime import datetime,timedelta
import json
from pathlib import Path
import sys

import httpx
import psutil

from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import action,trigger
from tests.test_job_action_templates import config


def test_complete_template_reference_snapshot_and_recovery_http(tmp_path,monkeypatch):
    assert h.PORT not in {8767,8768}
    monkeypatch.setattr(h,'SERVER_LAUNCHER',Path(__file__).parent/'support/automation_http_server.py')
    rt=h._PanRuntime(tmp_path/'owned-job-profile');rt.data_root.mkdir()
    (rt.data_root/'config.json').write_text(json.dumps({'plugin_manifests':[],'scheduler':{'enabled':True}}),encoding='utf-8')
    cli=tmp_path/'owned-command';cli.mkdir();(cli/'fixture.marker').write_text('job-action-fixture',encoding='utf-8')
    evidence={'port':h.PORT,'root':str(rt.data_root),'processes':[],'cleanupActions':[]}
    out=Path('artifacts/job-templates');out.mkdir(parents=True,exist_ok=True)
    def capture():
        if rt.process and rt.process.poll() is None:
            parent=psutil.Process(rt.process.pid)
            for p in [parent,*parent.children(recursive=True)]:
                try:evidence['processes'].append({'pid':p.pid,'created':p.create_time(),'command':p.cmdline()})
                except psutil.Error:pass
    def remaining():
        live=[]
        for p in evidence['processes']:
            try:
                process=psutil.Process(p['pid'])
                if abs(process.create_time()-p['created'])<.01:live.append(process)
            except psutil.Error:pass
        return list({p.pid:p for p in live}.values())
    def stop():
        capture();rt.stop()
        # The generic harness stops its owned server/fakeCLI. Also retire only
        # retained descendants captured from this fixture (Action command trees).
        for p in remaining():
            try:
                evidence['cleanupActions'].append({'pid':p.pid,'created':p.create_time(),'action':'terminate owned retained descendant'})
                p.terminate()
            except psutil.Error:pass
        gone,alive=psutil.wait_procs(remaining(),timeout=3)
        for p in alive:
            # Retained Process objects keep original creation identity.
            if p.is_running():p.kill()
        h._wait_for(lambda:not remaining(),label='all owned Job command/Worker descendants stopped')
    def request(method,path,**kwargs):return rt.request(method,path,**kwargs)
    def login():
        key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        return {'Authorization':'Bearer '+request('POST','/api/automation/login',json={'key':key})['token']}
    def call(method,path,body=None,auth=None):
        return httpx.request(method,rt.base_url+path,json=body,headers=auth,trust_env=False,timeout=15)
    def all_jobs():return request('GET','/api/jobs/action-jobs')['jobs']
    def get_job(jid):return next(j for j in all_jobs() if j['jobId']==jid)
    def done(jid,status='completed'):
        return h._wait_for(lambda:(j if j.get('lastExecution',{}).get('status')==status and not j.get('execution') else None) if (j:=get_job(jid)) else None,label='Job terminal '+status)
    def registry():return json.loads((rt.data_root/'actions/registry.json').read_text(encoding='utf-8'))
    def trace():
        p=cli/'trace.jsonl'
        return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []
    try:
        rt.start();auth=login()
        def create(name):return request('POST','/api/sessions',json={'name':name,'adapter':'cbc','model':'deepseek-v4-flash','mcpServers':[]})
        target,control=[create(n) for n in ('job-target','job-control')]
        for s in (target,control):assert 'error' not in h._spawn(rt,s['id'])
        pending=request('POST',f"/api/sessions/{target['id']}/queue",json={'text':'job-must-stay-queued','locked':True,'clientMessageId':'job-locked'})['item']
        a=action([target['id']]);request('POST','/api/actions/actions',json={'item':a})
        schema=request('GET','/api/jobs/action-jobs/schema?actionId=pause')
        assert schema['config']['properties']['arguments']['additionalProperties'] is False
        cfg=config();cfg['name']='fixed Pause Job'
        template=request('POST','/api/jobs/job-templates',json={'item':{'name':'complete','enabled':True,'config':cfg},'expectedRevision':0},headers=auth)['item']
        path='/api/jobs/job-templates/'+template['id']+'/instantiate';body={'expectedRevision':1,'idempotencyKey':'owned-http'}
        assert call('POST',path,body).status_code==401
        assert call('POST',path,{**body,'overrides':{'arguments':{'text':'inject'}}},auth).status_code==400
        job=request('POST',path,json=body,headers=auth)['job']
        assert request('POST',path,json=body,headers=auth)['job']['jobId']==job['jobId']
        jobpath='/api/jobs/action-jobs/'+job['jobId']
        assert not call('POST','/api/jobs/'+job['jobId']+'/run-now').json()['ok']
        request('POST',jobpath+'/run-now',json={'expectedRevision':1},headers=auth)
        paused=done(job['jobId'])
        assert not registry().get('jobExecutions') and 'definition' not in paused['lastExecution']
        request('PATCH',f"/api/sessions/{target['id']}/queue/{pending['queueItemId']}/lock",json={'locked':False})
        h._enqueue(rt,control['id'],'job-control-works','control-initial')
        h._wait_for(lambda:h._received(rt,text='job-control-works'),label='control fakeCLI consumes')
        h._wait_for(lambda:all(s.get('workerStatus')!='running' for s in request('GET','/api/sessions')['sessions']),label='clean idle edge before enabling template Trigger')
        assert request('GET',f"/api/sessions/{target['id']}/queue")['queuePaused'] and not h._received(rt,text='job-must-stay-queued')
        instantiate={**action(),'id':'instantiate','idempotent':True,'execution':{'kind':'job_template','templateId':template['id'],'templateRevision':1}}
        assert call('POST','/api/actions/actions',{'item':instantiate}).status_code==401
        request('POST','/api/actions/actions',json={'item':instantiate},headers=auth)
        t={**trigger(),'actionId':'instantiate'}
        request('POST','/api/automation/triggers',json={'item':t,'expectedRevision':0},headers=auth)
        request('POST','/api/automation/logout',headers=auth)
        h._enqueue(rt,control['id'],'template-capability-after-logout','capability')
        def created():
            return next((r for r in request('GET','/api/automation')['runs'].values() if r['actionId']=='instantiate' and r['status']=='succeeded'),None)
        run=h._wait_for(created,label='real Worker event creates Job via delegated fixed template')
        assert run['result']['jobCompleted'] is False and get_job(run['result']['jobId'])['runCount']==0
        assert 'action' not in run and run['actionDigest']
        auth=login();request('POST','/api/automation/triggers',json={'item':{**t,'enabled':False},'expectedRevision':1},headers=auth)
        assert call('DELETE','/api/actions/actions/pause',auth=auth).status_code==401
        request('DELETE','/api/actions/actions/pause?confirmReferences=true',headers=auth)
        missing=get_job(job['jobId']);assert missing['integrity']['status']=='incomplete' and not missing['enabled']
        request('POST','/api/actions/actions',json={'item':a})
        restored=get_job(job['jobId']);assert restored['integrity']['status']=='complete' and restored['integrity']['approvalRequired'] and not restored['enabled']
        repair=request('PUT',jobpath,json={'config':cfg,'expectedRevision':1},headers=auth)['job'];assert not repair['enabled']
        request('PUT',jobpath,json={'config':cfg,'expectedRevision':2},headers=auth)
        # New source definition; its immutable in-flight command must survive edit/delete.
        hold={**a,'id':'hold','name':'Owned command','execution':{'kind':'command','executable':str(Path(sys.executable).resolve()),
             'args':[str(Path(__file__).parent/'support/job_action_cli.py'),str(cli),'original'],'cwd':str(cli),'timeout_sec':30}}
        request('POST','/api/actions/actions',json={'item':hold})
        command_cfg={**config(),'actionId':'hold','name':'command Job'}
        command=request('POST','/api/jobs/action-jobs',json={'config':command_cfg},headers=auth)['job']
        commandpath='/api/jobs/action-jobs/'+command['jobId']
        request('POST',commandpath+'/run-now',json={'expectedRevision':1},headers=auth)
        h._wait_for(lambda:any(r['kind']=='start' for r in trace()),label='real owned command blocked')
        active=registry()['jobExecutions'];assert len(active)==1
        assert next(iter(active.values()))['definition']['execution']['args'][-1]=='original'
        edited={**hold,'execution':{**hold['execution'],'args':[str(Path(__file__).parent/'support/job_action_cli.py'),str(cli),'edited']}}
        request('PUT','/api/actions/actions/hold',json={'item':edited})
        request('DELETE','/api/actions/actions/hold?confirmReferences=true',headers=auth)
        assert get_job(command['jobId'])['status']=='running' and get_job(command['jobId'])['integrity']['status']=='incomplete'
        request('PATCH',f"/api/sessions/{control['id']}",json={'name':'unrelated edit while command runs'})
        h._enqueue(rt,control['id'],'unrelated-during-job-command','unrelated')
        h._wait_for(lambda:h._received(rt,text='unrelated-during-job-command'),label='unrelated Session not held by snapshot gate')
        capture();(cli/'release').write_text('go',encoding='utf-8');command_done=done(command['jobId'])
        assert any(r['kind']=='finish' and r['label']=='original' for r in trace()) and not any(r['label']=='edited' for r in trace())
        assert not registry()['jobExecutions']
        request('POST','/api/actions/actions',json={'item':hold})
        (cli/'release').unlink()
        cancelled=request('POST','/api/jobs/action-jobs',json={'config':command_cfg},headers=auth)['job']
        cancelpath='/api/jobs/action-jobs/'+cancelled['jobId'];starts=sum(r['kind']=='start' for r in trace())
        request('POST',cancelpath+'/run-now',json={'expectedRevision':1},headers=auth)
        h._wait_for(lambda:sum(r['kind']=='start' for r in trace())>starts,label='command to cancel starts')
        capture();request('POST',cancelpath+'/cancel',json={'expectedRevision':1},headers=auth);cancel_done=done(cancelled['jobId'],'cancelled')
        assert not registry()['jobExecutions']
        reboot=request('POST','/api/jobs/action-jobs',json={'config':command_cfg},headers=auth)['job'];starts=sum(r['kind']=='start' for r in trace())
        request('POST','/api/jobs/action-jobs/'+reboot['jobId']+'/run-now',json={'expectedRevision':1},headers=auth)
        h._wait_for(lambda:sum(r['kind']=='start' for r in trace())>starts,label='command before owned service interruption')
        stop();rt.start();capture()
        recovered=done(reboot['jobId'],'uncertain')
        assert not registry()['jobExecutions'] and not recovered['enabled']
        count=len(trace())
        # An actual scheduled entry uses the same kernel, not only manual launch.
        auth=login();auto_cfg={**cfg,'name':'actual scheduled Pause','schedule':[{'kind':'once','at':(datetime.now()+timedelta(seconds=3)).isoformat()}]}
        auto=request('POST','/api/jobs/action-jobs',json={'config':auto_cfg},headers=auth)['job'];auto_done=done(auto['jobId'])
        assert not auto_done['enabled'] and not registry()['jobExecutions']
        assert len(trace())==count  # Recovery never reran the interrupted command.
        evidence.update(templateId=template['id'],pauseExecution=paused['lastExecution'],capabilityRun=run,
            missing=missing,restored=restored,commandExecution=command_done['lastExecution'],cancelExecution=cancel_done['lastExecution'],
            recovered=recovered['lastExecution'],scheduled=auto_done['lastExecution'],trace=trace(),
            snapshotCountAtEnd=len(registry()['jobExecutions']),jobs=all_jobs())
    finally:
        stop();evidence.update(ownedProcessesRemaining=[],portReleased=h._port_is_free(h.PORT))
        (out/'http-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        (out/'http-server.log').write_text((rt.data_root/'pan.log').read_text(encoding='utf-8',errors='replace'),encoding='utf-8')
