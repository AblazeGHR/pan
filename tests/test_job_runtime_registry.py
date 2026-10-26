"""R5-P1 canonical storage/metadata ports, isolated profiles only."""
import asyncio
import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from packages.core import background_jobs as legacy
from packages.jobs import final_contracts as fc, pins
from packages.jobs.final_runtime.coordinator import FinalRuntime, JsonRegistryStore, atomic_write, registry_lock
from packages.jobs.final_runtime.executor import IndependentExecutor
from packages.jobs.final_runtime.ledger import aggregate
from packages.jobs.runtime_owner import OwnerFence
from tests.test_job_final_http_service import h, create, enabled, launch, control


def old_record(root, **extra):
    value = {"jobId": "job_original", "status": "completed", "kind": "session-message",
             "updatedAt": 1, "configRevision": 1, "notificationState": "not_applicable", **extra}
    return legacy._create(value, root)


def test_registry_process_cas_and_same_lock_as_compatibility(tmp_path):
    root = tmp_path / "registry"
    old_record(root, counter=0)
    script = """
import sys
from packages.jobs.final_runtime.coordinator import JsonRegistryStore
from packages.jobs.final_contracts import ContractError
store = JsonRegistryStore(sys.argv[1])
try:
    store.compare_and_swap('job_original', 1, lambda job: job.update(configRevision=2, counter=job['counter']+1))
except ContractError:
    sys.exit(9)
"""
    children = [subprocess.Popen([sys.executable, "-c", script, str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
    codes = []
    for child in children:
        _, error = child.communicate(timeout=30)
        assert child.returncode in {0, 9}, error.decode()
        codes.append(child.returncode)
    assert sorted(codes) == [0, 9, 9, 9]
    assert legacy.get("job_original", root)["counter"] == 1
    # Joining either import path is reentrant, with one lock identity.
    with legacy._job_lock("job_original", root), registry_lock(root, "job:job_original"):
        assert JsonRegistryStore(root).get("job_original")["configRevision"] == 2


def test_atomic_replace_fault_preserves_bytes_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "job.json"
    atomic_write(path, {"old": True})
    before = path.read_bytes()
    def fail(*unused):
        raise OSError("owned replacement fault")
    monkeypatch.setattr("packages.jobs.final_runtime.coordinator.os.replace", fail)
    with pytest.raises(OSError, match="replacement fault"):
        atomic_write(path, {"new": True})
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_json_writers_and_pins_are_fenced_but_old_bytes_remain_readable(tmp_path):
    root = tmp_path / "background_jobs"
    old_record(root)
    pins.set_pin(root, "job_original", True)
    raw = (root / "jobs/job_original.json").read_bytes()
    pin_bytes = (root / "pins.json").read_bytes()
    owner = OwnerFence(tmp_path)
    owner.claim(0)
    owner.set_state("active")
    try:
        for write in [lambda: pins.set_pin(root, "job_original", False),
                      lambda: JsonRegistryStore(root).mutate("job_original", lambda j: j.clear()),
                      lambda: legacy.delete_job("job_original", root),
                      lambda: legacy.cleanup_completed_jobs(registry_root=root, retention_days=1, now=10**8),
                      lambda: legacy.retarget_session_jobs("old", "new", root)]:
            with pytest.raises(fc.ContractError):
                write()
        assert pins.read(root) == {"job_original": True}
        assert legacy.get("job_original", root)["status"] == "completed"
        assert (root / "jobs/job_original.json").read_bytes() == raw
        assert (root / "pins.json").read_bytes() == pin_bytes
    finally:
        owner.release()


def test_legacy_retention_obeys_pin_and_corrupt_metadata(tmp_path):
    root = tmp_path / "background_jobs"
    old_record(root)
    pins.set_pin(root, "job_original", True)
    assert legacy.cleanup_completed_jobs(registry_root=root, retention_days=1, now=10**8)["deleted"] == 0
    (root / "pins.json").write_text("broken", encoding="utf-8")
    result = legacy.cleanup_completed_jobs(registry_root=root, retention_days=1, now=10**8)
    assert result["deleted"] == 0 and result["errorCount"] == 1
    assert legacy.get("job_original", root)


def test_canonical_metadata_cas_uses_owner_transaction(h):
    item = create(h)
    final = h.service.final
    h.service.owner.claim(0)
    h.service.owner.set_state("active")
    def pin(_):
        try:
            return final.set_pin(item["id"], True, expected_revision=0)
        except fc.ContractError:
            return None
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(pin, range(4)))
    assert sum(r is not None for r in results) == 1
    assert final.metadata(item["id"])["pinned"] is True
    assert final.metadata(item["id"])["revision"] == 1
    h.service.owner.release()
    with pytest.raises(fc.ContractError):
        final.set_pin(item["id"], False, expected_revision=1)


def test_handoff_changes_future_literals_preserves_run_and_original_notice_key(h):
    item = enabled(h)
    first = launch(h, item)
    final = h.service.final
    frozen = copy.deepcopy(final.store.get(item["id"])["finalRuntime"]["plans"])
    result = final.handoff(item["id"], "owned-test-session", "new-session",
                           expected_revision=item["configRevision"], request_key="handoff")
    assert final.handoff(item["id"], "owned-test-session", "new-session",
                         expected_revision=item["configRevision"], request_key="handoff") == result
    with pytest.raises(fc.ContractError):
        final.handoff(item["id"], "owned-test-session", "another", expected_revision=item["configRevision"], request_key="handoff")
    assert final.store.get(item["id"])["finalRuntime"]["plans"] == frozen
    assert final.store.get(item["id"])["config"]["steps"][0]["arguments"]["sessionId"]["value"] == "new-session"
    asyncio.run(h.service.dispatch_once())
    assert h.provider.calls[0][1]["runId"] == first["runId"]
    assert {row["legacyId"] for row in final.history(item["id"])["outbox"]} == {"canonical:" + first["runId"]}
    second = h.service.create_run(item["id"], {"requestKey": "new-run", "expectedRevision": result["configRevision"],
        "input": {}, "cause": {"type": "manual"}}, actor={"type": "user", "id": "local"})
    next_plan = final.store.get(item["id"])["finalRuntime"]["plans"][second["runId"]]
    assert next_plan["frozen"]["steps"][0]["arguments"]["sessionId"]["value"] == "new-session"


def finished(h):
    item = enabled(h)
    launched = launch(h, item)
    asyncio.run(h.service.dispatch_once())
    response = control(h, "jobs", item, "disable")
    assert response.status_code == 200, response.text
    return response.json()["result"]["object"], launched


def test_original_id_history_outbox_unknown_ack_and_archive(h):
    item, launched = finished(h)
    final = h.service.final
    jid, rid = item["id"], launched["runId"]
    final.register_original(jid, "legacy-job", source_digest="a" * 64, source_path="background_jobs/jobs/job_original.json",
                            expected_revision=item["configRevision"])
    before = final.history(jid)
    row = before["outbox"][0]
    unknown = {"outcome": "unknown", "originalKey": row["legacyId"]}
    assert final.settle_notification(jid, row["legacyId"], receipt=unknown)["status"] == "uncertain"
    with pytest.raises(fc.ContractError, match="outbox"):
        final.delete(jid, expected_revision=item["configRevision"])
    receipt = {"outcome": "succeeded", "originalKey": row["legacyId"], "queueId": "original-queue-id"}
    assert final.settle_notification(jid, row["legacyId"], receipt=receipt)["status"] == "acked"
    assert final.settle_notification(jid, row["legacyId"], receipt=receipt)["status"] == "acked"
    with pytest.raises(fc.ContractError):
        final.settle_notification(jid, row["legacyId"], receipt=unknown)
    for original in [jid, "legacy-job", rid, row["legacyId"], row["outboxId"],
                     before["effects"][0]["attemptId"], "effect_" + fc.digest(before["effects"][0]["effectKey"])]:
        result = final.query_original(original)
        assert len(result) == 1 and result[0]["jobId"] == jid and result[0]["effects"] == before["effects"]
    assert final.query_original("missing") == []
    assert final.delete(jid, expected_revision=item["configRevision"])["historyRetained"] is True
    with pytest.raises(KeyError):
        final.store.get(jid)
    assert final.query_original("legacy-job")[0]["archived"] is True
    assert final.history(jid)["effects"] == before["effects"]


@pytest.mark.parametrize("mutation,reason", [
    ({"runtimeMetadata": {"pinned": True}}, "pinned"),
    ({"runtimeMetadata": {"pinned": "unknown"}}, "pin_metadata_unknown"),
    ({"status": "cleanup_pending"}, "active_or_unknown"),
    ({"status": "unknown"}, "active_or_unknown"),
    ({"activeExecutions": {"old": {}}}, "active_resources"),
    ({"unknownOwnership": True}, "cleanup_or_ownership_unknown"),
    ({"finalRuntime": {"runs": {"r": {"state": "uncertain"}}}}, "active_or_unknown_run"),
    ({"finalRuntime": {"effectAdmissions": {"effect": {}}}}, "unsettled_effect"),
    ({"finalRuntime": {"outbox": {"notice": {"status": "sending"}}}}, "unacknowledged_outbox"),
    ({"finalRuntime": {"outbox": {"notice": {"status": "uncertain"}}}}, "unacknowledged_outbox"),
])
def test_deletion_gate_is_kind_independent(mutation, reason):
    for kind in ("background-process", "session-message", "scheduled-task", "main-lifecycle"):
        assert reason in FinalRuntime.deletion_blockers({"kind": kind, "status": "completed", **mutation})


def test_retention_rechecks_pins_and_activity_even_when_config_revision_unchanged(h, monkeypatch):
    item, _ = finished(h)
    final = h.service.final
    jid = item["id"]
    row = final.history(jid)["outbox"][0]
    final.settle_notification(jid, row["legacyId"], receipt={"outcome": "succeeded"})
    final.set_pin(jid, True, expected_revision=0)
    result = final.retention(retention_days=1, at=10**11)
    assert not result["deleted"] and "pinned" in result["retained"][jid]
    final.set_pin(jid, False, expected_revision=1)
    remove = final.delete
    def race(job_id, **kwargs):
        final.set_pin(job_id, True, expected_revision=2)
        return remove(job_id, **kwargs)
    monkeypatch.setattr(final, "delete", race)
    result = final.retention(retention_days=1, at=10**11)
    assert not result["deleted"] and "changed" in result["retained"][jid]
    monkeypatch.setattr(final, "delete", remove)
    final.set_pin(jid, False, expected_revision=3)
    assert final.retention(retention_days=1, at=10**11)["deleted"] == [jid]


def active_effect(h):
    item = enabled(h)
    rid = launch(h, item)["runId"]
    final = h.service.final
    def begin(job):
        final.ledger.transition_run(job, final.root, rid, "running")
        attempt = final.ledger.begin_attempt(job, final.root, rid, "s0")
        key = final.ledger.admit_effect(job, final.root, attempt["attemptId"], "original-session")
        return copy.deepcopy(attempt), key
    attempt, key = final.store.mutate(item["id"], begin)
    return item["id"], attempt, key


@pytest.mark.parametrize("outcome,proof,expected", [("unknown", False, "uncertain"), ("rejected", True, "confirmed"), ("succeeded", True, "uncertain")])
def test_cancel_resource_keeps_identity_fence_and_never_resends_unknown(h, outcome, proof, expected):
    jid, attempt, key = active_effect(h)
    calls = []
    class Executor:
        async def cancel_effect(self, **kwargs):
            calls.append(kwargs)
            return {"effectKey": key, "outcome": outcome, "receipt": {"cleanupProof": {"confirmed": proof}}}
    def cancel(epoch=attempt["ownerEpoch"]):
        return asyncio.run(h.service.final.cancel_resource(jid, attempt["attemptId"], executor=Executor(),
            expected_epoch=epoch, expected_generation=attempt["generation"]))
    with pytest.raises(fc.ContractError):
        cancel(99)
    assert not calls
    assert cancel()["status"] == expected
    assert calls == [{"attempt": attempt, "effect_key": key}]
    with pytest.raises(fc.ContractError, match="do not resend"):
        cancel()
    assert len(calls) == 1
    assert h.service.final.query_original(jid)[0]["pendingEffects"][0]["effectKey"] == key


def test_canonical_cancel_adapter_uses_original_execution_and_attempt(tmp_path):
    from packages.jobs.independent_executor import ExecutionStore, effect_id
    from tests.test_job_final_independent_execution import attempt, key, process_plan
    store = ExecutionStore(tmp_path)
    store.admit(job_id="job_original", attempt=attempt(), effect_key=key(), frozen_plan=process_plan(tmp_path))
    calls = []
    def cancel(identity, original):
        calls.append((identity, original))
        return store.cancel(identity, original)
    executor = IndependentExecutor(job_id="job_original", client=SimpleNamespace(get_execution=store.get, cancel_execution=cancel))
    receipt = asyncio.run(executor.cancel_effect(attempt=attempt(), effect_key=key()))
    assert calls == [(effect_id(key()), attempt())]
    assert receipt["effectKey"] == key() and receipt["outcome"] == "rejected"


def test_old_action_delete_is_fenced_before_unlink(h):
    from packages.jobs.action_runtime import Runtime
    runtime = Runtime(legacy._root(), SimpleNamespace(root=h.root / "actions"), None)
    old_record(legacy._root())
    h.service.owner.claim(0)
    try:
        with pytest.raises(fc.ContractError):
            runtime.delete("job_original", 1)
        assert legacy.get("job_original")
    finally:
        h.service.owner.release()


def test_legacy_history_remains_read_only_and_preserves_original_dispatch_key(tmp_path):
    root = tmp_path / "background_jobs"
    root.mkdir()
    path = root / "runs.jsonl"
    rows = [{"run_id": "old-run", "task_id": "old-task", "dispatch_key": "original-dispatch", "status": "unknown"},
            {"run_id": "other-run", "task_id": "other-task", "dispatch_key": "other"}]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    before = path.read_bytes()
    owner = OwnerFence(tmp_path)
    owner.claim(0)
    try:
        assert FinalRuntime.legacy_history(root, task_id="old-task") == [rows[0]]
        assert legacy.list_run_records(registry_root=root) == rows[::-1]
        assert path.read_bytes() == before
    finally:
        owner.release()


def test_bridge_legacy_cancel_and_ack_refused_before_remote_request(h, monkeypatch):
    from packages.jobs import pan_bridge
    calls = []
    client = SimpleNamespace(cancel=lambda *args: calls.append(args), ack=lambda *args: calls.append(args))
    monkeypatch.setattr(pan_bridge, "configured_client", lambda: client)
    h.service.owner.claim(0)
    try:
        for method in ("cancel", "ack"):
            with pytest.raises(fc.ContractError):
                pan_bridge.request(method, "original-id")
        assert not calls
    finally:
        h.service.owner.release()


def test_log_retention_cannot_collect_pinned_resources(tmp_path):
    root = tmp_path / "background_jobs"
    old_record(root)
    path = root / "logs/job_original.log"
    path.write_text("owned fixture", encoding="utf-8")
    os.utime(path, (1, 1))
    legacy._update("job_original", {"logPath": str(path)}, registry_root=root)
    pins.set_pin(root, "job_original", True)
    assert legacy.cleanup_job_log_files(registry_root=root, retention_days=1, now=1_000_000)["deleted"] == 0
    assert path.exists()
    pins.set_pin(root, "job_original", False)
    assert legacy.cleanup_job_log_files(registry_root=root, retention_days=1, now=1_000_000)["deleted"] == 1


def test_notification_fanout_requires_original_recipient_row(h):
    item, _ = finished(h)
    final = h.service.final
    jid = item["id"]
    row = final.history(jid)["outbox"][0]
    second = {**copy.deepcopy(row), "outboxId": "outbox_second", "recipientId": "original-recipient", "channel": "session"}
    final.store.mutate(jid, lambda job: aggregate(job, final.root)["outbox"].update({second["outboxId"]: second}))
    with pytest.raises(fc.ContractError, match="recipient"):
        final.settle_notification(jid, row["legacyId"], receipt={"outcome": "succeeded"})
    final.settle_notification(jid, row["legacyId"], outbox_id=row["outboxId"], receipt={"outcome": "succeeded"})
    assert [r["status"] for r in final.history(jid)["outbox"]] == ["acked", "pending"]
    with pytest.raises(fc.ContractError):
        final.settle_notification(jid, row["legacyId"], outbox_id=second["outboxId"],
                                  receipt={"outcome": "succeeded", "originalKey": "wrong-key"})


def test_registry_lock_crash_releases_same_original_lock(tmp_path):
    root = tmp_path / "registry"
    old_record(root)
    script = """
import os,sys
from packages.jobs.final_runtime.coordinator import registry_lock
with registry_lock(sys.argv[1], 'job:job_original'):
    os._exit(77)
"""
    child = subprocess.run([sys.executable, "-c", script, str(root)], capture_output=True, timeout=30)
    assert child.returncode == 77, child.stderr.decode()
    JsonRegistryStore(root).compare_and_swap("job_original", 1, lambda job: job.update(configRevision=2))
    assert legacy.get("job_original", root)["configRevision"] == 2


def test_owner_activated_ports_and_stale_service_writes(h):
    item = enabled(h)
    launch(h, item)
    final = h.service.final
    h.service.owner.claim(0)
    h.service.owner.set_state("active")
    try:
        final.register_original(item["id"], "old-id", source_digest="a" * 64,
            source_path="reviewed-source.json", expected_revision=item["configRevision"])
        assert final.query_original("old-id")[0]["jobId"] == item["id"]
        result = final.handoff(item["id"], "owned-test-session", "new", expected_revision=item["configRevision"], request_key="owner-handoff")
        assert result["changed"] is True
        asyncio.run(h.service.dispatch_once())
        row = final.history(item["id"])["outbox"][0]
        assert final.settle_notification(item["id"], row["legacyId"], receipt={"outcome": "succeeded"})["status"] == "acked"
    finally:
        h.service.owner.release()
    with pytest.raises(fc.ContractError):
        final.handoff(item["id"], "new", "again", expected_revision=result["configRevision"], request_key="stale-owner")


def test_parallel_older_finalization_cannot_replace_latest_run_projection(tmp_path, monkeypatch):
    from tests.test_job_action_chain import _runtime, _config, _steps
    from packages.actions import executor
    async def check():
        runtime = await _runtime(tmp_path)
        completed = threading.Event()
        failed_run = None
        finalize = runtime.finalize
        def ordered_finish(jid, eid, status, audit=None):
            if status == "failed":
                assert completed.wait(20), "newer Run did not finalize"
            result = finalize(jid, eid, status, audit)
            if status == "completed":
                completed.set()
            return result
        async def execute(action, args, event, *unused):
            return {"ok": not (action["id"] == "two" and event["id"].split(":")[0] == failed_run)}
        monkeypatch.setattr(runtime, "finalize", ordered_finish)
        monkeypatch.setattr(executor, "execute_dynamic", execute)
        try:
            job = runtime.create(_config(_steps(), trigger={"kind": "manual"}, overlap={"mode": "parallel", "parallelLimit": 2}), True)
            first = await runtime.launch(job["jobId"], 1)
            failed_run = first["execution"]["runId"]
            second = await runtime.launch(job["jobId"], 1)
            await asyncio.gather(*runtime.tasks.values())
            current = runtime.get(job["jobId"])
            assert current["runSummaries"][failed_run]["status"] == "failed"
            assert current["lastExecution"]["runId"] == second["execution"]["runId"]
            assert current["lastStatus"] == "completed"
        finally:
            completed.set()
            await runtime.close()
    asyncio.run(check())
