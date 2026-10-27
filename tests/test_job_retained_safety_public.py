"""Default pending adapter, actual public cancellation and cold reconciliation."""
import asyncio
import copy
import json
import os
import subprocess
import sys
import time

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import definition
from packages.jobs.independent_executor import fence
from packages.jobs.window import command_window_options
from tests.test_job_retained_safety_canonical import canonical, launch, USER
from tests.test_job_retained_safety_owned import owned, wait_for, remaining


def refresh(c):
    asyncio.run(c.service.recover_external_receipts())
    return c.client.get("/api/job-runs/" + c.run_id).json()


def cancel(c, key, revision=None):
    run = c.client.get("/api/job-runs/" + c.run_id).json()
    body = {"operation": "cancel", "expectedStateRevision": run["stateRevision"] if revision is None else revision, "requestKey": key}
    response = c.client.post("/api/job-runs/" + c.run_id + "/operations", json=body)
    return response, body


def cold(c, expected):
    o = c.owned
    code = '''import asyncio,json,sys
from packages.jobs.final_service import FinalService
from packages.jobs.final_api import CanonicalUserAuth
from packages.actions.typed_executor import TypedExecutor
from packages.jobs.independent_executor import IndependentExecutor
from packages.jobs.independent_client import IndependentJobClient
s=FinalService(sys.argv[1],CanonicalUserAuth(sys.argv[2]))
s.executor=TypedExecutor(independent_executor=IndependentExecutor(job_id=sys.argv[3],client=IndependentJobClient(sys.argv[4])))
s.recover()
asyncio.run(s.recover_external_receipts())
state=s.final.store.get(sys.argv[3])['finalRuntime']
run=state['runs'][sys.argv[5]]
assert run['state']==sys.argv[6],run
print(json.dumps({'state':run['state'],'attempts':list(state['attempts']),'runs':list(state['runs']),
                 'effects':[{'attemptId':e['attemptId'],'effectKey':e['effectKey'],'outcome':e['outcome']} for e in state['effects']]}))
asyncio.run(s.close())
'''
    result = subprocess.run([sys.executable, "-c", code, str(c.service.root), str(o.tmp_path / "automation"),
        c.job["id"], o.client.endpoint, c.run_id, expected], capture_output=True, text=True, timeout=15,
        **command_window_options())
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(os.name != "nt", reason="P23 Windows real public owned-tree path")
def test_default_pending_cold_reopen_resolves_original_uncertain_attempt(canonical):
    c, o = canonical, canonical.owned
    o.evidence["label"] = "public-default-pending-cold"
    launch(c, default_pending=True)
    original = copy.deepcopy(c.service.final.store.get(c.job["id"])["finalRuntime"])
    assert c.client.get("/api/job-runs/" + c.run_id).json()["state"] == "uncertain"
    assert cold(c, "uncertain")["attempts"] == list(original["attempts"])
    assert len(remaining(o.identities)) == len(o.identities)
    o.release.touch()
    doc = wait_for(lambda: o.client.get_execution(o.identity), lambda d: d["result"] is not None)
    assert doc["result"]["outcome"] == "succeeded"
    o.children[0].wait(5)
    result = cold(c, "completed")
    assert result["attempts"] == list(original["attempts"]) and result["runs"] == [c.run_id]
    assert result["effects"][0]["effectKey"] == o.key
    state = c.service.final.store.get(c.job["id"])["finalRuntime"]
    attempt, = state["attempts"].values()
    assert fence(attempt) == fence(o.attempt)
    assert len(state["effects"]) == 1 and len(state["receiptReconciliations"][attempt["attemptId"]]) == 1
    assert c.client.get("/api/operations/" + c.run_operation_id).json()["status"] == "completed"
    asyncio.run(c.service.dispatch_once())
    assert o.effects.read_text().splitlines() == ["effect"] and len(o.children) == 1
    assert not remaining(o.identities)
    o.evidence.update(defaultPending=True, coldReopen=True, originalAttemptPreserved=True,
        runState="completed", effects=1, cleanupCalls=1, noResubmit=True)


@pytest.mark.skipif(os.name != "nt", reason="P23 Windows real public owned-tree path")
def test_public_unknown_query_explicit_recheck_and_lost_response_identity(canonical):
    c, o = canonical, canonical.owned
    o.evidence["label"] = "public-default-unknown-recheck"
    launch(c, default_pending=True)
    (o.root / "deny-probe").touch()
    response, original_body = cancel(c, "original-public-cancel")
    assert response.status_code == 202, response.text
    op_id = response.json()["operationId"]
    wait_for(lambda: o.client.get_execution(o.identity), lambda d: d.get("cleanupRequest", {}).get("status") == "unknown")
    refresh(c)
    operation = c.client.get("/api/operations/" + op_id).json()
    assert operation["status"] == "unknown" and operation["result"]["cleanupStatus"] == "unknown"
    query = c.client.get("/api/operations", params={"operation": "cancel", "requestKey": "original-public-cancel"})
    assert query.status_code == 200 and query.json()["operationId"] == op_id
    replay = c.client.post("/api/job-runs/" + c.run_id + "/operations", json=original_body)
    assert replay.status_code == 202 and replay.json()["operationId"] == op_id
    assert cold(c, "uncertain")["attempts"] == [o.attempt["attemptId"]]
    assert not (o.root / "kills.jsonl").exists() and len(remaining(o.identities)) == len(o.identities)
    (o.root / "deny-probe").unlink()
    for _ in range(3):
        refresh(c)
        assert c.client.get("/api/operations/" + op_id).json()["status"] == "unknown"
    assert not (o.root / "kills.jsonl").exists()
    # CAS and authorization still reject before a new destructive request.
    stale, _ = cancel(c, "stale-request", revision=original_body["expectedStateRevision"])
    assert stale.status_code == 409, stale.text
    denied = c.client.post("/api/job-runs/" + c.run_id + "/operations", json={**original_body, "requestKey": "anonymous"},
                           headers={"Authorization": "Bearer invalid"})
    assert denied.status_code == 401, denied.text
    assert o.client.get_execution(o.identity)["cleanupRequest"]["revision"] == 1
    rechecked, recheck_body = cancel(c, "explicit-public-recheck")
    assert rechecked.status_code == 202, rechecked.text
    second_id = rechecked.json()["operationId"]
    assert second_id != op_id
    wait_for(lambda: refresh(c), lambda r: r["state"] == "cancelled")
    o.children[0].wait(5)
    assert not remaining(o.identities)
    for identity in (op_id, second_id):
        final = c.client.get("/api/operations/" + identity).json()
        assert final["status"] == "completed" and final["result"]["cleanupStatus"] == "confirmed"
    duplicate = c.client.post("/api/job-runs/" + c.run_id + "/operations", json=recheck_body)
    assert duplicate.status_code == 200 and duplicate.json()["operationId"] == second_id
    assert o.client.get_execution(o.identity)["cleanupRequest"]["revision"] == 2
    assert len((o.root / "kills.jsonl").read_text().splitlines()) == 1
    assert o.effects.read_text().splitlines() == ["effect"] and len(o.children) == 1
    o.evidence.update(publicUnknownPersisted=True, lostResponseQuerySameOperation=True, replayNoCleanup=True,
        explicitPublicRecheck=True, casRejected=True, anonymousRejected=True, cleanupRevision=2,
        effects=1, cleanupCalls=1, originalAttemptPreserved=True, runState="cancelled")


def test_process_cancel_release_is_additive_and_frozen_old_action_unchanged(canonical):
    c = canonical
    actions = {item["id"]: item for item in c.service.actions(actor=USER)["items"]}
    old, new = actions["product_process"], actions["product_process_cleanup_v1"]
    old_bytes = fc.canonical_json(old)
    assert old["cancel"]["supported"] is False and new["cancel"]["supported"] is True
    assert old["providerRef"]["releaseDigest"] != new["providerRef"]["releaseDigest"]
    with pytest.raises(fc.ContractError, match="immutable"):
        c.service.publish_action("product_process", definition("action", new))
    c.service._install_product_actions()
    current = {item["id"]: item for item in c.service.actions(actor=USER)["items"]}
    assert fc.canonical_json(current["product_process"]) == old_bytes
    assert current["product_process_cleanup_v1"] == new


@pytest.mark.skipif(os.name != "nt", reason="P23 Windows real public owned-tree path")
def test_public_cancel_waits_for_original_supervisor_exit(canonical):
    c, o = canonical, canonical.owned
    o.evidence["label"] = "public-supervisor-exit-gate"
    launch(c, default_pending=True)
    gate = o.root / "hold-supervisor-exit"
    gate.touch()
    try:
        response, _ = cancel(c, "wait-for-supervisor")
        assert response.status_code == 202
        op = response.json()["operationId"]
        wait_for(lambda: o.client.get_execution(o.identity), lambda d: d["result"] is not None)
        for _ in range(3):
            run = refresh(c)
            assert run["state"] != "cancelled"
            assert c.client.get("/api/operations/" + op).json()["status"] != "completed"
        assert not remaining(o.effect_identities) and remaining(o.supervisor_identities)
    finally:
        gate.unlink(missing_ok=True)
    o.children[0].wait(5)
    assert refresh(c)["state"] == "cancelled"
    assert c.client.get("/api/operations/" + op).json()["status"] == "completed"
    o.evidence.update(supervisorAliveBlockedCompletion=True, supervisorExitConfirmed=True, effects=1, cleanupCalls=1)


def test_public_queued_cancel_does_not_create_attempt_or_process(canonical):
    c, o = canonical, canonical.owned
    o.evidence["label"] = "public-queued-cancel"
    launch(c, default_pending=True, queued=True)
    response, _ = cancel(c, "queued-cancel")
    assert response.status_code == 202, response.text
    asyncio.run(c.service.dispatch_once())
    assert c.client.get("/api/job-runs/" + c.run_id).json()["state"] == "cancelled"
    assert c.client.get("/api/operations/" + response.json()["operationId"]).json()["status"] == "completed"
    state = c.service.final.store.get(c.job["id"])["finalRuntime"]
    assert state["attempts"] == {} and state["effects"] == []
    assert o.children == [] and not o.effects.exists()
    o.evidence.update(queuedCancelled=True, attempts=0, effects=0, supervisors=0)


@pytest.mark.skipif(os.name != "nt", reason="P23 Windows real original-receipt fence")
def test_original_uncertain_reconcile_rejects_old_epoch_generation_and_lease(canonical):
    c, o = canonical, canonical.owned
    o.evidence["label"] = "public-original-fence"
    launch(c, default_pending=True, action_id="product_process")
    frozen = copy.deepcopy(c.service.final.store.get(c.job["id"])["finalRuntime"]["plans"][c.run_id]["frozen"])
    assert frozen["actions"]["product_process"]["cancel"]["supported"] is False
    c.service._install_product_actions()
    assert c.service.final.store.get(c.job["id"])["finalRuntime"]["plans"][c.run_id]["frozen"] == frozen
    o.release.touch()
    doc = wait_for(lambda: o.client.get_execution(o.identity), lambda d: d["result"] is not None)
    o.children[0].wait(5)
    before = c.service.final.store.get(c.job["id"])
    for field, value in [("ownerEpoch", o.attempt["ownerEpoch"] + 1), ("generation", o.attempt["generation"] + 1),
                         ("leaseId", "other-lease"), ("attemptId", "other-attempt")]:
        expected = {**o.attempt, field: value}
        with pytest.raises((fc.ContractError, KeyError)):
            c.service.final.store.mutate(c.job["id"], lambda job: c.service.final.ledger.reconcile_attempt(
                job, c.service.root, expected_attempt=expected, effect_key=o.key, receipt=doc["result"]))
        assert c.service.final.store.get(c.job["id"]) == before
    with pytest.raises(fc.ContractError, match="active Attempt"):
        c.service.final.store.mutate(c.job["id"], lambda job: c.service.final.ledger.finish_attempt(
            job, c.service.root, o.attempt["attemptId"], "completed",
            expected_epoch=o.attempt["ownerEpoch"], expected_generation=o.attempt["generation"]))
    assert c.service.final.store.get(c.job["id"]) == before
    assert refresh(c)["state"] == "completed"
    assert c.service.final.store.get(c.job["id"])["finalRuntime"]["plans"][c.run_id]["frozen"] == frozen
    assert len(o.children) == 1 and o.effects.read_text().splitlines() == ["effect"]
    o.evidence.update(oldFrozenPlanUnchanged=True, newReleaseImmutable=True, staleFencesRejected=4,
        finishAttemptStillRejectsUncertain=True, originalAttemptReconciled=True, effects=1)


@pytest.mark.parametrize("missing", ["ownedTree", "identityVerified", "treeTerminated", "supervisorExitProof", "confirmed"])
def test_cancel_completion_requires_all_resource_proofs(missing):
    # Protocol negative only; real tree and delayed supervisor are above.
    from packages.jobs.final_service import FinalService
    doc = {"result": {"outcome": "rejected", "cleanupProof": {"confirmed": True, "identityVerified": True,
        "treeTerminated": True, "ownedTree": {"confirmed": True, "remaining": []}}},
        "supervisorExitProof": {"confirmed": True}}
    assert FinalService._cleanup_complete(doc)
    if missing == "supervisorExitProof":
        doc.pop(missing)
    else:
        doc["result"]["cleanupProof"].pop(missing)
    assert not FinalService._cleanup_complete(doc)


@pytest.mark.skipif(os.name != "nt", reason="P23 Windows real product default executor")
def test_product_default_executor_has_owned_receipt_and_no_second_lifecycle(canonical):
    from packages.jobs.independent_executor import ExecutionStore
    from packages.core.platform.execution_host import supervisor_exit_evidence
    c, o = canonical, canonical.owned
    o.evidence["label"] = "public-product-default-executor"
    launch(c, default_pending=True, queued=True)
    c.service.executor = c.service._product_executor
    o.release.touch()
    asyncio.run(c.service.dispatch_once())
    wait_for(lambda: refresh(c), lambda run: run["state"] == "completed")
    state = c.service.final.store.get(c.job["id"])["finalRuntime"]
    aid, = state["attempts"]
    effect, = state["effects"]
    host = ExecutionStore(c.service.root / "executions")
    from packages.jobs.independent_executor import effect_id
    doc = host.get(effect_id(effect["effectKey"]))
    assert doc["attempt"]["attemptId"] == aid
    assert doc["result"]["cleanupProof"]["confirmed"] is True
    assert doc["result"]["cleanupProof"]["ownedTree"]["confirmed"] is True
    wait_for(lambda: supervisor_exit_evidence(doc["supervisorIdentity"]), lambda proof: proof["confirmed"])
    from packages.core.platform.process import default_process_owner
    pids = json.loads(o.ready.read_text())
    # Original process identities come from the trusted native receipt. Every
    # member PID absence is a postcondition, not authority to issue a kill.
    import psutil
    for pid in {*pids.values(), doc["supervisorIdentity"]["pid"]}:
        try:
            assert not psutil.Process(pid).is_running(), pid
        except psutil.NoSuchProcess:
            pass
    asyncio.run(c.service.dispatch_once())
    assert len(state["runs"]) == 1 and len(state["attempts"]) == 1
    assert o.effects.read_text().splitlines() == ["effect"]
    assert not (c.service.root / "executions" / "jobs.sqlite3").exists()
    o.evidence.update(productDefaultOwned=True, supervisorExitConfirmed=True, effects=1,
        attempts=1, noLegacyLifecycle=True, productProcessesRemaining=[])
