"""R6-D partial retirement: fail closed at real routes without starting a host."""
import asyncio
from pathlib import Path

import httpx
import pytest

from tests.test_scheduler_api import WRITES_WITH_CALLERS, contents, scheduler_env  # noqa: F401


@pytest.mark.parametrize('identity', ['anonymous', 'user', 'delegated'])
@pytest.mark.parametrize('method,path', WRITES_WITH_CALLERS)
def test_production_scheduler_writes_cannot_reach_either_owner(
        scheduler_env, tmp_path, monkeypatch, method, path, identity):
    from packages.automation.authorization import _MARKER, _SCOPE_KEY
    from packages.jobs.final_service import FinalService
    from packages.jobs.independent_executor import ExecutionStore
    from packages.jobs.runtime_owner import OwnerFence
    from packages.scheduler import store
    from packages.scheduler.creators import CreatorStore
    from packages.scheduler.legacy_bridge import LegacySchedulerBridge
    from packages.scheduler.time_events import Sources
    from packages.web.server import app

    client, task, _, headers = scheduler_env
    monkeypatch.setattr(app.state, 'automation', client.app.state.automation, raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail('Retired scheduler URL reached a store, owner or execution host')

    for cls in (FinalService, ExecutionStore, OwnerFence, CreatorStore, LegacySchedulerBridge, Sources):
        monkeypatch.setattr(cls, '__init__', forbidden)
    for name in ('create_task', 'update_task', 'delete_task', 'append_run',
                 'replace_task_schedule', 'set_task_misfire_policy'):
        monkeypatch.setattr(store, name, forbidden)

    async def scoped(scope, receive, send):
        if identity == 'delegated':
            scope[_SCOPE_KEY] = _MARKER
        await app(scope, receive, send)

    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(scoped), base_url='http://ipc') as http:
            return await http.request(method, '/api/scheduler' + path.replace('{id}', task['id']),
                                      headers=headers if identity == 'user' else {},
                                      json={'enabled': True, 'schedule': {'kind': 'interval', 'intervalSec': 1}})

    before = contents(tmp_path)
    response = asyncio.run(call())
    assert response.status_code == (401 if identity == 'anonymous' else 410), response.text
    if identity != 'anonymous':
        assert response.json()['detail']['code'] == 'retired'
    assert contents(tmp_path) == before


def test_retired_task_client_has_no_remaining_production_reference():
    root = Path(__file__).resolve().parents[1] / 'packages/web/src'
    assert not (root / 'stores/scheduleStore.ts').exists()
    names = ('useScheduleStore', 'fetchScheduledTasks', 'createScheduledTask',
             'updateScheduledTask', 'deleteScheduledTask', 'pauseScheduledTask',
             'resumeScheduledTask', 'runScheduledTaskNow', 'fetchTaskRuns',
             'fetchSchedulerNext', 'fetchSchedulerStatus')
    for path in root.rglob('*'):
        if path.suffix not in {'.ts', '.tsx'} or '.test.' in path.name:
            continue
        source = path.read_text(encoding='utf-8')
        assert not any(name in source for name in names), str(path.relative_to(root))


def test_audit_resolves_explicit_relative_and_dynamic_import_candidates(tmp_path, monkeypatch):
    from scripts import audit_scheduler_surface as audit
    sources = {
        'packages/scheduler/consumer.py': (
            'from . import store\nfrom .cron import next_n\n'
            'from packages.scheduler import engine\n'
            'from packages.scheduler.time_events import Sources\n'
            'import importlib\nimportlib.import_module("packages.scheduler.legacy_bridge")\n'),
        'packages/web/client.ts': 'const store = import("./stores/scheduleStore");\n',
        'broken.py': 'def broken(\n',
    }
    for name, source in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')

    def git_output(args, **kwargs):
        if args[:2] == ['git', 'ls-files']:
            return ('\0'.join(sources) + '\0').encode()
        assert args == ['git', 'rev-parse', 'HEAD']
        return '0' * 40

    monkeypatch.setattr(audit.subprocess, 'check_output', git_output)
    result = audit.scan(tmp_path)
    imports = {(row['path'], row['module']) for row in result['hostImports']}
    assert imports == {('packages/scheduler/consumer.py', 'packages.scheduler.' + name)
                       for name in ('store', 'cron', 'engine', 'time_events')}
    assert len(result['allDynamicImports']) == len(result['frontendDynamicImports']) == 1
    assert 'legacy_bridge' in result['allDynamicImports'][0]['code']
    assert result['pythonParseErrors'] == [{'path': 'broken.py', 'line': 1,
                                           'message': "'(' was never closed"}]
