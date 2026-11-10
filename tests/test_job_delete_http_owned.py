"""Authenticated canonical Cancel Run then retirement of an actual owned tree."""
import asyncio,copy,json,os,socket,sys,threading,time,subprocess
from pathlib import Path
import httpx,pytest,uvicorn
from packages.jobs.final_service import FinalService
from packages.jobs.independent_executor import ExecutionStore
from packages.core.terminal.identity import open_process_for_probe,read_creation_filetime,wait_state,ProcessStatus,close_handle_checked
from tests.test_job_automatic_recovery import env,job_definition
from tests.test_job_final_http_service import enabled,launch
from tests.test_job_hidden_production import wait_value


@pytest.mark.skipif(os.name!='nt',reason='Windows retained identity / owned Job verification')
@pytest.mark.parametrize('first_unknown',[False,True])
def test_real_owned_tree_delete_http(tmp_path,monkeypatch,first_unknown,env):
    ready=tmp_path/'ready.json';counter=tmp_path/'counter'
    script=tmp_path/'owned.py'
    script.write_text("import subprocess,sys,time,json,os\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\nPath(sys.argv[1]).write_text(json.dumps({'parent':os.getpid(),'child':p.pid}),encoding='utf-8')\nPath(sys.argv[2]).write_text('once',encoding='utf-8')\ntime.sleep(60)\n",encoding='utf-8')
    value=job_definition(env,{'automatic':False})
    value['steps'][0]['arguments']['argv']['value']=[sys.executable,str(script),str(ready),str(counter)]
    job=enabled(env,value);rid=launch(env,job)['runId'];asyncio.run(env.service.dispatch_once())
    pids=wait_value(lambda:json.loads(ready.read_text(encoding='utf-8')) if ready.exists() else None,bool)
    store=ExecutionStore(env.root/'executions')
    with store.connect() as db:doc=json.loads(db.execute('SELECT document FROM executions').fetchone()[0])
    retained=[]
    for pid in {*pids.values(),doc['supervisorIdentity']['pid']}:
        handle=open_process_for_probe(pid);assert handle
        retained.append((handle,read_creation_filetime(handle)))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];assert port not in {8767,8768}
    server=uvicorn.Server(uvicorn.Config(env.app,lifespan='off',log_level='error'));thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        wait_value(lambda:server.started,bool)
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers=dict(env.client.headers),trust_env=False) as client:
            if first_unknown:
                factory=env.service._external_adapter;calls=[]
                def unconfirmed(jid):
                    adapter=factory(jid)
                    async def denied(**kwargs):
                        calls.append(copy.deepcopy(kwargs));raise OSError('owned cleanup transport response unknown')
                    adapter.cleanup_execution=denied
                    return adapter
                monkeypatch.setattr(env.service,'_external_adapter',unconfirmed)
                original_run=client.get('/api/job-runs/'+rid).json()
                body={'operation':'cancel','expectedStateRevision':original_run['stateRevision'],'requestKey':'original-unknown-cancel'}
                response=client.post('/api/job-runs/'+rid+'/operations',json=body)
                assert response.status_code==202,response.text
                operation=response.json()['operationId']
                receipt=client.get('/api/operations/'+operation).json()
                assert receipt['status']=='unknown' and receipt['error']['code']=='reconciliation_required'
                assert len(calls)==1 and counter.read_text(encoding='utf-8')=='once'
                assert all(wait_state(handle) is ProcessStatus.ALIVE for handle,_ in retained)
                native=store.get(doc['effectId']);assert native['result'] is None and 'cleanupRequest' not in native
                asyncio.run(env.service.close())
                from packages.jobs.window import command_window_options
                cold_script = """import asyncio,json,sys
from pathlib import Path
from packages.jobs.final_service import FinalService
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.independent_executor import ExecutionStore
root=Path(sys.argv[1]);service=FinalService(root,CanonicalUserAuth(Path(sys.argv[2])))
async def query():
 try:
  service.recover()
  for _ in range(2):await service.dispatch_once()
  with ExecutionStore(root/'executions').connect() as db:
   doc=json.loads(db.execute('SELECT document FROM executions').fetchone()[0])
  assert doc['result'] is None and 'cleanupRequest' not in doc
  state=service.final.get(doc['jobId'])
  assert len(state['attempts'])==len(state['effects'])==1
  attempt=next(iter(state['attempts'].values()))
  assert attempt['attemptId']==doc['attempt']['attemptId'] and attempt['state']=='uncertain'
  effect=state['effects'][0]
  assert effect['effectKey']==doc['effectKey'] and effect['outcome']=='unknown'
  assert effect['receipt']=={'code':'host_restarted'}
  assert state['runs'][doc['attempt']['runId']]['state']=='uncertain'
  print(json.dumps({'originalEffectId':doc['effectId'],'originalAttemptId':doc['attempt']['attemptId'],'noCleanupReplay':True}))
 finally:await service.close()
asyncio.run(query())
"""
                child=subprocess.run([sys.executable,'-c',cold_script,str(env.root),str(env.tmp/'automation')],
                    capture_output=True,text=True,timeout=20,**command_window_options())
                assert child.returncode==0,child.stdout+child.stderr
                evidence=json.loads(child.stdout.strip().splitlines()[-1])
                assert evidence=={'originalEffectId':doc['effectId'],'originalAttemptId':doc['attempt']['attemptId'],'noCleanupReplay':True}
                cold=FinalService(env.root,env.auth);env.service=cold;env.app.state.job_final_service=cold
                cold.recover()
                for _ in range(2):asyncio.run(cold.dispatch_once())
                assert store.get(doc['effectId'])==native
                assert counter.read_text(encoding='utf-8')=='once'
                assert all(wait_state(handle) is ProcessStatus.ALIVE for handle,_ in retained)
                replay=client.post('/api/job-runs/'+rid+'/operations',json=body)
                assert replay.status_code==202 and replay.json()['operationId']==operation
                assert replay.json()['status']=='unknown' and len(calls)==1
                assert store.get(doc['effectId'])==native
            run=client.get('/api/job-runs/'+rid).json()
            response=client.post('/api/job-runs/'+rid+'/operations',json={'operation':'cancel',
                'expectedStateRevision':run['stateRevision'],'requestKey':'explicit-cleanup-recheck'})
            assert response.status_code==202,response.text
            def completed():
                asyncio.run(env.service.dispatch_once())
                return client.get('/api/job-runs/'+rid).json()
            final=wait_value(completed,lambda r:r['state']=='cancelled')
            native=store.get(doc['effectId'])
            assert native['cleanupRequest']['status']=='confirmed'
            assert native['cleanupRequest']['proof']['confirmed'] is True
            assert native['cleanupRequest']['proof']['ownedTree']['remaining']==[]
            wait_value(lambda:[wait_state(h) for h,_ in retained],lambda states:all(s is ProcessStatus.DEAD for s in states))
            assert all(read_creation_filetime(handle)==birth for handle,birth in retained)
            assert counter.read_text(encoding='utf-8')=='once'
            current=client.get('/api/jobs/'+job['id']).json()
            response=client.request('DELETE','/api/jobs/'+job['id'],json={'expectedRevision':current['configRevision'],
                'expectedStateRevision':current['stateRevision'],'requestKey':'retire-after-proven-cleanup'})
            assert response.status_code==200,response.text
            payload = response.json()
            assert payload['ok'] is True and payload['retired'] is True and payload['job']['retired'] is True
            # Retirement preserves the original canonical history/receipt. It
            # never purges unknown effects or fabricates a physical delete.
            assert client.get('/api/job-runs/'+rid).json()==final
            assert len(env.service.final.get(job['id'])['attempts'])==1
            assert store.get(doc['effectId'])==native
    finally:
        server.should_exit=True;thread.join(8);sock.close();assert not thread.is_alive()
        for handle,_ in retained:close_handle_checked(handle)
