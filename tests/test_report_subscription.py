"""Tests for report subscription pipeline (立项 4.3 订阅制).

- Session.report_subscriptions roundtrip (set, persisted as sorted list)
- report enqueue: managed session done → report appended to manager's
  queue_pending (only when the manager subscribed); unsubscribed / no
  managed_by → no append
- consumption concatenation: _consumer drains queue_pending reports into one
  batched message (separator + source); non-report items stay single
- HTTP endpoints report_subscribe / report_unsubscribe
- MCP tools report_subscribe / report_unsubscribe hit the right endpoints
"""

import asyncio
import sys
from pathlib import Path

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


def _cleanup():
    worker.workers.clear()
    worker._result_waiters.clear()
    worker._task_status.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid="ses_test", **kwargs):
    s = _sess.Session(id=sid, name="test", **kwargs)
    _sess._cache[sid] = s
    return s


def _noop_save(s):
    pass


async def _noop_save_async(s):
    pass


# ── Session field roundtrip ──

def test_report_subscriptions_roundtrip():
    s = _sess.Session(id="ses_mgr", name="mgr")
    s.report_subscriptions = {"ses_a", "ses_b"}
    d = s.to_dict()
    # persisted as JSON-safe sorted list
    assert d["report_subscriptions"] == ["ses_a", "ses_b"]
    # read back through _from_data (JSON gives list, not set)
    s2 = _sess.Session._from_data(dict(d))
    assert s2.report_subscriptions == {"ses_a", "ses_b"}


def test_report_subscriptions_default_empty():
    s = _sess.Session(id="ses_x", name="x")
    assert s.report_subscriptions == set()


def test_report_subscriptions_legacy_data_absent():
    """Old JSON without the field → default empty set."""
    data = {"id": "ses_x", "name": "x", "adapter_config": {}}
    s = _sess.Session._from_data(data)
    assert s.report_subscriptions == set()


# ── report enqueue ──

def test_enqueue_report_subscribed(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr")
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        await worker._enqueue_report("ses_child", "done", "the answer", "task-1", "worker-1")

    asyncio.run(scenario())

    assert len(mgr.queue_pending) == 1
    r = mgr.queue_pending[0]
    assert r == {
        "status": "done",
        "result": "the answer",
        "sessionId": "ses_child",
        "taskId": "task-1",
        "workerId": "worker-1",
    }
    _cleanup()


def test_enqueue_report_not_subscribed(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr")
    mgr = _setup_session("ses_mgr")  # no subscription

    async def scenario():
        await worker._enqueue_report("ses_child", "done", "x", "task-1", "worker-1")

    asyncio.run(scenario())

    assert mgr.queue_pending == [], "unsubscribed session must NOT enqueue"
    _cleanup()


def test_enqueue_report_no_managed_by(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    standalone = _setup_session("ses_standalone")

    async def scenario():
        await worker._enqueue_report("ses_standalone", "done", "x", "task-1", "worker-1")

    asyncio.run(scenario())

    assert standalone.queue_pending == [], "no managed_by → no enqueue"
    _cleanup()


def test_enqueue_report_wakes_manager_consumer(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr")
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}
    mw = worker.Worker(
        worker_id="worker-mgr", session_id="ses_mgr",
        adapter=CbcAdapter(), status="idle", process=None,
        pending_signal=asyncio.Queue(),
    )
    worker.workers["worker-mgr"] = mw

    async def scenario():
        await worker._enqueue_report("ses_child", "done", "r", "task-1", "worker-1")

    asyncio.run(scenario())

    item = asyncio.run(mw.pending_signal.get())
    assert item == {"type": "report_signal"}, f"got {item}"
    _cleanup()


def test_enqueue_report_error_status_also_enqueued(monkeypatch):
    """error 也算完成 → 照常入队，status 字段区分。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr")
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        await worker._enqueue_report("ses_child", "error", "boom", "task-2", "worker-1")

    asyncio.run(scenario())

    assert mgr.queue_pending[0]["status"] == "error"
    _cleanup()


# ── consumption concatenation ──

def test_format_report_batch():
    reports = [
        {"status": "done", "result": "r1", "sessionId": "ses_child", "taskId": "t1", "workerId": "worker-1"},
        {"status": "error", "result": "boom", "sessionId": "ses_child", "taskId": "t2", "workerId": "worker-1"},
    ]
    text = worker._format_report_batch(reports)
    assert "ses_child" in text          # source annotated
    assert '"result": "r1"' in text     # verbatim JSON
    assert worker._REPORT_SEP in text   # visible separator
    assert text.count(worker._REPORT_SEP) >= 2


def test_consumer_drains_reports_as_one_message(monkeypatch):
    """report_signal → queue_pending 全部积压拼成一条消息；消费即删。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    mgr = _setup_session("ses_mgr")
    mgr.queue_pending = [
        {"status": "done", "result": "r1", "sessionId": "ses_child", "taskId": "t1", "workerId": "worker-1"},
        {"status": "done", "result": "r2", "sessionId": "ses_child", "taskId": "t2", "workerId": "worker-1"},
    ]
    w = worker.Worker(
        worker_id="worker-mgr", session_id="ses_mgr",
        adapter=CbcAdapter(), status="idle", process=None,
        pending_signal=asyncio.Queue(),
    )
    worker.workers["worker-mgr"] = w
    received = []

    async def fake_stream(ww, text, source, s):
        received.append((text, source))

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "report_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert len(received) == 1, f"reports should merge into one message, got {len(received)}"
    text, source = received[0]
    assert source == "report"
    assert "r1" in text and "r2" in text
    assert mgr.queue_pending == [], "queue_pending should be drained after consumption"
    _cleanup()


def test_consumer_report_signal_no_pending_is_noop(monkeypatch):
    """信号到了但队列已空 → 不产生消息、不报错。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    mgr = _setup_session("ses_mgr")
    w = worker.Worker(
        worker_id="worker-mgr", session_id="ses_mgr",
        adapter=CbcAdapter(), status="idle", process=None,
        pending_signal=asyncio.Queue(),
    )
    worker.workers["worker-mgr"] = w
    received = []

    async def fake_stream(ww, text, source, s):
        received.append((text, source))

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "report_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert received == []
    _cleanup()


def test_consumer_non_report_single(monkeypatch):
    """非 report 消息保持单条处理（seq/taskId 透传）。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    mgr = _setup_session("ses_mgr")
    w = worker.Worker(
        worker_id="worker-mgr", session_id="ses_mgr",
        adapter=CbcAdapter(), status="idle", process=None,
        pending_signal=asyncio.Queue(),
    )
    worker.workers["worker-mgr"] = w
    received = []

    async def fake_stream(ww, text, source, s):
        received.append((text, source))

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"text": "hello", "source": "agent", "seq": 1, "taskId": None})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert received == [("hello", "agent")]
    assert w._current_seq == 1  # seq pairing preserved for handoff
    _cleanup()


# ── HTTP endpoints ──

def test_report_subscribe_endpoint(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    _setup_session("ses_child", managed_by="ses_mgr")
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_report_subscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert result.get("subscribed") is True
    assert mgr.report_subscriptions == {"ses_child"}

    # unsubscribe
    result = asyncio.run(srv.api_report_unsubscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert result.get("subscribed") is False
    assert mgr.report_subscriptions == set()
    _cleanup()


def test_report_subscribe_rejects_foreign_manager(monkeypatch):
    """session 已归属别的 manager → 拒绝订阅（越权防护）。"""
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    _setup_session("ses_child", managed_by="ses_owner")
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_report_subscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert "managed by" in result.get("error", ""), f"got {result}"
    assert mgr.report_subscriptions == set()
    _cleanup()


def test_report_subscribe_errors(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    _setup_session("ses_mgr")

    r = asyncio.run(srv.api_report_subscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_nope"}))
    assert "not found" in r.get("error", "")
    r = asyncio.run(srv.api_report_subscribe(
        {"managerId": "ses_nope", "sessionId": "ses_mgr"}))
    assert "not found" in r.get("error", "")
    r = asyncio.run(srv.api_report_subscribe({}))
    assert "required" in r.get("error", "")
    _cleanup()


# ── MCP tools ──

def test_mcp_report_subscribe(monkeypatch):
    import packages.mcp.server as mcp_server
    captured = {}

    def fake_api(method, path, body=None, timeout=30.0):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"subscribed": True}

    monkeypatch.setattr(mcp_server, "_api", fake_api)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_mgr")
    result = mcp_server.report_subscribe("ses_child")
    assert captured["path"] == "/api/report-subscribe"
    assert captured["body"] == {"managerId": "ses_mgr", "sessionId": "ses_child"}
    assert result["subscribed"] is True


def test_mcp_report_unsubscribe(monkeypatch):
    import packages.mcp.server as mcp_server
    captured = {}

    def fake_api(method, path, body=None, timeout=30.0):
        captured["path"] = path
        captured["body"] = body
        return {"subscribed": False}

    monkeypatch.setattr(mcp_server, "_api", fake_api)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_mgr")
    result = mcp_server.report_unsubscribe("ses_child")
    assert captured["path"] == "/api/report-unsubscribe"
    assert captured["body"] == {"managerId": "ses_mgr", "sessionId": "ses_child"}
    assert result["subscribed"] is False


def test_mcp_report_tools_require_identity(monkeypatch):
    import packages.mcp.server as mcp_server
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    r1 = mcp_server.report_subscribe("ses_child")
    r2 = mcp_server.report_unsubscribe("ses_child")
    assert r1.get("ok") is False
    assert r2.get("ok") is False
    assert "PAN_AGENT_SESSION_ID" in r1.get("error", {}).get("message", "")


if __name__ == "__main__":
    test_report_subscriptions_roundtrip()
    test_report_subscriptions_default_empty()
    test_report_subscriptions_legacy_data_absent()
    test_enqueue_report_subscribed()
    test_enqueue_report_not_subscribed()
    test_enqueue_report_no_managed_by()
    test_enqueue_report_wakes_manager_consumer()
    test_enqueue_report_error_status_also_enqueued()
    test_format_report_batch()
    test_consumer_drains_reports_as_one_message()
    test_consumer_report_signal_no_pending_is_noop()
    test_consumer_non_report_single()
    test_report_subscribe_endpoint()
    test_report_subscribe_rejects_foreign_manager()
    test_report_subscribe_errors()
    test_mcp_report_subscribe()
    test_mcp_report_unsubscribe()
    test_mcp_report_tools_require_identity()
    print("\n=== ALL REPORT SUBSCRIPTION TESTS PASSED ===")
