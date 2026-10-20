"""Integration-only gate and atomicity checks on main + sticky Wake."""
import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from packages.core import legal_offline as offline, queue_locks, session as sess, worker
from test_illegal_queue_wake import incident, registry
from test_manual_task_report import _append_run


@pytest.mark.parametrize("entry", ["send", "assign", "notice", "channel", "report", "manual-report", "wake"])
def test_every_entry_rolls_back_incident_membership_on_replace_failure(monkeypatch, entry):
    async def run():
        s, batch = incident()
        s.queue_paused = True
        s.qq_subscriptions = {"user:42"}
        child = sess.create("child")
        child.managed_by = s.id
        terminal = _append_run(child, "assigned", 1, ["done"])
        s.report_subscriptions.add(child.id)
        sess.save(child); sess.save(s)
        before = offline.snapshot(s)
        before_bytes = sess._path(s.id).read_bytes()
        events = AsyncMock(); monkeypatch.setattr(worker, "_broadcast", events)
        receipts = copy.deepcopy(s.manual_report_receipts)
        original = sess._replace_session_metadata
        def fail(*args):
            raise OSError("integration replace failure")
        monkeypatch.setattr(sess, "_replace_session_metadata", fail)
        async def deliver():
            if entry == "send": return await worker.send_session(s.id, "message", client_message_id="receipt")
            if entry == "assign": return await worker.assign(s.id, "assignment", task_id="assigned-new")
            if entry == "notice": return await worker.enqueue_notice(s.id, "notice", event_id="job:terminal")
            if entry == "channel": return await worker.enqueue_channel_reminder("qq", "user", "42", text="plugin")
            if entry == "report": return await worker._enqueue_report(child.id, "done", "result", "assigned", "provider")
            if entry == "manual-report": return await worker.enqueue_manual_report(child.id, "assigned", 1, terminal["tail_id"])
            return await worker.wake_session(s.id, client_message_id="wake")
        try:
            result = await deliver()
            if entry == "manual-report": assert result[1]["code"] == "persist_failed"
            else: assert result["status"] == "error"
        except OSError:
            assert entry in {"notice", "channel", "report"}
        # Empty idempotency-map normalization is harmless; no candidate record survives.
        after = offline.snapshot(s)
        for key in before:
            if key != "queue_idempotency_index": assert after[key] == before[key], key
        assert not s.queue_pending and not s.queue_delivery_ledger
        assert sess._path(s.id).read_bytes() == before_bytes
        events.assert_not_awaited()
        assert s.manual_report_receipts == receipts
        assert s.illegal_batches[batch]["messages"] == []
        monkeypatch.setattr(sess, "_replace_session_metadata", original)
        await deliver()
        assert len(s.queue_pending) == 1
        row = s.queue_pending[0]
        assert queue_locks.source(row) == (None if entry == "wake" else "illegal")
        assert s.illegal_batches[batch]["messages"] == [row["id"]]
    asyncio.run(run())


def test_wake_preserves_report_policy_manual_lock_edit_lease_and_protocol_holds():
    async def run():
        s, _ = incident()
        s.queue_paused = True
        await worker.enqueue_user_message(s.id, "editing", "edit")
        editing = s.queue_pending[0]
        worker.acquire_queue_edit_lock(s, editing["id"], "browser-editor")
        await worker.enqueue_user_message(s.id, "manual", "manual", locked=True)
        child = sess.create("reporter"); child.managed_by = s.id
        s.report_subscriptions.add(child.id); s.agent_reports_paused = True
        sess.save(child)
        await worker._enqueue_report(child.id, "done", "report", None, None)
        report = s.queue_pending[-1]
        editing["queueSteerPending"] = True
        editing["forceSendHeld"] = True
        sess.save(s)
        results = await asyncio.gather(*[worker.wake_session(s.id, client_message_id="same-wake") for _ in range(4)])
        assert len({row["queueItemId"] for row in results}) == 1
        assert len(s.queue_pending) == 4
        wake = s.queue_pending[0]
        assert wake["wakeMessage"] and queue_locks.source(wake) is None
        assert worker._select_queue_unit(s) is None  # Session Pause still gates all.
        assert worker.queue_item_edit_locked(s, editing["id"])
        assert report["queueLockAutoReport"]
        assert queue_locks.source(s.queue_pending[2]) == "manual"
        s.queue_paused = False
        assert worker._select_queue_unit(s) == [wake]  # Priority changes only the new row.
        s.queue_pending.remove(wake)
        offline.activate(s, {"token": "ready-successor", "generation": 2}, remember=lambda row: worker._remember_queue_item(s, row))
        assert not worker.queue_item_eligible(s, editing)
        assert editing["queueSteerPending"] and editing["forceSendHeld"]
        assert worker.queue_item_edit_locked(s, editing["id"])
        assert report["queueLockAutoReport"] and not worker.queue_item_eligible(s, report)
        assert queue_locks.source(s.queue_pending[1]) == "manual"
    asyncio.run(run())


@pytest.mark.parametrize("owner_status", ["alive", "unknown"])
def test_wake_does_not_retire_or_replace_unconfirmed_runtime_owner(monkeypatch, owner_status):
    async def run():
        s, _ = incident()
        owner = copy.deepcopy(s.offline_owner)
        operations = copy.deepcopy(s.execution_operations)
        monkeypatch.setattr(offline, "previous_owner_status", lambda _: owner_status)
        create = AsyncMock(); monkeypatch.setattr(worker, "create_worker", create)
        result = await worker.wake_session(s.id, client_message_id="wake-owner")
        assert result["status"] == "queued"
        with pytest.raises(RuntimeError, match=owner_status):
            await worker._recover_session(s.id)
        create.assert_not_awaited()
        assert s.offline_owner == owner and s.execution_operations == operations
        assert offline.is_illegal(s) and not s.legal_offline
    asyncio.run(run())
