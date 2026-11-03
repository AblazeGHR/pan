"""Original TerminalService and leases with the real non-durable local host."""
import sys
import time
from dataclasses import replace
import pytest
from packages.core.terminal.service import TerminalService,StartupFailed,CleanupUnconfirmed,DetachRefused
from packages.core.terminal.contracts import NotControlLeaseError,StaleLeaseError

native=pytest.mark.skipif(sys.platform not in {'linux','darwin'},reason='actual local POSIX host')


def wait(read,condition):
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        value=read()
        if condition(value):return value
        time.sleep(.02)
    raise AssertionError('owned host observation deadline')


def shell():
    return [sys.executable,'-u','-c',"import sys;print('HOST-READY',flush=True); "
        "exec(\"for line in sys.stdin: print('ECHO:'+line.strip(),flush=True)\")"]


@native
def test_original_service_registry_leases_disconnect_and_tail(tmp_path):
    s=TerminalService(tmp_path,shell_argv=shell())
    view=s.create(session_id='owned-session');tid=view['terminal_id']
    try:
        page=wait(lambda:s.read(tid),lambda p:b'HOST-READY' in p['data'])
        cursor=page['next_cursor'];pid=view['pid']
        observer=s.attach(tid,'observer',role='observer')
        with pytest.raises(NotControlLeaseError):s.input(tid,observer,b'forbidden\n')
        control=s.attach(tid,'controller',role='control',rows=24,cols=80)
        assert s.input(tid,control,'你好\n'.encode())['accepted'] is True
        page=wait(lambda:s.read(tid,cursor),lambda p:'ECHO:你好'.encode() in p['data'])
        assert page['seq']>=cursor and page['next_cursor']>cursor
        assert s.resize(tid,control,35,100)['pty']['accepted']
        s.release_attachment(control)
        with pytest.raises(StaleLeaseError):s.input(tid,control,b'old\n')
        again=s.attach(tid,'reconnected',role='control',rows=24,cols=80)
        assert s.get(tid)['pid']==pid
        assert s.input(tid,again,b'reconnected\n')['accepted']
        with pytest.raises(DetachRefused):s.detach(tid)
        snap=s.snapshot(tid)
        assert snap['fidelity']=='unavailable' and snap['cursors_valid'] is False
        assert s.registry.get(tid).process_identity['kind']
        assert not (tmp_path/'secrets').exists()
    finally:
        assert s.close(tid)['status']=='exited'
        s.shutdown()


@native
def test_lost_host_reconcile_unknown_duplicate_no_spawn(tmp_path,monkeypatch):
    s=TerminalService(tmp_path,shell_argv=shell());view=s.create();tid=view['terminal_id']
    other=TerminalService(tmp_path,shell_argv=shell())
    try:
        result=other.reconcile()
        assert result['counts']['unattributable']==1
        assert other.get(tid)['status']=='cleanup-failed'
        with pytest.raises(StartupFailed,match='existing-local-terminal-no-replay'):
            other.create(terminal_id=tid)
        assert len(other.registry.list())==1
        assert s._states[tid].client.backend.alive()
    finally:
        assert s.close(tid)['status']=='exited'
        s.shutdown()


@native
def test_unknown_close_retains_same_runtime_retry(tmp_path):
    s=TerminalService(tmp_path,shell_argv=shell());tid=s.create()['terminal_id']
    host=s._states[tid].client;probe=host.runtime._identity_probe
    try:
        host.runtime._identity_probe=lambda _:None
        with pytest.raises(CleanupUnconfirmed):s.close(tid)
        assert s._states[tid].client is host and host.backend.alive()
        assert s.registry.get(tid).status.value=='cleanup-failed'
    finally:
        host.runtime._identity_probe=probe
        assert s.close(tid)['status']=='exited'
        s.shutdown()


@native
def test_real_loopback_http_ws_original_router_permissions_and_reconnect(tmp_path):
    import asyncio,base64,json,socket,threading
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    import httpx,uvicorn,websockets
    from packages.web import terminal_api,terminal_ws
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
    port=listener.getsockname()[1];assert port not in {8767,8768}
    origin=f'http://127.0.0.1:{port}'
    service=TerminalService(tmp_path,shell_argv=shell())
    # Candidate assembly: original gates/router/WS, explicit test runtime;
    # no capability mutation or registry replacement. Product capability false.
    rt=terminal_api.TerminalRuntime(enabled=True,disabled_reason='',
        allowlist=terminal_api.build_allowlist({},port=port),service_factory=lambda:service)
    @asynccontextmanager
    async def lifespan(app):
        await terminal_api.start_runtime(app,runtime=rt)
        await terminal_ws.start_ws(app,runtime=rt)
        try:yield
        finally:
            await terminal_ws.stop_ws(app)
            await terminal_api.stop_runtime(app)
    app=FastAPI(lifespan=lifespan);app.include_router(terminal_api.router);app.include_router(terminal_ws.router)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);thread.start()
    wait(lambda:server.started,lambda x:x)
    headers={'Origin':origin,'Sec-Fetch-Site':'same-origin'}
    async def scenario():
        async with httpx.AsyncClient(base_url=origin,trust_env=False,headers=headers) as client:
            wait(lambda:rt.state,lambda x:x==terminal_api.STATE_READY)
            denied=await client.post('/api/terminals',json={},headers={'Origin':'http://untrusted.invalid'})
            assert denied.status_code==403 and not service.registry.list()
            unknown=await client.post('/api/terminals',json={'session_id':'unknown-owned-session'})
            assert unknown.status_code==422 and not service.registry.list()
            created=await client.post('/api/terminals',json={})
            assert created.status_code==200,created.text
            body=created.json();tid=body.get('result',{}).get('terminal_id')
            assert tid,body
            pid=service.get(tid)['pid']
            url=f'ws://127.0.0.1:{port}/ws/terminal/{tid}'
            async def receive(ws,kind):
                async with asyncio.timeout(6):
                    while True:
                        event=json.loads(await ws.recv())
                        if event['type']==kind:return event
            async with websockets.connect(url,origin=origin) as ws:
                await receive(ws,'hello')
                async def send(op,**fields):
                    await ws.send(json.dumps(dict(v=1,type='command',terminal_id=tid,op=op,**fields)))
                await send('resume',cursor='0');await receive(ws,'resume-result')
                await send('input',generation='1',data_b64=base64.b64encode(b'FORBIDDEN\n').decode())
                await receive(ws,'error')
                await ws.send(json.dumps(dict(v=1,type='command',terminal_id='forged-terminal',op='claim')))
                await receive(ws,'error')
                await send('claim');generation=(await receive(ws,'claim-result'))['generation']
                await send('input',generation=generation,data_b64=base64.b64encode('HTTP-你好\n'.encode()).decode())
                await receive(ws,'input-result')
                await send('resize',generation=generation,rows=32,cols=90)
                await receive(ws,'resize-result')
            async with websockets.connect(url,origin=origin) as ws:
                await receive(ws,'hello')
                assert service.get(tid)['pid']==pid
                await ws.send(json.dumps(dict(v=1,type='command',terminal_id=tid,op='resume',cursor='0')))
                await receive(ws,'resume-result')
                output=b''
                async with asyncio.timeout(6):
                    while b'ECHO:HTTP-' not in output:
                        event=json.loads(await ws.recv())
                        if event['type']=='output':output+=base64.b64decode(event['data_b64'])
                assert '你好'.encode() in output
            response=await client.post(f'/api/terminals/{tid}/close',json={})
            assert response.status_code==200,response.text
            assert service.get(tid)['status']=='exited'
    try:asyncio.run(scenario())
    finally:
        server.should_exit=True;thread.join(8);listener.close()
        assert not thread.is_alive()


def test_local_record_roundtrip_never_fakes_windows_identity():
    from packages.core.terminal.contracts import TerminalRecord
    r=TerminalRecord('owned',host_mode='local-posix',process_identity={'kind':'starttime','birth':'123','boot_id':'owned'})
    restored=TerminalRecord.from_dict(r.to_dict())
    assert restored.host_mode=='local-posix' and restored.process_identity==r.process_identity
    assert restored.process_created_at_filetime is None and restored.pipe is None and not restored.detached


def test_windows_default_service_keeps_original_runner(tmp_path):
    if sys.platform!='win32':pytest.skip('Windows original assembly only')
    s=TerminalService(tmp_path)
    assert s._local_posix is False and not tmp_path.joinpath('secrets').exists()


@native
def test_actual_host_process_loss_cannot_recover_or_replay(tmp_path):
    import subprocess,os,json
    from pathlib import Path
    code="""import json,os,sys
from packages.core.terminal.service import TerminalService
s=TerminalService(sys.argv[1],shell_argv=[sys.executable,'-c','import time;time.sleep(2)'])
v=s.create();print(json.dumps(v),flush=True)
os._exit(0)
"""
    child=subprocess.run([sys.executable,'-c',code,str(tmp_path)],capture_output=True,text=True,timeout=10,
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])})
    assert child.returncode==0,child.stderr
    view=json.loads(child.stdout.strip());tid=view['terminal_id']
    restored=TerminalService(tmp_path)
    result=restored.reconcile()
    assert result['counts']['unattributable']==1
    assert restored.registry.get(tid).process_identity
    assert restored.get(tid)['status']=='cleanup-failed'
    with pytest.raises(StartupFailed,match='existing-local-terminal-no-replay'):
        restored.create(terminal_id=tid)
    assert len(restored.registry.list())==1
    # The owned synthetic program has a finite lifetime; no recovery signal or
    # external PID killing is permitted after losing the original runtime.
