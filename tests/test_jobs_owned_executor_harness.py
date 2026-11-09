"""Real process-log fixture reuse plus owned HTTP host acceptance."""
import asyncio
import json
import pytest

from tests.support.jobs_owned_executor_server import app_for, ConsumingProvider
from tests.test_job_process_logs import live, start, terminal, all_bytes, cold_read, wait
from tests.test_job_final_executors import attempt, key
from packages.jobs.final_runtime.executor import TypedExecutor, InMemoryReceiptStore


def test_http_host_pulse_and_original_executor(tmp_path):
    (tmp_path / '.jobs-owned-harness').touch()
    app = app_for(tmp_path)
    with owned_http(app) as client:
        info = client.get('/test/ready').json()
        assert info['ready'] and info['owner']['state'] == 'active'
        assert app.state.owned.service.executor is app.state.owned.service._product_executor
        assert client.get('/api/jobs/actions', headers={'Authorization': 'Bearer ' + info['token']}).status_code == 200
        assert not info['diagnostics']


@pytest.mark.parametrize('cancel', [False, True])
def test_original_process_output_attempt_and_cancel(live, cancel):
    c = live
    start(c, "import os,time\nopen('effects','a').write('once\\n')\nos.write(1,b'owned stdout\\n');os.write(2,b'owned stderr\\n')\nopen('ready','w').write('yes')\n" + ('time.sleep(300)\n' if cancel else ''))
    wait(lambda: (c.tmp / 'ready').exists(), bool)
    if cancel:
        assert 'owned stdout' in cold_read(c)['text']
        run = c.client.get('/api/job-runs/' + c.rid).json()
        result = c.client.post('/api/job-runs/' + c.rid + '/operations', json={'operation': 'cancel', 'expectedStateRevision': run['stateRevision'], 'requestKey': 'cancel'})
        assert result.status_code == 202, result.text
    terminal(c, 'cancelled' if cancel else 'completed')
    assert all_bytes(c)[0] == b'owned stdout\nowned stderr\n'
    assert (c.tmp / 'effects').read_text().splitlines() == ['once']


def test_owned_broadcast_consumes_each_target_unknown_no_replay(tmp_path):
    provider = ConsumingProvider(tmp_path)
    store = InMemoryReceiptStore()
    args = dict(attempt=attempt(), effect_key=key(target='fanout'), frozen_plan={'operation': 'session-broadcast', 'sessionIds': ['owned-a', 'owned-b', 'owned-unknown', 'owned-a'], 'text': 'owned delivery'})
    first = asyncio.run(TypedExecutor(session_provider=provider, receipt_store=store).execute(**args))
    assert [r['outcome'] for r in first['receipt']['targets']] == ['succeeded', 'succeeded', 'unknown']
    cold = ConsumingProvider(tmp_path)
    second = asyncio.run(TypedExecutor(session_provider=cold, receipt_store=store).execute(**args, retry=True))
    assert second == first and cold.calls == []
    rows = [json.loads(line) for line in (tmp_path / 'consumed.jsonl').read_text().splitlines()]
    assert [r['sessionId'] for r in rows] == ['owned-a', 'owned-b', 'owned-unknown']


def test_permission_revocation_and_owner_loss(live):
    from packages.jobs import final_contracts as fc

    c = live
    start(c, "import os;os.write(1,b'private')")
    terminal(c)
    grants = c.auth._default_permissions()
    grants['job']['read'] = True
    c.auth.change_permissions(grants, 0)
    actor = {'type': 'agent', 'id': 'owned-agent', 'revision': 1}
    assert asyncio.run(c.service.read_process_log(c.rid, c.aid, actor=actor))['text'] == 'private'
    grants['job']['read'] = False
    c.auth.change_permissions(grants, 1)
    with pytest.raises(fc.ContractError, match='revoked'):
        asyncio.run(c.service.read_process_log(c.rid, c.aid, actor=actor))
    c.service.owner.claim(0)
    c.service.owner.set_state('active')
    c.service.owner.release()
    with pytest.raises(fc.ContractError):
        with c.service.owner.writer(canonical=True):
            pytest.fail('lost owner must not enter canonical write')
    assert (c.tmp / 'business.py').exists()



def submit(client, service, arguments, operation, request):
    from packages.jobs.final_service import object_ref
    action = next(a for a in service.actions(actor={'type': 'user', 'id': 'local'})['items'] if a['id'] == ('product_process_cleanup_v1' if operation == 'process' else 'product_session_broadcast_v2'))
    response = client.post('/api/jobs', json={'definition': {'name': request, 'steps': [{'stepId': 'owned', 'actionRef': object_ref('action', action), 'arguments': {k: {'from': 'literal', 'value': v} for k, v in arguments.items()}, 'completion': {'contract': 'process-exit' if operation == 'process' else 'durable-queue-accepted'}}], 'retention': {'policyRef': 'test'}}, 'expectedRevision': 0, 'requestKey': request})
    assert response.status_code == 201, response.text
    job = response.json()['result']['object']
    response = client.post('/api/jobs/' + job['id'] + '/operations', json={'operation': 'enable', 'expectedRevision': job['configRevision'], 'expectedStateRevision': job['stateRevision'], 'parameters': {}, 'requestKey': request + '-enable'})
    assert response.status_code == 200, response.text
    job = response.json()['result']['object']
    response = client.post('/api/jobs/' + job['id'] + '/runs', json={'expectedRevision': job['configRevision'], 'requestKey': request + '-run', 'input': {}, 'cause': {'type': 'manual'}})
    assert response.status_code == 202, response.text
    return response.json()['runId']


def test_http_pulse_process_and_broadcast(tmp_path):
    (tmp_path / '.jobs-owned-harness').touch()
    app = app_for(tmp_path)
    with owned_http(app) as client:
        info = client.get('/test/ready').json()
        client.headers['Authorization'] = 'Bearer ' + info['token']
        service = app.state.owned.service
        rid = submit(client, service, {'argv': info['scripts']['quick'], 'cwd': info['cwd']}, 'process', 'process')
        run = wait(lambda: client.get('/api/job-runs/' + rid).json(), lambda r: r['state'] == 'completed')
        logs = client.get('/api/job-runs/' + rid + '/logs').json()['items']
        assert len(logs) == 1
        assert client.get(logs[0]['href']).json()['text'] == 'owned stdout\nowned stderr\n'
        long_id = submit(client, service, {'argv': info['scripts']['long'], 'cwd': info['cwd']}, 'process', 'long')
        long_logs = wait(lambda: pending_logs(client, long_id), bool)
        wait(lambda: client.get(long_logs[0]['href']).json().get('text', ''), lambda text: 'owned stderr' in text)
        running = client.get('/api/job-runs/' + long_id).json()
        response = client.post('/api/job-runs/' + long_id + '/operations', json={'operation': 'cancel', 'expectedStateRevision': running['stateRevision'], 'requestKey': 'cancel-long'})
        assert response.status_code == 202, response.text
        wait(lambda: client.get('/api/job-runs/' + long_id).json(), lambda r: r['state'] == 'cancelled')
        rid = submit(client, service, {'sessionIds': ['owned-a', 'owned-b', 'owned-a'], 'text': 'pulse broadcast'}, 'session-broadcast', 'broadcast')
        wait(lambda: client.get('/api/job-runs/' + rid).json(), lambda r: r['state'] == 'completed')
        assert [r['sessionId'] for r in app.state.owned.provider.calls] == ['owned-a', 'owned-b']
        rid = submit(client, service, {'sessionIds': ['owned-unknown'], 'text': 'lost acknowledgement'}, 'session-broadcast', 'unknown')
        wait(lambda: len(app.state.owned.provider.calls), lambda n: n == 3)
        before = (tmp_path / 'consumed.jsonl').read_bytes()
        from packages.jobs.final_service import FinalService
        cold = FinalService(service.root, app.state.owned.auth)
        try:
            observed = cold.get_run(rid, actor={'type': 'user', 'id': 'local'})
            assert observed['state'] != 'completed'
            for _ in range(3):
                assert client.get('/api/job-runs/' + rid).json()['state'] != 'completed'
                cold.get_run(rid, actor={'type': 'user', 'id': 'local'})
            assert (tmp_path / 'consumed.jsonl').read_bytes() == before
            import subprocess
            import sys
            code = "import json,sys; from packages.jobs.final_service import FinalService; from packages.jobs.final_api import CanonicalUserAuth; s=FinalService(sys.argv[1],CanonicalUserAuth(sys.argv[2])); print(json.dumps(s.get_run(sys.argv[3],actor={'type':'user','id':'local'})))"
            result = subprocess.run([sys.executable, '-c', code, str(service.root), str(tmp_path / 'automation'), rid], capture_output=True, text=True, timeout=20)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)['state'] != 'completed'
            assert (tmp_path / 'consumed.jsonl').read_bytes() == before
        finally:
            asyncio.run(cold.close())



from contextlib import contextmanager


@contextmanager
def owned_http(app):
    import socket
    import threading
    import httpx
    import uvicorn
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        wait(lambda: server.started, bool)
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=15) as client:
            yield client
    finally:
        server.should_exit = True
        thread.join(20)
        sock.close()
        assert not thread.is_alive()
        assert not server.lifespan.shutdown_failed
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', port)) != 0






def pending_logs(client, rid):
    response = client.get('/api/job-runs/' + rid + '/logs')
    assert response.status_code in (200, 404, 409), response.text
    if response.status_code != 200:
        assert 'error' in response.json(), response.text
        return []
    return response.json()['items']
