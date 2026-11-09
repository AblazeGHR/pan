"""Original scheduled runtime broadcast through the UI auth assembly."""
import asyncio
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import pytest
from tests.support.jobs_owned_executor_server import app_for
from tests.test_jobs_owned_executor_harness import owned_http, wait


@pytest.mark.parametrize('auth_mode', ['bearer', 'cookie'])
def test_runtime_legacy_broadcast_cookie_assembly(tmp_path, auth_mode):
    (tmp_path / '.jobs-owned-harness').touch()
    app = app_for(tmp_path)
    from packages.automation import api
    from packages.jobs.final_service import definition, FinalService
    service = app.state.owned.service
    registry = service.creators.registry
    api.install(SimpleNamespace(app=app))
    automation = app.state.automation
    assert service.creators.registry is registry
    assert automation.final_jobs is app.state.job_final_service is service
    assert automation.auth is service.auth is app.state.owned.auth
    assert service.executor is service._product_executor
    with owned_http(app) as client:
        assert service.task is not None and not service.task.done()
        assert service.creators.registry is registry
        if auth_mode == 'bearer':
            info = client.get('/test/ready').json()
            client.headers['Authorization'] = 'Bearer ' + info['token']
        else:
            login = client.post('/api/automation/login', json={'key': automation.auth.key()})
            assert login.status_code == 200
            assert 'pan_automation_session' in client.cookies
            client.headers['Origin'] = str(client.base_url).rstrip('/')
            assert 'Authorization' not in client.headers
        body = {'kind': 'session-broadcast', 'name': 'runtime broadcast', 'target': {'sessionIds': ['owned-a', 'owned-b', 'owned-unknown']}, 'text': 'runtime original', 'schedule': {'type': 'once', 'at': (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()}, 'requestKey': 'runtime-fixture'}
        response = client.post('/api/jobs/legacy-create', json=body)
        assert response.status_code == 200, response.text
        jid = response.json()['job']['jobId']
        job = service.get('job', jid, actor={'type': 'user', 'id': 'local'})
        cfg = definition('job', job)
        assert job['profileId'] == service.profile_id
        assert job['createdBy'] == {'type': 'user', 'id': 'local'}
        assert cfg['steps'][0]['actionRef']['id'] == 'product_session_broadcast_runtime_v1'
        assert cfg['deliveryRuntime']['phase'] == 'before-reservation'
        assert cfg['steps'][0]['arguments']['text'] == {'from': 'input', 'path': 'delivery.text'}
        with service.transaction(readonly=True) as db:
            assert service._references(db, 'job', cfg, enabled=True)
        again = client.post('/api/jobs/legacy-create', json=body)
        assert again.status_code == 200 and again.json()['job']['jobId'] == jid
        assert app.state.owned.provider.calls == []  # schedule has not fired
        response = client.post('/api/jobs/' + jid + '/runs', json={'expectedRevision': job['configRevision'], 'requestKey': 'manual-owned-runtime', 'input': cfg['arguments'], 'cause': {'type': 'manual'}})
        assert response.status_code == 202, response.text
        rid = response.json()['runId']
        wait(lambda: client.get('/api/job-runs/' + rid).json(), lambda r: r['state'] == 'uncertain')
        rows = app.state.owned.provider.calls
        assert [r['sessionId'] for r in rows] == ['owned-a', 'owned-b', 'owned-unknown']
        assert all(r['text'] == 'runtime original' and r['effectKey']['runId'] == rid for r in rows)
        state = service.final.store.get(jid)['finalRuntime']
        reservation = state['plans'][rid]['runtimeDelivery']['execute']
        assert reservation['unknownPolicy'] == 'query-original'
        assert [c['status'] for c in reservation['children']] == ['accepted', 'accepted', 'unknown']
        before = (tmp_path / 'consumed.jsonl').read_bytes()
        cold = FinalService(service.root, service.auth)
        try:
            assert cold.get_run(rid, actor={'type': 'user', 'id': 'local'})['state'] == 'uncertain'
        finally:
            asyncio.run(cold.close())
        for _ in range(3):
            assert client.get('/api/job-runs/' + rid).json()['state'] == 'uncertain'
        assert (tmp_path / 'consumed.jsonl').read_bytes() == before



def test_production_legacy_process_reference(tmp_path):
    (tmp_path / '.jobs-owned-harness').touch()
    app = app_for(tmp_path)
    from packages.automation import api
    from packages.jobs.final_service import definition
    api.install(SimpleNamespace(app=app))
    with owned_http(app) as client:
        info = client.get('/test/ready').json()
        assert {'product_process', 'product_session_broadcast_runtime_v1', 'product_assign_runtime_v1'} <= set(info['actionRefs'])
        assert info['bindings']['sameService'] and info['bindings']['sameAuth'] and info['bindings']['defaultExecutor']
        provider = app.state.owned.service.creators.registry.get(info['timeProvider']['ref'])
        assert provider.health()['status'] == 'available'
        assert info['environment']['hostZone']
        client.headers['Authorization'] = 'Bearer ' + info['token']
        response = client.post('/api/jobs/legacy-create', json={'kind': 'background-process', 'name': 'production process', 'target': {'sessionId': 'owned-a'}, 'argv': info['scripts']['quick'], 'cwd': info['cwd'], 'requestKey': 'production-process'})
        assert response.status_code == 200, response.text
        jid = response.json()['job']['jobId']
        job = app.state.owned.service.get('job', jid, actor={'type': 'user', 'id': 'local'})
        assert definition('job', job)['steps'][0]['actionRef']['id'] == 'product_process'
        # A legacy background process starts on creation; reuse that Run when
        # present instead of admitting a second one under overlap=forbid.
        existing = client.get('/api/jobs/' + jid + '/runs').json()['items']
        if existing:
            rid = existing[0]['runId']
        else:
            response = client.post('/api/jobs/' + jid + '/runs', json={'expectedRevision': job['configRevision'], 'requestKey': 'process-run', 'input': {}, 'cause': {'type': 'manual'}})
            assert response.status_code == 202, response.text
            rid = response.json()['runId']
        wait(lambda: client.get('/api/job-runs/' + rid).json(), lambda r: r['state'] == 'completed')
        rows = client.get('/api/job-runs/' + rid + '/logs').json()['items']
        assert len(rows) == 1
        assert client.get(rows[0]['href']).json()['text'] == 'owned stdout\nowned stderr\n'


def test_scheduler_original_runtime_assign_clock_pulse(tmp_path):
    (tmp_path / '.jobs-owned-harness').touch()
    app = app_for(tmp_path)
    from packages.automation import api
    from packages.jobs.final_service import definition
    api.install(SimpleNamespace(app=app))
    with owned_http(app) as client:
        info = client.get('/test/ready').json()
        client.headers['Authorization'] = 'Bearer ' + info['token']
        at = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        response = client.post('/api/jobs/legacy-create', json={'kind': 'scheduled-task', 'name': 'owned clock assign', 'target': {'sessionId': 'owned-a'}, 'text': 'clock consumed', 'action': {'api': 'assign'}, 'schedule': [{'kind': 'interval', 'interval_sec': 5}], 'maxRuns': 1, 'requestKey': 'clock-runtime'})
        assert response.status_code == 200, response.text
        jid = response.json()['job']['jobId']
        job = app.state.owned.service.get('job', jid, actor={'type': 'user', 'id': 'local'})
        cfg = definition('job', job)
        assert cfg['steps'][0]['actionRef']['id'] == 'product_assign_runtime_v1'
        assert cfg['deliveryRuntime']['phase'] == 'before-reservation'
        rows = wait(lambda: client.get('/api/jobs/' + jid + '/runs').json()['items'], bool)
        assert len(rows) == 1
        rid = rows[0]['runId']
        done = wait(lambda: client.get('/api/job-runs/' + rid).json(), lambda r: r['state'] == 'completed')
        assert done['cause']['type'] == 'event'
        calls = app.state.owned.provider.calls
        assert len(calls) == 1 and calls[0]['text'] == 'clock consumed'
        assert calls[0]['effectKey']['runId'] == rid



def test_calendar_preflight_rejects_missing_dependency(tmp_path, monkeypatch):
    import builtins
    from tests.support.jobs_owned_executor_server import runtime_environment
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'tzlocal':
            raise ModuleNotFoundError('owned negative: tzlocal missing')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    with pytest.raises(RuntimeError, match='minimal-requirements') as caught:
        runtime_environment()
    assert isinstance(caught.value.__cause__, ModuleNotFoundError)


def test_owned_ui_created_target_is_consumed(tmp_path):
    from packages.core import session
    (tmp_path / '.jobs-owned-harness').touch()
    app = app_for(tmp_path)
    target = session.create('Owned UI selected target', adapter='cbc', workdir=str(tmp_path))
    provider = app.state.owned.provider
    key = {'runId': 'owned-ui', 'stepId': 'execute', 'targetId': target.id, 'operationOrdinal': 0}
    result = asyncio.run(provider.send_session(target.id, 'UI selected original', effect_key=key, source='automation'))
    assert result['consumption'] == 'confirmed'
    assert provider.calls[0]['sessionId'] == target.id
    rejected = asyncio.run(provider.send_session('../outside', 'reject', effect_key=key, source='automation'))
    assert rejected['status'] == 'rejected' and len(provider.calls) == 1
