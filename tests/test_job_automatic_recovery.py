"""Owned canonical policy/transaction tests and actual default detached effects."""
import asyncio
import copy
from decimal import Decimal
import json
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.automation.service import Service as Automation
from packages.jobs import final_api, final_contracts as fc
from packages.jobs.final_service import FinalService, bind, definition, object_ref
from packages.jobs.final_runtime.ledger import aggregate
from packages.jobs.independent_executor import ExecutionHost, ExecutionStore, effect_id

USER = {"type": "user", "id": "local"}


@pytest.fixture
def env(tmp_path):
    app = FastAPI()
    automation = Automation(tmp_path / "automation", SimpleNamespace(workers={}), None)
    automation.auth = final_api.CanonicalUserAuth(tmp_path / "automation")
    service = bind(app, automation, tmp_path / "canonical")
    app.state.automation = automation
    from packages.jobs.action_api import router as actions
    from packages.jobs.api import router as jobs
    final_api.install(app, actions, jobs)
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer " + automation.auth.login(automation.auth.key())
    h = SimpleNamespace(service=service, client=client, root=service.root, tmp=tmp_path,
                        auth=automation.auth, app=app, serial=0)
    yield h
    client.close()
    # Only this fixture's own admitted processes can be cleaned up.
    from packages.core.platform.execution_host import supervisor_exit_evidence
    store = ExecutionStore(h.root / "executions")
    with store.connect() as db:
        docs = [json.loads(row[0]) for row in db.execute("SELECT document FROM executions")]
    for doc in docs:
        if doc.get("supervisorIdentity"):
            if doc.get("result") is None:
                ExecutionHost(store).cleanup_execution(doc["effectId"], doc["attempt"])
            deadline = time.monotonic() + 12
            while not supervisor_exit_evidence(doc["supervisorIdentity"])["confirmed"]:
                assert time.monotonic() < deadline, "owned supervisor did not exit"
                time.sleep(.03)
    asyncio.run(h.service.close())


def policy(**kwargs):
    return {"automatic": True, "retryIf": ["failed"], "maxAttempts": 2,
            "backoffSeconds": ["0"], "stableResetSeconds": "0", **kwargs}


def job_definition(h, recovery=None, code="raise SystemExit(7)", steps=1):
    action = next(a for a in h.service.actions(actor=USER)["items"] if a["id"] == "product_process_cleanup_v1")
    return {"name": "owned recovery", "steps": [{"stepId": "s" + str(i),
        "actionRef": object_ref("action", action), "arguments": {
            "argv": {"from": "literal", "value": [sys.executable, "-c", code]},
            "cwd": {"from": "literal", "value": str(h.tmp)}},
        "completion": {"contract": "process-exit"}} for i in range(steps)],
        "recovery": recovery if recovery is not None else policy(),
        "retention": {"policyRef": "test"}}


def create(h, value=None, actor=USER):
    h.serial += 1
    result = h.client.post("/api/jobs", json={"definition": value or job_definition(h),
        "expectedRevision": 0, "requestKey": "create-" + str(h.serial)})
    assert result.status_code == 201, result.text
    job = result.json()["result"]["object"]
    result = h.client.post("/api/jobs/" + job["id"] + "/operations", json={"operation": "enable",
        "expectedRevision": 1, "expectedStateRevision": 0, "parameters": {}, "requestKey": "enable-" + str(h.serial)})
    assert result.status_code == 200, result.text
    receipt = h.service.create_run(job["id"], {"expectedRevision": 1, "requestKey": "run-" + str(h.serial),
        "input": {}, "cause": {"type": "manual"}}, actor=actor)
    return job["id"], receipt["runId"]


def state(h, jid):
    return h.service.final.get(jid)


def seed(h, monkeypatch, *, value=None, actor=USER, reason="failed", proof=True, supervisor=True):
    """Synthetic trusted lookup only for negative/race tests, never product proof."""
    jid, rid = create(h, value, actor)
    with h.service.transaction() as db:
        job = h.service._get(db, "job", jid)
        ledger = h.service.final.ledger
        ledger.start(job, h.root, rid)
        attempt = ledger.begin_attempt(job, h.root, rid, "s0")
        key = ledger.admit_effect(job, h.root, attempt["attemptId"], "product_process_cleanup_v1")
        plan = aggregate(job, h.root)["plans"][rid]
        plan["pendingProcessAttempt"] = attempt["attemptId"]
        frozen = plan["frozen"]
        action = frozen["actions"]["product_process_cleanup_v1"]
        executable = {"operation": "process", "effect": action["effectClass"], "arguments": {
            k: v["value"] for k, v in frozen["steps"][0]["arguments"].items()}}
        h.service._save(db, "job", jid, job)
    detail = {"exitCode": 7} if reason == "failed" else {"code": reason}
    doc = {"jobId": jid, "attempt": copy.deepcopy(attempt), "effectKey": key,
        "effectId": effect_id(key), "frozenPlan": executable, "planDigest": fc.digest(executable),
        "phase": "rejected", "supervisorExitProof": {"confirmed": supervisor},
        "result": {"effectKey": key, "outcome": "rejected", "receipt": detail,
            "cleanupProof": {"confirmed": proof, "identityVerified": proof, "treeTerminated": proof,
                             "ownedTree": {"confirmed": proof, "remaining": []}}}}

    class Original:
        calls = []
        async def lookup_execution(self, **kwargs):
            return copy.deepcopy(doc)
        async def execute(self, **kwargs):
            self.calls.append(copy.deepcopy(kwargs))
            return {"outcome": "unknown", "receipt": {"code": "owned_test_unknown"}}
    original = Original()
    monkeypatch.setattr(h.service, "_external_adapter", lambda _: original)
    return SimpleNamespace(jid=jid, rid=rid, attempt=attempt, key=key, doc=doc, original=original)


def observe(h):
    asyncio.run(h.service.recover_external_receipts())


def marker(h, row):
    return h.service.final.store.get(row.jid)["finalRuntime"]["plans"][row.rid].get("autoRecovery")


@pytest.mark.parametrize("recovery,code", [
    (policy(retryIf=[]), "capability_unsupported"),
    *[(policy(retryIf=[r]), "capability_unsupported") for r in ["orphaned", "interrupted", "dependency-unavailable", "cancelled"]],
    (policy(stableResetSeconds="1"), "capability_unsupported"),
    (policy(maxAttempts=11), "invalid_schema"), (policy(maxAttempts=True), "invalid_schema"),
    (policy(retryIf=["failed", "failed"]), "invalid_schema"),
    (policy(retryIf=["unknown"]), "invalid_schema"), (policy(retryIf="failed"), "invalid_schema"),
])
def test_unsupported_or_malformed_before_write(env, recovery, code):
    response = env.client.post("/api/jobs", json={"definition": job_definition(env, recovery),
        "expectedRevision": 0, "requestKey": "bad"})
    assert response.status_code == fc.ERROR_HTTP_STATUS[code]
    assert response.json()["error"]["code"] == code
    assert env.client.get("/api/jobs").json()["items"] == []


@pytest.mark.parametrize("reason,allowed", [("failed", True), ("timeout", False),
    ("cancelled_before_effect", False), ("provider_unavailable", False), ("process_exception", False)])
def test_reason_filter_and_no_generic_failure_fallback(env, monkeypatch, reason, allowed):
    row = seed(env, monkeypatch, reason=reason)
    observe(env)
    assert bool(marker(env, row)) is allowed
    assert state(env, row.jid)["runs"][row.rid]["state"] == ("retry_wait" if allowed else "failed")
    assert len(state(env, row.jid)["attempts"]) == 1


@pytest.mark.parametrize("extra,expected", [(0, False), (1, True), (9, True)])
def test_extra_retry_conversion_and_first_attempt_budget(env, monkeypatch, extra, expected):
    # Explicit reviewed mapping, never a clamp or implicit legacy execution.
    row = seed(env, monkeypatch, value=job_definition(env, policy(maxAttempts=extra + 1)))
    observe(env)
    assert bool(marker(env, row)) is expected
    assert state(env, row.jid)["steps"][row.rid]["s0"]["attemptCount"] == 1


def test_timeout_requires_full_original_proof(env, monkeypatch):
    row = seed(env, monkeypatch, value=job_definition(env, policy(retryIf=["timeout"])), reason="timeout")
    observe(env)
    assert marker(env, row)["reason"] == "timeout"


@pytest.mark.parametrize("missing", ["identityVerified", "treeTerminated", "ownedTree", "confirmed", "supervisor"])
def test_missing_cleanup_or_supervisor_proof_holds(env, monkeypatch, missing):
    row = seed(env, monkeypatch)
    if missing == "supervisor":
        row.doc["supervisorExitProof"]["confirmed"] = False
    else:
        row.doc["result"]["cleanupProof"].pop(missing)
    observe(env)
    assert marker(env, row) is None
    assert len(state(env, row.jid)["attempts"]) == 1
    assert state(env, row.jid)["runs"][row.rid]["state"] == "running"


@pytest.mark.parametrize("outcome", ["unknown", "partial"])
def test_real_unknown_or_partial_loses_pending_eligibility(env, monkeypatch, outcome):
    row = seed(env, monkeypatch)
    row.doc["result"]["outcome"] = outcome
    observe(env)
    assert state(env, row.jid)["runs"][row.rid]["state"] == "uncertain"
    row.doc["result"]["outcome"] = "rejected"  # Later resolution is not a pending placeholder.
    observe(env)
    assert marker(env, row) is None
    asyncio.run(env.service.dispatch_once())
    assert len(state(env, row.jid)["attempts"]) == 1


@pytest.mark.parametrize("field", ["effectKey", "attempt", "planDigest", "frozenPlan", "jobId"])
def test_wrong_original_fence_never_admits(env, monkeypatch, field):
    row = seed(env, monkeypatch)
    row.doc[field] = {} if field in {"effectKey", "attempt", "frozenPlan"} else "wrong"
    with pytest.raises((fc.ContractError, ValueError)):
        observe(env)
    assert marker(env, row) is None
    assert len(state(env, row.jid)["attempts"]) == 1


def test_decimal_deadline_frozen_policy_and_args(env, monkeypatch):
    delay = "0.123456789012345678901234567890123"
    row = seed(env, monkeypatch, value=job_definition(env, policy(backoffSeconds=[delay])))
    observe(env)
    before = marker(env, row)
    snapshot = env.service.final.store.get(row.jid)
    ended = snapshot["finalRuntime"]["attempts"][row.attempt["attemptId"]]["endedAt"]
    from decimal import localcontext
    with localcontext() as ctx:
        ctx.prec = 70
        assert Decimal(before["eligibleAt"]) == env.service._recovery_time(ended) + Decimal(delay)
    # A future definition edit cannot alter original recovery or argv.
    value = definition("job", snapshot["canonical"])
    value["recovery"] = policy(maxAttempts=1, backoffSeconds=["900"])
    value["steps"][0]["arguments"]["argv"]["value"] = [sys.executable, "-c", "raise SystemExit(99)"]
    response = env.client.put("/api/jobs/" + row.jid, json={"definition": value,
        "expectedRevision": 1, "requestKey": "edit"})
    assert response.status_code == 200, response.text
    with localcontext() as ctx:
        ctx.prec = 70
        just_before = Decimal(before["eligibleAt"]) - Decimal("0.000000000000000000000000000000001")
    monkeypatch.setattr(env.service, "_recovery_time", lambda timestamp=None: just_before)
    asyncio.run(env.service.dispatch_once())
    assert marker(env, row) == before and not row.original.calls
    monkeypatch.setattr(env.service, "_recovery_time", lambda timestamp=None: Decimal(before["eligibleAt"]))
    asyncio.run(env.service.dispatch_once())
    assert len(row.original.calls) == 1
    call = row.original.calls[0]
    assert call["effect_key"] == row.key
    assert call["attempt"]["attemptId"] != row.attempt["attemptId"]
    assert call["frozen_plan"] == row.doc["frozenPlan"]


@pytest.mark.parametrize("operation", ["pause", "cancel", "revoke"])
def test_wait_control_keeps_count_and_deadline(env, monkeypatch, operation):
    grants = env.auth._default_permissions()
    grants["job"]["execute"] = True
    grants["action"]["manageSystemActions"] = True
    env.auth.change_permissions(grants, 0)
    row = seed(env, monkeypatch, actor={"type": "agent", "id": "owned", "revision": 1})
    observe(env)
    before = marker(env, row)
    if operation == "revoke":
        env.auth.change_permissions(env.auth._default_permissions(), 1)
    else:
        run = state(env, row.jid)["runs"][row.rid]
        env.service.control_run(row.rid, {"operation": operation, "requestKey": operation,
            "expectedStateRevision": run["stateRevision"]}, actor=USER)
    asyncio.run(env.service.dispatch_once())
    assert marker(env, row) == before
    assert len(state(env, row.jid)["attempts"]) == 1 and not row.original.calls
    if operation == "revoke":
        assert state(env, row.jid)["runs"][row.rid]["state"] == "failed"


def test_transaction_rollback_before_marker_and_before_admission(env, monkeypatch):
    row = seed(env, monkeypatch)
    save = env.service._save
    def fail_marker(db, kind, jid, job):
        if job.get("finalRuntime", {}).get("plans", {}).get(row.rid, {}).get("autoRecovery"):
            raise OSError("owned marker write fault")
        return save(db, kind, jid, job)
    monkeypatch.setattr(env.service, "_save", fail_marker)
    with pytest.raises(OSError):
        observe(env)
    assert marker(env, row) is None
    assert state(env, row.jid)["attempts"][row.attempt["attemptId"]]["state"] == "running"
    monkeypatch.setattr(env.service, "_save", save)
    observe(env)
    before = marker(env, row)
    def fail_attempt(db, kind, jid, job):
        if len(job.get("finalRuntime", {}).get("attempts", {})) > 1:
            raise OSError("owned admission fault")
        return save(db, kind, jid, job)
    monkeypatch.setattr(env.service, "_save", fail_attempt)
    with pytest.raises(OSError):
        asyncio.run(env.service.dispatch_once())
    assert marker(env, row) == before
    assert len(state(env, row.jid)["attempts"]) == 1 and not row.original.calls


def test_concurrent_pulses_claim_once_and_late_original_is_inert(env, monkeypatch):
    row = seed(env, monkeypatch)
    async def both():
        await asyncio.gather(env.service.dispatch_once(), env.service.dispatch_once())
    asyncio.run(both())
    assert len(row.original.calls) == 1
    assert len(state(env, row.jid)["attempts"]) == 2
    observe(env)
    assert len(row.original.calls) == 1


def test_last_backoff_manual_attempts_and_per_step_budget(env, monkeypatch):
    row = seed(env, monkeypatch, value=job_definition(env,
        policy(maxAttempts=3, backoffSeconds=["0.125"]), steps=2))
    observe(env)
    original_time = env.service._recovery_time
    # Exercise the real manual operation; manual retries remain unbounded,
    # but their existing Step count consumes future automatic budget.
    with env.service.transaction() as db:
        job = env.service._get(db, "job", row.jid)
        plan = aggregate(job, env.root)["plans"][row.rid]
        plan.pop("autoRecovery")
        ledger = env.service.final.ledger
        ledger.start(job, env.root, row.rid)
        attempt = ledger.begin_attempt(job, env.root, row.rid, "s0")
        key = ledger.admit_effect(job, env.root, attempt["attemptId"], "product_process_cleanup_v1")
        row.doc["attempt"] = copy.deepcopy(attempt)
        ledger.finish_attempt(job, env.root, attempt["attemptId"], "failed")
        ledger.record_effect(job, env.root, attempt["attemptId"], "failed", row.doc["result"])
        ledger.transition_run(job, env.root, row.rid, "failed")
        assert env.service._schedule_automatic(db, job, row.rid, row.doc)
        ended = aggregate(job, env.root)["attempts"][attempt["attemptId"]]["endedAt"]
    assert Decimal(marker(env, row)["eligibleAt"]) == original_time(ended) + Decimal("0.125")
    assert state(env, row.jid)["steps"][row.rid]["s1"]["attemptCount"] == 0
    # Last delay repeats when a later failure exceeds the schedule length.
    with env.service.transaction() as db:
        job = env.service._get(db, "job", row.jid)
        plan = aggregate(job, env.root)["plans"][row.rid]
        plan.pop("autoRecovery")
        ledger.start(job, env.root, row.rid)
        last = ledger.begin_attempt(job, env.root, row.rid, "s0")
        ledger.admit_effect(job, env.root, last["attemptId"], "product_process_cleanup_v1")
        row.doc["attempt"] = copy.deepcopy(last)
        ledger.finish_attempt(job, env.root, last["attemptId"], "failed")
        ledger.record_effect(job, env.root, last["attemptId"], "failed", row.doc["result"])
        ledger.transition_run(job, env.root, row.rid, "failed")
        assert not env.service._schedule_automatic(db, job, row.rid, row.doc)
        env.service._save(db, "job", row.jid, job)
    run = state(env, row.jid)["runs"][row.rid]
    env.service.control_run(row.rid, {"operation": "retry", "requestKey": "manual-past-cap",
        "expectedStateRevision": run["stateRevision"]}, actor=USER)
    assert state(env, row.jid)["runs"][row.rid]["state"] == "retry_wait"
    assert marker(env, row) is None


def test_early_revoke_does_not_schedule_or_consume(env, monkeypatch):
    grants = env.auth._default_permissions()
    grants["job"]["execute"] = grants["action"]["manageSystemActions"] = True
    env.auth.change_permissions(grants, 0)
    row = seed(env, monkeypatch, actor={"type": "agent", "id": "owned", "revision": 1})
    env.auth.change_permissions(env.auth._default_permissions(), 1)
    observe(env)
    assert marker(env, row) is None
    assert state(env, row.jid)["runs"][row.rid]["state"] == "failed"
    assert len(state(env, row.jid)["attempts"]) == 1


def test_action_disable_and_cancel_during_proof_lookup(env, monkeypatch):
    row = seed(env, monkeypatch)
    observe(env)
    before = marker(env, row)
    lookup = row.original.lookup_execution
    async def cancel_during_lookup(**kwargs):
        run = state(env, row.jid)["runs"][row.rid]
        env.service.control_run(row.rid, {"operation": "cancel", "requestKey": "race-cancel",
            "expectedStateRevision": run["stateRevision"]}, actor=USER)
        return await lookup(**kwargs)
    monkeypatch.setattr(row.original, "lookup_execution", cancel_during_lookup)
    asyncio.run(env.service._drive_run(row.jid, row.rid))
    assert not row.original.calls
    assert len(state(env, row.jid)["attempts"]) == 1
    assert marker(env, row) == before


@pytest.mark.parametrize("mutation", ["action", "job"])
def test_live_action_or_job_disable_blocks_waiting_attempt(env, monkeypatch, mutation):
    row = seed(env, monkeypatch)
    observe(env)
    before = marker(env, row)
    with env.service.transaction() as db:
        if mutation == "action":
            value = env.service._get(db, "action", "product_process_cleanup_v1")
            value["enabled"] = False
            env.service._save(db, "action", value["id"], value)
        else:
            value = env.service._get(db, "job", row.jid)
            value["canonical"]["state"] = "disabled"
            env.service._save(db, "job", row.jid, value)
    asyncio.run(env.service.dispatch_once())
    assert marker(env, row) == before and not row.original.calls
    assert len(state(env, row.jid)["attempts"]) == 1


def test_due_filter_does_not_starve_ready_tail(env, monkeypatch):
    value = job_definition(env, policy(backoffSeconds=["3600"]))
    value["overlap"] = {"mode": "queue", "maxActive": 1, "maxPending": 2}
    row = seed(env, monkeypatch, value=value)
    observe(env)
    queued = env.service.create_run(row.jid, {"expectedRevision": 1, "requestKey": "tail",
        "input": {}, "cause": {"type": "manual"}}, actor=USER)["runId"]
    asyncio.run(env.service.dispatch_once())
    assert len(row.original.calls) == 1 and row.original.calls[0]["attempt"]["runId"] == queued
    assert marker(env, row) and state(env, row.jid)["steps"][row.rid]["s0"]["attemptCount"] == 1


def test_cold_pending_lookup_and_actual_host_loss_are_distinct(env, monkeypatch):
    row = seed(env, monkeypatch)
    restored = FinalService(env.root, env.auth)
    restored.recover()
    assert restored.get_run(row.rid, actor=USER)["state"] == "running"
    # No known pending reference means ownership/receipt loss: hold uncertain.
    with env.service.transaction() as db:
        job = env.service._get(db, "job", row.jid)
        aggregate(job, env.root)["plans"][row.rid].pop("pendingProcessAttempt")
        env.service._save(db, "job", row.jid, job)
    restored.recover()
    assert restored.get_run(row.rid, actor=USER)["state"] == "uncertain"
    observe(env)  # Fenced terminal evidence can resolve history, never auto-retry true uncertainty.
    assert marker(env, row) is None and len(state(env, row.jid)["attempts"]) == 1


@pytest.mark.parametrize("deadline", [None, "NaN", "-1", True])
def test_malformed_marker_never_becomes_due(env, monkeypatch, deadline):
    row = seed(env, monkeypatch)
    observe(env)
    with env.service.transaction() as db:
        job = env.service._get(db, "job", row.jid)
        aggregate(job, env.root)["plans"][row.rid]["autoRecovery"]["eligibleAt"] = deadline
        env.service._save(db, "job", row.jid, job)
    asyncio.run(env.service.dispatch_once())
    assert not row.original.calls and len(state(env, row.jid)["attempts"]) == 1


def test_legacy_definition_and_old_frozen_plan_stay_disabled(env, monkeypatch):
    row = seed(env, monkeypatch, value=job_definition(env, {"automatic": False}))
    with env.service.transaction() as db:
        job = env.service._get(db, "job", row.jid)
        aggregate(job, env.root)["plans"][row.rid]["frozen"].pop("recovery")
        env.service._save(db, "job", row.jid, job)
    observe(env)
    assert marker(env, row) is None
    assert state(env, row.jid)["runs"][row.rid]["state"] == "failed"
    assert len(state(env, row.jid)["attempts"]) == 1


def test_missing_original_receipt_is_not_permission(env, monkeypatch):
    row = seed(env, monkeypatch)
    row.doc["result"] = None
    observe(env)
    env.service.recover()
    observe(env)
    assert state(env, row.jid)["runs"][row.rid]["state"] == "running"
    assert len(state(env, row.jid)["attempts"]) == 1 and marker(env, row) is None


def test_pause_resume_uses_original_deadline(env, monkeypatch):
    row = seed(env, monkeypatch)
    observe(env)
    deadline = marker(env, row)["eligibleAt"]
    for operation in ["pause", "resume"]:
        run = state(env, row.jid)["runs"][row.rid]
        env.service.control_run(row.rid, {"operation": operation, "requestKey": operation,
            "expectedStateRevision": run["stateRevision"]}, actor=USER)
        asyncio.run(env.service.dispatch_once())
        if operation == "pause":
            assert marker(env, row)["eligibleAt"] == deadline and not row.original.calls
    assert len(row.original.calls) == 1
    assert state(env, row.jid)["steps"][row.rid]["s0"]["attemptCount"] == 2


def test_complete_step_is_not_repeated_when_later_step_retries(env):
    first, second = env.tmp / "first.txt", env.tmp / "second.txt"
    value = job_definition(env, steps=2)
    value["steps"][0]["arguments"]["argv"]["value"] = [sys.executable, "-c",
        f"from pathlib import Path; Path({str(first)!r}).open('a').write('first\\n')"]
    value["steps"][1]["arguments"]["argv"]["value"] = [sys.executable, "-c",
        f"from pathlib import Path; p=Path({str(second)!r}); n=int(p.read_text()) if p.exists() else 0; "
        "p.write_text(str(n+1)); raise SystemExit(7 if n == 0 else 0)"]
    jid, rid = create(env, value)
    asyncio.run(drive_until(env, jid, rid, "completed"))
    assert first.read_text().splitlines() == ["first"] and second.read_text() == "2"
    result = state(env, jid)
    assert result["steps"][rid]["s0"]["attemptCount"] == 1
    assert result["steps"][rid]["s1"]["attemptCount"] == 2


def test_actual_retained_owner_timeout_adds_verified_facts(env):
    from packages.actions.typed_executor import TypedExecutor
    from tests.test_job_final_independent_execution import attempt, key
    executor = TypedExecutor(process_timeout=.05, terminate_grace=2)
    receipt = asyncio.run(executor.execute(attempt=attempt(), effect_key=key(), frozen_plan={
        "operation": "process", "effect": "write_local", "arguments": {
            "argv": [sys.executable, "-c", "import time; time.sleep(30)"], "cwd": str(env.tmp), "hideWindow": False}}))
    assert receipt["outcome"] == "rejected" and receipt["receipt"]["code"] == "timeout"
    proof = receipt["cleanupProof"]
    assert proof["confirmed"] is True and proof["identityVerified"] is True
    assert proof["treeTerminated"] is True and proof["ownedTree"]["remaining"] == []


async def drive_until(h, jid, rid, desired, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await h.service.dispatch_once()
        run = state(h, jid)["runs"][rid]
        if run["state"] == desired:
            return run
        await asyncio.sleep(.04)
    raise AssertionError("owned default process did not reach " + desired + ": " + repr(run))


@pytest.mark.parametrize("extra", [0, 1])
def test_default_detached_process_budget_exhaustion(env, extra):
    count = env.tmp / "invocations.txt"
    code = f"from pathlib import Path; p=Path({str(count)!r}); p.open('a').write('run\\n'); raise SystemExit(7)"
    jid, rid = create(env, job_definition(env, policy(maxAttempts=extra + 1), code))
    asyncio.run(drive_until(env, jid, rid, "failed"))
    result = state(env, jid)
    assert count.read_text().splitlines() == ["run"] * (extra + 1)
    assert result["steps"][rid]["s0"]["attemptCount"] == extra + 1
    assert len({fc.digest(e["effectKey"]) for e in result["effects"]}) == 1
    doc = ExecutionStore(env.root / "executions").get(effect_id(result["effects"][-1]["effectKey"]))
    assert len(doc["history"]) == extra


def test_owned_http_default_process_cold_reopen_and_pulse(env):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    server = uvicorn.Server(uvicorn.Config(env.app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline
        time.sleep(.02)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=dict(env.client.headers))
    old_client = env.client
    env.client = client
    count = env.tmp / "counter.txt"
    code = (f"from pathlib import Path; p=Path({str(count)!r}); n=int(p.read_text()) if p.exists() else 0; "
            "p.write_text(str(n+1)); raise SystemExit(7 if n == 0 else 0)")
    try:
        jid, rid = create(env, job_definition(env, policy(backoffSeconds=["0.3"]), code))
        async def run():
            await drive_until(env, jid, rid, "retry_wait")
            before = copy.deepcopy(env.service.final.store.get(jid)["finalRuntime"]["plans"][rid]["autoRecovery"])
            frozen = copy.deepcopy(env.service.final.store.get(jid)["finalRuntime"]["plans"][rid]["frozen"])
            first_state = state(env, jid)
            first_attempt = first_state["attempts"][before["afterAttemptId"]]
            first_key = first_state["effects"][0]["effectKey"]
            original = await env.service._external_adapter(jid).lookup_execution(attempt=first_attempt, effect_key=first_key)
            assert env.service._cleanup_complete(original)
            await env.service.close()
            restored = FinalService(env.root, env.auth)
            env.service = restored
            env.app.state.job_final_service = restored
            restored.recover()
            assert restored.final.store.get(jid)["finalRuntime"]["plans"][rid]["autoRecovery"] == before
            await restored.start()  # Real existing quarter-second pulse, default detached adapter.
            try:
                deadline = time.monotonic() + 30
                while state(env, jid)["runs"][rid]["state"] != "completed":
                    assert time.monotonic() < deadline
                    await asyncio.sleep(.04)
            finally:
                await restored.close()
            result = state(env, jid)
            assert len(result["runs"]) == 1 and len(result["attempts"]) == 2
            assert result["steps"][rid]["s0"]["attemptCount"] == 2
            assert len({fc.digest(e["effectKey"]) for e in result["effects"]}) == 1
            assert restored.final.store.get(jid)["finalRuntime"]["plans"][rid]["frozen"] == frozen
            assert count.read_text() == "2"
            doc = ExecutionStore(env.root / "executions").get(effect_id(result["effects"][-1]["effectKey"]))
            assert len(doc["history"]) == 1
            assert doc["history"][0]["attempt"]["attemptId"] != doc["attempt"]["attemptId"]
            assert restored.get_run(rid, actor=USER)["state"] == "completed"
            out = Path("evidence/job-automatic-recovery-rev3/default-detached-proof.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"transport": "owned-loopback-HTTP", "executor": "product-default-detached",
                "pulse": "FinalService.start", "coldReopen": True, "originalCleanupComplete": True,
                "originalSupervisorExited": original["supervisorExitProof"]["confirmed"],
                "runs": len(result["runs"]), "attempts": len(result["attempts"]),
                "distinctEffects": len({fc.digest(e["effectKey"]) for e in result["effects"]}),
                "actualInvocations": int(count.read_text()), "executorHistoryRows": len(doc["history"])}, indent=2) + "\n", encoding="utf-8")
        asyncio.run(run())
        assert client.get("/api/job-runs/" + rid).json()["state"] == "completed"
    finally:
        env.client = old_client
        client.close()
        server.should_exit = True
        thread.join(8)
        sock.close()
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        proof_path = Path("evidence/job-automatic-recovery-rev3/default-detached-proof.json")
        if proof_path.exists():
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            proof["ownedPortReleased"] = True
            proof_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
