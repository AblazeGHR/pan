"""CBC's native admission protocol, run ownership and at-most-once Pan handoff."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from packages.core import session as sess, worker
from packages.core.adapters.cbc.adapter import CbcAdapter
from packages.core.adapters.cbc.native_adapter import CbcNativeAdapter


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, 'SESSION_DIR', tmp_path / 'sessions')
    worker.workers.clear()
    worker._queue_locks.clear()
    worker._spawn_locks.clear()
    sess._cache.clear()
    worker.set_broadcaster(None)
    yield
    worker.workers.clear()
    sess._cache.clear()


def live():
    s = sess.Session(id='cbc-steer-session', name='cbc-steer', adapter='cbc')
    s.cli_session_id = 'native-session'
    sess._cache[s.id] = s
    stdin = Mock(drain=AsyncMock())
    w = worker.Worker(worker_id='cbc-worker', session_id=s.id, adapter=CbcAdapter(),
                      process=SimpleNamespace(returncode=None, stdin=stdin), status='running',
                      generation=4)
    w._execution_seq = 9
    w._current_seq = 7
    w._current_task_id = 'original-task'
    w._steer_run_id = 'original-run'
    worker.workers[w.worker_id] = w
    return s, w, stdin


def ack(request, *, accepted=True, session_id='native-session'):
    return {'type': 'control_response', 'response': {'subtype': 'success',
            'request_id': request, 'response': {'steered': accepted,
            'session_id': session_id, **({} if accepted else {'reason': 'stale'})}}}


def test_native_control_wire_requires_bound_run_and_keeps_tui_distinct():
    adapter = CbcAdapter()
    assert adapter.supports_native_steer and adapter.supports_steer_receipt
    wire = json.loads(adapter.encode_control_message({'type': 'steer', 'text': 'change',
                      'requestId': 'request', 'sessionId': 'native-session',
                      'expectedRequestId': 'original-run'}))
    assert wire == {'type': 'control_request', 'request_id': 'request', 'request': {
        'subtype': 'steer', 'session_id': 'native-session',
        'expected_request_id': 'original-run', 'content_blocks': [{'type': 'text', 'text': 'change'}]}}
    with pytest.raises(ValueError, match='run'):
        adapter.encode_control_message({'type': 'steer', 'text': 'change', 'requestId': 'request'})
    assert not CbcNativeAdapter().supports_native_steer


def test_native_admission_is_not_consumption_and_malformed_response_is_unknown():
    adapter = CbcAdapter()
    accepted = adapter.normalize_steer_receipt(ack('request'))
    assert accepted['outcome'] == 'accepted' and accepted['consumption'] == 'unconfirmed'
    assert adapter.normalize_steer_receipt(ack('request', accepted=False))['outcome'] == 'rejected'
    malformed = ack('request'); malformed['response']['response'] = {}
    assert adapter.normalize_steer_receipt(malformed)['outcome'] == 'unknown'
    assert adapter.normalize_steer_receipt({'type': 'assistant'}) is None


def test_direct_admission_persists_original_run_and_idempotency_after_restart():
    async def scenario():
        s, w, stdin = live()
        def write(data):
            control = json.loads(data)
            assert control['request']['expected_request_id'] == 'original-run'
            worker._route_steer_receipt(w, ack(control['request_id']))
        stdin.write.side_effect = write
        assert await worker.steer_worker(w.worker_id, 'change', 'steer:once') is None
        row = next(row for row in s.history if row.get('clientMessageId') == 'steer:once')
        assert row['steer']['runId'] == 'original-run'
        assert row['steer']['taskId'] == 'original-task'
        assert row['steer']['consumption'] == 'unconfirmed'
        w.generation += 1; w._steer_run_id = 'next-run'
        assert await worker.steer_worker(w.worker_id, 'change', 'steer:once') is None
        assert stdin.write.call_count == 1
    asyncio.run(scenario())


def test_direct_unknown_survives_runtime_replacement_without_duplicate_write():
    async def scenario():
        s, w, stdin = live()
        def write(data):
            control = json.loads(data)
            worker._route_steer_receipt(w, {'type': 'control_response', 'response': {
                'subtype': 'success', 'request_id': control['request_id'], 'response': {}}})
        stdin.write.side_effect = write
        assert (await worker.steer_worker(w.worker_id, 'change', 'steer:unknown')).startswith('Steer outcome unknown')
        w._direct_steer_pending = False; w.generation += 1
        assert (await worker.steer_worker(w.worker_id, 'change', 'steer:unknown')).startswith('Steer outcome unknown')
        assert stdin.write.call_count == 1 and not s.history
    asyncio.run(scenario())


def test_unconfirmed_run_is_rejected_before_wire_write():
    async def scenario():
        _, w, stdin = live(); w._steer_run_id = None
        error = await worker.steer_worker(w.worker_id, 'change', 'steer:no-run')
        assert 'run' in error.lower()
        stdin.write.assert_not_called()
    asyncio.run(scenario())


def test_queue_admission_retains_item_identity_and_original_task():
    async def scenario():
        s, w, stdin = live()
        item = {'type': 'task', 'kind': 'task', 'text': 'queued change', 'source': 'user',
                'queueItemId': 'q-original', 'revision': 1, 'deliveryState': 'queued'}
        s.queue_pending.append(item)
        def write(data):
            control = json.loads(data); worker._route_steer_receipt(w, ack(control['request_id']))
        stdin.write.side_effect = write
        assert await worker.steer_queue_item(s.id, 'q-original', 1) is None
        assert not s.queue_pending
        row = next(row for row in s.history if 'q-original' in row.get('queueItemIds', []))
        assert row['steer']['runId'] == 'original-run'
        assert row['steer']['taskId'] == 'original-task'
        assert w._current_task_id == 'original-task' and w._current_seq == 7
    asyncio.run(scenario())


def test_late_ack_records_old_run_without_relabelling_the_new_task():
    async def scenario():
        s, w, stdin = live(); written = asyncio.Event()
        stdin.write.side_effect = lambda _: written.set()
        pending = asyncio.create_task(worker.steer_worker(w.worker_id, 'old input', 'steer:late'))
        await asyncio.wait_for(written.wait(), 1)
        request = json.loads(stdin.write.call_args.args[0])['request_id']
        w._terminal_handled = True; w._current_task_id = 'new-task'
        w._current_seq = 8; w._execution_seq = 10; w._steer_run_id = 'new-run'
        worker._route_steer_receipt(w, ack(request))
        assert await pending is None
        row = s.history[-1]
        assert row['steer']['runId'] == 'original-run' and row['steer']['taskSeq'] == 7
        assert row['steer']['taskId'] == 'original-task'
        assert w._current_task_id == 'new-task' and w._current_seq == 8
        worker._route_steer_receipt(w, ack(request))
        assert len(s.history) == 1 and stdin.write.call_count == 1
    asyncio.run(scenario())


def test_wrong_session_ack_and_recovered_unknown_never_replay():
    async def scenario():
        s, w, stdin = live()
        def write(data):
            request = json.loads(data)['request_id']
            worker._route_steer_receipt(w, ack(request, session_id='other-session'))
        stdin.write.side_effect = write
        error = await worker.steer_worker(w.worker_id, 'change', 'steer:wrong-session')
        assert error.startswith('Steer outcome unknown')
        sess._cache.clear()
        restored = sess.get(s.id)
        assert restored.adapter_config['_steer_requests']['steer:wrong-session']['status'] == 'unknown'
        worker.workers.clear()
        assert (await worker.steer_session_worker(s.id, 'change', 'steer:wrong-session')).startswith('Steer outcome unknown')
        assert stdin.write.call_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('replace', ['stdin', 'process', 'generation', 'run', 'owner'])
def test_cbc_reservation_cannot_retarget_after_persistence_yields(monkeypatch, replace):
    async def scenario():
        _, w, stdin = live(); other = Mock(drain=AsyncMock())
        persist = sess._persist_async_outcome
        async def replace_after_save(*args, **kwargs):
            result = await persist(*args, **kwargs)
            if replace == 'stdin': w.process.stdin = other
            elif replace == 'process': w.process = SimpleNamespace(returncode=None, stdin=other)
            elif replace == 'generation': w.generation += 1
            elif replace == 'run': w._steer_run_id = 'new-run'
            elif replace == 'owner': w.native_control_mode = 'native'
            return result
        monkeypatch.setattr(sess, '_persist_async_outcome', replace_after_save)
        error = await worker.steer_worker(w.worker_id, 'never retarget', 'steer:replace')
        assert error is not None and not error.startswith('Steer outcome unknown')
        stdin.write.assert_not_called(); other.write.assert_not_called()
    asyncio.run(scenario())


def test_concurrent_duplicate_sends_only_one_native_input():
    async def scenario():
        s, w, stdin = live(); written = asyncio.Event()
        stdin.write.side_effect = lambda _: written.set()
        first = asyncio.create_task(worker.steer_worker(w.worker_id, 'change', 'steer:concurrent'))
        await asyncio.wait_for(written.wait(), 1)
        duplicate = await worker.steer_worker(w.worker_id, 'change', 'steer:concurrent')
        assert 'unknown' in duplicate or 'awaiting' in duplicate
        request = json.loads(stdin.write.call_args.args[0])['request_id']
        worker._route_steer_receipt(w, ack(request))
        assert await first is None
        assert stdin.write.call_count == 1 and len(s.history) == 1
    asyncio.run(scenario())


def test_self_queue_uses_cbc_admission_without_starting_another_turn(monkeypatch):
    from packages.core import self_queue
    async def scenario():
        s, w, stdin = live()
        item = {'type': 'task', 'kind': 'task', 'text': 'own queue input', 'source': 'agent',
                'sourceSessionId': s.id, 'queueItemId': 'q-own', 'revision': 1, 'deliveryState': 'queued'}
        s.queue_pending.append(item)
        monkeypatch.setattr(worker, 'restart_or_start_worker', AsyncMock(side_effect=AssertionError('must not restart')))
        def write(data):
            worker._route_steer_receipt(w, ack(json.loads(data)['request_id']))
        stdin.write.side_effect = write
        result = await self_queue.consume(s.id, 'q-own')
        assert result['ok'] and result['status'] == 'consumed'
        assert result['steer']['runId'] == 'original-run'
        assert result['steer']['consumption'] == 'unconfirmed'
        assert (await self_queue.consume(s.id, 'q-own'))['alreadyConsumed']
        assert stdin.write.call_count == 1
    asyncio.run(scenario())


def test_pause_and_edit_lease_block_cbc_queue_before_admission():
    async def scenario():
        s, w, stdin = live()
        item = {'type': 'task', 'kind': 'task', 'text': 'queued', 'source': 'user',
                'queueItemId': 'q-gated', 'revision': 1, 'deliveryState': 'queued'}
        s.queue_pending.append(item)
        s.queue_paused = True
        assert 'Pause' in await worker.steer_queue_item(s.id, 'q-gated', 1)
        s.queue_paused = False
        worker.acquire_queue_edit_lock(s, 'q-gated', 'editor')
        assert await worker.steer_queue_item(s.id, 'q-gated', 1) is not None
        stdin.write.assert_not_called()
    asyncio.run(scenario())


def test_direct_cbc_pause_is_checked_at_control_handoff():
    async def scenario():
        s, w, stdin = live()
        s.queue_paused = True
        assert 'Pause' in await worker.steer_worker(w.worker_id, 'blocked', 'steer:paused')
        assert not s.adapter_config['_steer_requests']
        stdin.write.assert_not_called()
    asyncio.run(scenario())


def test_http_unknown_is_not_a_rejection_and_success_exposes_admission(monkeypatch):
    from packages.web import server
    async def scenario():
        _, w, _ = live()
        monkeypatch.setattr(worker, 'steer_session_worker', AsyncMock(return_value='Steer outcome unknown: receipt missing'))
        result = await server.api_session_worker_steer(w.session_id, {'text': 'change', 'messageId': 'steer:http'})
        assert result['outcome'] == 'unknown'
        monkeypatch.setattr(worker, 'steer_session_worker', AsyncMock(return_value=w))
        result = await server.api_session_worker_steer(w.session_id, {'text': 'change', 'messageId': 'steer:http'})
        assert result['outcome'] == 'accepted' and result['consumption'] == 'unconfirmed'
    asyncio.run(scenario())


def test_cbc_cancelled_sender_cannot_write_after_queue_lock_is_released(monkeypatch):
    async def scenario():
        _, w, stdin = live(); entered = asyncio.Event(); senders = []
        send = worker.send_control_message
        async def observed(*args, **kwargs):
            senders.append(asyncio.current_task()); entered.set()
            return await send(*args, **kwargs)
        monkeypatch.setattr(worker, 'send_control_message', observed)
        lock = worker.queue_lock(w.session_id); await lock.acquire()
        pending = asyncio.create_task(worker.steer_worker(w.worker_id, 'never write', 'steer:cancelled'))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError): await pending
            assert senders[0].cancelled()
        finally:
            lock.release()
            await asyncio.sleep(0)
        stdin.write.assert_not_called()
        assert (await worker.steer_worker(w.worker_id, 'never write', 'steer:cancelled')).startswith('Steer outcome unknown')
        for future in list(w._steer_receipts.values()): future.cancel()
    asyncio.run(scenario())


def test_replacement_reader_cannot_settle_original_pipe_receipt():
    async def scenario():
        s, w, stdin = live(); written = asyncio.Event()
        stdin.write.side_effect = lambda _: written.set()
        pending = asyncio.create_task(worker.steer_worker(w.worker_id, 'original', 'steer:old-reader'))
        await asyncio.wait_for(written.wait(), 1)
        request = json.loads(stdin.write.call_args.args[0])['request_id']
        original_runtime = (w, w.generation, w.process, stdin)
        w.generation += 1
        worker._route_steer_receipt(w, ack(request))
        assert not w._steer_receipts[request].done()
        worker._route_steer_receipt(w, ack(request), runtime=original_runtime)
        assert await pending is None
        assert s.history[-1]['steer']['workerGeneration'] == 4
    asyncio.run(scenario())


@pytest.mark.parametrize('status,detail', [('unknown', 'provider detail without status words'),
    ('rejected', 'Steer outcome unknown: awaiting receipt but definitively rejected')])
def test_direct_outcome_is_structured_independent_of_provider_prose(status, detail):
    async def scenario():
        s, w, stdin = live()
        def write(data):
            request = json.loads(data)['request_id']
            worker._route_steer_receipt(w, {'type': 'pan.steer_receipt', 'requestId': request,
                'ok': False, 'outcome': status, 'error': detail})
        stdin.write.side_effect = write
        outcome = {}
        error = await worker.steer_worker(w.worker_id, 'change', 'steer:structured', outcome=outcome)
        assert error and outcome['status'] == status
        assert w._direct_steer_pending == (status == 'unknown')
        assert bool(s.adapter_config['_steer_requests']) == (status == 'unknown')
    asyncio.run(scenario())


@pytest.mark.parametrize('endpoint', ['worker', 'session'])
@pytest.mark.parametrize('status,detail', [('unknown', 'opaque provider detail'),
    ('rejected', 'Steer outcome unknown: awaiting receipt')])
def test_http_direct_uses_structured_outcome(monkeypatch, endpoint, status, detail):
    from packages.web import server
    async def scenario():
        s, w, stdin = live()
        def write(data):
            worker._route_steer_receipt(w, {'type': 'pan.steer_receipt',
                'requestId': json.loads(data)['request_id'], 'ok': False,
                'outcome': status, 'error': detail})
        stdin.write.side_effect = write
        body = {'text': 'change', 'messageId': 'steer:http-typed'}
        result = await (server.api_worker_steer(w.worker_id, body) if endpoint == 'worker'
                        else server.api_session_worker_steer(s.id, body))
        assert result['outcome'] == status
        assert w._direct_steer_pending == (status == 'unknown')
    asyncio.run(scenario())


def test_unknown_internal_detail_without_prefix_cannot_release_direct_gate(monkeypatch):
    async def scenario():
        s, w, stdin = live()
        async def uncertain(*args, outcome, **kwargs):
            outcome['status'] = 'unknown'
            return 'opaque detail'
        monkeypatch.setattr(worker, '_send_steer_with_receipt', uncertain)
        outcome = {}
        assert await worker.steer_worker(w.worker_id, 'change', 'steer:opaque', outcome=outcome) == 'opaque detail'
        assert outcome['status'] == 'unknown' and w._direct_steer_pending
        assert s.adapter_config['_steer_requests']['steer:opaque']['status'] == 'unknown'
        stdin.write.assert_not_called()
    asyncio.run(scenario())


@pytest.mark.parametrize('status', ['accepted', 'rejected'])
def test_true_late_direct_receipt_settles_only_original_transaction(monkeypatch, status):
    async def scenario():
        s, w, stdin = live(); settled = asyncio.Event()
        original = worker._persist_queue_steer_state
        async def persisted(*args, **kwargs):
            result = await original(*args, **kwargs)
            settled.set()
            return result
        monkeypatch.setattr(worker, '_persist_queue_steer_state', persisted)
        record = worker._record_direct_steer
        async def recorded(*args, **kwargs):
            result = await record(*args, **kwargs)
            settled.set()
            return result
        monkeypatch.setattr(worker, '_record_direct_steer', recorded)
        runtime = (w, w.generation, w.process, stdin)
        stdin.write.side_effect = lambda data: setattr(w.process, 'returncode', 0)
        outcome = {}
        assert await worker.steer_worker(w.worker_id, 'old input', 'steer:truly-late', outcome=outcome)
        assert outcome['status'] == 'unknown' and w._direct_steer_pending
        request = json.loads(stdin.write.call_args.args[0])['request_id']
        w._steer_run_id = 'new-run'; w._current_seq = 8; w._current_task_id = 'new-task'
        worker._route_steer_receipt(w, {'type': 'pan.steer_receipt', 'requestId': request,
            'ok': status == 'accepted', 'outcome': status,
            'cliSessionId': 'native-session', 'error': None if status == 'accepted' else
            'Steer outcome unknown: awaiting receipt'}, runtime=runtime)
        await asyncio.wait_for(settled.wait(), 1)
        assert not w._direct_steer_pending
        if status == 'accepted':
            assert s.history[-1]['steer']['runId'] == 'original-run'
            assert s.history[-1]['steer']['taskSeq'] == 7
        else:
            assert not s.history and not s.adapter_config['_steer_requests']
        assert w._current_task_id == 'new-task'
    asyncio.run(scenario())


@pytest.mark.parametrize('broken', [False, True])
def test_claude_drain_boundary_exposes_structured_outcome(broken):
    from packages.core.adapters.claude.adapter import ClaudeAdapter
    async def scenario():
        s, w, stdin = live(); w.adapter = ClaudeAdapter()
        if broken: stdin.drain.side_effect = BrokenPipeError('opaque failure')
        outcome = {}
        error = await worker.steer_worker(w.worker_id, 'change', outcome=outcome)
        assert outcome['status'] == ('unknown' if broken else 'accepted')
        assert bool(error) == broken and w._direct_steer_pending == broken
        assert bool(s.history) != broken
    asyncio.run(scenario())
