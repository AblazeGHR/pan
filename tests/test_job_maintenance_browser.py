"""Actual production bundle, HTTP and WS against an owned maintenance profile."""
import hashlib
import json
from pathlib import Path
import socket
import threading
import time
from contextlib import asynccontextmanager

import httpx
from playwright.sync_api import sync_playwright
import uvicorn

from packages.automation.auth import UserAuth
from packages.core import session
from packages.jobs.runtime_owner import OwnerFence
from tests.test_job_final_http_service import h  # noqa: F401


def test_production_browser_maintenance_diagnostics_and_blocked_delete(tmp_path, monkeypatch):
    from packages.web import server
    profile = session.SESSION_DIR.parent
    fence = OwnerFence(profile)
    with fence.transaction() as db:
        db.execute("INSERT INTO legacy_leases VALUES('unverified-original-lease')")
    authority_hash = hashlib.sha256(fence.path.read_bytes()).hexdigest()
    source = profile / 'background_jobs/jobs/unknown.json'
    source.parent.mkdir(parents=True)
    raw = b'broken historical data, never parse or execute'
    source.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (profile / 'job-dispositions.json').write_text(json.dumps({'schemaVersion': 1, 'records': [{
        'id': 'unrecognized-one', 'originalId': 'old-original-identity',
        'sourceFile': 'background_jobs/jobs/unknown.json', 'sha256': digest,
        'revision': 1, 'reasonCodes': ['corrupt_record'], 'executable': False}]}))
    auth = UserAuth(profile / 'automation')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    assert port not in {8767, 8768}
    owned = uvicorn.Server(uvicorn.Config(server.app, log_level='error', lifespan='on'))
    thread = threading.Thread(target=owned.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 8
        while not owned.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert owned.started
        url = f'http://127.0.0.1:{port}'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url + '/react/')
                page.get_by_role('heading', name='Job 维护模式').wait_for()
                page.get_by_label('本机用户密钥').fill(auth.key())
                page.get_by_role('button', name='查看保留数据').click()
                page.get_by_role('heading', name='old-original-identity').wait_for()
                assert page.get_by_role('button', name='删除原记录').is_disabled()
                assert page.get_by_text('未知效果未结算、不会重放。', exact=False).is_visible()
                assert page.get_by_label('本机用户密钥').count() == 0
                for width in (1440, 390, 320):
                    page.set_viewport_size({'width': width, 'height': 900})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(tmp_path / f'maintenance-{width}.png'), full_page=True)
                denied = page.request.post(url + '/api/jobs', data={'definition': {}, 'requestKey': 'never'})
                assert denied.status == 503
                assert denied.json()['error']['code'] == 'runtime_ownership_unverified'
                page.route('**/api/job-maintenance/status', lambda route: route.fulfill(status=503, body='unavailable'))
                page.reload()
                page.get_by_role('alert').filter(has_text='无法核实启动状态').wait_for()
                assert page.get_by_role('heading', name='Canonical Jobs').count() == 0
            finally:
                browser.close()
        assert httpx.get(url + '/api/health', trust_env=False).status_code == 503
    finally:
        owned.should_exit = True
        thread.join(8)
        listener.close()
        if thread.is_alive():
            pending=[]
            for task in owned.server_state.tasks:
                frames=[];current=task.get_coro();seen=set()
                while current is not None and id(current) not in seen:
                    seen.add(id(current))
                    frame=getattr(current,'cr_frame',None) or getattr(current,'gi_frame',None)
                    if frame is not None:frames.append((frame.f_code.co_filename,frame.f_lineno,frame.f_code.co_name))
                    current=getattr(current,'cr_await',None) or getattr(current,'gi_yieldfrom',None)
                pending.append({'task':repr(task),'awaitChain':frames})
            print('OWNED_MAINTENANCE_PENDING='+repr(pending))
        assert not thread.is_alive()
    assert source.read_bytes() == raw
    assert hashlib.sha256(fence.path.read_bytes()).hexdigest() == authority_hash


def test_normal_owner_production_history_delete_and_lost_reply_query(h, tmp_path, monkeypatch):
    """Owned no-writer fixture; no runtime loop, old owner or resource recovery."""
    from packages.web import server
    from packages.jobs.final_service import FinalService
    profile = h.root.parent
    source = profile / 'background_jobs/jobs/unknown-history.json'
    source.parent.mkdir(parents=True)
    raw = b'opaque unrecognized history, no execution definition'
    source.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (profile / 'job-dispositions.json').write_text(json.dumps({'schemaVersion': 1, 'records': [{
        'id': 'normal-preserved', 'originalId': 'original-history-identity',
        'sourceFile': 'background_jobs/jobs/unknown-history.json', 'sha256': digest,
        'revision': 1, 'reasonCodes': ['unknown_schema'], 'executable': False}]}))

    @asynccontextmanager
    async def no_effects(app, owner):
        # Reuse the production service port under the one real outer owner.
        # No FinalService/Automation start, watchdog, CLI or provider runs.
        service = FinalService(h.root, h.automation.auth, executor=h.service.executor, owner=owner)
        owner.set_state('active')
        app.state.automation = h.automation
        app.state.job_final_service = service
        try:
            yield
        finally:
            app.state.automation = None
            app.state.job_final_service = None

    monkeypatch.setattr(server, '_owned_lifespan', no_effects)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    assert port not in {8767, 8768}
    owned = uvicorn.Server(uvicorn.Config(server.app, log_level='error', lifespan='on'))
    thread = threading.Thread(target=owned.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 8
        while not owned.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert owned.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(f'http://127.0.0.1:{port}/react/automation/canonical')
                page.get_by_label('Local user key').fill(h.automation.auth.key())
                page.get_by_role('button', name='Log in', exact=True).click()
                page.get_by_role('heading', name='original-history-identity').wait_for()
                assert page.get_by_role('button', name='删除原记录').is_enabled()
                for width in (1440, 390, 320):
                    page.set_viewport_size({'width': width, 'height': 900})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                calls = []
                def lose_reply(route):
                    calls.append(route.request.post_data_json)
                    response = route.fetch()
                    assert response.status == 200
                    route.abort()  # receipt committed; UI must query, not resend
                page.route('**/api/job-maintenance/records/normal-preserved', lose_reply)
                page.once('dialog', lambda dialog: dialog.accept())
                page.get_by_role('button', name='删除原记录').click()
                page.get_by_role('button', name='查询原删除结果').wait_for(state='visible')
                page.get_by_role('button', name='查询原删除结果').click()
                page.get_by_text('原记录已删除，审计备份保留。', exact=True).wait_for()
                assert len(calls) == 1
                assert calls[0]['expectedSha256'] == digest
                assert calls[0]['expectedRevision'] == 1
                assert calls[0]['expectedSourceFile'] == 'background_jobs/jobs/unknown-history.json'
                assert not source.exists()
                assert [p.read_bytes() for p in (h.root / 'preserved-history').glob('*.bin')] == [raw]
                assert h.provider.calls == []
            finally:
                browser.close()
    finally:
        owned.should_exit = True
        thread.join(8)
        listener.close()
        if thread.is_alive():
            pending=[]
            for task in owned.server_state.tasks:
                frames=[];current=task.get_coro();seen=set()
                while current is not None and id(current) not in seen:
                    seen.add(id(current))
                    frame=getattr(current,'cr_frame',None) or getattr(current,'gi_frame',None)
                    if frame is not None:frames.append((frame.f_code.co_filename,frame.f_lineno,frame.f_code.co_name))
                    current=getattr(current,'cr_await',None) or getattr(current,'gi_yieldfrom',None)
                pending.append({'task':repr(task),'awaitChain':frames})
            print('OWNED_MAINTENANCE_PENDING='+repr(pending))
        assert not thread.is_alive()
