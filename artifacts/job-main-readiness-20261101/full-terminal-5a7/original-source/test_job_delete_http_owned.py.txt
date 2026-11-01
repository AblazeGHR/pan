"""Real product API + real owned Windows runner/Job/child; random loopback port."""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil
import pytest
import uvicorn
from fastapi import FastAPI
from packages.core import background_jobs as jobs, session
from packages.jobs import api
from packages.scheduler import store


@pytest.mark.skipif(os.name != 'nt', reason='Windows retained identity / owned Job verification')
@pytest.mark.parametrize('first_unknown',[False,True])
def test_real_owned_tree_delete_http(tmp_path, monkeypatch,first_unknown):
    root=tmp_path/'jobs-root'
    monkeypatch.setattr(jobs,'DEFAULT_ROOT',root)
    monkeypatch.setattr(store,'DEFAULT_ROOT',root)
    monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR',str(root))
    monkeypatch.setenv('PAN_SCHEDULER_DIR',str(root))
    session._cache['ses_delete_owned']=session.Session(id='ses_delete_owned',name='owned',workdir=str(tmp_path))
    child_script=tmp_path/'owned.py'
    ready=tmp_path/'ready.json'
    child_script.write_text("import subprocess,sys,time,json,os\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\nPath(sys.argv[1]).write_text(json.dumps({'parent':os.getpid(),'child':p.pid}),encoding='utf-8')\ntime.sleep(60)\n",encoding='utf-8')
    app=FastAPI();app.include_router(api.router)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    assert port not in (8767,8768)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='off'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
    thread.start()
    job=None; identities=[]; evidence={'port':port}
    try:
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        job=jobs.start('ses_delete_owned',[sys.executable,str(child_script),str(ready)],str(tmp_path),name='owned deletion',registry_root=root)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            job=jobs.get(job['jobId'],root)
            if ready.exists() and job.get('processJobHandle'):break
            time.sleep(.02)
        assert ready.exists(), job
        assert job.get('processJobHandle'), job
        pids=json.loads(ready.read_text(encoding='utf-8'))
        for pid in {*pids.values(),job['runnerPid']}:
            proc=psutil.Process(pid);identities.append((pid,proc.create_time()))
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=10) as client:
            if first_unknown:
                from packages.jobs import process_cleanup
                original = process_cleanup._open_task_guard
                def denied(*args):
                    raise psutil.AccessDenied(job['runnerPid'])
                monkeypatch.setattr(process_cleanup,'_open_task_guard',denied)
                rejected=client.delete('/api/jobs/'+job['jobId']).json()
                assert rejected['error']['code']=='job_busy'
                assert jobs.get(job['jobId'],root)['status']=='cleanup_pending'
                assert all(psutil.Process(pid).is_running() for pid,_ in identities)
                # Cold interpreter runs the real recovery loader while this
                # request is absent; cleanup_pending must remain visible and
                # cannot relaunch or auto-repeat any deletion effects.
                from packages.jobs.window import command_window_options
                cold=subprocess.run([sys.executable,'-c',
                    "import sys;from packages.core import background_jobs as j;assert j.reconcile_running(sys.argv[1])==0;assert j.get(sys.argv[2],sys.argv[1])['status']=='cleanup_pending';print('cold recovery retained')",
                    str(root),job['jobId']],capture_output=True,encoding='utf-8',timeout=10,
                    **command_window_options())
                assert cold.returncode==0,cold.stderr
                assert all(psutil.Process(pid).is_running() for pid,_ in identities)
                evidence['coldRecoveryRetained']=True
                monkeypatch.setattr(process_cleanup,'_open_task_guard',original)
                evidence['unknownRetainedThenExplicitRecheck']=True
            response=client.delete('/api/jobs/'+job['jobId']).json()
            evidence['response']=response
            assert response.get('ok'), response
            assert jobs.get(job['jobId'],root) is None
            assert not client.delete('/api/jobs/'+job['jobId']).json()['ok']
        time.sleep(.1)
        remaining=[]
        for pid,birth in identities:
            try:
                proc=psutil.Process(pid)
                if proc.create_time()==birth and proc.is_running():remaining.append(pid)
            except psutil.NoSuchProcess:pass
        assert not remaining
        evidence['ownedProcessesRemaining']=remaining
    finally:
        # Cleanup only processes captured from our own runner and child receipt.
        for pid,birth in identities:
            try:
                proc=psutil.Process(pid)
                if proc.create_time()==birth:
                    proc.kill();proc.wait(3)
            except psutil.NoSuchProcess:pass
        server.should_exit=True;thread.join(5);sock.close()
        assert not thread.is_alive()
        with socket.socket() as probe:
            evidence['portReleased']=probe.connect_ex(('127.0.0.1',port))!=0
        out=Path('audit/job-delete-main-integration/http-owned-recheck.json' if first_unknown else 'audit/job-delete-main-integration/http-owned.json')
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
