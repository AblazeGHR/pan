"""Strict lifecycle successors: scheduled must precede worker completion."""
import asyncio
import threading

import pytest

from packages.core import lifecycle_action, platform, worker
from packages.jobs import independent_executor
from packages.jobs.independent_executor import ExecutionStore
from packages.web import server
from tests.test_lifecycle_canonical_preparation import prepared


@pytest.mark.parametrize("operation", ["exit", "restart"])
def test_scheduled_response_does_not_wait_for_worker_shutdown(prepared, operation):
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked_shutdown(**kwargs):
            prepared.events.append(("shutdown", kwargs))
            entered.set()
            await release.wait()
        prepared.monkeypatch.setattr(worker, "shutdown_all", blocked_shutdown)
        request = asyncio.create_task(server.api_system_main_lifecycle({"operation": operation,
            "options": {"markRunningSessionsOffline": True}}))
        await asyncio.wait_for(entered.wait(), timeout=10)
        try:
            result = await asyncio.wait_for(asyncio.shield(request), timeout=5)
            assert result["accepted"] and result["status"] == "scheduled"
            assert [row[0] for row in prepared.events] == ["gate", "shutdown"]
            same = await server.api_system_main_lifecycle({"operation": operation,
                "options": {"markRunningSessionsOffline": False}})
            cross = await server.api_system_main_lifecycle({"operation": "restart" if operation == "exit" else "exit",
                "options": {"markRunningSessionsOffline": False}})
            assert not same["ok"] and same["status"] == "busy"
            assert not cross["ok"] and cross["status"] == "busy"
            assert same["requestId"] == cross["requestId"] == result["requestId"]
            assert same["effectId"] == cross["effectId"] == result["effectId"]
            tasks = set(server.app.state.canonical_lifecycle_tasks)
            assert len(tasks) == 1
        finally:
            release.set()
            await request
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        assert [row[0] for row in prepared.events] == ["gate", "shutdown", "launch"]
    asyncio.run(exercise())


def test_task_creation_failure_preserves_original_unknown_without_launch(prepared):
    def fail_task(*args, **kwargs):
        raise RuntimeError("cannot schedule owned lifecycle task")
    prepared.monkeypatch.setattr(server.asyncio, "create_task", fail_task)
    result = asyncio.run(server.api_system_main_lifecycle({"operation": "exit",
        "options": {"markRunningSessionsOffline": True}}))
    assert result["accepted"] is False and result["error"]["code"] == "worker_preparation_failed"
    doc = ExecutionStore(lifecycle_action.receipt_root(prepared.root)).get(result["effectId"])
    assert doc["result"]["outcome"] == "unknown"
    assert doc["result"]["receipt"]["error"]["message"] == "cannot schedule owned lifecycle task"
    assert doc["result"]["receipt"]["preparationMayHaveChanged"] is True
    assert [row[0] for row in prepared.events] == ["gate"]


def test_cancelled_before_task_starts_is_fenced_and_durable(prepared):
    async def exercise():
        response = await server.api_system_main_lifecycle({"operation": "exit",
            "options": {"markRunningSessionsOffline": True}})
        task, = server.app.state.canonical_lifecycle_tasks
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Let the registered done callback run without a timing assumption.
        done = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(done.set_result, None)
        await done
        store = ExecutionStore(lifecycle_action.receipt_root(prepared.root))
        doc = store.get(response["effectId"])
        assert doc["result"]["outcome"] == "unknown"
        assert doc["result"]["receipt"]["error"]["type"] == "CancelledError"
        assert doc["result"]["cleanupProof"]["confirmed"] is False
        assert len(store.events()["events"]) == 1
        assert [row[0] for row in prepared.events] == ["gate"]
    asyncio.run(exercise())


def test_cancel_during_ambiguous_launch_preserves_original_unknown(prepared):
    entered, release = threading.Event(), threading.Event()
    def blocked_launch(root, eid):
        prepared.events.append(("launch-attempt", eid))
        entered.set()
        assert release.wait(10)
    prepared.monkeypatch.setattr(independent_executor, "launch_execution", blocked_launch)
    async def exercise():
        response = await server.api_system_main_lifecycle({"operation": "exit",
            "options": {"markRunningSessionsOffline": True}})
        assert await asyncio.to_thread(entered.wait, 10)
        task, = server.app.state.canonical_lifecycle_tasks
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        store = ExecutionStore(lifecycle_action.receipt_root(prepared.root))
        doc = store.get(response["effectId"])
        assert doc["launchAttempted"] is True and doc["result"]["outcome"] == "unknown"
        assert doc["result"]["receipt"]["code"] == "launch_unconfirmed"
        assert doc["result"]["cleanupProof"]["confirmed"] is False
        assert len(store.events()["events"]) == 1
    asyncio.run(exercise())


def test_admission_failure_never_closes_worker_gate(prepared):
    def fail_admit(*args, **kwargs):
        raise OSError("owned store unavailable")
    prepared.monkeypatch.setattr(ExecutionStore, "admit", fail_admit)
    with pytest.raises(OSError, match="owned store unavailable"):
        asyncio.run(server.api_system_main_lifecycle({"operation": "exit",
            "options": {"markRunningSessionsOffline": True}}))
    assert prepared.events == [] and not server.app.state.canonical_lifecycle_tasks


def test_duplicate_during_original_admission_waits_then_returns_original_busy(prepared):
    entered, release = threading.Event(), threading.Event()
    admit = ExecutionStore.admit
    def blocked_admission(self, **kwargs):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("admission test gate timed out")
        return admit(self, **kwargs)
    prepared.monkeypatch.setattr(ExecutionStore, "admit", blocked_admission)
    async def exercise():
        first = asyncio.create_task(server.api_system_main_lifecycle({"operation": "exit",
            "options": {"markRunningSessionsOffline": True}}))
        assert await asyncio.to_thread(entered.wait, 10)
        second = asyncio.create_task(server.api_system_main_lifecycle({"operation": "restart",
            "options": {"markRunningSessionsOffline": False}}))
        try:
            # The first admission owns the route lock; the second is pending
            # before we release the deterministic admission gate.
            loop = asyncio.get_running_loop()
            turn = loop.create_future()
            loop.call_soon(turn.set_result, None)
            await turn
            assert not first.done() and not second.done() and prepared.events == []
        finally:
            release.set()
        a, b = await asyncio.wait_for(asyncio.gather(first, second), timeout=10)
        assert a["accepted"] and b["status"] == "busy" and b["effectId"] == a["effectId"]
        tasks = set(server.app.state.canonical_lifecycle_tasks)
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        assert [row[0] for row in prepared.events] == ["gate", "shutdown", "launch"]
    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["exit", "restart"])
@pytest.mark.skipif(not platform.supports("service.lifecycle"), reason="real detached lifecycle supervisor requires host service.lifecycle capability")
def test_public_scheduled_response_precedes_real_detached_runner_completion(tmp_path, monkeypatch, operation):
    """Real protected executor and detached supervisor; no live service target."""
    import os
    import psutil
    from packages.core.platform.execution_host import supervisor_exit_evidence

    monkeypatch.setattr(server, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(server, "_main_restart_port", lambda: 48321)
    monkeypatch.setattr(server, "_canonical_lifecycle_pending", {})
    monkeypatch.setattr(server, "_lifecycle_operation_lock", asyncio.Lock())
    monkeypatch.setattr(server.app.state, "canonical_lifecycle_tasks", set(), raising=False)
    monkeypatch.setattr(server, "_session_lifecycle_preferences", lambda config=None:
        {"exitStrategy": "ask", "startupPreference": "ask"})
    monkeypatch.setattr(server.sess, "list_all", lambda **kw: [])
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        gate = []
        monkeypatch.setattr(worker, "begin_shutdown", lambda: gate.append("closed"))
        async def blocked_shutdown(**kwargs):
            entered.set()
            await release.wait()
        monkeypatch.setattr(worker, "shutdown_all", blocked_shutdown)
        request = asyncio.create_task(server.api_system_main_lifecycle({"operation": operation,
            "options": {"markRunningSessionsOffline": False}}))
        await asyncio.wait_for(entered.wait(), 10)
        result = await asyncio.wait_for(asyncio.shield(request), 5)
        assert result["accepted"] and result["status"] == "scheduled" and gate == ["closed"]
        store = ExecutionStore(lifecycle_action.receipt_root(tmp_path))
        doc = store.get(result["effectId"])
        assert doc["result"] is None and doc["phase"] == "admitted"
        assert doc["frozenPlan"]["arguments"]["options"] == {
            "markRunningSessionsOffline": False, "exitStrategy": "ask", "runningSessionIds": []}
        assert doc["attempt"]["runId"] == doc["effectKey"]["runId"]
        assert not doc.get("launchAttempted")
        tasks = set(server.app.state.canonical_lifecycle_tasks)
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)
        async def terminal():
            for _ in range(500):
                row = store.get(result["effectId"])
                if row.get("result") is not None:
                    return row
                await asyncio.sleep(.02)
            raise AssertionError("owned detached supervisor did not settle")
        final = await terminal()
        # The target is the pytest process, not an owned Pan main entry: the
        # real identity gate must refuse to stop it. This is no fake success.
        assert final["result"]["outcome"] == "rejected"
        assert final["result"]["receipt"]["code"] == "identity_unverified"
        assert final["result"]["cleanupProof"]["effectStarted"] is False
        assert final["frozenPlan"] == doc["frozenPlan"] and final["attempt"] == doc["attempt"]
        for _ in range(500):
            proof = supervisor_exit_evidence(final["supervisorIdentity"])
            if proof["confirmed"]:
                break
            await asyncio.sleep(.02)
        assert proof["confirmed"] and psutil.pid_exists(os.getpid())
    asyncio.run(exercise())
