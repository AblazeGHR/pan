"""Owned processes/config/provider; no user account or provider read."""
import asyncio
import copy
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS

import pytest

from packages.core import worker,session
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core.adapters.codex import provider_launch as launch
from packages.automation.quota_host import Bindings,retained_process
from packages.automation.quota_policy import Store
from tests.test_automation_core import async_test


@pytest.fixture
def provider():
    hits=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(b'owned-provider')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    assert server.server_port not in (8767,8768)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port),hits
    finally:server.shutdown();server.server_close();thread.join(3)


def export(url,provider='provider-a'):
    return {'providerId':provider,'exportReceiptId':'owned-export-'+provider,
        'config':f'model_provider="fixed"\n[model_providers.fixed]\nname="Owned"\nbase_url="{url}"\nwire_api="responses"\nrequires_openai_auth=false\n'}


def setup(tmp_path,monkeypatch,url):
    monkeypatch.setenv('PAN_CODEX_FIXED_PROVIDER_LAUNCH','1')
    root=session.SESSION_DIR.parent
    policy=Store(root/'automation');policy.set_host({'enabled':True,'sessionRoles':{}},0)
    a=CodexAdapter();selected=export(url+'/a')
    monkeypatch.setenv('CODEX_HOME',str(tmp_path/'unused-global-codex'))
    global_home=tmp_path/'unused-global-codex';global_home.mkdir(exist_ok=True)
    (global_home/'config.toml').write_text(export(url+'/global')['config'],encoding='utf-8')
    (global_home/'history-sentinel').write_text('native history stays here',encoding='utf-8')
    monkeypatch.setattr(CodexAdapter,'default_model',property(lambda self:'owned-model'))
    monkeypatch.setattr(a,'provider_launch_source',lambda s:copy.deepcopy(selected),raising=False)
    monkeypatch.setattr(a,'base_args',lambda:[sys.executable,'-u',str(Path('packages/core/adapters/codex/wrapper.py').resolve()),
        '--app-server','--node-path',sys.executable,'--codex-path',str(Path('tests/fixtures/fixed_codex_server.py').resolve())])
    monkeypatch.setattr(a,'mcp_args',lambda s:[])
    monkeypatch.setattr(a,'model_args',lambda s:[])
    monkeypatch.setattr(a,'permission_mode_args',lambda s:[])
    monkeypatch.setattr(worker,'_log_mcp_launch',lambda **kw:None)
    from packages.plugins import lifecycle
    async def capture(s):return None
    monkeypatch.setattr(lifecycle,'capture_async',capture)
    s=session.create('owned-launch');s.adapter='codex';s.adapter_config={};s.workdir=str(tmp_path)
    session.save(s)
    return a,s,selected,root


async def spawn(a,s):
    proc=await worker._spawn_process(s.id,a)
    assert not isinstance(proc,str),proc
    return proc


async def stop(proc):
    if proc.returncode is None:proc.terminate()
    await asyncio.wait_for(proc.wait(),5)
    task=getattr(proc,'_provider_launch_cleanup',None)
    if task:await asyncio.wait_for(task,5)


@async_test
async def test_actual_wrapper_child_loads_fixed_config_and_persists_binding(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url)
    proc=await spawn(a,s);pending=proc._provider_launch
    try:
        assert hits==['/a'] and pending.receipt['providerId']=='provider-a'
        w=worker.Worker('owned-worker',s.id,a,process=proc,generation=7)
        worker.workers[w.worker_id]=w
        await worker._claim_offline_owner(w)
        assert w._provider_launch_binding['generation']==7
        binding=w._provider_launch_binding
        from packages.core.terminal.secret_store import SecretStore
        verifier=SecretStore(root=root)
        for p in (pending.home,pending.home/'config.toml',pending.home/'launch.json'):verifier.verify_owner_only(p)
        key=json.loads((pending.home/'launch.json').read_text(encoding='utf-8'))['key']
        disk=json.loads((session.SESSION_DIR/(s.id+'.json')).read_text(encoding='utf-8'))
        assert disk['execution_operations'][binding['launchId']]['providerLaunchBinding']==binding
        selected.update(export(url+'/b','provider-b'))  # global source now different
        inventory=await Bindings(worker).inventory({s.id:'ta'},{},'provider-b')
        row=next(r for r in inventory['workers'] if r['sessionId']==s.id)
        assert row['accountId']=='provider-a' and row['task'] is None
        assert url not in json.dumps(disk) and 'launch.json' not in json.dumps(disk)
        assert key not in json.dumps(disk)
        assert pending.environment()['CODEX_HOME']==os.environ['CODEX_HOME']
        worker._bump_worker_generation(w)
        assert (await Bindings(worker).inventory({s.id:'ta'},{},'provider-b'))['workers'][0]['accountId'] is None
    finally:
        worker.workers.pop('owned-worker',None);await stop(proc)
    assert not pending.home.exists() and pending.native_handle is None
    assert (Path(os.environ['CODEX_HOME'])/'history-sentinel').read_text(encoding='utf-8')=='native history stays here'


@async_test
async def test_binding_save_failure_never_publishes_and_no_branch_inheritance(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url)
    proc=await spawn(a,s)
    try:
        w=worker.Worker('owned-worker',s.id,a,process=proc,generation=1);worker.workers[w.worker_id]=w
        original=worker._commit_offline
        async def fail(*args,**kw):raise OSError('owned injected persistence failure')
        monkeypatch.setattr(worker,'_commit_offline',fail)
        with pytest.raises(OSError):await worker._claim_offline_owner(w)
        assert w._provider_launch_binding is None and proc._provider_launch.owner is None
        monkeypatch.setattr(worker,'_commit_offline',original);await worker._claim_offline_owner(w)
        forged=worker.Worker('child',s.id,a,process=proc,generation=1)
        forged._provider_launch_binding=copy.deepcopy(w._provider_launch_binding)
        assert launch.candidate(forged,s,worker._offline.SERVICE_ID,retained_process(proc)) is None
    finally:worker.workers.pop('owned-worker',None);await stop(proc)


@async_test
async def test_next_spawn_has_own_receipt_and_no_task_transfer(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url)
    first=await spawn(a,s);old=first._provider_launch.receipt
    await stop(first)
    selected.update(export(url+'/b','provider-b'));second=await spawn(a,s)
    try:
        new=second._provider_launch.receipt
        assert old['launchId']!=new['launchId'] and old['nativeProcessIdentity']!=new['nativeProcessIdentity']
        assert new['providerId']=='provider-b' and hits==['/a','/b']
        assert 'task' not in new and 'executionSeq' not in new
    finally:await stop(second)


def test_default_disabled_no_source_calls_or_private_files(tmp_path,monkeypatch):
    a=CodexAdapter()
    def forbidden(s):raise AssertionError('must stay disabled')
    a.provider_launch_source=forbidden
    assert launch.prepare(a,NS(adapter_config={}),tmp_path) is None
    assert not (tmp_path/'automation/private-launches').exists()


@pytest.mark.parametrize('bad',['env_key','auth','unknown','disabled','native_control'])
def test_unsupported_credentials_and_untrusted_values_do_not_claim_binding(tmp_path,monkeypatch,provider,bad):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url)
    if bad=='env_key':selected['config']+='env_key="OPENAI_API_KEY"\n'
    elif bad=='auth':selected['config']=selected['config'].replace('requires_openai_auth=false','requires_openai_auth=true')
    elif bad=='unknown':selected['verified']=True
    elif bad=='disabled':
        p=Store(root/'automation');p.set_disabled(['provider-a'],p.read()['revision'])
    else:s.adapter_config={'native_control':True}
    if bad=='native_control':assert launch.prepare(a,s,root) is None
    else:
        with pytest.raises(ValueError):launch.prepare(a,s,root)
    assert hits==[]


@async_test
async def test_real_create_restart_and_native_fork_lifecycle_get_new_bindings(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url)
    monkeypatch.setattr(worker,'get_adapter',lambda name:a)
    native=NS(fork_session=lambda *args:'owned-fork',parse_history=lambda *args:[],get_raw_usage=lambda *args:[])
    monkeypatch.setattr(worker,'get_sessions_provider',lambda name:native)
    async def broadcast(*args):pass
    monkeypatch.setattr(worker,'_bcast',broadcast)
    owned=[]
    try:
        w=await worker.create_worker(s.id);assert isinstance(w,worker.Worker),w
        owned.append(w);oldproc=w.process;old=copy.deepcopy(w._provider_launch_binding)
        selected.update(export(url+'/b','provider-b'))
        assert await worker.restart_worker(w.worker_id) is None
        await asyncio.wait_for(oldproc._provider_launch_cleanup,5)
        new=w._provider_launch_binding
        assert new['providerId']=='provider-b' and new['generation']>old['generation']
        assert new['launchId']!=old['launchId'] and w._current_task_id is None
        s.cli_session_id='owned-thread';session.save(s)
        child=session.create('owned-fork');child.workdir=str(tmp_path);session.save(child)
        branch=await worker.branch_worker(w.worker_id,child.id)
        assert isinstance(branch,worker.Worker),branch
        owned.append(branch)
        assert branch._provider_launch_binding['launchId']!=new['launchId']
        assert branch._provider_launch_binding['workerId']==branch.worker_id
        assert branch._provider_launch_binding['sessionId']==child.id
        assert branch._current_task_id is None and hits==['/a','/b','/b']
    finally:
        for w in reversed(owned):
            proc=w.process
            await worker.kill_worker(w.worker_id)
            if proc:await stop(proc)


@async_test
async def test_global_change_after_export_cannot_rebind_child(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url)
    entered=threading.Event();release=threading.Event()
    def source(s):
        captured=copy.deepcopy(selected);entered.set();assert release.wait(5);return captured
    a.provider_launch_source=source
    task=asyncio.create_task(spawn(a,s));assert await asyncio.to_thread(entered.wait,5)
    selected.update(export(url+'/b','provider-b'))
    global_config=Path(os.environ['CODEX_HOME'])/'config.toml'
    global_config.write_text(export(url+'/global-b')['config'],encoding='utf-8')
    release.set();proc=await task
    try:
        assert proc._provider_launch.receipt['providerId']=='provider-a' and hits==['/a']
        assert global_config.read_text(encoding='utf-8')==export(url+'/global-b')['config']
    finally:await stop(proc)


@async_test
async def test_generation_change_while_receipt_save_waits_never_publishes(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url);proc=await spawn(a,s)
    w=worker.Worker('owned-race',s.id,a,process=proc,generation=1);worker.workers[w.worker_id]=w
    entered=asyncio.Event();release=asyncio.Event();commit=worker._commit_offline
    async def delayed(*args,**kwargs):
        entered.set();await release.wait();return await commit(*args,**kwargs)
    monkeypatch.setattr(worker,'_commit_offline',delayed)
    try:
        task=asyncio.create_task(worker._claim_offline_owner(w));await entered.wait()
        worker._bump_worker_generation(w);release.set()
        with pytest.raises(RuntimeError):await task
        assert w._provider_launch_binding is None
    finally:worker.workers.pop(w.worker_id,None);await stop(proc)


@async_test
async def test_cancel_preparation_retires_private_snapshot(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url)
    entered=threading.Event();release=threading.Event()
    def source(s):entered.set();assert release.wait(5);return copy.deepcopy(selected)
    a.provider_launch_source=source;task=asyncio.create_task(worker._spawn_process(s.id,a))
    assert await asyncio.to_thread(entered.wait,5);task.cancel();release.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert not list((root/'automation/private-launches').glob('*/config.toml')) and not hits


@pytest.mark.parametrize('bad',['private_file','signature','thread_provider'])
@async_test
async def test_changed_private_config_or_forged_receipt_fails_closed_and_cleans(tmp_path,monkeypatch,provider,bad):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url);accept=launch.Pending.accept
    if bad=='thread_provider':monkeypatch.setenv('PAN_TEST_FIXED_WRONG_THREAD_PROVIDER','1')
    async def altered(self,proc):
        if bad=='private_file':
            (self.home/'config.toml').write_text(export(url+'/wrong')['config'],encoding='utf-8')
        elif bad=='signature':
            read=proc.stdout.readline
            async def changed():
                line=await read();row=json.loads(line)
                if row.get('type')=='provider.launch.verified':row['signature']='0'*64
                return json.dumps(row).encode()+b'\n'
            proc.stdout.readline=changed
        return await accept(self,proc)
    monkeypatch.setattr(launch.Pending,'accept',altered)
    result=await worker._spawn_process(s.id,a)
    assert result=='Fixed provider launch was not confirmed'
    assert not list((root/'automation/private-launches').glob('*/config.toml'))
    assert hits==([] if bad=='private_file' else ['/a'])


@async_test
async def test_actual_ordered_file_save_failure_does_not_publish_receipt(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url);proc=await spawn(a,s)
    w=worker.Worker('owned-file-failure',s.id,a,process=proc,generation=1);worker.workers[w.worker_id]=w
    save=session._save_body
    def failure(*args,**kwargs):
        if any('providerLaunchBinding' in op for op in (kwargs.get('offline_override') or {}).get('execution_operations',{}).values()):
            raise OSError('owned replace failure')
        return save(*args,**kwargs)
    monkeypatch.setattr(session,'_save_body',failure)
    try:
        with pytest.raises(OSError):await worker._claim_offline_owner(w)
        assert w._provider_launch_binding is None and proc._provider_launch.owner is None
        assert not any('providerLaunchBinding' in op for op in s.execution_operations.values())
        disk=json.loads((session.SESSION_DIR/(s.id+'.json')).read_text(encoding='utf-8'))
        assert not any('providerLaunchBinding' in op for op in disk['execution_operations'].values())
    finally:worker.workers.pop(w.worker_id,None);await stop(proc)


@async_test
async def test_identity_changes_during_disk_write_and_unrelated_session_progresses(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url);proc=await spawn(a,s)
    w=worker.Worker('owned-file-race',s.id,a,process=proc,generation=1);worker.workers[w.worker_id]=w
    entered=threading.Event();release=threading.Event();save=session._save_body
    def blocked(*args,**kwargs):
        if any('providerLaunchBinding' in op for op in (kwargs.get('offline_override') or {}).get('execution_operations',{}).values()):
            entered.set();assert release.wait(5)
        return save(*args,**kwargs)
    monkeypatch.setattr(session,'_save_body',blocked)
    try:
        task=asyncio.create_task(worker._claim_offline_owner(w));assert await asyncio.to_thread(entered.wait,5)
        async with worker.queue_lock('unrelated-owned-launch'):worker._bump_worker_generation(w)
        release.set();await task
        assert w._provider_launch_binding is None
        assert launch.candidate(w,s,worker._offline.SERVICE_ID,retained_process(proc)) is None
    finally:release.set();worker.workers.pop(w.worker_id,None);await stop(proc)


@async_test
async def test_cancel_waiting_bootstrap_cleans_private_files_without_provider_request(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url);entered=asyncio.Event()
    async def waiting(self,proc):entered.set();await asyncio.Event().wait()
    monkeypatch.setattr(launch.Pending,'accept',waiting)
    task=asyncio.create_task(worker._spawn_process(s.id,a));await asyncio.wait_for(entered.wait(),5);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert not list((root/'automation/private-launches').glob('*/config.toml')) and not hits


@async_test
async def test_exporter_error_is_sanitized(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url)
    def failed(session):raise ValueError('credential=must-not-leak')
    a.provider_launch_source=failed
    result=await worker._spawn_process(s.id,a)
    assert result=='Fixed provider launch configuration unavailable' and not hits


@async_test
async def test_new_runtime_cannot_adopt_prior_launch_receipt(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,selected,root=setup(tmp_path,monkeypatch,url);proc=await spawn(a,s)
    w=worker.Worker('owned-epoch',s.id,a,process=proc,generation=1);worker.workers[w.worker_id]=w
    try:
        monkeypatch.setattr(worker._offline,'SERVICE_ID','different-runtime')
        await worker._claim_offline_owner(w)
        assert w._provider_launch_binding is None and proc._provider_launch.owner is None
        assert not any('providerLaunchBinding' in r for r in s.execution_operations.values())
    finally:worker.workers.pop(w.worker_id,None);await stop(proc)
