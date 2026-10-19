"""Session control entry points must retain and retire external writer ownership."""
import asyncio
import copy
import json
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from packages.core import session as sess, worker, legal_offline as offline
from packages.core.adapters.codex import CodexAdapter


def setup():
    s = sess.create('takeover-lifecycle', adapter='codex')
    w = worker.Worker(worker_id=s.id + '-worker', session_id=s.id,
                      adapter=CodexAdapter(), status='held')
    # Real takeover cancels the consumer. It remains a control target even
    # though it is no longer an available queue consumer.
    async def done():
        pass
    w._consume_task = asyncio.create_task(done())
    worker.workers[w.worker_id] = w
    worker._register_worker(w)
    return s, w


class Job:
    def __init__(self):
        self.closed = False
        self.proof = {'kind': 'windows-job-empty', 'jobId': 'owned', 'activeProcesses': 0}

    def terminate_and_verify(self):
        return self.proof

    def close_retired(self):
        self.closed = True


@pytest.mark.parametrize('action', ['kill', 'restart'])
def test_session_control_retires_held_owner_before_success(monkeypatch, action):
    async def run():
        s, w = setup()
        await w._consume_task
        await worker.reserve_takeover_owner(w)
        captured = copy.deepcopy(s.offline_owner)
        job = w.takeover_job = Job()
        monkeypatch.setattr(worker, '_restart_tasks', AsyncMock())
        spawn = AsyncMock(return_value=SimpleNamespace(returncode=None))
        monkeypatch.setattr(worker, '_spawn_process', spawn)
        fn = worker.kill_session_worker if action == 'kill' else worker.restart_or_start_worker
        result = await fn(s.id)
        assert result is w
        assert job.closed and w.takeover_job is None
        assert not s.offline_owner.get('externalTakeover')
        assert s.owner_retirements[captured['ownerId']]['proof']['activeProcesses'] == 0
        if action == 'restart':
            spawn.assert_awaited_once()
        else:
            spawn.assert_not_awaited()
            assert worker.get_worker(w.worker_id) is None
    asyncio.run(run())


def test_unknown_takeover_kill_reports_error_and_durable_illegal():
    async def run():
        s, w = setup()
        await w._consume_task
        await worker.reserve_takeover_owner(w)
        result = await worker.kill_session_worker(s.id)
        assert isinstance(result, str) and 'unknown' in result.lower()
        assert w.status == 'error'
        assert offline.is_illegal(s)
        persisted = sess.Session._from_data(json.loads(sess._path(s.id).read_text()))
        assert offline.is_illegal(persisted)
        assert persisted.offline_owner['ownershipUnknown']
    asyncio.run(run())


@pytest.mark.parametrize('route_name', ['api_takeover', 'api_session_takeover',
                                      'api_takeover_command', 'api_session_takeover_command'])
def test_unestablished_cli_id_cannot_bypass_takeover(monkeypatch, route_name):
    from packages.web import server
    async def run():
        s, w = setup()
        s.cli_session_id = 'fabricated-no-native-session'
        w.status = 'idle'
        hold = AsyncMock(return_value=w)
        opened = AsyncMock()
        monkeypatch.setattr(worker, 'find_alive_worker_by_session', lambda _: w)
        monkeypatch.setattr(worker, 'takeover_session_worker', hold)
        monkeypatch.setattr(worker, 'takeover_worker', AsyncMock())
        monkeypatch.setattr(server, '_open_takeover_terminal', opened)
        route = getattr(server, route_name)
        result = await route(s.id if 'session_' in route_name else w.worker_id)
        assert 'error' in result and 'resume' in result['error'].lower()
        hold.assert_not_awaited()
        opened.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['stop', 'save', 'close'])
def test_failed_retirement_retries_without_releasing_owner_or_queue(monkeypatch, failure):
    async def run():
        s, w = setup()
        await w._consume_task
        s.queue_paused = True
        s.queue_pending = [dict(id='B', queueItemId='B', type='task', text='B', deliveryState='queued'),
                           dict(id='C', queueItemId='C', type='task', text='C', deliveryState='queued',
                                queueLockSource='manual'),
                           dict(id='U', queueItemId='U', type='task', text='U', deliveryState='unknown',
                                steerPending=True, queueEditLease={'token': 'lease'})]
        await worker.reserve_takeover_owner(w)
        job = w.takeover_job = Job()
        captured = copy.deepcopy(s.offline_owner)
        original = sess.os.replace
        if failure == 'save':
            monkeypatch.setattr(sess.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('disk unavailable')))
        if failure == 'stop':
            job.terminate_and_verify = lambda: (_ for _ in ()).throw(TimeoutError('Job still active'))
        if failure == 'close':
            job.close_retired = lambda: (_ for _ in ()).throw(OSError('close failed'))
        error = await worker.kill_session_worker(s.id)
        assert isinstance(error, str) and 'failed' in error
        assert w.status == 'error' and w.takeover_job is job and not job.closed
        assert worker.get_worker(w.worker_id) is w
        if failure in {'save', 'stop'}:
            assert s.offline_owner == captured
        if failure == 'stop':
            assert offline.is_illegal(s)
        if failure == 'save':
            assert 'persistence failed' in error
        monkeypatch.setattr(sess.os, 'replace', original)
        job.terminate_and_verify = lambda: job.proof
        job.close_retired = lambda: setattr(job, 'closed', True)
        assert await worker.kill_session_worker(s.id) is w
        assert job.closed and w.takeover_job is None
        assert s.queue_paused
        assert s.queue_pending[1]['queueLockSource'] == 'manual'
        assert s.queue_pending[2]['deliveryState'] == 'unknown'
        assert s.queue_pending[2]['queueEditLease'] == {'token': 'lease'}
        before = sess._path(s.id).read_bytes()
        await worker._detect_offline_exit(w, captured, 'late-EOF')
        assert sess._path(s.id).read_bytes() == before
    asyncio.run(run())


@pytest.mark.parametrize('adapter', ['cbc', 'claude', 'codex', 'opencode', 'kimi'])
def test_native_storage_admission_does_not_require_pan_assistant(monkeypatch, tmp_path, adapter):
    from packages.core import takeover
    class Provider:
        available = False
        def session_exists(self, *args):
            return self.available
        def parse_history(self, *args):
            return [{'role': 'user', 'content': 'native imported turn'}] if self.available else []
        def _find_session_dir(self, sid, cwd, home):
            assert home == 'owned-kimi-home'
            return tmp_path if self.available else None
        def _read_state(self, directory):
            return {'session_id': 'minted-id'}
    provider = Provider()
    monkeypatch.setattr(takeover, 'get_sessions_provider', lambda _: provider)
    s = sess.create('admission', adapter=adapter)
    s.adapter_config['kimi_home_dir'] = 'owned-kimi-home'
    assert not takeover.readiness(s)['ready']
    s.cli_session_id = 'minted-id'
    assert not takeover.readiness(s)['ready']
    provider.available = True
    assert not s.history
    assert takeover.readiness(s)['ready']


def test_codex_native_rollout_required_even_with_thread_row(monkeypatch, tmp_path):
    from packages.core.adapters.codex import sessions
    from packages.core import takeover
    db = tmp_path / 'state.sqlite'
    rollout = tmp_path / 'rollout.jsonl'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE threads (id TEXT, rollout_path TEXT)')
        con.execute('INSERT INTO threads VALUES (?,?)', ('native', str(rollout)))
    monkeypatch.setattr(sessions, '_STATE_DB', db)
    s = sess.create('native-admission', adapter='codex')
    s.cli_session_id = 'native'
    assert not takeover.readiness(s)['ready']
    meta = {'type': 'session_meta', 'payload': {'id': 'native'}}
    rollout.write_text(json.dumps(meta) + '\n')
    assert not takeover.readiness(s)['ready']
    user = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                                               'content': [{'type': 'input_text', 'text': 'native user'}]}}
    rollout.write_text(json.dumps(meta) + '\n' + json.dumps(user) + '\n')
    assert takeover.readiness(s)['ready'] and not s.history
    meta['payload']['id'] = 'other-thread'
    rollout.write_text(json.dumps(meta) + '\n' + json.dumps(user) + '\n')
    assert not takeover.readiness(s)['ready']


def test_untracked_external_owner_kill_is_not_offline_success():
    async def run():
        s, w = setup()
        await w._consume_task
        await worker.reserve_takeover_owner(w)
        worker.workers.pop(w.worker_id)
        worker._unregister_worker(w)
        error = await worker.kill_session_worker(s.id)
        assert 'unknown' in error
        assert offline.is_illegal(s) and s.offline_owner['externalTakeover']
        assert worker.find_worker_by_session(s.id) is None
        error = await worker.restart_or_start_worker(s.id)
        assert 'second writer' in error
    asyncio.run(run())


@pytest.mark.parametrize('first_action', ['kill', 'restart'])
def test_mixed_kill_restart_serializes_retirement(monkeypatch, first_action):
    async def run():
        s, w = setup()
        await w._consume_task
        await worker.reserve_takeover_owner(w)
        w.takeover_job = Job()
        entered, release = asyncio.Event(), asyncio.Event()
        original = worker._stop_takeover_for_control
        async def stop(runtime):
            entered.set()
            await release.wait()
            return await original(runtime)
        monkeypatch.setattr(worker, '_stop_takeover_for_control', stop)
        monkeypatch.setattr(worker, '_restart_tasks', AsyncMock())
        monkeypatch.setattr(worker, '_kill_process_tree', AsyncMock())
        spawn = AsyncMock(return_value=SimpleNamespace(returncode=0))
        monkeypatch.setattr(worker, '_spawn_process', spawn)
        async def create(_):
            assert s.owner_retirements and not s.offline_owner.get('externalTakeover')
            worker.workers[w.worker_id] = w
            worker._register_worker(w)
            w.process = await spawn(s.id, adapter=w.adapter)
            return w
        monkeypatch.setattr(worker, '_create_worker', create)
        actions = {'kill': worker.kill_session_worker, 'restart': worker.restart_or_start_worker}
        first = asyncio.create_task(actions[first_action](s.id))
        await entered.wait()
        second = asyncio.create_task(actions['restart' if first_action == 'kill' else 'kill'](s.id))
        await asyncio.sleep(0)
        spawn.assert_not_awaited()
        release.set()
        assert await first is w
        assert await second is w
        spawn.assert_awaited_once()
        assert not s.offline_owner.get('externalTakeover')
    asyncio.run(run())


def test_kill_deadline_marks_illegal_and_reuses_single_stalled_verifier(monkeypatch):
    async def run():
        s, w = setup()
        await w._consume_task
        await worker.reserve_takeover_owner(w)
        job = w.takeover_job = Job()
        entered, release = threading.Event(), threading.Event()
        calls = []
        def slow_stop():
            calls.append('terminate')
            entered.set()
            assert release.wait(2)
            return job.proof
        job.terminate_and_verify = slow_stop
        monkeypatch.setattr(worker, '_TAKEOVER_STOP_TIMEOUT', .01)
        try:
            error = await worker.kill_session_worker(s.id)
            assert entered.is_set() and isinstance(error, str)
            assert offline.is_illegal(s) and w.status == 'error'
            pending = w._takeover_stop_task
            assert pending is not None and not pending.done() and not job.closed
            error = await worker.restart_or_start_worker(s.id)
            assert isinstance(error, str) and len(calls) == 1
            assert w._takeover_stop_task is pending
            release.set()
            await pending
            assert await worker.kill_session_worker(s.id) is w
            assert job.closed and len(calls) == 1
        finally:
            release.set()
    asyncio.run(run())
