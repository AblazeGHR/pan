"""Execution safety, lock provenance, crash windows and queue exclusion."""
import asyncio
import copy
import json
import os
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import psutil

from packages.core import legal_offline as offline, queue_locks, session as sess, worker
from packages.core.adapters import CbcAdapter
from packages.web import server


@pytest.fixture(autouse=True)
def runtime_registry(monkeypatch):
    for name in ("workers", "_workers_by_session", "_worker_generations", "_queue_locks",
                 "_spawn_locks", "_legal_state_locks", "_recovery_tasks"):
        monkeypatch.setattr(worker, name, {})
    monkeypatch.setattr(worker, "_recovery_required", set())
    monkeypatch.setattr(worker, "_broadcast", None)
    monkeypatch.setattr(worker, "_wake_worker", AsyncMock())


def setup(items=()):
    s = sess.create("execution-safety")
    s.queue_pending = [dict(id=value, queueItemId=value, type="task", text=value,
                            deliveryState="queued", revision=1) for value in items]
    w = worker.Worker(worker_id="worker-1", session_id=s.id, adapter=CbcAdapter(),
                      status="idle", process=SimpleNamespace(returncode=None, stdin=Mock(drain=AsyncMock())),
                      pending_signal=asyncio.Queue())
    worker.workers[w.worker_id] = w
    worker._register_worker(w)
    sess.save(s)
    return s, w


def reload(s):
    sess._cache.pop(s.id, None)
    return sess.get(s.id)


async def begin(s, w):
    await worker._activate_offline_worker(w, ready_proof=True)
    w._execution_seq += 1
    identity = await worker._begin_offline_run(w)
    w.status = "running"
    return identity


async def die(s, w, identity):
    w.process.returncode = 1
    await worker._detect_offline_exit(w, identity, "external-kill")
    return s.offline_run["id"]


@pytest.mark.parametrize("status,result", [("done", "ok"), ("error", "429 retry later"),
                                          ("error", "provider error"), ("cancelled", "cancelled")])
def test_safe_terminal_is_legal_while_stream_cli_is_alive(status, result):
    async def run():
        s, w = setup()
        await begin(s, w)
        assert not s.legal_offline and not offline.is_illegal(s)
        await worker._persist_terminal_state(w, s, status, result)
        assert w.process.returncode is None
        assert s.legal_offline and not offline.is_illegal(s)
        w.process.returncode = 1
        await worker._detect_offline_exit(w, offline.owner(w), "idle-kill")
        loaded = reload(s)
        assert loaded.legal_offline and not loaded.illegal_batches
    asyncio.run(run())


def test_same_failed_run_never_expands_or_relocks_and_successor_restores_only_illegal():
    async def run():
        s, w = setup(["A", "B", "C"])
        a, b, c = s.queue_pending
        queue_locks.set_lock(a, "manual")
        identity = await begin(s, w)
        batch = await die(s, w, identity)
        assert s.illegal_batches[batch]["messages"] == ["B", "C"]
        assert server._queue_pending_summary(s) == (3, True)
        await server.api_session_queue_item_lock(s.id, "B", {"locked": False})
        await server.api_session_queue_item_lock(s.id, "C", {"locked": True})
        d = dict(id="D", queueItemId="D", type="task", text="D", deliveryState="queued")
        s.queue_pending.append(d)
        await worker._detect_offline_exit(w, identity, "duplicate-EOF")
        assert queue_locks.source(b) is None and queue_locks.source(d) is None
        assert server._queue_pending_summary(s) == (4, False)
        worker.workers.clear(); worker._workers_by_session.clear()
        loaded = reload(s)
        await worker._prepare_offline_recovery(loaded)
        assert loaded.illegal_batches[batch]["messages"] == ["B", "C"]
        w.offline_token = "successor"; w.generation += 1
        w.process.returncode = None
        worker.workers[w.worker_id] = w; worker._register_worker(w)
        await worker._activate_offline_worker(w, ready_proof=True)
        rows = {item["id"]: item for item in loaded.queue_pending}
        assert queue_locks.source(rows["A"]) == queue_locks.source(rows["C"]) == "manual"
        assert queue_locks.source(rows["B"]) is None
        assert loaded.illegal_batches[batch]["recovered"]
        assert loaded.legal_offline
    asyncio.run(run())


@pytest.mark.parametrize("all_rows", [False, True])
def test_manual_unlock_accepts_illegal_and_lock_all_claims_it(all_rows):
    async def run():
        s, w = setup(["B", "C"])
        key = await die(s, w, await begin(s, w))
        await server.api_session_queue_locks(s.id, {"locked": True})
        assert all(queue_locks.source(row) == "manual" for row in s.queue_pending)
        assert all(not row.get("queueLockIllegalBatch") for row in s.queue_pending)
        if all_rows:
            await server.api_session_queue_locks(s.id, {"locked": False})
        else:
            await server.api_session_queue_item_lock(s.id, "B", {"locked": False})
        assert queue_locks.source(s.queue_pending[0]) is None
        assert s.illegal_batches[key]["messages"] == ["B", "C"]
    asyncio.run(run())


def test_multiple_batches_keep_old_membership_and_one_successor_recovers_predecessors():
    async def run():
        s, w = setup(["B"])
        first = await die(s, w, await begin(s, w))
        # A later failed execution must never steal a previous batch's lock.
        old = copy.deepcopy(s.offline_run)
        s.offline_run = offline.admit(s, {**old, "token": "second"},
                                     kind="foreground", operation_id="run-2")
        s.queue_pending.append(dict(id="C", type="task", text="C", deliveryState="queued"))
        async with worker.queue_lock(s.id):
            await worker._commit_offline(s, lambda candidate: offline.detect(candidate, "second", eligible=lambda row: (
                worker._delivery_state(row) == "queued" and not worker._queue_item_locked(row)),
                remember=lambda row: worker._remember_offline_lock(candidate, row)))
        assert s.queue_pending[0]["queueLockIllegalBatch"] == first
        assert s.illegal_batches["run-2"]["messages"] == ["C"]
        w.offline_token = "third"; w.generation += 1; w.process.returncode = None
        await worker._activate_offline_worker(w, ready_proof=True)
        assert all(queue_locks.source(row) is None for row in s.queue_pending)
        assert all(batch["recovered"] for batch in s.illegal_batches.values())
    asyncio.run(run())


def test_late_owner_cannot_finish_detect_or_recover_after_generation_replacement():
    async def run():
        s, w = setup(["B"])
        old = await begin(s, w)
        await die(s, w, old)
        worker._bump_worker_generation(w)
        w.process.returncode = None
        # Old callbacks cannot mark True or add another event, including same object.
        assert await worker._persist_terminal_state(w, s, "done", "late", expected_owner=old) is None
        await worker._detect_offline_exit(w, old, "late")
        assert offline.is_illegal(s) and len(s.illegal_batches) == 1
        await worker._activate_offline_worker(w, ready_proof=True)
        new = await worker._begin_offline_run(w)
        assert not offline.finish(s, old, reason="late", state="idle")
        assert not s.legal_offline
        assert new["token"] != old["token"]
    asyncio.run(run())


@pytest.mark.parametrize("point", ["begin", "detect", "activate", "terminal"])
def test_failed_atomic_replace_rolls_back_and_reload_never_sees_partial_facts(monkeypatch, point):
    async def run():
        s, w = setup(["B"])
        if point != "begin":
            identity = await begin(s, w)
        if point == "activate":
            await die(s, w, identity)
            worker._bump_worker_generation(w); w.process.returncode = None
        before = offline.snapshot(s)
        disk = sess._path(s.id).read_bytes()
        real_replace = sess.os.replace
        def fail(source, destination):
            if str(destination) == str(sess._path(s.id)):
                raise OSError("injected atomic replace failure")
            real_replace(source, destination)
        monkeypatch.setattr(sess.os, "replace", fail)
        with pytest.raises(OSError):
            if point == "begin":
                await worker._begin_offline_run(w)
            elif point == "detect":
                await die(s, w, identity)
            elif point == "activate":
                await worker._activate_offline_worker(w, ready_proof=True)
            else:
                await worker._persist_terminal_state(w, s, "error", "429")
        assert offline.snapshot(s) == before
        assert sess._path(s.id).read_bytes() == disk
        assert not offline.is_illegal(s) if point != "activate" else offline.is_illegal(s)
        w.process.stdin.write.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("fail", [False, True])
def test_cancelled_writer_retires_before_next_metadata_save(monkeypatch, fail):
    async def run():
        s, w = setup(["B"])
        identity = await begin(s, w)
        before = offline.snapshot(s)
        entered, release = threading.Event(), threading.Event()
        real_save = sess._save_body
        def delayed(candidate, **kwargs):
            if (kwargs.get("offline_override", {}).get("offline_run") or {}).get("state") == "illegal":
                entered.set()
                assert release.wait(5)
                if fail:
                    raise OSError("failed cancelled writer")
            return real_save(candidate, **kwargs)
        monkeypatch.setattr(sess, "_save_body", delayed)
        w.process.returncode = 1
        task = asyncio.create_task(worker._detect_offline_exit(w, identity, "EOF"))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        later = asyncio.create_task(sess.save_async(s))
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await later
        durable = json.loads(sess._path(s.id).read_text(encoding="utf-8"))
        if fail:
            assert offline.snapshot(s) == before
            assert not durable["illegal_batches"]
        else:
            assert len(durable["illegal_batches"]) == 1
            assert durable["queue_pending"][0]["queueLockSource"] == "illegal"
    asyncio.run(run())


def test_crash_recovery_normalizes_lost_reservation_and_ledger_before_snapshot():
    async def run():
        s, w = setup(["B", "R"])
        identity = await begin(s, w)
        s.queue_pending[1].update(deliveryState="reserved", reservedBy="lost", reservedGeneration=0)
        s.queue_delivery_ledger["L"] = dict(id="L", type="task", text="ledger", deliveryState="queued")
        sess.save(s)
        worker.workers.clear(); worker._workers_by_session.clear()
        loaded = reload(s)
        await worker._prepare_offline_recovery(loaded)
        assert set(loaded.illegal_batches[loaded.offline_run["id"]]["messages"]) == {"B", "R", "L"}
        assert len(loaded.illegal_batches) == 1
        await worker._prepare_offline_recovery(loaded)
        assert loaded.offline_run["token"] == identity["token"]
    asyncio.run(run())


def test_unknown_or_surviving_owner_refuses_replacement(monkeypatch):
    async def run():
        s, w = setup(["B"])
        await begin(s, w)
        worker.workers.clear(); worker._workers_by_session.clear()
        for status in ("alive", "unknown"):
            monkeypatch.setattr(offline, "previous_owner_status", lambda run: status)
            with pytest.raises(RuntimeError, match="second writer"):
                await worker._prepare_offline_recovery(s)
            assert not s.illegal_batches and not s.legal_offline
    asyncio.run(run())


def test_stop_reports_remains_independent_of_manual_and_illegal():
    item = {"type": "report"}
    queue_locks.set_lock(item, "illegal", batch="old")
    queue_locks.manual_change(item, None, True)
    assert queue_locks.source(item) == "illegal" and item["queueLockAutoReport"]
    assert item["queueLockIllegalBatch"] == "old"
    queue_locks.manual_change(item, None, False)
    assert queue_locks.source(item) == "illegal"
    queue_locks.manual_change(item, None, True)
    queue_locks.manual_change(item, True, None)
    queue_locks.manual_change(item, None, False)
    assert queue_locks.source(item) == "manual" and not item.get("queueLockAutoReport")
    item["queueSteerPending"] = True
    queue_locks.manual_change(item, False, False)
    assert queue_locks.source(item) is None and worker._queue_item_locked(item)


def test_migration_missing_legality_never_copies_old_state_or_rewrites_new_locks():
    for state in ("idle", "running", None):
        data = sess.create("legacy").to_dict()
        for key in ("legal_offline", "offline_run", "illegal_batches"):
            data.pop(key)
        data["last_legal_worker_state"] = state
        data["queue_pending"] = [{"id": "M", "locked": True}, {"id": "U", "locked": False}]
        migrated = sess.Session._from_data(copy.deepcopy(data))
        assert migrated.legal_offline and migrated.offline_run is None
        assert migrated.offline_migration_uncertainty == "legacy-missing-legality"
        assert queue_locks.source(migrated.queue_pending[0]) == "manual"
        assert queue_locks.source(migrated.queue_pending[1]) is None
        queue_locks.set_lock(migrated.queue_pending[0], "illegal", batch="new")
        again = sess.Session._from_data(migrated.to_dict())
        assert again.queue_pending[0]["queueLockIllegalBatch"] == "new"
        assert again.offline_migration_uncertainty == migrated.offline_migration_uncertainty


def test_edit_lease_remains_independent_after_recovery_and_save_keeps_lock():
    async def run():
        s, w = setup(["B"])
        result = await server.api_session_queue_edit_lock(s.id, "B", {"editToken": "editor", "expectedRevision": 1})
        assert result["ok"]
        await die(s, w, await begin(s, w))
        worker._bump_worker_generation(w); w.process.returncode = None
        await worker._activate_offline_worker(w, ready_proof=True)
        assert worker.queue_item_edit_locked(s, "B")
        assert worker._select_queue_unit(s) is None
        assert queue_locks.source(s.queue_pending[0]) is None
    asyncio.run(run())


def test_false_persistence_failure_prevents_actual_stream_write(monkeypatch):
    async def run():
        s, w = setup()
        before = sess._path(s.id).read_bytes()
        def fail(candidate, **kwargs):
            raise OSError("False was not durable")
        monkeypatch.setattr(sess, "_save_body", fail)
        with pytest.raises(OSError):
            await worker._consumer_stream(w, "must not execute", "user", s)
        w.process.stdin.write.assert_not_called()
        assert s.legal_offline and sess._path(s.id).read_bytes() == before
    asyncio.run(run())


def test_pause_before_handoff_is_safe_and_does_not_leave_false():
    async def run():
        s, w = setup(["B"])
        await begin(s, w)
        s.queue_paused = True
        operation = AsyncMock()
        with pytest.raises(worker._QueuePausedBeforeHandoff):
            await worker._queue_handoff(w, s, operation, None)
        operation.assert_not_awaited()
        assert s.legal_offline
        w.process.returncode = 1
        await worker._detect_offline_exit(w, offline.owner(w), "after-reject")
        assert not s.illegal_batches
    asyncio.run(run())


def test_late_detection_waiting_for_writer_cannot_mutate_replacement(monkeypatch):
    async def run():
        s, w = setup(["B"])
        old = await begin(s, w)
        entered, release = threading.Event(), threading.Event()
        def preceding():
            entered.set()
            assert release.wait(5)
        ticket = asyncio.create_task(sess._persist_async_outcome(s.id, preceding))
        assert await asyncio.to_thread(entered.wait, 5)
        detect = asyncio.create_task(worker._detect_offline_exit(w, old, "late"))
        await asyncio.sleep(0)
        worker._bump_worker_generation(w)
        release.set()
        await ticket; await detect
        assert not s.illegal_batches
        assert not s.queue_pending[0].get("queueLockSource")
    asyncio.run(run())


@pytest.mark.parametrize("transition", ["begin", "detect", "terminal", "activate"])
def test_http_projection_never_observes_uncommitted_legality_or_batch(monkeypatch, transition):
    async def run():
        s, w = setup(["B"])
        if transition != "begin":
            identity = await begin(s, w)
        if transition == "activate":
            await die(s, w, identity)
            worker._bump_worker_generation(w); w.process.returncode = None
        before = offline.snapshot(s)
        before_api = server._session_summary(s)
        entered, release = threading.Event(), threading.Event()
        real_replace = sess.os.replace
        def delayed(source, destination):
            if str(destination) == str(sess._path(s.id)):
                entered.set()
                assert release.wait(5)
            return real_replace(source, destination)
        monkeypatch.setattr(sess.os, "replace", delayed)
        if transition == "begin":
            task = asyncio.create_task(worker._begin_offline_run(w))
        elif transition == "detect":
            w.process.returncode = 1
            task = asyncio.create_task(worker._detect_offline_exit(w, identity, "EOF"))
        elif transition == "activate":
            task = asyncio.create_task(worker._activate_offline_worker(w, ready_proof=True))
        else:
            task = asyncio.create_task(worker._persist_terminal_state(w, s, "error", "429"))
        assert await asyncio.to_thread(entered.wait, 5)
        try:
            assert s.legal_offline == before["legal_offline"]
            assert s.offline_run == before["offline_run"]
            assert s.illegal_batches == before["illegal_batches"]
            current_api = server._session_summary(s)
            assert current_api["legalOffline"] == before_api["legalOffline"]
            assert current_api["illegalOffline"] == before_api["illegalOffline"]
            assert s.queue_pending == before["queue_pending"]
        finally:
            release.set()
        await task
        assert s.offline_run != before["offline_run"]
    asyncio.run(run())


@pytest.mark.parametrize("latest", [True, False])
def test_durable_terminal_duplicate_settles_current_run_without_republishing(latest):
    async def run():
        s, w = setup()
        await begin(s, w)
        w._current_task_id = "idempotent-task"
        w._current_task_idempotent = True
        w._current_seq = 1
        terminal = await worker._persist_terminal_state(w, s, "done", "done")
        assert terminal is not None and s.legal_offline
        if not latest:
            s.last_result = None
        w._terminal_handled = False
        w._execution_seq += 1
        await worker._begin_offline_run(w)
        assert not s.legal_offline
        duplicate = await worker._persist_terminal_state(w, s, "done", "duplicate")
        assert duplicate is None
        assert s.legal_offline
        assert len(s.terminal_results) == 1
    asyncio.run(run())


def test_concurrent_enqueue_unlock_and_manual_claim_serialize_after_exact_snapshot(monkeypatch):
    async def run():
        s, w = setup(["B", "C"])
        identity = await begin(s, w)
        entered, release = threading.Event(), threading.Event()
        real_replace = sess.os.replace
        def blocked(source, destination):
            if str(destination) == str(sess._path(s.id)) and not entered.is_set():
                entered.set()
                assert release.wait(5)
            return real_replace(source, destination)
        monkeypatch.setattr(sess.os, "replace", blocked)
        w.process.returncode = 1
        detection = asyncio.create_task(worker._detect_offline_exit(w, identity, "EOF"))
        assert await asyncio.to_thread(entered.wait, 5)
        enqueue = asyncio.create_task(worker.enqueue_user_message(s.id, "D", "D"))
        unlock = asyncio.create_task(server.api_session_queue_item_lock(s.id, "B", {"locked": False}))
        claim = asyncio.create_task(server.api_session_queue_item_lock(s.id, "C", {"locked": True}))
        await asyncio.sleep(0)
        assert not enqueue.done() and not unlock.done() and not claim.done()
        release.set()
        await asyncio.wait_for(asyncio.gather(detection, enqueue, unlock, claim), 5)
        batch = next(iter(s.illegal_batches.values()))
        assert batch["messages"] == ["B", "C"]
        rows = {row["text"]: row for row in s.queue_pending}
        assert queue_locks.source(rows["B"]) is None
        assert queue_locks.source(rows["C"]) == "manual"
        assert queue_locks.source(rows["D"]) is None
        loaded = reload(s)
        assert loaded.illegal_batches == s.illegal_batches
        assert queue_locks.source(loaded.queue_delivery_ledger["C"]) == "manual"
    asyncio.run(run())


@pytest.mark.parametrize("same_pid_identity", [True, False])
def test_surviving_idle_owner_is_legal_but_not_permission_for_second_writer(same_pid_identity):
    async def run():
        s, w = setup()
        await worker._activate_offline_worker(w, ready_proof=True)
        s.offline_run.update(serviceId="previous-pan-process", processes=[{
            "pid": os.getpid(),
            "createdAt": psutil.Process().create_time() + (0 if same_pid_identity else 1),
        }])
        worker.workers.clear(); worker._workers_by_session.clear()
        if same_pid_identity:
            with pytest.raises(RuntimeError, match="idle owner is alive"):
                await worker._prepare_offline_recovery(s)
        else:
            await worker._prepare_offline_recovery(s)  # PID reuse is not the old owner.
        assert s.legal_offline and not s.illegal_batches
    asyncio.run(run())


def test_constructed_idle_needs_proof_and_ready_preserves_current_run_and_boundary():
    async def run():
        s, w = setup(["B"])
        first = await die(s, w, await begin(s, w))
        worker._bump_worker_generation(w)
        w.process.returncode = None
        w._offline_predecessors = {first}
        await worker._activate_offline_worker(w)
        assert offline.is_illegal(s) and not s.illegal_batches[first]["recovered"]
        # A new unlocked item may begin before a lazy adapter emits init.
        w._execution_seq += 1
        identity = await worker._begin_offline_run(w)
        run_id = s.offline_run["id"]
        s.illegal_batches["after-boundary"] = {
            "id": "after-boundary", "run": {"token": "later-owner"},
            "messages": [], "recovered": False}
        s.queue_pending[0]["queueLockAutoReport"] = True
        await worker._activate_offline_worker(w, ready_proof=True)
        assert s.illegal_batches[first]["recovered"]
        assert not s.illegal_batches["after-boundary"]["recovered"]
        assert s.offline_run["id"] == run_id and not s.legal_offline
        assert offline.same_owner(s.offline_run, identity)
        assert queue_locks.source(s.queue_pending[0]) is None
        assert worker._queue_item_locked(s.queue_pending[0])  # Report policy retained.
        loaded = reload(s)
        assert loaded.queue_delivery_ledger["B"]["queueLockAutoReport"]
        assert loaded.queue_revision == s.queue_revision
    asyncio.run(run())


def test_all_illegal_locked_initialization_and_watchdog_do_not_spawn(monkeypatch):
    async def run():
        s, w = setup(["B"])
        await die(s, w, await begin(s, w))
        worker.workers.clear(); worker._workers_by_session.clear()
        create = AsyncMock()
        monkeypatch.setattr(worker, "create_worker", create)
        monkeypatch.setattr(worker, "_list_sessions_shallow", lambda: [s])
        await worker.initialize_legal_offline([s])
        await worker._global_watchdog_tick()
        assert worker._queue_recovery_blocked(s)
        assert s.id not in worker._recovery_required
        create.assert_not_awaited()
        assert not next(iter(s.illegal_batches.values()))["recovered"]
    asyncio.run(run())


@pytest.mark.parametrize("status", ["alive", "unknown", "dead"])
def test_unready_owner_registration_is_durable_and_blocks_second_writer(monkeypatch, status):
    async def run():
        s, w = setup(["B"])
        key = await die(s, w, await begin(s, w))
        worker._bump_worker_generation(w); w.process.returncode = None
        await worker._claim_offline_owner(w)
        assert s.offline_owner["token"] == w.offline_token
        assert not s.illegal_batches[key]["recovered"] and offline.is_illegal(s)
        loaded = reload(s)
        worker.workers.clear(); worker._workers_by_session.clear()
        monkeypatch.setattr(offline, "previous_owner_status", lambda _: status)
        if status == "dead":
            await worker._prepare_offline_recovery(loaded)
        else:
            with pytest.raises(RuntimeError, match="second writer"):
                await worker._prepare_offline_recovery(loaded)
        assert loaded.offline_owner == s.offline_owner
    asyncio.run(run())


def test_failed_session_recovery_does_not_abort_watchdog_for_other_sessions(monkeypatch):
    async def run():
        bad, w = setup()
        bad.offline_owner = {"token": "unknown", "generation": 1,
                             "ownershipUnknown": True, "processes": []}
        good = sess.create("good")
        good.queue_pending = [{"id": "good", "type": "task", "text": "good"}]
        worker.workers.clear(); worker._workers_by_session.clear()
        monkeypatch.setattr(worker, "_list_sessions_shallow", lambda: [bad, good])
        create = AsyncMock(return_value=None)
        monkeypatch.setattr(worker, "create_worker", create)
        await worker._global_watchdog_tick()
        create.assert_awaited_once_with(good.id)
        assert not bad.illegal_batches
    asyncio.run(run())


def test_native_admission_persists_false_rejects_stale_and_deduplicates():
    async def run():
        s, w = setup()
        runtime = (w, w.generation, w.process, w.process.stdin)
        await worker._admit_native_turn(w, runtime, request_id="request-1")
        value = json.loads(sess._path(s.id).read_text(encoding="utf-8"))
        assert value["legal_offline"] is False
        run_id = value["offline_run"]["id"]
        await worker._admit_native_turn(w, runtime, request_id="request-1")
        assert s.offline_run["id"] == run_id and w._execution_seq == 1
        worker._bump_worker_generation(w)
        with pytest.raises(RuntimeError, match="retired"):
            await worker._admit_native_turn(w, runtime, request_id="stale")
        assert s.offline_run["id"] == run_id
    asyncio.run(run())


@pytest.mark.parametrize("failure", [False, True])
def test_cbc_hook_waits_for_atomic_pan_admission_before_returning(monkeypatch, failure):
    from packages.core.adapters.cbc.native_bridge import NativeBridge
    from packages.core.adapters.codex.control_owner import ControlRejected
    async def run():
        s, w = setup()
        runtime = (w, w.generation, w.process, w.process.stdin)
        loop = asyncio.get_running_loop()
        requests = []
        async def admit(event):
            ok = False
            try:
                await worker._admit_native_turn(w, runtime, request_id=event["requestId"])
                assert not json.loads(sess._path(s.id).read_text(encoding="utf-8"))["legal_offline"]
                ok = True
            except OSError:
                pass
            bridge.control({"type": "native_admission", "requestId": event["requestId"], "ok": ok})
        def emit(event):
            if event["type"] == "pan.native_turn_admission":
                requests.append(asyncio.run_coroutine_threadsafe(admit(event), loop))
        bridge = NativeBridge(Mock(), emit, "cli")
        bridge.hook({"hook_event_name": "SessionStart", "session_id": "cli"})
        bridge.control({"type": "control_mode", "mode": "native", "generation": 0})
        entered, release = threading.Event(), threading.Event()
        replace = sess.os.replace
        def blocked(source, destination):
            if str(destination) == str(sess._path(s.id)):
                entered.set()
                assert release.wait(5)
                if failure:
                    raise OSError("admission durable write failure")
            return replace(source, destination)
        monkeypatch.setattr(sess.os, "replace", blocked)
        hook = asyncio.create_task(asyncio.to_thread(bridge.hook, {
            "hook_event_name": "UserPromptSubmit", "session_id": "cli", "generation_id": "native-1"}))
        assert await asyncio.to_thread(entered.wait, 5)
        assert not hook.done() and bridge.turn is None
        assert json.loads(sess._path(s.id).read_text(encoding="utf-8"))["legal_offline"]
        release.set()
        if failure:
            with pytest.raises(ControlRejected, match="admission rejected"):
                await hook
            assert s.legal_offline and bridge.turn is None
        else:
            await hook
            assert not s.legal_offline and bridge.turn == "native-1"
            await asyncio.to_thread(bridge.hook, {
                "hook_event_name": "UserPromptSubmit", "session_id": "cli", "generation_id": "native-1"})
            assert len(requests) == 1
    asyncio.run(run())


def test_unknown_identity_and_external_takeover_reservation_fail_closed():
    assert offline.previous_owner_status({"runtimeExpected": True, "processes": []}) == "unknown"
    async def run():
        s, w = setup()
        w.status = "held"
        await worker.reserve_takeover_owner(w)
        assert reload(s).offline_owner["ownershipUnknown"]
        with pytest.raises(RuntimeError, match="second writer"):
            await worker._kill_takeover_terminal(w)  # Reserved launch has no proven owner PID.
        worker.workers.clear(); worker._workers_by_session.clear()
        with pytest.raises(RuntimeError, match="second writer"):
            await worker._prepare_offline_recovery(s)
        assert s.legal_offline and not s.illegal_batches
    asyncio.run(run())


def test_late_ready_proof_cannot_recover_new_generation():
    async def run():
        s, w = setup(["B"])
        old = await begin(s, w)
        key = await die(s, w, old)
        worker._bump_worker_generation(w); w.process.returncode = None
        await worker._activate_offline_worker(w, ready_proof=True, expected_owner=old)
        assert not s.illegal_batches[key]["recovered"] and offline.is_illegal(s)
        assert w._offline_ready_token != w.offline_token
    asyncio.run(run())


@pytest.mark.parametrize("missing_root", [False, True])
def test_legacy_takeover_kill_return_is_not_retirement_proof(monkeypatch, missing_root):
    async def run():
        s, w = setup()
        w.status = "held"
        await worker.reserve_takeover_owner(w)
        w.takeover_pid = 2**30 if missing_root else os.getpid()
        original_pid = w.takeover_pid
        # Deliberately no-op: a swallowed kill failure cannot release ownership.
        monkeypatch.setattr(worker, "_kill_pid_tree", lambda _: None)
        with pytest.raises(RuntimeError, match="second writer"):
            await worker._kill_takeover_terminal(w)
        assert w.takeover_pid == original_pid
        assert s.offline_owner["ownershipUnknown"]
    asyncio.run(run())


def test_lock_migration_preserves_explicit_source_and_independent_report_policy():
    rows = [{"queueLockSource": "manual", "queueLockAutoReport": True},
            {"queueLockSource": "illegal", "queueLockIllegalBatch": "new", "queueLockAutoReport": True},
            {"queueLockAutoReport": True}]
    for row in rows:
        before = copy.deepcopy(row)
        queue_locks.migrate(row)
        queue_locks.migrate(row)
        assert row == before
    assert queue_locks.source(rows[2]) is None and worker._queue_item_locked(rows[2])
