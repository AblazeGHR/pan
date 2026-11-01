"""Phase 4b single-owner legacy Scheduler migration contracts."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from tests.job_retirement_native_fixture import h as native_h  # noqa: F401
from tests.test_retirement_rev16_behavior import h as behavior_h  # noqa: F401

from packages.actions.store import Store
from packages.automation.service import Service
from packages.jobs.action_runtime import Runtime
from packages.jobs import system_actions
from packages.scheduler import store
from packages.core.platform import data_paths
from packages.jobs import cron
from packages.scheduler.legacy_bridge import LegacySchedulerBridge
from packages.scheduler.time_events import Sources, Runtime as TimeRuntime, EVENT_TYPE
from tests.test_automation_core import async_test, until


def payload(tmp_path, task_id='sch_phase4b'):
    return {'id': task_id, 'name': 'Legacy timer', 'description': 'compat',
            'target_session_id': 'ses_target', 'text': 'hello', 'enabled': True,
            'paused': False, 'schedule': {'kind': 'interval', 'intervalSec': 60,
            'anchor': datetime.now().replace(microsecond=0).isoformat()},
            'max_runs': None, 'misfire_policy': 'skip'}


async def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DEFAULT_ROOT', tmp_path / 'jobs')
    monkeypatch.setattr(data_paths, 'DEFAULT_ROOT', tmp_path / 'jobs')
    monkeypatch.setattr(store, 'LEGACY_TASKS_ROOT', tmp_path / 'legacy')
    worker = SimpleNamespace(workers={})
    svc = Service(tmp_path / 'automation', worker, None)
    svc.engine.actions = Store(tmp_path / 'actions')
    svc.engine.actions.put('actions', {'id': 'pause', 'name': 'pause', 'enabled': True,
        'effect': 'write_local', 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'execution': {'kind': 'session_operation', 'operation': 'pause_everything',
                       'selector': {'mode': 'explicit', 'ids': ['ses_target']}}})
    svc.events.initialize(); svc.started = True
    jobs = Runtime(tmp_path / 'jobs', svc.engine.actions, None); await jobs.start()
    svc.job_runtime = jobs; svc.engine.job_runtime = jobs
    svc.time_sources = Sources(tmp_path / 'jobs')
    svc.scheduler_compat = LegacySchedulerBridge(svc)
    return svc, jobs


@async_test
async def test_switch_preserves_task_and_job_ids_and_creates_one_owner(tmp_path, monkeypatch):
    svc, jobs = await setup(tmp_path, monkeypatch)
    try:
        task = store.create_task(payload(tmp_path))
        before = store._job_for_task(task['id'])
        switched = await svc.scheduler_compat.switch(task['id'])
        assert switched['jobId'] == before['jobId'] and switched['taskId'] == task['id']
        assert switched['legacySchedulerCompat'] and switched['schedulerOwner'] == 'event'
        source = svc.time_sources.get(switched['legacySourceId'])
        trigger = svc.ledger.read()['triggers'][switched['legacyTriggerId']]
        assert source['owner'] == 'scheduler-event-v1'
        assert trigger['targetJobId'] == switched['jobId']
        assert trigger['eventFilter']['payload.scheduleId'] == source['id']
        assert len(svc.time_sources.references(source['id'])) == 1
        again = await svc.scheduler_compat.switch(task['id'])
        assert again['jobId'] == switched['jobId']
        assert len(svc.time_sources.list()) == 1
        assert len(svc.ledger.read()['triggers']) == 1
    finally:
        await jobs.close(); await svc.bus.close()


@async_test
async def test_unmappable_unknown_action_stays_legacy(tmp_path, monkeypatch):
    svc, jobs = await setup(tmp_path, monkeypatch)
    try:
        task = store.create_task(payload(tmp_path, 'sch_unmappable'))
        job = store._job_for_task(task['id'])
        background = __import__('packages.core.background_jobs', fromlist=['_update'])
        background._update(job['jobId'], {'action': {'api': 'future_action', 'x': 1}}, registry_root=store.data_root())
        with pytest.raises(ValueError): await svc.scheduler_compat.switch(task['id'])
        current = store._job_for_task(task['id'])
        assert not current.get('legacySchedulerCompat')
        assert not svc.time_sources.list()
    finally:
        await jobs.close(); await svc.bus.close()


def test_run_now_does_not_move_periodic_next_fire(behavior_h):
    # Existing manual public Run and independent Creator cursor: no old loader
    # or implicit migration is required to preserve this user capability.
    from tests.test_jobs_api import test_runs_and_next_and_run_now
    test_runs_and_next_and_run_now(behavior_h)
