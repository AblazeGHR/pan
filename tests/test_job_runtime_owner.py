"""R4 / FM05 / FR02 / FR05: fixture-only owner activation and recovery."""
import asyncio
import copy
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.core import background_jobs as legacy
from packages.jobs import final_contracts as fc
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_service import FinalService, definition
from packages.jobs.runtime_owner import OwnerFence
from packages.jobs.independent_service import JobStore, ServiceError
from packages.automation.service import Service as Automation
from packages.automation.store import Ledger
from packages.scheduler.providers import TimeProvider
from scripts.job_final_migration import apply as migration
from tests.test_job_final_http_service import h, enabled, launch, USER
from tests.test_job_final_migration import _message_job, _scheduled_job, _write_json
from tests.test_job_final_migration_apply import context_for, commit


class Executor:
    def __init__(self):
        self.calls = []

    async def execute(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return {"outcome": "succeeded", "receipt": {"fixture": True}}


def test_owner_epoch_cas_competition_and_stale_writers(tmp_path):
    def compete(_):
        owner = OwnerFence(tmp_path)
        try:
            owner.claim(0)
            owner.set_state("active")
            return owner
        except fc.ContractError:
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(compete, range(2)))
    winner = next(x for x in results if x is not None)
    assert sum(x is not None for x in results) == 1
    first = winner.token
    with pytest.raises(fc.ContractError, match="fence"):
        with OwnerFence(tmp_path).writer():
            pytest.fail("legacy write admitted")
    winner.release()
    replacement = OwnerFence(tmp_path)
    replacement.claim(1)
    assert replacement.token[0] == 2 and replacement.token != first
    with pytest.raises(fc.ContractError):
        with winner.writer(canonical=True):
            pytest.fail("stale writer admitted")
    replacement.release()
    with pytest.raises(fc.ContractError):
        OwnerFence(tmp_path).claim(1)


def test_process_crash_releases_lock_but_advances_durable_epoch(tmp_path):
    script = "from packages.jobs.runtime_owner import OwnerFence; import os,sys; o=OwnerFence(sys.argv[1]); o.claim(0); os._exit(77)"
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True)
    assert child.returncode == 77, child.stderr
    owner = OwnerFence(tmp_path)
    assert owner.status()["epoch"] == 1
    owner.claim(1)
    assert owner.token[0] == 2
    owner.release()


def test_pass_in_flight_and_abandoned_reservation_fail_closed(tmp_path):
    owner = OwnerFence(tmp_path)
    with owner.legacy_pass():
        with pytest.raises(fc.ContractError, match="Legacy pass"):
            OwnerFence(tmp_path).claim(0)
    owner.claim(0)
    owner.release()


def test_old_stores_are_readable_and_all_new_writes_refused(h, tmp_path):
    record = {"jobId": "job_fixture", "kind": "session-message", "status": "pending", "text": "old"}
    legacy._create(record)
    ledger = Ledger(tmp_path / "automation")
    ledger.mutate(lambda state: state["runs"].update({"old": {"status": "running"}}))
    independent = JobStore(str(tmp_path / "independent"))
    before = ledger.path.read_bytes()
    async def check():
        await h.service.start()
        try:
            with pytest.raises(fc.ContractError):
                legacy._create({"jobId": "job_new", "kind": "session-message"})
            with pytest.raises(fc.ContractError):
                legacy._update("job_fixture", {"text": "changed"})
            with pytest.raises(fc.ContractError):
                ledger.mutate(lambda state: state.clear())
            with pytest.raises(ServiceError) as exc:
                independent.create({})
            assert exc.value.status == 410
            with pytest.raises(ServiceError):
                independent.retry("job_missing")
            with pytest.raises(fc.ContractError):
                await legacy.run_due_message_jobs()
            with pytest.raises(fc.ContractError):
                await legacy.run_due_scheduled_tasks()
            with pytest.raises(fc.ContractError):
                await legacy.recover_notifications()
            assert legacy.get("job_fixture")["text"] == "old"
            assert ledger.read()["runs"]["old"]["status"] == "running"
            assert ledger.path.read_bytes() == before
            assert independent.list() == {"jobs": []}
        finally:
            await h.service.close()
    asyncio.run(check())


def migration_fixture(tmp_path, source=None):
    profile = tmp_path / "profile"
    source = source or _message_job()
    _write_json(profile / "background_jobs" / "jobs" / (source["jobId"] + ".json"), source)
    service = FinalService(profile / "job-final", CanonicalUserAuth(profile / "auth"), executor=Executor())
    context = context_for(profile)
    context["profileId"] = service.profile_id
    provider = TimeProvider()
    for record in context["records"].values():
        record["scheduler"].update(pausePolicy="advance", dstPolicy="strict", providerRef={"type": "time", "id": provider.provider_id,
            "releaseDigest": provider.release_digest, "generation": provider.generation},
            configSchemaRef=provider.config_schema_ref, requiredCapabilities=list(provider.capabilities))
    for binding in context["actions"].values():
        service.publish_action(binding["object"]["id"], definition("action", binding["object"]))
    path = tmp_path / "canonical.json"
    commit(profile, path, context, mark_read_only=True)
    bundle = migration.read_bundle(path)
    spec = {"path": str(path), "expected_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            "expected_plan_digest": bundle["planDigest"], "active_ids": []}
    return service, spec, bundle


def test_offline_bundle_activation_explicit_states_and_idempotent_restart(tmp_path):
    service, spec, bundle = migration_fixture(tmp_path)
    ids = {record["kind"]: record["object"]["id"] for record in bundle["canonical"]}
    spec["active_ids"] = [ids["job"]]
    raw = Path(spec["path"]).read_bytes()
    async def check():
        await service.start(activation=spec)
        try:
            assert service.get("job", ids["job"], actor=USER)["state"] == "enabled"
            assert service.get("trigger", ids["trigger"], actor=USER)["state"] == "disabled"
            assert service.creators.get(ids["event_creator"])["state"] == "disabled"
            assert service.creators.get(ids["event_creator"])["ownerEpoch"] >= service.owner.token[0]
            assert service.owner.status()["state"] == "active"
        finally:
            await service.close()
        restored = FinalService(service.root, service.auth)
        await restored.start(activation=spec)
        try:
            assert restored.owner.token[0] == 2
            assert len(restored.list("job", actor=USER)["items"]) == 1
        finally:
            await restored.close()
        assert Path(spec["path"]).read_bytes() == raw
    asyncio.run(check())


@pytest.mark.parametrize("corruption", ["hash", "plan", "bundle", "profile", "references", "active", "readonly", "cursor"])
def test_bad_activation_rolls_back_graph_and_never_reopens_legacy(tmp_path, corruption):
    service, spec, bundle = migration_fixture(tmp_path)
    if corruption == "hash":
        spec["expected_hash"] = "0" * 64
    elif corruption == "plan":
        spec["expected_plan_digest"] = "0" * 64
    else:
        if corruption == "bundle":
            bundle["bundleDigest"] = "0" * 64
        elif corruption == "profile":
            bundle["canonical"][0]["object"]["profileId"] = "foreign_profile"
        elif corruption == "references":
            next(r for r in bundle["canonical"] if r["kind"] == "job")["object"]["steps"][0]["actionRef"]["digest"] = "0" * 64
        elif corruption == "active":
            spec["active_ids"] = ["job_missing"]
        elif corruption == "readonly":
            bundle["entries"][0]["oldRecordReadOnly"] = False
        elif corruption == "cursor":
            bundle["entries"][0]["cursor"]["nextFireAt"] = "2030-01-01T00:00:00"
        if corruption != "bundle":
            bundle = migration._seal(bundle)
        Path(spec["path"]).write_text(fc.canonical_json(bundle), encoding="utf-8")
        spec["expected_hash"] = hashlib.sha256(Path(spec["path"]).read_bytes()).hexdigest()
    with pytest.raises(fc.ContractError):
        asyncio.run(service.start(activation=spec))
    assert service.task is None
    assert service.owner.status()["state"] == "failed"
    assert service.list("job", actor=USER)["items"] == []
    assert service.creators.list() == []
    with pytest.raises(fc.ContractError):
        with OwnerFence(service.root.parent).writer():
            pytest.fail("failure reopened a legacy writer")


@pytest.mark.parametrize("kind", ["interval", "cron"])
def test_activation_adopts_reviewed_next_fire_without_historical_replay(tmp_path, kind):
    entry = {"id": "rule_fixture", "kind": kind, "timezone": "UTC", "misfirePolicy": "fire_now",
             "enabled": True, "nextFireAt": "2030-01-01T00:00:00+00:00", "lastFireAt": None}
    if kind == "interval":
        entry.update(intervalSec=3600, anchor="2025-01-01T00:00:00+00:00")
    else:
        entry["cron"] = "0 * * * *"
    source = _scheduled_job([entry])
    source["nextFireAt"] = entry["nextFireAt"]
    service, spec, bundle = migration_fixture(tmp_path, source)
    spec["active_ids"] = [r["object"]["id"] for r in bundle["canonical"]]
    cid = next(r["object"]["id"] for r in bundle["canonical"] if r["kind"] == "event_creator")
    from packages.scheduler.providers import instant
    service.creators.clock = lambda: instant("2029-12-31T23:00:00Z")
    async def check():
        await service.start(activation=spec)
        try:
            await service.tick()
            assert service.executor.calls == []
            assert service.events.stats()["events"] == 0
            item = service.creators.get(cid)
            assert item["cursor"]["rules"]["rule_fixture"]["nextFireAt"] == "2030-01-01T00:00:00Z"
            assert item["migrationCursor"] == bundle["entries"][0]["cursor"]
            service.creators.clock = lambda: instant("2030-01-01T00:00:00Z")
            await service.tick()
            await service.tick()
            assert len(service.executor.calls) == 1
            assert service.events.stats()["events"] == 1
        finally:
            await service.close()
    asyncio.run(check())


def test_single_execution_single_outbox_delivery_and_restart_no_replay(h):
    item = enabled(h)
    receipt = launch(h, item)
    delivered = []
    async def sender(row):
        delivered.append(row["outboxId"])
        return {"outcome": "succeeded"}
    h.service.outbox_sender = sender
    async def check():
        await h.service.start()
        try:
            await h.service.tick()
            await h.service.tick()
            assert h.service.get_run(receipt["runId"], actor=USER)["state"] == "completed"
            assert len(h.provider.calls) == len(delivered) == 1
            with h.service.transaction(readonly=True) as db:
                job = h.service._get(db, "job", item["id"])
                assert len(job["finalRuntime"]["outbox"]) == 1
                assert next(iter(job["finalRuntime"]["attempts"].values()))["ownerEpoch"] == 1
        finally:
            await h.service.close()
        restored = FinalService(h.root, h.automation.auth, executor=h.service.executor)
        restored.outbox_sender = sender
        await restored.start()
        try:
            await restored.tick()
            assert len(h.provider.calls) == len(delivered) == 1
            with pytest.raises(fc.ContractError):
                h.service.create_run(item["id"], {"expectedRevision": 1, "requestKey": "stale", "input": {}}, actor=USER)
        finally:
            await restored.close()
    asyncio.run(check())


def test_restart_lost_attempt_and_notification_remain_uncertain_without_replay(h):
    item = enabled(h)
    rid = launch(h, item)["runId"]
    with h.service.transaction() as db:
        job = h.service._get(db, "job", item["id"])
        ledger = h.service.final.ledger
        ledger.start(job, h.root, rid)
        attempt = ledger.begin_attempt(job, h.root, rid, "s0")
        ledger.admit_effect(job, h.root, attempt["attemptId"], h.action["id"])
        ledger.ensure_outbox(job, h.root, rid, "lost", {"status": "running"})
        next(iter(job["finalRuntime"]["outbox"].values()))["status"] = "sending"
        h.service._save(db, "job", item["id"], job)
    async def check():
        await h.service.start()
        try:
            await h.service.tick()
            assert h.service.get_run(rid, actor=USER)["state"] == "uncertain"
            assert h.provider.calls == []
            with h.service.transaction(readonly=True) as db:
                state = h.service._get(db, "job", item["id"])["finalRuntime"]
                assert state["attempts"][attempt["attemptId"]]["state"] == "uncertain"
                assert any(row["status"] == "uncertain" for row in state["outbox"].values())
        finally:
            await h.service.close()
    asyncio.run(check())


def test_dual_runtime_start_only_one_enters_active(h):
    contender = FinalService(h.root, h.automation.auth)
    async def check():
        results = await asyncio.gather(h.service.start(expected_epoch=0), contender.start(expected_epoch=0), return_exceptions=True)
        try:
            assert sum(result is None for result in results) == 1
            assert sum(isinstance(result, fc.ContractError) for result in results) == 1
            assert sum(service.task is not None for service in (h.service, contender)) == 1
        finally:
            await h.service.close()
            await contender.close()
    asyncio.run(check())


def test_creator_event_single_admission_execution_notification_and_restart(tmp_path):
    service, spec, bundle = migration_fixture(tmp_path)
    spec["active_ids"] = [record["object"]["id"] for record in bundle["canonical"]]
    from packages.scheduler.providers import instant
    service.creators.clock = lambda: instant("2030-01-01T00:00:00+00:00")
    delivered = []
    async def sender(row):
        delivered.append(row["outboxId"])
        return {"outcome": "succeeded"}
    service.outbox_sender = sender
    job_id = next(r["object"]["id"] for r in bundle["canonical"] if r["kind"] == "job")
    async def check():
        await service.start(activation=spec)
        try:
            await service.tick()
            await service.tick()
            assert len(service.executor.calls) == 1
            assert len(delivered) == 1
            assert service.events.stats()["events"] == 1
            assert len(service.runs(job_id, actor=USER)["items"]) == 1
        finally:
            await service.close()
        restored = FinalService(service.root, service.auth, executor=service.executor)
        restored.creators.clock = service.creators.clock
        restored.outbox_sender = sender
        await restored.start(activation=spec)
        try:
            await restored.tick()
            assert len(service.executor.calls) == len(delivered) == 1
            assert restored.events.stats()["events"] == 1
        finally:
            await restored.close()
    asyncio.run(check())


def test_failed_activation_cannot_be_bypassed_by_omitting_bundle(tmp_path):
    service, spec, _ = migration_fixture(tmp_path)
    spec["expected_hash"] = "0" * 64
    with pytest.raises(fc.ContractError):
        asyncio.run(service.start(activation=spec))
    restored = FinalService(service.root, service.auth)
    with pytest.raises(fc.ContractError, match="original reviewed bundle"):
        asyncio.run(restored.start())
    assert restored.owner.status()["state"] == "failed"


def test_canonical_automation_admission_keeps_old_trigger_runs_byte_identical(h):
    item = enabled(h)
    trigger = h.service.write("trigger", None, {"expectedRevision": 0, "requestKey": "trigger",
        "definition": {"name": "fixture", "eventTypes": ["fixture.event"],
            "creatorSelector": {"mode": "registeredType", "providerType": "fixture"},
            "targetJobId": item["id"]}}, actor=USER)["result"]["object"]
    h.service.control("trigger", trigger["id"], {"operation": "enable", "expectedRevision": trigger["revision"],
        "expectedStateRevision": trigger["stateRevision"], "requestKey": "enable-trigger", "parameters": {}}, actor=USER)
    ledger = h.automation.ledger
    ledger.mutate(lambda state: state["runs"].update({"historical": {"status": "running"}}))
    before = ledger.path.read_bytes()
    async def check():
        await h.service.start()
        try:
            event = {"id": "event_fixture", "type": "fixture.event", "schemaVersion": 1,
                     "source": {"type": "fixture", "pluginId": "fixture", "instanceId": "fixture"}, "payload": {},
                     "occurredAt": "2026-10-26T00:00:00Z"}
            target = h.service.targets(event)[0]
            first = await h.service.admission.admit(event, target)
            second = await h.service.admission.admit(event, target)
            assert first["runId"] == second["runId"]
            await h.service.dispatch_once()
            assert len(h.provider.calls) == 1
            assert ledger.path.read_bytes() == before
        finally:
            await h.service.close()
    asyncio.run(check())


def test_restart_queries_wp05b_original_effect_and_never_resubmits(h):
    from packages.jobs.independent_executor import IndependentExecutor, effect_id
    item = enabled(h)
    rid = launch(h, item)["runId"]
    with h.service.transaction() as db:
        job = h.service._get(db, "job", item["id"])
        ledger = h.service.final.ledger
        ledger.start(job, h.root, rid)
        attempt = ledger.begin_attempt(job, h.root, rid, "s0")
        key = ledger.admit_effect(job, h.root, attempt["attemptId"], h.action["id"])
        h.service._save(db, "job", item["id"], job)
    queries = []
    class Client:
        def get_execution(self, identity):
            queries.append(identity)
            return {"jobId": item["id"], "attempt": attempt, "effectKey": key,
                    "effectId": effect_id(key), "result": {"effectKey": key,
                    "outcome": "succeeded", "receipt": {"persisted": True}}}
        def submit_execution(self, request):
            pytest.fail("recovery resubmitted a canonical effect")
    h.service.executor.independent_executor = IndependentExecutor(job_id=item["id"], client=Client())
    async def check():
        await h.service.start()
        try:
            assert h.service.get_run(rid, actor=USER)["state"] == "completed"
            assert queries == [effect_id(key)]
            assert h.provider.calls == []
        finally:
            await h.service.close()
    asyncio.run(check())


def test_abandoned_legacy_runner_reservation_blocks_takeover(tmp_path):
    script = "from packages.jobs.runtime_owner import OwnerFence; import os,sys; o=OwnerFence(sys.argv[1]); c=o.legacy_pass(); c.__enter__(); os._exit(77)"
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True)
    assert child.returncode == 77, child.stderr
    with pytest.raises(fc.ContractError, match="unknown"):
        OwnerFence(tmp_path).claim(0)
    assert OwnerFence(tmp_path).status() is None


def test_old_canonical_store_handles_cannot_write_after_restart(h):
    from packages.scheduler.creators import CreatorStore, CanonicalEventStore
    unbound_creators = CreatorStore(h.root)
    unbound_events = CanonicalEventStore(h.root)
    async def check():
        await h.service.start()
        try:
            assert unbound_creators.list() == []
            with pytest.raises(fc.ContractError):
                with unbound_creators.connection():
                    pytest.fail("unbound Creator writer bypassed epoch")
            with pytest.raises(fc.ContractError):
                with unbound_events.connection():
                    pytest.fail("unbound Event writer bypassed epoch")
        finally:
            await h.service.close()
    asyncio.run(check())


def test_override_store_in_another_profile_is_fail_closed(h, tmp_path):
    h.service.legacy_roots = [tmp_path / "other-profile" / "background_jobs"]
    with pytest.raises(fc.ContractError, match="different owner profile"):
        asyncio.run(h.service.start())
    assert h.service.owner.status()["state"] == "failed"
    assert h.service.task is None


def test_bound_automation_starts_admission_only_and_preserves_old_runs(h, monkeypatch):
    monkeypatch.setattr(h.automation.worker, "set_running_observer", lambda callback: None, raising=False)
    item = enabled(h)
    trigger = h.service.write("trigger", None, {"expectedRevision": 0, "requestKey": "core-trigger",
        "definition": {"name": "core fixture", "eventTypes": ["workers.all_idle"],
            "creatorSelector": {"mode": "registeredType", "providerType": "core"},
            "targetJobId": item["id"]}}, actor=USER)["result"]["object"]
    h.service.control("trigger", trigger["id"], {"operation": "enable", "expectedRevision": trigger["revision"],
        "expectedStateRevision": trigger["stateRevision"], "requestKey": "core-enable", "parameters": {}}, actor=USER)
    ledger = h.automation.ledger
    old_event = {"id": "old-event", "type": "workers.all_idle", "source": {"type": "core", "id": "workers"}, "payload": {"runningCount": 0}}
    ledger.mutate(lambda state: state["runs"].update({"historical": {"event": old_event, "status": "running"}}))
    before = ledger.path.read_bytes()
    async def check():
        await h.service.start()
        await h.automation.start()
        try:
            assert h.automation.job_runtime is None
            assert h.automation.engine_started is False
            event = h.automation.bus.prepare(h.automation.core, "workers.all_idle", {"runningCount": 0}, "new-fixture-event")
            await h.automation.accept(event, h.automation.core)
            row = h.automation.events.lookup("new-fixture-event")
            await h.automation.deliver(row)
            await h.service.dispatch_once()
            assert len(h.provider.calls) == 1
            assert ledger.path.read_bytes() == before
        finally:
            await h.automation.close()
            await h.service.close()
    asyncio.run(check())


def test_notification_unknown_is_never_sent_again(h):
    item = enabled(h)
    launch(h, item)
    sent = []
    async def sender(row):
        sent.append(row["outboxId"])
        return {"outcome": "unknown"}
    h.service.outbox_sender = sender
    async def check():
        await h.service.start()
        try:
            await h.service.tick()
            await h.service.tick()
            assert len(sent) == 1
            with h.service.transaction(readonly=True) as db:
                rows = h.service._get(db, "job", item["id"])["finalRuntime"]["outbox"]
                assert next(iter(rows.values()))["status"] == "uncertain"
        finally:
            await h.service.close()
    asyncio.run(check())


def test_lifespan_fences_before_any_startup_and_adopts_same_epoch(h, monkeypatch):
    from contextlib import asynccontextmanager
    from packages.web import server
    observed = []
    @asynccontextmanager
    async def isolated_startup(app, owner):
        observed.append(owner.status())
        service = FinalService(h.root, h.automation.auth, owner=owner)
        await service.start()
        try:
            assert service.owner.token[0] == 1
            with pytest.raises(fc.ContractError):
                legacy._create({"jobId": "job_during_startup"})
            yield
        finally:
            await service.close()
    monkeypatch.setattr(server, "_owned_lifespan", isolated_startup)
    async def check():
        async with server.lifespan(h.app):
            assert observed[0]["epoch"] == 1
            assert observed[0]["state"] == "activating"
        assert OwnerFence(h.root.parent).status()["state"] == "closed"
    asyncio.run(check())
