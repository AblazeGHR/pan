"""Real venv redirector before-import proof through canonical execution ports."""
import asyncio,json,os,site,sys,time,venv
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from packages.jobs.independent_executor import ExecutionStore,ExecutionHost
from packages.core.platform.execution_host import supervisor_alive,supervisor_exit_evidence
from packages.core.terminal.identity import open_process_for_probe,read_creation_filetime,close_handle_checked,wait_state,ProcessStatus
from tests.test_job_automatic_recovery import env,job_definition
from tests.test_job_final_http_service import enabled,launch,control
from tests.test_job_hidden_production import wait_value

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows venv console regression')


@pytest.mark.parametrize('entry,hidden,exit_code',[('mcp',True,0),('mcp',False,7),('independent',True,0),('scheduled',True,0)])
def test_venv_runner_has_no_window_before_task_imports(tmp_path,monkeypatch,entry,hidden,exit_code,env):
    venv_root=tmp_path/'venv';venv.EnvBuilder(with_pip=False,system_site_packages=True).create(venv_root)
    (venv_root/'Lib/site-packages/test-parent.pth').write_text('\n'.join(site.getsitepackages())+'\n',encoding='utf-8')
    python=str(venv_root/'Scripts/python.exe')
    observation=tmp_path/'runner';observation.mkdir()
    helper=Path(__file__).parent/'helpers/runner_startup_probe.py'
    (venv_root/'Lib/site-packages/sitecustomize.py').write_text('import runpy;runpy.run_path('+repr(str(helper.resolve()))+', init_globals={"OUTPUT_DIRECTORY":'+repr(str(observation))+'})',encoding='utf-8')
    # Same fixed native launcher and module, hosted in an actual venv. This
    # changes only this module's interpreter binding, not global sys or argv.
    from packages.jobs import independent_executor
    monkeypatch.setattr(independent_executor,'sys',SimpleNamespace(executable=python))
    output=tmp_path/'payload.json';release=tmp_path/'release'
    program=('import ctypes,json,os,sys,time;from pathlib import Path;'
        +'Path('+repr(str(output))+').write_text(json.dumps({"pid":os.getpid(),"prefix":sys.prefix,"cwd":os.getcwd(),"console":bool(ctypes.windll.kernel32.GetConsoleWindow())}));'
        +'print("owned stdout",flush=True);print("owned stderr",file=sys.stderr,flush=True);'
        +'deadline=time.monotonic()+20\nwhile not Path('+repr(str(release))+').exists():\n assert time.monotonic()<deadline;time.sleep(.02)\nsys.exit('+str(exit_code)+')')
    value=job_definition(env,{'automatic':False},code=program)
    value['steps'][0]['arguments']['argv']['value']=[python,'-c',program]
    value['steps'][0]['arguments']['hideWindow']={'from':'literal','value':hidden}
    job=enabled(env,value)
    if entry=='scheduled':
        from packages.scheduler.providers import time_definition
        from tests.test_job_final_creators import config
        at=(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()
        response=env.client.post('/api/event-creators',json={'definition':time_definition('venv clock',config([{'ruleId':'original','type':'once','at':at}])), 'expectedRevision':0,'requestKey':'clock'})
        assert response.status_code==201,response.text
        creator=control(env,'event-creators',response.json()['result']['object'],'enable','clock-enable').json()['result']['object']
        response=env.client.post('/api/triggers',json={'definition':{'name':'venv subscription','targetJobId':job['id'],'creatorSelector':{'mode':'ids','ids':[creator['id']]},'eventTypes':['scheduler.time.v2']},'expectedRevision':0,'requestKey':'subscription'})
        assert response.status_code==201,response.text
        control(env,'triggers',response.json()['result']['object'],'enable','subscription-enable')
        env.service.refresh_subscriptions();asyncio.run(env.service.creator_runtime.tick(at=at));asyncio.run(env.service.tick())
        rid=next(iter(env.service.final.get(job['id'])['runs']))
    else:
        rid=launch(env,job)['runId'];asyncio.run(env.service.dispatch_once())
    store=ExecutionStore(env.root/'executions');host=ExecutionHost(store)
    handles=[];doc=None
    try:
        files=wait_value(lambda:list(observation.glob('*.json')),bool)
        assert len(files)==1
        before=json.loads(files[0].read_text(encoding='utf-8'))
        assert before['modulesBefore']==[] and before['console']==0
        assert Path(before['prefix']).resolve()==venv_root.resolve()
        assert before['pid'] in before['consoleClients'] and before['parent'] in before['consoleClients']
        for pid,birth in [(before['pid'],before['filetime']),(before['parent'],before['parentFiletime'])]:
            handle=open_process_for_probe(pid);assert handle
            handles.append(handle);assert str(read_creation_filetime(handle))==birth
        state=env.service.final.store.get(job['id'])['finalRuntime'];admission=next(iter(state['effectAdmissions'].values()))
        from packages.jobs.independent_executor import effect_id
        doc=wait_value(lambda:store.get(effect_id(admission['effectKey'])),lambda d:bool(d.get('supervisorIdentity')))
        assert doc['jobId']==job['id'] and doc['attempt']['runId']==rid
        assert doc['supervisorIdentity']['pid']==before['pid'] and doc['supervisorIdentity']['birth']==before['filetime']
        payload=wait_value(lambda:json.loads(output.read_text()) if output.exists() else None,bool)
        assert Path(payload['prefix']).resolve()==venv_root.resolve()
        assert Path(payload['cwd']).resolve()==tmp_path.resolve()
        assert payload['console'] is (not hidden)
        payload_handle=open_process_for_probe(payload['pid']);assert payload_handle
        handles.append(payload_handle)
        release.write_text('exit',encoding='utf-8')
        done=wait_value(lambda:store.get(doc['effectId']),lambda d:d['result'] is not None)
        assert done['result']['receipt']['exitCode']==exit_code
        assert done['result']['outcome']==('succeeded' if exit_code==0 else 'rejected')
        assert done['result']['cleanupProof']['confirmed'] is True
        assert done['result']['cleanupProof']['ownedTree']['confirmed'] is True
        assert done['result']['cleanupProof']['ownedTree']['remaining']==[]
        assert done['result']['cleanupProof']['ownedTree']['owned']
        wait_value(lambda:supervisor_exit_evidence(done['supervisorIdentity']),lambda p:p['confirmed'])
        wait_value(lambda:[wait_state(handle) for handle in handles],lambda states:all(s is ProcessStatus.DEAD for s in states))
        probes=json.loads(files[0].read_text(encoding='utf-8'))
        assert probes['entryModule']['name']=='packages.jobs.independent_execution_runner'
        assert Path(probes['entryModule']['path']).resolve().is_relative_to(Path(__file__).resolve().parents[1])
        assert probes['loadedModules'] and probes['filetime']==before['filetime'] and probes['parentFiletime']==before['parentFiletime']
        def settled():
            asyncio.run(env.service.dispatch_once())
            return env.client.get('/api/job-runs/'+rid).json()
        final=wait_value(settled,lambda r:r['state']==('completed' if exit_code==0 else 'failed'))
        aggregate=env.service.final.get(job['id'])
        assert len(aggregate['runs'])==len(aggregate['attempts'])==len(aggregate['effects'])==1
        assert aggregate['effects'][0]['receipt']==done['result']
        if entry=='scheduled':
            event=env.service.events.get(final['cause']['eventId'])['event']
            from packages.scheduler.providers import timestamp
            assert event['payload']['scheduledAt']==timestamp(at) and event['payload']['ruleId']=='original'
        log=host.read_execution_log(done['effectId'],done['attempt'])
        import base64
        text=base64.b64decode(log['bytesBase64']).decode('utf-8')
        assert 'owned stdout' in text and 'owned stderr' in text
        # Cold original lookup and retry of this exact completed Attempt do
        # not launch again, including the known exit7 case.
        spy=[];cold=ExecutionHost(ExecutionStore(store.root),launch=lambda *args:spy.append(args))
        request={'jobId':job['id'],'attempt':done['attempt'],'effectKey':done['effectKey'],'frozenPlan':done['frozenPlan']}
        assert cold.get_execution(done['effectId'])==done
        assert cold.submit_execution(request)==done and not spy
    finally:
        release.touch()
        if doc and doc.get('supervisorIdentity') and supervisor_alive(doc['supervisorIdentity']):
            if store.get(doc['effectId'])['result'] is None:host.cleanup_execution(doc['effectId'],doc['attempt'])
            wait_value(lambda:supervisor_exit_evidence(doc['supervisorIdentity']),lambda p:p['confirmed'])
        for handle in handles:close_handle_checked(handle)
