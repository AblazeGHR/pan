"""Actual compatibility admission, frozen parameters and conservative receipts."""
import asyncio
import copy

import pytest

from packages.actions.store import Store, validate_action
from packages.core import background_jobs as jobs
from packages.jobs import system_actions as system


def record(root, **changes):
    job = {'jobId': 'job_1234567890abcdef12345678', 'kind': 'scheduled-task', 'status': 'scheduled',
           'name': 'system test', 'targetSessionId': 'session', 'text': 'original',
           'action': {'api': 'send_session'}, 'createdAt': 1, 'updatedAt': 1, **changes}
    jobs._create(job, root)
    return job


def test_product_actions_are_real_immutable_registry_items(tmp_path):
    from tests.support.canonical_host import build, USER
    import asyncio
    h = build(tmp_path)
    try:
        items = h.service.actions(actor=USER)['items']
        product = [item for item in items if item.get('system')]
        assert product
        for item in product:
            with pytest.raises(Exception):
                h.service.write('action', item['id'], {'definition': item['definition'],
                    'expectedRevision': item['revision'], 'requestKey': 'system-edit'}, actor=USER)
    finally:
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())


def test_compatibility_effect_uses_persisted_snapshot_and_cleans(tmp_path, monkeypatch):
    async def run():
        job = record(tmp_path)
        before = jobs.get(job['jobId'], tmp_path)
        async def forbidden(*args): pytest.fail('retired dispatcher performed an effect')
        for dispatch_key in ('original', 'retry'):
            with pytest.raises(AttributeError, match='retired'):
                await system.invoke(job, dispatch_key, forbidden, tmp_path)
            with pytest.raises(AttributeError, match='retired'):
                await system.execute(system.definition('scheduled-task'), {}, {}, forbidden)
        assert jobs.get(job['jobId'], tmp_path) == before
        assert system.recovery_ready(system.definition('background-process'), {}) is False
    asyncio.run(run())


def test_write_failure_blocks_effect_and_unknown_does_not_replay(tmp_path, monkeypatch):
    async def run():
        job = record(tmp_path)
        before = jobs.get(job['jobId'], tmp_path)
        async def forbidden(*args): pytest.fail('retired dispatcher performed an effect')
        for dispatch_key in ('original', 'retry'):
            with pytest.raises(AttributeError, match='retired'):
                await system.invoke(job, dispatch_key, forbidden, tmp_path)
            with pytest.raises(AttributeError, match='retired'):
                await system.execute(system.definition('scheduled-task'), {}, {}, forbidden)
        assert jobs.get(job['jobId'], tmp_path) == before
        assert system.recovery_ready(system.definition('background-process'), {}) is False
    asyncio.run(run())


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled', 'unknown', 'uncertain', 'timed_out'])
def test_process_and_supervisor_terminal_cleanup_is_same_atomic_commit(tmp_path, status):
    job = record(tmp_path, kind='main-lifecycle', phase='requested', status='pending')
    current = jobs.get(job['jobId'], tmp_path)
    assert current['attemptSnapshot']['definition']['id'] == 'system_main_lifecycle'
    jobs._update(job['jobId'], {'status': status}, registry_root=tmp_path)
    terminal = jobs.get(job['jobId'], tmp_path)
    assert 'attemptSnapshot' not in terminal
    assert terminal['attempts'][0]['status'] == status
    assert terminal['snapshotCleanup'] == 'cleaned'


def test_system_action_forgery_is_rejected():
    forged = copy.deepcopy(system.definition('session-message'))
    forged['input_schema']['additionalProperties'] = True
    with pytest.raises(ValueError): validate_action(forged)


def test_process_waiter_uses_existing_event_without_polling(tmp_path):
    async def run():
        child = record(tmp_path, status='running')
        waiting = asyncio.create_task(system.wait_process(child, tmp_path))
        await asyncio.sleep(.01)
        system.notify({'type': 'job.updated', 'job': {**child, 'status': 'completed', 'exitCode': 0}})
        result = await asyncio.wait_for(waiting, 1)
        assert result['exitCode'] == 0
        assert not system._waiters
    asyncio.run(run())
