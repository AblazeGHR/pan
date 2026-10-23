"""Installed CLI consumes a private protocol export with synthetic bearer only."""
import asyncio
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import pytest
import psutil
from packages.core import session,worker
from packages.core.terminal import identity
from packages.jobs.window import command_window_options
from tests.test_automation_core import async_test
from tests.test_provider_launch import setup,spawn,stop
from tests.test_provider_launch_real_codex import turn
from tests.support.private_export_owner import install

CLI=os.environ.get('TEST_REAL_CODEX_JS')
pytestmark=pytest.mark.skipif(not CLI,reason='explicit installed-CLI integration opt-in required')
OUT=Path('artifacts/provider-export')

@async_test
async def test_installed_cli_private_bearer_export_start_and_resume(tmp_path,monkeypatch):
    keep={'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','PATHEXT','COMSPEC'}
    for key in list(os.environ):
        if key.upper() not in keep:monkeypatch.delenv(key,raising=False)
    for key,suffix in [('HOME','user'),('USERPROFILE','user'),('APPDATA','appdata'),('LOCALAPPDATA','localappdata')]:monkeypatch.setenv(key,str(tmp_path/suffix))
    hits=[];entered=threading.Event();release=threading.Event()
    owner=None
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=self.rfile.read(int(self.headers['Content-Length']));request=json.loads(body)
            with owner.lock:expected=owner.tokens['provider-a']
            auth_ok=hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+expected)
            hits.append({'path':self.path,'syntheticAuthorizationMatched':auth_ok,'model':request.get('model')})
            if len(hits)==1:
                entered.set();assert release.wait(8)
            item={'id':'msg_local','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'owned-loopback-answer','annotations':[]}]}
            response={'id':'resp_local','object':'response','created_at':1,'status':'completed','model':'owned-model','output':[item], 'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
            events=[{'type':'response.created','response':{**response,'status':'in_progress','output':[]}},
                    {'type':'response.output_item.added','output_index':0,'item':{**item,'content':[],'status':'in_progress'}},
                    {'type':'response.output_text.delta','item_id':item['id'],'output_index':0,'content_index':0,'delta':'owned-loopback-answer'},
                    {'type':'response.output_item.done','output_index':0,'item':item},
                    {'type':'response.completed','response':response}]
            raw=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode('utf-8')
            self.send_response(200 if auth_ok else 401);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);assert server.server_port not in (8767,8768)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    proc=None;home=None
    evidence={'actualNativeCLI':True,'receipts':[],'cleanup':[]}
    async def retire(p):
        pending=p._provider_launch;handles=[];members=[]
        try:
            for pid in pending.guard.member_pids():
                handle=identity.open_process_for_probe(pid);assert handle;handles.append((pid,handle))
                proof=identity.probe_handle(handle,pid)
                members.append({'pid':pid,'birthToken':str(proof.identity.created_at_filetime),'image':psutil.Process(pid).exe()})
            await stop(p)
            for pid,handle in handles:
                await asyncio.to_thread(identity.kernel32().WaitForSingleObject,handle,2000)
                assert identity.wait_state(handle).value=='dead'
            assert pending.guard is None and not pending.home.exists()
            evidence['cleanup'].append({'members':members,'ownedProcessesRemaining':[],'privateDirectoryRemoved':True})
        finally:
            for _,handle in handles:assert identity.close_handle_checked(handle)
    try:
        url='http://127.0.0.1:'+str(server.server_port)
        a,s,_,root=setup(tmp_path,monkeypatch,url);owner=install(monkeypatch,a,url)
        home=Path(os.environ['CODEX_HOME']);config=home/'config.toml'
        config.write_text('cli_auth_credentials_store="file"\ncheck_for_update_on_startup=false\n[analytics]\nenabled=false\n[feedback]\nenabled=false\n',encoding='utf-8')
        sentinel=(home/'history-sentinel').read_bytes()
        node=shutil.which('node');assert node and Path(CLI).is_file()
        version=subprocess.run([node,CLI,'--version'],capture_output=True,timeout=15,**command_window_options())
        assert version.returncode==0
        evidence['cliVersion']=version.stdout.decode('utf-8').strip()
        monkeypatch.setattr(a,'base_args',lambda:[sys.executable,'-u',str(Path('packages/core/adapters/codex/wrapper.py').resolve()),'--app-server','--node-path',node,'--codex-path',CLI])
        monkeypatch.setattr(a,'model_args',lambda s:['-c','model="owned-model"','-c','approval_policy="never"','-c','sandbox_mode="read-only"'])
        first=None
        # Change the synthetic owner's current provider after its atomic export,
        # before the real CLI starts. This cannot relabel the immutable bundle.
        owner.after=lambda *args:owner.switch()
        for generation in (1,2):
            if generation==2:
                owner.after=None
                # Explicitly restore only the synthetic test owner selection to
                # verify native resume with a new receipt/config alias.
                with owner.lock:owner.provider='provider-a';owner.revision+=1
                s.cli_session_id=first;session.save(s)
            proc=await spawn(a,s)
            w=worker.Worker('private-real-cli',s.id,a,process=proc,generation=generation);worker.workers[w.worker_id]=w
            await worker._claim_offline_owner(w)
            assert w._provider_launch_binding is not None
            receipt=w._provider_launch_binding.copy();evidence['receipts'].append(receipt)
            if first is None:first=receipt['nativeThreadId']
            else:assert receipt['nativeThreadId']==first
            if generation==1:
                task=asyncio.create_task(turn(proc));assert await asyncio.to_thread(entered.wait,5)
                config.write_text('cli_auth_credentials_store="file"\nmodel_provider="unrelated"\n',encoding='utf-8')
                release.set();await task
                assert owner.provider=='provider-b' and receipt['providerId']=='provider-a'
            else:await turn(proc)
            # Credentials must not reach any persisted Session JSON or safe evidence.
            public=json.dumps(evidence)+(session.SESSION_DIR/(s.id+'.json')).read_text(encoding='utf-8')
            leaked=any(token in public for token in owner.tokens.values());assert not leaked
            await retire(proc);proc=None;worker.workers.pop('private-real-cli',None)
        assert len(hits)==2 and all(h['path']=='/provider-a/responses' and h['syntheticAuthorizationMatched'] for h in hits)
        assert (home/'history-sentinel').read_bytes()==sentinel
        evidence.update(providerHits=hits,codexHomeRetained=True,secretPublished=False,concurrentGlobalChange=True)
    finally:
        release.set()
        if proc:await retire(proc)
        worker.workers.pop('private-real-cli',None)
        server.shutdown();server.server_close();thread.join(3);assert not thread.is_alive()
        with socket.socket() as check:check.bind(('127.0.0.1',server.server_port))
        if home:
            assert home.resolve()==(tmp_path/'unused-global-codex').resolve();shutil.rmtree(home)
        evidence.update(providerPortReleased=True,ownedProfileRemoved=home is None or not home.exists())
        OUT.mkdir(parents=True,exist_ok=True);(OUT/'real-cli-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
