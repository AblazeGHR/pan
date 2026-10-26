"""R6-C historical HTTP reads and retained R5 refusals, with owned data only.

Old CRUD/dispatch positives no longer describe the public contract. Calendar,
validation and execution invariants remain in store/cron/canonical suites.
No application lifespan, listener or service is started.
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.automation.auth import UserAuth
from packages.scheduler import api, store


WRITES_WITH_CALLERS = [
    ('POST', '/tasks'), ('PATCH', '/tasks/{id}'), ('DELETE', '/tasks/{id}'),
    ('POST', '/tasks/{id}/pause'), ('POST', '/tasks/{id}/resume'),
    ('POST', '/tasks/{id}/run-now'), ('POST', '/event-sources'),
    ('PUT', '/event-sources/{id}'), ('DELETE', '/event-sources/{id}'),
]


@pytest.fixture
def scheduler_env(tmp_path, monkeypatch):
    root = tmp_path / 'historical-jobs'
    monkeypatch.setenv('PAN_SCHEDULER_DIR', str(root))
    task = store.create_task({'name': 'historical', 'target_session_id': 'ses_ok',
                             'text': 'old task', 'schedule': {
                                 'kind': 'interval', 'intervalSec': 60}})
    auth = UserAuth(tmp_path / 'auth')
    token = auth.login(auth.key())
    app = FastAPI()
    app.state.automation = SimpleNamespace(auth=auth)
    app.include_router(api.router)
    client = TestClient(app)
    try:
        yield client, task, root, {'Authorization': 'Bearer ' + token}
    finally:
        client.close()
        api.bind(broadcast=None)


def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('method,path', WRITES_WITH_CALLERS)
@pytest.mark.parametrize('authorized', [False, True])
def test_retained_writes_refuse_before_validation_or_any_effect(scheduler_env, method, path, authorized):
    client, task, root, headers = scheduler_env
    events = []
    api.bind(events.append)
    before = contents(root)
    response = client.request(method, '/api/scheduler' + path.replace('{id}', task['id']),
                              json={'schedule': {'kind': 'invalid'}},
                              headers=headers if authorized else {})
    assert response.status_code == (410 if authorized else 401), response.text
    if authorized:
        assert response.json()['detail']['code'] == 'retired'
    assert contents(root) == before
    assert events == []


def test_list_get_and_runs_preserve_historical_camel_case(scheduler_env):
    client, task, root, headers = scheduler_env
    store.append_run({'run_id': 'old_run', 'task_id': task['id'],
                      'fire_at': '2026-09-16T09:00:00', 'status': 'completed'})
    before = contents(root)
    listed = client.get('/api/scheduler/tasks', headers=headers).json()
    assert listed['ok'] and [t['id'] for t in listed['tasks']] == [task['id']]
    public = client.get('/api/scheduler/tasks/' + task['id'], headers=headers).json()['task']
    assert public['targetSessionId'] == 'ses_ok'
    assert public['schedule']['intervalSec'] == 60
    assert public['nextRunAt'] == public['nextFireAt']
    assert public['lastRunAt'] == public['lastFireAt']
    assert 'createdAt' in public and 'runCount' in public
    runs = client.get('/api/scheduler/tasks/' + task['id'] + '/runs', headers=headers).json()
    assert runs['ok'] and runs['runs'][0]['runId'] == 'old_run'
    assert runs['runs'][0]['taskId'] == task['id']
    assert runs['runs'][0]['fireAt'] == '2026-09-16T09:00:00'
    assert contents(root) == before


@pytest.mark.parametrize('suffix', ['', '/runs'])
def test_unknown_history_is_not_found(scheduler_env, suffix):
    client, _, _, headers = scheduler_env
    result = client.get('/api/scheduler/tasks/sch_missing' + suffix, headers=headers).json()
    assert result['ok'] is False and result['error']['code'] == 'not_found'


@pytest.mark.parametrize('limit', [0, 501])
def test_runs_rejects_bad_limit(scheduler_env, limit):
    client, task, _, headers = scheduler_env
    result = client.get(f'/api/scheduler/tasks/{task["id"]}/runs?limit={limit}', headers=headers).json()
    assert result['error']['code'] == 'invalid_argument'


@pytest.mark.parametrize('count', [0, 99])
def test_next_preview_rejects_bad_count(scheduler_env, count):
    client, _, _, headers = scheduler_env
    result = client.get(f'/api/scheduler/next?count={count}', headers=headers).json()
    assert result['error']['code'] == 'invalid_argument'


def test_status_and_next_preview(monkeypatch, scheduler_env):
    client, task, root, headers = scheduler_env
    monkeypatch.setattr(api.scheduler_engine, 'status', lambda: {'running': False, 'tickSec': 1})
    before = contents(root)
    response = client.get('/api/scheduler/status', headers=headers)
    assert response.json()['status'] == {'running': False, 'tickSec': 1}
    preview = client.get(f'/api/scheduler/next?task_id={task["id"]}&count=2', headers=headers).json()
    assert preview['ok'] and len(preview['next']) == 2
    assert all(row['taskId'] == task['id'] for row in preview['next'])
    assert contents(root) == before


def test_on_event_supports_async_broadcast(scheduler_env):
    seen = []
    async def broadcast(event):
        seen.append(event)
    api.bind(broadcast)
    async def scenario():
        api.on_event({'type': 'scheduler.task.fired', 'taskId': 'sch_x'})
        await asyncio.sleep(0)
    asyncio.run(scenario())
    assert seen == [{'type': 'scheduler.task.fired', 'taskId': 'sch_x'}]
