"""Fresh production browser consuming actual original outbox WS frames."""
import asyncio
import copy
import json
from pathlib import Path
import subprocess

from fastapi import WebSocket, WebSocketDisconnect
from starlette.staticfiles import StaticFiles
from packages.core import session
from packages.jobs.final_service import FinalService
from tests.job_retirement_native_fixture import h as native_h
from tests.test_retirement_rev16_behavior import h
from tests.test_session_product_closure import config
from tests.test_job_final_http_service import enabled, launch


def test_fresh_browser_original_partial_outbox_ws_duplicate_and_runtime_form_http(h):
    from packages.web import server
    root = Path(__file__).resolve().parents[1]
    runner = root / 'packages/web/e2e/retirement-r25-ws.mjs'
    build = subprocess.run(['node', str(runner), '--build'], cwd=root, capture_output=True,
        text=True, encoding='utf-8', errors='replace', timeout=120)
    assert build.returncode == 0, build.stdout + build.stderr
    h.app.mount('/react', StaticFiles(directory=root / 'packages/web/test-results/retirement-r25-production', html=True))
    clients = set()
    ready = asyncio.Event()

    @h.app.websocket('/ws')
    async def ws(socket: WebSocket):
        await socket.accept()
        clients.add(socket)
        server.ws_clients.add(socket)
        ready.set()
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(socket)
            server.ws_clients.discard(socket)
            outbound = server._ws_outbound.pop(socket, None)
            if outbound:
                await outbound.close_now()

    job = enabled(h, config(h, 'session-broadcast', sessionIds=['ses_a', 'ses_b'], text='real fanout'))
    rid = launch(h, job)['runId']
    session.delete('ses_b')
    asyncio.run(h.service.dispatch_once())
    history = h.service.final.history(job['id'])
    row = next(r for r in history['outbox'] if r['channel'] == 'ws')
    row['jobId'] = job['id']  # deliver_outbox supplies this association to its adapter
    assert row['summary']['failedTargets'] == ['ses_b'] and row['status'] == 'pending'
    h.service.outbox_sender = server._send_final_notice

    @h.app.post('/owned/drive')
    async def drive():
        await asyncio.wait_for(ready.wait(), 8)
        await h.service.deliver_outbox()
        return {'ok': True}

    @h.app.post('/owned/duplicate')
    async def duplicate():
        await server._send_final_notice(copy.deepcopy(row))
        return {'ok': True}

    cfg = h.tmp / 'owned-r25-browser.json'
    cfg.write_text(json.dumps({'url': str(h.client.base_url).rstrip('/'), 'token': h.token,
        'outboxId': row['outboxId']}), encoding='utf-8')
    result = subprocess.run(['node', str(runner), str(cfg)], cwd=root, capture_output=True,
        text=True, encoding='utf-8', errors='replace', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    print('R25_BROWSER=' + json.dumps(evidence))
    assert len(session.get('ses_a').queue_pending) == 1
    template = next(t for t in h.client.get('/api/job-templates').json()['items']
                    if t['name'] == 'Runtime browser Template')
    assert template['jobDefinition']['deliveryRuntime']['owner'] == 'delivery-manager'
    frozen_effects = h.service.final.history(job['id'])['effects']
    cold = FinalService(h.root, h.automation.auth)
    cold.outbox_sender = server._send_final_notice
    try:
        asyncio.run(cold.deliver_outbox())
        asyncio.run(cold.dispatch_once())
        assert cold.final.history(job['id'])['effects'] == frozen_effects
        assert len(session.get('ses_a').queue_pending) == 1
        assert all(r['status'] == 'acked' for r in cold.final.history(job['id'])['outbox'])
    finally:
        asyncio.run(cold.close())
