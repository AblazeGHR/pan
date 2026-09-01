"""Regression tests for durable user queueing and restart ownership.

These tests deliberately exercise the boundary between a dead worker and the
next generation: queued user text must remain a task, duplicate browser sends
must be receipt-idempotent, and restart may not start recovery before the old
consumer has relinquished ownership.
"""

import asyncio
import sys
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CbcAdapter


async def _save_noop(_session):
    return None


def _cleanup():
    for task in list(worker._recovery_tasks.values()):
        task.cancel()
    worker._recovery_tasks.clear()
    worker.workers.clear()
    worker._inflight_task_ids.clear()
    worker._task_status.clear()
    worker._spawn_locks.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _session(sid="ses-hardening"):
    s = _sess.Session(id=sid, name=sid, adapter="cbc", model="test")
    _sess._cache[s.id] = s
    return s


def _worker(s, wid="worker-hardening"):
    w = worker.Worker(
        worker_id=wid, session_id=s.id, adapter=CbcAdapter(), status="idle",
        process=None, pending_signal=asyncio.Queue(), _task_done=asyncio.Event(),
    )
    worker.workers[wid] = w
    return w


def test_offline_user_message_is_a_durable_user_task(monkeypatch):
    _cleanup()
    s = _session()
    monkeypatch.setattr(_sess, "save_async", _save_noop)

    result = asyncio.run(worker.send_session(
        s.id, "do not turn me into an agent report", source="user",
        client_message_id="browser-1"))

    assert result["status"] == "queued"
    assert result["pendingSpawn"] is True
    assert len(s.queue_pending) == 1
    assert s.queue_pending[0]["type"] == "task"
    assert s.queue_pending[0]["text"] == "do not turn me into an agent report"
    assert s.queue_pending[0]["source"] == "user"
    assert s.queue_pending[0]["seq"] == 1
    assert s.queue_pending[0]["deliveryState"] == "queued"
    assert s.queue_pending[0]["clientMessageId"] == "browser-1"
    assert s.accepted_input_ids == ["browser-1"]
    _cleanup()


def test_offline_enqueue_starts_recovery_without_waiting_for_watchdog(monkeypatch):
    _cleanup()
    s = _session("ses-immediate-recovery")
    calls = []
    monkeypatch.setattr(_sess, "save_async", _save_noop)

    async def fake_create(session_id):
        calls.append(session_id)
        return "spawned"

    monkeypatch.setattr(worker, "create_worker", fake_create)

    async def scenario():
        result = await worker.send_session(s.id, "wake now", source="user")
        # Let the scheduled recovery task run; the HTTP acknowledgement itself
        # must not wait for create_worker.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return result

    result = asyncio.run(scenario())
    assert result["pendingSpawn"] is True
    assert calls == [s.id]
    _cleanup()


def test_concurrent_offline_enqueue_coalesces_recovery(monkeypatch):
    _cleanup()
    s = _session("ses-coalesced-recovery")
    calls = []
    entered = asyncio.Event()
    release = asyncio.Event()
    monkeypatch.setattr(_sess, "save_async", _save_noop)

    async def fake_create(session_id):
        calls.append(session_id)
        entered.set()
        await release.wait()
        return "spawned"

    monkeypatch.setattr(worker, "create_worker", fake_create)

    async def scenario():
        first, second = await asyncio.gather(
            worker.send_session(s.id, "one", source="user"),
            worker.send_session(s.id, "two", source="user"),
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert calls == [s.id]
        release.set()
        await asyncio.sleep(0)
        return first, second

    first, second = asyncio.run(scenario())
    assert first["status"] == second["status"] == "queued"
    assert len(s.queue_pending) == 2
    _cleanup()


def test_client_receipt_id_deduplicates_reconnect_retransmit(monkeypatch):
    _cleanup()
    s = _session()
    w = _worker(s)
    monkeypatch.setattr(_sess, "save_async", _save_noop)

    async def scenario():
        first = await worker.send_task(w.worker_id, "once", source="user",
                                       client_message_id="browser-2")
        second = await worker.send_task(w.worker_id, "once", source="user",
                                        client_message_id="browser-2")
        return first, second

    assert asyncio.run(scenario()) == (None, None)
    assert len(s.queue_pending) == 1
    assert w.pending_signal.qsize() == 1
    assert s.accepted_input_ids == ["browser-2"]
    _cleanup()


def test_old_client_message_id_remains_idempotent_after_receipt_ledger_eviction(monkeypatch):
    """Receipt history prevents a late browser retry after the bounded ledger rolls."""
    _cleanup()
    s = _session("ses-old-receipt")
    s.history = [{
        "role": "user", "content": "already accepted",
        "clientMessageId": "old-browser-id",
    }]
    s.accepted_input_ids = []
    monkeypatch.setattr(_sess, "save_async", _save_noop)

    item, err = asyncio.run(worker._persist_task_item(
        s, "retry", "user", None, None, "old-browser-id"))

    assert item is None and err is None
    assert s.queue_pending == []
    _cleanup()


def test_websocket_offline_user_input_is_acknowledged_once(monkeypatch):
    """Dashboard input uses the durable session route even without a worker."""
    from fastapi.testclient import TestClient
    import packages.web.server as server

    _cleanup()
    s = _session("ses-ws-hardening")
    monkeypatch.setattr(_sess, "save_async", _save_noop)

    with TestClient(server.app) as client:
        with client.websocket_connect("/ws") as ws:
            payload = {
                "type": "user_inject", "sessionId": s.id,
                "text": "offline dashboard input", "clientMessageId": "browser-ws-1",
            }
            ws.send_json(payload)
            assert ws.receive_json()["type"] == "user_inject.accepted"
            # Simulate the exact reconnect ambiguity: the first accepted ack
            # was lost, so the browser sends the same id again.
            ws.send_json(payload)
            assert ws.receive_json()["type"] == "user_inject.accepted"

    # The immediate recovery task may already let the Worker consume the item;
    # either way the browser receipt ledger must contain exactly one id and the
    # user content must occur at most once in history.
    assert len(s.accepted_input_ids) == 1
    assert s.accepted_input_ids[0] == "browser-ws-1"
    assert [entry.get("content") for entry in s.history
            if entry.get("role") == "user"].count("offline dashboard input") <= 1
    _cleanup()
    s = _session()
    w = _worker(s)
    monkeypatch.setattr(_sess, "save_async", _save_noop)

    async def scenario():
        first = await worker.send_task(w.worker_id, "once", source="user",
                                       client_message_id="browser-2")
        second = await worker.send_task(w.worker_id, "once", source="user",
                                        client_message_id="browser-2")
        return first, second

    assert asyncio.run(scenario()) == (None, None)
    assert len(s.queue_pending) == 1
    assert w.pending_signal.qsize() == 1
    assert s.accepted_input_ids == ["browser-2"]
    _cleanup()


def test_restart_migrates_legacy_user_text_to_task_not_report(monkeypatch):
    _cleanup()


def test_unmarked_legacy_text_defaults_to_user_not_agent():
    _cleanup()
    s = _session()
    s.queue_pending = [{"text": "legacy text with lost provenance"}]
    w = _worker(s)

    assert worker._recover_pending_signals(w, s) is True
    assert s.queue_pending[0]["type"] == "task"
    assert s.queue_pending[0]["source"] == "user"
    assert w.pending_signal.get_nowait()["type"] == "task_signal"
    _cleanup()


def test_non_durable_direct_signal_is_ignored(monkeypatch):
    _cleanup()
    s = _session()
    w = _worker(s)
    monkeypatch.setattr(_sess, "save_async", _save_noop)
    received = []

    async def fake_stream(_w, text, source, _s):
        received.append((text, source))

    async def scenario():
        monkeypatch.setattr(worker, "_consumer_stream", fake_stream)
        await w.pending_signal.put({"text": "ambiguous"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())
    assert received == [], "body-bearing signals have no at-most-once receipt"
    _cleanup()
    s = _session()
    # This is the historical direct-signal shape that previously fell through
    # the report branch after a process death.
    s.queue_pending = [{"text": "legacy dashboard input", "source": "user"}]
    w = _worker(s)

    changed = worker._recover_pending_signals(w, s)
    signal = w.pending_signal.get_nowait()

    assert changed is True
    assert signal["type"] == "task_signal"
    assert s.queue_pending[0]["type"] == "task"
    assert s.queue_pending[0]["source"] == "user"
    assert "result" not in s.queue_pending[0]
    _cleanup()


def test_restart_waits_for_old_consumer_cleanup_before_recovery(monkeypatch):
    _cleanup()
    s = _session()
    w = _worker(s)
    w.process = object()
    cleanup_finished = asyncio.Event()
    recovery_started_after_cleanup = []

    async def old_consumer():
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_finished.set()

    async def old_stdout():
        await asyncio.Event().wait()

    async def old_watchdog():
        await asyncio.Event().wait()

    async def fake_kill_process(_w):
        return None

    async def fake_spawn(_session_id, adapter, extra_args=None):
        return object()

    async def fake_restart_tasks(_w):
        recovery_started_after_cleanup.append(cleanup_finished.is_set())

    async def scenario():
        w._consume_task = asyncio.create_task(old_consumer())
        w._stdout_task = asyncio.create_task(old_stdout())
        w._watchdog_task = asyncio.create_task(old_watchdog())
        await asyncio.sleep(0)
        await worker.restart_worker(w.worker_id)

    monkeypatch.setattr(worker, "_kill_process_tree", fake_kill_process)
    monkeypatch.setattr(worker, "_spawn_process", fake_spawn)
    monkeypatch.setattr(worker, "_restart_tasks", fake_restart_tasks)
    asyncio.run(scenario())

    assert recovery_started_after_cleanup == [True]
    _cleanup()


def test_interrupt_reaps_old_stream_process_before_replaying_task(tmp_path, monkeypatch):
    """A real subprocess must be gone before restart recovery reuses its task."""
    _cleanup()


def test_running_restart_does_not_replay_consumed_task(tmp_path, monkeypatch):
    """Restarting a busy worker never replays the task already consumed by it."""
    _cleanup()
    fake_cli = tmp_path / "fake_cbc_restart.py"
    fast_marker = tmp_path / "fast"
    fake_cli.write_text(
        """import json, os, sys, time
seen = 0
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('type') != 'user':
        continue
    seen += 1
    if seen == 1 and not os.path.exists(os.environ['PAN_TEST_FAST']):
        time.sleep(30)
    print(json.dumps({'type':'result','result':msg.get('message','ok'),'is_error':False}), flush=True)
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(CbcAdapter, "_resolve_cbc_argv",
                        lambda _self: [sys.executable, str(fake_cli)])
    monkeypatch.setenv("PAN_TEST_FAST", str(fast_marker))
    monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)
    s = _session("ses-running-restart")
    s.workdir = str(tmp_path)

    async def scenario():
        original_save = _sess.save_async
        _sess.save_async = _save_noop
        try:
            w = await worker.create_worker(s.id)
            assert isinstance(w, worker.Worker)
            assert (await worker.assign(s.id, "first", task_id="restart-1"))["status"] == "queued"
            for _ in range(100):
                if w.status == "running":
                    break
                await asyncio.sleep(0.01)
            assert w.status == "running"
            assert (await worker.assign(s.id, "second", task_id="restart-2"))["status"] == "queued"
            assert [item["text"] for item in s.queue_pending] == ["second"]
            fast_marker.write_text("1", encoding="ascii")
            assert await worker.restart_worker(w.worker_id) is None
            await asyncio.sleep(0.2)
            assert all(item["text"] != "first" for item in s.queue_pending)
            for _ in range(300):
                if not s.queue_pending and s.last_result:
                    break
                await asyncio.sleep(0.01)
            assert s.queue_pending == []
            assert s.last_result and s.last_result["result"]["content"][0]["text"] == "second"
        finally:
            active = worker.find_worker_by_session(s.id)
            if active:
                await worker.kill_worker(active.worker_id)
            _sess.save_async = original_save

    asyncio.run(scenario())
    _cleanup()
    fake_cli = tmp_path / "fake_cbc.py"
    fast_marker = tmp_path / "fast"
    fake_cli.write_text(
        """import json, os, sys, time
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('type') != 'user':
        continue
    if not os.path.exists(os.environ['PAN_TEST_FAST']):
        time.sleep(30)
    print(json.dumps({'type':'result','result':'replayed','is_error':False}), flush=True)
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(CbcAdapter, "_resolve_cbc_argv",
                        lambda _self: [sys.executable, str(fake_cli)])
    monkeypatch.setenv("PAN_TEST_FAST", str(fast_marker))
    monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)
    s = _session("ses-process-hardening")
    s.workdir = str(tmp_path)

    async def scenario():
        original_save = _sess.save_async
        _sess.save_async = _save_noop
        try:
            w = await worker.create_worker(s.id)
            assert isinstance(w, worker.Worker)
            assert await worker.assign(s.id, "long task", task_id="task-process") == {
                "status": "queued", "workerId": w.worker_id,
                "sessionId": s.id, "taskId": "task-process",
            }
            for _ in range(100):
                if w.status == "running":
                    break
                await asyncio.sleep(0.01)
            assert w.status == "running"
            old_pid = w.process.pid
            fast_marker.write_text("1", encoding="ascii")
            assert await worker.interrupt_worker(w.worker_id) is None
            assert not psutil.pid_exists(old_pid)
            await asyncio.sleep(0.2)
            assert s.last_result is None
            assert s.queue_pending == [], "interrupt must not requeue a consumed task"
            assert await worker.retry_pending_item(s.id, "missing") == "Queue item not found"
            await asyncio.sleep(0.2)
            assert s.last_result is None, "replacement worker must not replay the task"
            assert s.queue_pending == []
        finally:
            active = worker.find_worker_by_session(s.id)
            if active:
                await worker.kill_worker(active.worker_id)
            _sess.save_async = original_save

    asyncio.run(scenario())
    _cleanup()
