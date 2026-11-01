"""R6-I host continuation: reviewed sources keep identity and run after restart."""
import asyncio
import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from packages.automation.event_store import EventStore
from packages.jobs import final_contracts as fc
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_runtime.executor import TypedExecutor
from packages.jobs.final_service import FinalService, object_ref
from packages.jobs.runtime_owner import OwnerFence
from packages.scheduler.source_history import SourceHistory
from tests.test_automation_canonical import USER, SessionProvider, job_def
from tests.test_job_final_creators import at
from tests.test_scheduler_pending_adoption import PHASES, setup_case


def host(root, owner, provider):
    service = FinalService(root / "canonical", CanonicalUserAuth(root / "auth"),
                           executor=TypedExecutor(session_provider=provider), owner=owner)
    service._initialize_schema()
    service._install_product_actions()
    service.creators.clock = lambda: at(60)
    service.events.clock = lambda: at(60)
    return service


def enabled(service, kind, value, key):
    item = service.write(kind, None, {"definition": value, "expectedRevision": 0, "requestKey": key}, actor=USER)["result"]["object"]
    return enable(service, kind, item, key + ":enable")


def enable(service, kind, item, key):
    return service.control(kind, item["id"], {"operation": "enable",
        "expectedRevision": item.get("configRevision", item.get("revision")),
        "expectedStateRevision": item["stateRevision"], "requestKey": key, "parameters": {}},
        actor=USER)["result"]["object"]


def graph(service, cid):
    from packages.core import session
    session.save(session.Session(id="owned-session", name="Owned source migration target"))
    action = next(a for a in service.actions(actor=USER)["items"] if a["id"] == "product_session_message")
    job = enabled(service, "job", job_def(SimpleNamespace(action=object_ref("action", action))), "successor-job")
    trigger = enabled(service, "trigger", {"name": "reviewed successor", "targetJobId": job["id"],
        "eventTypes": ["scheduler.time.v1", "scheduler.time.v2"],
        "creatorSelector": {"mode": "ids", "ids": [cid]}}, "successor-trigger")
    return job, trigger


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("cold_restart", [False, True])
def test_explicit_host_adoption_continues_same_grid_and_effect_keys(tmp_path, phase, cold_restart):
    sources, pending, legacy, snapshot, owner, _, _, _ = setup_case(tmp_path, phase, effects=True)
    source_bytes = sources.path.read_bytes()
    provider = SessionProvider()
    service = host(tmp_path, owner, provider)
    migration = service.scheduler_migration(legacy)
    sid, eid = snapshot["sources"][0]["id"], pending["event"]["id"]
    try:
        reader = SourceHistory(tmp_path / "jobs")
        assert reader.snapshot() == snapshot
        receipt = migration.adopt(import_key="history", source_id=sid, timezone_name="UTC", actor=USER)
        cid = receipt["creatorId"]
        assert migration.status(cid, actor=USER)["identity"]["driveId"] == eid
        item = service.creators.get(cid)
        with pytest.raises(fc.ContractError, match="settle"):
            enable(service, "event_creator", item, "premature-enable")
        job, trigger = graph(service, cid)
        with pytest.raises(fc.ContractError, match="Complete"):
            migration.bind_successors(cid, trigger_map={}, job_map={}, expected_revision=1, actor=USER)
        migration.bind_successors(cid, trigger_map={"old-trigger": trigger["id"]},
            job_map={"old-job": job["id"]}, expected_revision=1, actor=USER)
        assert migration.status(cid, actor=USER)["sourceLinks"] == snapshot["links"]
        assert migration.status(cid, actor=USER)["sourceRequests"] == snapshot["requests"]
        asyncio.run(service.tick())
        assert provider.calls == [] and service.final.runs(job["id"]) == []
        if phase == "before-admit":
            with pytest.raises(fc.ContractError, match="explicit user"):
                asyncio.run(migration.publish_pending(cid, actor={"type": "agent", "id": "untrusted", "revision": 1}))
            asyncio.run(migration.publish_pending(cid, actor=USER))
            assert service.events.get(eid)["targets"][0]["id"] == trigger["id"]
        else:
            with pytest.raises(fc.ContractError, match="committed"):
                asyncio.run(migration.publish_pending(cid, actor=USER))
            assert service.events.get(eid)["targets"] == []
            assert migration.status(cid, actor=USER)["legacyEvidence"]["subscriptions"]["old-trigger"]["status"] == "uncertain"
        settled = migration.settle(cid, actor=USER)
        assert settled["publicationSettled"] and settled["cursorAdvanced"]
        assert service.creators.get(cid)["producedCount"] == 1
        asyncio.run(service.tick())
        assert len(provider.calls) == int(phase == "before-admit")
        enable(service, "event_creator", service.creators.get(cid), "future-enable")
        # Close the in-memory host and original owner. New host has no copied
        # Runtime/subscription state; cursor, authority, graph and receipts are on disk.
        if cold_restart:
            owner.release()
            replacement = OwnerFence(tmp_path)
            replacement.claim(1)
            replacement.set_state("active")
            service = host(tmp_path, replacement, provider)
            service.recover()
            migration = service.scheduler_migration(EventStore(tmp_path / "automation"))
        assert migration.adopt(import_key="history", source_id=sid, timezone_name="UTC", actor=USER)["publicationSettled"]
        for point in (120, 120, 180, 180):
            service.creators.clock = lambda point=point: at(point)
            service.events.clock = lambda point=point: at(point)
            asyncio.run(service.tick())
        current = service.creators.get(cid)
        assert current["producedCount"] == 3 and len(current["drives"]) == 3
        assert current["cursor"]["rules"]["legacy"]["nextFireAt"] == at(240)
        assert current["pendingPublication"] is None and current["lastPublication"]["eventStoreReleased"]
        assert service.events.stats() == {"events": 3, "pins": 0}
        expected_runs = 2 + int(phase == "before-admit")
        runs = service.final.runs(job["id"])
        assert len(runs) == expected_runs and len(provider.calls) == expected_runs
        assert all(run["state"] == "completed" for run in runs)
        assert len({fc.digest(key) for _, key in provider.calls}) == expected_runs
        assert {run["cause"]["eventId"] for run in runs} == {
            row["eventId"] for row in service.events.page() if phase == "before-admit" or row["eventId"] != eid}
        assert sources.path.read_bytes() == source_bytes and reader.snapshot() == snapshot
        assert service.creators.legacy_history("history")["snapshot"] == snapshot
        assert migration.status(cid, actor=USER)["identity"] == receipt["identity"]
    finally:
        service.owner.release()


def test_host_rechecks_successor_revision_and_cannot_activate_uncertain_publication(tmp_path):
    sources, pending, legacy, snapshot, owner, _, _, _ = setup_case(tmp_path, "before-admit")
    service = host(tmp_path, owner, SessionProvider())
    migration = service.scheduler_migration(legacy)
    try:
        sid = snapshot["sources"][0]["id"]
        receipt = migration.adopt(import_key="history", source_id=sid, timezone_name="UTC", actor=USER)
        cid = receipt["creatorId"]
        job, trigger = graph(service, cid)
        migration.bind_successors(cid, trigger_map={"old-trigger": trigger["id"]},
            job_map={"old-job": job["id"]}, expected_revision=1, actor=USER)
        from packages.jobs.final_service import definition
        service.write("trigger", trigger["id"], {"definition": {**definition("trigger", trigger), "name": "changed"},
            "expectedRevision": trigger["revision"], "requestKey": "change-successor"}, actor=USER)
        with pytest.raises(fc.ContractError, match="successors changed"):
            asyncio.run(migration.publish_pending(cid, actor=USER))
        assert service.events.stats()["events"] == 0
        assert migration.settle(cid, actor=USER)["publicationState"] == "awaiting_admission"
    finally:
        owner.release()


def test_missing_original_commit_remains_uncertain_through_host_and_restart(tmp_path):
    _, _, legacy, snapshot, owner, _, _, _ = setup_case(tmp_path, "before-ack")
    legacy.path.rename(legacy.path.with_suffix(".retained"))
    service = host(tmp_path, owner, SessionProvider())
    migration = service.scheduler_migration(legacy)
    try:
        result = migration.adopt(import_key="history", source_id=snapshot["sources"][0]["id"], timezone_name="UTC", actor=USER)
        cid = result["creatorId"]
        assert result["state"] == "adopted_uncertain"
        with pytest.raises(fc.ContractError, match="uncertain"):
            asyncio.run(migration.publish_pending(cid, actor=USER))
        assert not migration.settle(cid, actor=USER)["publicationSettled"]
        with pytest.raises(fc.ContractError, match="settle"):
            enable(service, "event_creator", service.creators.get(cid), "unknown-enable")
        service.creators.clock = lambda: at(600)
        asyncio.run(service.tick())
        assert service.events.stats() == {"events": 0, "pins": 0}
        assert service.creators.get(cid)["producedCount"] == 0
    finally:
        owner.release()


def test_source_history_is_read_only_for_missing_and_pending_records(tmp_path):
    history = SourceHistory(tmp_path / "missing")
    assert history.snapshot() == {"sources": [], "links": [], "requests": []}
    assert history.list() == [] and history.get("missing") is None and history.references("missing") == []
    assert not (tmp_path / "missing").exists()
    sources, _, _, snapshot, owner, _, _, _ = setup_case(tmp_path, "before-admit")
    try:
        before = sources.path.read_bytes()
        reader = SourceHistory(tmp_path / "jobs")
        assert reader.snapshot() == snapshot and reader.list() == snapshot["sources"]
        sid = snapshot["sources"][0]["id"]
        assert reader.get(sid) == snapshot["sources"][0]
        assert reader.references(sid) == [{"triggerId": "old-trigger", "jobId": "old-job"}]
        assert "pending" not in reader.public(reader.get(sid))
        assert sources.path.read_bytes() == before
    finally:
        owner.release()


@pytest.mark.parametrize("phase", PHASES)
def test_cold_creator_publication_and_canonical_admission_recover_before_effect_boundary(tmp_path, phase):
    sources, pending, legacy, snapshot, owner, _, _, _ = setup_case(tmp_path, phase)
    original = sources.path.read_bytes()
    provider = SessionProvider()
    service = host(tmp_path, owner, provider)
    migration = service.scheduler_migration(legacy)
    try:
        cid = migration.adopt(import_key="history", source_id=snapshot["sources"][0]["id"], timezone_name="UTC", actor=USER)["creatorId"]
        job, trigger = graph(service, cid)
        migration.bind_successors(cid, trigger_map={"old-trigger": trigger["id"]}, job_map={"old-job": job["id"]}, expected_revision=1, actor=USER)
        if phase == "before-admit":
            asyncio.run(migration.publish_pending(cid, actor=USER))
        migration.settle(cid, actor=USER)
        enable(service, "event_creator", service.creators.get(cid), "continue")
        owner.release()
        replacement = OwnerFence(tmp_path)
        replacement.claim(1)
        replacement.set_state("active")
        service = host(tmp_path, replacement, provider)
        service.recover()
        service.refresh_subscriptions()
        assert asyncio.run(service.creator_runtime.tick(at=at(120))) == 1
        assert asyncio.run(service.creator_runtime.tick(at=at(120))) == 0
        item = service.creators.get(cid)
        assert item["producedCount"] == 2 and item["cursor"]["rules"]["legacy"]["nextFireAt"] == at(180)
        assert service.events.stats() == {"events": 2, "pins": 0}
        future = next(e for e in service.events.page() if e["eventId"] != pending["event"]["id"])
        targets = service.events.get(future["eventId"])["targets"]
        assert targets[0]["id"] == trigger["id"]
        first = asyncio.run(service.admit_event_async(future, targets[0]))
        second = asyncio.run(service.admit_event_async(future, targets[0]))
        assert first["runId"] == second["runId"] and second["replayed"]
        assert len(service.final.runs(job["id"])) == 1 and provider.calls == []
        assert service.final.runs(job["id"])[0]["state"] == "queued"
        assert sources.path.read_bytes() == original
    finally:
        service.owner.release()


def test_canonical_retirement_keeps_original_identity_links_requests_and_event_history(tmp_path):
    sources, pending, legacy, snapshot, owner, _, _, _ = setup_case(tmp_path, "before-ack")
    service = host(tmp_path, owner, SessionProvider())
    migration = service.scheduler_migration(legacy)
    try:
        receipt = migration.adopt(import_key="history", source_id=snapshot["sources"][0]["id"], timezone_name="UTC", actor=USER)
        cid = receipt["creatorId"]
        settled = migration.settle(cid, actor=USER)
        item = service.creators.get(cid)
        service.retire("event_creator", cid, {"operation":"retire", "parameters":{}, "expectedRevision":item["revision"],
            "expectedStateRevision":item["stateRevision"], "requestKey":"retire-source"}, actor=USER)
        assert service.get("event_creator", cid, actor=USER)["state"] == "retired"
        assert migration.status(cid, actor=USER) == settled
        assert SourceHistory(tmp_path / "jobs").snapshot() == snapshot
        assert service.events.get(pending["event"]["id"])["event"]["payload"] == pending["event"]["payload"]
        assert service.creators.get(cid)["producedCount"] == 1
    finally:
        owner.release()


def test_unrecognized_source_snapshot_is_preserved_without_auto_adoption(tmp_path):
    reader = SourceHistory(tmp_path / "jobs")
    reader.path.parent.mkdir(parents=True)
    opaque = {"id":"retained-unrecognized", "owner":"unknown-source", "enabled":True,
              "extension":{"unrecognized":[1, "opaque"]}, "pending":{"unknown":"do not resend"}}
    with sqlite3.connect(reader.path) as db:
        db.execute("CREATE TABLE sources(id TEXT PRIMARY KEY,document TEXT NOT NULL)")
        db.execute("CREATE TABLE source_links(trigger_id TEXT PRIMARY KEY,source_id TEXT,job_id TEXT)")
        db.execute("CREATE TABLE source_requests(key TEXT PRIMARY KEY,fingerprint TEXT,id TEXT)")
        db.execute("INSERT INTO sources VALUES(?,?)", (opaque["id"], json.dumps(opaque, indent=3)))
    original = reader.path.read_bytes()
    owner = OwnerFence(tmp_path)
    owner.claim(0)
    owner.set_state("active")
    service = host(tmp_path, owner, SessionProvider())
    try:
        snapshot = reader.snapshot()
        assert snapshot["sources"] == [opaque]
        history = service.creators.import_legacy_sources(reader, request_key="opaque-history", expected_digest=fc.digest(snapshot))
        assert history["snapshot"] == snapshot
        asyncio.run(service.tick())
        assert service.creators.list() == [] and service.events.stats() == {"events":0, "pins":0}
        assert reader.path.read_bytes() == original
    finally:
        owner.release()
