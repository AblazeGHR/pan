"""Regression coverage for source type vs source Session ID metadata."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CbcAdapter
from packages.web import server


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(_sess, "_all_loaded", False)
    worker.workers.clear()
    worker._task_status.clear()
    worker._spawn_locks.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)
    yield
    worker.workers.clear()
    worker._task_status.clear()
    worker._spawn_locks.clear()
    _sess._cache.clear()
    _sess._all_loaded = False


def _session(sid, **kwargs):
    value = _sess.Session(id=sid, name=sid, **kwargs)
    _sess._cache[sid] = value
    return value


def _worker(sid, wid="worker-1"):
    proc = AsyncMock()
    proc.returncode = None
    value = worker.Worker(
        worker_id=wid, session_id=sid, adapter=CbcAdapter(), status="idle",
        process=proc, pending_signal=asyncio.Queue(),
    )
    worker.workers[wid] = value
    return value


def test_send_session_persists_source_type_and_session_id(monkeypatch):
    target = _session("target")
    caller = _session("caller")
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    monkeypatch.setattr(worker, "_schedule_session_recovery", lambda _sid: None)

    result = asyncio.run(worker.send_session(
        target.id, "task", source="agent", source_session_id=caller.id))

    assert result["status"] == "queued"
    assert target.queue_pending[0]["source"] == "agent"
    assert target.queue_pending[0]["sourceSessionId"] == "caller"
    assert target.queue_pending[0]["type"] == "task"


def test_readonly_uses_source_session_id_and_not_source_type(monkeypatch):
    manager = _session("manager", managed=["child"])
    child = _session("child", managed_by="manager", readonly_session=True)
    before = list(child.queue_pending)

    assigned = asyncio.run(worker.assign(
        child.id, "task", source="agent", source_session_id=manager.id))
    sent = asyncio.run(worker.send_session(
        child.id, "message", source="agent", source_session_id=manager.id))
    notice = asyncio.run(worker.enqueue_notice(
        child.id, "notice", source="agent", source_session_id=manager.id))

    assert assigned["result"] == worker.READONLY_SESSION_ERROR
    assert sent["result"] == worker.READONLY_SESSION_ERROR
    assert notice["error"]["code"] == "readonly_session"
    assert child.queue_pending == before


@pytest.mark.parametrize("endpoint, body", [
    ("assign", {"sessionId": "target", "text": "x"}),
    ("task", {"sessionId": "target", "text": "x"}),
    ("send", {"sessionId": "target", "text": "x"}),
    ("notify", {"targetSessionId": "target", "text": "x"}),
])
def test_http_rejects_session_id_in_source_field(endpoint, body):
    _session("target")
    result = asyncio.run(getattr(server, f"api_{endpoint}")(
        {**body, "source": "caller"}))
    if endpoint == "assign":
        assert result["status"] == "error"
        assert "Unknown task source" in result["result"]
    else:
        assert "Unknown task source" in str(result)


def test_http_paths_forward_independent_fields(monkeypatch):
    _session("target")
    _session("caller")
    live = _worker("target")
    captured = {}

    async def fake_send_task(worker_id, text, source="agent", **kwargs):
        captured["task"] = (worker_id, text, source, kwargs)
        return None

    async def fake_send_session(session_id, text, source="agent", **kwargs):
        captured["send"] = (session_id, text, source, kwargs)
        return {"status": "queued", "sessionId": session_id}

    async def fake_notify(session_id, text, source="agent", **kwargs):
        captured["notify"] = (session_id, text, source, kwargs)
        return {"ok": True, "sessionId": session_id}

    async def fake_assign(session_id, text, source="agent", **kwargs):
        captured["assign"] = (session_id, text, source, kwargs)
        return {"status": "queued", "sessionId": session_id}

    monkeypatch.setattr(worker, "send_task", fake_send_task)
    monkeypatch.setattr(worker, "send_session", fake_send_session)
    monkeypatch.setattr(worker, "enqueue_notice", fake_notify)
    monkeypatch.setattr(worker, "assign", fake_assign)

    assert asyncio.run(server.api_task({
        "workerId": live.worker_id, "sessionId": "target", "text": "t",
        "source": "agent", "sourceSessionId": "caller"}))[
            "status"] == "queued"
    assert asyncio.run(server.api_send({
        "sessionId": "target", "text": "s", "source": "agent",
        "sourceSessionId": "caller"}))[
            "status"] == "queued"
    assert asyncio.run(server.api_notify({
        "targetSessionId": "target", "text": "n", "source": "agent",
        "sourceSessionId": "caller"}))[
            "ok"] is True
    assert asyncio.run(server.api_assign({
        "sessionId": "target", "text": "a", "source": "agent",
        "sourceSessionId": "caller"}))[
            "status"] == "queued"

    for key in ("task", "send", "notify", "assign"):
        assert captured[key][2] == "agent"
        assert captured[key][3]["source_session_id"] == "caller"


def test_unknown_source_session_is_rejected_without_spawning(monkeypatch):
    _session("target")
    spawned = AsyncMock()
    monkeypatch.setattr(worker, "create_worker", spawned)

    result = asyncio.run(server.api_assign({
        "sessionId": "target", "text": "x", "source": "agent",
        "sourceSessionId": "missing"}))

    assert result["status"] == "error"
    assert "Source session missing not found" in result["result"]
    spawned.assert_not_awaited()


def test_recovery_retains_source_session_id_metadata(monkeypatch):
    source = _session("source")
    target = _session("target")
    item = {
        "type": "task", "id": "task-1", "text": "recover me",
        "source": "agent", "sourceSessionId": source.id,
        "seq": 1, "taskId": "idempotent-1", "deliveryState": "queued",
    }
    target.queue_pending = [item]
    w = _worker(target.id)

    changed = worker._recover_pending_signals(w, target)

    assert changed is False
    assert target.queue_pending == [item]
    assert w.pending_signal.get_nowait() == {"type": "task_signal", "id": "task-1"}


def test_task_history_receipt_retains_source_metadata(monkeypatch):
    source = _session("source")
    target = _session("target")
    target.queue_pending = [{
        "type": "task", "id": "task-1", "text": "record me",
        "source": "agent", "sourceSessionId": source.id,
        "seq": 1, "taskId": None, "deliveryState": "queued",
    }]
    w = _worker(target.id)

    async def fake_stream(_worker, _text, _source, _session):
        return None

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)
    monkeypatch.setattr(worker, "_maybe_inject_memory",
                        AsyncMock(side_effect=lambda _s, text: text))

    async def run_consumer():
        task = asyncio.create_task(worker._consumer(w))
        await w.pending_signal.put({"type": "task_signal", "id": "task-1"})
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(run_consumer())

    assert target.queue_pending == []
    assert target.history[-1]["source"] == "agent"
    assert target.history[-1]["sourceSessionId"] == "source"


def test_assign_task_id_remains_idempotent_with_source_metadata(monkeypatch):
    _session("target")
    _session("caller")
    w = _worker("target")
    calls = []

    async def fake_send_task(worker_id, text, source="agent", **kwargs):
        calls.append((worker_id, text, source, kwargs))
        return None

    monkeypatch.setattr(worker, "_ensure_worker", AsyncMock(return_value=(w, None)))
    monkeypatch.setattr(worker, "send_task", fake_send_task)

    first = asyncio.run(worker.assign(
        "target", "once", source="agent", source_session_id="caller",
        task_id="same-task"))
    second = asyncio.run(worker.assign(
        "target", "once", source="agent", source_session_id="caller",
        task_id="same-task"))

    assert first["status"] == "queued"
    assert second == {"status": "pending", "taskId": "same-task"}
    assert len(calls) == 1
    assert calls[0][2] == "agent"
    assert calls[0][3]["source_session_id"] == "caller"
