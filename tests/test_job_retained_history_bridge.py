"""Exact retained successor of the old bridge manual-run/cursor contract.

Old bridge tests remain verbatim. Only canonical Job admission executes here;
the old source, dispatch receipt and task IDs are synthetic offline evidence.
"""
import asyncio
import copy
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.runtime_owner import OwnerFence
from packages.scheduler.history import History
from packages.scheduler.source_history import SourceHistory
from tests.test_automation_canonical import USER, SessionProvider
from tests.test_job_final_creators import at
from tests.test_job_retained_history_fixtures import archive_job, forbid_legacy_effects
from tests.test_scheduler_source_migration import host, graph, enable


def terminal_history(root):
    """Build offline evidence before owner activation; no dispatch/runtime."""
    from packages.scheduler.time_events import Sources
    from packages.automation.event_store import EventStore
    from packages.scheduler.creators import CreatorStore
    start = datetime(2026, 1, 1)
    sources = Sources(root / "jobs")
    source = sources.put({"name": "offline bridge", "enabled": True,
        "schedule": {"kind": "interval", "intervalSec": 60, "anchor": start.isoformat()}},
        0, at=start, creation_key="original-create")
    sources.bind_link(source["id"], "old-trigger", "job_original_bridge")
    pending = sources.reserve(source["id"], start + timedelta(seconds=60))
    legacy = EventStore(root / "automation", clock=lambda: 1767225660)
    legacy.initialize()
    eid = pending["event"]["id"]
    legacy.append(pending["event"], [{"id": "old-trigger", "jobId": "job_original_bridge"}])
    legacy.subscription(eid, "old-trigger", "completed", runId="original-run")
    legacy.delivery(eid, done=True)
    sources.advance(source["id"], eid)
    original = sources.snapshot()
    owner = OwnerFence(root)
    owner.claim(0)
    owner.set_state("active")
    creators = CreatorStore(root / "canonical", clock=lambda: at(0))
    creators.owner_fence = owner
    creators.import_legacy_sources(sources, request_key="history", expected_digest=fc.digest(original))
    return sources, pending, legacy, original, owner


def test_run_now_does_not_move_periodic_next_fire_retained_successor(tmp_path, monkeypatch):
    # Offline source reservation/ack + original receipt, with no legacy Runtime.
    sources, pending, legacy, original, owner = terminal_history(tmp_path)
    eid = pending["event"]["id"]
    root = tmp_path / "jobs"
    monkeypatch.setenv("PAN_BACKGROUND_JOBS_DIR", str(root))
    old = archive_job(root, "job_original_bridge", "scheduled-task", taskId="sch_original_bridge",
        schedulerOwner="event", legacySourceId=original["sources"][0]["id"],
        schedulerMigration="complete", lastStatus="completed", nextFireAt="2026-01-01T00:02:00",
        schedule=[{"id": "original_entry", "kind": "interval", "intervalSec": 60,
                   "anchor": "2026-01-01T00:00:00", "nextFireAt": "2026-01-01T00:02:00", "enabled": False}])
    run = {"run_id": "original-run", "task_id": old["taskId"], "dispatch_key": "original_dispatch",
           "entry_id": "original_entry", "event_id": eid, "status": "completed"}
    (root / "runs.jsonl").write_text(json.dumps(run) + "\n", encoding="utf-8")
    retained_paths = [sources.path, root / "jobs/job_original_bridge.json", root / "runs.jsonl"]
    retained_bytes = {p: p.read_bytes() for p in retained_paths}
    forbid_legacy_effects(monkeypatch)
    from packages.scheduler.legacy_bridge import LegacySchedulerBridge
    from packages.jobs.action_runtime import Runtime
    def forbidden(*args, **kwargs):
        raise AssertionError("Retired bridge execution/writer must never run")
    monkeypatch.setattr(LegacySchedulerBridge, "switch", forbidden)
    monkeypatch.setattr(LegacySchedulerBridge, "run_now", forbidden)
    monkeypatch.setattr(Runtime, "launch", forbidden)
    monkeypatch.setattr(sources.__class__, "reserve", forbidden)
    monkeypatch.setattr(sources.__class__, "advance", forbidden)
    provider = SessionProvider()
    service = host(tmp_path, owner, provider)
    try:
        migration = service.scheduler_migration(legacy)
        receipt = migration.adopt(import_key="history", source_id=old["legacySourceId"], timezone_name="UTC", actor=USER)
        cid = receipt["creatorId"]
        settled = migration.settle(cid, actor=USER)
        assert settled["publicationSettled"] and settled["cursorAdvanced"] and settled["eventStoreReleased"]
        assert settled["identity"]["eventId"] == eid
        assert settled["legacyEvidence"]["done"] is True
        assert settled["legacyEvidence"]["targets"] == [{"id": "old-trigger", "jobId": old["jobId"]}]
        assert settled["legacyEvidence"]["subscriptions"]["old-trigger"]["status"] == "completed"
        assert settled["legacyEvidence"]["subscriptions"]["old-trigger"]["runId"] == "original-run"
        job, trigger = graph(service, cid)
        migration.bind_successors(cid, trigger_map={"old-trigger": trigger["id"]},
            job_map={old["jobId"]: job["id"]}, expected_revision=1, actor=USER)
        settled = migration.status(cid, actor=USER)
        enable(service, "event_creator", service.creators.get(cid), "enable-periodic")
        before = copy.deepcopy(service.creators.get(cid))
        assert before["cursor"]["rules"]["legacy"]["nextFireAt"] == at(120)
        assert provider.calls == [] and service.final.runs(job["id"]) == []
        manual = service.create_run(job["id"], {"expectedRevision": job["configRevision"],
            "requestKey": "manual-once", "input": {}, "cause": {"type": "manual"}}, actor=USER)
        asyncio.run(service.dispatch_once())
        rid = manual["runId"]
        assert service.get_run(rid, actor=USER)["state"] == "completed"
        assert service.get_run(rid, actor=USER)["cause"] == {"type": "manual"}
        assert len(provider.calls) == 1 and service.events.stats() == {"events": 1, "pins": 0}
        # Query original operation/Run identities; no resubmission of the intent.
        assert service.operation(manual["operationId"], actor=USER)["status"] == "completed"
        assert service.creators.get(cid) == before
        assert migration.status(cid, actor=USER) == settled
        history = History(tmp_path / "canonical")
        for identity in (old["jobId"], old["taskId"]):
            task = history.get(identity)
            assert task["id"] == old["taskId"] and not task["executable"]
            assert task["history"]["source"]["schedulerOwner"] == "event"
            assert task["nextFireAt"] == old["nextFireAt"] and history.runs(task) == [run]
        assert SourceHistory(root).snapshot() == original
        source = SourceHistory(root).get(old["legacySourceId"])
        assert source["owner"] == "scheduler-event-v1"
        assert source["publishCursor"]["eventId"] == eid
        assert source["nextFireAt"] == old["nextFireAt"]
        assert all(p.read_bytes() == raw for p, raw in retained_bytes.items())
        with pytest.raises(fc.ContractError, match="fence"):
            with OwnerFence(tmp_path).legacy_pass():
                pytest.fail("legacy writer admitted under canonical owner")
        with sqlite3.connect(owner.path) as db:
            assert db.execute("SELECT count(*) FROM legacy_leases").fetchone()[0] == 0
        assert owner.status()["state"] == "active"
    finally:
        asyncio.run(service.close())
        owner.release()
    assert owner.status()["state"] == "closed"
    assert all(p.read_bytes() == raw for p, raw in retained_bytes.items())
