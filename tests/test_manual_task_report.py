"""Tests for explicit per-task manual reports (POST /api/sessions/{id}/manual-report).

Covers the identity rules that make a browser-clicked per-task report safe:

- content/target reuse: the manual item mirrors the automatic report item
  (same queue target, same shape, content resolved from that task run's durable
  terminal row — never from "whatever terminal happens to be newest")
- every completed task run in the bounded durable index stays reportable,
  including older runs after newer ones finish or while a newer one is running
- the submitted messageId must be that run's final text block, proven from the
  terminal row's own history epoch/revision; a non-tail block, a block from
  another run, an unprovable (legacy/rewritten) history and an unknown task are
  all definite failures with no delivery and no substitution
- subscription independence: delivered after report_unsubscribe, and the
  subscription is never modified (no temporary subscribe either)
- idempotency is keyed on the task run identity, so an arbitrary or repeated
  client messageId cannot turn one run into several deliveries, while distinct
  runs of one task id stay distinct
- save failure rolls the attempt back (definite failure; a retry stays fresh)
- concurrent duplicate clicks deliver once (queue lock)
- automatic report behavior is untouched by this feature
- the list projection exposes bounded terminal identities (no result bodies)
- real durable write + cold reload keeps per-run dedup
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


async def _noop_save_async(s, *, outcome=None):
    pass


def _noop_save(s):
    pass


def _append_run(s, task_id, task_seq, answers, *, terminal=True,
                status="done", result=None, result_cursor=None) -> dict | None:
    """Append one task run exactly like ``_persist_terminal_state`` does.

    Dispatch row and assistant rows are appended first, then — for a run with a
    terminal — the run's result row is appended and only afterwards is the
    terminal's history epoch/revision recorded from the post-append revision
    (the production path is explicit about covering the revision after the
    final append).  The run's final text block is therefore the last row of the
    covered region, not the row at the boundary offset.

    Returns the terminal record (with the run's ``tail_id`` added) or None.
    """
    _sess.append_history(s, {
        "role": "user", "content": f"do {task_id}",
        "source": "agent", "taskId": task_id, "taskIdSource": "assign",
    })
    for answer in answers:
        _sess.append_history(s, {"role": "assistant", "content": answer})
    if not terminal:
        return None
    body = result if result is not None else (answers[-1] if answers else "done")
    # The worker appends the run's result row before recording the boundary.
    _sess.append_history(s, {"role": "assistant", "content": body})
    record = {
        "status": status,
        "result": body,
        "taskId": task_id,
        "taskSeq": task_seq,
        "workerId": "worker-1",
        "resultCursor": task_seq if result_cursor is None else result_cursor,
        "terminalKey": f"terminal:{task_id}:{task_seq}",
        "historyEpoch": s.history_epoch,
        "historyRevision": s.history_revision,
    }
    s.terminal_results = list(s.terminal_results) + [record]
    s.last_result = dict(record)
    record["tail_id"] = s.history[-1]["messageId"]
    return record


def _child(runs, manager=True, sid="ses_child"):
    """A managed Session with a real history/terminal correspondence.

    ``runs`` is a list of (task_id, task_seq, answers) tuples.
    """
    s = _setup_session(sid, managed_by="ses_mgr" if manager else None)
    records = []
    for task_id, task_seq, answers in runs:
        records.append(_append_run(s, task_id, task_seq, answers))
    return s, records


def _report(sid="ses_child", task_id="task-1", task_seq=1, message_id="pan:tail"):
    """Call the real delivery function the HTTP route calls."""
    return worker.enqueue_manual_report(sid, task_id, task_seq, message_id)


# ── delivery semantics ──

def test_manual_report_delivers_to_manager(monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        return await _report(message_id=tail)

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
    # Content semantics = that task run's durable terminal row.
    assert item["status"] == "done"
    assert item["result"] == "the answer"
    assert item["taskId"] == "task-1"
    assert item["taskSeq"] == 1
    assert item["workerId"] == "worker-1"
    assert item["manual"] is True
    assert item["messageId"] == tail
    assert item["manualReportKey"] == worker.manual_report_key("ses_child", "task-1", 1)
    assert _no_real_recovery_spawn == ["ses_mgr"], \
        "manual report must wake the manager through the same recovery path"
    _cleanup()


def test_manual_report_reports_each_historical_task_separately(monkeypatch):
    """Every completed run in the durable index is reportable with its own
    block and its own result — not just the newest one."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([
        ("task-a", 1, ["answer A"]),
        ("task-b", 2, ["answer B"]),
        ("task-c", 3, ["answer C"]),
    ])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await _report(task_id="task-a", task_seq=1, message_id=records[0]["tail_id"])
        second = await _report(task_id="task-c", task_seq=3, message_id=records[2]["tail_id"])
        third = await _report(task_id="task-b", task_seq=2, message_id=records[1]["tail_id"])
        return first, second, third

    results = asyncio.run(scenario())
    assert all(error is None for _, error in results)
    rows = {item["taskId"]: item for item in mgr.queue_pending}
    assert set(rows) == {"task-a", "task-b", "task-c"}
    assert rows["task-a"]["result"] == "answer A"
    assert rows["task-b"]["result"] == "answer B"
    assert rows["task-c"]["result"] == "answer C"
    assert len(mgr.queue_pending) == 3, "each run keeps its own queue row"
    _cleanup()


def test_manual_report_older_task_while_newest_is_running(monkeypatch):
    """A newer run still executing (no terminal, no settled answer) must not
    block reporting an older completed run, nor change its content."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s, records = _child([("task-old", 1, ["old answer"])])
    # The newer run has been dispatched but has no answer and no terminal yet.
    _append_run(s, "task-new", 2, [], terminal=False)
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report(task_id="task-old", task_seq=1, message_id=records[0]["tail_id"])

    result, error = asyncio.run(scenario())
    assert error is None, f"older run must stay reportable: {error}"
    assert mgr.queue_pending[0]["taskId"] == "task-old"
    assert mgr.queue_pending[0]["result"] == "old answer"
    _cleanup()


def test_manual_report_older_task_survives_late_new_terminal(monkeypatch):
    """A newer run finishing between the click and the retry never re-targets
    the older report, and the newer run reports its own result."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s, records = _child([("task-old", 1, ["old answer"])])
    older_tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        older = await _report(task_id="task-old", task_seq=1, message_id=older_tail)
        # The newer run completes before the retry.
        newer_record = _append_run(s, "task-new", 2, ["new answer"])
        older_retry = await _report(task_id="task-old", task_seq=1, message_id=older_tail)
        newer = await _report(task_id="task-new", task_seq=2,
                             message_id=newer_record["tail_id"])
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


# ── block identity proof ──

def test_manual_report_rejects_a_non_tail_block_of_the_run(monkeypatch):
    """Only the run's final text block is accepted: an earlier assistant row of
    the same run is a definite failure (nothing is delivered)."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_child", managed_by="ses_mgr")
    _append_run(s, "task-1", 1, ["first block", "final block"])
    middle_block = next(
        row["messageId"] for row in s.history
        if row.get("role") == "assistant" and row.get("content") == "first block")
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report(message_id=middle_block)

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "block_mismatch"
    assert error["retryable"] is False
    assert mgr.queue_pending == []
    assert mgr.manual_report_receipts == {}
    _cleanup()


def test_manual_report_rejects_a_block_of_another_run(monkeypatch):
    """Red/green regression: the same taskId dispatched twice must never pair an
    earlier run's block with a later run's terminal (the pre-fix behaviour would
    have reported the newer result for the older block)."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([
        ("task-r", 1, ["first run"]),
        ("task-r", 2, ["second run"]),
    ])
    first_tail, second_tail = records[0]["tail_id"], records[1]["tail_id"]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        wrong = await _report(task_id="task-r", task_seq=2, message_id=first_tail)
        right_first = await _report(task_id="task-r", task_seq=1, message_id=first_tail)
        right_second = await _report(task_id="task-r", task_seq=2, message_id=second_tail)
        return wrong, right_first, right_second

    wrong, right_first, right_second = asyncio.run(scenario())
    assert wrong[0] is None and wrong[1]["code"] == "block_mismatch", \
        "an earlier run's block must not report a later run"
    assert right_first[1] is None and right_second[1] is None
    rows = [(item["taskId"], item["taskSeq"], item["result"])
            for item in mgr.queue_pending]
    assert rows == [("task-r", 1, "first run"), ("task-r", 2, "second run")]
    _cleanup()


def test_manual_report_rejects_a_foreign_message_id(monkeypatch):
    """An arbitrary message id that is not a durable row of this Session is a
    definite failure — it can never create a second report for one run."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await _report(message_id=tail)
        spam = await _report(message_id="pan:made-up")
        retry = await _report(message_id=tail)
        return first, spam, retry

    first, spam, retry = asyncio.run(scenario())
    assert first[1] is None
    assert spam[0] is None and spam[1]["code"] == "block_mismatch"
    assert retry[0]["alreadyReported"] is True
    assert len(mgr.queue_pending) == 1, \
        "a client-supplied message id must not turn one run into several reports"
    _cleanup()


def test_manual_report_unprovable_history_is_definite_failure(monkeypatch):
    """Legacy rows without a history boundary (and rewritten epochs) cannot
    prove the block relation: refuse instead of guessing."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_child", managed_by="ses_mgr")
    _sess.append_history(s, {"role": "user", "content": "do task-1",
                             "source": "agent", "taskId": "task-1",
                             "taskIdSource": "assign"})
    _sess.append_history(s, {"role": "assistant", "content": "the answer"})
    tail = s.history[-1]["messageId"]
    s.terminal_results = [{
        "status": "done", "result": "the answer", "taskId": "task-1",
        "taskSeq": 1, "workerId": "worker-1", "resultCursor": 1,
        "terminalKey": "terminal:task-1:1",
    }]
    s.last_result = dict(s.terminal_results[0])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report(message_id=tail)

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "block_unproven"
    assert error["retryable"] is False
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_rejects_a_rewritten_history_epoch(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    # The history was replaced after the terminal committed: the recorded
    # boundary no longer applies to the current rows.
    _sess.replace_history(s, list(s.history))
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report(message_id=tail)

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "block_unproven"
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_never_substitutes_another_task(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-new", 2, ["new answer"])])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report(task_id="task-missing", task_seq=1,
                             message_id=records[0]["tail_id"])

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "no_task_result"
    assert mgr.queue_pending == [], "nothing may be sent for an unknown task"
    assert mgr.manual_report_receipts == {}
    _cleanup()


def test_manual_report_task_seq_mismatch_is_definite_failure(monkeypatch):
    """The same task id at another run's sequence is a different identity."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-1", 5, ["answer"])])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report(task_id="task-1", task_seq=4,
                             message_id=records[0]["tail_id"])

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "no_task_result"
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_task_seq_optional(monkeypatch):
    """A caller that knows only the task id still resolves that run (and the
    key uses the resolved run's identity)."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await _report(task_id="task-1", task_seq=None, message_id=tail)
        second = await _report(task_id="task-1", task_seq=1, message_id=tail)
        return first, second

    first, second = asyncio.run(scenario())
    assert first[1] is None
    assert mgr.queue_pending[0]["taskSeq"] == 1
    assert second[0]["alreadyReported"] is True, \
        "the resolved run identity is the dedup key"
    _cleanup()


def test_manual_report_error_status_terminal(monkeypatch):
    """An errored run is still a completion: the same status is reported."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_child", managed_by="ses_mgr")
    record = _append_run(s, "task-e", 9, ["boom"], status="error", result="boom")
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await _report(task_id="task-e", task_seq=9, message_id=record["tail_id"])

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
    _, records = _child([("task-1", 1, ["the answer"])])
    mgr = _setup_session("ses_mgr")  # not subscribed

    async def scenario():
        return await _report(message_id=records[0]["tail_id"])

    result, error = asyncio.run(scenario())

    assert error is None, f"unexpected error: {error}"
    assert len(mgr.queue_pending) == 1
    assert mgr.report_subscriptions == set(), \
        "manual report must not modify the subscription"
    _cleanup()


def test_manual_report_keeps_existing_subscription(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-1", 1, ["the answer"])])
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        await _report(message_id=records[0]["tail_id"])

    asyncio.run(scenario())
    assert mgr.report_subscriptions == {"ses_child"}
    _cleanup()


def test_manual_report_no_manager_is_definite_failure(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-1", 1, ["the answer"])], manager=False)

    async def scenario():
        return await _report(message_id=records[0]["tail_id"])

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "no_report_target"
    assert error["retryable"] is False
    _cleanup()


def test_manual_report_missing_manager_session(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_child", managed_by="ses_ghost")
    record = _append_run(s, "task-1", 1, ["the answer"])

    async def scenario():
        return await _report(message_id=record["tail_id"])

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
        return await _report()

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
    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await _report(message_id=tail)
        second = await _report(message_id=tail)
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
    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await _report(message_id=tail)
        mgr.queue_pending.clear()  # the manager's consumer drained the batch
        second = await _report(message_id=tail)
        return first, second

    first, second = asyncio.run(scenario())
    assert len(mgr.queue_pending) == 0
    assert second[0]["alreadyReported"] is True
    assert second[0]["queueItemId"] == first[0]["queueItemId"]
    receipt = mgr.manual_report_receipts[
        worker.manual_report_key("ses_child", "task-1", 1)]
    assert receipt["messageId"] == tail
    assert receipt["taskId"] == "task-1"
    assert receipt["taskSeq"] == 1
    _cleanup()


def test_manual_report_concurrent_clicks_deliver_once(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await asyncio.gather(
            _report(message_id=tail), _report(message_id=tail), _report(message_id=tail))

    outcomes = asyncio.run(scenario())
    assert len(mgr.queue_pending) == 1, f"concurrent clicks double-sent: {mgr.queue_pending}"
    assert sum(1 for _, error in outcomes if error is None) == 3
    assert sum(1 for result, _ in outcomes if result and result["alreadyReported"]) == 2
    _cleanup()


def test_manual_report_task_rerun_is_a_distinct_report(monkeypatch):
    """The same task id at a new sequence is a new report of a new run."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-1", 1, ["first run"])])
    mgr = _setup_session("ses_mgr")
    first_tail = records[0]["tail_id"]

    async def scenario():
        first = await _report(task_id="task-1", task_seq=1, message_id=first_tail)
        rerun = _append_run(_sess.get("ses_child"), "task-1", 2, ["second run"])
        second = await _report(task_id="task-1", task_seq=2, message_id=rerun["tail_id"])
        return first, second

    first, second = asyncio.run(scenario())
    assert first[0]["alreadyReported"] is False
    assert second[0]["alreadyReported"] is False
    assert [item["result"] for item in mgr.queue_pending] == ["first run", "second run"]
    _cleanup()


# ── durability / rollback ──

def test_manual_report_save_failure_rolls_back(monkeypatch):
    _cleanup()

    async def _failing_save(s, *, outcome=None):
        raise OSError("disk full")

    monkeypatch.setattr(_sess, "save_async", _failing_save)
    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")
    old_revision = mgr.queue_revision

    async def scenario():
        return await _report(message_id=tail)

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
        return await _report(message_id=tail)

    result2, error2 = asyncio.run(retry())
    assert error2 is None
    assert result2["alreadyReported"] is False
    assert len(mgr.queue_pending) == 1
    _cleanup()


def test_manual_report_receipts_bounded(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_child", managed_by="ses_mgr")
    tails = [
        _append_run(s, f"task-{index}", index, [f"answer {index}"])["tail_id"]
        for index in range(1, _sess.MANUAL_REPORT_RECEIPT_MAX + 6)
    ]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        for index, tail in enumerate(tails, start=1):
            await _report(task_id=f"task-{index}", task_seq=index, message_id=tail)

    asyncio.run(scenario())
    assert len(mgr.manual_report_receipts) == _sess.MANUAL_REPORT_RECEIPT_MAX
    _cleanup()


# ── wake path ──

def test_manual_report_wakes_manager_consumer(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-1", 1, ["the answer"])])
    _setup_session("ses_mgr")
    mw = worker.Worker(
        worker_id="worker-mgr", session_id="ses_mgr",
        adapter=CbcAdapter(), status="idle", process=None,
        pending_signal=asyncio.Queue(),
    )
    worker.workers["worker-mgr"] = mw

    async def scenario():
        await _report(message_id=records[0]["tail_id"])

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
    _, records = _child([("task-1", 1, ["the answer"])])
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        await worker._enqueue_report("ses_child", "done", "auto", "task-0", "worker-0")
        await _report(message_id=records[0]["tail_id"])

    asyncio.run(scenario())
    assert len(mgr.queue_pending) == 2
    assert mgr.queue_pending[0].get("manual") is None
    assert mgr.queue_pending[1]["manual"] is True
    _cleanup()


# ── durable index resolution ──

def test_durable_terminal_for_task_picks_newest_run():
    _cleanup()
    s, records = _child([
        ("task-1", 1, ["old"]),
        ("task-2", 2, ["other"]),
        ("task-1", 3, ["new"]),
    ])
    terminal = worker.durable_terminal_for_task(s, "task-1")
    assert terminal is not None and terminal["result"] == "new"
    assert worker.durable_terminal_for_task(s, "task-1", 1)["result"] == "old"
    assert worker.durable_terminal_for_task(s, "task-9") is None
    assert records[0]["result"] == "old"
    _cleanup()


def test_task_tail_block_id_matches_appended_result_row():
    _cleanup()
    s, records = _child([("task-1", 1, ["answer"])])
    tail = worker.task_tail_block_id(s, records[0])
    assert tail == records[0]["tail_id"] == s.history[-1]["messageId"]
    _cleanup()


def test_task_tail_block_id_unproven_cases():
    _cleanup()
    s, records = _child([("task-1", 1, ["answer"])])
    record = records[0]
    assert worker.task_tail_block_id(s, {**record, "historyEpoch": None}) is None
    assert worker.task_tail_block_id(s, {**record, "historyEpoch": "other"}) is None
    assert worker.task_tail_block_id(s, {**record, "historyRevision": None}) is None
    assert worker.task_tail_block_id(s, {**record, "historyRevision": 0}) is None
    assert worker.task_tail_block_id(s, {**record, "historyRevision": len(s.history) + 5}) is None
    _cleanup()


# ── Session field roundtrip ──

def test_manual_report_receipts_roundtrip():
    mgr = _sess.Session(id="ses_mgr", name="mgr")
    mgr.manual_report_receipts = {"manual:a:b": {"queueItemId": "q1"}}
    data = mgr.to_dict()
    assert data["manual_report_receipts"] == {"manual:a:b": {"queueItemId": "q1"}}
    restored = _sess.Session._from_data(dict(data))
    assert restored.manual_report_receipts == {"manual:a:b": {"queueItemId": "q1"}}


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
    """A list read must let a per-task action bind (and know the run's block is
    provable) on a cold load, while result bodies stay out of it."""
    import packages.web.server as srv
    _cleanup()
    s, records = _child([("task-a", 1, ["answer A"]), ("task-b", 2, ["answer B"])])
    summary = srv._session_summary(s, {})
    expected = [
        {key: record[key] for key in
         ("taskId", "taskSeq", "resultCursor", "terminalKey", "status",
          "historyEpoch", "historyRevision")}
        for record in records
    ]
    assert summary["terminalResults"] == expected
    for row in summary["terminalResults"]:
        assert set(row) == {"taskId", "taskSeq", "resultCursor", "terminalKey",
                            "status", "historyEpoch", "historyRevision"}
        assert "result" not in row, "the list projection must not carry result bodies"
    assert "lastResult" not in summary, "the list read stays identity-only"
    _cleanup()


def test_session_summary_terminal_identities_bounded():
    import packages.web.server as srv
    _cleanup()
    s = _setup_session("ses_child", managed_by="ses_mgr")
    for index in range(1, srv._TERMINAL_IDENTITY_LIMIT + 6):
        _append_run(s, f"task-{index}", index, [f"answer {index}"])
    rows = srv._session_summary(s, {})["terminalResults"]
    assert len(rows) == srv._TERMINAL_IDENTITY_LIMIT
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
    for an older run as well as for the newest one."""
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s, records = _child([("task-old", 1, ["old answer"]), ("task-new", 2, ["new answer"])])
    _setup_session("ses_mgr")
    older = srv._session_summary(s, {})["terminalResults"][0]
    result = asyncio.run(srv.api_session_manual_report(
        "ses_child",
        {"taskId": older["taskId"], "taskSeq": older["taskSeq"],
         "messageId": records[0]["tail_id"]},
    ))
    assert result.get("ok") is True, f"older identity must be accepted: {result}"
    _cleanup()


# ── HTTP endpoint ──

def test_manual_report_endpoint_ok(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "taskSeq": 1, "messageId": tail}))
    assert result.get("ok") is True
    assert result.get("managerId") == "ses_mgr"
    assert result.get("alreadyReported") is False
    assert isinstance(result.get("queueItemId"), str)
    assert len(mgr.queue_pending) == 1

    replay = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "taskSeq": 1, "messageId": tail}))
    assert replay.get("ok") is True
    assert replay.get("alreadyReported") is True
    assert len(mgr.queue_pending) == 1
    _cleanup()


def test_manual_report_endpoint_after_unsubscribe(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    subscribed = asyncio.run(srv.api_report_subscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert subscribed.get("subscribed") is True
    unsubscribed = asyncio.run(srv.api_report_unsubscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert unsubscribed.get("subscribed") is False

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "taskSeq": 1, "messageId": tail}))
    assert result.get("ok") is True, f"manual report must work after unsubscribe: {result}"
    assert len(mgr.queue_pending) == 1
    assert mgr.report_subscriptions == set(), "unsubscribe state must be unchanged"
    _cleanup()


def test_manual_report_endpoint_unknown_task(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _child([("task-other", 1, ["other"])])
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "taskSeq": 1,
                      "messageId": records[0]["tail_id"]}))
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
    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    for body in (
        {},
        {"taskId": "task-1"},
        {"messageId": tail},
        {"taskId": "task-1", "messageId": ""},
        {"taskId": "task-1", "messageId": 42},
        {"taskId": 7, "messageId": tail},
        {"taskId": "task-1", "taskSeq": "1", "messageId": tail},
        {"taskId": "task-1", "taskSeq": -1, "messageId": tail},
        {"taskId": "task-1", "taskSeq": True, "messageId": tail},
        {"taskId": "task-1", "messageId": "x" * 201},
    ):
        result = asyncio.run(srv.api_session_manual_report("ses_child", body))
        assert result.get("ok") is False, f"body {body!r} must be rejected"
        assert "error" in result
    assert mgr.queue_pending == [], "rejected bodies must not deliver"
    _cleanup()


def test_manual_report_endpoint_rejects_non_tail_block(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_child", managed_by="ses_mgr")
    _append_run(s, "task-1", 1, ["first block", "final block"])
    middle = next(row["messageId"] for row in s.history
                  if row.get("role") == "assistant" and row.get("content") == "first block")
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1", "taskSeq": 1, "messageId": middle}))
    assert result.get("ok") is False
    assert result["error"]["code"] == "block_mismatch"
    assert mgr.queue_pending == []
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

def test_manual_report_per_run_dedup_survives_cold_reload(monkeypatch, _no_real_recovery_spawn):
    """Real durable write into the isolated data root, then a cold reload: the
    per-run rows and receipts are on disk, retrying an OLDER run after the reload
    is still deduplicated, and a newer run reports independently."""
    import json
    import packages.web.server as srv
    _cleanup()
    s, records = _child([("task-old", 1, ["old answer"]), ("task-new", 2, ["new answer"])])
    old_tail, new_tail = records[0]["tail_id"], records[1]["tail_id"]
    _setup_session("ses_mgr")

    async def scenario():
        await _sess.save_async(_sess.get("ses_child"))
        await _sess.save_async(_sess.get("ses_mgr"))
        older = await srv.api_session_manual_report(
            "ses_child", {"taskId": "task-old", "taskSeq": 1, "messageId": old_tail})
        on_disk = json.loads(
            (_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
        # Cold load: drop every in-memory Session and read the manager from disk.
        _sess._cache.clear()
        reloaded = _sess.get("ses_mgr")
        older_retry = await srv.api_session_manual_report(
            "ses_child", {"taskId": "task-old", "taskSeq": 1, "messageId": old_tail})
        newer = await srv.api_session_manual_report(
            "ses_child", {"taskId": "task-new", "taskSeq": 2, "messageId": new_tail})
        return older, on_disk, reloaded, older_retry, newer

    older, on_disk, reloaded, older_retry, newer = asyncio.run(scenario())

    assert older.get("ok") is True and older.get("alreadyReported") is False
    rows = {item.get("taskId"): item for item in on_disk["queue_pending"]
            if item.get("manualReportKey")}
    assert set(rows) == {"task-old"}
    assert rows["task-old"]["result"] == "old answer"
    assert rows["task-old"]["taskSeq"] == 1
    assert rows["task-old"]["messageId"] == old_tail
    assert worker.manual_report_key("ses_child", "task-old", 1) \
        in on_disk["manual_report_receipts"]

    assert reloaded is not None
    assert worker.manual_report_key("ses_child", "task-old", 1) \
        in reloaded.manual_report_receipts
    assert older_retry.get("alreadyReported") is True
    assert newer.get("ok") is True and newer.get("alreadyReported") is False
    manual_rows = {item["taskId"]: item for item in reloaded.queue_pending
                   if item.get("manualReportKey")}
    assert set(manual_rows) == {"task-old", "task-new"}, \
        "a retry after a cold reload must not double-deliver, and each run keeps its own row"
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
    _, records = _child([("task-old", 1, ["old answer"]), ("task-new", 2, ["new answer"])])
    old_tail, new_tail = records[0]["tail_id"], records[1]["tail_id"]
    mgr = _setup_session("ses_mgr")

    app = FastAPI()
    app.add_api_route("/api/sessions/{session_id}/manual-report",
                      server.api_session_manual_report, methods=["POST"])
    with TestClient(app) as client:
        response = client.post("/api/sessions/ses_child/manual-report",
                               json={"taskId": "task-old", "taskSeq": 1,
                                     "messageId": old_tail})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["managerId"] == "ses_mgr"
        assert body["alreadyReported"] is False
        assert isinstance(body["queueItemId"], str)

        replay = client.post("/api/sessions/ses_child/manual-report",
                             json={"taskId": "task-old", "taskSeq": 1,
                                   "messageId": old_tail})
        assert replay.status_code == 200
        assert replay.json()["alreadyReported"] is True

        # The other run's block is a different, accepted report; the same block
        # submitted for the other run is refused.
        wrong = client.post("/api/sessions/ses_child/manual-report",
                            json={"taskId": "task-new", "taskSeq": 2,
                                  "messageId": old_tail})
        assert wrong.json()["ok"] is False
        assert wrong.json()["error"]["code"] == "block_mismatch"

        newer = client.post("/api/sessions/ses_child/manual-report",
                            json={"taskId": "task-new", "taskSeq": 2,
                                  "messageId": new_tail})
        assert newer.json()["ok"] is True
        assert newer.json()["alreadyReported"] is False
        assert len(mgr.queue_pending) == 2

        invalid = client.post("/api/sessions/ses_child/manual-report",
                              json={"messageId": old_tail})
        assert invalid.status_code == 200
        assert invalid.json()["ok"] is False

        missing = client.post("/api/sessions/ses_ghost/manual-report",
                              json={"taskId": "task-old", "taskSeq": 1,
                                    "messageId": old_tail})
        assert missing.status_code == 200
        assert missing.json()["error"]["code"] == "session_not_found"
    _cleanup()


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("\n=== ALL MANUAL TASK REPORT TESTS PASSED ===")
