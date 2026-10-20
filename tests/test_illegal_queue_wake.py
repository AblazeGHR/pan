"""Sticky Illegal admission and explicit wake, without providers or shared data."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from packages.core import config, legal_offline as offline, queue_locks, session as sess, worker
from packages.core.adapters import get_adapter, resolve_execution_mode
from packages.web import server


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    for key in ("workers", "_workers_by_session", "_worker_generations", "_queue_locks", "_spawn_locks", "_recovery_tasks", "_task_status"):
        monkeypatch.setattr(worker, key, {})
    monkeypatch.setattr(worker, "_broadcast", None)
    monkeypatch.setattr(worker, "_recovery_required", set())
    monkeypatch.setattr(worker, "_schedule_session_recovery", Mock())


def incident():
    s = sess.create("sticky-illegal")
    identity = {"token": "failed", "generation": 1, "serviceId": "previous", "workerId": "old", "pid": None}
    record = offline.admit(s, identity, kind="foreground")
    s.offline_run = record
    offline.detect(s, "test-crash", eligible=lambda row: True, remember=lambda row: worker._remember_queue_item(s, row))
    sess.save(s)
    return s, record["id"]


def disk(s):
    return json.loads(sess._path(s.id).read_text(encoding="utf-8"))


@pytest.mark.parametrize("source", ["user", "agent", "automation", "meta-agent"])
def test_new_messages_stay_illegal_and_queued(source):
    async def run():
        s, batch = incident()
        result = await worker.send_session(s.id, "new arrival", source=source, client_message_id="arrival")
        assert result["status"] == "queued"
        row = s.queue_pending[0]
        assert queue_locks.source(row) == "illegal"
        assert s.illegal_batches[batch]["messages"] == [row["id"]]
        assert worker._queue_recovery_blocked(s)
        assert not worker._has_dispatchable_items(s)
        assert disk(s)["queue_pending"][0]["queueLockSource"] == "illegal"
        assert offline.is_illegal(s)
        # Duplicate receipt cannot relock an explicit unlock.
        queue_locks.manual_change(row, False, None)
        worker._remember_queue_item(s, row)
        sess.save(s)
        await worker.send_session(s.id, "new arrival", source=source, client_message_id="arrival")
        assert len(s.queue_pending) == 1 and queue_locks.source(row) is None
        assert offline.is_illegal(s)
    asyncio.run(run())


def test_notice_plugin_channel_report_and_concurrent_duplicates_share_admission():
    async def run():
        s, batch = incident()
        s.qq_subscriptions = {"user:42"}
        child = sess.create("reporter")
        child.managed_by = s.id
        s.report_subscriptions.add(child.id)
        sess.save(child)
        # Plugin/channel HTTP paths use these same production entry points.
        await asyncio.gather(*[worker.enqueue_notice(s.id, "job failed", source="automation", event_id="job:terminal") for _ in range(4)])
        await worker.enqueue_channel_reminder("qq", "user", "42", text="plugin notification")
        await worker._enqueue_report(child.id, "done", "report", None, None)
        await worker.assign(s.id, "plugin assign", source="agent", task_id="plugin-task")
        assert worker._task_status["plugin-task"]["status"] == "pending"
        repeated = await worker.assign(s.id, "plugin assign", source="agent", task_id="plugin-task")
        assert repeated["status"] == "pending" and repeated["duplicate"]
        assert len(s.queue_pending) == 4
        assert all(queue_locks.source(row) == "illegal" for row in s.queue_pending)
        assert set(s.illegal_batches[batch]["messages"]) == {row["id"] for row in s.queue_pending}
        assert worker._queue_recovery_blocked(s)
        assert offline.is_illegal(s)
    asyncio.run(run())


def test_wake_is_atomic_first_and_preserves_pause_manual_and_other_rows(monkeypatch):
    async def run():
        s, batch = incident()
        await worker.enqueue_user_message(s.id, "old", "old")
        await worker.enqueue_user_message(s.id, "manual", "manual", locked=True)
        old, manual = s.queue_pending
        s.queue_paused = True
        sess.save(s)
        config.CONFIG_FILE.write_text(json.dumps({"session_lifecycle": {"wakeMessage": "Resume custom task"}}), encoding="utf-8")
        result = await worker.wake_session(s.id, client_message_id="wake-1")
        assert result["status"] == "queued" and result["queuePaused"]
        wake = s.queue_pending[0]
        assert wake["text"] == "Resume custom task" and wake["wakeMessage"]
        assert queue_locks.source(wake) is None
        assert queue_locks.source(old) == "illegal" and queue_locks.source(manual) == "manual"
        assert offline.is_illegal(s) and worker._queue_recovery_blocked(s)
        saved = disk(s)
        assert saved["queue_pending"][0]["id"] == wake["id"]
        assert saved["queue_delivery_ledger"][wake["id"]].get("queueLockSource") is None
        # A retry does not override a subsequent Manual lock or move it again.
        queue_locks.set_lock(wake, "manual")
        worker._remember_queue_item(s, wake)
        await worker.wake_session(s.id, client_message_id="wake-1")
        assert len(s.queue_pending) == 3 and queue_locks.source(wake) == "manual"
        successor = {"token": "successor", "generation": 2}
        offline.activate(s, successor, remember=lambda row: worker._remember_queue_item(s, row))
        assert not offline.is_illegal(s)
        assert queue_locks.source(old) is None and queue_locks.source(manual) == "manual"
    asyncio.run(run())


@pytest.mark.parametrize("wake", [False, True])
def test_failed_replace_rolls_back_queue_membership_ledger_and_receipt(monkeypatch, wake):
    async def run():
        s, _ = incident()
        worker._ensure_idempotency_index(s)
        old = offline.snapshot(s)
        original = sess._replace_session_metadata
        def fail(*args):
            raise OSError("injected replace failure")
        monkeypatch.setattr(sess, "_replace_session_metadata", fail)
        result = (await worker.wake_session(s.id, client_message_id="failed") if wake else
                  await worker.enqueue_user_message(s.id, "failed", "failed"))
        assert result["status"] == "error"
        assert offline.snapshot(s) == old
        assert "failed" not in s.accepted_input_ids
        monkeypatch.setattr(sess, "_replace_session_metadata", original)
        result = await worker.enqueue_user_message(s.id, "retry", "failed")
        assert result["status"] == "queued" and len(s.queue_pending) == 1
        assert queue_locks.source(s.queue_pending[0]) == "illegal"
    asyncio.run(run())


def test_oneshot_retirement_rejects_new_selection_and_resolves_historical_configuration():
    for name in ("cbc", "claude"):
        s = sess.Session(id="historical", name="historical", adapter=name,
                         adapter_config={"output_mode": "oneshot"})
        assert resolve_execution_mode(get_adapter(name), s) == "stream"
        assert server._session_to_api(s)["executionModes"] == ["stream"]
        with pytest.raises(ValueError):
            server._apply_output_mode(s, "oneshot")


@pytest.mark.parametrize("value", ["", "   ", None, 42, "x" * 32001])
def test_wake_message_config_rejects_invalid(value):
    with pytest.raises(server.HTTPException):
        asyncio.run(server.api_put_settings_session_lifecycle({"wakeMessage": value}))


def test_wake_message_config_roundtrip():
    result = asyncio.run(server.api_put_settings_session_lifecycle({"wakeMessage": "My custom wake"}))
    assert result["wakeMessage"] == worker.configured_wake_message() == "My custom wake"


def test_empty_illegal_incident_blocks_forced_recovery_and_repeated_watchdog(monkeypatch):
    async def run():
        s, _ = incident()
        monkeypatch.setattr(worker, "_list_sessions_shallow", lambda: [s])
        monkeypatch.setattr(worker, "_prepare_offline_recovery", AsyncMock())
        create = AsyncMock()
        monkeypatch.setattr(worker, "create_worker", create)
        worker._recovery_required.add(s.id)
        await worker._recover_session(s.id, force=True)
        for _ in range(4):
            await worker._global_watchdog_tick()
        create.assert_not_awaited()
        assert offline.is_illegal(s) and worker._queue_recovery_blocked(s)
    asyncio.run(run())
