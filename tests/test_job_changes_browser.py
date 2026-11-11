"""Production React, two clients, external canonical commit -> native WS refresh."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
from packages.jobs.window import command_window_options


def test_two_production_pages_refresh_external_commit_and_logout_stays_empty(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    repo = Path(__file__).resolve().parents[1]
    root = tmp_path / 'owned-profile'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    assert port not in {8767, 8768}
    with (tmp_path / 'host.log').open('wb') as log:
        child = subprocess.Popen([sys.executable, '-m', 'tests.support.scheduler_management_server',
            '--root', str(root), '--port', str(port), '--seed-time-template-target'], cwd=repo,
            stdout=log, stderr=subprocess.STDOUT, **command_window_options())
        try:
            url = f'http://127.0.0.1:{port}'
            with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
                for _ in range(100):
                    assert child.poll() is None
                    try:
                        if client.get('/test/ready').status_code == 200: break
                    except httpx.TransportError: pass
                    time.sleep(.1)
                key = (root / 'automation/user.key').read_text(encoding='utf8').strip()
                client.headers['Authorization'] = 'Bearer ' + client.post('/api/automation/login', json={'key':key}).json()['token']
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    try:
                        pages = [browser.new_page(), browser.new_page()]
                        errors, frames = [], []
                        for page in pages:
                            page.on('pageerror', lambda e: errors.append(str(e)))
                            page.on('websocket', lambda ws: ws.on('framereceived', lambda frame: frames.append(frame)))
                            page.goto(url + '/react/automation/canonical')
                            page.get_by_role('button', name='Settings', exact=True).click()
                            page.get_by_label('Local user key', exact=True).fill(key)
                            page.get_by_role('button', name='Log in', exact=True).click()
                            page.get_by_role('button', name='All Jobs', exact=True).click()
                            expect(page.get_by_role('button', name='New Job', exact=True)).to_be_visible()
                        from packages.jobs.final_service import object_ref, definition
                        action = next(a for a in client.get('/api/jobs/actions').json()['items'] if a['id']=='product_session_message')
                        cfg = {'name':'Remote create', 'steps':[{'stepId':'message','actionRef':object_ref('action',action),
                            'arguments':{'sessionId':{'from':'literal','value':'owned-session'},
                            'text':{'from':'literal','value':'private body stays in authorized API'}},
                            'completion':{'contract':'durable-queue-accepted'}}], 'retention':{'policyRef':'test'}}
                        response = client.post('/api/jobs',json={'definition':cfg,'expectedRevision':0,'requestKey':'browser-external-create'})
                        assert response.status_code==201,response.text
                        job=response.json()['result']['object']
                        for page in pages:
                            expect(page.get_by_text('Remote create',exact=True)).to_be_visible()
                        cfg = definition('job',job);cfg['name']='Remote edit'
                        response=client.put('/api/jobs/'+job['id'],json={'definition':cfg,'expectedRevision':job['configRevision'],'requestKey':'browser-external-edit'})
                        assert response.status_code==200,response.text
                        job=response.json()['result']['object']
                        for page in pages:
                            expect(page.get_by_text('Remote edit',exact=True)).to_be_visible()
                        pages[0].get_by_role('button',name='Settings',exact=True).click()
                        pages[0].get_by_role('button',name='Log out',exact=True).click()
                        expect(pages[0].get_by_text('Sign in required',exact=True)).to_be_visible()
                        pages[0].get_by_role('button',name='All Jobs',exact=True).click()
                        expect(pages[0].get_by_text('Remote edit',exact=True)).to_have_count(0)
                        cfg=definition('job',job);cfg['name']='After logout'
                        response=client.put('/api/jobs/'+job['id'],json={'definition':cfg,'expectedRevision':job['configRevision'],'requestKey':'browser-external-after-logout'})
                        assert response.status_code==200,response.text
                        expect(pages[1].get_by_text('After logout',exact=True)).to_be_visible()
                        expect(pages[0].get_by_text('After logout',exact=True)).to_have_count(0)
                        assert any('canonical.changed' in f for f in frames if isinstance(f,str))
                        for frame in frames:
                            if isinstance(frame,str) and 'canonical.changed' in frame:
                                data=json.loads(frame)
                                assert not ({'jobId','object','arguments','runs','name'} & set(data))
                        assert client.get('/api/jobs/'+job['id']+'/runs').json()['items']==[]
                        assert client.get('/test/ready').json()['providerCalls']==0 and not errors
                    finally: browser.close()
        finally:
            child.terminate()
            child.wait(timeout=15)
