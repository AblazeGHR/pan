"""Regression coverage for source type vs source Session ID metadata.

source 契约（方案 B，2026-09-02 确认）：
- source 是开放但受校验的来源元数据，合法集合集中维护在 worker.SOURCE_TYPES。
- HTTP handler 在投递前校验客户端传入的 source（_request_source_metadata）：
  非法类型 / 未知字符串按既有格式拒绝（"Unknown task source: ..."）；缺省
  取 agent；合法 source（含既有 user/agent/system_prompt/report 与已规划的
  meta-agent/automation）原样保留透传——绝不无条件覆盖为 agent。
- source 只是来源元数据，不是身份或权限凭证：身份/授权继续由 sourceSessionId
  + managed/readonly 校验承担（_source_access_error），本文件不改变该语义。
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CbcAdapter
from packages.web import server


# endpoint name → body；target 为 session "target"（调用前需 _session("target")）。
HTTP_ENDPOINT_BODIES = {
    "assign": {"sessionId": "target", "text": "x"},
    "task": {"sessionId": "target", "text": "x"},
    "send": {"sessionId": "target", "text": "x"},
    "notify": {"targetSessionId": "target", "text": "x"},
}


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


def _task_envelope(sid, text, **extra):
    """一个符合当前统一队列契约的 task 行（_persist_task_item 形状）。"""
    item = {
        "type": "task", "kind": "task", "id": sid, "queueItemId": sid,
        "text": text, "source": "agent", "seq": 1, "taskId": None,
        "deliveryState": "queued", "dispatchState": "queued",
        "revision": 1, "createdAt": time.time(),
    }
    item.update(extra)
    return item


# ── worker 层：合法集合与规范化 ──


def test_source_types_remain_open_but_validated():
    # 既有合法来源全部保留
    for source in ("user", "agent", "system_prompt", "report"):
        assert worker._normalize_source_type(source) == (source, None), source
    # 已规划的未来外部来源纳入白名单
    assert worker._normalize_source_type("meta-agent") == ("meta-agent", None)
    assert worker._normalize_source_type("automation") == ("automation", None)
    # 缺省 agent 语义不变
    assert worker._normalize_source_type(None) == ("agent", None)
    assert worker._normalize_source_type("") == ("agent", None)


def test_source_type_rejects_unknown_strings_and_illegal_types():
    for bad in ("caller", "ses_123", "meta", "bot", 42, ["agent"], {"s": "agent"}):
        normalized, error = worker._normalize_source_type(bad)
        assert normalized is None, bad
        assert isinstance(error, str) and error.startswith("Unknown task source"), bad


# ── worker 层：source 元数据持久化 / readonly ──


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


def test_send_session_accepts_future_external_sources(monkeypatch):
    """未来 external meta-agent / automation source 在 worker 层同样合法。"""
    target = _session("target")
    caller = _session("caller")
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    monkeypatch.setattr(worker, "_schedule_session_recovery", lambda _sid: None)

    result = asyncio.run(worker.send_session(
        target.id, "task", source="meta-agent", source_session_id=caller.id))

    assert result["status"] == "queued"
    assert target.queue_pending[0]["source"] == "meta-agent"
    assert target.queue_pending[0]["sourceSessionId"] == "caller"


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


# ── HTTP 层：非法 source 在投递前拒绝（不 spawn） ──


@pytest.mark.parametrize("endpoint", ["assign", "task", "send", "notify"])
@pytest.mark.parametrize("bad_source", [
    "caller",          # session id 被误填到 source 字段
    "ses_unknown",     # 未知字符串
    "random-label",    # 未登记的未来来源
    42,                # 非法类型：非字符串
    ["agent"],         # 非法类型：list
    {"s": "agent"},    # 非法类型：dict
])
def test_http_rejects_unknown_or_illegal_source_without_spawning(
        monkeypatch, endpoint, bad_source):
    """未知/非法 source 按既有错误格式拒绝，且不触发真实 CLI spawn。"""
    _session("target")
    spawned = AsyncMock()
    monkeypatch.setattr(worker, "create_worker", spawned)

    result = asyncio.run(getattr(server, f"api_{endpoint}")(
        {**HTTP_ENDPOINT_BODIES[endpoint], "source": bad_source}))

    if endpoint == "assign":
        assert result["status"] == "error"
        assert "Unknown task source" in result["result"]
    else:
        assert "Unknown task source" in str(result)
    spawned.assert_not_awaited()


@pytest.mark.parametrize("endpoint, body", [
    ("assign", {"sessionId": "target", "text": "x"}),
    ("task", {"sessionId": "target", "text": "x"}),
    ("send", {"sessionId": "target", "text": "x"}),
    ("notify", {"targetSessionId": "target", "text": "x"}),
])
def test_http_rejects_session_id_in_source_field(monkeypatch, endpoint, body):
    """回归：session id 误用作 source 时必须报 Unknown task source。"""
    _session("target")
    spawned = AsyncMock()
    monkeypatch.setattr(worker, "create_worker", spawned)
    result = asyncio.run(getattr(server, f"api_{endpoint}")(
        {**body, "source": "caller"}))
    if endpoint == "assign":
        assert result["status"] == "error"
        assert "Unknown task source" in result["result"]
    else:
        assert "Unknown task source" in str(result)
    spawned.assert_not_awaited()


# ── HTTP 层：合法 source 校验后保留透传（不覆盖、不 spawn 真实 CLI） ──


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


@pytest.mark.parametrize("source", ["meta-agent", "automation"])
def test_http_paths_forward_planned_external_sources(monkeypatch, source):
    """已规划的 meta-agent / automation source 校验通过并原样透传。"""
    _session("target")
    _session("caller")
    _worker("target")
    captured = {}

    async def fake_send_task(worker_id, text, source="agent", **kwargs):
        captured["task"] = (source, kwargs)
        return None

    async def fake_send_session(session_id, text, source="agent", **kwargs):
        captured["send"] = (source, kwargs)
        return {"status": "queued", "sessionId": session_id}

    async def fake_notify(session_id, text, source="agent", **kwargs):
        captured["notify"] = (source, kwargs)
        return {"ok": True, "sessionId": session_id}

    async def fake_assign(session_id, text, source="agent", **kwargs):
        captured["assign"] = (source, kwargs)
        return {"status": "queued", "sessionId": session_id}

    monkeypatch.setattr(worker, "send_task", fake_send_task)
    monkeypatch.setattr(worker, "send_session", fake_send_session)
    monkeypatch.setattr(worker, "enqueue_notice", fake_notify)
    monkeypatch.setattr(worker, "assign", fake_assign)

    assert asyncio.run(server.api_task({
        "workerId": "worker-1", "sessionId": "target", "text": "t",
        "source": source, "sourceSessionId": "caller"}))[
            "status"] == "queued"
    assert asyncio.run(server.api_send({
        "sessionId": "target", "text": "s", "source": source,
        "sourceSessionId": "caller"}))[
            "status"] == "queued"
    assert asyncio.run(server.api_notify({
        "targetSessionId": "target", "text": "n", "source": source,
        "sourceSessionId": "caller"}))[
            "ok"] is True
    assert asyncio.run(server.api_assign({
        "sessionId": "target", "text": "a", "source": source,
        "sourceSessionId": "caller"}))[
            "status"] == "queued"

    for key in ("task", "send", "notify", "assign"):
        assert captured[key][0] == source
        assert captured[key][1]["source_session_id"] == "caller"


def test_http_paths_default_to_agent_when_source_omitted(monkeypatch):
    """source 缺省仍走 agent（与覆盖前行为一致），合法 agent 保留透传。"""
    _session("target")
    _session("caller")
    _worker("target")
    captured = {}

    async def fake_send_task(worker_id, text, source="agent", **kwargs):
        captured["source"] = source
        return None

    monkeypatch.setattr(worker, "send_task", fake_send_task)

    assert asyncio.run(server.api_task({
        "workerId": "worker-1", "sessionId": "target", "text": "t",
        "sourceSessionId": "caller"}))["status"] == "queued"
    assert captured["source"] == "agent"


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


# ── 持久化 / 恢复链路保留 source 元数据 ──


def test_recovery_retains_source_session_id_metadata(monkeypatch):
    source = _session("source")
    target = _session("target")
    item = _task_envelope(
        "task-1", "recover me", source="agent", sourceSessionId=source.id,
        taskId="idempotent-1")
    target.queue_pending = [item]
    w = _worker(target.id)

    changed = worker._recover_pending_signals(w, target)

    assert changed is False
    assert target.queue_pending == [item]
    assert w.pending_signal.get_nowait() == {"type": "queue_signal"}


def test_task_history_receipt_retains_source_metadata(monkeypatch):
    source = _session("source")
    target = _session("target")
    target.queue_pending = [_task_envelope(
        "task-1", "record me", source="agent", sourceSessionId=source.id)]
    w = _worker(target.id)

    async def fake_stream(_worker, _text, _source, _session):
        return None

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)
    monkeypatch.setattr(worker, "_maybe_inject_memory",
                        AsyncMock(side_effect=lambda _s, text: text))

    async def run_consumer():
        task = asyncio.create_task(worker._consumer(w))
        await w.pending_signal.put({"type": "task_signal", "id": "task-1"})
        await w.pending_signal.put(None)
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
