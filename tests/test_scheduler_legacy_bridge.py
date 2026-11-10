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


@pytest.mark.parametrize('shape', ['known', 'unknown'])
def test_legacy_switch_is_read_only_and_cannot_create_an_owner(tmp_path, monkeypatch, shape):
    import json
    from packages.jobs.runtime_owner import OwnerFence
    profile = tmp_path / 'owned-profile'
    root = profile / 'background_jobs'
    root.mkdir(parents=True)
    original = payload(tmp_path)
    if shape == 'unknown':
        original['action'] = {'api': 'future_action', 'x': 1}
    path = root / 'preserved.json'
    path.write_text(json.dumps(original), encoding='utf8')
    before = path.read_bytes()
    monkeypatch.setattr(data_paths, 'DEFAULT_ROOT', root)
    bridge = LegacySchedulerBridge(SimpleNamespace())
    def forbidden(*args, **kwargs):
        pytest.fail('Retired switch read or wrote an execution/owner path')
    monkeypatch.setattr(bridge, '_job', forbidden)
    monkeypatch.setattr(OwnerFence, '__init__', forbidden)
    with pytest.raises(PermissionError, match='bridge retired'):
        asyncio.run(bridge.switch(original['id']))
    with pytest.raises(PermissionError, match='bridge retired'):
        bridge._switch(original['id'])
    assert path.read_bytes() == before
    assert sorted(p.relative_to(profile).as_posix() for p in profile.rglob('*') if p.is_file()) == ['background_jobs/preserved.json']


def test_run_now_does_not_move_periodic_next_fire(behavior_h):
    # Existing manual public Run and independent Creator cursor: no old loader
    # or implicit migration is required to preserve this user capability.
    from tests.test_jobs_api import test_runs_and_next_and_run_now
    test_runs_and_next_and_run_now(behavior_h)
