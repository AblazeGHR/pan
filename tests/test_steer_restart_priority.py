"""Deterministic retirement intent versus preparation, wakeups and self-consume."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from packages.core import session as sess, worker, self_queue
from packages.core.adapters.cbc.adapter import CbcAdapter


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, 'SESSION_DIR', tmp_path / 'sessions')
    for mapping in (worker.workers, worker._queue_locks, worker._spawn_locks, worker._delayed_restarts, sess._cache):
        mapping.clear()
    worker.set_broadcaster(None)
    yield
    worker.workers.clear(); sess._cache.clear(); worker._delayed_restarts.clear()


def target(status='idle', capable=True):
    s = sess.Session(id='ses-priority', name='priority')
    item = {'type': 'task', 'source': 'user', 'queueItemId': 'q-original', 'id': 'q-original',
            'text': 'original message', 'deliveryState': 'queued', 'revision': 1}
    s.queue_pending = [item]; sess._cache[s.id] = s
    adapter = CbcAdapter(); adapter.supports_native_steer = capable
    w = worker.Worker(worker_id='old', session_id=s.id, adapter=adapter, status=status,
                      generation=4, process=SimpleNamespace(returncode=None), pending_signal=asyncio.Queue())
    worker.workers[w.worker_id] = w
    return s, item, w


@pytest.mark.parametrize('during_save', [False, True])
def test_restart_during_running_preparation_blocks_old_handoff(monkeypatch, during_save):
    async def run():
        s, item, w = target('running')
        w._queue_unit_active = True; w._current_queue_item = item
        if during_save:
            item['selfConsumeRequested'] = True
            original = sess._persist_async_outcome
            async def accepted_while_persisting(*args, **kwargs):
                result = await original(*args, **kwargs)
                worker.request_delayed_restart(s.id)
                return result
            monkeypatch.setattr(sess, '_persist_async_outcome', accepted_while_persisting)
        else:
            worker.request_delayed_restart(s.id)
        operation = AsyncMock()
        with pytest.raises(worker._QueuePausedBeforeHandoff):
            await worker._queue_handoff(w, s, operation, None)
        operation.assert_not_called()
        assert w.pending_restart and worker.delayed_restart_state(s.id)['status'] == 'pending'
        assert item['queueItemId'] == 'q-original' and item['deliveryState'] == 'queued'
    asyncio.run(run())


@pytest.mark.parametrize('capable', [False, True])
def test_self_consume_wakeup_keeps_accepted_delayed_intent(monkeypatch, capable):
    async def run():
        s, item, w = target('running', capable)
        # An already accepted intent reaches idle before self-consume begins.
        worker.request_delayed_restart(s.id); w.status = 'idle'
        intent = worker._delayed_restarts[s.id]
        respawn = AsyncMock()
        monkeypatch.setattr(worker, '_respawn_worker', respawn)
        immediate = AsyncMock(side_effect=AssertionError('must not cancel delayed intent'))
        monkeypatch.setattr(worker, 'restart_or_start_worker', immediate)
        result = await self_queue.consume(s.id, item['id'])
        await asyncio.sleep(0)
        assert result['status'] == 'pending' and s.queue_pending[0] is item
        assert worker._delayed_restarts[s.id] is intent and intent['status'] == 'restarting'
        assert w.status == 'restarting'
        immediate.assert_not_called(); respawn.assert_awaited_once_with(w, intent)
    asyncio.run(run())


def test_pause_does_not_block_retirement_wakeup(monkeypatch):
    async def run():
        s, _, w = target('running'); worker.request_delayed_restart(s.id)
        w.status = 'idle'; s.queue_paused = True
        respawn = AsyncMock(); monkeypatch.setattr(worker, '_respawn_worker', respawn)
        await worker._wake_worker(s.id); await asyncio.sleep(0)
        assert w.status == 'restarting'
        respawn.assert_awaited_once()
        assert w.pending_signal.empty()
    asyncio.run(run())


def test_handed_off_task_keeps_terminal_and_cleanup_before_retirement(monkeypatch):
    async def run():
        s, _, w = target('running'); w._queue_unit_active = True; w._current_handoff_acked = True
        respawn = AsyncMock(); monkeypatch.setattr(worker, '_respawn_worker', respawn)
        worker.request_delayed_restart(s.id)
        w.status = 'idle'; w._terminal_finishing = True
        await worker._wake_worker(s.id); respawn.assert_not_called()
        w._terminal_finishing = False
        worker._maybe_restart_pending(w); respawn.assert_not_called()
        w._queue_unit_active = False
        worker._maybe_restart_pending(w); await asyncio.sleep(0)
        respawn.assert_awaited_once()
        assert w.status == 'restarting'
    asyncio.run(run())


def test_absent_delayed_start_and_automatic_start_share_spawn_lock(monkeypatch):
    async def run():
        s, _, w = target(); worker.workers.clear()
        lock = await worker._session_spawn_lock(s.id); await lock.acquire()
        count = 0
        async def create(sid):
            nonlocal count
            assert lock.locked()
            if worker.workers: return w
            count += 1; worker.workers[w.worker_id] = w
            return w
        monkeypatch.setattr(worker, '_create_worker', create)
        intent = worker.request_delayed_restart(s.id)
        automatic = asyncio.create_task(worker.create_worker(s.id))
        await asyncio.sleep(0); assert count == 0
        lock.release()
        assert await automatic is w
        for _ in range(3): await asyncio.sleep(0)
        assert count == 1
        assert worker.delayed_restart_state(s.id)['status'] == 'completed'
        assert intent['action'] == 'start'
    asyncio.run(run())
