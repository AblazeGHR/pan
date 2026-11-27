"""Pure policy checks; these do not prove local transport authentication."""
import pytest
import asyncio
import os
import json
from types import SimpleNamespace
from packages.web import script_auth


def profile():
    return script_auth.validate_profile({'enabled': True, 'sessions': ['ses_allowed'], 'rules': [
        {'method': method, 'path': path} for method, path in sorted(script_auth.RULES)]})


def test_known_watcher_operations_and_restrictions():
    p = profile()
    for method, path, body in [
        ('GET', '/api/automation/events?after=5&limit=100', {}),
        ('GET', '/api/sessions?summary=1', {}),
        ('GET', '/api/list', {}),
        ('GET', '/api/sessions/ses_allowed?historyLimit=1', {}),
        ('GET', '/api/sessions/ses_allowed/worker/delayed-restart', {}),
        ('POST', '/api/sessions/ses_allowed/worker/delayed-restart?existing_worker_only=true', {}),
        ('POST', '/api/sessions/wake', {'sessionIds': ['ses_allowed'], 'source': 'automation', 'clientMessageId': 'fixed'}),
    ]:
        assert script_auth.permit(p, method, path, body)


@pytest.mark.parametrize('method,path,body', [
    ('GET', '/api/automation/events?after=0&limit=101', {}),
    ('GET', '/api/automation/events?after=0&after=1', {}),
    ('GET', '/api/sessions?summary=0', {}),
    ('GET', '/api/sessions/ses_other?historyLimit=1', {}),
    ('GET', '/api/sessions/ses_allowed?historyLimit=2', {}),
    ('POST', '/api/sessions/ses_allowed/worker/delayed-restart', {}),
    ('POST', '/api/sessions/wake', {'sessionIds': ['ses_other'], 'source': 'automation', 'clientMessageId': 'x'}),
    ('POST', '/api/sessions/wake', {'sessionIds': [[]], 'source': 'automation', 'clientMessageId': 'x'}),
    ('GET', '/api/%73essions?summary=1', {}),
    ('POST', '/api/send', {}),
    ('GET', '/api/list?x=1', {}),
])
def test_refuses_scope_bypasses(method, path, body):
    assert not script_auth.permit(profile(), method, path, body)


def test_disabled_and_unsupported_rules_fail_closed():
    p = script_auth.validate_profile({'enabled': False, 'sessions': [], 'rules': []})
    assert not script_auth.permit(p, 'GET', '/api/list', {})
    with pytest.raises(ValueError):
        script_auth.validate_profile({'enabled': True, 'sessions': [], 'rules': [{'method': 'GET', 'path': '/api/**'}]})
    for extra in ({'targetMode': '*'}, {'targetMode': 'codex', 'sessions': ['s1']},
                  {'targetMode': 'codex', 'adapter': 'codex'}):
        with pytest.raises(ValueError):
            script_auth.validate_profile({'enabled': True, 'sessions': [], 'rules': [], **extra})


def test_duplicate_policy_keys_refused(tmp_path):
    policy = tmp_path / 'policy.json'
    policy.write_text('{"enabled":false,"enabled":true}', encoding='utf-8')
    with pytest.raises(ValueError): script_auth._read_json(policy)


def test_collection_projection_preserves_cursor_without_global_counts():
    from tests.test_codex_limit_rotate_script import issue
    event = issue()
    event['system']['sessionId'] = event['payload']['correlation']['sessionId'] = 'ses_allowed'
    event['payload']['observation'] = {'arbitrary': {'secret': 'private-payload-canary'}}
    response = {'events': [
        {'seq': 1, 'event': event},
        {'seq': 2, 'event': {'system': {'sessionId': 'ses_other'}, 'payload': {'sessionId': 'ses_allowed'}}},
        {'seq': 3, 'event': {'payload': {'sessionId': 'ses_allowed'}}},
    ], 'nextAfter': 3, 'storage': {'events': 99}}
    result = script_auth.project_collection(profile(), '/api/automation/events?after=0', response)
    assert [row['seq'] for row in result['events']] == [1]
    assert 'private-payload-canary' not in json.dumps(result)
    poisoned = issue()
    poisoned['system']['sessionId'] = poisoned['payload']['correlation']['sessionId'] = 'ses_allowed'
    poisoned['payload']['correlation']['extra'] = {'value': 'private-correlation-canary'}
    assert script_auth.project_collection(profile(), '/api/automation/events',
        {'events': [{'seq': 4, 'event': poisoned}], 'nextAfter': 4}) == {'events': [], 'nextAfter': 4}
    conflict = issue()
    conflict['system']['sessionId'] = conflict['payload']['correlation']['sessionId'] = 'ses_allowed'
    conflict['system']['sourceSessionId'] = 'ses_other'
    assert script_auth.project_collection(profile(), '/api/automation/events',
        {'events': [{'seq': 5, 'event': conflict}], 'nextAfter': 5}) == {'events': [], 'nextAfter': 5}
    assert result['nextAfter'] == 3 and 'storage' not in result
    sessions = script_auth.project_collection(profile(), '/api/sessions?summary=1',
        {'sessions': [{'id': 'ses_allowed'}, {'id': 'ses_other'}], 'total': 2})
    assert sessions == {'sessions': [{'id': 'ses_allowed'}]}
    with pytest.raises(ValueError):
        script_auth.project_collection(profile(), '/api/automation/events', {'events': [], 'nextAfter': True})


@pytest.mark.skipif(os.name != 'nt', reason='script client is unsupported on this platform')
def test_ipc_denial_never_falls_back_to_http(tmp_path, monkeypatch):
    from scripts.codex_limit_rotate import PanScriptIPC, AuthDenied
    from packages.plugins import ipc
    address = r'\\.\pipe\pan-script-123-' + 'a' * 32
    (tmp_path / 'script-ipc.json').write_text(json.dumps({'address': address, 'pid': 123}))
    monkeypatch.setattr(script_auth, 'load_client', lambda *_: '0' * 64)
    calls = []
    def refused(message, address):
        calls.append((message['operation'], address))
        return {'ok': False, 'error': {'code': 403, 'message': 'denied'}}
    monkeypatch.setattr(ipc, 'request', refused)
    with pytest.raises(AuthDenied): PanScriptIPC(tmp_path, 'owned').events(0)
    assert calls == [('script_http', address)]


def test_remote_pipe_locator_does_not_read_or_send_secret(tmp_path, monkeypatch):
    from scripts.codex_limit_rotate import PanScriptIPC, Uncertain
    (tmp_path / 'script-ipc.json').write_text(json.dumps({'address': r'\\remote\pipe\pan-script-123-' + 'a' * 32, 'pid': 123}))
    monkeypatch.setattr(script_auth, 'load_client', lambda *_: pytest.fail('must not decrypt for remote locator'))
    with pytest.raises(Uncertain): PanScriptIPC(tmp_path, 'owned').events(0)


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipe creation contract')
def test_actual_script_pipe_instances_reject_remote_flag_and_owner_acl(tmp_path, monkeypatch):
    from packages.core.terminal import win_pipe
    from packages.core.platform.script_ipc import PIPE_REJECT_REMOTE_CLIENTS
    from packages.plugins.ipc import Bridge, request
    from packages.plugins.runtime import Runtime
    seen = []
    original = win_pipe._k32.CreateNamedPipeW
    def capture(*args):
        seen.append((args[2], bool(args[7])))
        return original(*args)
    monkeypatch.setattr(win_pipe._k32, 'CreateNamedPipeW', capture)
    async def run():
        runtime = Runtime(lambda: None, lambda sid: None)
        async def handler(*args): return {'ok': True}
        runtime.script_http = handler
        bridge = Bridge(runtime, tmp_path, script_only=True)
        try:
            # Loopback remote syntax exercises the redirector without starting
            # SMB or contacting another host. An unavailable redirector is
            # recorded separately from a server-side rejection.
            import subprocess
            import sys
            remote = bridge.address.replace('\\\\.\\pipe\\', '\\\\127.0.0.1\\pipe\\', 1)
            probe = ("import _winapi,sys\n"
                "try:\n"
                " h=_winapi.CreateFile(sys.argv[1],0xC0000000,0,0,3,0,0)\n"
                "except OSError as e:\n"
                " print('remote_alias_error',e.winerror); sys.exit(0)\n"
                "_winapi.CloseHandle(h); print('unexpected_remote_open'); sys.exit(9)\n")
            checked = subprocess.run([sys.executable, '-c', probe, remote], capture_output=True,
                                     text=True, timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)
            assert checked.returncode == 0, checked.stdout + checked.stderr
            assert 'remote_alias_error' in checked.stdout
            print(checked.stdout.strip())
            message = {'session_id': '', 'operation': 'script_http', 'profile': 'x',
                       'credential': 'test', 'method': 'GET', 'path': '/api/list', 'body': {}}
            for _ in range(2):
                assert (await asyncio.to_thread(request, message, bridge.address))['ok'] is True
        finally:
            await bridge.close()
    asyncio.run(run())
    assert len(seen) >= 3
    assert all(mode & PIPE_REJECT_REMOTE_CLIENTS and acl for mode, acl in seen)


@pytest.mark.skipif(os.name != 'nt', reason='owner-only DPAPI profile is Windows-only')
def test_local_helper_init_and_revoke_without_secret_output(tmp_path):
    import subprocess
    import sys
    root = tmp_path / 'owned-cli'
    root.mkdir()
    policy_file = root / 'policy.json'
    policy_file.write_text(json.dumps({'enabled': True, 'sessions': ['ses_allowed'], 'rules': [
        {'method': 'GET', 'path': '/api/list'}]}))
    command = [sys.executable, '-m', 'packages.web.script_auth', 'init', '--data-dir', str(root),
               '--profile', 'watcher', '--policy', str(policy_file)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    secret = script_auth.load_client(root, 'watcher')
    assert secret not in result.stdout + result.stderr and secret not in json.dumps(command)
    assert script_auth.authenticate(root, 'watcher', secret)['enabled']
    result = subprocess.run([sys.executable, '-m', 'packages.web.script_auth', 'revoke', '--data-dir',
        str(root), '--profile', 'watcher'], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and secret not in result.stdout + result.stderr
    with pytest.raises(ValueError): script_auth.authenticate(root, 'watcher', secret)


@pytest.mark.skipif(os.name != 'nt', reason='owner-only DPAPI profile is Windows-only')
def test_private_profile_and_real_local_ipc_read_revoke(tmp_path, monkeypatch):
    from packages.plugins.ipc import Bridge, request
    from packages.plugins.runtime import Runtime
    from packages.web import server

    async def run():
        root = tmp_path / 'owned'
        root.mkdir()
        policy = {'enabled': True, 'sessions': ['ses_allowed'], 'rules': [
            {'method': method, 'path': path} for method, path in sorted(script_auth.RULES)]}
        script_auth.initialize(root, 'watcher', policy)
        secret = script_auth.load_client(root, 'watcher')
        assert secret not in (root / 'script-api/watcher/profile.json').read_text()
        assert secret.encode() not in (root / 'script-api/watcher/client.secret').read_bytes()
        async def io(fn, *args): return fn(*args)
        from tests.test_codex_limit_rotate_script import issue
        valid = issue()
        valid['system']['sessionId'] = valid['payload']['correlation']['sessionId'] = 'ses_allowed'
        event = {'seq': 1, 'event': valid}
        other = {'seq': 2, 'event': {'system': {'sessionId': 'ses_other'},
            'payload': {'correlation': {'sessionId': 'ses_other'}}}}
        svc = SimpleNamespace(engine=SimpleNamespace(io=io), events=SimpleNamespace(page=lambda after, limit:
            {'events': [event, other], 'nextAfter': 2}))
        monkeypatch.setattr(server.app.state, 'automation', svc, raising=False)
        async def inventory(): return {'workers': [{'sessionId': 'ses_allowed'}, {'sessionId': 'ses_other'}]}
        monkeypatch.setattr(server, 'api_list', inventory)
        async def detail(sid, historyLimit=0): return {'id': sid, 'historyLimit': historyLimit}
        async def restart(sid, existing_worker_only=False):
            return {'sid': sid, 'existingWorkerOnly': existing_worker_only}
        async def restart_state(sid): return {'delayedRestart': {'sessionId': sid}}
        async def wake(body): return {'ok': True, 'results': [{'status': 'queued', 'sessionId': body['sessionIds'][0]}]}
        monkeypatch.setattr(server, 'api_get_session', detail)
        monkeypatch.setattr(server, 'api_delayed_restart', restart)
        monkeypatch.setattr(server, 'api_delayed_restart_state', restart_state)
        monkeypatch.setattr(server, 'api_sessions_wake', wake)
        runtime = Runtime(lambda: None, lambda sid: None)
        script_auth.bind(runtime, root)
        bridge = Bridge(runtime, root, script_only=True)
        ordinary = Bridge(runtime, root)
        async def call(path, credential=secret, method='GET', body=None, session_id=''):
            return await asyncio.to_thread(request, {'operation': 'script_http', 'session_id': session_id,
                'profile': 'watcher', 'credential': credential, 'method': method, 'path': path,
                'body': body or {}}, bridge.address)
        try:
            general = await asyncio.to_thread(request, {'operation': 'script_http', 'session_id': '',
                'profile': 'watcher', 'credential': secret, 'method': 'GET', 'path': '/api/list',
                'body': {}}, ordinary.address)
            assert general['isError'] is True
            forged = await asyncio.to_thread(request, {'operation': 'script_http', 'session_id': '',
                'profile': 'watcher', 'credential': secret, 'method': 'GET', 'path': '/api/list',
                'body': {}, 'script_only': True}, ordinary.address)
            assert forged['isError'] is True
            wrong_channel = await asyncio.to_thread(request, {'operation': 'core_http', 'session_id': 'ses_allowed',
                'method': 'GET', 'path': '/api/list', 'body': {}}, bridge.address)
            assert wrong_channel['error']['code'] == 403
            result = await call('/api/automation/events?after=0&limit=100')
            assert result['nextAfter'] == 2 and [r['seq'] for r in result['events']] == [1]
            assert (await call('/api/list')) == {'workers': [{'sessionId': 'ses_allowed'}]}
            assert (await call('/api/sessions/ses_allowed?historyLimit=1'))['id'] == 'ses_allowed'
            assert (await call('/api/sessions/ses_allowed/worker/delayed-restart', method='GET'))['delayedRestart']['sessionId'] == 'ses_allowed'
            assert (await call('/api/sessions/ses_allowed/worker/delayed-restart?existing_worker_only=true', method='POST'))['existingWorkerOnly'] is True
            response = await call('/api/sessions/wake', method='POST', body={'sessionIds': ['ses_allowed'],
                                  'source': 'automation', 'clientMessageId': 'once'})
            assert response['results'][0]['status'] == 'queued'
            assert (await call('/api/list', credential='0' * 64))['error']['code'] == 403
            assert (await call('/api/list', session_id='ses_allowed'))['error']['code'] == 403
            assert (await call('/api/sessions/ses_other?historyLimit=1'))['error']['code'] == 403
            assert (await call('/api/list?unknown=1'))['error']['code'] == 403
            script_auth.revoke(root, 'watcher')
            assert (await call('/api/list'))['error']['code'] == 403
        finally:
            await bridge.close()
            await ordinary.close()
    asyncio.run(run())


@pytest.mark.skipif(os.name != 'nt', reason='owner-only DPAPI profile is Windows-only')
@pytest.mark.parametrize('target_mode', ['fixed', 'codex'])
def test_owned_server_watcher_first_scan_without_browser_login(tmp_path, monkeypatch, target_mode):
    import secrets
    import socket
    import httpx
    import sys
    import time
    from packages.automation.auth import UserAuth
    from scripts.codex_limit_rotate import PanScriptIPC, Ledger, observe
    from tests import test_real_queue_http_e2e as h

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', h.ROOT / 'tests/support/script_api_http_server.py')
    root = tmp_path / 'owned-server'
    root.mkdir()
    (root / 'config.json').write_text(json.dumps({'qq': {'enabled': False}, 'wechat': {'enabled': False},
        'remote': {'enabled': False}, 'tailscale': {'startupEnabled': False}, 'lan': {'startupEnabled': False}}))
    password = 'Aa1!' + secrets.token_urlsafe(24)
    UserAuth(root / 'automation').initialize(password)
    runtime = h._PanRuntime(root)
    client = httpx.Client(base_url=runtime.base_url, trust_env=False, timeout=10)
    try:
        runtime.start()
        assert client.get('/api/sessions').status_code == 401
        assert client.get('/api/sessions', headers={'X-Pan-Script-Profile': 'watcher',
                          'X-Pan-Script-Credential': 'synthetic'}).status_code == 401
        login = client.post('/api/automation/login', json={'key': password}, headers={'Origin': runtime.base_url})
        assert login.status_code == 200
        created = client.post('/api/sessions', json={'name': 'Owned-Script-Fixture', 'adapter': 'codex',
            'mcpServers': []}, headers={'Authorization': 'Bearer ' + login.json()['token']})
        assert 'id' in created.json(), created.text
        sid = created.json()['id']
        policy = {'enabled': True, 'sessions': [sid], 'rules': [
            {'method': method, 'path': path} for method, path in sorted(script_auth.RULES)]}
        if target_mode == 'codex':
            policy.update(targetMode='codex', sessions=[])
        script_auth.initialize(root, 'watcher', policy)
        if target_mode == 'codex':
            added = client.post('/api/sessions', json={'name': 'New-Codex-Fixture', 'adapter': 'codex',
                'mcpServers': []}, headers={'Authorization': 'Bearer ' + login.json()['token']}).json()['id']
            unrelated = client.post('/api/sessions', json={'name': 'Other-Fixture', 'adapter': 'cbc',
                'mcpServers': []}, headers={'Authorization': 'Bearer ' + login.json()['token']}).json()['id']
        api = PanScriptIPC(root, 'watcher')
        assert api.session(sid)['id'] == sid
        assert any(item['id'] == sid for item in api.get('/api/sessions?summary=1')['sessions'])
        if target_mode == 'codex':
            ids = {item['id'] for item in api.get('/api/sessions?summary=1')['sessions']}
            assert added in ids and unrelated not in ids
            from scripts.codex_limit_rotate import AuthDenied
            with pytest.raises(AuthDenied): api.session(unrelated)
        assert isinstance(api.events(0)['nextAfter'], int)
        ledger = Ledger(root / 'owned-ledger.json', True)
        observe(api, ledger)
        assert isinstance(ledger.data['cursor'], int) and not ledger.data['batches']
        assert api.restart(sid).get('error')  # only-existing-worker refuses offline
        assert api.restart_state(sid) is None
        auth = {'Authorization': 'Bearer ' + login.json()['token']}
        assert client.patch(f'/api/sessions/{sid}/queue/paused', json={'paused': True}, headers=auth).status_code == 200
        argv = [sys.executable, str(h.ROOT / 'scripts/codex_limit_rotate.py'),
                '--script-profile', 'watcher', '--script-data-dir', str(root),
                '--ledger', str(root / 'runner-ledger.json'), '--bridge-data', str(root / 'fake-bridge'), '--execute']
        # Preserve main's retired writer boundary and use one canonical run.
        assert client.post('/api/background-jobs', headers=auth, json={}).status_code in {401, 410}
        def run_scan(argv, key):
            catalog = client.get('/api/jobs/actions', headers=auth)
            assert catalog.status_code == 200, catalog.text
            action = next(item for item in catalog.json()['items'] if item['id'] == 'product_process_logs_v1')
            from packages.jobs.final_service import object_ref
            definition = {'name': 'Owned script IPC scan', 'steps': [{
                'stepId': 'scan', 'actionRef': object_ref('action', action),
                'arguments': {key: {'from': 'literal', 'value': value} for key, value in {
                    'argv': argv, 'cwd': str(h.ROOT), 'hideWindow': True}.items()},
                'completion': {'contract': 'process-exit'}}], 'retention': {'policyRef': 'default'}}
            created_job = client.post('/api/jobs', headers=auth, json={
                'definition': definition, 'expectedRevision': 0, 'requestKey': key + '-create'})
            assert created_job.status_code == 201, created_job.text
            job = created_job.json()['result']['object']
            enabled = client.post('/api/jobs/' + job['id'] + '/operations', headers=auth, json={
                'operation': 'enable', 'expectedRevision': job['configRevision'],
                'expectedStateRevision': job['stateRevision'], 'parameters': {}, 'requestKey': key + '-enable'})
            assert enabled.status_code == 200, enabled.text
            job = enabled.json()['result']['object']
            admitted = client.post('/api/jobs/' + job['id'] + '/runs', headers=auth, json={
                'expectedRevision': job['configRevision'], 'requestKey': key + '-run',
                'input': {}, 'cause': {'type': 'manual'}})
            assert admitted.status_code == 202, admitted.text
            run_id = admitted.json()['runId']
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                response = client.get('/api/job-runs/' + run_id, headers=auth)
                assert response.status_code == 200, response.text
                run = response.json()
                assert run['jobId'] == job['id']
                if run.get('state') in {'completed', 'failed', 'cancelled', 'uncertain'}: break
                time.sleep(.1)
            assert run['state'] == 'completed', run
            references = client.get('/api/job-runs/' + run_id + '/logs', headers=auth)
            assert references.status_code == 200, references.text
            items = references.json()['items']
            assert len(items) == 1 and items[0]['status'] == 'available', items
            from packages.jobs.independent_executor import ExecutionStore, effect_id
            store = ExecutionStore(root / 'job-final/executions')
            execution = store.get(effect_id(items[0]['effectKey']))
            assert execution['effectKey']['runId'] == run_id
            assert execution['result']['receipt']['exitCode'] == 0
            assert execution['result']['cleanupProof']['ownedTree']['confirmed']
            logged = client.get(items[0]['href'], headers=auth)
            assert logged.status_code == 200, logged.text
            log = logged.json()['text']
            return job, log
        job, log = run_scan(argv, 'initial-scan')
        assert json.loads((root / 'runner-ledger.json').read_text())['cursor'] >= 0
        assert 'scan_complete' in log
        secret = script_auth.load_client(root, 'watcher')
        assert secret not in log and secret not in json.dumps(job) and secret not in json.dumps(argv)
        assert secret not in json.dumps(dict(os.environ))
        # Seed only this isolated service's event store: one fully filtered
        # page, one partially visible page, then another visible quota event.
        from packages.automation.event_store import EventStore
        from tests.test_codex_limit_rotate_script import issue
        store = EventStore(root / 'automation')
        start = 0
        while True:
            raw = store.page(start, 100)
            if raw['nextAfter'] == start:
                break
            start = raw['nextAfter']
        fixture_events = []
        for n in range(201):
            if n in (100, 200):
                event = issue('fixture-quota-' + str(n))
                event['system']['sessionId'] = event['payload']['correlation']['sessionId'] = sid
            else:
                event = {'id': 'fixture-filtered-' + str(n), 'type': 'fixture.other', 'payload': {}}
            fixture_events.append(event)
        assert httpx.post(runtime.base_url + '/__test/script-api-events', trust_env=False,
                          json={'events': fixture_events}).status_code == 401
        seeded = client.post('/__test/script-api-events', headers=auth, json={'events': fixture_events})
        assert seeded.status_code == 200, seeded.text
        assert seeded.json() == {'appended': 201}
        first = api.events(start)
        assert first['events'] == [] and first['nextAfter'] > start
        second = api.events(first['nextAfter'])
        assert len(second['events']) == 1 and second['nextAfter'] > second['events'][0]['seq']
        third = api.events(second['nextAfter'])
        assert len(third['events']) == 1
        runner_ledger = root / 'readonly-ledger.json'
        original = {'version': 1, 'cursor': start, 'pending': {}, 'seen': {}, 'batches': []}
        runner_ledger.write_text(json.dumps(original))
        argv = [sys.executable, str(h.ROOT / 'scripts/codex_limit_rotate.py'), '--script-profile', 'watcher',
                '--script-data-dir', str(root), '--ledger', str(runner_ledger), '--bridge-data', str(root / 'fake-bridge')]
        _, output = run_scan(argv, 'filtered-scan')
        health = next(json.loads(line) for line in output.splitlines() if '"scan_complete"' in line)
        assert health['cursor'] >= third['nextAfter'] and health['pendingCount'] == 2
        assert json.loads(runner_ledger.read_text()) == original and not (root / 'fake-bridge').exists()
        script_auth.revoke(root, 'watcher')
        from scripts.codex_limit_rotate import AuthDenied
        with pytest.raises(AuthDenied): api.events(0)
        assert client.get('/api/sessions', headers={'Authorization': 'Bearer invalid'}).status_code == 401
    finally:
        client.close()
        runtime.stop()
    assert h._port_is_free(port)


@pytest.mark.skipif(os.name != 'nt', reason='owner-only DPAPI profile is Windows-only')
@pytest.mark.parametrize('target_mode', ['fixed', 'codex'])
def test_local_ipc_watcher_simulated_rotation_once(tmp_path, monkeypatch, target_mode):
    from packages.plugins.ipc import Bridge
    from packages.plugins.runtime import Runtime
    from packages.web import server
    from scripts import codex_limit_rotate as rotate
    from tests.test_codex_limit_rotate_script import issue, detail, RecoveryBridge

    async def run():
        root = tmp_path / 'owned-rotation'
        root.mkdir()
        policy = {'enabled': True, 'sessions': ['s1', 's2'], 'rules': [
            {'method': method, 'path': path} for method, path in sorted(script_auth.RULES)]}
        if target_mode == 'codex':
            policy.update(targetMode='codex', sessions=[])
        script_auth.initialize(root, 'watcher', policy)
        events, effects, receipts = [], [], {}
        async def io(fn, *args): return fn(*args)
        def page(after, limit):
            rows = [x for x in events if x['seq'] > after][:limit]
            return {'events': rows, 'nextAfter': rows[-1]['seq'] if rows else after}
        monkeypatch.setattr(server.app.state, 'automation', SimpleNamespace(engine=SimpleNamespace(io=io),
            events=SimpleNamespace(page=page)), raising=False)
        workers = [{'sessionId': 's1', 'workerId': 'w1', 'generation': 1, 'status': 'idle'},
                   {'sessionId': 's2', 'workerId': 'w2', 'generation': 1, 'status': 'running'},
                   {'sessionId': 's3', 'workerId': 'private-other-worker', 'generation': 1, 'status': 'idle'}]
        async def inventory(): return {'workers': workers}
        async def read(sid, historyLimit=0):
            if sid != 's1':
                return {'id': sid}
            result = detail(issue())
            result['lastResult']['reportDiagnostic']['runtimeIssue']['observation'] = {'raw': {'text': 'private-diagnostic-canary'}}
            return result
        async def store(fn, *args, **kwargs): return fn(*args, **kwargs)
        registry = {'s1': 'codex', 's2': 'codex', 's3': 'cbc'}
        async def restart(sid, existing_worker_only=False):
            assert existing_worker_only
            w = next(w for w in workers if w['sessionId'] == sid)
            receipts[sid] = {'sessionId': sid, 'workerId': w['workerId'], 'generation': w['generation'],
                'action': 'restart', 'status': 'pending', 'revision': len(effects) + 1}
            effects.append(('restart', sid))
            return {'delayedRestart': receipts[sid]}
        async def restart_state(sid): return {'delayedRestart': receipts.get(sid)}
        async def wake(body):
            effects.append(('wake', body['sessionIds'][0]))
            return {'ok': True, 'results': [{'sessionId': body['sessionIds'][0], 'status': 'queued', 'queueItemId': 'q-fixture'}]}
        monkeypatch.setattr(server, 'api_list', inventory)
        monkeypatch.setattr(server, 'api_get_session', read)
        monkeypatch.setattr(server, '_store_read', store)
        monkeypatch.setattr(server, '_summary_session_get', lambda sid: SimpleNamespace(id=sid, adapter=registry[sid]) if sid in registry else None)
        monkeypatch.setattr(server.sess, 'list_all', lambda load_history=False: [SimpleNamespace(id=sid, adapter=adapter) for sid, adapter in registry.items()])
        monkeypatch.setattr(server, 'api_delayed_restart', restart)
        monkeypatch.setattr(server, 'api_delayed_restart_state', restart_state)
        monkeypatch.setattr(server, 'api_sessions_wake', wake)
        runtime = Runtime(lambda: None, lambda sid: None)
        script_auth.bind(runtime, root)
        transport = Bridge(runtime, root, script_only=True)
        (root / 'script-ipc.json').write_text(json.dumps({'address': transport.address, 'pid': os.getpid()}))
        api = rotate.PanScriptIPC(root, 'watcher')
        ledger = rotate.Ledger(root / 'owned-ledger.json', True)
        switcher = RecoveryBridge()
        try:
            from scripts.codex_limit_rotate import AuthDenied
            visible = await asyncio.to_thread(api.get, '/api/list')
            assert 'private-other-worker' not in json.dumps(visible)
            assert 'private-diagnostic-canary' not in json.dumps(await asyncio.to_thread(api.session, 's1'))
            with pytest.raises(AuthDenied):
                await asyncio.to_thread(api.request, 'POST', '/api/sessions/wake', {'sessionIds': ['s3'], 'source': 'automation', 'clientMessageId': 'denied'})
            with pytest.raises(AuthDenied):
                await asyncio.to_thread(api.request, 'POST', '/api/sessions/wake', {'sessionIds': ['s1'], 'source': 'automation', 'clientMessageId': 'denied', 'adapter': 'codex'})
            await asyncio.to_thread(rotate.observe, api, ledger)
            events.append({'seq': 1, 'event': issue()})
            await asyncio.to_thread(rotate.observe, api, ledger)
            rotate.inspect_quota(ledger, switcher, now=lambda: 1000)
            await asyncio.to_thread(rotate.reconcile, api, switcher, ledger, lambda: 1000)
            assert switcher.switches == [('a', 'b')]
            assert effects == [('restart', 's1'), ('restart', 's2'), ('wake', 's1')]
            await asyncio.to_thread(rotate.reconcile, api, switcher, rotate.Ledger(root / 'owned-ledger.json', True), lambda: 1000)
            assert len(effects) == 3
            if target_mode == 'codex':
                registry['s2'] = 'cbc'
                with pytest.raises(AuthDenied):
                    await asyncio.to_thread(api.restart, 's2')
                del registry['s1']
                with pytest.raises(AuthDenied):
                    await asyncio.to_thread(api.session, 's1')
        finally:
            await transport.close()
    asyncio.run(run())
