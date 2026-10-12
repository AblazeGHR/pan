"""Tests for explicit manual task reports (POST /api/sessions/{id}/manual-report).

Covers the delivery semantics that make a browser-clicked report safe:

- content/target reuse: the manual item mirrors the automatic report item
  (same queue target, same shape, same content source = durable last_result)
- subscription independence: delivered after report_unsubscribe, and the
  subscription is never modified (no temporary subscribe either)
- identity binding: taskId must match the durable terminal; a moved terminal
  is a definite failure (task_changed) and nothing is sent
- no manager / no terminal result / unknown session → definite failures
- idempotency: retries of the same (session, task, message) deliver exactly
  once — including after the queue item was consumed (receipt registry)
- save failure rolls the attempt back (definite failure; a retry stays fresh)
- concurrent duplicate clicks deliver once (queue lock)
- automatic report behavior is untouched by this feature
"""

import asyncio
import sys
from pathlib import Path

import pytest

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


@pytest.fixture(autouse=True)
def _no_real_recovery_spawn(monkeypatch):
    """Same guard as test_report_subscription: the wake path would otherwise
    schedule a real cbc spawn whose cancellation deadlocked pytest on Windows."""
    spawned = []

    async def _no_spawn(session_id):
        spawned.append(session_id)
        return "spawn suppressed by test fixture"

    monkeypatch.setattr(worker, "create_worker", _no_spawn)
    yield spawned


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid="ses_test", **kwargs):
    s = _sess.Session(id=sid, name="test", **kwargs)
    _sess._cache[sid] = s
    return s


def _terminal(task_id="task-1", status="done", result="the answer", worker_id="worker-1"):
    return {
        "status": status,
        "result": result,
        "taskId": task_id,
        "workerId": worker_id,
        "resultCursor": 7,
        "terminalKey": f"terminal:{task_id}",
    }


async def _noop_save_async(s):
    pass


def _noop_save(s):
    pass


# ── delivery semantics ──

def test_manual_report_delivers_to_manager(monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        return await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")

    result, error = asyncio.run(scenario())

    assert error is None, f"unexpected error: {error}"
    assert result["managerId"] == "ses_mgr"
    assert result["alreadyReported"] is False
    assert len(mgr.queue_pending) == 1
    item = mgr.queue_pending[0]
    # Same routing target and item shape as the automatic report.
    assert item["type"] == "report"
    assert item["kind"] == "report"
    assert item["source"] == "report"
    assert item["sourceSessionId"] == "ses_child"
    assert item["sessionId"] == "ses_child"
    assert item["deliveryState"] == "queued"
    assert item["queueItemId"] == item["id"]
    # Content semantics = the durable terminal the automatic report would send.
    assert item["status"] == "done"
    assert item["result"] == "the answer"
    assert item["taskId"] == "task-1"
    assert item["workerId"] == "worker-1"
    # Manual markers for audit/dedup.
    assert item["manual"] is True
    assert item["messageId"] == "pan:msg1"
    assert item["manualReportKey"] == worker.manual_report_key("ses_child", "task-1", "pan:msg1")
    assert _no_real_recovery_spawn == ["ses_mgr"], \
        "manual report must wake the manager through the same recovery path"
    _cleanup()


def test_manual_report_works_after_unsubscribe_without_touching_subscription(monkeypatch):
    """report_unsubscribe only stops automatic pushes; an explicit report is
    still delivered and the subscription set is neither read nor modified."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")  # not subscribed

    async def scenario():
        return await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")

    result, error = asyncio.run(scenario())

    assert error is None, f"unexpected error: {error}"
    assert len(mgr.queue_pending) == 1
    assert mgr.report_subscriptions == set(), \
        "manual report must not modify the subscription"
    _cleanup()


def test_manual_report_keeps_existing_subscription(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")

    asyncio.run(scenario())
    assert mgr.report_subscriptions == {"ses_child"}
    _cleanup()


def test_manual_report_error_status_terminal(monkeypatch):
    """An errored task is still a completion: the same status is reported."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr",
                   last_result=_terminal(task_id="task-9", status="error", result="boom"))
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await worker.enqueue_manual_report("ses_child", "task-9", "pan:msg9")

    result, error = asyncio.run(scenario())
    assert error is None
    assert mgr.queue_pending[0]["status"] == "error"
    assert mgr.queue_pending[0]["result"] == "boom"
    _cleanup()


# ── identity binding / definite failures ──

def test_manual_report_task_mismatch_is_definite_failure(monkeypatch):
    """A newer terminal (different task) invalidates the captured identity."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal(task_id="task-2"))
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "task_changed"
    assert error["retryable"] is False
    assert mgr.queue_pending == [], "nothing may be sent on a task mismatch"
    assert mgr.manual_report_receipts == {}
    _cleanup()


def test_manual_report_null_task_matches_plain_turn(monkeypatch):
    """A plain (non-assign) turn has taskId None on both sides and reports."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr",
                   last_result=_terminal(task_id=None, result="plain answer"))
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await worker.enqueue_manual_report("ses_child", None, "pan:msg1")

    result, error = asyncio.run(scenario())
    assert error is None, f"unexpected error: {error}"
    assert mgr.queue_pending[0]["taskId"] is None
    _cleanup()


def test_manual_report_no_manager_is_definite_failure(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_standalone", last_result=_terminal())

    async def scenario():
        return await worker.enqueue_manual_report("ses_standalone", "task-1", "pan:msg1")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "no_report_target"
    assert error["retryable"] is False
    _cleanup()


def test_manual_report_missing_manager_session(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_ghost", last_result=_terminal())

    async def scenario():
        return await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "no_report_target"
    _cleanup()


def test_manual_report_without_terminal_result(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr")  # no last_result
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "no_task_result"
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_session_not_found(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    async def scenario():
        return await worker.enqueue_manual_report("ses_nope", "task-1", "pan:msg1")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "session_not_found"
    _cleanup()


# ── idempotency (no double-send on retry) ──

def test_manual_report_retry_delivers_once(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")
        second = await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")
        return first, second

    first, second = asyncio.run(scenario())
    assert len(mgr.queue_pending) == 1, "a retry must not enqueue a second report"
    assert first[1] is None and second[1] is None
    assert first[0]["alreadyReported"] is False
    assert second[0]["alreadyReported"] is True
    assert second[0]["queueItemId"] == first[0]["queueItemId"]
    _cleanup()


def test_manual_report_retry_after_consumption_delivers_once(monkeypatch):
    """Once the manager consumed the queue item, the durable receipt still
    proves delivery: a late retry after an unconfirmed response cannot
    double-send."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")
        # Simulate the manager's consumer draining the batch.
        mgr.queue_pending.clear()
        second = await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")
        return first, second

    first, second = asyncio.run(scenario())
    assert len(mgr.queue_pending) == 0
    assert second[0]["alreadyReported"] is True
    assert second[0]["queueItemId"] == first[0]["queueItemId"]
    receipt = mgr.manual_report_receipts[
        worker.manual_report_key("ses_child", "task-1", "pan:msg1")]
    assert receipt["messageId"] == "pan:msg1"
    assert receipt["taskId"] == "task-1"
    _cleanup()


def test_manual_report_concurrent_clicks_deliver_once(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await asyncio.gather(
            worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1"),
            worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1"),
            worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1"),
        )

    outcomes = asyncio.run(scenario())
    assert len(mgr.queue_pending) == 1, f"concurrent clicks double-sent: {mgr.queue_pending}"
    assert sum(1 for _, error in outcomes if error is None) == 3
    assert sum(1 for result, _ in outcomes if result and result["alreadyReported"]) == 2
    _cleanup()


def test_manual_report_distinct_messages_deliver_separately(monkeypatch):
    """Different message identities are different reports (no over-dedup)."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")

    async def scenario():
        await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")
        await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg2")

    asyncio.run(scenario())
    assert len(mgr.queue_pending) == 2
    _cleanup()


# ── durability / rollback ──

def test_manual_report_save_failure_rolls_back(monkeypatch):
    _cleanup()

    async def _failing_save(s):
        raise OSError("disk full")

    monkeypatch.setattr(_sess, "save_async", _failing_save)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")
    old_revision = mgr.queue_revision

    async def scenario():
        return await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "persist_failed"
    assert error["retryable"] is True
    assert mgr.queue_pending == [], "failed attempt must not stay queued"
    assert mgr.manual_report_receipts == {}
    assert mgr.queue_revision == old_revision
    assert all(
        record.get("manualReportKey") is None
        for record in mgr.queue_delivery_ledger.values()
    ), "failed attempt must not leave a durable receipt behind"

    # A retry after the definite failure is a fresh first attempt.
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    async def retry():
        return await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")

    result2, error2 = asyncio.run(retry())
    assert error2 is None
    assert result2["alreadyReported"] is False
    assert len(mgr.queue_pending) == 1
    _cleanup()


def test_manual_report_receipts_bounded(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")

    async def scenario():
        for index in range(_sess.MANUAL_REPORT_RECEIPT_MAX + 5):
            await worker.enqueue_manual_report("ses_child", "task-1", f"pan:msg{index}")

    asyncio.run(scenario())
    assert len(mgr.manual_report_receipts) == _sess.MANUAL_REPORT_RECEIPT_MAX
    _cleanup()


# ── wake path ──

def test_manual_report_wakes_manager_consumer(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")
    mw = worker.Worker(
        worker_id="worker-mgr", session_id="ses_mgr",
        adapter=CbcAdapter(), status="idle", process=None,
        pending_signal=asyncio.Queue(),
    )
    worker.workers["worker-mgr"] = mw

    async def scenario():
        await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")

    asyncio.run(scenario())

    item = asyncio.run(mw.pending_signal.get())
    assert item == {"type": "queue_signal"}, f"got {item}"
    _cleanup()


# ── automatic report regression (unchanged behavior) ──

def test_auto_report_still_requires_subscription(monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr")
    mgr = _setup_session("ses_mgr")  # no subscription

    async def scenario():
        await worker._enqueue_report("ses_child", "done", "x", "task-1", "worker-1")

    asyncio.run(scenario())
    assert mgr.queue_pending == [], "automatic report must still honor the subscription"
    _cleanup()


def test_auto_report_still_delivers_when_subscribed(monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr")
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        await worker._enqueue_report("ses_child", "done", "x", "task-1", "worker-1")

    asyncio.run(scenario())
    assert len(mgr.queue_pending) == 1
    assert mgr.queue_pending[0].get("manual") is None, \
        "automatic reports must not carry the manual marker"
    _cleanup()


def test_manual_and_auto_reports_coexist(monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        await worker._enqueue_report("ses_child", "done", "auto", "task-0", "worker-0")
        await worker.enqueue_manual_report("ses_child", "task-1", "pan:msg1")

    asyncio.run(scenario())
    assert len(mgr.queue_pending) == 2
    assert mgr.queue_pending[0].get("manual") is None
    assert mgr.queue_pending[1]["manual"] is True
    _cleanup()


# ── Session field roundtrip ──

def test_manual_report_receipts_roundtrip():
    mgr = _sess.Session(id="ses_mgr", name="mgr")
    mgr.manual_report_receipts = {"manual:a:b:c": {"queueItemId": "q1"}}
    data = mgr.to_dict()
    assert data["manual_report_receipts"] == {"manual:a:b:c": {"queueItemId": "q1"}}
    restored = _sess.Session._from_data(dict(data))
    assert restored.manual_report_receipts == {"manual:a:b:c": {"queueItemId": "q1"}}


def test_manual_report_receipts_default_and_legacy():
    fresh = _sess.Session(id="ses_x", name="x")
    assert fresh.manual_report_receipts == {}
    legacy = _sess.Session._from_data({"id": "ses_x", "name": "x", "adapter_config": {}})
    assert legacy.manual_report_receipts == {}
    broken = _sess.Session._from_data({
        "id": "ses_x", "name": "x", "adapter_config": {},
        "manual_report_receipts": {"ok": {"queueItemId": "q"}, "bad": "not-a-dict", "": {}},
    })
    assert broken.manual_report_receipts == {"ok": {"queueItemId": "q"}}


# ── HTTP endpoint ──

def test_manual_report_endpoint_ok(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "messageId": "pan:msg1"}))
    assert result.get("ok") is True
    assert result.get("managerId") == "ses_mgr"
    assert result.get("alreadyReported") is False
    assert isinstance(result.get("queueItemId"), str)
    assert len(mgr.queue_pending) == 1

    # Idempotent replay through the endpoint.
    replay = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "messageId": "pan:msg1"}))
    assert replay.get("ok") is True
    assert replay.get("alreadyReported") is True
    assert len(mgr.queue_pending) == 1
    _cleanup()


def test_manual_report_endpoint_after_unsubscribe(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")

    subscribed = asyncio.run(srv.api_report_subscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert subscribed.get("subscribed") is True
    unsubscribed = asyncio.run(srv.api_report_unsubscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert unsubscribed.get("subscribed") is False

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "messageId": "pan:msg1"}))
    assert result.get("ok") is True, f"manual report must work after unsubscribe: {result}"
    assert len(mgr.queue_pending) == 1
    assert mgr.report_subscriptions == set(), "unsubscribe state must be unchanged"
    _cleanup()


def test_manual_report_endpoint_task_changed(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal(task_id="task-2"))
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "messageId": "pan:msg1"}))
    assert result.get("ok") is False
    assert result["error"]["code"] == "task_changed"
    assert result.get("retryable") is False
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_endpoint_rejects_bad_bodies(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")

    for body in (
        {},
        {"taskId": "task-1"},
        {"messageId": "pan:msg1"},
        {"taskId": "task-1", "messageId": ""},
        {"taskId": "task-1", "messageId": 42},
        {"taskId": 7, "messageId": "pan:msg1"},
        {"taskId": "task-1", "messageId": "x" * 201},
    ):
        result = asyncio.run(srv.api_session_manual_report("ses_child", body))
        assert result.get("ok") is False, f"body {body!r} must be rejected"
        assert "error" in result
    assert mgr.queue_pending == [], "rejected bodies must not deliver"
    _cleanup()


def test_manual_report_endpoint_session_not_found(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    result = asyncio.run(srv.api_session_manual_report(
        "ses_nope", {"taskId": "task-1", "messageId": "pan:msg1"}))
    assert result.get("ok") is False
    assert result["error"]["code"] == "session_not_found"
    _cleanup()


# ── list projection: terminal identity without the result body ──

def test_session_summary_exposes_terminal_identity_only(monkeypatch):
    """The list read must let the tail-block action bind the latest task on a
    cold load, while the (potentially large) result body stays out of it."""
    import packages.web.server as srv
    _cleanup()
    ta = _setup_session("ses_ta", managed_by="ses_mgr", last_result=_terminal())
    summary = srv._session_summary(ta, {})
    assert summary["lastResult"] == {
        "status": "done",
        "taskId": "task-1",
        "taskSeq": None,
        "workerId": "worker-1",
        "terminalKey": "terminal:task-1",
        "resultCursor": 7,
        "timestamp": None,
    }
    assert "result" not in summary["lastResult"], \
        "the list projection must not carry the result body"
    _cleanup()


def test_session_summary_terminal_identity_null_without_terminal():
    import packages.web.server as srv
    _cleanup()
    bare = _setup_session("ses_bare")
    assert srv._session_summary(bare, {})["lastResult"] is None
    _cleanup()


def test_session_summary_identity_matches_binding_task(monkeypatch):
    """The identity the list exposes is exactly what the report route accepts."""
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    ta = _setup_session("ses_ta", managed_by="ses_mgr", last_result=_terminal())
    _setup_session("ses_mgr")
    summary = srv._session_summary(ta, {})
    result = asyncio.run(srv.api_session_manual_report(
        "ses_ta",
        {"taskId": summary["lastResult"]["taskId"], "messageId": "pan:msg1"},
    ))
    assert result.get("ok") is True, f"summary identity must be accepted: {result}"
    _cleanup()


# ── real link: real persistence + real ASGI route (no save stubs) ──

def test_manual_report_dedup_survives_cold_reload(monkeypatch, _no_real_recovery_spawn):
    """Real durable write into the isolated data root, then a cold reload.

    This is the end-to-end persistence proof the delivery semantics depend on:
    the queue row and the dedup receipt are on disk, and a retry issued after
    every in-memory Session was dropped is still deduplicated — a lost response
    can never become a second report.
    """
    import json
    import packages.web.server as srv
    _cleanup()
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    _setup_session("ses_mgr")

    async def scenario():
        await _sess.save_async(_sess.get("ses_child"))
        await _sess.save_async(_sess.get("ses_mgr"))
        first = await srv.api_session_manual_report(
            "ses_child", {"taskId": "task-1", "messageId": "pan:msg1"})
        on_disk = json.loads(
            (_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
        # Cold load: drop every in-memory Session and read the manager from disk.
        _sess._cache.clear()
        reloaded = _sess.get("ses_mgr")
        second = await srv.api_session_manual_report(
            "ses_child", {"taskId": "task-1", "messageId": "pan:msg1"})
        return first, on_disk, reloaded, second

    first, on_disk, reloaded, second = asyncio.run(scenario())

    assert first.get("ok") is True and first.get("alreadyReported") is False
    # Durable evidence: one report row and its dedup receipt are persisted.
    persisted_rows = [item for item in on_disk["queue_pending"]
                      if item.get("manualReportKey")]
    assert len(persisted_rows) == 1, f"durable queue row missing: {on_disk['queue_pending']}"
    assert persisted_rows[0]["result"] == "the answer"
    assert persisted_rows[0]["taskId"] == "task-1"
    assert persisted_rows[0]["messageId"] == "pan:msg1"
    assert any(key in on_disk["manual_report_receipts"]
               for key in [worker.manual_report_key("ses_child", "task-1", "pan:msg1")]), \
        f"dedup receipt missing on disk: {on_disk['manual_report_receipts']}"

    # The receipt survives the cold reload and still dedups the retry.
    assert reloaded is not None
    assert worker.manual_report_key("ses_child", "task-1", "pan:msg1") in reloaded.manual_report_receipts
    assert second.get("ok") is True and second.get("alreadyReported") is True
    manual_rows = [item for item in reloaded.queue_pending if item.get("manualReportKey")]
    assert len(manual_rows) == 1, "a retry after a cold reload must not double-deliver"
    _cleanup()


def test_manual_report_route_through_asgi(monkeypatch, _no_real_recovery_spawn):
    """The real HTTP route (request parsing + JSON response), not just the
    handler function, through an ASGI app without the production lifespan."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import packages.web.server as server
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr", last_result=_terminal())
    mgr = _setup_session("ses_mgr")

    app = FastAPI()
    app.add_api_route("/api/sessions/{session_id}/manual-report",
                      server.api_session_manual_report, methods=["POST"])
    with TestClient(app) as client:
        response = client.post("/api/sessions/ses_child/manual-report",
                               json={"taskId": "task-1", "messageId": "pan:msg1"})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["managerId"] == "ses_mgr"
        assert body["alreadyReported"] is False
        assert isinstance(body["queueItemId"], str)

        replay = client.post("/api/sessions/ses_child/manual-report",
                             json={"taskId": "task-1", "messageId": "pan:msg1"})
        assert replay.status_code == 200
        assert replay.json()["alreadyReported"] is True
        assert len(mgr.queue_pending) == 1

        invalid = client.post("/api/sessions/ses_child/manual-report",
                              json={"messageId": "pan:msg1"})
        assert invalid.status_code == 200
        assert invalid.json()["ok"] is False

        missing = client.post("/api/sessions/ses_ghost/manual-report",
                              json={"taskId": "task-1", "messageId": "pan:msg1"})
        assert missing.status_code == 200
        assert missing.json()["error"]["code"] == "session_not_found"
    _cleanup()


if __name__ == "__main__":
    test_manual_report_delivers_to_manager()
    test_manual_report_works_after_unsubscribe_without_touching_subscription()
    test_manual_report_keeps_existing_subscription()
    test_manual_report_error_status_terminal()
    test_manual_report_task_mismatch_is_definite_failure()
    test_manual_report_null_task_matches_plain_turn()
    test_manual_report_no_manager_is_definite_failure()
    test_manual_report_missing_manager_session()
    test_manual_report_without_terminal_result()
    test_manual_report_session_not_found()
    test_manual_report_retry_delivers_once()
    test_manual_report_retry_after_consumption_delivers_once()
    test_manual_report_concurrent_clicks_delivers_once()
    test_manual_report_distinct_messages_deliver_separately()
    test_manual_report_save_failure_rolls_back()
    test_manual_report_receipts_bounded()
    test_manual_report_wakes_manager_consumer()
    test_auto_report_still_requires_subscription()
    test_auto_report_still_delivers_when_subscribed()
    test_manual_and_auto_reports_coexist()
    test_manual_report_receipts_roundtrip()
    test_manual_report_receipts_default_and_legacy()
    test_manual_report_endpoint_ok()
    test_manual_report_endpoint_after_unsubscribe()
    test_manual_report_endpoint_task_changed()
    test_manual_report_endpoint_rejects_bad_bodies()
    test_manual_report_endpoint_session_not_found()
    test_session_summary_exposes_terminal_identity_only()
    test_session_summary_terminal_identity_null_without_terminal()
    test_session_summary_identity_matches_binding_task()
    test_manual_report_dedup_survives_cold_reload()
    test_manual_report_route_through_asgi()
    print("\n=== ALL MANUAL TASK REPORT TESTS PASSED ===")
