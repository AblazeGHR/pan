"""History terminal facts share the existing Session commit and ordering."""
import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from packages.core import session, worker
from packages.core.adapters import CbcAdapter


def make(sid="terminal-history"):
    s = session.Session(id=sid, name=sid)
    session._cache[sid] = s
    w = worker.Worker(worker_id="w-" + sid, session_id=sid, adapter=CbcAdapter(),
                      status="running", pending_signal=asyncio.Queue())
    w._current_seq = 1
    w._execution_seq = 1
    worker.workers[w.worker_id] = w
    return s, w


@pytest.mark.parametrize("status,body", [("done", "answer"), ("error", "partial answer"), ("error", "")])
def test_terminal_commit_cold_reopen_and_dedup(status, body):
    async def run():
        s, w = make()
        if body:
            row = {"role": "assistant", "content": body}
            session.append_history(s, row)
            w._current_task_assistant_ids.add(row["messageId"])
        receipt = await worker._persist_terminal_state(w, s, status, body if status == "done" else "failure",
            error_context={"origin": "worker.stream.result", "cli_raw": {
                "error": {"code": "serverOverloaded", "message": "capacity\nBearer PRIVATE"},
                "error_source": "codex_error_payload"}})
        event = copy.deepcopy(s.history[-1])
        assert event["kind"] == "terminal" and event["content"] == ""
        fact = event["terminal"]
        assert fact["status"] == status and fact["order"] == 1
        assert fact["anchorMessageId"] == (s.history[0]["messageId"] if body else None)
        assert receipt["historyTerminalId"] == event["messageId"]
        assert receipt["historyRevision"] == s.history_revision
        assert worker.task_tail_block_id(s, receipt) == (s.history[0]["messageId"] if body else None)
        if status == "error":
            assert fact["error"]["code"] == "serverOverloaded"
            assert fact["error"]["source"] == "codex_error_payload"
            assert "PRIVATE" not in fact["error"]["message"]
        if body:
            assert s.history[0]["content"] == body
        session._cache.pop(s.id)
        reopened = session.get(s.id)
        assert reopened.history[-1] == event
        replacement = worker.Worker(worker_id=w.worker_id, session_id=s.id, adapter=CbcAdapter(),
            status="running", pending_signal=asyncio.Queue())
        replacement._current_seq = 1
        replacement._execution_seq = 1
        assert await worker._persist_terminal_state(replacement, reopened, status, "changed") is None
        assert reopened.history[-1] == event
        replacement._terminal_handled = False
        assert await worker._persist_terminal_state(replacement, reopened,
            "done" if status == "error" else "error", "conflicting late outcome") is None
        assert reopened.history[-1] == event
    asyncio.run(run())


def test_freezes_anchor_before_await_and_next_done_preserves_error(monkeypatch):
    async def run():
        s, w = make()
        first = {"role": "assistant", "content": "original body"}
        session.append_history(s, first)
        snapshot = session.history_tail_snapshot(s)
        session.append_history(s, {"role": "user", "content": "appended while terminal lock waits"})
        async def log(*args):
            session.append_history(s, {"role": "user", "content": "later input"})
        monkeypatch.setattr(worker, "_record_terminal_error_sample", log)
        await worker._persist_terminal_state(w, s, "error", "native error",
            error_context={"previous_captured": True, "previous_message": snapshot})
        original = copy.deepcopy(s.history[-1])
        assert original["terminal"]["anchorMessageId"] == first["messageId"]
        w._terminal_handled = False
        w._current_seq = 2
        w._execution_seq = 2
        w._execution_history_start = len(s.history)
        await worker._persist_terminal_state(w, s, "done", "")
        assert s.history[-1]["terminal"]["anchorMessageId"] is None
        assert s.history[-2] == original
        assert [r["terminal"]["status"] for r in s.history if r.get("terminal")] == ["error", "done"]
    asyncio.run(run())


def test_old_history_missing_metadata_and_reimport_keeps_known_facts():
    async def run():
        s, w = make()
        body = {"role": "assistant", "content": "unchanged"}
        session.append_history(s, body)
        await worker._persist_terminal_state(w, s, "error", "actual failure")
        event = copy.deepcopy(s.history[-1])
        imported = session.assign_pan_message_ids([{"role": "assistant", "content": "unchanged"}],
                                                  previous_history=s.history)
        assert imported[-1] == event
        assert imported[0]["messageId"] == body["messageId"]
        assert session.assign_pan_message_ids([], previous_history=s.history) == [event]
        forged = copy.deepcopy(event)
        forged["terminal"]["status"] = "done"
        assert session.assign_pan_message_ids([forged], previous_history=s.history) == [event]
        legacy = session.assign_pan_message_ids([{"role": "user", "content": "old"}])
        assert all("terminal" not in row for row in legacy)
        assert session.assign_pan_message_ids([]) == []
        # Reimport itself uses the normal replacement/usage durable ticket.
        await session.replace_usage_totals_async(s, None, native_entries=[], history=imported,
                                                 force_full=True, clear_last_result=True)
        session._cache.pop(s.id)
        assert session.get(s.id).history[-1] == event
    asyncio.run(run())


def test_failed_base_commit_retries_original_event_without_duplicate(monkeypatch):
    async def run():
        s, w = make()
        flush = worker._flush_history_now
        monkeypatch.setattr(worker, "_flush_history_now", AsyncMock(side_effect=OSError("synthetic disk fault")))
        with pytest.raises(OSError, match="synthetic disk fault"):
            await worker._persist_terminal_state(w, s, "done", "answer")
        event = copy.deepcopy(s.history[-1])
        monkeypatch.setattr(worker, "_flush_history_now", flush)
        await worker._persist_terminal_state(w, s, "done", "answer")
        assert [row for row in s.history if row.get("kind") == "terminal"] == [event]
        assert len([row for row in s.history if row["role"] == "assistant"]) == 1
    asyncio.run(run())
