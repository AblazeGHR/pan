"""History facts, manager reports and the accepted notification ledger coexist."""
import asyncio
import copy
import json

import pytest

from packages.core import session, session_error_log, source_notifications as notices, worker
from packages.core.adapters import CbcAdapter
from packages.interactions.store import InteractionStore
from tests.test_report_error_context import (
    _setup_managed_pair, _StreamProcess, _event_line, _assistant_event,
    _cleanup, _no_real_recovery_spawn,
)


@pytest.mark.parametrize("global_on", [False, True])
def test_error_history_report_and_notification_then_done(tmp_path, monkeypatch, global_on):
    from packages.interactions import api
    class LiveProcess(_StreamProcess):
        async def read(self, n=-1):
            if not self._events:
                # Keep the provider stream logically open after its result.
                # Ending this test reader must not fabricate a separate EOF
                # incident, which correctly has its own notification identity.
                raise asyncio.CancelledError()
            return await super().read(n)
    def run_stream(target, events, *, seq):
        process = LiveProcess(events)
        w = worker.Worker(worker_id="coexist-worker", session_id=target.id, adapter=CbcAdapter(),
            status="running", process=process, pending_signal=asyncio.Queue())
        w._current_seq = seq
        w._current_task_id = "coexist-task"
        worker.workers[w.worker_id] = w
        async def run():
            await worker._capture_auto_report(w, [{"source": "agent"}])
            try:
                await worker._read_stdout(w)
            except asyncio.CancelledError:
                assert not process._events and w._terminal_handled and w.status == "idle"
        asyncio.run(run())
    store = InteractionStore(tmp_path / "owned-notification.sqlite")
    monkeypatch.setattr(api, "get_store", lambda: store)
    monkeypatch.setattr(notices, "settings", lambda source: notices.normalize({
        "enabled": global_on, "browser": True, "qqReport": True,
        "qqTargets": ["user:123@456"], "epoch": "coexist"}))
    monkeypatch.setattr(notices, "session_policy", lambda: (True, "coexist"))
    child, manager, _ = _setup_managed_pair()
    child.notification_settings = {"browser": True, "system": False}
    child.qq_report_targets = {"user:123@456"}
    native = {"type": "result", "is_error": True, "result": "original assistant body",
        "error": {"message": "capacity; api_key=private-value", "code": "serverOverloaded"},
        "error_source": "codex_error_payload"}
    try:
        run_stream(child, [_event_line(_assistant_event("original assistant body")),
                                  _event_line(native)], seq=1)
        original = copy.deepcopy(child.history[-1])
        assert original["terminal"]["status"] == "error"
        assert original["terminal"]["anchorMessageId"] == child.history[0]["messageId"]
        assert original["terminal"]["error"]["code"] == "serverOverloaded"
        assert "private-value" not in original["terminal"]["error"]["message"]
        assert child.qq_report_outbox == {}, "error must not re-enter completion QQ outbox"
        assert manager.queue_pending[0]["status"] == "error"
        assert manager.queue_pending[0]["errorCode"] == "serverOverloaded"
        assert manager.queue_pending[0]["lastMessage"] == "original assistant body"
        records = [json.loads(line) for line in session_error_log.path().read_text(encoding="utf-8").splitlines()]
        assert len(records) == 1
        with store.db() as db:
            events = [dict(row) for row in db.execute("SELECT * FROM source_notification_events")]
        assert len(events) == 1 and events[0]["identity"] == records[0]["eventId"]
        grants = json.loads(events[0]["targets"])
        assert set(grants) == {"browser", "user:123@456"}
        assert all(set(origins) == ({"global", "session"} if global_on else {"session"})
                   for origins in grants.values())
        sent, browser = [], []
        async def sender(**kwargs):
            sent.append(kwargs)
            return {"ok": True}
        async def bcast(payload):
            browser.append(payload)
        async def drain(current):
            while await notices.drain_once(sender, current, browser_sender=bcast):
                pass
        asyncio.run(drain(store))
        assert len(sent) == len(browser) == 1
        assert set(browser[0]) == {"type", "notificationId"}
        assert browser[0]["type"] == "notification.source"
        assert "private-value" not in sent[0]["text"]
        run_stream(child, [_event_line(_assistant_event("next answer")),
            _event_line({"type": "result", "is_error": False, "result": "next answer"})], seq=2)
        assert child.history[-1]["terminal"]["status"] == "done"
        assert child.history[-3] == original
        assert len(child.qq_report_outbox) == 1
        assert next(iter(child.qq_report_outbox.values()))["text"] == "next answer"
        assert [row["status"] for row in manager.queue_pending] == ["error", "done"]
        # Reopen both durable stores and replay the original error after done.
        session._cache.pop(child.id)
        cold = session.get(child.id)
        run_stream(cold, [_event_line(native)], seq=1)
        assert [row["terminal"]["status"] for row in cold.history if row.get("terminal")] == ["error", "done"]
        assert next(row for row in cold.history if row.get("kind") == "terminal") == original
        asyncio.run(drain(InteractionStore(store.path)))
        assert len(sent) == len(browser) == 1
        assert len(manager.queue_pending) == 2
    finally:
        _cleanup()
