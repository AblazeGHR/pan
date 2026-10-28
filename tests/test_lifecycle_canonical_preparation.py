"""Canonical lifecycle preparation: original safety assertions at the real route."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from packages.core import lifecycle_action, worker
from packages.jobs import independent_executor
from packages.jobs.independent_executor import ExecutionStore
from packages.web import server


def run_and_drain(coroutine):
    async def run():
        result = await coroutine
        tasks = set(server.app.state.canonical_lifecycle_tasks)
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        return result
    return asyncio.run(run())


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(server, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(server, "_main_restart_port", lambda: 48321)
    monkeypatch.setattr(server, "_canonical_lifecycle_pending", {})
    monkeypatch.setattr(server, "_lifecycle_operation_lock", asyncio.Lock())
    monkeypatch.setattr(server.app.state, "canonical_lifecycle_tasks", set(), raising=False)
    monkeypatch.setattr(server, "_session_lifecycle_preferences", lambda config=None: {
        "exitStrategy": "ask", "startupPreference": "ask"})
    monkeypatch.setattr(server.sess, "list_all", lambda **kw: [
        SimpleNamespace(id="running-b", last_legal_worker_state="idle",
            execution_operations={"run": {"state": "running", "safeTerminal": False}}),
        SimpleNamespace(id="running-a", last_legal_worker_state="idle",
            execution_operations={"run": {"state": "running", "safeTerminal": False}})])
    def gate():
        store = ExecutionStore(lifecycle_action.receipt_root(tmp_path))
        with store.connect() as db:
            docs = [json.loads(row[0]) for row in db.execute("SELECT document FROM executions")]
        assert len(docs) == 1 and docs[0]["phase"] == "admitted"
        assert docs[0]["frozenPlan"]["arguments"]["options"]["runningSessionIds"] == ["running-a", "running-b"]
        events.append(("gate", None))
    async def stop(**kwargs):
        events.append(("shutdown", kwargs))
    monkeypatch.setattr(worker, "begin_shutdown", gate)
    monkeypatch.setattr(worker, "shutdown_all", stop)
    monkeypatch.setattr(worker, "reopen_after_failed_shutdown", lambda: events.append(("reopen", None)))
    def launch(root, eid):
        current = ExecutionStore(root).get(eid)
        assert current.get("launchAttempted") is True and "preparationToken" not in current
        assert [event[0] for event in events] == ["gate", "shutdown"]
        events.append(("launch", eid))
    monkeypatch.setattr(independent_executor, "launch_execution", launch)
    return SimpleNamespace(root=tmp_path, events=events, monkeypatch=monkeypatch)


@pytest.mark.parametrize("operation", ["exit", "restart"])
def test_ask_without_choice_rejects_before_admission_or_worker_gate(prepared, operation):
    with pytest.raises(server.HTTPException) as caught:
        asyncio.run(server.api_system_main_lifecycle({"operation": operation}))
    assert caught.value.status_code == 400 and caught.value.detail["code"] == "lifecycle_choice_required"
    assert not lifecycle_action.receipt_root(prepared.root).exists() and prepared.events == []


@pytest.mark.parametrize("operation,strategy,choice,mark", [
    ("exit", "ask", True, True), ("exit", "ask", False, False),
    ("exit", "offline", False, True), ("restart", "preserve-running", True, False),
])
def test_freeze_before_gate_and_worker_shutdown_before_supervisor(prepared, operation, strategy, choice, mark):
    prepared.monkeypatch.setattr(server, "_session_lifecycle_preferences", lambda config=None: {
        "exitStrategy": strategy, "startupPreference": "ask"})
    result = run_and_drain(server.api_system_main_lifecycle({"operation": operation,
        "options": {"markRunningSessionsOffline": choice}}))
    assert result["accepted"]
    assert [event[0] for event in prepared.events] == ["gate", "shutdown", "launch"]
    args = prepared.events[1][1]
    assert args["mark_legal_offline"] is True
    assert list(args["mark_legal_offline_session_ids"]) == (["running-a", "running-b"] if mark else [])
    assert list(args["preserve_legal_running_session_ids"]) == ([] if mark else ["running-a", "running-b"])
    doc = ExecutionStore(lifecycle_action.receipt_root(prepared.root)).get(result["effectId"])
    options = doc["frozenPlan"]["arguments"]["options"]
    assert options["exitStrategy"] == strategy and options["markRunningSessionsOffline"] is mark
    assert options["runningSessionIds"] == ["running-a", "running-b"] and doc["result"] is None


def test_duplicate_and_cross_operation_reject_while_original_effect_queryable(prepared):
    async def exercise():
        first = await server.api_system_main_lifecycle({"operation": "exit", "options": {"markRunningSessionsOffline": True}})
        duplicate = await server.api_system_main_lifecycle({"operation": "exit", "options": {"markRunningSessionsOffline": False}})
        other = await server.api_system_main_lifecycle({"operation": "restart", "options": {"markRunningSessionsOffline": True}})
        return first, duplicate, other
    first, duplicate, other = run_and_drain(exercise())
    assert first["accepted"] and not duplicate["ok"] and duplicate["status"] == "busy"
    assert not other["ok"] and other["status"] == "busy"
    assert duplicate["effectId"] == other["effectId"] == first["effectId"]
    assert [item[0] for item in prepared.events] == ["gate", "shutdown", "launch"]
    assert ExecutionStore(lifecycle_action.receipt_root(prepared.root)).get(first["effectId"])["result"] is None


def test_concurrent_exit_and_restart_share_one_worker_preparation(prepared):
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked_shutdown(**kwargs):
            prepared.events.append(("shutdown", kwargs))
            entered.set()
            await release.wait()
        prepared.monkeypatch.setattr(worker, "shutdown_all", blocked_shutdown)
        first = asyncio.create_task(server.api_system_main_lifecycle({"operation": "exit",
            "options": {"markRunningSessionsOffline": True}}))
        await entered.wait()
        second = asyncio.create_task(server.api_system_main_lifecycle({"operation": "restart",
            "options": {"markRunningSessionsOffline": False}}))
        await asyncio.sleep(0)
        release.set()
        return await asyncio.gather(first, second)
    first, second = run_and_drain(exercise())
    assert first["accepted"] and second["status"] == "busy"
    assert second["effectId"] == first["effectId"]
    assert [row[0] for row in prepared.events] == ["gate", "shutdown", "launch"]


@pytest.mark.parametrize("again", ["exit", "restart"])
def test_lost_memory_still_fences_duplicate_or_cross_operation(prepared, again):
    async def exercise():
        first = await server.api_system_main_lifecycle({"operation": "exit",
            "options": {"markRunningSessionsOffline": True}})
        server._canonical_lifecycle_pending.clear()
        # A conflicting choice must query the original effect; it must not
        # change the frozen plan or create a new operation after memory loss.
        second = await server.api_system_main_lifecycle({"operation": again,
            "options": {"markRunningSessionsOffline": False}})
        return first, second
    first, second = run_and_drain(exercise())
    assert first["accepted"] and not second["accepted"] and second["status"] == "busy"
    assert second["effectId"] == first["effectId"] and second["requestId"] == first["requestId"]
    assert [row[0] for row in prepared.events] == ["gate", "shutdown", "launch"]
    doc = ExecutionStore(lifecycle_action.receipt_root(prepared.root)).get(first["effectId"])
    assert doc["frozenPlan"]["arguments"]["options"]["markRunningSessionsOffline"] is True
    assert doc["result"] is None


def test_partial_worker_failure_is_unknown_and_never_launches_or_reopens(prepared):
    async def fail_after_partial(**kwargs):
        prepared.events.append(("partial-shutdown", kwargs))
        raise RuntimeError("owned Worker shutdown failed")
    prepared.monkeypatch.setattr(worker, "shutdown_all", fail_after_partial)
    result = run_and_drain(server.api_system_main_lifecycle({"operation": "exit",
        "options": {"markRunningSessionsOffline": True}}))
    assert result["accepted"] is True and result["status"] == "scheduled"
    assert [row[0] for row in prepared.events] == ["gate", "partial-shutdown"]
    store = ExecutionStore(lifecycle_action.receipt_root(prepared.root))
    doc = store.get(result["effectId"])
    assert doc["result"]["outcome"] == "unknown" and doc["result"]["cleanupProof"]["confirmed"] is False
    receipt = doc["result"]["receipt"]
    assert receipt["code"] == "worker_preparation_failed"
    assert receipt["mainEffectStarted"] is False and receipt["preparationMayHaveChanged"] is True
    assert receipt["error"] == {"type": "RuntimeError", "message": "owned Worker shutdown failed"}
    assert len(store.events()["events"]) == 1
    duplicate = asyncio.run(server.api_system_main_lifecycle({"operation": "restart",
        "options": {"markRunningSessionsOffline": False}}))
    assert duplicate["status"] == "busy" and duplicate["effectId"] == doc["effectId"]
    assert [row[0] for row in prepared.events] == ["gate", "partial-shutdown"]


def test_ambiguous_supervisor_launch_is_unknown_and_is_not_replayed(prepared):
    def ambiguous(root, eid):
        doc = ExecutionStore(root).get(eid)
        assert doc["launchAttempted"] is True and "preparationToken" not in doc
        prepared.events.append(("launch-attempt", eid))
        raise OSError("ambiguous detached launch")
    prepared.monkeypatch.setattr(independent_executor, "launch_execution", ambiguous)
    result = run_and_drain(server.api_system_main_lifecycle({"operation": "exit",
        "options": {"markRunningSessionsOffline": True}}))
    assert result["accepted"] is True and result["status"] == "scheduled"
    store = ExecutionStore(lifecycle_action.receipt_root(prepared.root))
    doc = store.get(result["effectId"])
    assert doc["result"]["outcome"] == "unknown" and doc["result"]["cleanupProof"]["confirmed"] is False
    assert doc["result"]["receipt"]["code"] == "launch_unconfirmed"
    again = asyncio.run(server.api_system_main_lifecycle({"operation": "exit",
        "options": {"markRunningSessionsOffline": True}}))
    assert again["status"] == "busy" and again["effectId"] == doc["effectId"]
    assert [row[0] for row in prepared.events] == ["gate", "shutdown", "launch-attempt"]
    assert len(store.events()["events"]) == 1
