"""Owned real HTTP, fake Worker/CLI and production Chromium combination."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta

import psutil

from packages.jobs.window import command_window_options
from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import action

OUT=Path('artifacts/job-unified-live')


def test_unified_event_system_actions_legacy_and_production_ui(tmp_path,monkeypatch):
    assert h.PORT not in (8767,8768)
    monkeypatch.setattr(h,'SERVER_LAUNCHER',Path(__file__).parent/'support/automation_http_server.py')
    rt=h._PanRuntime(tmp_path/'owned-unified-profile');rt.data_root.mkdir()
    (rt.data_root/'config.json').write_text(json.dumps({'plugin_manifests':[],'scheduler':{'enabled':True}}),encoding='utf-8')
    OUT.mkdir(parents=True,exist_ok=True)
    identities={}
    evidence={'port':h.PORT,'profile':str(rt.data_root),'sourceSha':subprocess.check_output(['git','rev-parse','HEAD'],text=True,encoding='utf-8').strip()}
    def capture():
        if rt.process and rt.process.poll() is None:
            for proc in [psutil.Process(rt.process.pid),*psutil.Process(rt.process.pid).children(recursive=True)]:
                try:identities[proc.pid]=proc.create_time()
                except psutil.Error:pass
    def get_job(jid):return rt.request('GET','/api/jobs/'+jid)['job']
    def done(jid):return h._wait_for(lambda:(j if j.get('status')=='completed' and not j.get('execution') else None) if (j:=get_job(jid)) else None,label='unified Job completion')
    try:
        rt.start()
        key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        auth={'Authorization':'Bearer '+rt.request('POST','/api/automation/login',json={'key':key})['token']}
        def create(name):return rt.request('POST','/api/sessions',json={'name':name,'adapter':'cbc','model':'deepseek-v4-flash','mcpServers':[]})
        target=create('Unified-match-original');control=create('Unified-control')
        for session in (target,control):assert 'error' not in h._spawn(rt,session['id'])
        a=action();a['name']='Unified Event query Pause';a['execution']['selector']={'mode':'event','path':'jobSelection.ids'}
        rt.request('POST','/api/actions/actions',json={'item':a})
        cfg={'name':'Unified Event Job','actionId':'pause','arguments':{},'enabled':True,'schedule':[],
             'trigger':{'kind':'event','eventTypes':['workers.all_idle'],'query':{'field':'name','op':'contains','value':'Unified-match'}}}
        event_job=rt.request('POST','/api/jobs/action-jobs',json={'config':cfg},headers=auth)['job']
        h._enqueue(rt,control['id'],'unified-event-edge','unified-event-edge')
        def event_done():
            job=get_job(event_job['jobId'])
            return job if job.get('lastExecution',{}).get('status')=='completed' else None
        settled=h._wait_for(event_done,label='real Worker running edge -> Event Store -> unified Job query -> Pause')
        assert settled['lastExecution']['triggerTargetIds']==[target['id']]
        assert settled['lastExecution']['targetIds']==[target['id']]
        assert rt.request('GET',f"/api/sessions/{target['id']}/queue")['queuePaused']
        rt.request('PUT','/api/jobs/action-jobs/'+event_job['jobId'],json={'config':{**cfg,'enabled':False},'expectedRevision':1},headers=auth)
        create('Unified-match-added-later')
        rt.request('PATCH',f"/api/sessions/{target['id']}",json={'name':'renamed-after-freeze'})
        assert get_job(event_job['jobId'])['lastExecution']['targetIds']==[target['id']]
        evidence['eventRun']=settled['lastExecution']
        # The old API remains a compatibility adapter, with real queue receipts.
        old=[]
        for kind in ('session-message','session-broadcast'):
            target_spec={'sessionId':control['id']} if kind=='session-message' else {'sessionIds':[control['id']]}
            job=rt.request('POST','/api/jobs',json={'kind':kind,'name':'legacy '+kind,'target':target_spec,'text':'legacy '+kind,'schedule':{'type':'once','delaySeconds':1}})['job']
            finished=done(job['jobId']);assert finished['attempts'][-1]['status']=='completed'
            assert finished['snapshotCleanup']=='cleaned';old.append(finished)
        process=rt.request('POST','/api/jobs',json={'kind':'background-process','name':'legacy owned CLI','target':{'sessionId':control['id']},
            'argv':[sys.executable,'-c','print("owned legacy CLI")'],'cwd':str(tmp_path)})['job']
        capture();finished=done(process['jobId']);assert finished['snapshotCleanup']=='cleaned';old.append(finished)
        # New System Action uses the same native runner and waits for its terminal receipt.
        new_cfg={'name':'unified owned CLI','actionId':'system_background_process','arguments':{'sessionId':control['id'],
            'argv':[sys.executable,'-c','print("owned unified CLI")'],'cwd':str(tmp_path)},'schedule':[],
            'trigger':{'kind':'immediate'},'enabled':True}
        new=rt.request('POST','/api/jobs/action-jobs',json={'config':new_cfg},headers=auth)['job']
        capture();new_done=done(new['jobId']);assert new_done['snapshotCleanup']=='cleaned'
        evidence.update(legacy=old,systemExecution=new_done['lastExecution'])
        monitor_cfg={**new_cfg,'name':'unified owned monitor','completion':{'kind':'monitoring','signalType':'owned.done'},
            'recovery':{'enabled':True,'monitoring':True,'maxAttempts':3,'backoffSec':[0,0,0],'stableResetSec':300},
            'arguments':{**new_cfg['arguments'],'argv':[sys.executable,'-c','print(\'{"panJobCompletion":{"type":"owned.done","completed":true}}\')']}}
        monitor=rt.request('POST','/api/jobs/action-jobs',json={'config':monitor_cfg},headers=auth)['job']
        capture();monitor_done=done(monitor['jobId'])
        assert len(monitor_done['attempts'])==1 and monitor_done['snapshotCleanup']=='cleaned'
        evidence['monitorExecution']=monitor_done['lastExecution']
        scheduled=rt.request('POST','/api/jobs',json={'kind':'scheduled-task','name':'legacy scheduled send',
            'target':{'sessionId':control['id']},'text':'scheduled compatibility','action':{'api':'send_session'},
            'schedule':[{'kind':'once','at':(datetime.now()+timedelta(seconds=2)).isoformat()}]})['job']
        old_scheduled=done(scheduled['jobId'])
        assert old_scheduled['attempts'][-1]['actionId']=='system_scheduled_task'
        evidence['legacyScheduled']=old_scheduled['lastExecution']
        input_file=OUT/'browser-input.json';input_file.write_text(json.dumps({'controlId':control['id']}),encoding='utf-8')
        browser=subprocess.run(['node','packages/web/e2e/job-unified-live.mjs',rt.base_url,str(input_file.resolve()),str(OUT.resolve())],
            env={**os.environ,'PAN_TEST_JOB_KEY':key},capture_output=True,text=True,encoding='utf-8',timeout=90,**command_window_options())
        (OUT/'browser.log').write_text(browser.stdout+'\n'+browser.stderr,encoding='utf-8')
        assert browser.returncode==0,browser.stderr
        registry=json.loads((rt.data_root/'actions/registry.json').read_text(encoding='utf-8'))
        assert not registry.get('jobExecutions')
        evidence['snapshotCountAtEnd']=len(registry.get('jobExecutions',{}))
    finally:
        capture();rt.stop()
        alive=[]
        for pid,born in identities.items():
            try:
                process=psutil.Process(pid)
                if abs(process.create_time()-born)<.01:alive.append(process)
            except psutil.Error:pass
        for process in alive:
            try:process.terminate()
            except psutil.Error:pass
        _,remaining=psutil.wait_procs(alive,timeout=3)
        for process in remaining:
            try:process.kill()
            except psutil.Error:pass
        _,remaining=psutil.wait_procs(remaining,timeout=3)
        evidence.update(ownedProcessesRemaining=[p.pid for p in remaining],portReleased=h._port_is_free(h.PORT))
        (OUT/'http.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        (OUT/'server.log').write_text((rt.data_root/'pan.log').read_text(encoding='utf-8',errors='replace'),encoding='utf-8')
        assert not remaining and evidence['portReleased']
