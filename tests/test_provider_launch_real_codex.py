"""Opt-in installed Codex CLI + unmodified Pan wrapper + loopback Responses API.

No credentials are loaded: own CODEX_HOME, file-only auth, scrubbed environment.
TEST_REAL_CODEX_JS must explicitly identify the installed CLI under test.
"""
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import psutil
from packages.jobs.window import command_window_options
from packages.core import worker, session
from packages.core.adapters.codex import sessions as native
from packages.core.adapters.codex import provider_launch as launch
from packages.automation.quota_host import Bindings
from packages.core.terminal import identity
from tests.test_automation_core import async_test
from tests.test_provider_launch import setup, spawn, stop

CLI=os.environ.get('TEST_REAL_CODEX_JS')
pytestmark=pytest.mark.skipif(not CLI,reason='requires explicit owned installed-CLI integration opt-in')
OUT=Path('artifacts/provider-launch-real')

@pytest.fixture
def responses():
    hits=[];entered=threading.Event();release=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append({'method':'GET','path':self.path})
            body=json.dumps({'data':[{'id':'owned-model','object':'model','owned_by':'local'}]}).encode('utf-8')
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):
            size=int(self.headers['Content-Length']);assert size<2*1024*1024
            raw=self.rfile.read(size)
            request=json.loads(raw)
            hits.append({'method':'POST','path':self.path,'model':request.get('model'),
                         'authorizationPresent':bool(self.headers.get('Authorization'))})
            if len(hits)==1:
                entered.set();assert release.wait(8),'owned concurrent-config barrier not released'
            item={'id':'msg_owned','type':'message','role':'assistant','status':'completed',
                  'content':[{'type':'output_text','text':'owned-loopback-answer','annotations':[]}]}
            response={'id':'resp_owned','object':'response','created_at':1,'status':'completed',
                      'model':'owned-model','output':[item],
                      'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
            events=[{'type':'response.created','response':{**response,'status':'in_progress','output':[]}},
                    {'type':'response.output_item.added','output_index':0,'item':{**item,'status':'in_progress','content':[]}},
                    {'type':'response.output_text.delta','item_id':item['id'],'output_index':0,'content_index':0,'delta':'owned-loopback-answer'},
                    {'type':'response.output_item.done','output_index':0,'item':item},
                    {'type':'response.completed','response':response}]
            body=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode('utf-8')
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);assert server.server_port not in (8767,8768)
    t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port),hits,entered,release
    finally:
        release.set()
        server.shutdown();server.server_close();t.join(3)
        assert not t.is_alive()
        with socket.socket() as s:s.bind(('127.0.0.1',server.server_port))
        if (OUT/'real-cli-evidence.json').exists():
            evidence=json.loads((OUT/'real-cli-evidence.json').read_text(encoding='utf-8'))
            evidence.update(providerPort=server.server_port,providerPortReleased=True)
            (OUT/'real-cli-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')

async def turn(proc):
    proc.stdin.write(b'{"text":"Reply with the local fixture answer; do not use tools."}\n');await proc.stdin.drain()
    rows=[]
    async def read():
        while True:
            line=await proc.stdout.readline();assert line,'wrapper exited before result'
            try:row=json.loads(line.decode('utf-8'))
            except ValueError:
                rows.append({'diagnostic':line.decode('utf-8')});continue
            rows.append(row)
            if row.get('type')=='result':return row
    result=await asyncio.wait_for(read(),25)
    assert not result.get('is_error'),rows
    assert result.get('result')=='owned-loopback-answer',rows
    return result

@async_test
async def test_installed_cli_fixed_provider_start_resume_and_materialized_branch(tmp_path,monkeypatch,responses):
    url,hits,entered,release=responses
    # Do not inherit any user's auth, native home, proxy, MCP or config settings.
    keep={'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','PATHEXT','COMSPEC'}
    for k in list(os.environ):
        if k.upper() not in keep:monkeypatch.delenv(k,raising=False)
    monkeypatch.setenv('USERPROFILE',str(tmp_path));monkeypatch.setenv('HOME',str(tmp_path))
    monkeypatch.setenv('APPDATA',str(tmp_path/'appdata'));monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'localappdata'))
    a,s,selected,root=setup(tmp_path,monkeypatch,url)
    home=Path(os.environ['CODEX_HOME'])
    config=home/'config.toml'
    config.write_text('cli_auth_credentials_store="file"\ncheck_for_update_on_startup=false\n[analytics]\nenabled=false\n[feedback]\nenabled=false\n',encoding='utf-8')
    sentinel=(home/'history-sentinel').read_bytes()
    node=shutil.which('node');assert node and Path(CLI).is_file()
    version=subprocess.run([node,CLI,'--version'],capture_output=True,timeout=15,**command_window_options())
    assert version.returncode==0,version.stderr.decode('utf-8')
    monkeypatch.setattr(a,'base_args',lambda:[sys.executable,'-u',str(Path('packages/core/adapters/codex/wrapper.py').resolve()),'--app-server','--node-path',node,'--codex-path',CLI])
    monkeypatch.setattr(a,'model_args',lambda s:['-c','model="owned-model"','-c','approval_policy="never"','-c','sandbox_mode="read-only"'])
    evidence={'cliVersion':version.stdout.decode('utf-8').strip(),'cliPath':CLI,'actualNativeCLI':True,'receipts':[]}
    accept=launch.Pending.accept
    async def capture(pending,process):
        stream=process.stdout
        class Tap:
            async def readline(self):
                line=await stream.readline()
                try:
                    row=json.loads(line);row.pop('signature',None)
                except ValueError:row=line.decode('utf-8')
                evidence.setdefault('handshake',[]).append(row)
                return line
        process.stdout=Tap()
        try:return await accept(pending,process)
        finally:process.stdout=stream
    monkeypatch.setattr(launch.Pending,'accept',capture)
    proc=None;owned=None
    async def bind(process):
        nonlocal owned
        generation=len(evidence['receipts'])
        owned=worker.Worker('owned-real-cli',s.id,a,process=process,generation=generation)
        worker.workers[owned.worker_id]=owned
        await worker._claim_offline_owner(owned)
        assert owned._provider_launch_binding is not None
        inventory=await Bindings(worker).inventory({s.id:'ta'},{},'unrelated-global-provider')
        assert next(row for row in inventory['workers'] if row['workerId']==owned.worker_id)['accountId']=='provider-a'
        disk=json.loads((session.SESSION_DIR/(s.id+'.json')).read_text(encoding='utf-8'))
        assert disk['execution_operations'][owned._provider_launch_binding['launchId']]['providerLaunchBinding']==owned._provider_launch_binding
        evidence.setdefault('durableBindings',[]).append(owned._provider_launch_binding.copy())
    async def retire(process):
        pending=process._provider_launch
        if pending.guard is None:return
        handles=[];members=[];remaining=[]
        try:
            for pid in pending.guard.member_pids():
                handle=identity.open_process_for_probe(pid);assert handle
                handles.append((pid,handle))
                proof=identity.probe_handle(handle,pid)
                assert proof.identity is not None
                members.append({'pid':pid,'birthToken':str(proof.identity.created_at_filetime),
                                'image':psutil.Process(pid).exe()})
            await stop(process)
            for pid,handle in handles:
                await asyncio.to_thread(identity.kernel32().WaitForSingleObject,handle,2000)
                if identity.wait_state(handle).value!='dead':remaining.append(pid)
            assert not remaining
        finally:
            for _,handle in handles:assert identity.close_handle_checked(handle)
        assert pending.guard is None and pending.native_handle is None and pending.wrapper_handle is None
        assert not pending.home.exists()
        evidence.setdefault('cleanup',[]).append({'jobMembers':members,'ownedProcessesRemaining':remaining,'privateDirectoryRemoved':True,'retainedHandlesClosed':True})
    try:
        proc=await spawn(a,s)
        evidence['receipts'].append(proc._provider_launch.receipt.copy())
        await bind(proc)
        pending_turn=asyncio.create_task(turn(proc))
        assert await asyncio.to_thread(entered.wait,5)
        first=proc._provider_launch.receipt['nativeThreadId']
        # Deterministic overlap: the actual native HTTP request is blocked at
        # our provider while the global provider definition changes on disk.
        config.write_text('cli_auth_credentials_store="file"\nmodel_provider="changed"\n[model_providers.changed]\nname="Changed"\nbase_url="'+url+'/wrong"\nwire_api="responses"\nrequires_openai_auth=false\n',encoding='utf-8')
        release.set();await pending_turn
        evidence['concurrentGlobalChangeWhileRequestBlocked']=True
        await turn(proc)
        assert all(h['path'].startswith('/a/') for h in hits),hits
        await retire(proc);proc=None
        s.cli_session_id=first;session.save(s)
        proc=await spawn(a,s);evidence['receipts'].append(proc._provider_launch.receipt.copy())
        await bind(proc)
        assert proc._provider_launch.receipt['nativeThreadId']==first
        await turn(proc)
        # Native state indexing is asynchronous; wait for its actual durable
        # row before asking the existing provider to materialize it. No rows
        # or history fixtures are synthesized by this test.
        async def indexed():
            for _ in range(100):
                with closing(sqlite3.connect(home/'state_5.sqlite')) as db:
                    if db.execute('SELECT 1 FROM threads WHERE id=?',(first,)).fetchone():return
                await asyncio.sleep(.05)
            raise AssertionError('real native thread did not become durable')
        await indexed()
        await retire(proc);proc=None
        for key,value in {'_CODEX_DIR':home,'_STATE_DB':home/'state_5.sqlite','_HISTORY_DB':home/'thread_history_1.sqlite','_SESSIONS_DIR':home/'sessions'}.items():monkeypatch.setattr(native,key,value)
        fork=await asyncio.to_thread(native.fork_session,first,'owned-actual-branch',str(tmp_path))
        assert fork!=first
        s.cli_session_id=fork;session.save(s)
        proc=await spawn(a,s);evidence['receipts'].append(proc._provider_launch.receipt.copy())
        await bind(proc)
        assert proc._provider_launch.receipt['nativeThreadId']==fork
        await turn(proc)
        assert len(hits)==4 and all(h['path']=='/a/responses' and h['model']=='owned-model' and not h.get('authorizationPresent') for h in hits),hits
        assert (home/'history-sentinel').read_bytes()==sentinel
        evidence.update(branchMaterialized=True,providerHits=hits,codexHomeRetained=True)
    finally:
        release.set()
        if proc:await retire(proc)
        worker.workers.pop('owned-real-cli',None)
        evidence['privateConfigsRemaining']=[str(p) for p in (root/'automation/private-launches').glob('*/config.toml')]
        evidence['ownedWrapperStopped']=proc is None or proc.returncode is not None
        evidence['providerHits']=hits
        assert home.resolve()==(tmp_path/'unused-global-codex').resolve()
        shutil.rmtree(home)
        evidence['ownedProfileRemoved']=not home.exists()
        OUT.mkdir(parents=True,exist_ok=True);(OUT/'real-cli-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    assert evidence['privateConfigsRemaining']==[]
