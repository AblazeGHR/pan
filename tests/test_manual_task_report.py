"""Tests for explicit per-task manual reports (POST /api/sessions/{id}/manual-report).

Covers the delivery semantics that make a browser-clicked per-task report safe:

- content/target reuse: the manual item mirrors the automatic report item
  (same queue target, same shape, content resolved from that task's durable
  terminal row — not from "whatever terminal happens to be newest")
- any completed task in the bounded durable index stays reportable, including
  older tasks after newer ones finish or while a newer one is still running
- a task without a durable terminal row is a definite failure (never a
  substitution with another task's result)
- subscription independence: delivered after report_unsubscribe, and the
  subscription is never modified (no temporary subscribe either)
- idempotency: retries of the same (session, task, block) deliver once, and
  distinct tasks never dedupe each other
- save failure rolls the attempt back (definite failure; a retry stays fresh)
- concurrent duplicate clicks deliver once (queue lock)
- automatic report behavior is untouched by this feature
- the list projection exposes bounded terminal identities (no result bodies)
- real durable write + cold reload keeps per-task dedup
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


def _terminal(task_id="task-1", status="done", result="the answer",
              worker_id="worker-1", task_seq=1, result_cursor=1, terminal_key=None):
    return {
        "status": status,
        "result": result,
        "taskId": task_id,
        "taskSeq": task_seq,
        "workerId": worker_id,
        "resultCursor": result_cursor,
        "terminalKey": terminal_key or f"terminal:{task_id}:{task_seq}",
    }


def _child(**kwargs):
    """A managed Session whose durable per-task index holds the given terminals."""
    terminals = kwargs.pop("terminals", [_terminal()])
    last = kwargs.pop("last_result", terminals[-1] if terminals else None)
    return _setup_session("ses_child", managed_by="ses_mgr",
                          terminal_results=terminals, last_result=last, **kwargs)


async def _noop_save_async(s):
    pass


def _noop_save(s):
    pass


def _report(task_id="task-1", task_seq=1, message_id="pan:msg1"):
    """Call the real delivery function the HTTP route calls."""
    return worker.enqueue_manual_report("ses_child", task_id, task_seq, message_id)


# ── delivery semantics ──

def test_manual_report_delivers_to_manager(monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        return await _report()

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
    # Content semantics = that task's durable terminal row.
    assert item["status"] == "done"
    assert item["result"] == "the answer"
    assert item["taskId"] == "task-1"
    assert item["taskSeq"] == 1
    assert item["workerId"] == "worker-1"
    # Manual markers for audit/dedup.
    assert item["manual"] is True
    assert item["messageId"] == "pan:msg1"
    assert item["manualReportKey"] == worker.manual_report_key(
        "ses_child", "task-1", 1, "pan:msg1")
    assert _no_real_recovery_spawn == ["ses_mgr"], \
        "manual report must wake the manager through the same recovery path"
    _cleanup()


def test_manual_report_reports_each_historical_task_separately(monkeypatch):
    """Every completed task in the durable index is reportable, not just the
    newest one — and each report carries its own task's result."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child(terminals=[
        _terminal(task_id="task-a", result="answer A", task_seq=1, result_cursor=1),
        _terminal(task_id="task-b", result="answer B", task_seq=2, result_cursor=2),
        _terminal(task_id="task-c", result="answer C", task_seq=3, result_cursor=3),
    ])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await _report("task-a", 1, "pan:blockA")
        second = await _report("task-c", 3, "pan:blockC")
        third = await _report("task-b", 2, "pan:blockB")
        return first, second, third

    results = asyncio.run(scenario())
    assert all(error is None for _, error in results)
    rows = {item["taskId"]: item for item in mgr.queue_pending}
    assert set(rows) == {"task-a", "task-b", "task-c"}
    assert rows["task-a"]["result"] == "answer A"
    assert rows["task-b"]["result"] == "answer B"
    assert rows["task-c"]["result"] == "answer C"
    assert len(mgr.queue_pending) == 3, "each task keeps its own queue row"
    _cleanup()


def test_manual_report_older_task_while_newest_is_running(monkeypatch):
    """A newer task still running (no terminal yet) must not block reporting an
    older completed task, and must not change what that report contains."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child(terminals=[_terminal(task_id="task-old", result="old answer",
                                task_seq=1, result_cursor=1)],
           last_result=_terminal(task_id="task-old", result="old answer",
                                 task_seq=1, result_cursor=1))
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report("task-old", 1, "pan:blockOld")

    result, error = asyncio.run(scenario())
    assert error is None, f"older task must stay reportable: {error}"
    assert mgr.queue_pending[0]["taskId"] == "task-old"
    assert mgr.queue_pending[0]["result"] == "old answer"
    _cleanup()


def test_manual_report_older_task_survives_late_new_terminal(monkeypatch):
    """A newer task finishing between the capture and the retry never
    re-targets the older report, and the newer task reports independently."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    child = _child(terminals=[
        _terminal(task_id="task-old", result="old answer", task_seq=1, result_cursor=1),
    ])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        older = await _report("task-old", 1, "pan:blockOld")
        # A newer task completes between the first click and the retry.
        child.terminal_results = child.terminal_results + [
            _terminal(task_id="task-new", result="new answer", task_seq=2, result_cursor=2),
        ]
        child.last_result = child.terminal_results[-1]
        older_retry = await _report("task-old", 1, "pan:blockOld")
        newer = await _report("task-new", 2, "pan:blockNew")
        return older, older_retry, newer

    older, older_retry, newer = asyncio.run(scenario())
    assert older[1] is None and newer[1] is None
    assert older[0]["alreadyReported"] is False
    assert older_retry[0]["alreadyReported"] is True, \
        "the older report is unchanged by the newer terminal"
    rows = {item["taskId"]: item for item in mgr.queue_pending}
    assert rows["task-old"]["result"] == "old answer"
    assert rows["task-new"]["result"] == "new answer"
    assert len(mgr.queue_pending) == 2
    _cleanup()


def test_manual_report_never_substitutes_another_task(monkeypatch):
    """A requested task absent from the index fails instead of reporting the
    newest (or any other) task's result."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child(terminals=[_terminal(task_id="task-new", result="new answer",
                                task_seq=2, result_cursor=2)])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report("task-missing", 1, "pan:block")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "no_task_result"
    assert error["retryable"] is False
    assert mgr.queue_pending == [], "nothing may be sent for an unknown task"
    assert mgr.manual_report_receipts == {}
    _cleanup()


def test_manual_report_task_seq_mismatch_is_definite_failure(monkeypatch):
    """The same task id at another run's sequence is a different identity."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child(terminals=[_terminal(task_id="task-1", task_seq=5, result_cursor=5)])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report("task-1", 4, "pan:block")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "no_task_result"
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_legacy_last_result_fallback(monkeypatch):
    """Old Sessions with only ``last_result`` still resolve their task."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child(terminals=[], last_result=_terminal(task_id="task-legacy",
                                               result="legacy answer"))
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report("task-legacy", 1, "pan:block")

    result, error = asyncio.run(scenario())
    assert error is None, f"unexpected error: {error}"
    assert mgr.queue_pending[0]["result"] == "legacy answer"
    _cleanup()


def test_manual_report_error_status_terminal(monkeypatch):
    """An errored task is still a completion: the same status is reported."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child(terminals=[_terminal(task_id="task-e", status="error", result="boom",
                                task_seq=9, result_cursor=9)])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report("task-e", 9, "pan:block")

    result, error = asyncio.run(scenario())
    assert error is None
    assert mgr.queue_pending[0]["status"] == "error"
    assert mgr.queue_pending[0]["result"] == "boom"
    _cleanup()


def test_manual_report_works_after_unsubscribe_without_touching_subscription(monkeypatch):
    """report_unsubscribe only stops automatic pushes; an explicit report is
    still delivered and the subscription set is neither read nor modified."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    mgr = _setup_session("ses_mgr")  # not subscribed

    async def scenario():
        return await _report()

    result, error = asyncio.run(scenario())

    assert error is None, f"unexpected error: {error}"
    assert len(mgr.queue_pending) == 1
    assert mgr.report_subscriptions == set(), \
        "manual report must not modify the subscription"
    _cleanup()


def test_manual_report_keeps_existing_subscription(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        await _report()

    asyncio.run(scenario())
    assert mgr.report_subscriptions == {"ses_child"}
    _cleanup()


def test_manual_report_no_manager_is_definite_failure(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", terminal_results=[_terminal()],
                   last_result=_terminal())

    async def scenario():
        return await worker.enqueue_manual_report("ses_child", "task-1", 1, "pan:msg1")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "no_report_target"
    assert error["retryable"] is False
    _cleanup()


def test_manual_report_missing_manager_session(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_ghost",
                   terminal_results=[_terminal()], last_result=_terminal())

    async def scenario():
        return await worker.enqueue_manual_report("ses_child", "task-1", 1, "pan:msg1")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "no_report_target"
    _cleanup()


def test_manual_report_without_any_terminal(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_child", managed_by="ses_mgr")  # no index, no last_result
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await worker.enqueue_manual_report("ses_child", "task-1", 1, "pan:msg1")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "no_task_result"
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_session_not_found(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    async def scenario():
        return await worker.enqueue_manual_report("ses_nope", "task-1", 1, "pan:msg1")

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "session_not_found"
    _cleanup()


# ── idempotency (no double-send on retry) ──

def test_manual_report_retry_delivers_once(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await _report()
        second = await _report()
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
    _child()
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await _report()
        mgr.queue_pending.clear()  # the manager's consumer drained the batch
        second = await _report()
        return first, second

    first, second = asyncio.run(scenario())
    assert len(mgr.queue_pending) == 0
    assert second[0]["alreadyReported"] is True
    assert second[0]["queueItemId"] == first[0]["queueItemId"]
    receipt = mgr.manual_report_receipts[
        worker.manual_report_key("ses_child", "task-1", 1, "pan:msg1")]
    assert receipt["messageId"] == "pan:msg1"
    assert receipt["taskId"] == "task-1"
    assert receipt["taskSeq"] == 1
    _cleanup()


def test_manual_report_concurrent_clicks_deliver_once(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await asyncio.gather(_report(), _report(), _report())

    outcomes = asyncio.run(scenario())
    assert len(mgr.queue_pending) == 1, f"concurrent clicks double-sent: {mgr.queue_pending}"
    assert sum(1 for _, error in outcomes if error is None) == 3
    assert sum(1 for result, _ in outcomes if result and result["alreadyReported"]) == 2
    _cleanup()


def test_manual_report_distinct_blocks_of_same_task_deliver_separately(monkeypatch):
    """Different blocks are different reports of the same task."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    mgr = _setup_session("ses_mgr")

    async def scenario():
        await _report(message_id="pan:msg1")
        await _report(message_id="pan:msg2")

    asyncio.run(scenario())
    assert len(mgr.queue_pending) == 2
    _cleanup()


def test_manual_report_task_rerun_is_a_distinct_report(monkeypatch):
    """The same task id at a new sequence is a new report, not a replay."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    child = _child(terminals=[
        _terminal(task_id="task-1", result="first run", task_seq=1, result_cursor=1),
    ])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await _report("task-1", 1, "pan:block")
        child.terminal_results = child.terminal_results + [
            _terminal(task_id="task-1", result="second run", task_seq=2, result_cursor=2),
        ]
        child.last_result = child.terminal_results[-1]
        second = await _report("task-1", 2, "pan:block")
        return first, second

    first, second = asyncio.run(scenario())
    assert first[0]["alreadyReported"] is False
    assert second[0]["alreadyReported"] is False
    assert [item["result"] for item in mgr.queue_pending] == ["first run", "second run"]
    _cleanup()


# ── durability / rollback ──

def test_manual_report_save_failure_rolls_back(monkeypatch):
    _cleanup()

    async def _failing_save(s):
        raise OSError("disk full")

    monkeypatch.setattr(_sess, "save_async", _failing_save)
    _child()
    mgr = _setup_session("ses_mgr")
    old_revision = mgr.queue_revision

    async def scenario():
        return await _report()

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
        return await _report()

    result2, error2 = asyncio.run(retry())
    assert error2 is None
    assert result2["alreadyReported"] is False
    assert len(mgr.queue_pending) == 1
    _cleanup()


def test_manual_report_receipts_bounded(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    mgr = _setup_session("ses_mgr")

    async def scenario():
        for index in range(_sess.MANUAL_REPORT_RECEIPT_MAX + 5):
            await _report(message_id=f"pan:msg{index}")

    asyncio.run(scenario())
    assert len(mgr.manual_report_receipts) == _sess.MANUAL_REPORT_RECEIPT_MAX
    _cleanup()


# ── wake path ──

def test_manual_report_wakes_manager_consumer(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    _setup_session("ses_mgr")
    mw = worker.Worker(
        worker_id="worker-mgr", session_id="ses_mgr",
        adapter=CbcAdapter(), status="idle", process=None,
        pending_signal=asyncio.Queue(),
    )
    worker.workers["worker-mgr"] = mw

    async def scenario():
        await _report()

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
    _child()
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        await worker._enqueue_report("ses_child", "done", "auto", "task-0", "worker-0")
        await _report()

    asyncio.run(scenario())
    assert len(mgr.queue_pending) == 2
    assert mgr.queue_pending[0].get("manual") is None
    assert mgr.queue_pending[1]["manual"] is True
    _cleanup()


# ── durable index resolution ──

def test_durable_terminal_for_task_picks_newest_run():
    _cleanup()
    child = _child(terminals=[
        _terminal(task_id="task-1", result="old", task_seq=1, result_cursor=1),
        _terminal(task_id="task-2", result="other", task_seq=2, result_cursor=2),
        _terminal(task_id="task-1", result="new", task_seq=3, result_cursor=3),
    ])
    terminal = worker.durable_terminal_for_task(child, "task-1")
    assert terminal is not None and terminal["result"] == "new"
    assert worker.durable_terminal_for_task(child, "task-1", 1)["result"] == "old"
    assert worker.durable_terminal_for_task(child, "task-9") is None
    _cleanup()


def test_durable_terminal_ignores_non_terminal_statuses():
    _cleanup()
    child = _child(terminals=[
        {"status": "running", "result": "partial", "taskId": "task-1", "taskSeq": 1},
        {"status": "done", "result": "final", "taskId": "task-1", "taskSeq": 1,
         "resultCursor": 1},
    ], last_result=None)
    assert worker.durable_terminal_for_task(child, "task-1")["result"] == "final"
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


# ── list projection: bounded terminal identities, no result bodies ──

def test_session_summary_exposes_bounded_terminal_identities():
    """A list read must let a per-task action bind on a cold load, while the
    (potentially large) result bodies stay out of it."""
    import packages.web.server as srv
    _cleanup()
    ta = _child(terminals=[
        _terminal(task_id="task-a", result="answer A", task_seq=1, result_cursor=1),
        _terminal(task_id="task-b", result="answer B", task_seq=2, result_cursor=2),
    ])
    summary = srv._session_summary(ta, {})
    assert summary["terminalResults"] == [
        {"taskId": "task-a", "taskSeq": 1, "resultCursor": 1,
         "terminalKey": "terminal:task-a:1", "status": "done"},
        {"taskId": "task-b", "taskSeq": 2, "resultCursor": 2,
         "terminalKey": "terminal:task-b:2", "status": "done"},
    ]
    assert "result" not in summary["terminalResults"][0], \
        "the list projection must not carry result bodies"
    assert "lastResult" not in summary, "the list read stays identity-only"
    _cleanup()


def test_session_summary_terminal_identities_bounded():
    import packages.web.server as srv
    _cleanup()
    terminals = [
        _terminal(task_id=f"task-{index}", task_seq=index, result_cursor=index)
        for index in range(1, srv._TERMINAL_IDENTITY_LIMIT + 6)
    ]
    ta = _child(terminals=terminals)
    rows = srv._session_summary(ta, {})["terminalResults"]
    assert len(rows) == srv._TERMINAL_IDENTITY_LIMIT
    # Newest last: the oldest identities are the ones dropped.
    assert rows[-1]["taskId"] == f"task-{srv._TERMINAL_IDENTITY_LIMIT + 5}"
    assert all("result" not in row for row in rows)
    _cleanup()


def test_session_summary_terminal_identities_empty_without_terminal():
    import packages.web.server as srv
    _cleanup()
    bare = _setup_session("ses_bare")
    assert srv._session_summary(bare, {})["terminalResults"] == []
    _cleanup()


def test_session_summary_identity_is_accepted_by_report_route(monkeypatch):
    """The identity the list exposes is exactly what the report route accepts,
    for an older task as well as for the newest one."""
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    ta = _child(terminals=[
        _terminal(task_id="task-old", result="old answer", task_seq=1, result_cursor=1),
        _terminal(task_id="task-new", result="new answer", task_seq=2, result_cursor=2),
    ])
    _setup_session("ses_mgr")
    older = srv._session_summary(ta, {})["terminalResults"][0]
    result = asyncio.run(srv.api_session_manual_report(
        "ses_child",
        {"taskId": older["taskId"], "taskSeq": older["taskSeq"],
         "messageId": "pan:blockOld"},
    ))
    assert result.get("ok") is True, f"older identity must be accepted: {result}"
    _cleanup()


# ── HTTP endpoint ──

def test_manual_report_endpoint_ok(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "taskSeq": 1, "messageId": "pan:msg1"}))
    assert result.get("ok") is True
    assert result.get("managerId") == "ses_mgr"
    assert result.get("alreadyReported") is False
    assert isinstance(result.get("queueItemId"), str)
    assert len(mgr.queue_pending) == 1

    replay = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "taskSeq": 1, "messageId": "pan:msg1"}))
    assert replay.get("ok") is True
    assert replay.get("alreadyReported") is True
    assert len(mgr.queue_pending) == 1
    _cleanup()


def test_manual_report_endpoint_after_unsubscribe(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    mgr = _setup_session("ses_mgr")

    subscribed = asyncio.run(srv.api_report_subscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert subscribed.get("subscribed") is True
    unsubscribed = asyncio.run(srv.api_report_unsubscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert unsubscribed.get("subscribed") is False

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "taskSeq": 1, "messageId": "pan:msg1"}))
    assert result.get("ok") is True, f"manual report must work after unsubscribe: {result}"
    assert len(mgr.queue_pending) == 1
    assert mgr.report_subscriptions == set(), "unsubscribe state must be unchanged"
    _cleanup()


def test_manual_report_endpoint_unknown_task(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child(terminals=[_terminal(task_id="task-other")])
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "taskSeq": 1, "messageId": "pan:msg1"}))
    assert result.get("ok") is False
    assert result["error"]["code"] == "no_task_result"
    assert result.get("retryable") is False
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_endpoint_rejects_bad_bodies(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    mgr = _setup_session("ses_mgr")

    for body in (
        {},
        {"taskId": "task-1"},
        {"messageId": "pan:msg1"},
        {"taskId": "task-1", "messageId": ""},
        {"taskId": "task-1", "messageId": 42},
        {"taskId": 7, "messageId": "pan:msg1"},
        {"taskId": "task-1", "taskSeq": "1", "messageId": "pan:msg1"},
        {"taskId": "task-1", "taskSeq": -1, "messageId": "pan:msg1"},
        {"taskId": "task-1", "taskSeq": True, "messageId": "pan:msg1"},
        {"taskId": "task-1", "messageId": "x" * 201},
    ):
        result = asyncio.run(srv.api_session_manual_report("ses_child", body))
        assert result.get("ok") is False, f"body {body!r} must be rejected"
        assert "error" in result
    assert mgr.queue_pending == [], "rejected bodies must not deliver"
    _cleanup()


def test_manual_report_endpoint_task_seq_optional(monkeypatch):
    """A caller that knows only the task id still resolves that task."""
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _child()
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "messageId": "pan:msg1"}))
    assert result.get("ok") is True, f"taskSeq must be optional: {result}"
    assert mgr.queue_pending[0]["taskSeq"] == 1
    _cleanup()


def test_manual_report_endpoint_session_not_found(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    result = asyncio.run(srv.api_session_manual_report(
        "ses_nope", {"taskId": "task-1", "taskSeq": 1, "messageId": "pan:msg1"}))
    assert result.get("ok") is False
    assert result["error"]["code"] == "session_not_found"
    _cleanup()


# ── real link: real persistence + real ASGI route (no save stubs) ──

def test_manual_report_per_task_dedup_survives_cold_reload(monkeypatch, _no_real_recovery_spawn):
    """Real durable write into the isolated data root, then a cold reload: the
    per-task rows and receipts are on disk, retrying an OLDER task after the
    reload is still deduplicated, and a newer task reports independently."""
    import json
    import packages.web.server as srv
    _cleanup()
    _setup_session("ses_child", managed_by="ses_mgr",
                   terminal_results=[
                       _terminal(task_id="task-old", result="old answer",
                                 task_seq=1, result_cursor=1),
                       _terminal(task_id="task-new", result="new answer",
                                 task_seq=2, result_cursor=2),
                   ],
                   last_result=_terminal(task_id="task-new", result="new answer",
                                         task_seq=2, result_cursor=2))
    _setup_session("ses_mgr")

    async def scenario():
        await _sess.save_async(_sess.get("ses_child"))
        await _sess.save_async(_sess.get("ses_mgr"))
        older = await srv.api_session_manual_report(
            "ses_child", {"taskId": "task-old", "taskSeq": 1,
                          "messageId": "pan:blockOld"})
        on_disk = json.loads(
            (_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
        # Cold load: drop every in-memory Session and read the manager from disk.
        _sess._cache.clear()
        reloaded = _sess.get("ses_mgr")
        older_retry = await srv.api_session_manual_report(
            "ses_child", {"taskId": "task-old", "taskSeq": 1,
                          "messageId": "pan:blockOld"})
        newer = await srv.api_session_manual_report(
            "ses_child", {"taskId": "task-new", "taskSeq": 2,
                          "messageId": "pan:blockNew"})
        return older, on_disk, reloaded, older_retry, newer

    older, on_disk, reloaded, older_retry, newer = asyncio.run(scenario())

    assert older.get("ok") is True and older.get("alreadyReported") is False
    rows = {item.get("taskId"): item for item in on_disk["queue_pending"]
            if item.get("manualReportKey")}
    assert set(rows) == {"task-old"}
    assert rows["task-old"]["result"] == "old answer"
    assert rows["task-old"]["taskSeq"] == 1
    assert rows["task-old"]["messageId"] == "pan:blockOld"
    assert worker.manual_report_key("ses_child", "task-old", 1, "pan:blockOld") \
        in on_disk["manual_report_receipts"]

    assert reloaded is not None
    assert worker.manual_report_key("ses_child", "task-old", 1, "pan:blockOld") \
        in reloaded.manual_report_receipts
    assert older_retry.get("alreadyReported") is True
    assert newer.get("ok") is True and newer.get("alreadyReported") is False
    manual_rows = {item["taskId"]: item for item in reloaded.queue_pending
                   if item.get("manualReportKey")}
    assert set(manual_rows) == {"task-old", "task-new"}, \
        "a retry after a cold reload must not double-deliver, and each task keeps its own row"
    assert manual_rows["task-new"]["result"] == "new answer"
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
    _child(terminals=[
        _terminal(task_id="task-old", result="old answer", task_seq=1, result_cursor=1),
        _terminal(task_id="task-new", result="new answer", task_seq=2, result_cursor=2),
    ])
    mgr = _setup_session("ses_mgr")

    app = FastAPI()
    app.add_api_route("/api/sessions/{session_id}/manual-report",
                      server.api_session_manual_report, methods=["POST"])
    with TestClient(app) as client:
        response = client.post("/api/sessions/ses_child/manual-report",
                               json={"taskId": "task-old", "taskSeq": 1,
                                     "messageId": "pan:blockOld"})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["managerId"] == "ses_mgr"
        assert body["alreadyReported"] is False
        assert isinstance(body["queueItemId"], str)

        replay = client.post("/api/sessions/ses_child/manual-report",
                             json={"taskId": "task-old", "taskSeq": 1,
                                   "messageId": "pan:blockOld"})
        assert replay.status_code == 200
        assert replay.json()["alreadyReported"] is True

        newer = client.post("/api/sessions/ses_child/manual-report",
                            json={"taskId": "task-new", "taskSeq": 2,
                                  "messageId": "pan:blockNew"})
        assert newer.json()["ok"] is True
        assert newer.json()["alreadyReported"] is False
        assert len(mgr.queue_pending) == 2

        invalid = client.post("/api/sessions/ses_child/manual-report",
                              json={"messageId": "pan:blockOld"})
        assert invalid.status_code == 200
        assert invalid.json()["ok"] is False

        missing = client.post("/api/sessions/ses_ghost/manual-report",
                              json={"taskId": "task-old", "taskSeq": 1,
                                    "messageId": "pan:blockOld"})
        assert missing.status_code == 200
        assert missing.json()["error"]["code"] == "session_not_found"
    _cleanup()


if __name__ == "__main__":
    test_manual_report_delivers_to_manager()
    test_manual_report_reports_each_historical_task_separately()
    test_manual_report_older_task_while_newest_is_running()
    test_manual_report_older_task_survives_late_new_terminal()
    test_manual_report_never_substitutes_another_task()
    test_manual_report_task_seq_mismatch_is_definite_failure()
    test_manual_report_legacy_last_result_fallback()
    test_manual_report_error_status_terminal()
    test_manual_report_works_after_unsubscribe_without_touching_subscription()
    test_manual_report_keeps_existing_subscription()
    test_manual_report_no_manager_is_definite_failure()
    test_manual_report_missing_manager_session()
    test_manual_report_without_any_terminal()
    test_manual_report_session_not_found()
    test_manual_report_retry_delivers_once()
    test_manual_report_retry_after_consumption_delivers_once()
    test_manual_report_concurrent_clicks_deliver_once()
    test_manual_report_distinct_blocks_of_same_task_deliver_separately()
    test_manual_report_task_rerun_is_a_distinct_report()
    test_manual_report_save_failure_rolls_back()
    test_manual_report_receipts_bounded()
    test_manual_report_wakes_manager_consumer()
    test_auto_report_still_requires_subscription()
    test_auto_report_still_delivers_when_subscribed()
    test_manual_and_auto_reports_coexist()
    test_durable_terminal_for_task_picks_newest_run()
    test_durable_terminal_ignores_non_terminal_statuses()
    test_manual_report_receipts_roundtrip()
    test_manual_report_receipts_default_and_legacy()
    test_session_summary_exposes_bounded_terminal_identities()
    test_session_summary_terminal_identities_bounded()
    test_session_summary_terminal_identities_empty_without_terminal()
    test_session_summary_identity_is_accepted_by_report_route()
    test_manual_report_endpoint_ok()
    test_manual_report_endpoint_after_unsubscribe()
    test_manual_report_endpoint_unknown_task()
    test_manual_report_endpoint_rejects_bad_bodies()
    test_manual_report_endpoint_task_seq_optional()
    test_manual_report_endpoint_session_not_found()
    test_manual_report_per_task_dedup_survives_cold_reload()
    test_manual_report_route_through_asgi()
    print("\n=== ALL MANUAL TASK REPORT TESTS PASSED ===")
