"""Fresh authenticated Chromium verifies kind choices and actual admission."""
import json
import os
from pathlib import Path
import secrets
import subprocess

from tests import test_real_queue_http_e2e as h

OUT = Path('artifacts/notification-kind-coverage/owned-http')


def test_shared_kind_exclusions_browser_and_background(tmp_path, monkeypatch):
    assert h.PORT not in {8767, 8768}
    nonce = secrets.token_hex(24)
    monkeypatch.setenv('PAN_TEST_NOTIFICATION_NONCE', nonce)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', Path(__file__).parent / 'support/notification_http_server.py')
    rt = h._PanRuntime(tmp_path / 'data')
    rt.data_root.mkdir()
    (rt.data_root / 'config.json').write_text(json.dumps({'memory': {'enabled': False}}), encoding='utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    evidence = {'port': h.PORT, 'realProviders': False}
    fixture = {'x-notification-fixture': nonce}
    try:
        rt.start()
        evidence['ownedPidBirths'] = [[pid, born] for pid, born, _ in rt._owned_processes]
        identity = rt.request('GET', '/__test/job-inbox-identity')
        assert Path(identity['sessions']).parent == rt.data_root
        key = (rt.data_root / 'automation/user.key').read_text(encoding='utf-8')
        token = rt.request('POST', '/api/automation/login', json={'key': key})['token']
        auth = {'Authorization': 'Bearer ' + token}
        owner = rt.request('POST', '/api/sessions', json={'name': 'notification-kind-owned', 'adapter': 'cbc', 'model': 'deepseek-v4-flash', 'mcpServers': []})
        routes = {'enabled': False, 'system': True, 'browser': True, 'qqReport': True, 'qqTargets': ['user:123@456']}
        rt.request('PUT', '/api/settings/ui', headers=auth, json={'sessionErrorBridge': True,
            'errorNotificationExclusions': {'enabled': False, 'types': ['codex_error_payload/serverOverloaded']},
            'notifications': {'newError': routes}})
        rt.request('PATCH', '/api/sessions/' + owner['id'], headers=auth, json={'notificationSettings': {'system': True, 'browser': True}})
        rt.request('PUT', '/api/sessions/' + owner['id'] + '/qq-report', headers=auth,
            json={'target': 'user:123@456', 'enabled': True, 'expectedEnabled': False})
        browser = subprocess.run(['node', 'packages/web/e2e/notification-kind-coverage.mjs', rt.base_url, str(OUT.resolve()), owner['id']],
            env={**os.environ, 'PAN_TEST_NOTIFICATION_KEY': key}, capture_output=True, text=True, encoding='utf-8', timeout=120)
        (OUT / 'browser.txt').write_text(browser.stdout + '\n' + browser.stderr, encoding='utf-8')
        assert browser.returncode == 0, browser.stderr
        def records():
            return rt.request('GET', '/__test/notification/records', headers=fixture)
        before = records()
        assert len(before['records']) == 6 and before['workers'] == 0
        saved = rt.request('GET', '/api/settings/ui')['errorNotificationExclusions']
        assert saved == {'enabled': True, 'types': ['codex_error_payload/serverOverloaded', 'kind/tool_failure']}
        # Browser closed; a distinct kind with no CLI payload still forwards in
        # the server-owned union, while a selected tool kind remains excluded.
        for seq, kind in [(207, 'worker_crash'), (208, 'tool_failure')]:
            emitted = rt.request('POST', '/__test/notification/emit', headers=fixture,
                json={'owner': owner['id'], 'kind': 'error', 'id': seq, 'errorKind': kind})
            if kind == 'tool_failure':
                assert not any(a['identity'] == emitted['id'] for a in records()['attempts'])
        h._wait_for(lambda: len(records()['records']) == 8, label='closed-browser worker crash forwarded once per server path')
        rt.stop()
        rt.start()
        evidence['ownedPidBirths'] += [[pid, born] for pid, born, _ in rt._owned_processes]
        assert len(records()['records']) == 8 and records()['workers'] == 0
        assert rt.request('GET', '/api/settings/ui')['errorNotificationExclusions'] == saved
        evidence.update(backgroundKindForwarding=True, restartNoReplay=True, workers=0,
                        externalRecords=8, sessionAndGlobalPaths=True)
    finally:
        rt.stop()
        evidence['ownedCleanupPassed'] = True
        evidence['portReleased'] = h._port_is_free(h.PORT)
        (OUT / 'evidence.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        assert evidence['portReleased']
