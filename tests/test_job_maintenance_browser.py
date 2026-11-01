"""Actual production bundle, HTTP and WS against an owned maintenance profile."""
import hashlib
import json
from pathlib import Path
import socket
import threading
import time

import httpx
from playwright.sync_api import sync_playwright
import uvicorn

from packages.automation.auth import UserAuth
from packages.core import session
from packages.jobs.runtime_owner import OwnerFence


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
        assert not thread.is_alive()
    assert source.read_bytes() == raw
    assert hashlib.sha256(fence.path.read_bytes()).hexdigest() == authority_hash
