"""Runtime registration must survive concurrent starts in different Sessions."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from packages.core import legal_offline as offline, session as sess, worker
from test_legal_offline import runtime_registry


def test_concurrent_session_starts_keep_distinct_registered_owners(monkeypatch):
    async def run():
        sessions = [sess.create('concurrent-a'), sess.create('concurrent-b')]
        entered = 0
        both_spawning = asyncio.Event()
        first_claimed = asyncio.Event()
        original_claim = worker._claim_offline_owner
        async def claim(w):
            await original_claim(w)
            if w.session_id == sessions[0].id:
                first_claimed.set()
        async def spawn(session_id, *args, **kwargs):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_spawning.set()
            await both_spawning.wait()
            if session_id == sessions[1].id:
                await asyncio.wait_for(first_claimed.wait(), 5)
            return SimpleNamespace(returncode=None, stdin=None)
        monkeypatch.setattr(worker, '_claim_offline_owner', claim)
        monkeypatch.setattr(worker, '_spawn_process', spawn)
        monkeypatch.setattr(worker, '_spawn_system_prompt_args', lambda *args: [])
        monkeypatch.setattr(worker, '_read_stdout', AsyncMock())
        monkeypatch.setattr(worker, '_watchdog', AsyncMock())
        monkeypatch.setattr(worker, '_consumer', AsyncMock())
        monkeypatch.setattr(worker, '_kill_process_tree', AsyncMock())
        results = await asyncio.gather(*(worker.create_worker(s.id) for s in sessions), return_exceptions=True)
        try:
            assert all(isinstance(w, worker.Worker) for w in results), results
            assert len({w.worker_id for w in results}) == 2
            for s, w in zip(sessions, results):
                assert worker.workers[w.worker_id] is w
                assert worker.find_worker_by_session(s.id) is w
                assert offline.same_owner(s.offline_owner, offline.owner(w))
                assert sess.get(s.id).offline_owner == s.offline_owner
        finally:
            for w in list(worker.workers.values()):
                await worker._stop_worker_tasks(w)
    asyncio.run(run())


@pytest.mark.parametrize('owner_status', ['alive', 'unknown'])
def test_untracked_runtime_kill_retains_owner_and_reports_failure(monkeypatch, owner_status):
    async def run():
        s = sess.create('untracked-owner')
        s.offline_owner = dict(token='original-owner', generation=10, workerId='worker-1',
                               runtimeExpected=True, processes=[dict(pid=30092, createdAt=123)])
        s.queue_pending = [dict(id='original-item', text='keep', locked=True)]
        sess.save(s)
        owner = copy.deepcopy(s.offline_owner)
        monkeypatch.setattr(offline, 'previous_owner_status', lambda _: owner_status)
        kill = AsyncMock()
        monkeypatch.setattr(worker, '_kill_process_tree', kill)
        result = await worker.kill_session_worker(s.id)
        assert isinstance(result, str) and owner_status in result
        assert s.offline_owner == owner
        assert s.queue_pending[0]['id'] == 'original-item'
        assert worker.find_worker_by_session(s.id) is None
        kill.assert_not_awaited()
        with pytest.raises(RuntimeError, match='second writer'):
            await worker._prepare_offline_recovery(s)
    asyncio.run(run())


def test_untracked_dead_owner_kill_remains_noop(monkeypatch):
    async def run():
        s = sess.create('dead-owner')
        s.offline_owner = dict(token='dead', generation=1, workerId='worker-1', processes=[])
        monkeypatch.setattr(offline, 'previous_owner_status', lambda _: 'dead')
        assert await worker.kill_session_worker(s.id) is None
    asyncio.run(run())


def test_concurrent_branch_and_start_keep_distinct_registered_owners(monkeypatch):
    from packages.core.adapters import CbcAdapter
    async def run():
        parent, child, other = [sess.create(name) for name in ('parent', 'child', 'other')]
        adapter = CbcAdapter()
        monkeypatch.setattr(adapter, 'fork_args', lambda s: ['--fork'])
        source = worker.Worker(worker_id='worker-1', session_id=parent.id, adapter=adapter)
        worker.workers[source.worker_id] = source
        worker._register_worker(source)
        branch_spawning, other_claimed = asyncio.Event(), asyncio.Event()
        original_claim = worker._claim_offline_owner
        async def claim(w):
            await original_claim(w)
            if w.session_id == other.id:
                other_claimed.set()
        async def spawn(session_id, *args, **kwargs):
            if session_id == child.id:
                branch_spawning.set()
                await asyncio.wait_for(other_claimed.wait(), 5)
            return SimpleNamespace(returncode=None, stdin=None)
        monkeypatch.setattr(worker, '_spawn_process', spawn)
        monkeypatch.setattr(worker, '_claim_offline_owner', claim)
        monkeypatch.setattr(worker, '_spawn_system_prompt_args', lambda *args: [])
        for name in ('_read_stdout', '_watchdog', '_consumer'):
            monkeypatch.setattr(worker, name, AsyncMock())
        fork = asyncio.create_task(worker.branch_worker(source.worker_id, child.id))
        await asyncio.wait_for(branch_spawning.wait(), 5)
        normal = await worker.create_worker(other.id)
        branched = await fork
        try:
            assert isinstance(normal, worker.Worker) and isinstance(branched, worker.Worker)
            assert normal.worker_id != branched.worker_id
            for w in (source, normal, branched):
                assert worker.workers[w.worker_id] is w
                assert worker.find_worker_by_session(w.session_id) is w
        finally:
            for w in (source, normal, branched):
                await worker._stop_worker_tasks(w)
    asyncio.run(run())


def test_concurrent_starts_over_random_port_with_real_owned_processes(monkeypatch, tmp_path):
    """Real HTTP + OS, with a harmless stand-in CLI and temporary Session data."""
    import json
    import socket
    import sys
    import httpx
    import uvicorn
    from fastapi import FastAPI
    from packages.web import server

    async def run():
        sessions = [sess.create('real-a'), sess.create('real-b')]
        entered = 0
        ready = asyncio.Event()
        processes = []
        async def spawn(*args, **kwargs):
            nonlocal entered
            entered += 1
            if entered == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 5)
            proc = await asyncio.create_subprocess_exec(
                sys.executable, '-u', '-c', 'import time; time.sleep(120)',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
            processes.append(proc)
            return proc
        monkeypatch.setattr(worker, '_spawn_process', spawn)
        monkeypatch.setattr(worker, '_spawn_system_prompt_args', lambda *args: [])
        monkeypatch.setattr(worker, '_watchdog', AsyncMock())
        app = FastAPI()
        app.post('/api/sessions/{session_id}/worker/restart')(server.api_restart_or_start)
        app.post('/api/sessions/{session_id}/worker/kill')(server.api_session_kill)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        assert port not in (8767, 8768)
        http = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        serving = asyncio.create_task(http.serve(sockets=[listener]))
        evidence = dict(port=port, profile=str(sess.SESSION_DIR), pids=[])
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=15) as client:
                results = await asyncio.gather(*(client.post(f'/api/sessions/{s.id}/worker/restart') for s in sessions))
                bodies = [r.json() for r in results]
                assert all('error' not in r for r in bodies), bodies
                assert len({r['workerId'] for r in bodies}) == 2
                evidence['starts'] = bodies
                for s in sessions:
                    w = worker.find_worker_by_session(s.id)
                    assert w is not None and w.process.returncode is None
                    assert offline.previous_owner_status(s.offline_owner) == 'alive'
                    evidence['pids'].append(w.process.pid)
                for s in sessions:
                    response = (await client.post(f'/api/sessions/{s.id}/worker/kill')).json()
                    assert response['status'] == 'offline', response
                    assert offline.previous_owner_status(s.offline_owner) == 'dead'
                    assert worker.find_worker_by_session(s.id) is None
                assert all(p.returncode is not None for p in processes)
                evidence['exitCodes'] = [p.returncode for p in processes]
        finally:
            for w in list(worker.workers.values()):
                await worker._stop_worker_tasks(w)
            for p in processes:
                if p.returncode is None:
                    p.kill()
                await p.wait()
            http.should_exit = True
            await asyncio.wait_for(serving, 5)
            listener.close()
        print('REAL_HTTP_OS_EVIDENCE=' + json.dumps(evidence))
    asyncio.run(run())
