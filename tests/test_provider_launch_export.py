"""Synthetic private export protocol, actual owner-only files and launch lifecycle."""
import asyncio
import json
from pathlib import Path
import threading
import time

import pytest
from packages.core import session,worker
from packages.core.adapters.codex import provider_launch as launch
from packages.cc_switch_bridge import launch_export as protocol
from packages.automation.quota_policy import Store
from tests.test_automation_core import async_test
from tests.test_provider_launch import setup,spawn,stop,provider
from tests.support.private_export_owner import install


def test_default_off_and_old_cli_do_not_export(tmp_path,monkeypatch,provider):
    url,_=provider;a,s,_,root=setup(tmp_path,monkeypatch,url)
    owner=install(monkeypatch,a,url)
    monkeypatch.delenv(protocol.FLAG)
    assert launch.prepare(a,s,root) is None and not owner.calls
    monkeypatch.setenv(protocol.FLAG,'1');owner.supported=False
    assert launch.prepare(a,s,root) is None
    assert owner.calls==[['--version']]
    assert not (root/'automation/private-launches').exists()

@pytest.mark.parametrize('credential',[False,True])
@async_test
async def test_private_export_consumed_by_spawn_binding_and_cleaned(tmp_path,monkeypatch,provider,credential):
    url,_=provider;a,s,_,root=setup(tmp_path,monkeypatch,url)
    owner=install(monkeypatch,a,url,credential)
    proc=await spawn(a,s);pending=proc._provider_launch
    w=worker.Worker('export-worker',s.id,a,process=proc,generation=1);worker.workers[w.worker_id]=w
    try:
        await worker._claim_offline_owner(w)
        assert w._provider_launch_binding['privateExport']['providerRevision']==1
        disk=json.dumps(json.loads((session.SESSION_DIR/(s.id+'.json')).read_text(encoding='utf-8')))
        leaked=any(token in disk for token in owner.tokens.values());assert not leaked
        audit=w._provider_launch_binding['privateExport']
        assert set(audit)=={'contract','ownerEpoch','providerRevision','selectionRevision','credentialSource','issuedAt','expiresAt'}
        assert 'bundleMac' not in disk and 'privateBundleDigest' not in disk
    finally:worker.workers.pop(w.worker_id,None);await stop(proc)
    assert not pending.home.exists()

@pytest.mark.parametrize('bad',['nonce','contract','revision','expiry','unknown','oauth','credential','config','duplicate','mac','changed_bundle'])
def test_bad_receipts_or_material_never_produce_pending(tmp_path,monkeypatch,provider,bad):
    url,_=provider;a,s,_,root=setup(tmp_path,monkeypatch,url);owner=install(monkeypatch,a,url)
    def mutate(receipt,bundle):
        if bad=='nonce':receipt['requestId']='wrong'
        elif bad=='contract':receipt['contract']='future/v2'
        elif bad=='revision':receipt['providerRevision']=True
        elif bad=='expiry':receipt['expiresAt']=receipt['issuedAt']-1
        elif bad=='unknown':receipt['verified']=True
        elif bad=='oauth':receipt['credentialSource']['kind']='managed-oauth'
        elif bad=='credential':bundle['credential']['token']='invalid\ncredential'
        elif bad=='config':bundle['config']+='env_key="OTHER"\n'
    owner.mutate=mutate
    def after(receipt,path):
        if bad=='mac':receipt['bundleMac']='0'*64
        elif bad=='changed_bundle':(path.parent/'bundle.json').write_text('{}',encoding='utf-8')
        elif bad=='duplicate':(path.parent/'bundle.json').write_text('{"config":1,"config":2}',encoding='utf-8')
    owner.after=after
    with pytest.raises((ValueError,KeyError)):launch.prepare(a,s,root)
    assert not list((root/'automation/private-launches').glob('*/bundle.json'))

@async_test
async def test_export_switch_race_rejects_and_does_not_lock_other_session(tmp_path,monkeypatch,provider):
    url,_=provider;a,s,_,root=setup(tmp_path,monkeypatch,url);owner=install(monkeypatch,a,url)
    owner.release.clear()
    task=asyncio.create_task(asyncio.to_thread(launch.prepare,a,s,root))
    assert await asyncio.to_thread(owner.entered.wait,3)
    async with worker.queue_lock('unrelated-export-session'):owner.switch()
    owner.release.set()
    with pytest.raises(ValueError,match='revision conflict'):await task
    assert not list((root/'automation/private-launches').glob('*/bundle.json'))

@async_test
async def test_switch_after_atomic_capture_does_not_backfill_export_identity(tmp_path,monkeypatch,provider):
    url,_=provider;a,s,_,root=setup(tmp_path,monkeypatch,url);owner=install(monkeypatch,a,url)
    owner.after=lambda *args:owner.switch()
    pending=await asyncio.to_thread(launch.prepare,a,s,root)
    try:
        assert pending.provider=='provider-a' and owner.provider=='provider-b'
        assert pending.manifest['exportAudit']['providerRevision']==1
    finally:await asyncio.to_thread(pending.close)

@async_test
async def test_host_configuration_changes_during_export_reject(tmp_path,monkeypatch,provider):
    url,_=provider;a,s,_,root=setup(tmp_path,monkeypatch,url);owner=install(monkeypatch,a,url)
    owner.release.clear();task=asyncio.create_task(asyncio.to_thread(launch.prepare,a,s,root))
    assert await asyncio.to_thread(owner.entered.wait,3)
    store=Store(root/'automation');store.set_disabled(['provider-a'],store.read()['revision']);owner.release.set()
    with pytest.raises(ValueError,match='admission changed'):await task
    assert not list((root/'automation/private-launches').glob('*/bundle.json'))

@async_test
async def test_cancel_private_export_retires_helper_then_removes_secret_files(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,_,root=setup(tmp_path,monkeypatch,url);owner=install(monkeypatch,a,url)
    owner.release.clear();task=asyncio.create_task(worker._spawn_process(s.id,a))
    assert await asyncio.to_thread(owner.entered.wait,3)
    task.cancel();owner.release.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert not list((root/'automation/private-launches').glob('*/bundle.json')) and not hits

@pytest.mark.parametrize('failure',['save','thread'])
@async_test
async def test_failed_persistence_or_native_receipt_never_publishes(tmp_path,monkeypatch,provider,failure):
    url,_=provider;a,s,_,root=setup(tmp_path,monkeypatch,url);owner=install(monkeypatch,a,url)
    if failure=='thread':
        monkeypatch.setenv('PAN_TEST_FIXED_WRONG_THREAD_PROVIDER','1')
        result=await worker._spawn_process(s.id,a)
        assert result=='Fixed provider launch was not confirmed'
        assert not list((root/'automation/private-launches').glob('*/bundle.json'))
        return
    proc=await spawn(a,s)
    w=worker.Worker('export-save-fail',s.id,a,process=proc,generation=1);worker.workers[w.worker_id]=w
    async def failed(*args,**kw):raise OSError('injected export binding save failure')
    monkeypatch.setattr(worker,'_commit_offline',failed)
    try:
        with pytest.raises(OSError):await worker._claim_offline_owner(w)
        assert w._provider_launch_binding is None and proc._provider_launch.owner is None
    finally:worker.workers.pop(w.worker_id,None);await stop(proc)

def test_private_launch_output_redacts_secret_before_buffers_and_streams(monkeypatch):
    import io
    from types import SimpleNamespace
    from packages.core.adapters.codex import app_server_wrapper as wrapper
    stdout=io.BytesIO();stderr=io.BytesIO()
    monkeypatch.setattr(wrapper.sys,'stdout',SimpleNamespace(buffer=stdout))
    monkeypatch.setattr(wrapper.sys,'stderr',SimpleNamespace(buffer=stderr))
    monkeypatch.setattr(wrapper,'_STARTUP_OUTPUT',[])
    monkeypatch.setattr(wrapper,'_STARTUP_BYTES',0)
    monkeypatch.setattr(wrapper,'_STARTUP_OVERFLOW',False)
    monkeypatch.setattr(wrapper,'_LAUNCH_OUTPUT_SECRETS',(b'synthetic-sensitive-value',))
    wrapper._write_stderr('error synthetic-sensitive-value\n')
    wrapper._write_stdout({'type':'result','result':'synthetic-sensitive-value'})
    assert b'synthetic-sensitive-value' not in repr(wrapper._STARTUP_OUTPUT).encode('utf-8')
    wrapper._write_launch_receipt({'type':'provider.launch.verified'})
    wrapper._write_stdout({'type':'result','result':'synthetic-sensitive-value'})
    assert b'synthetic-sensitive-value' not in stdout.getvalue()+stderr.getvalue()
    assert b'private-launch-redacted' in stderr.getvalue()

@pytest.mark.parametrize('case',['oversize','hardlink','public_acl','expired_before_accept','env_changed','bundle_changed'])
@async_test
async def test_private_material_boundary_rechecks(tmp_path,monkeypatch,provider,case):
    import os
    from packages.core.terminal import win_pipe
    url,_=provider;a,s,_,root=setup(tmp_path,monkeypatch,url);owner=install(monkeypatch,a,url)
    if case in ('oversize','hardlink','public_acl'):
        def after(receipt,path):
            bundle=path.parent/'bundle.json'
            if case=='oversize':bundle.write_bytes(b'x'*(protocol.MAX_BUNDLE+1))
            elif case=='hardlink':os.link(bundle,path.parent/'alias')
            else:
                bundle.unlink();bundle.write_text('{}',encoding='utf-8')
                from packages.core.terminal.secret_store import SecretStore
                original=SecretStore.verify_owner_only
                def reject(self,p):
                    if p==bundle:raise ValueError('synthetic insecure ACL')
                    return original(self,p)
                monkeypatch.setattr(SecretStore,'verify_owner_only',reject)
        owner.after=after
        with pytest.raises(ValueError):await asyncio.to_thread(launch.prepare,a,s,root)
        assert not list((root/'automation/private-launches').glob('*/bundle.json'))
        return
    pending=await asyncio.to_thread(launch.prepare,a,s,root)
    try:
        if case=='expired_before_accept':
            pending.manifest['exportAudit']['expiresAt']=0
            with pytest.raises(RuntimeError,match='expired'):await pending.accept(None)
        elif case=='bundle_changed':
            (pending.home/'bundle.json').write_text('{}',encoding='utf-8')
            with pytest.raises(ValueError,match='changed'):pending.environment()
        else:
            # Environment is built once for the owned child; a mutated parent
            # token is stripped and replaced from the authenticated private file.
            key=pending.manifest['pin']['provider']['env_key']
            monkeypatch.setenv(key,'untrusted-inherited-value')
            env=pending.environment()
            matched=env[key]==owner.tokens['provider-a'];assert matched
            assert env['CODEX_HOME']==str(tmp_path/'unused-global-codex')
    finally:await asyncio.to_thread(pending.close)

@pytest.mark.parametrize('race',['aba','provider-edit','credential-edit'])
@async_test
async def test_monotonic_revisions_fence_aba_and_in_place_edits(tmp_path,monkeypatch,provider,race):
    url,_=provider;a,s,_,root=setup(tmp_path,monkeypatch,url);owner=install(monkeypatch,a,url)
    owner.release.clear();task=asyncio.create_task(asyncio.to_thread(launch.prepare,a,s,root))
    assert await asyncio.to_thread(owner.entered.wait,3)
    if race=='aba':
        owner.switch()
        with owner.lock:owner.provider='provider-a';owner.revision+=1
    else:
        with owner.lock:
            owner.revision+=1
            if race=='credential-edit':owner.tokens['provider-a']='replacement-synthetic-credential'
    owner.release.set()
    with pytest.raises(ValueError,match='revision conflict'):await task
    assert not list((root/'automation/private-launches').glob('*/bundle.json'))


def test_bridge_private_export_is_internal_bounded_and_does_not_reuse_panel_lock(tmp_path,monkeypatch):
    from packages.cc_switch_bridge.provider import Bridge
    calls=[]
    def runner(args):calls.append(args);return {'receipt':'safe'}
    bridge=Bridge(tmp_path,runner=runner)
    monkeypatch.setattr(bridge,'_lock',lambda:pytest.fail('must not hold Panel bridge lock'))
    request=(tmp_path/'request.json').resolve()
    assert bridge.private_launch_export(request)=={'receipt':'safe'}
    assert calls==[['--app','codex','provider','launch-export',str(request),'--json']]
    with pytest.raises(ValueError):bridge.private_launch_export('relative.json')
    bridge.runner=lambda args:{'oversize':'x'*20000}
    with pytest.raises(ValueError):bridge.private_launch_export(request)
    # Deliberately no plugin-facing operation/capability for private export.
    capabilities=json.loads(Path('packages/cc_switch_bridge/capabilities.json').read_text(encoding='utf-8'))
    assert 'launch-export' not in json.dumps(capabilities)

@pytest.mark.parametrize('token',['quote-in-token-value"','backslash-in-token-value\\','space in token value','tiny','x'*16385])
def test_bearer_grammar_prevents_escaped_or_invalid_secret_transport(tmp_path,monkeypatch,provider,token):
    url,_=provider;a,s,_,root=setup(tmp_path,monkeypatch,url);owner=install(monkeypatch,a,url)
    owner.mutate=lambda receipt,bundle:bundle['credential'].update(token=token)
    with pytest.raises(ValueError,match='credential'):launch.prepare(a,s,root)
    assert not list((root/'automation/private-launches').glob('*/bundle.json'))


def test_duplicate_json_is_explicitly_rejected():
    with pytest.raises(ValueError,match='Duplicate'):protocol.decode(b'{"config":1,"config":2}')


def test_native_config_must_attest_exact_private_env_route():
    config={'model_provider':'fixed','model_providers':{'fixed':{'name':'local','base_url':'http://127.0.0.1/v1','wire_api':'responses','requires_openai_auth':False,'env_key':'OWNED_ENV'}}}
    assert launch.native_projection(config,'OWNED_ENV')['provider']['env_key']=='OWNED_ENV'
    with pytest.raises(ValueError,match='credential route'):launch.native_projection(config,'OTHER_ENV')
    config['model_providers']['fixed']['experimental_bearer_token']='forbidden-direct-token'
    with pytest.raises(ValueError):launch.native_projection(config,'OWNED_ENV')

@async_test
async def test_environment_private_reads_run_off_event_loop(tmp_path,monkeypatch,provider):
    url,_=provider;a,s,_,root=setup(tmp_path,monkeypatch,url);install(monkeypatch,a,url)
    loop_thread=threading.get_ident();environment=launch.Pending.environment;called=[]
    def checked(pending):
        called.append(threading.get_ident());assert threading.get_ident()!=loop_thread
        return environment(pending)
    monkeypatch.setattr(launch.Pending,'environment',checked)
    proc=await spawn(a,s)
    try:assert called
    finally:await stop(proc)

@async_test
async def test_upstream_failure_is_sanitized_and_private_files_retired(tmp_path,monkeypatch,provider):
    url,hits=provider;a,s,_,root=setup(tmp_path,monkeypatch,url);owner=install(monkeypatch,a,url)
    def fail(receipt,path):raise ValueError('must-not-escape-helper-private-material')
    owner.after=fail
    result=await worker._spawn_process(s.id,a)
    assert result=='Fixed provider launch configuration unavailable'
    assert not hits and not list((root/'automation/private-launches').glob('*/bundle.json'))
