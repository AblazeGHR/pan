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
- any completed run is reportable, including plain conversations without a
  taskId: the durable result cursor (or the run's taskSeq) binds the exact run,
  an identity-less request is a definite failure (never the newest terminal),
  and conflicting identities are refused instead of substituted
- save failure rolls the attempt back (definite failure; a retry stays fresh)
- concurrent duplicate clicks deliver once (queue lock)
- automatic report behavior is untouched by this feature
- the list projection exposes bounded terminal identities (no result bodies)
- real durable write + cold reload keeps per-run dedup
"""

import asyncio
import json
import sys
import threading
import time
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
    # Per-session asyncio locks bind to the loop that first contended them;
    # every test runs its own asyncio.run loop (and the ASGI route test runs
    # yet another through TestClient), so stale locks must not survive into
    # the next test or a contended acquire raises "bound to a different
    # event loop".
    worker._queue_locks.clear()


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
    """The candidate write fails inside the real ticket/executor: the rollback
    runs before the ticket retires, so memory and disk are left clean and a
    retry stays a fresh first attempt."""
    _cleanup()
    real_save_body = _sess._save_body
    calls = {"n": 0}

    def _failing_first_write(session, *args, **kwargs):
        if session.id == "ses_mgr":
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk full")
        return real_save_body(session, *args, **kwargs)

    monkeypatch.setattr(_sess, "_save_body", _failing_first_write)
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
    assert calls["n"] == 1, "the failure must come from the real writer"
    assert mgr.queue_pending == [], "failed attempt must not stay queued"
    assert mgr.manual_report_receipts == {}
    assert mgr.queue_revision == old_revision
    assert all(
        record.get("manualReportKey") is None
        for record in mgr.queue_delivery_ledger.values()
    ), "failed attempt must not leave a durable receipt behind"

    # A retry after the definite failure is a fresh first attempt.
    monkeypatch.setattr(_sess, "_save_body", real_save_body)

    async def retry():
        return await _report(message_id=tail)

    result2, error2 = asyncio.run(retry())
    assert error2 is None
    assert result2["alreadyReported"] is False
    assert len(mgr.queue_pending) == 1
    _cleanup()


def test_manual_report_failed_write_rolls_back_inside_the_ticket(monkeypatch):
    """Thread-gating regression: a later queued writer for the same session must
    never persist the failed candidate.  The rollback therefore runs inside the
    report's own ticket, before it retires, and a disk re-read holds no failed
    report row or receipt."""
    _cleanup()
    real_save_body = _sess._save_body
    calls = {"n": 0}

    def _failing_first_write(session, *args, **kwargs):
        if session.id == "ses_mgr":
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("first write failed")
        return real_save_body(session, *args, **kwargs)

    monkeypatch.setattr(_sess, "_save_body", _failing_first_write)
    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        # The later writer is enqueued while the report's ticket is in flight,
        # so the executor would persist the candidate if the rollback happened
        # only after the ticket retired.
        report_task = asyncio.create_task(_report(message_id=tail))
        later_writer = asyncio.create_task(_sess.save_async(_sess.get("ses_mgr")))
        outcome = await asyncio.gather(report_task, return_exceptions=True)
        await later_writer
        return outcome

    gathered = asyncio.run(scenario())
    error = gathered[0]
    assert isinstance(error, tuple) and error[0] is None \
        and error[1]["code"] == "persist_failed", f"unexpected outcome: {error}"
    assert mgr.queue_pending == [] and mgr.manual_report_receipts == {}
    on_disk = json.loads((_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
    assert [item.get("taskId") for item in on_disk["queue_pending"]] == [], \
        "a later writer must not persist the failed candidate"
    assert on_disk["manual_report_receipts"] == {}, \
        "no failed receipt may reach the disk"
    _cleanup()


def test_manual_report_reverse_interleave_never_persists_candidate(monkeypatch, _no_real_recovery_spawn):
    """Reverse interleaving with a gated old writer: an old writer is already
    executing (gated inside its own ticket) when the report is requested; the
    report's ticket queues behind it; releasing the old writer completes it, and
    only then does the report writer fail.  The apply/save/rollback all run
    inside the report's ticket, so the disk must hold no failed report or
    receipt (the gated old writer saved the pre-report state)."""
    _cleanup()
    real_save_body = _sess._save_body
    calls = {"n": 0}
    release = threading.Event()

    def _gated_then_failing(session, *args, **kwargs):
        if session.id != "ses_mgr":
            return real_save_body(session, *args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            # The old writer is already executing and gated.
            assert release.wait(timeout=10), "gated old writer was never released"
            return real_save_body(session, *args, **kwargs)
        raise OSError("report write failed")

    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")
    asyncio.run(_sess.save_async(mgr))
    on_disk_before = json.loads(
        (_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
    # Inject only after the baseline save: the gated call must be the old
    # writer's, never the baseline's.
    monkeypatch.setattr(_sess, "_save_body", _gated_then_failing)

    async def scenario():
        # 1) the old writer starts and gates inside its ticket
        old_writer = asyncio.create_task(_sess.save_async(_sess.get("ses_mgr")))
        await asyncio.sleep(0.2)
        # 2) the manual report is requested: its ticket queues behind the old one
        report_task = asyncio.create_task(_report(message_id=tail))
        await asyncio.sleep(0.2)
        assert mgr.queue_pending == [],             "the candidate must not be applied before the report ticket executes"
        # 3) release the old writer; it completes and saves the pre-report state
        release.set()
        await old_writer
        # 4) the report writer now fails inside its own ticket
        return await report_task

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "persist_failed"
    assert calls["n"] == 2
    assert mgr.queue_pending == [] and mgr.manual_report_receipts == {}
    on_disk_after = json.loads(
        (_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
    # The gated old writer saved the pre-report state: no manual report row and
    # no receipt may be on the disk (updated_at moves on every save by design).
    assert [item.get("taskId") for item in on_disk_after["queue_pending"]] == []
    assert on_disk_after["manual_report_receipts"] == {}
    _cleanup()


def test_manual_report_cancelled_failed_write_leaves_no_candidate(monkeypatch, _no_real_recovery_spawn):
    """Cancel the caller while the real writer is failing: the ticket rolls the
    candidate back before retiring and the disk re-read holds nothing."""
    _cleanup()
    real_save_body = _sess._save_body
    calls = {"n": 0}

    def _slow_failing_first_write(session, *args, **kwargs):
        if session.id == "ses_mgr":
            calls["n"] += 1
            if calls["n"] == 1:
                time.sleep(0.3)
                raise OSError("write failed")
        return real_save_body(session, *args, **kwargs)

    _, records = _child([("task-1", 1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")
    asyncio.run(_sess.save_async(mgr))
    on_disk_before = json.loads(
        (_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
    # Inject only after the baseline save so the real writer runs the attempt.
    monkeypatch.setattr(_sess, "_save_body", _slow_failing_first_write)

    async def scenario():
        task = asyncio.create_task(_report(message_id=tail))
        await asyncio.sleep(0.05)  # the writer is running when the request dies
        task.cancel()
        return await task

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "persist_failed"
    assert mgr.queue_pending == [] and mgr.manual_report_receipts == {}
    on_disk_after = json.loads(
        (_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
    assert on_disk_after == on_disk_before,         "an uncommitted cancelled attempt must not reach the disk"
    _cleanup()


def test_manual_report_receipts_bounded(monkeypatch):
    _cleanup()
    monkeypatch.setattr(_sess, "_save_body", lambda session, *args, **kwargs: None)
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


# ── any completion: plain conversations without a taskId ──

def _append_plain_run(s, task_seq, answers, *, terminal=True, status="done",
                      result=None, result_cursor=None) -> dict | None:
    """Append one plain (no taskId) conversation run.

    Same shape as ``_append_run`` but for a run that never had a formal assign:
    the user row carries no taskId, and the terminal record's taskId is None
    with ``terminalKey`` taking the production ``seq:<taskSeq>`` form.  Plain
    runs are exactly the durable rows ``_persist_terminal_state`` writes for
    browser chat / native turns, so the report path must resolve them without
    ever falling back to "whatever terminal is newest".
    """
    _sess.append_history(s, {"role": "user", "content": f"plain message {task_seq}",
                             "source": "user"})
    for answer in answers:
        _sess.append_history(s, {"role": "assistant", "content": answer})
    if not terminal:
        return None
    body = result if result is not None else (answers[-1] if answers else "done")
    _sess.append_history(s, {"role": "assistant", "content": body})
    record = {
        "status": status,
        "result": body,
        "taskId": None,
        "taskSeq": task_seq,
        "workerId": "worker-1",
        "resultCursor": task_seq if result_cursor is None else result_cursor,
        "terminalKey": f"seq:{task_seq}",
        "historyEpoch": s.history_epoch,
        "historyRevision": s.history_revision,
    }
    s.terminal_results = list(s.terminal_results) + [record]
    s.last_result = dict(record)
    record["tail_id"] = s.history[-1]["messageId"]
    return record


def _plain_child(runs, manager=True, sid="ses_child"):
    """A managed Session whose runs are plain conversations (no taskId).

    ``runs`` is a list of (task_seq, answers) tuples.
    """
    s = _setup_session(sid, managed_by="ses_mgr" if manager else None)
    records = [_append_plain_run(s, task_seq, answers)
               for task_seq, answers in runs]
    return s, records


def _report_plain(sid="ses_child", task_seq=None, message_id="pan:tail",
                  result_cursor=None):
    """Report one plain (no taskId) run through the real delivery function."""
    return worker.enqueue_manual_report(sid, None, task_seq, message_id,
                                        result_cursor)


def test_manual_report_plain_conversation_delivers_to_manager(monkeypatch, _no_real_recovery_spawn):
    """A completed plain answer is reportable: no taskId anywhere in the chain,
    the run's own durable row supplies status/result, and the receipt is keyed
    on the run identity (taskId absent, taskSeq present)."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    result, error = asyncio.run(_report_plain(task_seq=1, message_id=tail))
    assert error is None
    assert result["alreadyReported"] is False
    assert len(mgr.queue_pending) == 1
    item = mgr.queue_pending[0]
    assert item["taskId"] is None and item["taskSeq"] == 1
    assert item["result"] == "the answer" and item["messageId"] == tail
    assert item["manualReportKey"] == worker.manual_report_key("ses_child", None, 1)
    assert mgr.manual_report_receipts[item["manualReportKey"]]["taskId"] is None
    _cleanup()


def test_manual_report_plain_run_by_result_cursor_only(monkeypatch, _no_real_recovery_spawn):
    """The durable result cursor alone binds an OLDER plain run: resolution
    must not pick the newest plain terminal, and the delivered body is the
    bound run's own."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["first answer"]), (2, ["second answer"])])
    old_tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    result, error = asyncio.run(_report_plain(
        result_cursor=records[0]["resultCursor"], message_id=old_tail))
    assert error is None, f"older plain run must be bindable by cursor: {error}"
    assert result["alreadyReported"] is False
    assert len(mgr.queue_pending) == 1
    assert mgr.queue_pending[0]["result"] == "first answer"
    assert mgr.queue_pending[0]["taskSeq"] == 1
    _cleanup()


def test_manual_report_without_any_run_identity_is_definite_failure(monkeypatch, _no_real_recovery_spawn):
    """No taskId, no taskSeq, no resultCursor: the request carries no run
    identity at all, so it must fail definitely instead of reporting whatever
    terminal happens to be newest."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    result, error = asyncio.run(
        worker.enqueue_manual_report("ses_child", None, None, tail))
    assert result is None
    assert error["code"] == "no_run_identity"
    assert error["retryable"] is False
    assert mgr.queue_pending == [] and mgr.manual_report_receipts == {}
    _cleanup()


def test_manual_report_result_cursor_unknown_is_definite_failure(monkeypatch, _no_real_recovery_spawn):
    """A resultCursor outside the durable window resolves to nothing — never
    to the newest terminal as a substitute."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["first answer"]), (2, ["second answer"])])
    mgr = _setup_session("ses_mgr")

    result, error = asyncio.run(_report_plain(
        result_cursor=99, message_id=records[1]["tail_id"]))
    assert result is None
    assert error["code"] == "no_task_result"
    assert error["retryable"] is False
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_plain_rejects_a_block_of_another_run(monkeypatch, _no_real_recovery_spawn):
    """A plain run's identity with another run's tail block is a definite
    failure — by taskSeq and by resultCursor alike."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["first answer"]), (2, ["second answer"])])
    mgr = _setup_session("ses_mgr")

    _, error = asyncio.run(_report_plain(
        task_seq=2, message_id=records[0]["tail_id"]))
    assert error["code"] == "block_mismatch"
    _, error = asyncio.run(_report_plain(
        result_cursor=records[1]["resultCursor"],
        message_id=records[0]["tail_id"]))
    assert error["code"] == "block_mismatch"
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_result_cursor_conflicting_identity_is_definite_failure(monkeypatch, _no_real_recovery_spawn):
    """When several identities are submitted they must describe ONE run: a
    cursor pointing at the plain run combined with a formal taskId (or another
    run's taskSeq) is refused, never silently resolved to either side."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_child", managed_by="ses_mgr")
    _append_run(s, "task-1", 1, ["formal answer"])
    plain = _append_plain_run(s, 2, ["plain answer"])
    mgr = _setup_session("ses_mgr")

    _, error = asyncio.run(worker.enqueue_manual_report(
        "ses_child", "task-1", None, plain["tail_id"], plain["resultCursor"]))
    assert error["code"] == "run_mismatch"
    _, error = asyncio.run(worker.enqueue_manual_report(
        "ses_child", None, 1, plain["tail_id"], plain["resultCursor"]))
    assert error["code"] == "run_mismatch"
    assert mgr.queue_pending == [] and mgr.manual_report_receipts == {}
    _cleanup()


def test_manual_report_plain_multi_turn_reports_each_run(monkeypatch, _no_real_recovery_spawn):
    """Multi-turn plain conversation: every completed turn stays reportable,
    the same middle run reached by taskSeq and by resultCursor dedups to one
    delivery, and each run carries its own answer."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s, records = _plain_child([
        (1, ["first answer"]), (2, ["second answer"]), (3, ["third answer"])])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        middle_by_seq = await _report_plain(
            task_seq=2, message_id=records[1]["tail_id"])
        middle_by_cursor = await _report_plain(
            result_cursor=records[1]["resultCursor"],
            message_id=records[1]["tail_id"])
        newest = await _report_plain(
            task_seq=3, message_id=records[2]["tail_id"])
        return middle_by_seq, middle_by_cursor, newest

    middle_by_seq, middle_by_cursor, newest = asyncio.run(scenario())
    assert middle_by_seq[1] is None and middle_by_seq[0]["alreadyReported"] is False
    assert middle_by_cursor[1] is None, \
        f"the same run via resultCursor must dedup: {middle_by_cursor[1]}"
    assert middle_by_cursor[0]["alreadyReported"] is True
    assert newest[1] is None and newest[0]["alreadyReported"] is False
    assert [item["result"] for item in mgr.queue_pending] == [
        "second answer", "third answer"]
    assert all(item["taskId"] is None for item in mgr.queue_pending)
    _cleanup()


def test_manual_report_plain_older_while_newest_is_running(monkeypatch, _no_real_recovery_spawn):
    """An older plain answer stays reportable while a newer plain turn is
    still running (no terminal row for it yet)."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s, records = _plain_child([(1, ["old answer"])])
    _append_plain_run(s, 2, ["still running"], terminal=False)
    mgr = _setup_session("ses_mgr")

    result, error = asyncio.run(_report_plain(
        task_seq=1, message_id=records[0]["tail_id"]))
    assert error is None
    assert mgr.queue_pending[0]["result"] == "old answer"
    _cleanup()


def test_manual_report_plain_survives_late_new_terminal(monkeypatch, _no_real_recovery_spawn):
    """A late newer plain terminal must not disturb an older plain run's
    report: the retry stays deduplicated and the newer run reports on its
    own identity."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s, records = _plain_child([(1, ["old answer"])])
    mgr = _setup_session("ses_mgr")
    old_tail = records[0]["tail_id"]

    async def scenario():
        first = await _report_plain(task_seq=1, message_id=old_tail)
        newer = _append_plain_run(s, 2, ["new answer"])
        retry = await _report_plain(task_seq=1, message_id=old_tail)
        second = await _report_plain(
            task_seq=2, message_id=newer["tail_id"])
        return first, retry, second

    first, retry, second = asyncio.run(scenario())
    assert first[0]["alreadyReported"] is False
    assert retry[0]["alreadyReported"] is True
    assert second[0]["alreadyReported"] is False
    assert [item["result"] for item in mgr.queue_pending] == [
        "old answer", "new answer"]
    _cleanup()


def test_manual_report_plain_double_click_delivers_once(monkeypatch, _no_real_recovery_spawn):
    """Concurrent duplicate clicks on one plain answer deliver once."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        return await asyncio.gather(
            _report_plain(task_seq=1, message_id=tail),
            _report_plain(task_seq=1, message_id=tail),
            _report_plain(result_cursor=records[0]["resultCursor"],
                          message_id=tail))

    outcomes = asyncio.run(scenario())
    assert len(mgr.queue_pending) == 1, \
        f"concurrent clicks double-sent: {mgr.queue_pending}"
    assert sum(1 for _, error in outcomes if error is None) == 3
    assert sum(1 for result, _ in outcomes
               if result and result["alreadyReported"]) == 2
    _cleanup()


def test_manual_report_plain_retry_after_consumption_delivers_once(monkeypatch, _no_real_recovery_spawn):
    """After the manager consumed the plain report, the durable receipt still
    proves delivery — including for a retry that switches identity path."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        first = await _report_plain(task_seq=1, message_id=tail)
        mgr.queue_pending.clear()  # the manager's consumer drained the batch
        second = await _report_plain(task_seq=1, message_id=tail)
        by_cursor = await _report_plain(
            result_cursor=records[0]["resultCursor"], message_id=tail)
        return first, second, by_cursor

    first, second, by_cursor = asyncio.run(scenario())
    assert len(mgr.queue_pending) == 0
    assert second[0]["alreadyReported"] is True
    assert by_cursor[0]["alreadyReported"] is True
    assert by_cursor[0]["queueItemId"] == first[0]["queueItemId"]
    receipt = mgr.manual_report_receipts[
        worker.manual_report_key("ses_child", None, 1)]
    assert receipt["messageId"] == tail
    assert receipt["taskId"] is None and receipt["taskSeq"] == 1
    _cleanup()


def test_manual_report_plain_works_after_unsubscribe_without_touching_subscription(monkeypatch, _no_real_recovery_spawn):
    """Subscription independence holds for plain runs too: delivered after
    report_unsubscribe, and the subscription is never modified."""
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    subscribed = asyncio.run(srv.api_report_subscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert subscribed.get("subscribed") is True
    unsubscribed = asyncio.run(srv.api_report_unsubscribe(
        {"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert unsubscribed.get("subscribed") is False

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": None, "taskSeq": 1, "messageId": tail}))
    assert result.get("ok") is True, \
        f"plain manual report must work after unsubscribe: {result}"
    assert len(mgr.queue_pending) == 1
    assert mgr.report_subscriptions == set(), \
        "unsubscribe state must be unchanged"
    _cleanup()


def test_manual_report_plain_and_auto_reports_coexist(monkeypatch, _no_real_recovery_spawn):
    """A subscribed manager receives the automatic plain report and the
    explicit plain report as two distinct items."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["the answer"])])
    mgr = _setup_session("ses_mgr")
    mgr.report_subscriptions = {"ses_child"}

    async def scenario():
        await worker._enqueue_report("ses_child", "done", "auto", None, "worker-0")
        await _report_plain(task_seq=1, message_id=records[0]["tail_id"])

    asyncio.run(scenario())
    assert len(mgr.queue_pending) == 2
    assert mgr.queue_pending[0].get("manual") is None
    assert mgr.queue_pending[1]["manual"] is True
    assert mgr.queue_pending[1]["taskId"] is None
    _cleanup()


def test_manual_report_plain_save_failure_rolls_back(monkeypatch, _no_real_recovery_spawn):
    """The plain path reuses the same ticket: a failing write rolls the
    candidate back (memory and receipts clean) and a retry after the disk
    recovers stays a fresh first attempt."""
    _cleanup()
    real_save_body = _sess._save_body
    calls = {"n": 0}

    def _failing_first_write(session, *args, **kwargs):
        if session.id == "ses_mgr":
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk full")
        return real_save_body(session, *args, **kwargs)

    monkeypatch.setattr(_sess, "_save_body", _failing_first_write)
    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    async def scenario():
        failed = await _report_plain(task_seq=1, message_id=tail)
        # Snapshot right after the failed attempt: nothing may be left behind.
        clean = (list(mgr.queue_pending), dict(mgr.manual_report_receipts))
        retried = await _report_plain(task_seq=1, message_id=tail)
        return failed, clean, retried

    failed, clean, retried = asyncio.run(scenario())
    assert failed[0] is None
    assert failed[1]["code"] == "persist_failed"
    assert failed[1]["retryable"] is True
    assert clean == ([], {}), "a rolled-back attempt must leave no candidate"
    assert retried[1] is None and retried[0]["alreadyReported"] is False
    assert len(mgr.queue_pending) == 1
    _cleanup()


def test_manual_report_plain_cancelled_failed_write_leaves_no_candidate(monkeypatch, _no_real_recovery_spawn):
    """Cancel the caller while the real writer is failing on the plain path:
    the ticket rolls the candidate back before retiring and the disk re-read
    holds nothing."""
    _cleanup()
    real_save_body = _sess._save_body
    calls = {"n": 0}

    def _slow_failing_first_write(session, *args, **kwargs):
        if session.id == "ses_mgr":
            calls["n"] += 1
            if calls["n"] == 1:
                time.sleep(0.3)
                raise OSError("write failed")
        return real_save_body(session, *args, **kwargs)

    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")
    asyncio.run(_sess.save_async(mgr))
    on_disk_before = json.loads(
        (_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
    # Inject only after the baseline save so the real writer runs the attempt.
    monkeypatch.setattr(_sess, "_save_body", _slow_failing_first_write)

    async def scenario():
        task = asyncio.create_task(_report_plain(task_seq=1, message_id=tail))
        await asyncio.sleep(0.05)  # the writer is running when the request dies
        task.cancel()
        return await task

    result, error = asyncio.run(scenario())
    assert result is None
    assert error["code"] == "persist_failed"
    assert mgr.queue_pending == [] and mgr.manual_report_receipts == {}
    on_disk_after = json.loads(
        (_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
    assert on_disk_after == on_disk_before, \
        "an uncommitted cancelled attempt must not reach the disk"
    _cleanup()


def test_manual_report_plain_cold_cache_history_hydrates(monkeypatch, _no_real_recovery_spawn):
    """A cold cache (Session metadata only) hydrates its history for the block
    proof, so a plain answer is still reportable after the history was never
    loaded in this process."""
    _cleanup()
    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    _setup_session("ses_mgr")

    async def scenario():
        await _sess.save_async(_sess.get("ses_child"))
        await _sess.save_async(_sess.get("ses_mgr"))
        _sess._cache.clear()
        # Direct service call: the route's full load is bypassed, so the
        # shallow session must hydrate its own history for the proof.
        return await _report_plain(task_seq=1, message_id=tail)

    result, error = asyncio.run(scenario())
    assert error is None and result["alreadyReported"] is False
    assert _sess.get("ses_mgr").queue_pending[0]["result"] == "the answer"
    _cleanup()


def test_manual_report_plain_per_run_dedup_survives_cold_reload(monkeypatch, _no_real_recovery_spawn):
    """Real durable write into the isolated data root, then a cold reload:
    plain-run rows and receipts are on disk, a retry (by taskSeq or by
    resultCursor) of an older run stays deduplicated, and a newer run reports
    independently."""
    import packages.web.server as srv
    _cleanup()
    s, records = _plain_child([(1, ["old answer"]), (2, ["new answer"])])
    old_tail, new_tail = records[0]["tail_id"], records[1]["tail_id"]
    _setup_session("ses_mgr")

    async def scenario():
        await _sess.save_async(_sess.get("ses_child"))
        await _sess.save_async(_sess.get("ses_mgr"))
        older = await srv.api_session_manual_report(
            "ses_child", {"taskId": None, "taskSeq": 1, "messageId": old_tail})
        on_disk = json.loads(
            (_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
        # Cold load: drop every in-memory Session and read the manager from disk.
        _sess._cache.clear()
        reloaded = _sess.get("ses_mgr")
        older_by_cursor = await srv.api_session_manual_report(
            "ses_child", {"resultCursor": records[0]["resultCursor"],
                          "messageId": old_tail})
        older_retry = await srv.api_session_manual_report(
            "ses_child", {"taskId": None, "taskSeq": 1, "messageId": old_tail})
        newer = await srv.api_session_manual_report(
            "ses_child", {"taskId": None, "taskSeq": 2, "messageId": new_tail})
        return older, on_disk, reloaded, older_by_cursor, older_retry, newer

    (older, on_disk, reloaded, older_by_cursor,
     older_retry, newer) = asyncio.run(scenario())

    assert older.get("ok") is True and older.get("alreadyReported") is False
    rows = {item.get("taskSeq"): item for item in on_disk["queue_pending"]
            if item.get("manualReportKey")}
    assert set(rows) == {1}
    assert rows[1]["result"] == "old answer"
    assert rows[1]["taskId"] is None
    assert rows[1]["messageId"] == old_tail
    assert worker.manual_report_key("ses_child", None, 1) \
        in on_disk["manual_report_receipts"]

    assert reloaded is not None
    assert worker.manual_report_key("ses_child", None, 1) \
        in reloaded.manual_report_receipts
    assert older_by_cursor.get("alreadyReported") is True, \
        "the same run reached by resultCursor must dedup after a cold reload"
    assert older_retry.get("alreadyReported") is True
    assert newer.get("ok") is True and newer.get("alreadyReported") is False
    manual_rows = {item["taskSeq"]: item for item in reloaded.queue_pending
                   if item.get("manualReportKey")}
    assert set(manual_rows) == {1, 2}, \
        "a retry after a cold reload must not double-deliver, and each run keeps its own row"
    assert manual_rows[2]["result"] == "new answer"
    _cleanup()


# ── durable cursor resolution (no taskId) ──

def test_durable_terminal_by_cursor_resolves_exact_run():
    _cleanup()
    s, records = _plain_child([(1, ["first"]), (2, ["second"])])
    row = worker.durable_terminal_by_cursor(s, records[0]["resultCursor"])
    assert row is not None and row["result"] == "first"
    assert worker.durable_terminal_by_cursor(
        s, records[1]["resultCursor"])["result"] == "second"
    assert worker.durable_terminal_by_cursor(s, 99) is None
    _cleanup()


def test_durable_terminal_by_cursor_legacy_compat_and_rejections():
    """Legacy rows carry no resultCursor: they resolve through the same
    compatibility cursor the identity view exposes (taskSeq).  Non-cursor
    values and an ambiguous compatibility cursor resolve to nothing rather
    than guessing."""
    _cleanup()
    s = _setup_session("ses_child", managed_by="ses_mgr")
    legacy = {"status": "done", "result": "legacy", "taskId": None,
              "taskSeq": 7, "terminalKey": "seq:7"}
    s.terminal_results = [dict(legacy)]
    s.last_result = dict(legacy)
    row = worker.durable_terminal_by_cursor(s, 7)
    assert row is not None and row["result"] == "legacy"
    assert worker.durable_terminal_by_cursor(s, True) is None
    assert worker.durable_terminal_by_cursor(s, -1) is None
    assert worker.durable_terminal_by_cursor(s, "7") is None
    # Two rows sharing one compatibility cursor is ambiguous: refuse.
    s.terminal_results = [dict(legacy), dict(legacy)]
    assert worker.durable_terminal_by_cursor(s, 7) is None
    _cleanup()


def test_durable_terminal_by_cursor_last_result_fallback():
    _cleanup()
    s = _setup_session("ses_child", managed_by="ses_mgr")
    s.terminal_results = []
    s.last_result = {"status": "done", "result": "only", "taskId": None,
                     "taskSeq": 3, "resultCursor": 3}
    row = worker.durable_terminal_by_cursor(s, 3)
    assert row is not None and row["result"] == "only"
    assert worker.durable_terminal_by_cursor(s, 4) is None
    _cleanup()


def test_manual_report_key_run_identity_paths():
    """One run keeps one key no matter which durable identity reaches it."""
    assert worker.manual_report_key("ses", "task-1", 1) == "manual:ses:task-1:1"
    assert worker.manual_report_key("ses", None, 1) == "manual:ses:-:1"
    assert worker.manual_report_key("ses", None, None) == "manual:ses:-:-"
    # A run without a taskSeq is keyed by its durable result cursor.
    assert worker.manual_report_key("ses", None, None, 5) == "manual:ses:-:rc:5"
    assert worker.manual_report_key("ses", "task-1", None, 5) == \
        "manual:ses:task-1:rc:5"
    # The cursor never overrides a present taskSeq: both identity paths of
    # one run derive the same key.
    assert worker.manual_report_key("ses", None, 1, 9) == \
        worker.manual_report_key("ses", None, 1)


# ── HTTP endpoint: any completion ──

def test_manual_report_endpoint_plain_conversation_ok(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": None, "taskSeq": 1, "messageId": tail}))
    assert result.get("ok") is True
    assert result.get("managerId") == "ses_mgr"
    assert result.get("alreadyReported") is False
    assert isinstance(result.get("queueItemId"), str)
    assert len(mgr.queue_pending) == 1
    assert mgr.queue_pending[0]["taskId"] is None

    replay = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": None, "taskSeq": 1, "messageId": tail}))
    assert replay.get("ok") is True
    assert replay.get("alreadyReported") is True
    assert len(mgr.queue_pending) == 1
    _cleanup()


def test_manual_report_endpoint_plain_by_result_cursor(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["old answer"]), (2, ["new answer"])])
    old_tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"resultCursor": records[0]["resultCursor"],
                      "messageId": old_tail}))
    assert result.get("ok") is True, f"cursor identity must be accepted: {result}"
    assert result.get("alreadyReported") is False
    assert len(mgr.queue_pending) == 1
    assert mgr.queue_pending[0]["result"] == "old answer"
    _cleanup()


def test_manual_report_endpoint_rejects_identity_less_body(monkeypatch):
    """A body that names no run at all is a definite failure even when the
    session has plain terminals: the endpoint must not report the newest."""
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"messageId": tail}))
    assert result.get("ok") is False
    assert result["error"]["code"] == "no_run_identity"
    assert result.get("retryable") is False
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_endpoint_rejects_bad_result_cursor(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    for bad in ("1", -1, True, 1.5, []):
        result = asyncio.run(srv.api_session_manual_report(
            "ses_child", {"taskSeq": 1, "resultCursor": bad,
                          "messageId": tail}))
        assert result.get("ok") is False, f"resultCursor {bad!r} must be rejected"
        assert result["error"]["code"] == "invalid_result_cursor"
    assert mgr.queue_pending == [], "rejected bodies must not deliver"
    _cleanup()


def test_manual_report_endpoint_result_cursor_conflict_is_run_mismatch(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_child", managed_by="ses_mgr")
    _append_run(s, "task-1", 1, ["formal answer"])
    plain = _append_plain_run(s, 2, ["plain answer"])
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"taskId": "task-1",
                      "resultCursor": plain["resultCursor"],
                      "messageId": plain["tail_id"]}))
    assert result.get("ok") is False
    assert result["error"]["code"] == "run_mismatch"
    assert result.get("retryable") is False
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_endpoint_unknown_result_cursor(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, records = _plain_child([(1, ["the answer"])])
    tail = records[0]["tail_id"]
    mgr = _setup_session("ses_mgr")

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child", {"resultCursor": 99, "messageId": tail}))
    assert result.get("ok") is False
    assert result["error"]["code"] == "no_task_result"
    assert result.get("retryable") is False
    assert mgr.queue_pending == []
    _cleanup()


def test_session_summary_plain_identity_is_accepted_by_report_route(monkeypatch):
    """The identity view exposes no-taskId runs (taskId null, resultCursor
    present) and the report route accepts exactly that identity — by
    (taskId, taskSeq) and by resultCursor alone, deduplicated to one run."""
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s, records = _plain_child([(1, ["old answer"]), (2, ["new answer"])])
    _setup_session("ses_mgr")
    rows = srv._session_summary(s, {})["terminalResults"]
    assert [row["taskId"] for row in rows] == [None, None]
    older = rows[0]
    assert older["resultCursor"] == records[0]["resultCursor"]
    assert older["taskSeq"] == 1

    result = asyncio.run(srv.api_session_manual_report(
        "ses_child",
        {"taskId": older["taskId"], "taskSeq": older["taskSeq"],
         "messageId": records[0]["tail_id"]},
    ))
    assert result.get("ok") is True, f"plain identity must be accepted: {result}"
    by_cursor = asyncio.run(srv.api_session_manual_report(
        "ses_child",
        {"resultCursor": older["resultCursor"],
         "messageId": records[0]["tail_id"]},
    ))
    assert by_cursor.get("ok") is True
    assert by_cursor.get("alreadyReported") is True
    _cleanup()


# ── derived zero / identity-less terminals (narrow fix) ──

def _append_identityless_done(s, content, *, raw_cursor=None) -> dict:
    """Append a legacy done run that carries NO provable durable identity.

    ``taskId`` and ``taskSeq`` are absent; when ``raw_cursor`` is None the row
    has no ``resultCursor`` field either, so the identity view's derived
    compatibility cursor is 0 — a value production never allocates.  Such rows
    must never become a reportable run through the derived 0.
    """
    _sess.append_history(s, {"role": "user", "content": f"legacy {content}",
                             "source": "user"})
    _sess.append_history(s, {"role": "assistant", "content": content})
    _sess.append_history(s, {"role": "assistant", "content": content})
    record = {
        "status": "done",
        "result": content,
        "taskId": None,
        "workerId": "worker-1",
        "terminalKey": "seq:0",
        "historyEpoch": s.history_epoch,
        "historyRevision": s.history_revision,
    }
    if raw_cursor is not None:
        record["resultCursor"] = raw_cursor
    s.terminal_results = list(s.terminal_results) + [record]
    s.last_result = dict(record)
    record["tail_id"] = s.history[-1]["messageId"]
    return record


def test_manual_report_identity_less_done_by_derived_cursor_zero_is_refused(monkeypatch, _no_real_recovery_spawn):
    """A sole legacy done row without taskId/taskSeq/resultCursor is not a
    provable run: cursor 0 (the derived compat value) must not resolve it as a
    deliverable run, and no degenerate ``-:-`` receipt may be written."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_child", managed_by="ses_mgr")
    legacy = _append_identityless_done(s, "旧回答")
    mgr = _setup_session("ses_mgr")

    result, error = asyncio.run(_report_plain(
        result_cursor=0, message_id=legacy["tail_id"]))
    assert result is None
    assert error["code"] == "no_run_identity"
    assert error["retryable"] is False
    assert mgr.queue_pending == []
    assert mgr.manual_report_receipts == {}, \
        "an identity-less row must not mint a degenerate receipt"
    _cleanup()


def test_durable_terminal_by_cursor_never_derives_zero_identity():
    """The compatibility cursor derives only from a stored taskSeq: a row with
    no stored identity is never matched by cursor 0, while the legacy taskSeq
    compatibility itself keeps resolving."""
    _cleanup()
    s = _setup_session("ses_child", managed_by="ses_mgr")
    _append_identityless_done(s, "旧回答")
    assert worker.durable_terminal_by_cursor(s, 0) is None, \
        "derived 0 must not bind an identity-less row"
    # Legacy last_result without any stored identity: same refusal.
    s2 = _setup_session("ses_child_2", managed_by="ses_mgr")
    _append_identityless_done(s2, "旧回答")
    s2.terminal_results = []
    assert worker.durable_terminal_by_cursor(s2, 0) is None
    # A legacy row WITH a stored taskSeq still resolves through the compat
    # cursor the identity view exposes for it.
    s3 = _setup_session("ses_child_3", managed_by="ses_mgr")
    _sess.append_history(s3, {"role": "assistant", "content": "legacy seven"})
    s3.terminal_results = [{
        "status": "done", "result": "legacy seven", "taskId": None,
        "taskSeq": 7, "terminalKey": "seq:7",
    }]
    s3.last_result = dict(s3.terminal_results[0])
    row = worker.durable_terminal_by_cursor(s3, 7)
    assert row is not None and row["result"] == "legacy seven"
    assert worker.durable_terminal_by_cursor(s3, 0) is None
    _cleanup()


def test_manual_report_evicted_identity_less_run_cannot_swallow_a_new_one(monkeypatch, _no_real_recovery_spawn):
    """The cross-time hazard: run A was delivered under the degenerate
    ``-:-`` key in the past; A is evicted and a NEW identity-less run B is the
    only surviving row.  B must be refused as identity-less — never answered
    ``alreadyReported`` from A's surviving receipt."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_child", managed_by="ses_mgr")
    b = _append_identityless_done(s, "淘汰回答")
    mgr = _setup_session("ses_mgr")
    # The surviving receipt of the past delivery of A (as a real store keeps it).
    mgr.manual_report_receipts["manual:ses_child:-:-"] = {
        "sessionId": "ses_child", "taskId": None, "taskSeq": None,
        "messageId": "pan:evicted-a-tail", "queueItemId": "q_evicted_a",
        "at": 0.0,
    }

    result, error = asyncio.run(_report_plain(
        result_cursor=0, message_id=b["tail_id"]))
    assert result is None, \
        "the new identity-less run must not be answered from the old receipt"
    assert error["code"] == "no_run_identity"
    assert error["retryable"] is False
    assert mgr.queue_pending == [], "nothing may be enqueued for B"
    assert mgr.manual_report_receipts["manual:ses_child:-:-"]["messageId"] \
        == "pan:evicted-a-tail", "the surviving receipt must stay untouched"
    _cleanup()


def test_manual_report_derived_zero_and_raw_zero_are_refused(monkeypatch, _no_real_recovery_spawn):
    """Derived 0 stays refused with several identity-less rows, and a raw
    resultCursor 0 is not a legal identity either (production only allocates
    positive cursors): every zero-cursor request is no_run_identity."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    # Several identity-less rows: still refused (previously no_task_result /
    # delivery under a fabricated pin would both be wrong here).
    s = _setup_session("ses_child", managed_by="ses_mgr")
    first = _append_identityless_done(s, "旧回答一")
    _append_identityless_done(s, "旧回答二")
    mgr = _setup_session("ses_mgr")
    result, error = asyncio.run(_report_plain(
        result_cursor=0, message_id=first["tail_id"]))
    assert result is None and error["code"] == "no_run_identity"

    # A raw-0 row (the exact-first branch of the old resolver): 0 is still not
    # a legal identity, and the resolver itself must not pin it.
    s2 = _setup_session("ses_child_2", managed_by="ses_mgr")
    raw = _append_identityless_done(s2, "零游标回答", raw_cursor=0)
    assert worker.durable_terminal_by_cursor(s2, 0) is None
    result, error = asyncio.run(_report_plain(
        sid="ses_child_2", result_cursor=0, message_id=raw["tail_id"]))
    assert result is None and error["code"] == "no_run_identity"
    assert mgr.queue_pending == []
    _cleanup()


def test_manual_report_legal_run_identities_are_untouched(monkeypatch, _no_real_recovery_spawn):
    """The gate must not over-tighten: legal taskSeq, positive resultCursor
    and the legacy taskId-only contract all keep resolving and delivering."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_child", managed_by="ses_mgr")
    plain = _append_plain_run(s, 1, ["legal answer"])
    legacy_seq = _append_identityless_done(s, "legacy seq answer")
    # Give the legacy-seq row its stored taskSeq identity (no raw cursor).
    legacy_seq["taskSeq"] = 7
    s.terminal_results[-1]["taskSeq"] = 7
    s.last_result["taskSeq"] = 7
    formal = _append_run(s, "task-legacy", None, ["formal legacy"])
    mgr = _setup_session("ses_mgr")

    async def scenario():
        by_seq = await _report_plain(task_seq=1, message_id=plain["tail_id"])
        by_cursor = await _report_plain(
            result_cursor=plain["resultCursor"], message_id=plain["tail_id"])
        legacy = await _report_plain(task_seq=7, message_id=legacy_seq["tail_id"])
        task_only = await worker.enqueue_manual_report(
            "ses_child", "task-legacy", None, formal["tail_id"])
        return by_seq, by_cursor, legacy, task_only

    by_seq, by_cursor, legacy, task_only = asyncio.run(scenario())
    assert by_seq[1] is None and by_seq[0]["alreadyReported"] is False
    assert by_cursor[1] is None and by_cursor[0]["alreadyReported"] is True
    assert legacy[1] is None and legacy[0]["alreadyReported"] is False
    assert task_only[1] is None and task_only[0]["alreadyReported"] is False
    assert [item["result"] for item in mgr.queue_pending] == [
        "legal answer", "legacy seq answer", "formal legacy"]
    assert mgr.manual_report_receipts[
        worker.manual_report_key("ses_child", None, 7)]["taskSeq"] == 7
    assert mgr.manual_report_receipts[
        worker.manual_report_key("ses_child", "task-legacy", None)]["taskId"] \
        == "task-legacy"
    _cleanup()


def test_manual_report_real_http_derived_zero_refused_and_receipt_persists(monkeypatch, _no_real_recovery_spawn):
    """Real HTTP through the ASGI route with real durable writes: the derived-0
    request is refused as identity-less, a legal plain run is delivered, its
    receipt survives a cold reload, and the replay dedups across identity
    paths."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import packages.web.server as server
    _cleanup()
    s, records = _plain_child([(1, ["legal answer"])])
    legal_tail = records[0]["tail_id"]
    legacy = _append_identityless_done(s, "legacy answer")
    _setup_session("ses_mgr")

    app = FastAPI()
    app.add_api_route("/api/sessions/{session_id}/manual-report",
                      server.api_session_manual_report, methods=["POST"])
    asyncio.run(_sess.save_async(_sess.get("ses_child")))
    with TestClient(app) as client:
        refused = client.post("/api/sessions/ses_child/manual-report",
                              json={"resultCursor": 0,
                                    "messageId": legacy["tail_id"]})
        assert refused.status_code == 200
        body = refused.json()
        assert body["ok"] is False, f"derived 0 must be refused over HTTP: {body}"
        assert body["error"]["code"] == "no_run_identity"
        assert body.get("retryable") is False

        delivered = client.post("/api/sessions/ses_child/manual-report",
                                json={"taskId": None, "taskSeq": 1,
                                      "messageId": legal_tail})
        assert delivered.json()["ok"] is True

        on_disk = json.loads(
            (_sess.SESSION_DIR / "ses_mgr.json").read_text(encoding="utf-8"))
        assert worker.manual_report_key("ses_child", None, 1) \
            in on_disk["manual_report_receipts"]
        assert "manual:ses_child:-:-" not in on_disk["manual_report_receipts"], \
            "no degenerate receipt may reach the disk"

        # Cold reload: the receipt is durable and the cursor-path replay dedups.
        _sess._cache.clear()
        replay = client.post("/api/sessions/ses_child/manual-report",
                             json={"resultCursor": records[0]["resultCursor"],
                                   "messageId": legal_tail})
        assert replay.json()["ok"] is True
        assert replay.json()["alreadyReported"] is True
    _cleanup()


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("\n=== ALL MANUAL TASK REPORT TESTS PASSED ===")
