"""Error completion reports must carry the run's own last useful message.

Regression shape (2026-10-16): a worker turn ended with ``status=error`` and an
empty ``result`` while the session history's final assistant message held the
real diagnosis (an explicit 429 with the reset time).  The subscription report
delivered to the manager (MA) lost that message, so the coordinator only saw
``result:`` empty.

These tests drive the real callback chains — stream ``_read_stdout``, one-shot
``_consumer_oneshot``, zombie/report enqueue, queue delivery into the manager
message — and pin:

- an error report captures the current run's last non-empty assistant text,
  annotated with its role and truncation state;
- capture is run-scoped: queued/stale text from an earlier turn, or an earlier
  session-history tail, is never guessed into a later error report;
- an existing error result is preserved and the context is added next to it;
- no captured message renders an explicit "(no diagnostics available)";
- over-long text stays bounded while keeping both ends (429 reason + reset);
- report identity/receipts do not depend on the context fields (no duplicates);
- success/cancelled bodies stay byte-identical;
- other confirmed error exits (zombie while running, spawn failure) attach the
  same run-scoped context;
- cold reload of the manager session keeps the context in the queue row.

All stores stay in pytest tmp roots (conftest); no provider process is spawned
(create_worker is stubbed, no real sockets/ports are used).
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


RATE_LIMIT_TEXT = (
    "429 Too Many Requests: rate limit reached for model glm-5.3-flash. "
    "Please retry after 2026-10-17T05:00:00+08:00."
)


@pytest.fixture(autouse=True)
def _no_real_recovery_spawn(monkeypatch):
    """Never spawn a provider process for the manager wake-up path."""
    spawned = []

    async def _no_spawn(session_id):
        spawned.append(session_id)
        return "spawn suppressed by test fixture"

    monkeypatch.setattr(worker, "create_worker", _no_spawn)
    # Completion notifications are unrelated to report content; keep OS sends out.
    monkeypatch.setattr(
        worker._notifications, "dispatch_completion_nonblocking",
        lambda *args, **kwargs: None,
    )
    yield spawned


def _cleanup():
    worker.workers.clear()
    worker._workers_by_session.clear()
    worker._task_status.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid="ses_test", **kwargs):
    s = _sess.Session(id=sid, name="test", **kwargs)
    _sess._cache[sid] = s
    return s


def _setup_managed_pair(subscribe=True):
    child = _setup_session("ses_child", managed_by="ses_mgr")
    mgr = _setup_session("ses_mgr")
    if subscribe:
        mgr.report_subscriptions = {"ses_child"}
    mw = worker.Worker(
        worker_id="worker-mgr", session_id="ses_mgr",
        adapter=CbcAdapter(), status="idle", process=None,
        pending_signal=asyncio.Queue(),
    )
    worker.workers[mw.worker_id] = mw
    return child, mgr, mw


def _assistant_event(text):
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _event_line(event):
    return (json.dumps(event) + "\n").encode("utf-8")


class _StreamProcess:
    """Minimal stdout mock for ``_iter_stdout_lines`` (one event per read)."""

    def __init__(self, events, returncode=None):
        self._events = list(events)
        self.returncode = returncode
        self.pid = 1000
        self.stdin = AsyncMock()
        self.stdout = self

    async def read(self, n=-1):
        if self._events:
            return self._events.pop(0)
        return b""

    async def readline(self):
        return await self.read()


def _run_stream_worker(child, events, *, seq, task_id="task-1",
                       status="running", worker_id="worker-child"):
    w = worker.Worker(
        worker_id=worker_id, session_id=child.id,
        adapter=CbcAdapter(), status=status,
        process=_StreamProcess(events), pending_signal=asyncio.Queue(),
        _replaying=False,
    )
    w._current_seq = seq
    w._current_task_id = task_id
    worker.workers[w.worker_id] = w
    async def run():
        # This fixture simulates an admitted MA execution. Production freezes
        # that origin at handoff; stdout/history alone cannot establish it.
        await worker._capture_auto_report(w, [{"source": "agent"}])
        await worker._read_stdout(w)
    asyncio.run(run())
    return w


class _OneShotProcess:
    """stdout/read/wait/kill mock for ``_consumer_oneshot`` collection loop."""

    def __init__(self, output: bytes, returncode=0):
        self._output = output
        self._offset = 0
        self.returncode = returncode
        self.pid = 2000
        outer = self

        class _Stdout:
            async def read(self, n=-1):
                if outer._offset >= len(outer._output):
                    return b""
                end = len(outer._output) if n is None or n < 0 else outer._offset + n
                chunk = outer._output[outer._offset:end]
                outer._offset += len(chunk)
                return chunk

        self.stdout = _Stdout()

    async def wait(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _run_oneshot_worker(monkeypatch, child, output, *, seq,
                        task_id="task-oneshot", worker_id="worker-child"):
    proc = _OneShotProcess(output)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec",
                        fake_create_subprocess_exec)
    w = worker.Worker(
        worker_id=worker_id, session_id=child.id,
        adapter=CbcAdapter(), status="idle", process=None,
        pending_signal=asyncio.Queue(), _replaying=False,
    )
    w._current_seq = seq
    w._current_task_id = task_id
    worker.workers[w.worker_id] = w
    asyncio.run(worker._consumer_oneshot(w, "prompt", "user", child))
    return w


def _deliver_reports_to_manager(monkeypatch, mw):
    """Run the real queue delivery with a captured manager message body."""
    received = []

    async def fake_stream(ww, text, source, s, *, on_handoff):
        assert await on_handoff()
        received.append((text, source))
        worker._ack_current_reports(ww, s)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await mw.pending_signal.put({"type": "report_signal"})
        await mw.pending_signal.put(None)
        await worker._consumer(mw)

    asyncio.run(scenario())
    return received


# ── core incident shape: stream chain (empty error result + 429 message) ──

def test_stream_error_empty_result_report_carries_429_and_reset_time(
        monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    _run_stream_worker(child, [
        _event_line(_assistant_event(RATE_LIMIT_TEXT)),
        _event_line({"type": "result", "is_error": True}),
    ], seq=7, task_id="task-429")

    assert len(mgr.queue_pending) == 1, f"report missing: {mgr.queue_pending}"
    r = mgr.queue_pending[0]
    assert r["status"] == "error"
    assert r["result"] == ""
    assert r["lastMessageRole"] == "assistant"
    assert "429" in r["lastMessage"] and "2026-10-17T05:00:00" in r["lastMessage"]
    assert r["lastMessageTruncated"] is False
    # Terminal model itself is unchanged: the raw result stays empty.
    assert child.last_result["status"] == "error"
    assert child.last_result["result"] == ""

    body = worker._format_report_batch([dict(r)])
    assert "status: error" in body
    assert "lastMessageRole: assistant" in body
    assert "429 Too Many Requests" in body
    assert "2026-10-17T05:00:00" in body
    _cleanup()


def test_error_report_visible_in_manager_message_after_delivery(
        monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    _run_stream_worker(child, [
        _event_line(_assistant_event(RATE_LIMIT_TEXT)),
        _event_line({"type": "result", "is_error": True}),
    ], seq=7, task_id="task-429")

    received = _deliver_reports_to_manager(monkeypatch, mw)

    assert len(received) == 1, f"expected one manager message, got {received}"
    text, source = received[0]
    assert source == "report"
    assert "429 Too Many Requests" in text, text
    assert "2026-10-17T05:00:00" in text, text
    assert "lastMessageRole: assistant" in text, text
    # The injected MA history entry carries the same diagnostic.
    injected = [h for h in mgr.history if h.get("source") == "report"]
    assert len(injected) == 1
    assert "429 Too Many Requests" in injected[0]["content"]
    assert mgr.queue_pending == []
    _cleanup()


# ── core incident shape: one-shot chain ──

@pytest.mark.skip(reason="Oneshot mode retired by user; implementation retained")
def test_oneshot_error_empty_result_report_carries_last_assistant_message(
        monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    output = (
        _event_line(_assistant_event(RATE_LIMIT_TEXT))
        + _event_line({"type": "result", "is_error": True})
    )
    _run_oneshot_worker(monkeypatch, child, output, seq=12)

    assert len(mgr.queue_pending) == 1, f"report missing: {mgr.queue_pending}"
    r = mgr.queue_pending[0]
    assert r["status"] == "error"
    # No provider text at all still reports the failure honestly (no fake success).
    assert r["result"] == "(no output)"
    assert "429" in r["lastMessage"]
    assert "2026-10-17T05:00:00" in r["lastMessage"]
    assert r["lastMessageRole"] == "assistant"
    _cleanup()


@pytest.mark.skip(reason="Oneshot mode retired by user; implementation retained")
def test_error_report_keeps_existing_error_result_and_adds_context(
        monkeypatch, _no_real_recovery_spawn):
    """已有 error 正文时，原错误原因保留，最后消息作为上下文附加。"""
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    output = (
        _event_line(_assistant_event(RATE_LIMIT_TEXT))
        + _event_line({"type": "result", "is_error": True,
                       "result": "429 quota exceeded"})
    )
    _run_oneshot_worker(monkeypatch, child, output, seq=13)

    r = mgr.queue_pending[0]
    assert r["status"] == "error"
    assert r["result"] == "429 quota exceeded"  # 原错误原因不被覆盖
    assert "2026-10-17T05:00:00" in r["lastMessage"]  # 追加的上下文保留重置时间
    body = worker._format_report_batch([dict(r)])
    assert "result:\n429 quota exceeded" in body
    assert "2026-10-17T05:00:00" in body
    _cleanup()


def test_error_report_attaches_context_even_when_result_equals_message(
        monkeypatch, _no_real_recovery_spawn):
    """即使 result 已含同一错误，仍保留最后消息上下文（不加条件跳过）。"""
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    _run_stream_worker(child, [
        _event_line(_assistant_event(RATE_LIMIT_TEXT)),
        _event_line({"type": "result", "is_error": True,
                     "result": RATE_LIMIT_TEXT}),
    ], seq=8, task_id="task-dup")

    r = mgr.queue_pending[0]
    assert r["result"] == RATE_LIMIT_TEXT
    assert r["lastMessage"] == RATE_LIMIT_TEXT
    assert r["lastMessageRole"] == "assistant"
    _cleanup()


# ── no message / success / cancelled boundaries ──

def test_error_report_without_message_marks_no_diagnostics(
        monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    _run_stream_worker(child, [
        _event_line({"type": "result", "is_error": True}),
    ], seq=9, task_id="task-silent")

    r = mgr.queue_pending[0]
    assert r["status"] == "error"
    assert r["result"] == ""
    assert r.get("lastMessage") is None
    assert r.get("lastMessageRole") is None
    body = worker._format_report_batch([dict(r)])
    assert "lastMessage: (no diagnostics available)" in body
    _cleanup()


def test_error_report_does_not_guess_from_session_history_tail(
        monkeypatch, _no_real_recovery_spawn):
    """Session 历史尾部属于旧 run 时，error 报告不得把它当本轮诊断。"""
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    child.history = [
        {"role": "assistant", "content": "old answer from a previous run",
         "messageId": "pan:old-run"},
    ]
    _run_stream_worker(child, [
        _event_line({"type": "result", "is_error": True, "result": "boom"}),
    ], seq=10, task_id="task-taint")

    r = mgr.queue_pending[0]
    assert r["result"] == "boom"
    assert r.get("lastMessage") is None, (
        "must not guess the session's last assistant row into this run")
    _cleanup()


def test_success_report_body_unchanged(monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    _run_stream_worker(child, [
        _event_line(_assistant_event("the answer")),
        _event_line({"type": "result", "is_error": False, "result": "the answer"}),
    ], seq=11, task_id="task-ok")

    r = mgr.queue_pending[0]
    assert r["status"] == "done"
    for key in ("lastMessage", "lastMessageRole", "lastMessageTruncated"):
        assert key not in r, f"success report must not carry {key}"
    body = worker._format_report_batch([dict(r)])
    assert "lastMessage" not in body
    assert "result:\nthe answer" in body
    _cleanup()


def test_cancelled_report_body_unchanged(monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    _run_stream_worker(child, [
        _event_line(_assistant_event("partial text before cancel")),
        _event_line({"type": "result", "is_error": False, "cancelled": True}),
    ], seq=12, task_id="task-cancel")

    r = mgr.queue_pending[0]
    assert r["status"] == "cancelled"
    for key in ("lastMessage", "lastMessageRole", "lastMessageTruncated"):
        assert key not in r, f"cancelled report must not carry {key}"
    assert "lastMessage" not in worker._format_report_batch([dict(r)])
    _cleanup()


def test_tool_blocks_are_not_captured_as_last_message(
        monkeypatch, _no_real_recovery_spawn):
    """只有 assistant 文本进入上下文：tool_use 的敏感参数不得被倒出。"""
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    tool_event = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{
                "type": "tool_use", "name": "shell",
                "input": {"command": "curl -H 'Authorization: Bearer SECRET-TOKEN'"},
            }],
        },
    }
    _run_stream_worker(child, [
        _event_line(tool_event),
        _event_line({"type": "result", "is_error": True}),
    ], seq=17, task_id="task-tool")

    r = mgr.queue_pending[0]
    assert r["status"] == "error"
    assert r.get("lastMessage") is None
    body = worker._format_report_batch([dict(r)])
    assert "SECRET-TOKEN" not in body
    assert "lastMessage: (no diagnostics available)" in body
    _cleanup()


# ── truncation ──

def test_overlong_last_message_is_bounded_and_marked(
        monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    long_text = (
        "429 quota exceeded for this model."
        + "x" * 4000
        + " reset at 2026-10-17T05:00:00+08:00."
    )
    _run_stream_worker(child, [
        _event_line(_assistant_event(long_text)),
        _event_line({"type": "result", "is_error": True}),
    ], seq=14, task_id="task-long")

    r = mgr.queue_pending[0]
    assert r["lastMessageTruncated"] is True
    assert len(r["lastMessage"]) <= 1300, len(r["lastMessage"])
    assert "truncated" in r["lastMessage"]
    # Both ends survive: the 429 reason at the head and the reset time at the tail.
    assert r["lastMessage"].startswith("429 quota exceeded")
    assert r["lastMessage"].rstrip().endswith("reset at 2026-10-17T05:00:00+08:00.")
    body = worker._format_report_batch([dict(r)])
    assert "lastMessageTruncated: true" in body
    assert "429 quota exceeded" in body
    assert "2026-10-17T05:00:00" in body
    _cleanup()


# ── run-scoped capture across runs/generations ──

def test_interleaved_runs_keep_their_own_frozen_context(
        monkeypatch, _no_real_recovery_spawn):
    """两个 run 的迟到/交错报告各自保留本 run 的消息，不互相串线。"""
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    w1 = _run_stream_worker(child, [
        _event_line(_assistant_event("run A diagnostic: 429 reset 05:00")),
        _event_line({"type": "result", "is_error": True}),
    ], seq=20, task_id="task-A", worker_id="worker-child-a")
    # A later run (next worker generation / object) reuses the same public path.
    _run_stream_worker(child, [
        _event_line(_assistant_event("run B diagnostic: 500 backend error")),
        _event_line({"type": "result", "is_error": True}),
    ], seq=21, task_id="task-B", worker_id="worker-child-b")

    assert len(mgr.queue_pending) == 2
    report_a, report_b = mgr.queue_pending[0], mgr.queue_pending[1]
    assert report_a["taskId"] == "task-A"
    assert "run A diagnostic" in report_a["lastMessage"]
    assert "run B" not in report_a["lastMessage"]
    assert report_b["taskId"] == "task-B"
    assert "run B diagnostic" in report_b["lastMessage"]
    assert "run A" not in report_b["lastMessage"]

    # Frozen snapshot: mutating the worker's in-memory capture afterwards does
    # not rewrite an already-enqueued report (late delivery stays truthful).
    w1._current_task_last_assistant_text = "mutated after enqueue"
    assert "run A diagnostic" in report_a["lastMessage"]
    _cleanup()


def test_queued_zombie_does_not_attach_previous_run_message(
        monkeypatch, _no_real_recovery_spawn):
    """queued 状态没有本 run 输出：不得把上一 run 的遗留文本当诊断。"""
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    w = worker.Worker(
        worker_id="worker-child", session_id=child.id,
        adapter=CbcAdapter(), status="queued", process=None,
        pending_signal=asyncio.Queue(),
    )
    # Stale capture left over from the previous completed run.
    w._current_seq = 30
    w._current_task_last_assistant_text = "previous run answer"
    w._current_task_last_assistant_seq = 30
    worker.workers[w.worker_id] = w

    asyncio.run(worker._enqueue_zombie_report(w, "queued timeout (no output for 300s)"))

    r = mgr.queue_pending[0]
    assert r["result"] == "worker died: queued timeout (no output for 300s)"
    assert r.get("lastMessage") is None
    _cleanup()


# ── other confirmed error exits ──

def test_running_zombie_attaches_current_run_message(
        monkeypatch, _no_real_recovery_spawn):
    """运行中异常死亡（timeout/crash 同类出口）：保留原因并附本 run 最后诊断。"""
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    w = worker.Worker(
        worker_id="worker-child", session_id=child.id,
        adapter=CbcAdapter(), status="running", process=None,
        pending_signal=asyncio.Queue(),
    )
    w._current_seq = 31
    w._current_task_id = "task-zombie"
    w._current_task_last_assistant_text = "partial 429 diagnostic before crash"
    w._current_task_last_assistant_seq = 31
    worker.workers[w.worker_id] = w

    asyncio.run(worker._enqueue_zombie_report(w, "task timeout (running 300s)"))

    r = mgr.queue_pending[0]
    assert r["status"] == "error" and r["type"] == "zombie"
    assert r["result"] == "worker died: task timeout (running 300s)"
    assert r["lastMessage"] == "partial 429 diagnostic before crash"
    assert r["lastMessageRole"] == "assistant"
    _cleanup()


def test_spawn_failure_report_keeps_error_and_marks_no_diagnostics(
        monkeypatch, _no_real_recovery_spawn):
    """适配器/spawn 失败出口：原错误保留，无本 run 消息则明确标注。"""
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    w = worker.Worker(
        worker_id="worker-child", session_id=child.id,
        adapter=CbcAdapter(), status="running", process=None,
        pending_signal=asyncio.Queue(),
    )
    w._current_seq = 32
    w._current_task_id = "task-spawn"
    worker.workers[w.worker_id] = w

    asyncio.run(worker._finish_task_error(
        w, child, "cbc CLI spawn failed: FileNotFoundError"))

    r = mgr.queue_pending[0]
    assert r["status"] == "error"
    assert r["result"] == "cbc CLI spawn failed: FileNotFoundError"
    assert r.get("lastMessage") is None
    assert "lastMessage: (no diagnostics available)" in worker._format_report_batch([dict(r)])
    _cleanup()


def test_worker_process_dead_report_keeps_error_and_marks_no_diagnostics(
        monkeypatch, _no_real_recovery_spawn):
    """进程不可用出口（stream consumer 无进程）：错误保留，无诊断明确标注。"""
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    w = worker.Worker(
        worker_id="worker-child", session_id=child.id,
        adapter=CbcAdapter(), status="queued", process=None,
        pending_signal=asyncio.Queue(),
    )
    w._current_seq = 33
    w._current_task_id = "task-dead"
    worker.workers[w.worker_id] = w

    asyncio.run(worker._consumer_stream(w, "msg", "user", child))

    r = mgr.queue_pending[0]
    assert r["status"] == "error"
    assert "Worker process dead" in r["result"]
    assert r.get("lastMessage") is None
    assert "lastMessage: (no diagnostics available)" in worker._format_report_batch([dict(r)])
    _cleanup()


# ── identity / duplicate receipts unchanged ──

def test_report_context_fields_do_not_change_delivery_identity():
    base = {
        "type": "report", "kind": "report", "id": "q_1", "queueItemId": "q_1",
        "source": "report", "sourceSessionId": "ses_child", "status": "error",
        "result": "", "sessionId": "ses_child", "taskId": "task-1",
        "workerId": "worker-1", "deliveryState": "queued",
        "dispatchState": "queued", "revision": 1, "createdAt": 1.0,
    }
    base_key = worker._delivery_key(dict(base))

    with_fields = dict(base)
    with_fields.update({
        "lastMessageRole": "assistant",
        "lastMessage": RATE_LIMIT_TEXT,
        "lastMessageTruncated": False,
    })
    assert worker._delivery_key(with_fields) == base_key, (
        "context fields must not change report identity/receipt key")

    other_context = dict(base)
    other_context.update({
        "lastMessageRole": "assistant",
        "lastMessage": "different diagnostic",
        "lastMessageTruncated": True,
    })
    assert worker._delivery_key(other_context) == base_key

    # A receipt written for the plain row still matches the context-bearing row:
    # delivery reconciliation must not replay the same report as a duplicate.
    s = _sess.Session(id="ses_mgr", name="mgr")
    s.history = [{"role": "user", "content": "report",
                  "delivered_keys": [base_key]}]
    assert worker._delivery_mark_in_history(s, with_fields) is True


# ── cold reload of the manager session keeps the context ──

def test_manager_cold_reload_keeps_report_context(
        monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    _run_stream_worker(child, [
        _event_line(_assistant_event(RATE_LIMIT_TEXT)),
        _event_line({"type": "result", "is_error": True}),
    ], seq=15, task_id="task-cold")

    # Drop the in-memory manager and reload it from the tmp disk store.
    _sess._cache.clear()
    reloaded = _sess.get("ses_mgr", load_history=False)
    assert reloaded is not None
    assert len(reloaded.queue_pending) == 1
    r = reloaded.queue_pending[0]
    assert "429" in r["lastMessage"]
    body = worker._format_report_batch([dict(r)])
    assert "429 Too Many Requests" in body
    assert "2026-10-17T05:00:00" in body
    # The child session's terminal shape survives reload too (status stays error).
    child_reloaded = _sess.get("ses_child", load_history=False)
    assert child_reloaded.last_result["status"] == "error"
    _cleanup()


# ── Pause / Stop reports classification unchanged ──

def test_paused_manager_still_marks_error_report_pauseable(
        monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    child, mgr, mw = _setup_managed_pair()
    mgr.agent_reports_paused = True
    _run_stream_worker(child, [
        _event_line(_assistant_event(RATE_LIMIT_TEXT)),
        _event_line({"type": "result", "is_error": True}),
    ], seq=16, task_id="task-pause")

    r = mgr.queue_pending[0]
    assert r["queueLockAutoReport"] is True
    assert worker._is_pauseable_agent_report(r) is True
    _cleanup()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
