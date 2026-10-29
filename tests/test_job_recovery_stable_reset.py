"""Stable windows use original execution proof; generations remain monotonic."""
import asyncio
import copy
from decimal import Decimal
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
import uvicorn

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, definition, object_ref
from packages.jobs.final_runtime.ledger import aggregate
from packages.jobs.independent_executor import ExecutionStore, effect_id
from tests.test_job_automatic_recovery import (
    USER, env, policy, job_definition, create, state, seed as old_seed, observe, marker, drive_until,
)


def timing(seconds):
    return {"startedAt": "2026-10-29T00:00:00Z", "endedAt": "2026-10-29T00:00:01Z",
            "startedMonotonicNs": 1000000000000,
            "endedMonotonicNs": 1000000000000 + int(Decimal(seconds) * 1000000000)}


def seed(h, monkeypatch, durations=("0.1", "0.1", "0.1"), **kwargs):
    value = kwargs.pop("value", job_definition(h, policy(stableResetSeconds="1")))
    row = old_seed(h, monkeypatch, value=value, **kwargs)
    row.doc["result"]["receipt"]["executionTiming"] = timing(durations[0])
    row.calls = []
    async def execute(**call):
        row.calls.append(copy.deepcopy(call))
        index = len(row.calls)
        row.doc["attempt"] = copy.deepcopy(call["attempt"])
        row.doc["result"]["receipt"]["executionTiming"] = timing(durations[min(index, len(durations)-1)])
        return {"effectKey": call["effect_key"], "outcome": "unknown", "receipt": {"code": "execution_pending"}}
    monkeypatch.setattr(row.original, "execute", execute)
    return row


def plan(h, row):
    return h.service.final.store.get(row.jid)["finalRuntime"]["plans"][row.rid]


def test_baseline_rejection_and_old_algorithm_characterization(env):
    baseline = "4b577633d2d9ffcdc5bf8a44b12836724c18804e"
    source = subprocess.check_output(["git", "show", baseline + ":packages/jobs/final_service.py"], text=True, encoding="utf-8")
    namespace = {"__name__": "owned_recovery_baseline"}
    exec(compile(source, "owned-baseline-final-service", "exec"), namespace)
    value = fc.validate_definition("job", job_definition(env, policy(stableResetSeconds="1")))
    with env.service.transaction(readonly=True) as db:
        with pytest.raises(fc.ContractError) as exc:
            namespace["FinalService"]._references(env.service, db, "job", value)
    assert exc.value.code == "capability_unsupported"
    old = subprocess.check_output(["git", "show", baseline + ":packages/jobs/action_runtime.py"], text=True, encoding="utf-8")
    assert "stable=reset>0 and time.time()-execution_audit.get('startedAt',time.time())>=reset" in old
    assert "count=(0 if stable else job.get('recoveryFailures',0))+(0 if outcome.get('audit',{}).get('gateBlocked') else 1)" in old
    assert "set(signal)=={'type','completed'}" in old
    jid, rid = create(env, value)
    assert plan(env, type("Row", (), {"jid": jid, "rid": rid})())["frozen"]["recovery"]["stableResetSeconds"] == "1"


@pytest.mark.parametrize("second,reset", [("0.999999999", False), ("1", True), ("1.000000001", True)])
def test_before_exact_after_threshold_and_monotonic_attempts(env, monkeypatch, second, reset):
    row = seed(env, monkeypatch, durations=("0.1", second, "0.1"))
    observe(env)  # Failure 1: first budget.
    asyncio.run(env.service.dispatch_once())  # Attempt 2.
    observe(env)
    windows = copy.deepcopy(plan(env, row).get("recoveryWindows", {}))
    assert bool(windows) is reset
    if reset:
        assert windows["s0"]["baseGeneration"] == 1
        assert state(env, row.jid)["steps"][row.rid]["s0"]["attemptCount"] == 2
        asyncio.run(env.service.dispatch_once())
        observe(env)
    result = state(env, row.jid)
    total = 3 if reset else 2
    assert result["runs"][row.rid]["state"] == "failed"
    assert sorted(a["generation"] for a in result["attempts"].values()) == list(range(1, total+1))
    assert len({e["effectKey"]["runId"] for e in result["effects"]}) == 1
    assert len({fc.digest(e["effectKey"]) for e in result["effects"]}) == 1
    assert len({a["attemptId"] for a in result["attempts"].values()}) == total
    assert plan(env, row).get("recoveryWindows", {}) == windows


def test_decimal_threshold_uses_execution_ticks_not_late_host_clock(env, monkeypatch):
    row = seed(env, monkeypatch, durations=("0.1", "0.100000001", "0.01"),
        value=job_definition(env, policy(stableResetSeconds="0.100000001", backoffSeconds=["0", "0.125"])))
    observe(env)
    asyncio.run(env.service.dispatch_once())
    original = env.service._recovery_time
    monkeypatch.setattr(env.service, "_recovery_time", lambda timestamp=None: original(timestamp) if timestamp else Decimal("999999999999"))
    observe(env)
    assert plan(env, row)["recoveryWindows"]["s0"]["baseGeneration"] == 1
    assert Decimal(marker(env, row)["eligibleAt"]) == original(state(env, row.jid)["attempts"][row.doc["attempt"]["attemptId"]]["endedAt"])


@pytest.mark.parametrize("change", ["missing", "reversed", "bool", "invalidTimestamp", "negative", "prose"])
def test_untrusted_or_missing_timing_cannot_reset_or_schedule(env, monkeypatch, change):
    row = seed(env, monkeypatch)
    t = row.doc["result"]["receipt"]["executionTiming"]
    if change == "missing":
        row.doc["result"]["receipt"].pop("executionTiming")
    elif change == "reversed":
        t["endedMonotonicNs"] = t["startedMonotonicNs"] - 1
    elif change == "bool":
        t["endedMonotonicNs"] = True
    elif change == "invalidTimestamp":
        t["endedAt"] = "not a timestamp"
    elif change == "negative":
        t["startedMonotonicNs"] = -1
    else:
        row.doc["result"]["receipt"]["executionTiming"] = "stable for 300 seconds"
    observe(env)
    assert plan(env, row).get("recoveryWindows") is None and marker(env, row) is None
    assert state(env, row.jid)["runs"][row.rid]["state"] == "failed"
    assert len(state(env, row.jid)["attempts"]) == 1


def test_wait_and_query_lag_never_become_stable_execution(env, monkeypatch):
    row = seed(env, monkeypatch, durations=("0.1", "0.1"))
    monkeypatch.setattr(env.service, "_recovery_time", lambda timestamp=None: Decimal("999999999") if timestamp else Decimal("999999999999"))
    observe(env)
    asyncio.run(env.service.dispatch_once())
    observe(env)
    assert state(env, row.jid)["runs"][row.rid]["state"] == "failed"
    assert plan(env, row).get("recoveryWindows") is None


def test_cold_reopen_concurrent_pulse_and_lost_lookup_ack(env, monkeypatch):
    row = seed(env, monkeypatch, durations=("0.1", "1", "0.1"))
    observe(env)
    asyncio.run(env.service.dispatch_once())
    observe(env)
    before = copy.deepcopy(plan(env, row))
    original_calls = len(row.calls)
    observe(env)  # Lost acknowledgment: already-consumed original is not reapplied.
    assert plan(env, row) == before
    restored = FinalService(env.root, env.auth)
    env.service = restored
    monkeypatch.setattr(restored, "_external_adapter", lambda _: row.original)
    restored.recover()
    assert plan(env, row) == before
    async def both():
        await asyncio.gather(restored.dispatch_once(), restored.dispatch_once())
    asyncio.run(both())
    assert len(row.calls) == original_calls + 1
    observe(env)
    assert len(state(env, row.jid)["attempts"]) == 3
    assert plan(env, row)["recoveryWindows"] == before["recoveryWindows"]


@pytest.mark.parametrize("control", ["revoke", "cancel", "pause"])
def test_window_and_deadline_survive_controls_without_attempt(env, monkeypatch, control):
    grants = env.auth._default_permissions()
    grants["job"]["execute"] = grants["action"]["manageSystemActions"] = True
    env.auth.change_permissions(grants, 0)
    row = seed(env, monkeypatch, durations=("0.1", "1", "0.1"), actor={"type":"agent", "id":"owned", "revision":1})
    observe(env)
    asyncio.run(env.service.dispatch_once())
    observe(env)
    before = copy.deepcopy(plan(env, row))
    if control == "revoke":
        env.auth.change_permissions(env.auth._default_permissions(), 1)
    else:
        run = state(env, row.jid)["runs"][row.rid]
        env.service.control_run(row.rid, {"operation": control, "requestKey": control,
            "expectedStateRevision":run["stateRevision"]}, actor=USER)
    asyncio.run(env.service.dispatch_once())
    after = plan(env, row)
    assert after["autoRecovery"] == before["autoRecovery"]
    assert after["recoveryWindows"] == before["recoveryWindows"]
    assert len(state(env, row.jid)["attempts"]) == 2 and len(row.calls) == 1


@pytest.mark.parametrize("uncertain", ["unknown", "partial", "cleanup", "supervisor", "stale"])
def test_stable_elapsed_never_overrides_original_safety(env, monkeypatch, uncertain):
    row = seed(env, monkeypatch, durations=("1",))
    if uncertain in {"unknown", "partial"}:
        row.doc["result"]["outcome"] = uncertain
    elif uncertain == "cleanup":
        row.doc["result"]["cleanupProof"]["confirmed"] = False
    elif uncertain == "supervisor":
        row.doc["supervisorExitProof"]["confirmed"] = False
    else:
        row.doc["attempt"]["generation"] += 1
    if uncertain == "stale":
        with pytest.raises(fc.ContractError):
            observe(env)
    else:
        observe(env)
    assert marker(env, row) is None and plan(env, row).get("recoveryWindows") is None
    assert len(state(env, row.jid)["attempts"]) == 1


def test_reset_window_rolls_back_with_receipt_commit(env, monkeypatch):
    row = seed(env, monkeypatch, durations=("1",))
    save = env.service._save
    def fail(db, kind, jid, job):
        if job.get("finalRuntime", {}).get("plans", {}).get(row.rid, {}).get("recoveryWindows"):
            raise OSError("owned window commit fault")
        return save(db, kind, jid, job)
    monkeypatch.setattr(env.service, "_save", fail)
    with pytest.raises(OSError):
        observe(env)
    assert plan(env, row).get("recoveryWindows") is None
    assert state(env, row.jid)["attempts"][row.attempt["attemptId"]]["state"] == "running"
    monkeypatch.setattr(env.service, "_save", save)
    observe(env)
    before = copy.deepcopy(plan(env, row))
    observe(env)
    assert plan(env, row) == before


def test_frozen_reset_policy_is_not_promoted_or_changed(env, monkeypatch):
    row = seed(env, monkeypatch, durations=("0.1", "1"))
    observe(env)
    snapshot = env.service.final.store.get(row.jid)
    value = definition("job", snapshot["canonical"])
    value["recovery"]["stableResetSeconds"] = "9999"
    response = env.client.put("/api/jobs/" + row.jid, json={"definition":value, "expectedRevision":1, "requestKey":"edit"})
    assert response.status_code == 200, response.text
    asyncio.run(env.service.dispatch_once())
    observe(env)
    assert plan(env, row)["recoveryWindows"]["s0"]["baseGeneration"] == 1
    assert plan(env, row)["frozen"]["recovery"]["stableResetSeconds"] == "1"


@pytest.mark.parametrize("reason", ["timeout", "failed"])
def test_stable_window_supported_reasons_still_need_allowlist(env, monkeypatch, reason):
    row = seed(env, monkeypatch, durations=("0.1", "1", "0.1"), reason=reason,
        value=job_definition(env, policy(stableResetSeconds="1", retryIf=[reason])))
    observe(env)
    asyncio.run(env.service.dispatch_once())
    observe(env)
    assert plan(env, row)["recoveryWindows"]["s0"]["baseGeneration"] == 1
    assert marker(env, row)["reason"] == reason


def test_stable_result_denied_reason_never_resets_budget(env, monkeypatch):
    row = seed(env, monkeypatch, durations=("1",), reason="timeout")
    observe(env)
    assert marker(env, row) is None and plan(env, row).get("recoveryWindows") is None


def test_zero_reset_retains_lifetime_budget_even_with_long_execution(env, monkeypatch):
    row = seed(env, monkeypatch, durations=("300", "300"), value=job_definition(env, policy(stableResetSeconds="0")))
    observe(env)
    asyncio.run(env.service.dispatch_once())
    observe(env)
    assert state(env, row.jid)["runs"][row.rid]["state"] == "failed"
    assert plan(env, row).get("recoveryWindows") is None and len(state(env, row.jid)["attempts"]) == 2


@pytest.mark.parametrize("mutation", ["base", "attempt", "timing", "threshold"])
def test_corrupt_window_cannot_buy_another_attempt(env, monkeypatch, mutation):
    row = seed(env, monkeypatch, durations=("0.1", "1", "0.1"))
    observe(env)
    asyncio.run(env.service.dispatch_once())
    observe(env)
    with env.service.transaction() as db:
        job = env.service._get(db, "job", row.jid)
        s = aggregate(job, env.root)
        p = s["plans"][row.rid]
        window = p["recoveryWindows"]["s0"]
        if mutation == "base":
            window["baseGeneration"] = -1
        elif mutation == "attempt":
            window["afterAttemptId"] = "missing_original"
        elif mutation == "timing":
            effect = next(e for e in s["effects"] if e["attemptId"] == window["afterAttemptId"])
            effect["receipt"]["receipt"]["executionTiming"] = timing("0.1")
        else:
            p["frozen"]["recovery"]["stableResetSeconds"] = "2"
        env.service._save(db, "job", row.jid, job)
    with pytest.raises(fc.ContractError):
        asyncio.run(env.service.dispatch_once())
    assert len(row.calls) == 1 and len(state(env, row.jid)["attempts"]) == 2


def test_monitoring_frozen_history_never_executes_as_plain_exit_zero(env):
    jid, rid = create(env, job_definition(env, code="raise SystemExit(0)"))
    # Previously admitted data can be read; the existing effect boundary must
    # still fail closed rather than silently reinterpret the completion contract.
    with env.service.transaction() as db:
        job = env.service._get(db, "job", jid)
        aggregate(job, env.root)["plans"][rid]["frozen"]["steps"][0]["completion"] = {
            "contract":"monitoring", "parameters":{"signalType":"done"}}
        env.service._save(db, "job", jid, job)
    asyncio.run(env.service.dispatch_once())
    assert state(env, jid)["runs"][rid]["state"] == "failed"
    assert not state(env, jid)["attempts"] and not state(env, jid)["effects"]


def test_historical_monitoring_pending_exit_zero_remains_query_only(env, monkeypatch):
    row = seed(env, monkeypatch)
    with env.service.transaction() as db:
        job = env.service._get(db, "job", row.jid)
        aggregate(job, env.root)["plans"][row.rid]["frozen"]["steps"][0]["completion"] = {
            "contract":"monitoring", "parameters":{"signalType":"done"}}
        env.service._save(db, "job", row.jid, job)
    row.doc["result"]["outcome"] = "succeeded"
    row.doc["result"]["receipt"]["exitCode"] = 0
    observe(env)
    assert state(env, row.jid)["runs"][row.rid]["state"] == "running"
    assert not state(env, row.jid)["effects"]
    assert marker(env, row) is None
    assert env.service.diagnostics[-1]["code"] == "monitoring_completion_unsupported"


def test_monitoring_is_explicitly_unsupported_not_exit_zero_completion(env):
    action = next(a for a in env.service.actions(actor=USER)["items"] if a["id"] == "product_process_cleanup_v1")
    value = definition("action", action)
    value["completionContracts"].append({"id":"monitoring", "schema":{"type":"object"}, "terminalProofSchema":{"type":"object"}})
    action = env.service.publish_action("owned_monitor_declared", value)
    body = job_definition(env, policy(stableResetSeconds="1"), code="raise SystemExit(0)")
    body["steps"][0]["actionRef"] = object_ref("action", action)
    body["steps"][0]["completion"] = {"contract":"monitoring", "parameters":{"signalType":"owned.done"}}
    response = env.client.post("/api/jobs", json={"definition":body, "expectedRevision":0, "requestKey":"monitor"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "capability_unsupported"
    assert env.client.get("/api/jobs").json()["items"] == []


def test_default_detached_http_stable_reset_exceeds_total_cap_without_identity_reuse(env):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    server = uvicorn.Server(uvicorn.Config(env.app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets":[sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline
        time.sleep(.02)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=dict(env.client.headers))
    previous = env.client
    env.client = client
    counter = env.tmp / "invocations.txt"
    code = (f"from pathlib import Path; import time; p=Path({str(counter)!r}); n=int(p.read_text()) if p.exists() else 0; "
            "p.write_text(str(n+1)); time.sleep(2.2 if n == 1 else 0); raise SystemExit(7 if n < 2 else 0)")
    try:
        jid, rid = create(env, job_definition(env, policy(stableResetSeconds="2", backoffSeconds=["0.2"]), code))
        async def run():
            await drive_until(env, jid, rid, "retry_wait")
            # First short failure has no reset. Actual second process is stable.
            assert not env.service.final.store.get(jid)["finalRuntime"]["plans"][rid].get("recoveryWindows")
            await env.service.dispatch_once()
            deadline = time.monotonic() + 30
            while True:
                await env.service.dispatch_once()
                p = env.service.final.store.get(jid)["finalRuntime"]["plans"][rid]
                if p.get("recoveryWindows"):
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(.03)
            frozen, window = copy.deepcopy(p["frozen"]), copy.deepcopy(p["recoveryWindows"])
            assert window["s0"]["baseGeneration"] == 1
            await env.service.close()
            restored = FinalService(env.root, env.auth)
            env.service = restored
            env.app.state.job_final_service = restored
            restored.recover()
            assert restored.final.store.get(jid)["finalRuntime"]["plans"][rid]["recoveryWindows"] == window
            await restored.start()  # Actual existing pulse after cold reopen.
            try:
                deadline = time.monotonic() + 30
                while state(env, jid)["runs"][rid]["state"] != "completed":
                    assert time.monotonic() < deadline
                    await asyncio.sleep(.04)
            finally:
                await restored.close()
            result = state(env, jid)
            assert len(result["runs"]) == 1 and len(result["attempts"]) == 3
            assert sorted(a["generation"] for a in result["attempts"].values()) == [1, 2, 3]
            assert len({fc.digest(e["effectKey"]) for e in result["effects"]}) == 1
            assert counter.read_text() == "3"
            doc = ExecutionStore(env.root / "executions").get(effect_id(result["effects"][-1]["effectKey"]))
            assert len(doc["history"]) == 2
            assert restored._stable_execution_seconds(doc["history"][1]) >= Decimal("2")
            assert restored.final.store.get(jid)["finalRuntime"]["plans"][rid]["frozen"] == frozen
            proof = {"transport":"owned HTTP", "executor":"default detached", "pulse":"FinalService.start",
                "maxAttempts":2, "actualInvocations":3, "runCount":1, "generations":[1,2,3], "distinctEffectKeys":1,
                "stableWindowBaseGeneration":1, "coldReopen":True, "frozenPolicyPreserved":True,
                "historyRows":len(doc["history"]), "originalStableElapsed":str(restored._stable_execution_seconds(doc["history"][1]))}
            out = Path("evidence/job-recovery-r4/default-detached.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(proof, indent=2)+"\n", encoding="utf-8")
        asyncio.run(run())
        assert client.get("/api/job-runs/"+rid).json()["state"] == "completed"
    finally:
        env.client = previous
        client.close()
        server.should_exit = True
        thread.join(8)
        sock.close()
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        out = Path("evidence/job-recovery-r4/default-detached.json")
        if out.exists():
            proof = json.loads(out.read_text(encoding="utf-8"))
            proof["ownedPortReleased"] = True
            out.write_text(json.dumps(proof, indent=2)+"\n", encoding="utf-8")
