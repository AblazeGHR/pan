"""WP05b FE02/FE05: owned temporary stores and mocked lifecycle effects."""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import subprocess
import sys
import time

import pytest

from packages.actions.typed_executor import EffectReceipt, TypedExecutor
from packages.core import main_lifecycle as lifecycle
from packages.jobs.independent_executor import (
    ExecutionConflict, ExecutionHost, ExecutionStore, IndependentExecutor,
    effect_id, recover_execution_events,
)
from packages.jobs.independent_execution_runner import execute_claimed


def run(value):
    return asyncio.run(value)


def attempt(**changes):
    return {"attemptId": "attempt_original", "runId": "run_original", "stepId": "step_original",
            "leaseId": "lease_original", "ownerEpoch": 1, "generation": 1, "state": "running", **changes}


def key():
    return {"runId": "run_original", "stepId": "step_original", "targetId": "original", "operationOrdinal": 0}


def process_plan(tmp_path):
    return {"operation": "process", "argv": [sys.executable, "-c", "pass"], "cwd": str(tmp_path)}


def admit(store, plan, current=None, **kwargs):
    return store.admit(job_id="job_original", attempt=current or attempt(), effect_key=key(), frozen_plan=plan, **kwargs)


def claim(store, plan):
    doc, _ = admit(store, plan)
    return store.claim(doc["effectId"], attempt(), {"pid": 7, "birth": "birth"})


def test_fe02_concurrent_admission_never_calls_legacy_create_retry(tmp_path, monkeypatch):
    from packages.jobs.independent_service import JobStore
    def forbidden(*unused):
        raise AssertionError("canonical executor called legacy JobStore")
    monkeypatch.setattr(JobStore, "create", forbidden)
    monkeypatch.setattr(JobStore, "retry", forbidden)
    store = ExecutionStore(tmp_path)
    launches = []
    host = ExecutionHost(store, launch=lambda *args: launches.append(args))
    request = {"jobId": "job_original", "attempt": attempt(), "effectKey": key(), "frozenPlan": process_plan(tmp_path)}
    with ThreadPoolExecutor(max_workers=6) as pool:
        docs = list(pool.map(host.submit_execution, [request] * 6))
    assert len(launches) == 1
    assert {doc["jobId"] for doc in docs} == {"job_original"}
    assert {doc["attempt"]["runId"] for doc in docs} == {"run_original"}
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchone() is None


@pytest.mark.parametrize("field,value", [("leaseId", "lease_other"), ("ownerEpoch", 2), ("generation", 2), ("attemptId", "attempt_other")])
def test_fe02_stale_fence_never_settles_or_cancels(tmp_path, field, value):
    store = ExecutionStore(tmp_path)
    doc = claim(store, process_plan(tmp_path))
    result = EffectReceipt(key(), "succeeded", {"messageId": "external-original"}).as_dict()
    changed = attempt(**{field: value})
    for action in [lambda: store.finish(doc["effectId"], changed, doc["claim"], result),
                   lambda: store.cancel(doc["effectId"], changed),
                   lambda: admit(store, process_plan(tmp_path), changed)]:
        with pytest.raises(ExecutionConflict):
            action()
    assert store.get(doc["effectId"])["result"] is None
    assert store.events() == {"events": []}


@pytest.mark.parametrize("outcome", ["succeeded", "unknown", "partial"])
def test_fe02_success_unknown_partial_cannot_be_replayed(tmp_path, outcome):
    store = ExecutionStore(tmp_path)
    plan = process_plan(tmp_path)
    doc = claim(store, plan)
    receipt = EffectReceipt(key(), outcome, {"messageId": "external-31"}).as_dict()
    store.finish(doc["effectId"], attempt(), doc["claim"], receipt)
    assert admit(store, plan, retry=True)[0]["result"] == receipt
    with pytest.raises(ExecutionConflict):
        admit(store, plan, attempt(attemptId="attempt_retry", generation=2), retry=True)
    assert store.events()["events"][0]["result"]["receipt"]["messageId"] == "external-31"


def test_fe02_retry_keeps_job_run_step_effect_with_new_attempt_history(tmp_path):
    store = ExecutionStore(tmp_path)
    plan = process_plan(tmp_path)
    doc = claim(store, plan)
    receipt = EffectReceipt(key(), "rejected", {"exitCode": 1, "processIdentity": {"pid": 2, "birth": "original"}}, {"confirmed": True}).as_dict()
    store.finish(doc["effectId"], attempt(), doc["claim"], receipt)
    retried, created = admit(store, plan, attempt(attemptId="attempt_retry", generation=2), retry=True)
    assert created and retried["jobId"] == doc["jobId"] and retried["effectKey"] == doc["effectKey"]
    assert retried["attempt"]["runId"] == doc["attempt"]["runId"]
    assert retried["history"][0]["result"] == receipt
    assert retried["attempt"]["attemptId"] != doc["attempt"]["attemptId"]
    retry_attempt = retried["attempt"]
    retry_doc = store.claim(doc["effectId"], retry_attempt, {"pid": 8, "birth": "retry-birth"})
    store.finish(doc["effectId"], retry_attempt, retry_doc["claim"], receipt)
    with pytest.raises(ExecutionConflict):
        admit(store, plan, attempt(generation=3), retry=True)
    assert len(store.events()["events"]) == 2


@pytest.mark.parametrize("confirmed", [False, None])
def test_fe02_retry_needs_cleanup_proof(tmp_path, confirmed):
    store = ExecutionStore(tmp_path)
    doc = claim(store, process_plan(tmp_path))
    store.finish(doc["effectId"], attempt(), doc["claim"], EffectReceipt(key(), "rejected", {}, {"confirmed": confirmed}).as_dict())
    with pytest.raises(ExecutionConflict):
        admit(store, process_plan(tmp_path), attempt(attemptId="retry", generation=2), retry=True)


def test_fe02_missing_or_wrong_supervisor_claim_cannot_publish(tmp_path):
    store = ExecutionStore(tmp_path)
    doc, _ = admit(store, process_plan(tmp_path))
    for token in [None, "", "foreign"]:
        with pytest.raises(ExecutionConflict):
            store.phase(doc["effectId"], attempt(), token, "executing")
        with pytest.raises(ExecutionConflict):
            store.finish(doc["effectId"], attempt(), token, EffectReceipt(key(), "succeeded", {}).as_dict())
    assert store.get(doc["effectId"])["result"] is None


def test_fe02_ambiguous_launch_does_not_relaunch_or_change_other_intents(tmp_path):
    store = ExecutionStore(tmp_path)
    other_key = {**key(), "targetId": "other"}
    other, _ = store.admit(job_id="job_other", attempt=attempt(), effect_key=other_key, frozen_plan=process_plan(tmp_path))
    calls = []
    def fail(*args):
        calls.append(args)
        raise OSError("spawn result unknown")
    host = ExecutionHost(store, launch=fail)
    request = {"jobId": "job_original", "attempt": attempt(), "effectKey": key(), "frozenPlan": process_plan(tmp_path)}
    doc = host.submit_execution(request)
    assert doc["result"]["outcome"] == "unknown"
    assert host.submit_execution(request)["result"] == doc["result"] and len(calls) == 1
    assert store.get(other["effectId"])["phase"] == "admitted"


def test_fe02_intent_and_terminal_outbox_transactions_are_atomic(tmp_path):
    store = ExecutionStore(tmp_path)
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_intent BEFORE INSERT ON executions BEGIN SELECT RAISE(ABORT,'failure'); END")
    launches = []
    host = ExecutionHost(store, launch=lambda *args: launches.append(args))
    with pytest.raises(sqlite3.IntegrityError):
        host.submit_execution({"jobId": "job_original", "attempt": attempt(), "effectKey": key(), "frozenPlan": process_plan(tmp_path)})
    assert not launches
    with store.connect() as db:
        db.execute("DROP TRIGGER reject_intent")
    doc = claim(store, process_plan(tmp_path))
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_outbox BEFORE INSERT ON execution_outbox BEGIN SELECT RAISE(ABORT,'failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.finish(doc["effectId"], attempt(), doc["claim"], EffectReceipt(key(), "succeeded", {"messageId": "31"}).as_dict())
    assert store.get(doc["effectId"])["result"] is None and not store.events()["events"]


def test_fe02_live_supervisor_survives_host_reload_and_ack_loss(tmp_path):
    store = ExecutionStore(tmp_path)
    doc = claim(store, process_plan(tmp_path))
    store.phase(doc["effectId"], attempt(), doc["claim"], "running", processIdentity={"pid": 8, "birth": "original"})
    reloaded = ExecutionStore(tmp_path)
    probes = []
    reloaded.recover(lambda identity: probes.append(identity) or True)
    assert probes == [{"pid": 7, "birth": "birth"}]
    assert reloaded.get(doc["effectId"])["result"] is None
    receipt = EffectReceipt(key(), "succeeded", {"messageId": "msg-original", "processIdentity": {"pid": 8, "birth": "original"}}, {"confirmed": True}).as_dict()
    store.finish(doc["effectId"], attempt(), doc["claim"], receipt)
    host = ExecutionHost(reloaded, launch=lambda *args: pytest.fail("must not relaunch"))
    delivered = {}
    async def persist(event):
        delivered[event["eventId"]] = event
        return True
    original_ack = host.ack_execution
    host.ack_execution = lambda identity: (_ for _ in ()).throw(OSError("ack lost"))
    with pytest.raises(OSError):
        run(recover_execution_events(host, persist))
    assert len(host.execution_events()["events"]) == 1
    host.ack_execution = original_ack
    assert run(recover_execution_events(host, persist)) == 1
    assert run(recover_execution_events(host, persist)) == 0
    assert len(delivered) == 1
    event = next(iter(delivered.values()))
    assert event["jobId"] == "job_original" and event["attempt"] == attempt()
    assert event["result"] == receipt


def test_fe02_typed_external_message_receipt_survives_offline_recovery(tmp_path):
    store = ExecutionStore(tmp_path)
    plan = {"operation": "session-message", "sessionId": "session_target", "text": "typed message"}
    doc = claim(store, plan)
    calls = []
    class Provider:
        async def send_session(self, session_id, text, **kwargs):
            calls.append((session_id, text, kwargs["effect_key"]))
            return {"outcome": "succeeded", "messageId": "remote-original-123"}
    final = run(execute_claimed(store, doc, executor=TypedExecutor(session_provider=Provider())))
    offline = ExecutionStore(tmp_path)
    assert len(calls) == 1 and calls[0][2] == key()
    assert offline.get(doc["effectId"])["result"]["receipt"]["messageId"] == "remote-original-123"
    assert admit(offline, plan)[0]["result"] == final["result"]
    assert offline.events()["events"][0]["result"]["receipt"]["messageId"] == "remote-original-123"


def test_fe02_explicit_reconciliation_queries_original_receipt_without_new_attempt(tmp_path):
    store = ExecutionStore(tmp_path)
    doc = claim(store, process_plan(tmp_path))
    unknown = EffectReceipt(key(), "unknown", {"messageId": "remote-original-123"}).as_dict()
    store.finish(doc["effectId"], attempt(), doc["claim"], unknown)
    store.ack(store.events()["events"][0]["eventId"])
    lookups = []
    def lookup(original):
        lookups.append(original["result"]["receipt"]["messageId"])
        return EffectReceipt(original["effectKey"], "succeeded", {"messageId": lookups[-1], "providerVerified": True}).as_dict()
    with pytest.raises(ExecutionConflict):
        store.reconcile(doc["effectId"], attempt(ownerEpoch=2), lookup)
    assert lookups == []
    final = store.reconcile(doc["effectId"], attempt(), lookup)
    assert lookups == ["remote-original-123"] and final["attempt"] == attempt()
    assert final["reconciliationHistory"] == [unknown]
    assert len(store.events()["events"]) == 1
    assert ":reconciled:" in store.events()["events"][0]["eventId"]
    assert store.reconcile(doc["effectId"], attempt(), lookup) == final
    assert lookups == ["remote-original-123"]


def test_fe02_lost_or_unclaimed_supervisor_is_unknown_without_replay(tmp_path):
    store = ExecutionStore(tmp_path)
    doc, _ = admit(store, process_plan(tmp_path))
    store.recover(lambda identity: False)
    recovered = store.get(doc["effectId"])
    assert recovered["result"]["outcome"] == "unknown"
    with pytest.raises(ExecutionConflict):
        store.claim(doc["effectId"], attempt(), {"pid": 7, "birth": "birth"})
    assert len(store.events()["events"]) == 1
    assert store.ack(store.events()["events"][0]["eventId"])["ok"]
    assert ExecutionStore(tmp_path).get(doc["effectId"])["result"]["outcome"] == "unknown"


def test_fe02_real_detached_worker_keeps_identity_while_pan_is_absent(tmp_path):
    # No Pan listener/process: the standalone worker owns the actual command.
    store = ExecutionStore(tmp_path / "host")
    marker = tmp_path / "effect-count.txt"
    script = "import time,os; from pathlib import Path; assert not any(k.upper().startswith('PAN_') or k.upper()=='PYTHONPATH' for k in os.environ); time.sleep(.2); p=Path(" + repr(str(marker)) + "); p.write_text(p.read_text()+'x' if p.exists() else 'x')"
    plan = {"operation": "process", "argv": [sys.executable, "-c", script], "cwd": str(tmp_path)}
    request = {"jobId": "job_original", "attempt": attempt(), "effectKey": key(), "frozenPlan": plan}
    doc = ExecutionHost(store).submit_execution(request)
    reader = ExecutionStore(store.root)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        final = reader.get(doc["effectId"])
        if final["result"] is not None:
            break
        time.sleep(.05)
    assert final["result"] is not None, final
    assert final["result"]["outcome"] == "succeeded", final
    assert final["jobId"] == "job_original" and final["attempt"] == attempt()
    assert final["supervisorIdentity"]["pid"] != final["result"]["receipt"]["processIdentity"]["pid"]
    assert final["bootstrapReceipt"]["exited"] is True and final["bootstrapReceipt"]["exitCode"] == 0
    assert final["evidence"]["processIdentity"] == final["result"]["receipt"]["processIdentity"]
    assert final["result"]["cleanupProof"]["confirmed"] is True
    assert final["result"]["cleanupProof"]["ownedTree"]["confirmed"] is True
    assert ExecutionHost(reader).submit_execution(request)["result"] == final["result"]
    assert marker.read_text() == "x" and len(reader.events()["events"]) == 1


def test_fe02_real_worker_survives_the_admitting_host_process_exit(tmp_path):
    from packages.jobs.window import command_window_options
    root = tmp_path / "standalone-host"
    marker = tmp_path / "effect.txt"
    plan = {"operation": "process", "argv": [sys.executable, "-c",
        "import time; from pathlib import Path; time.sleep(.5); Path(" + repr(str(marker)) + ").write_text('once')"], "cwd": str(tmp_path)}
    request = {"jobId": "job_original", "attempt": attempt(), "effectKey": key(), "frozenPlan": plan}
    submitter = ("import json,sys; from packages.jobs.independent_executor import ExecutionHost,ExecutionStore; "
                 "ExecutionHost(ExecutionStore(sys.argv[1])).submit_execution(json.loads(sys.argv[2]))")
    parent = subprocess.run([sys.executable, "-c", submitter, str(root), json.dumps(request)],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=10, **command_window_options())
    assert parent.returncode == 0, parent.stderr.decode(errors="replace")
    # The admitting host is already gone; all following facts come from disk.
    reader = ExecutionStore(root)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        doc = reader.get(effect_id(key()))
        if doc["result"] is not None:
            break
        time.sleep(.05)
    assert doc["result"] is not None and doc["result"]["outcome"] == "succeeded", doc
    assert doc["attempt"] == attempt() and marker.read_text() == "once"
    assert len(reader.events()["events"]) == 1


def test_fe02_canonical_client_http_receipt_and_ack_do_not_use_legacy_jobs(tmp_path, monkeypatch):
    from tests.test_independent_job_service import _service, _stop
    from packages.jobs.independent_service import JobStore
    store = ExecutionStore(tmp_path)
    launches = []
    host = ExecutionHost(store, launch=lambda *args: launches.append(args))
    monkeypatch.setattr(JobStore, "execution_host", property(lambda self: host))
    server, thread, client, legacy = _service(tmp_path)
    request = {"jobId": "job_original", "attempt": attempt(), "effectKey": key(), "frozenPlan": process_plan(tmp_path)}
    try:
        doc = client.submit_execution(request)
        assert client.get_execution(doc["effectId"])["attempt"] == attempt()
        assert client.submit_execution(request)["effectId"] == doc["effectId"] and len(launches) == 1
        assert client.cancel_execution(doc["effectId"], attempt())["phase"] == "cancelled"
        event = client.execution_events()["events"][0]
        assert event["jobId"] == "job_original" and event["attempt"]["runId"] == "run_original"
        assert client.ack_execution(event["eventId"])["ok"] is True
        assert client.ack_execution(event["eventId"])["ok"] is True
        assert client.execution_events()["events"] == [] and legacy.list()["jobs"] == []
        for bad in [{**request, "attempt": attempt(ownerEpoch=2)},
                    {**request, "frozenPlan": {**request["frozenPlan"], "effect": "high_risk"}},
                    {**request, "frozenPlan": life_plan(tmp_path)}, {**request, "frozenPlan": None}]:
            with pytest.raises(RuntimeError, match="40[09]"):
                client.submit_execution(bad)
    finally:
        _stop(server, thread)


def test_fe02_pending_typed_executor_requeries_host_instead_of_caching_unknown(tmp_path):
    store = ExecutionStore(tmp_path)
    host = ExecutionHost(store, launch=lambda *args: None)
    adapter = IndependentExecutor(job_id="job_original", client=host)
    typed = TypedExecutor(independent_executor=adapter)
    args = {"attempt": attempt(), "effect_key": key(), "frozen_plan": process_plan(tmp_path)}
    pending = run(typed.execute(**args))
    assert pending["outcome"] == "unknown" and pending["receipt"]["code"] == "execution_pending"
    doc = store.claim(effect_id(key()), attempt(), {"pid": 7, "birth": "birth"})
    final = EffectReceipt(key(), "succeeded", {"processIdentity": {"pid": 8, "birth": "proof"}}, {"confirmed": True}).as_dict()
    store.finish(doc["effectId"], attempt(), doc["claim"], final)
    assert run(typed.execute(**args)) == final


def life_plan(tmp_path, operation="service.restart"):
    return {"operation": operation, "effect": "high_risk", "arguments": {
        "root": str(tmp_path), "port": 43210, "oldPid": 41, "oldPidCreatedAt": 12.5}}


def life_setup(tmp_path, monkeypatch, operation="service.restart"):
    store = ExecutionStore(tmp_path / "executor")
    plan = life_plan(tmp_path, operation)
    doc = claim(store, plan)
    calls = []
    monkeypatch.setattr(lifecycle, "service_process_identity", lambda *args: {"ok": True})
    def stop(*args, **kwargs):
        current = store.get(doc["effectId"])
        assert current["phase"] == "stopping" and current["cancellable"] is False
        with pytest.raises(ExecutionConflict):
            store.cancel(doc["effectId"], attempt())
        calls.append("stop")
        return {"stopped": True}
    monkeypatch.setattr(lifecycle.launcher, "stop_service", stop)
    monkeypatch.setattr(lifecycle.launcher, "start_service", lambda *args, **kwargs: calls.append("start"))
    monkeypatch.setattr(lifecycle, "offline_checks", lambda **kwargs: {"ok": True, "listenerOwner": None, "oldProcessStatus": "dead"})
    monkeypatch.setattr(lifecycle, "START_TIMEOUT_SEC", .015)
    monkeypatch.setattr(lifecycle, "READY_POLL_SEC", .001)
    monkeypatch.setattr(lifecycle, "ready_checks", lambda **kwargs: {
        "ok": True, "newPid": 42, "newPidCreatedAt": 13.5, "listenerOwner": 42,
        "port": 43210, "http": True, "identity": {"ok": True, "root": str(tmp_path)}})
    return store, doc, plan, calls


def test_fe05_protected_restart_completes_only_with_full_ready_evidence(tmp_path, monkeypatch):
    store, doc, plan, calls = life_setup(tmp_path, monkeypatch)
    result = run(execute_claimed(store, doc))
    assert calls == ["stop", "start"]
    assert result["result"]["outcome"] == "succeeded"
    assert result["phase"] == "ready" and result["cancellable"] is False
    assert result["phases"] == ["admitted", "preparing", "stopping", "stopped", "starting", "checking_ready", "ready"]
    assert result["evidence"]["root"] == str(tmp_path)
    assert ExecutionStore(store.root).get(doc["effectId"])["result"] == result["result"]
    assert len(store.events()["events"]) == 1


@pytest.mark.parametrize("change", [
    {"newPid": 41}, {"newPid": None}, {"newPidCreatedAt": None}, {"http": False},
    {"newPidCreatedAt": 0}, {"newPidCreatedAt": 12.5}, {"newPidCreatedAt": float("nan")},
    {"port": 43211}, {"listenerOwner": 43}, {"identity": {"ok": False}},
    {"identity": {"ok": True, "root": "other-checkout"}}, {"ok": False},
])
def test_fe05_restart_rejects_incomplete_ready_proof(tmp_path, monkeypatch, change):
    store, doc, plan, calls = life_setup(tmp_path, monkeypatch)
    original = lifecycle.ready_checks
    monkeypatch.setattr(lifecycle, "ready_checks", lambda **kwargs: {**original(**kwargs), **change})
    result = run(execute_claimed(store, doc))
    assert result["result"]["outcome"] == "unknown" and result["phase"] == "uncertain"
    assert calls == ["stop", "start"] and result["cancellable"] is False


@pytest.mark.parametrize("operation", ["service.restart", "service.exit"])
def test_fe05_offline_evidence_required_even_after_stop_reports_success(tmp_path, monkeypatch, operation):
    store, doc, plan, calls = life_setup(tmp_path, monkeypatch, operation)
    monkeypatch.setattr(lifecycle, "offline_checks", lambda **kwargs: {"ok": False, "listenerOwner": 99})
    result = run(execute_claimed(store, doc))
    assert result["result"]["outcome"] == "unknown" and calls == ["stop"]


def test_fe05_exit_offline_and_pre_effect_cancellation(tmp_path, monkeypatch):
    store, doc, plan, calls = life_setup(tmp_path, monkeypatch, "service.exit")
    store.cancel(doc["effectId"], attempt())
    cancelled = run(execute_claimed(store, doc))
    assert calls == [] and cancelled["phase"] == "cancelled"
    with pytest.raises(ExecutionConflict):
        admit(store, plan, attempt(attemptId="retry", generation=2), retry=True)


def test_fe05_exit_never_starts_service_and_receipt_is_queryable_offline(tmp_path, monkeypatch):
    store, doc, plan, calls = life_setup(tmp_path, monkeypatch, "service.exit")
    final = run(execute_claimed(store, doc))
    assert calls == ["stop"] and final["phase"] == "offline"
    assert final["result"]["outcome"] == "succeeded"
    reopened = ExecutionStore(store.root)
    assert reopened.get(doc["effectId"])["result"]["receipt"]["offlineProof"]["ok"] is True
    assert len(reopened.events()["events"]) == 1


def test_fe05_stop_exception_and_supervisor_loss_do_not_reexecute(tmp_path, monkeypatch):
    store, doc, plan, calls = life_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(lifecycle.launcher, "stop_service", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("stop ambiguous")))
    final = run(execute_claimed(store, doc))
    assert final["result"]["outcome"] == "unknown" and not final["cancellable"]
    assert admit(store, plan)[0]["result"] == final["result"]
    assert calls == []


@pytest.mark.parametrize("grant_change", [{"stepCount": 2}, {"operation": "service.exit"}, {"jobId": "other"}, {"root": "other"}])
def test_fe05_payload_cannot_grant_privilege_or_multistep_lifecycle(tmp_path, grant_change):
    store = ExecutionStore(tmp_path / "executor")
    launches = []
    protected = lifecycle.ProtectedLifecycleExecutor(job_id="job_original", store=store,
        authorize=lambda scope: {**scope, **grant_change}, launch=lambda *args: launches.append(args))
    result = run(TypedExecutor(lifecycle_executor=protected).execute(attempt=attempt(), effect_key=key(), frozen_plan=life_plan(tmp_path)))
    assert result["outcome"] == "rejected" and result["receipt"]["code"] == "authorization_denied"
    assert not launches and not store.events()["events"]


def test_fe05_protected_admission_and_capability_guard(tmp_path, monkeypatch):
    from packages.core import platform
    store = ExecutionStore(tmp_path / "executor")
    launches = []
    protected = lifecycle.ProtectedLifecycleExecutor(job_id="job_original", store=store,
        authorize=lambda scope: scope, launch=lambda *args: launches.append(args))
    monkeypatch.setattr(platform, "supports", lambda capability: False)
    result = run(protected.execute(attempt=attempt(), effect_key=key(), frozen_plan=life_plan(tmp_path)))
    assert result["receipt"]["code"] == "capability_unsupported" and not launches
    monkeypatch.setattr(platform, "supports", lambda capability: True)
    result = run(protected.execute(attempt=attempt(), effect_key=key(), frozen_plan=life_plan(tmp_path)))
    assert result["outcome"] == "unknown" and len(launches) == 1
    assert run(protected.execute(attempt=attempt(), effect_key=key(), frozen_plan=life_plan(tmp_path))) == result
    assert len(launches) == 1
    denied = run(TypedExecutor().execute(attempt=attempt(), effect_key=key(), frozen_plan=life_plan(tmp_path)))
    assert denied["receipt"]["code"] == "authorization_denied"


@pytest.mark.parametrize("probe_error", ["listener", "birth"])
def test_fe05_offline_probe_errors_are_not_offline(tmp_path, monkeypatch, probe_error):
    import psutil
    from packages.core.platform.execution_host import service_offline_evidence
    def error(*args, **kwargs):
        raise psutil.AccessDenied()
    monkeypatch.setattr(psutil, "net_connections", error if probe_error == "listener" else lambda **kwargs: [])
    monkeypatch.setattr(psutil, "Process", error if probe_error == "birth" else lambda pid: (_ for _ in ()).throw(psutil.NoSuchProcess(pid)))
    assert service_offline_evidence(root=str(tmp_path), port=43210, old_pid=41, old_pid_created_at=12.5)["ok"] is False


def test_fe05_bootstrap_exit_receipt_requires_matching_nonce_and_success(tmp_path):
    from packages.core.platform.execution_host import read_bootstrap_receipt
    receipt = tmp_path / "bootstrap.json"
    for value in [{"nonce": "wrong", "exited": True, "exitCode": 0},
                  {"nonce": "original", "exited": False, "exitCode": 0},
                  {"nonce": "original", "exited": True, "exitCode": 1}]:
        receipt.write_text(json.dumps(value))
        with pytest.raises(RuntimeError):
            read_bootstrap_receipt(receipt, "original")
    with pytest.raises(RuntimeError):
        read_bootstrap_receipt(tmp_path / "missing.json", "original", timeout=.01)
