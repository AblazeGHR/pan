"""Force send's MCP entry points and shared capability routing."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from packages.core import session as sess, worker
from packages.mcp import server as mcp
from tests.test_self_queue import FutureSteerAdapter


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, 'SESSION_DIR', tmp_path / 'sessions')
    sess._cache.clear(); worker.workers.clear(); worker._spawn_locks.clear(); worker._queue_locks.clear()
    worker.set_broadcaster(None)
    yield
    sess._cache.clear(); worker.workers.clear()


def live(status='running', capable=True):
    s = sess.Session(id='force-target', name='force-target')
    s.active_task_id = 'original-task'; sess._cache[s.id] = s
    adapter = FutureSteerAdapter(); adapter.supports_native_steer = capable
    stdin = Mock(drain=AsyncMock())
    w = worker.Worker(worker_id='force-worker', session_id=s.id, adapter=adapter, status=status,
                      process=SimpleNamespace(returncode=None, stdin=stdin), pending_signal=asyncio.Queue())
    worker.workers[w.worker_id] = w
    return s, w, stdin


@pytest.mark.parametrize('outcome', ['accepted', 'rejected', 'unknown'])
def test_capable_force_immediately_attempts_steer_without_restart(monkeypatch, outcome):
    async def run():
        s, w, stdin = live()
        restart = AsyncMock(side_effect=AssertionError('no fallback after native attempt'))
        monkeypatch.setattr(worker, 'restart_or_start_worker', restart)
        def write(data):
            control = json.loads(data)
            worker._route_steer_receipt(w, {'type': 'pan.steer_receipt', 'requestId': control['requestId'],
                'ok': outcome == 'accepted', 'outcome': outcome,
                'error': None if outcome == 'accepted' else 'fixture'})
        stdin.write.side_effect = write
        result = await worker.send_session(s.id, 'urgent correction', force=True, client_message_id='force-one')
        assert result['status'] == outcome and result['consumption'] == 'unconfirmed'
        conflict = await worker.send_session(s.id, 'different correction', force=True, client_message_id='force-one')
        assert conflict['status'] == 'rejected' and conflict['outcome'] == 'rejected'
        assert conflict['queueItemId'] == result['queueItemId']
        assert stdin.write.call_count == 1
        restart.assert_not_called()
        if outcome == 'accepted':
            assert not s.queue_pending
            assert s.history[-1]['taskId'] == 'original-task'
        else:
            assert s.queue_pending[0]['forceSendHeld']
            assert not worker.queue_item_eligible(s, s.queue_pending[0])
        if outcome in {'accepted', 'unknown'}:
            duplicate = await worker.send_session(s.id, 'urgent correction', force=True, client_message_id='force-one')
            assert duplicate['duplicate'] and stdin.write.call_count == 1
        elif outcome == 'rejected':
            retried = await worker.send_session(s.id, 'urgent correction', force=True, client_message_id='force-one')
            assert retried['status'] == 'rejected' and retried['queueItemId'] == result['queueItemId']
            assert stdin.write.call_count == 2 and len(s.queue_pending) == 1
    asyncio.run(run())


@pytest.mark.parametrize('status,capable,pending', [('idle', True, False), ('queued', True, False), ('idle', False, True), ('running', False, True)])
def test_idle_capability_or_delayed_intent_uses_queue_without_restart(monkeypatch, status, capable, pending):
    async def run():
        s, w, stdin = live(status, capable); w.pending_restart = pending
        restart = AsyncMock(side_effect=AssertionError('must preserve idle/delayed handoff'))
        monkeypatch.setattr(worker, 'restart_or_start_worker', restart)
        monkeypatch.setattr(worker, '_wake_worker', AsyncMock())
        result = await worker.send_session(s.id, 'urgent', force=True)
        assert result['status'] == 'queued' and len(s.queue_pending) == 1
        restart.assert_not_called(); stdin.write.assert_not_called()
        assert w.pending_restart == pending
    asyncio.run(run())


def test_idle_noncapable_force_uses_normal_send_not_restart(monkeypatch):
    """Changed behavior: an idle non-Steer worker is never restarted by Force."""
    async def run():
        s, w, stdin = live('idle', capable=False)
        restart = AsyncMock(side_effect=AssertionError('idle must use normal send'))
        monkeypatch.setattr(worker, 'restart_or_start_worker', restart)
        result = await worker.send_session(s.id, 'urgent', force=True)
        await worker.wait_auto_force(s.id)
        assert result['status'] == 'queued'
        assert len(s.queue_pending) == 1
        assert s.queue_pending[0]['text'] == 'urgent'
        assert w.pending_signal.qsize() == 1
        restart.assert_not_called()
        stdin.write.assert_not_called()
    asyncio.run(run())


def test_force_illegal_session_queues_without_steer_or_restart(monkeypatch):
    """Illegal owns admission until Wake/recovery; Force must not clear it."""
    async def run():
        s, w, stdin = live()
        identity = {"token": "failed", "generation": 1, "serviceId": "previous", "workerId": "old"}
        record = worker._offline.admit(s, identity, kind="foreground")
        s.offline_run = record
        worker._offline.detect(s, "test-crash", eligible=lambda row: True,
                               remember=lambda row: worker._remember_queue_item(s, row))
        restart = AsyncMock(side_effect=AssertionError('illegal blocks restart'))
        monkeypatch.setattr(worker, 'restart_or_start_worker', restart)
        result = await worker.send_session(s.id, 'urgent', force=True)
        assert result['status'] == 'queued'
        assert len(s.queue_pending) == 1
        stdin.write.assert_not_called()
        restart.assert_not_called()
    asyncio.run(run())


def test_noncapable_force_retains_restart_then_send(monkeypatch):
    async def run():
        s, w, stdin = live(capable=False)
        restart = AsyncMock(return_value=w); monkeypatch.setattr(worker, 'restart_or_start_worker', restart)
        monkeypatch.setattr(worker, '_wake_worker', AsyncMock())
        result = await worker.send_session(s.id, 'urgent', force=True)
        assert result['status'] == 'queued'
        restart.assert_awaited_once_with(s.id, allow_illegal_recovery=False); stdin.write.assert_not_called()
        assert s.queue_pending[0]['text'] == 'urgent'
    asyncio.run(run())


def test_noncapable_force_preserves_active_edit_lease(monkeypatch):
    async def run():
        s, w, stdin = live(capable=False)
        worker.acquire_queue_edit_lock(s, 'editing-original', 'editor-token')
        restart = AsyncMock(side_effect=AssertionError('edit lease blocks force restart'))
        monkeypatch.setattr(worker, 'restart_or_start_worker', restart)
        monkeypatch.setattr(worker, '_wake_worker', AsyncMock())
        result = await worker.send_session(s.id, 'urgent', force=True)
        assert result['status'] == 'queued'
        assert worker.queue_item_edit_locked(s, 'editing-original')
        restart.assert_not_called(); stdin.write.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize('alias', ['agent', 'worker_session', 'worker_id'])
def test_mcp_force_entries_delegate_capability_choice_to_backend(monkeypatch, alias):
    monkeypatch.setattr(mcp, '_check_access', lambda *args, **kwargs: None)
    monkeypatch.setattr(mcp, '_caller_identity', lambda: {'id': 'sender'})
    monkeypatch.setattr(mcp, '_worker_session_id', lambda _: 'target')
    calls = []
    def api(method, route, body):
        calls.append((method, route, body)); return {'status': 'accepted', 'outcome': 'accepted'}
    monkeypatch.setattr(mcp, '_api', api)
    if alias == 'agent': result = mcp.agent_send('target', 'urgent', 'force-stable')
    elif alias == 'worker_session': result = mcp.worker_send_force(session_id='target', text='urgent', client_message_id='force-stable')
    else: result = mcp.worker_send_force(worker_id='old-worker', text='urgent', client_message_id='force-stable')
    assert result['outcome'] == 'accepted' and len(calls) == 1
    method, route, body = calls[0]
    assert method == 'POST' and route == '/api/send'
    assert body['force'] is True and body['sessionId'] == 'target'
    assert body['sourceSessionId'] == 'sender' and body['clientMessageId'] == 'force-stable'


@pytest.mark.parametrize('tool_name', ['agent_send', 'agent_send_to_queue'])
@pytest.mark.parametrize('gate', ['open', 'pause', 'illegal_lock'])
def test_public_send_routes_keep_task_and_admission_gates(monkeypatch, tool_name, gate):
    """Public tool -> HTTP handler -> queue/native delivery, with no live CLI."""
    from packages.web import server
    s, w, stdin = live()
    s.queue_paused = gate == 'pause'
    sess._cache['sender'] = sess.Session(id='sender', name='sender')
    if gate == 'illegal_lock':
        identity = worker._offline.owner(w)
        worker._offline.admit(s, identity, kind='foreground')
        worker._offline.detect(s, 'test-crash', identity=identity,
                               eligible=lambda _: True, remember=lambda _: None)
    monkeypatch.setattr(mcp, '_check_access', lambda *args, **kwargs: None)
    monkeypatch.setattr(mcp, '_caller_identity', lambda: {'id': 'sender'})
    monkeypatch.setattr(worker, 'restart_or_start_worker',
                        AsyncMock(side_effect=AssertionError('no restart')))
    monkeypatch.setattr(worker, '_wake_worker', AsyncMock())
    def write(data):
        control = json.loads(data)
        worker._route_steer_receipt(w, {
            'type': 'pan.steer_receipt', 'requestId': control['requestId'],
            'ok': True, 'outcome': 'accepted'})
    stdin.write.side_effect = write
    def api(method, route, body):
        assert (method, route) == ('POST', '/api/send')
        assert body.get('force', False) is (tool_name == 'agent_send')
        return asyncio.run(server.api_send(body))
    monkeypatch.setattr(mcp, '_api', api)
    result = getattr(mcp, tool_name)(s.id, 'task context')
    immediate = tool_name == 'agent_send' and gate == 'open'
    assert result.get('status') == ('accepted' if immediate else 'queued'), result
    assert stdin.write.call_count == int(immediate)
    item = s.history[-1] if immediate else s.queue_pending[0]
    assert item['taskId'] == s.active_task_id == 'original-task'
    assert item['taskIdSource'] == 'active'
    assert item['sourceSessionId'] == 'sender'
    assert item['source'] == 'agent'
    if not immediate:
        assert len(s.queue_pending) == 1
        if gate != 'open':
            assert not worker.queue_item_eligible(s, item)


def test_force_body_conflict_is_rechecked_inside_queue_lock(monkeypatch):
    async def run():
        s, w, stdin = live()
        async with worker.queue_lock(s.id):
            original, error = await worker._persist_task_item(s, 'original text', 'user', None,
                None, 'racing-key', None, idempotent_task_id=False)
        assert error is None
        lookup = worker._find_queue_item_by_idempotency
        first = True
        def racing_lookup(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                return None  # Another request reserves the ID before lock acquisition.
            return lookup(*args, **kwargs)
        monkeypatch.setattr(worker, '_find_queue_item_by_idempotency', racing_lookup)
        response = await worker.send_session(s.id, 'different text', source='user',
            force=True, client_message_id='racing-key')
        assert response['status'] == 'rejected' and response['queueItemId'] == original['queueItemId']
        assert len(s.queue_pending) == 1 and s.queue_pending[0]['text'] == 'original text'
        stdin.write.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize('route', ['steer', 'restart'])
def test_force_cannot_clear_illegal_discovered_after_routing(monkeypatch, route):
    from packages.core import legal_offline as offline, queue_locks
    async def run():
        s, w, stdin = live(capable=route == 'steer')
        identity = offline.owner(w)
        offline.admit(s, identity, kind='foreground')
        def changed(*_):
            offline.detect(s, 'concurrent-exit', identity=identity,
                eligible=lambda _: True, remember=lambda _: None)
            return route
        monkeypatch.setattr(worker, 'steer_delivery_route', changed)
        # For restart, use the real spawn-lock check without creating a CLI.
        monkeypatch.setattr(worker, '_prepare_offline_recovery', AsyncMock())
        result = await worker.send_session(s.id, 'ordinary force', force=True)
        assert result['status'] == 'queued'
        assert offline.is_illegal(s)
        assert queue_locks.source(s.queue_pending[0]) == 'illegal'
        stdin.write.assert_not_called()
    asyncio.run(run())
