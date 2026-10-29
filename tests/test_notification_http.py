"""Owned authenticated HTTP + production Chromium; recording QQ/System only."""
import json
import os
from pathlib import Path
import secrets
import subprocess

import httpx
from tests import test_real_queue_http_e2e as h

OUT = Path('artifacts/notification-kind-coverage/regression-http')


def test_notification_settings_live_and_background_delivery(tmp_path, monkeypatch):
    assert h.PORT not in {8767, 8768}
    nonce = secrets.token_hex(24)
    monkeypatch.setenv('PAN_TEST_NOTIFICATION_NONCE', nonce)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', Path(__file__).parent / 'support/notification_http_server.py')
    rt = h._PanRuntime(tmp_path / 'data')
    rt.data_root.mkdir()
    (rt.data_root / 'config.json').write_text(json.dumps({'memory': {'enabled': False}}), encoding='utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    evidence = {'port': h.PORT, 'realQQ': False, 'realSystem': False}
    fixture = {'x-notification-fixture': nonce}
    def records(): return rt.request('GET', '/__test/notification/records', headers=fixture)
    def emit(kind, identity):
        return rt.request('POST', '/__test/notification/emit', headers=fixture,
                          json={'owner': owner['id'], 'kind': kind, 'id': identity})
    try:
        rt.start()
        evidence['ownedPidBirths'] = [[pid, born] for pid, born, _ in rt._owned_processes]
        identity = rt.request('GET', '/__test/job-inbox-identity')
        assert Path(identity['sessions']).parent == rt.data_root
        key = (rt.data_root / 'automation/user.key').read_text(encoding='utf-8')
        token = rt.request('POST', '/api/automation/login', json={'key': key})['token']
        auth = {'Authorization': 'Bearer ' + token}
        catalog_path = '/api/notifications/error-types'
        assert httpx.get(rt.base_url + catalog_path, trust_env=False).status_code == 401
        catalog = rt.request('GET', catalog_path, headers=auth)['types']
        assert len(catalog) > 6 and len({r['id'] for r in catalog}) == len(catalog)
        assert 'codex_error_payload/usageLimitExceeded' in {r['id'] for r in catalog}
        assert rt.request('GET', '/api/settings/ui')['errorNotificationExclusions'] == {'enabled': False, 'types': []}
        assert httpx.put(rt.base_url + '/api/settings/ui', json={'errorNotificationExclusions': {'enabled': True, 'types': []}}, trust_env=False).status_code == 401
        for bad in ({'enabled': 'yes'}, {'enabled': True, 'types': ['unknown']}, {'enabled': True, 'types': [1]}):
            assert httpx.put(rt.base_url + '/api/settings/ui', json={'errorNotificationExclusions': bad}, headers=auth, trust_env=False).status_code == 422
        assert httpx.put(rt.base_url + '/api/settings/ui', json={'errorNotificationExclusionsEpoch': 'client'}, headers=auth, trust_env=False).status_code == 422
        for invalid in ({'sessionErrorBridge': 'yes'}, {'notifications': {'newError': {'qqSubscribe': True}}},
                        {'notifications': {'lockerUnread': {'system': 'yes'}}}):
            response = httpx.put(rt.base_url + '/api/settings/ui', json=invalid, headers=auth, trust_env=False)
            assert response.status_code == 422
        owner = rt.request('POST', '/api/sessions', json={'name': 'notification-owned-source', 'adapter': 'cbc', 'model': 'deepseek-v4-flash', 'mcpServers': []})
        assert 'id' in owner, owner
        emit('locker', 'history-off')
        browser = subprocess.run(['node', 'packages/web/e2e/notification-sources.mjs', rt.base_url, str(OUT.resolve()), owner['id']],
            env={**os.environ, 'PAN_TEST_NOTIFICATION_KEY': key}, capture_output=True, text=True, encoding='utf-8', timeout=120)
        (OUT / 'browser.log').write_text(browser.stdout + '\n' + browser.stderr, encoding='utf-8')
        assert browser.returncode == 0, browser.stderr
        # The browser has now closed. All remaining sends must be server owned.
        global_config = {'enabled': True, 'qqReport': True, 'qqTargets': ['user:123@456'], 'system': True, 'browser': True}
        rt.request('PUT', '/api/settings/ui', headers=auth, json={'sessionErrorBridge': True,
            'notifications': {'newError': global_config, 'lockerUnread': {**global_config, 'system': False, 'browser': False}}})
        rt.request('PATCH', '/api/sessions/' + owner['id'], headers=auth, json={'notificationSettings': {'system': True, 'browser': True}})
        rt.request('PUT', '/api/sessions/' + owner['id'] + '/qq-report', headers=auth,
                   json={'target': 'user:123@456', 'enabled': True, 'expectedEnabled': False})
        error = emit('error', 100)
        emit('error', 100)
        locker = emit('locker', 'new-background')
        def settled():
            value = records()
            return value if len(value['records']) == 3 and not any(r['state'] == 'pending' for r in value['attempts']) else None
        first = h._wait_for(settled, label='one System and one QQ error plus one Locker QQ')
        assert sum(r['channel'] == 'system' for r in first['records']) == 1
        assert sum(error['id'] in r.get('text', '') for r in first['records']) == 2
        assert sum(locker['id'] in r.get('text', '') for r in first['records']) == 1
        assert 'private-value' not in json.dumps(first)
        ref = next(row['ref'] for row in first['browserRefs'] if row['identity'] == error['id'])
        assert httpx.get(rt.base_url + '/api/notifications/browser-events/' + ref, trust_env=False).status_code == 401
        payload = rt.request('GET', '/api/notifications/browser-events/' + ref, headers=auth)
        assert error['id'] in payload['body']
        rt.request('PUT', '/api/settings/ui', headers=auth, json={'sessionErrorBridge': False,
            'notifications': {'newError': {**global_config, 'enabled': False}, 'lockerUnread': {'enabled': False}}})
        emit('error', 101)
        emit('locker', 'off-again')
        assert len(records()['records']) == 3
        # Re-enable never backfills those old identities.
        rt.request('PUT', '/api/settings/ui', headers=auth, json={'notifications': {'newError': global_config}})
        emit('error', 101)
        new_error = emit('error', 102)
        last = h._wait_for(lambda: records() if len(records()['records']) == 5 else None, label='global only new error')
        assert sum(new_error['id'] in r.get('text', '') for r in last['records']) == 2
        assert last['workers'] == 0
        before_epoch = rt.request('GET', '/api/settings/ui').get('errorNotificationExclusionsEpoch')
        exclusions = {'enabled': True, 'types': sorted(row['id'] for row in catalog if not row['id'].startswith('kind/'))}
        saved = rt.request('PUT', '/api/settings/ui', headers=auth, json={'errorNotificationExclusions': exclusions})
        epoch = saved['errorNotificationExclusionsEpoch']
        assert epoch and epoch != before_epoch
        same = rt.request('PUT', '/api/settings/ui', headers=auth, json={'errorNotificationExclusions': exclusions})
        assert same['errorNotificationExclusionsEpoch'] == epoch
        excluded = rt.request('POST', '/__test/notification/emit', headers=fixture,
            json={'owner': owner['id'], 'kind': 'error', 'id': 103, 'code': 'usageLimitExceeded'})
        current = records()
        assert not any(row['identity'] == excluded['id'] for row in current['attempts'])
        assert next(row for row in current['events'] if row['identity'] == excluded['id'])['exclusion_epoch'] == epoch
        rt.request('POST', '/__test/notification/emit', headers=fixture,
            json={'owner': owner['id'], 'kind': 'error', 'id': 104, 'code': 'unknownFutureCode'})
        h._wait_for(lambda: len(records()['records']) == 7, label='unknown structured type forwarded')
        rt.stop()
        rt.start()
        evidence['ownedPidBirths'] += [[pid, born] for pid, born, _ in rt._owned_processes]
        after = records()
        assert len(after['records']) == 7 and after['workers'] == 0
        assert rt.request('GET', '/api/settings/ui')['errorNotificationExclusions'] == exclusions
        evidence.update(sharedExclusions=True, catalogTypes=len(catalog), unknownDefaultForward=True, exclusionEpoch=bool(epoch), exclusionAuth=True)
        evidence.update(backgroundQQ=True, samePathSystemCount=1, duplicateEventNoReplay=True,
                        reopenNoHistory=True, restartNoReplay=True, authRead=True, workers=0,
                        deliveryStates=sorted({r['state'] for r in after['attempts']}))
    finally:
        rt.stop()
        evidence['ownedCleanupPassed'] = True
        evidence['portReleased'] = h._port_is_free(h.PORT)
        (OUT / 'evidence.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        assert evidence['portReleased']
