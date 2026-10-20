"""Automatic Force send for newly enqueued structured sources.

Covers the three independent Behavior switches (Job terminal / MA / report),
structured classification (never body text), the idle normal-send rule, the
running Steer path, the non-Steer head+restart path, and every preserved
gate (Pause, read-only, Illegal, locks, edit leases, unknown/rejected Steer).
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from packages.core import config, session as sess, worker
from tests.test_self_queue import FutureSteerAdapter


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    sess._cache.clear()
    worker.workers.clear()
    worker._spawn_locks.clear()
    worker._queue_locks.clear()
    worker._auto_force_tasks.clear()
    worker.set_broadcaster(None)
    yield
    sess._cache.clear()
    worker.workers.clear()
    worker._auto_force_tasks.clear()


def configure(monkeypatch, **flags):
    values = {"system": False, "ma": False, "report": False, **flags}
    monkeypatch.setattr(worker, "load_config", lambda: {
        "ui": {"autoForceSend": values},
    })
    return values


def make_session(session_id="target", *, managed_by=None):
    s = sess.Session(id=session_id, name=session_id)
    s.active_task_id = "original-task"
    if managed_by is not None:
        s.managed_by = managed_by
    sess._cache[s.id] = s
    return s


def make_manager(session_id="ma", children=("target",)):
    manager = sess.Session(id=session_id, name=session_id)
    manager.managed = list(children)
    sess._cache[manager.id] = manager
    return manager


def live(s, *, status="running", capable=True, pending_restart=False):
    adapter = FutureSteerAdapter()
    adapter.supports_native_steer = capable
    stdin = Mock(drain=AsyncMock())
    w = worker.Worker(
        worker_id="auto-worker",
        session_id=s.id,
        adapter=adapter,
        status=status,
        process=SimpleNamespace(returncode=None, stdin=stdin),
        pending_signal=asyncio.Queue(),
    )
    w.pending_restart = pending_restart
    worker.workers[w.worker_id] = w
    return w, stdin


def accept_on_write(w, outcome="accepted"):
    def write(data):
        control = json.loads(data)
        worker._route_steer_receipt(w, {
            "type": "pan.steer_receipt",
            "requestId": control["requestId"],
            "ok": outcome == "accepted",
            "outcome": outcome,
            "error": None if outcome == "accepted" else "fixture " + outcome,
        })
    return write


async def queue_ma_message(s, ma, text="follow-up"):
    return await worker.send_session(
        s.id, text, source="agent", source_session_id=ma.id)


def test_all_switches_default_off_keeps_legacy_queue(monkeypatch):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        w, stdin = live(s)
        configure(monkeypatch)
        w.pending_signal = asyncio.Queue()
        result = await queue_ma_message(s, ma)
        await worker.wait_auto_force(s.id)
        assert result["status"] == "queued"
        assert len(s.queue_pending) == 1
        assert stdin.write.call_count == 0
        assert w.status == "running"
    asyncio.run(run())


@pytest.mark.parametrize("enabled", ["system", "ma", "report"])
def test_single_switch_matrix_only_schedules_its_own_class(monkeypatch, enabled):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        live(s, capable=False)
        configure(monkeypatch, **{enabled: True})
        shapes = {
            "system": {
                "type": "notice", "kind": "report", "id": "q_sys",
                "source": "automation", "noticeKind": "background_job_terminal",
                "jobId": "job-x", "status": "done", "result": "x",
                "eventId": "job-x:terminal",
            },
            "ma": {
                "type": "task", "kind": "task", "id": "q_ma", "text": "hi",
                "source": "agent", "sourceSessionId": ma.id,
                "taskId": "original-task", "taskIdSource": "active",
            },
            "report": {
                "type": "report", "kind": "report", "id": "q_rep",
                "source": "report", "result": "x", "status": "done",
            },
        }
        for source_class, item in shapes.items():
            task = worker._schedule_auto_force(s.id, item)
            if source_class == enabled:
                assert task is not None, source_class
                task.cancel()
            else:
                assert task is None, source_class
        await asyncio.sleep(0)
    asyncio.run(run())


def test_ma_switch_steers_original_item_and_preserves_identity(monkeypatch):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        w, stdin = live(s)
        stdin.write.side_effect = accept_on_write(w)
        configure(monkeypatch, ma=True)
        restart = AsyncMock(side_effect=AssertionError("native Steer must not restart"))
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        await queue_ma_message(s, ma, "steer body")
        queued = next(item for item in s.queue_pending
                      if item.get("text") == "steer body")
        await worker.wait_auto_force(s.id)
        assert stdin.write.call_count == 1
        assert not s.queue_pending
        row = s.history[-1]
        assert row["content"] == "steer body"
        assert row["queueItemIds"] == [queued["queueItemId"]]
        assert row["taskId"] == "original-task"
        assert row["source"] == "agent"
        restart.assert_not_called()
    asyncio.run(run())


def test_non_ma_agent_and_formal_assign_are_not_classified(monkeypatch):
    async def run():
        s = make_session()
        ma = make_manager()
        other = make_session("other-agent")
        s.managed_by = ma.id
        w, stdin = live(s)
        stdin.write.side_effect = accept_on_write(w)
        configure(monkeypatch, ma=True)
        # A sibling/unrelated agent is not an ancestor of the target.
        await worker.send_session(s.id, "not ma", source="agent",
                                  source_session_id=other.id)
        await worker.wait_auto_force(s.id)
        assert stdin.write.call_count == 0
        # A formal assign from the MA is a task dispatch, not a message.
        async with worker.queue_lock(s.id):
            await worker._persist_task_item(
                s, "assign body", "agent", None, "formal-task", None, ma.id)
        await worker.wait_auto_force(s.id)
        assert stdin.write.call_count == 0
        assert [item["text"] for item in s.queue_pending] == [
            "not ma", "assign body"]
    asyncio.run(run())


@pytest.mark.parametrize("notice_kind", ["background_job_terminal", "agent_notice"])
def test_system_switch_only_matches_job_terminal(monkeypatch, notice_kind):
    async def run():
        s = make_session()
        w, stdin = live(s)
        stdin.write.side_effect = accept_on_write(w)
        configure(monkeypatch, system=True)
        await worker.enqueue_notice(
            s.id, "job payload", source="automation",
            event_id="job-1:terminal" if notice_kind == "background_job_terminal" else "n-1",
            notice_kind=notice_kind, job_id="job-1" if notice_kind == "background_job_terminal" else None,
            notice_status="done")
        await worker.wait_auto_force(s.id)
        if notice_kind == "background_job_terminal":
            assert stdin.write.call_count == 1
            assert not s.queue_pending
        else:
            assert stdin.write.call_count == 0
            assert len(s.queue_pending) == 1
    asyncio.run(run())


def test_report_switch_steers_manager_report(monkeypatch):
    async def run():
        manager = make_manager("manager", children=("child",))
        manager.report_subscriptions = {"child"}
        child = make_session("child", managed_by="manager")
        w, stdin = live(manager)
        stdin.write.side_effect = accept_on_write(w)
        configure(monkeypatch, report=True)
        await worker._enqueue_report(
            child.id, "done", "child result", "task-1", "worker-1")
        await worker.wait_auto_force(manager.id)
        assert stdin.write.call_count == 1
        assert not manager.queue_pending
        row = manager.history[-1]
        assert "child result" in row["content"]
        assert row["kind"] == "report"
        assert row["source"] == "report"
    asyncio.run(run())


def test_idle_empty_queue_uses_normal_send_without_steer_or_restart(monkeypatch):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        w, stdin = live(s, status="idle", capable=False)
        configure(monkeypatch, ma=True)
        restart = AsyncMock(side_effect=AssertionError("idle must not restart"))
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        steer = AsyncMock(side_effect=AssertionError("idle must not Steer"))
        monkeypatch.setattr(worker, "steer_queue_item", steer)
        result = await worker.send_session(
            s.id, "idle message", source="agent", source_session_id=ma.id,
            force=True)
        await worker.wait_auto_force(s.id)
        assert result["status"] == "queued"
        assert len(s.queue_pending) == 1
        assert w.pending_signal.qsize() == 1
        restart.assert_not_called()
        steer.assert_not_called()
        stdin.write.assert_not_called()
    asyncio.run(run())


def test_noncapable_running_moves_original_item_to_head_before_restart(monkeypatch):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        w, stdin = live(s, capable=False)
        configure(monkeypatch, ma=True)
        async with worker.queue_lock(s.id):
            older, error = await worker._persist_task_item(
                s, "older queued message", "user", None, None, None, None,
                idempotent_task_id=False)
        assert error is None
        restart = AsyncMock(return_value=None)
        monkeypatch.setattr(worker, "_restart_worker_unlocked", restart)
        await queue_ma_message(s, ma, "new urgent message")
        queued = next(item for item in s.queue_pending
                      if item.get("text") == "new urgent message")
        await worker.wait_auto_force(s.id)
        assert restart.await_count == 1
        assert s.queue_pending[0]["text"] == "new urgent message"
        assert s.queue_pending[1]["text"] == "older queued message"
        assert s.queue_pending[0]["queueItemId"] == queued["queueItemId"]
        assert len(s.queue_pending) == 2  # no duplicate enqueue
        assert [item["position"] for item in s.queue_pending] == [0, 1]
        assert stdin.write.call_count == 0
    asyncio.run(run())


def test_capable_running_steers_even_when_queue_has_earlier_items(monkeypatch):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        w, stdin = live(s)
        stdin.write.side_effect = accept_on_write(w)
        configure(monkeypatch, ma=True)
        async with worker.queue_lock(s.id):
            older, _ = await worker._persist_task_item(
                s, "older", "user", None, None, None, None,
                idempotent_task_id=False)
        await queue_ma_message(s, ma, "steer now")
        queued = next(item for item in s.queue_pending
                      if item.get("text") == "steer now")
        await worker.wait_auto_force(s.id)
        assert stdin.write.call_count == 1
        assert [item["text"] for item in s.queue_pending] == ["older"]
        assert s.queue_pending[0]["queueItemId"] == older["queueItemId"]
        assert s.history[-1]["queueItemIds"] == [queued["queueItemId"]]
    asyncio.run(run())


@pytest.mark.parametrize("state", ["paused", "readonly", "illegal"])
def test_pause_readonly_illegal_block_all_auto_force(monkeypatch, state):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        w, stdin = live(s)
        configure(monkeypatch, ma=True, system=True)
        if state == "paused":
            s.queue_paused = True
        elif state == "readonly":
            s.readonly_session = True
        else:
            identity = {"token": "failed", "generation": 1, "serviceId": "previous", "workerId": "old"}
            record = worker._offline.admit(s, identity, kind="foreground")
            s.offline_run = record
            worker._offline.detect(s, "test-crash", eligible=lambda row: True,
                                   remember=lambda row: worker._remember_queue_item(s, row))
        restart = AsyncMock(side_effect=AssertionError("gate must block restart"))
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        if state == "readonly":
            # System notices carry no managed source Session, so the read-only
            # write gate is not what is under test here: the automatic router
            # itself must refuse to Steer/restart a read-only Session.
            await worker.enqueue_notice(
                s.id, "readonly job", source="automation",
                event_id="job-ro:terminal",
                notice_kind="background_job_terminal", job_id="job-ro",
                notice_status="done")
        else:
            await queue_ma_message(s, ma)
        await worker.wait_auto_force(s.id)
        assert stdin.write.call_count == 0
        restart.assert_not_called()
        assert len(s.queue_pending) == 1
    asyncio.run(run())


@pytest.mark.parametrize("capable,expected_writes", [(False, 0), (True, 1)])
def test_delayed_restart_intent_is_preserved(monkeypatch, capable, expected_writes):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        w, stdin = live(s, capable=capable, pending_restart=True)
        stdin.write.side_effect = accept_on_write(w)
        configure(monkeypatch, ma=True)
        restart = AsyncMock(side_effect=AssertionError("delayed intent must win"))
        monkeypatch.setattr(worker, "_restart_worker_unlocked", restart)
        await queue_ma_message(s, ma)
        await worker.wait_auto_force(s.id)
        assert stdin.write.call_count == expected_writes
        restart.assert_not_called()
        assert w.pending_restart is True
        if expected_writes == 0:
            assert len(s.queue_pending) == 1
    asyncio.run(run())


def test_edit_lease_blocks_steer_and_restart(monkeypatch):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        w, stdin = live(s)
        configure(monkeypatch, ma=True)
        async with worker.queue_lock(s.id):
            item, _ = await worker._persist_task_item(
                s, "message", "agent", None, "original-task", None, ma.id,
                idempotent_task_id=False)
        worker.acquire_queue_edit_lock(s, item["queueItemId"], "editor")
        restart = AsyncMock(side_effect=AssertionError("lease blocks restart"))
        monkeypatch.setattr(worker, "_restart_worker_unlocked", restart)
        await worker._schedule_auto_force(s.id, item)
        await worker.wait_auto_force(s.id)
        assert stdin.write.call_count == 0
        restart.assert_not_called()
        assert item in s.queue_pending
    asyncio.run(run())


def test_stop_report_lock_blocks_report_auto_force(monkeypatch):
    async def run():
        manager = make_manager("manager", children=("child",))
        manager.report_subscriptions = {"child"}
        child = make_session("child", managed_by="manager")
        w, stdin = live(manager)
        configure(monkeypatch, report=True)
        monkeypatch.setattr(worker, "_is_pauseable_agent_report", lambda item: True)
        # Simulate the future-report lock produced by stop reports.
        manager.agent_reports_paused = True
        await worker._enqueue_report(
            child.id, "done", "locked report", "task-1", "worker-1")
        await worker.wait_auto_force(manager.id)
        assert stdin.write.call_count == 0
        assert len(manager.queue_pending) == 1
        assert worker._queue_item_locked(manager.queue_pending[0])
    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["rejected", "unknown"])
def test_rejected_or_unknown_steer_is_not_retried(monkeypatch, outcome):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        w, stdin = live(s)
        stdin.write.side_effect = accept_on_write(w, outcome)
        configure(monkeypatch, ma=True)
        await queue_ma_message(s, ma)
        await worker.wait_auto_force(s.id)
        assert stdin.write.call_count == 1
        item = s.queue_pending[0]
        assert item["queueItemId"]
        if outcome == "rejected":
            assert item.get("lastSteerError")
        else:
            assert item.get("queueSteerPending")
        # A duplicate event/trigger must not attempt the provider again.
        worker._schedule_auto_force(s.id, item)
        await worker.wait_auto_force(s.id)
        assert stdin.write.call_count == 1
    asyncio.run(run())


def test_duplicate_event_and_concurrent_schedule_keep_one_original_id(monkeypatch):
    async def run():
        s = make_session()
        w, stdin = live(s)
        stdin.write.side_effect = accept_on_write(w)
        configure(monkeypatch, system=True)
        first = await worker.enqueue_notice(
            s.id, "terminal", source="automation", event_id="job-9:terminal",
            notice_kind="background_job_terminal", job_id="job-9",
            notice_status="done")
        duplicate = await worker.enqueue_notice(
            s.id, "terminal", source="automation", event_id="job-9:terminal",
            notice_kind="background_job_terminal", job_id="job-9",
            notice_status="done")
        await worker.wait_auto_force(s.id)
        assert duplicate.get("duplicate") is True
        assert first["pending"] == 1
        assert stdin.write.call_count == 1
        assert len(s.history[-1]["queueItemIds"]) == 1
    asyncio.run(run())


def test_attachment_parts_are_preserved_by_auto_steer(monkeypatch, tmp_path):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        w, stdin = live(s)
        stdin.write.side_effect = accept_on_write(w)
        configure(monkeypatch, ma=True)
        attachment = tmp_path / "note.txt"
        attachment.write_text("hello", encoding="utf-8")
        parts = [{"type": "text", "text": "see attachment"},
                 {"type": "attachment", "name": "note.txt",
                  "__serverPath": str(attachment)}]
        item, error = await worker._persist_task_item(
            s, "fallback", "agent", None, "original-task", None, ma.id, parts,
            idempotent_task_id=False)
        assert error is None
        worker._schedule_auto_force(s.id, item)
        await worker.wait_auto_force(s.id)
        assert stdin.write.call_count == 1
        assert not s.queue_pending
        row = s.history[-1]
        assert row["parts"] == parts
        assert row["queueItemIds"] == [item["queueItemId"]]
    asyncio.run(run())


def test_force_restart_preserves_original_id_and_fifo_for_noncapable_running(monkeypatch):
    async def run():
        s = make_session()
        w, stdin = live(s, capable=False)
        async with worker.queue_lock(s.id):
            older, _ = await worker._persist_task_item(
                s, "older", "user", None, None, None, None,
                idempotent_task_id=False)
        restarted = AsyncMock(return_value=w)
        monkeypatch.setattr(worker, "restart_or_start_worker", restarted)
        result = await worker.send_session(s.id, "forced", force=True)
        assert result["status"] == "queued"
        assert s.queue_pending[0]["text"] == "forced"
        assert s.queue_pending[0]["queueItemId"] == result["queueItemId"]
        assert s.queue_pending[1]["queueItemId"] == older["queueItemId"]
        assert len(s.queue_pending) == 2
        restarted.assert_awaited_once_with(s.id, allow_illegal_recovery=False)
        stdin.write.assert_not_called()
    asyncio.run(run())


def test_manager_wake_uses_atomic_wake_without_automatic_force(monkeypatch):
    """Explicit Wake retains priority/FIFO semantics even with MA Force enabled."""
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        w, stdin = live(s)
        configure(monkeypatch, ma=True, system=True, report=True)
        restart = AsyncMock(side_effect=AssertionError("Wake must not Force restart"))
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        result = await worker.wake_session(
            s.id, source="agent", source_session_id=ma.id,
            client_message_id="manager-wake")
        await worker.wait_auto_force(s.id)
        assert result["status"] == "queued"
        assert len(s.queue_pending) == 1
        assert s.queue_pending[0]["wakeMessage"]
        assert s.queue_pending[0]["id"] == result["queueItemId"]
        stdin.write.assert_not_called()
        restart.assert_not_called()
    asyncio.run(run())


def test_force_restart_race_with_illegal_retains_original_locked_item(monkeypatch):
    """An incident between ordering and restart must remain queued, not an error."""
    async def run():
        s = make_session()
        live(s, capable=False)
        async def raced_restart(session_id, *, allow_illegal_recovery):
            assert session_id == s.id and allow_illegal_recovery is False
            identity = {"token": "failed", "generation": 1,
                        "serviceId": "previous", "workerId": "old"}
            record = worker._offline.admit(s, identity, kind="foreground")
            s.offline_run = record
            worker._offline.detect(s, "test-crash", eligible=lambda row: True,
                                   remember=lambda row: worker._remember_queue_item(s, row))
            return "illegal_queue_hold"
        monkeypatch.setattr(worker, "restart_or_start_worker", raced_restart)
        result = await worker.send_session(s.id, "forced-race", force=True)
        assert result["status"] == "queued"
        assert len(s.queue_pending) == 1
        item = s.queue_pending[0]
        assert item["id"] == result["queueItemId"]
        assert item["queueLockSource"] == "illegal"
        assert worker._offline.is_illegal(s)
        assert not worker.queue_item_eligible(s, item)
    asyncio.run(run())


def test_auto_force_snapshot_race_cannot_restart_illegal(monkeypatch):
    async def run():
        s = make_session()
        ma = make_manager()
        s.managed_by = ma.id
        live(s, capable=False)
        configure(monkeypatch, ma=True)
        async def incident_on_snapshot(event):
            if event.get("type") != "queue.snapshot":
                return
            identity = {"token": "failed", "generation": 1,
                        "serviceId": "previous", "workerId": "old"}
            s.offline_run = worker._offline.admit(s, identity, kind="foreground")
            worker._offline.detect(s, "test-crash", eligible=lambda row: True,
                                   remember=lambda row: worker._remember_queue_item(s, row))
        monkeypatch.setattr(worker, "_broadcast", incident_on_snapshot)
        restart = AsyncMock(side_effect=AssertionError("Illegal forbids automatic restart"))
        monkeypatch.setattr(worker, "_restart_worker_unlocked", restart)
        result = await queue_ma_message(s, ma)
        await worker.wait_auto_force(s.id)
        assert result["status"] == "queued"
        assert len(s.queue_pending) == 1
        assert s.queue_pending[0]["queueLockSource"] == "illegal"
        restart.assert_not_awaited()
    asyncio.run(run())
