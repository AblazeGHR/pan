"""T-062.7 bounded result-cursor and authoritative resync tests."""

import asyncio
from types import SimpleNamespace

import packages.core.session as session
import packages.core.worker as worker
import packages.web.server as server
from packages.core.adapters.cbc.adapter import CbcAdapter


class _FastWS:
    def __init__(self):
        self.sent = []
        self.closed = []

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=None, reason=None):
        self.closed.append((code, reason))


def _run(coro):
    return asyncio.run(coro)


def _cleanup(ws=None, sid=None):
    for client in tuple(server._ws_outbound.values()):
        if client._sender_task is not None:
            client._sender_task.cancel()
    server._ws_outbound.clear()
    server.ws_clients.clear()
    server.agent_clients.clear()
    server.agent_subscriptions.clear()
    if ws is not None:
        server._ws_outbound.pop(ws, None)
    if sid is not None:
        session._cache.pop(sid, None)
        worker.workers.clear()
        worker._workers_by_session.clear()


def _terminal(cursor, text, *, task_seq=None, status="done"):
    return {
        "resultCursor": cursor,
        "terminalKey": f"seq:{cursor}",
        "status": status,
        "result": text,
        "taskSeq": task_seq if task_seq is not None else cursor,
        "workerId": f"worker-{cursor}",
        "generation": 1,
    }


def test_agent_cursor_replays_every_retained_terminal_and_is_idempotent():
    sid = "ses_t0627_replay"
    s = session.Session(id=sid, name="t0627-replay")
    s.result_cursor = 3
    s.terminal_results = [_terminal(1, "one"), _terminal(2, "two"), _terminal(3, "three")]
    s.last_result = dict(s.terminal_results[-1])
    session._cache[sid] = s
    ws = _FastWS()
    server.agent_subscriptions[ws] = server.AgentSubscription()
    try:
        _run(server._replay_agent_results(ws, [sid], {sid: 1}))
        assert [item["resultCursor"] for item in ws.sent] == [2, 3]
        assert [item["result"] for item in ws.sent] == ["two", "three"]
        assert server.agent_subscriptions[ws].consumed_cursor == {sid: 3}

        # Repeating the same reconnect cursor is safe for the caller, and a
        # cursor at the current boundary has no duplicate terminal frames.
        ws.sent.clear()
        _run(server._replay_agent_results(ws, [sid], {sid: 3}))
        assert ws.sent == []
    finally:
        _cleanup(ws, sid)


def test_expired_result_cursor_returns_snapshot_boundary_with_queue_and_history():
    sid = "ses_t0627_expired"
    s = session.Session(
        id=sid,
        name="t0627-expired",
        history=[{"role": "user", "content": "hello"}],
        queue_pending=[{
            "type": "task",
            "id": "q-1",
            "queueItemId": "q-1",
            "text": "pending",
            "source": "user",
            "deliveryState": "queued",
            "position": 0,
        }],
        queue_revision=7,
        result_cursor=10,
        terminal_results=[_terminal(10, "latest")],
        last_result=_terminal(10, "latest"),
    )
    session._cache[sid] = s
    ws = _FastWS()
    server.agent_subscriptions[ws] = server.AgentSubscription()
    try:
        _run(server._replay_agent_results(ws, [sid], {sid: 0}))
        assert [item["type"] for item in ws.sent] == [
            "resync_required", "resync.snapshot",
        ]
        snapshot = ws.sent[-1]
        assert snapshot["boundary"] == "authoritative"
        assert snapshot["details"][sid]["queue"]["queueRevision"] == 7
        assert snapshot["details"][sid]["history"] == s.history
        assert snapshot["details"][sid]["summaryRevision"] == s.summary_projection["revision"]
    finally:
        _cleanup(ws, sid)


def test_terminal_persistence_assigns_durable_cursor_before_publication(monkeypatch):
    sid = "ses_t0627_terminal"
    s = session.Session(id=sid, name="t0627-terminal")
    session._cache[sid] = s
    w = worker.Worker(
        worker_id="worker-t0627",
        session_id=sid,
        adapter=CbcAdapter(),
        status="running",
        process=SimpleNamespace(returncode=None),
        pending_signal=asyncio.Queue(),
        _task_done=asyncio.Event(),
    )
    worker.workers[w.worker_id] = w
    monkeypatch.setattr(worker, "_flush_history_now", _noop_flush)
    try:
        w._current_seq = 1
        w._current_task_id = "task-one"
        w._current_task_idempotent = True
        first = _run(worker._persist_terminal_state(w, s, "done", "one"))
        assert first["resultCursor"] == 1
        assert s.last_result["resultCursor"] == 1
        assert s.terminal_results[0]["terminalKey"] == first["terminalKey"]

        w._terminal_handled = False
        w._current_seq = 2
        w._current_task_id = "task-two"
        second = _run(worker._persist_terminal_state(w, s, "error", "two"))
        assert second["resultCursor"] == 2
        assert [item["result"] for item in s.terminal_results] == ["one", "two"]
    finally:
        _cleanup(sid=sid)


def test_pre_handoff_error_keeps_result_idle_order(monkeypatch):
    sid = "ses_t0627_error_order"
    s = session.Session(id=sid, name="t0627-error-order")
    session._cache[sid] = s
    w = worker.Worker(
        worker_id="worker-t0627-error",
        session_id=sid,
        adapter=CbcAdapter(),
        status="running",
        process=SimpleNamespace(returncode=1),
        pending_signal=asyncio.Queue(),
        _task_done=asyncio.Event(),
    )
    worker.workers[w.worker_id] = w
    events = []

    async def capture_broadcast(event):
        events.append(event)

    async def ignore_report(*_args, **_kwargs):
        return None

    monkeypatch.setattr(worker, "_bcast", capture_broadcast)
    monkeypatch.setattr(worker, "_enqueue_report", ignore_report)
    monkeypatch.setattr(worker, "_flush_history_now", _noop_flush)
    try:
        w._current_seq = 1
        w._current_task_id = "task-pre-handoff"
        w._current_task_idempotent = True
        _run(worker._finish_task_error(w, s, "dead before handoff"))

        assert [event["type"] for event in events] == [
            "worker.result", "worker.status",
        ]
        assert events[0]["resultCursor"] == 1
        assert events[1]["status"] == "idle"
        assert s.last_result["result"] == "dead before handoff"
    finally:
        _cleanup(sid=sid)


async def _noop_flush(_worker):
    return None
