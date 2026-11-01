"""Atomic prelaunch rejection, claim races and crash boundaries."""
import asyncio
import copy
import os

import psutil
import pytest

from packages.core import lifecycle_action
from packages.core.main_lifecycle import ProtectedLifecycleExecutor
from packages.jobs.independent_executor import ExecutionConflict, ExecutionStore


def admitted(tmp_path):
    job, identity, plan = lifecycle_action.protected_plan(root=tmp_path, port=48321,
        old_pid=os.getpid(), old_pid_created_at=psutil.Process().create_time(), operation="exit",
        options={"markRunningSessionsOffline": True}, request_id="prelaunch-fixed")
    store = ExecutionStore(lifecycle_action.receipt_root(tmp_path))
    doc, created = store.admit(job_id=job, attempt=identity["attempt"],
                               effect_key=identity["effectKey"], frozen_plan=plan)
    assert created
    return store, doc, identity, plan


def install_token(store, doc, token="same-call"):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        original = store._load(db, doc["effectId"])
        original["preparationToken"] = token
        store._save(db, original)
        db.commit()


def test_prelaunch_original_error_idempotence_and_attempt_fence(tmp_path):
    store, doc, identity, _ = admitted(tmp_path)
    install_token(store, doc)
    attempt, eid = identity["attempt"], doc["effectId"]
    error = {"type": "RuntimeError", "message": "partially stopped"}
    bad = {**attempt, "generation": attempt["generation"] + 1}
    with pytest.raises(ExecutionConflict):
        store.reject_prelaunch(eid, bad, preparation_token="same-call", error=error)
    with pytest.raises(ExecutionConflict):
        store.reject_prelaunch(eid, attempt, preparation_token="different", error=error)
    settled = store.reject_prelaunch(eid, attempt, preparation_token="same-call", error=error)
    assert settled["result"]["outcome"] == "unknown"
    assert settled["result"]["receipt"]["preparationMayHaveChanged"] is True
    assert settled["result"]["receipt"]["mainEffectStarted"] is False
    assert settled["result"]["cleanupProof"]["confirmed"] is False
    assert store.reject_prelaunch(eid, attempt, preparation_token="same-call", error=error)["result"] == settled["result"]
    with pytest.raises(ExecutionConflict):
        store.reject_prelaunch(eid, attempt, preparation_token="same-call", error={"type": "Other", "message": "changed"})
    assert store.get(eid)["result"] == settled["result"] and len(store.events()["events"]) == 1


@pytest.mark.parametrize("state", ["claimed", "launch-attempted-without-claim"])
def test_claim_or_launch_marker_forbids_prelaunch_rejection(tmp_path, state):
    store, doc, identity, _ = admitted(tmp_path)
    install_token(store, doc)
    eid, attempt = doc["effectId"], identity["attempt"]
    if state == "claimed":
        store.claim(eid, attempt, {"pid": os.getpid(), "birth": "owned-test"})
    else:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            original = store._load(db, eid)
            original["launchAttempted"] = True
            store._save(db, original)
            db.commit()
    before = store.get(eid)
    with pytest.raises(ExecutionConflict):
        store.reject_prelaunch(eid, attempt, preparation_token="same-call", error={"type": "Error"})
    assert store.get(eid) == before and store.events()["events"] == []


@pytest.mark.parametrize("state", ["plain-admitted", "marked-preparation", "marked-launch"])
def test_existing_intent_is_query_only_without_preparation_or_relaunch(tmp_path, state):
    store, doc, identity, plan = admitted(tmp_path)
    if state == "marked-preparation":
        install_token(store, doc, token="crashed-original-execute")
    if state == "marked-launch":
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = store._load(db, doc["effectId"])
            current["launchAttempted"] = True
            store._save(db, current)
            db.commit()
    calls = []
    executor = ProtectedLifecycleExecutor(job_id=doc["jobId"], store=store,
        authorize=lambda scope: scope, launch=lambda *args: calls.append("launch"))
    async def prepare(_):
        calls.append("prepare")
    executor.prepare = prepare
    result = asyncio.run(executor.execute(attempt=identity["attempt"], effect_key=identity["effectKey"], frozen_plan=plan))
    assert result["outcome"] == "unknown" and result["receipt"]["code"] == "execution_pending"
    assert calls == [] and store.get(doc["effectId"])["result"] is None


def test_claim_during_preparation_is_not_overwritten_or_relaunched(tmp_path):
    from packages.jobs.independent_executor import effect_id
    job, identity, plan = lifecycle_action.protected_plan(root=tmp_path, port=48321,
        old_pid=os.getpid(), old_pid_created_at=psutil.Process().create_time(), operation="exit",
        options={"markRunningSessionsOffline": True}, request_id="prelaunch-claim-race")
    store = ExecutionStore(lifecycle_action.receipt_root(tmp_path))
    eid = effect_id(identity["effectKey"])
    launches = []
    executor = ProtectedLifecycleExecutor(job_id=job, store=store,
        authorize=lambda scope: scope, launch=lambda *args: launches.append(True))
    async def prepare(original):
        store.claim(original["effectId"], identity["attempt"], {"pid": os.getpid(), "birth": "owned-test"})
        raise RuntimeError("failure after concurrent claim")
    executor.prepare = prepare
    with pytest.raises(ExecutionConflict):
        asyncio.run(executor.execute(attempt=identity["attempt"], effect_key=identity["effectKey"], frozen_plan=plan))
    actual = store.get(eid)
    assert actual["phase"] == "preparing" and actual["claim"] and actual["result"] is None
    assert launches == [] and store.events()["events"] == []
