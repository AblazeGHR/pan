"""R6-G canonical preview/status and removed engine boundary.

Legacy dispatch positives described an already retired host. Publication,
misfire, pause, recovery and idempotency remain tested by
 test_job_final_creators and test_scheduler_canonical_migration.
"""
import asyncio
import copy
import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from packages.jobs import cron
from packages.scheduler import api
from packages.scheduler.creators import CreatorRuntime


@pytest.mark.parametrize('module', ['engine', 'cron'])
def test_retired_modules_cannot_be_imported(module):
    with pytest.raises(ModuleNotFoundError) as exc:
        importlib.import_module('packages.scheduler.' + module)
    assert exc.value.name == 'packages.scheduler.' + module


class History:
    def __init__(self, tasks):
        self.items = tasks
    def tasks(self, include_disabled=True):
        return [t for t in self.items if include_disabled or t['enabled']]
    def get(self, identity):
        return next((t for t in self.items if t['id'] == identity), None)


AT = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


@pytest.mark.parametrize('schedule', [
    {'kind': 'once', 'at': (AT + timedelta(seconds=30)).isoformat()},
    {'kind': 'once', 'at': (AT - timedelta(seconds=30)).isoformat()},
    {'kind': 'interval', 'intervalSec': 60, 'anchor': AT.isoformat()},
    {'kind': 'interval', 'interval_sec': 60},
    {'kind': 'cron', 'cron': '0 9 * * *'},
    {'kind': 'cron', 'cron': '0 9 * * *', 'timezone': 'UTC'},
    {'kind': 'cron', 'cron': '*/15 9-17 * * 1-5', 'timezone': '+08:00'},
    {'kind': 'cron', 'cron': '0 0 29 2 *', 'timezone': 'UTC'},
])
def test_canonical_preview_preserves_calendar_count_order_and_no_writes(schedule):
    task = {'id': 'old', 'enabled': True, 'createdAt': AT.isoformat(), 'schedule': schedule}
    history = History([task])
    before = copy.deepcopy(task)
    result = api._preview_next(history, 'old', 3, at=AT)
    spec = dict(schedule)
    if spec['kind'] == 'interval':
        spec.setdefault('anchor', task['createdAt'])
    # Canonical provider also supports explicit fixed offsets; legacy calculator
    # uses IANA names, so use the equivalent stable zone for this comparison.
    if spec.get('timezone') == '+08:00':
        spec['timezone'] = 'Asia/Shanghai'
    expected = cron.next_n(spec, AT, 3)
    assert result == [{'taskId': 'old', 'fireAt': p.replace(microsecond=0).isoformat()} for p in expected]
    assert task == before


def test_preview_global_sort_limit_disabled_and_missing():
    schedule = {'kind': 'interval', 'intervalSec': 60, 'anchor': AT.isoformat()}
    history = History([{'id': name, 'enabled': name != 'disabled', 'schedule': schedule}
                       for name in ('b', 'a', 'disabled')])
    rows = api._preview_next(history, None, 3, at=AT)
    assert rows == [
        {'taskId': 'a', 'fireAt': (AT + timedelta(seconds=60)).isoformat()},
        {'taskId': 'b', 'fireAt': (AT + timedelta(seconds=60)).isoformat()},
        {'taskId': 'a', 'fireAt': (AT + timedelta(seconds=120)).isoformat()}]
    assert api._preview_next(history, 'missing', 3, at=AT) == []
    assert api._preview_next(history, 'disabled', 3, at=AT) == []


@pytest.mark.parametrize('schedule', [
    {'kind': 'interval', 'intervalSec': 0, 'anchor': AT.isoformat()},
    {'kind': 'cron', 'cron': 'bad', 'timezone': 'UTC'},
    {'kind': 'cron', 'cron': '* * * * *', 'timezone': 'Invalid/Zone'},
    {'kind': 'unknown'},
])
def test_preview_fails_explicitly_for_invalid_schedule(schedule):
    with pytest.raises(ValueError):
        api._preview_next(History([{'id': 'bad', 'enabled': True, 'schedule': schedule}]), None, 3, at=AT)


def test_status_without_binding_is_explicitly_unavailable():
    result = asyncio.run(api.engine_status())
    assert result['ok'] is False and result['error']['code'] == 'engine_not_running'


def test_status_reports_actual_canonical_task_and_scan_metrics():
    async def scenario():
        class Creators:
            def list(self):
                return [{'id': 'one'}, {'id': 'two'}]
        runtime = CreatorRuntime(Creators(), None)
        # A scan can encounter malformed Creators; health evidence must still
        # describe the scan, without resetting pending/cursor state.
        await runtime.tick(at='2026-01-01T00:00:00Z')
        await runtime.tick(at='2026-01-01T00:00:01Z')
        task = asyncio.create_task(asyncio.sleep(0))
        service = SimpleNamespace(task=task, closed=False, creator_runtime=runtime)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_final_service=service)))
        result = await api.engine_status(request)
        assert result == {'ok': True, 'status': {'running': True, 'tickSec': 0.25,
                                               'dueScanned': 4, 'lastTickAt': '2026-01-01T00:00:01Z'}}
        await task
        assert (await api.engine_status(request))['status']['running'] is False
        service.closed = True
        assert (await api.engine_status(request))['status']['running'] is False
    asyncio.run(scenario())


def test_server_uses_canonical_close_without_retired_stop_loop():
    import ast
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'packages/web/server.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    lifespan = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_owned_lifespan')
    closes = [node for node in ast.walk(lifespan) if isinstance(node, ast.Try)
              and any(isinstance(child, ast.Await) and ast.unparse(child.value) == 'final_jobs.close()'
                      for statement in node.finalbody for child in ast.walk(statement))]
    assert closes, 'Canonical FinalService.close must remain protected by finally'
    assert 'scheduler_engine' not in source and 'stop_loop' not in source
