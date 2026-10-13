"""Opt-in real hy3 Worker/API/ConPTY probe; uses only a dedicated temporary Session.

Normal CLI authentication is used without reading tokens or changing HOME.
No full Pan server lifespan, external MCP, QQ or shared Session is started.
"""
import argparse
import os
import sys
from pathlib import Path
import tempfile
import asyncio
import json
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--real-hy3", action="store_true", required=True)
parser.add_argument("--browser", action="store_true", help="Also connect real Chromium components to the isolated native API")
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for key in list(os.environ):
    if key.startswith('PAN_'):
        os.environ.pop(key, None)

from packages.core import session, worker
from packages.core.adapters.cbc.native_adapter import CbcNativeAdapter
from packages.web import native_control_api as api
from fastapi import FastAPI
import httpx

isolated = Path(tempfile.mkdtemp(prefix='pan-cbc-api-real-'))
session.SESSION_DIR = isolated / 'sessions'
session._cache.clear()
session._all_loaded = True
worker._DEFAULTS_INITIALIZED = True
worker._WORKER_IDLE_SEC = 600
# No Pan server lifespan, QQ, external MCP, provider fallback or enrichment.
CbcNativeAdapter.mcp_args = lambda self, s: ['--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '', '--no-session-persistence', '--tools', 'PowerShell', '--settings', '{"trustAll":true,"autoUpdates":false}']
CbcNativeAdapter.enrich_after_result = lambda self, s: None
api._enter = lambda request: (None, None)
events = []
async def broadcast(event):
    events.append(event)
worker._bcast = broadcast

async def until(predicate, limit=60):
    async with asyncio.timeout(limit):
        while not predicate():
            await asyncio.sleep(.05)

async def main():
    sid = 'ses-cbc-proof-' + uuid.uuid4().hex
    cwd = isolated / 'work'
    cwd.mkdir()
    s = session.Session(id=sid, name='isolated CBC API proof', adapter='cbc', model='hy3', permission_mode='bypassPermissions', workdir=str(cwd), adapter_config={'native_control': True, 'output_mode': 'stream'})
    session._cache[sid] = s
    session.save(s)
    app = FastAPI()
    app.include_router(api.router)
    w = await worker.create_worker(sid)
    assert isinstance(w, worker.Worker), w
    pid = w.process.pid
    try:
        try:
            await until(lambda: w.native_kernel_ready, limit=20)
        except TimeoutError:
            terminal = bytes(w.native_output).decode('utf-8', errors='replace')
            print(json.dumps({'startup_failed': True, 'status': w.status, 'returncode': w.process.returncode, 'native_error': w.native_control_error, 'output_bytes': len(w.native_output), 'event_types': sorted({e.get('type', '') for e in events}), 'ui_markers': [word for word in ['Authentication required', 'trust', 'Error', 'Unknown', 'invalid', 'permission', 'Welcome', 'workspace', 'login'] if word.lower() in terminal.lower()]}), flush=True)
            raise
        response = await worker.send(w.worker_id, 'Use PowerShell exactly once: Start-Sleep -Seconds 20; Write-Output API_OK. Then reply exactly DONE.', source='user')
        assert response['status'] == 'queued', response
        await until(lambda: len(w.native_output) > 0)
        await until(lambda: bool(s.cli_session_id), limit=25)
        await until(lambda: w.status == 'running')
        if args.browser:
            import socket
            import uvicorn
            import psutil
            def kernel_identity():
                matches = [child for child in psutil.Process(pid).children(recursive=True)
                           if any(arg.replace(chr(92), '/').endswith('/bin/codebuddy') for arg in child.cmdline())]
                assert len(matches) == 1, 'expected exactly one native CBC CLI kernel'
                return (matches[0].pid, matches[0].create_time())
            identity = kernel_identity()
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            assert port not in (8767, 8768)
            http_server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, lifespan='off', access_log=False, log_level='warning'))
            server_task = asyncio.create_task(http_server.serve(sockets=[listener]))
            browser_process = None
            try:
                await until(lambda: http_server.started, limit=10)
                browser_process = await asyncio.create_subprocess_exec(
                    'node', str(ROOT / 'packages/web/e2e/cbc-native-real.e2e.mjs'),
                    f'http://127.0.0.1:{port}', sid, cwd=ROOT / 'packages/web',
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await asyncio.wait_for(browser_process.communicate(), timeout=45)
                assert browser_process.returncode == 0, stderr.decode(errors='replace')
                print(stdout.decode().strip(), flush=True)
                assert kernel_identity() == identity, 'native CBC PID/creation identity changed'
                print(json.dumps({'native_cli_pid': identity[0], 'same_kernel_after_browser_refresh_switch': True}), flush=True)
            finally:
                if browser_process is not None and browser_process.returncode is None:
                    browser_process.terminate()
                    await browser_process.wait()
                http_server.should_exit = True
                await asyncio.wait_for(server_task, timeout=10)
                listener.close()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://isolated') as client:
            url = '/api/sessions/' + sid + '/native-control'
            native = await client.post(url + '/mode', json={'mode': 'native'})
            assert native.status_code == 200, native.text
            state = native.json()['result']
            assert w.process.pid == pid and w.process.returncode is None
            steer = await worker.steer_worker(w.worker_id, 'MUST_NOT_STEER')
            assert steer == 'Adapter does not support native Steer', steer
            queued = await worker.send(w.worker_id, 'Reply exactly QUEUED. Do not use tools.', source='user')
            assert queued['status'] == 'queued', queued
            resized = await client.post(url + '/write', json={'workerId': w.worker_id, 'generation': state['generation'], 'rows': 37, 'cols': 101})
            assert resized.status_code == 200, resized.text
            stale = await client.post(url + '/write', json={'workerId': w.worker_id, 'generation': state['generation'] - 1, 'text': 'MUST_NOT_WRITE'})
            assert stale.status_code == 409, stale.text
            output = await client.get(url + '/output?cursor=0')
            assert output.status_code == 200 and output.json()['result']['data']
            await until(lambda: any(e.get('type') == 'worker.result' for e in events))
            assert s.result_cursor == 1 and s.queue_pending, 'native ownership must retain queue'
            assert s.last_result['status'] == 'done' and s.last_result['result'] == 'DONE', s.last_result
            pan = await client.post(url + '/mode', json={'mode': 'pan'})
            assert pan.status_code == 200, pan.text
            await until(lambda: s.result_cursor == 2)
            assert w.process.pid == pid and w.process.returncode is None
            assert s.result_cursor == 2 and not s.queue_pending, s.result_cursor
            assert s.last_result['status'] == 'done' and s.last_result['result'] == 'QUEUED', s.last_result
            print(json.dumps({'passed': True, 'layer': 'real Worker + ASGI native API + real hy3 CLI/ConPTY', 'session': sid, 'wrapper_pid': pid, 'same_runtime': True, 'resize': True, 'stale_input_rejected': True, 'output_cursor': True, 'terminal_results': s.result_cursor, 'native_queue_gate': True, 'bootstrap_queue_gate': 'covered by mock transport; real probe uses temporary CLI trustAll to prevent user settings writes', 'native_steer_rejected': True, 'evidence_root': str(isolated), 'credits': 'not independently measured'}, ensure_ascii=True), flush=True)
    finally:
        await worker.kill_worker(w.worker_id)

asyncio.run(main())
