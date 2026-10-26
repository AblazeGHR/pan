"""R6-J: original v1 identity handoff; synthetic roots, no service/effect host."""
import asyncio
import copy
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta

import pytest

from packages.automation.event_store import EventStore
from packages.jobs import final_contracts as fc
from packages.jobs.runtime_owner import OwnerFence
from packages.scheduler.creators import CanonicalEventStore, CreatorRuntime, CreatorStore, EventBus
from packages.scheduler.pending_adoption import PendingPublicationAdoption
from packages.scheduler.time_events import Sources
from tests.test_job_final_creators import at


PHASES = ("before-admit", "after-admit-before-cursor", "before-ack")


def setup_case(root, phase, *, kind="interval", effects=False):
    start = datetime(2026, 1, 1)
    sources = Sources(root / "jobs")
    schedule = {"kind": "interval", "intervalSec": 60, "anchor": start.isoformat()} if kind == "interval" else {
        "kind": "once", "at": (start + timedelta(seconds=60)).isoformat()}
    source = sources.put({"name": "synthetic", "enabled": True, "schedule": schedule},
                         0, at=start, creation_key="original-create")
    sources.bind_link(source["id"], "old-trigger", "old-job")
    pending = sources.reserve(source["id"], start + timedelta(seconds=60))
    legacy = EventStore(root / "automation", clock=lambda: 1767225660)
    legacy.initialize()
    if phase != "before-admit":
        legacy.append(copy.deepcopy(pending["event"]), [{"id": "old-trigger", "jobId": "old-job"}])
        if effects:
            legacy.subscription(pending["event"]["id"], "old-trigger", "uncertain", runId="original-run")
            legacy.delivery(pending["event"]["id"], error="lost original receipt")
    if phase == "before-ack":
        sources.advance(source["id"], pending["event"]["id"])
    snapshot = sources.snapshot()
    owner = OwnerFence(root)
    owner.claim(0)
    owner.set_state("active")
    creators = CreatorStore(root / "canonical", clock=lambda: at(0))
    events = CanonicalEventStore(root / "canonical", clock=lambda: at(60))
    creators.owner_fence = events.owner_fence = owner
    creators.import_legacy_sources(sources, request_key="history", expected_digest=fc.digest(snapshot))
    port = PendingPublicationAdoption(creators, events, legacy)
    return sources, pending, legacy, snapshot, owner, creators, events, port


def adopt(port, source_id):
    return port.creators.adopt_legacy_pending(port.events, port.legacy_events,
        import_key="history", source_id=source_id, timezone_name="UTC")


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("kind", ["interval", "once"])
def test_three_breakpoints_keep_identity_and_pending_until_explicit_settlement(tmp_path, phase, kind):
    sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(tmp_path, phase, kind=kind)
    before = {p: p.read_bytes() for p in (sources.path, legacy.path)}
    source_id = snapshot["sources"][0]["id"]
    try:
        receipt = adopt(port, source_id)
        cid, eid = receipt["creatorId"], pending["event"]["id"]
        assert receipt["state"] == "adopted_pending" and receipt["phase"] == "adopted"
        assert receipt["identity"] == {"sourceId": source_id, "eventId": eid, "driveId": eid,
            "driveKey": ["scheduler-event-v1", source_id, 1, "2026-01-01T00:01:00"],
            "occurrenceKey": "time:legacy:2026-01-01T00:01:00"}
        assert receipt["legacyEnvelope"] == pending["event"]
        assert receipt["sourceLinks"] == snapshot["links"] and receipt["sourceRequests"] == snapshot["requests"]
        assert receipt["cursorAdvanced"] == (phase == "before-ack")
        item = creators.get(cid)
        assert (item["pendingPublication"] is None) == (phase == "before-ack")
        assert item["producedCount"] == int(phase == "before-ack")
        assert item["drives"][0]["outcome"] == ("published" if phase == "before-ack" else "reserved")
        assert not receipt["eventStoreReleased"] and not receipt["publicationSettled"]
        assert all(p.read_bytes() == raw for p, raw in before.items())
        assert adopt(port, source_id) == receipt
        bus = EventBus(events, creators.get)
        bus.subscribe("new-trigger", ["scheduler.time.v1"], creator_ids=[cid])
        assert asyncio.run(CreatorRuntime(creators, bus).tick(at=at(120))) == 0
        assert events.stats() == {"events": int(phase != "before-admit"), "pins": int(phase != "before-admit")}
        if phase == "before-admit":
            assert port.settle(cid)["publicationState"] == "awaiting_admission"
            item = creators.get(cid)
            # This separate explicit publication uses the ordinary canonical
            # bus. Adoption and settle never call it or select new targets.
            asyncio.run(bus.publish(item["pendingPublication"]["envelope"], item["pendingPublication"]["fence"]))
            assert events.get(eid)["targets"][0]["subscriptionId"] == "new-trigger"
        else:
            assert events.get(eid)["targets"] == []
        settled = port.settle(cid)
        assert settled["state"] == "settled" and settled["publicationState"] == "settled"
        assert settled["cursorAdvanced"] and settled["eventStoreReleased"]
        assert port.settle(cid) == settled and adopt(port, source_id) == settled
        item = creators.get(cid)
        assert item["producedCount"] == 1 and len(item["drives"]) == 1
        assert item["drives"][0]["eventId"] == eid and item["lastPublication"]["eventStoreReleased"]
        assert item["cursor"]["rules"]["legacy"]["nextFireAt"] == (at(120) if kind == "interval" else None)
        assert item["pendingPublication"] is None and item["state"] == "disabled"
        assert not events.is_pinned(eid)
        with legacy.connection(readonly=True) as db:
            assert not db.execute("SELECT 1 FROM event_pins WHERE event_id=?", (eid,)).fetchone()
        assert sources.path.read_bytes() == before[sources.path]
        assert creators.legacy_history("history")["snapshot"] == snapshot
        assert asyncio.run(CreatorRuntime(creators, bus).tick(at=at(600))) == 0
        with pytest.raises(fc.ContractError) as exc:
            events.append(pending["event"], profile_id=creators.profile_id)
        assert exc.value.code == "invalid_schema"
    finally:
        owner.release()


@pytest.mark.parametrize("fault", ["before_event", "after_event", "after_cursor", "after_legacy_pin", "after_canonical_pin"])
def test_crash_reopen_recovers_each_committed_saga_boundary(tmp_path, monkeypatch, fault):
    sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(tmp_path, "after-admit-before-cursor")
    source_id = snapshot["sources"][0]["id"]
    def crash(*args, **kwargs):
        raise OSError("synthetic committed-boundary fault")
    if fault in {"before_event", "after_event"}:
        original = port._adopt_event
        def broken(item):
            if fault == "after_event":
                original(item)
            crash()
        monkeypatch.setattr(PendingPublicationAdoption, "_adopt_event", lambda self, item: broken(item))
        with pytest.raises(OSError):
            adopt(port, source_id)
        prepared = creators.list()[0]
        assert not port.status(prepared["id"])["adoptionComplete"]
        assert prepared["pendingPublication"] and not prepared["pendingAdoption"]["eventStoreReleased"]
    else:
        receipt = adopt(port, source_id)
        if fault == "after_cursor":
            monkeypatch.setattr(port, "_release_original_pin", crash)
        elif fault == "after_legacy_pin":
            monkeypatch.setattr(events, "release_pin", crash)
        else:
            monkeypatch.setattr(creators, "acknowledged", crash)
        with pytest.raises(OSError):
            port.settle(receipt["creatorId"])
    owner.release()
    monkeypatch.undo()
    replacement = OwnerFence(tmp_path)
    replacement.claim(1)
    replacement.set_state("active")
    try:
        reopened = CreatorStore(tmp_path / "canonical")
        retained = CanonicalEventStore(tmp_path / "canonical")
        reopened.owner_fence = retained.owner_fence = replacement
        recovered = PendingPublicationAdoption(reopened, retained, legacy)
        receipt = adopt(recovered, source_id)
        result = recovered.settle(receipt["creatorId"])
        assert result["publicationSettled"] and result["identity"]["eventId"] == pending["event"]["id"]
        assert len(retained.page()) == 1 and reopened.get(receipt["creatorId"])["producedCount"] == 1
        assert retained.stats()["pins"] == 0
    finally:
        replacement.release()


@pytest.mark.parametrize("phase", PHASES[1:])
def test_original_unknown_effects_and_receipts_are_never_replayed_or_marked_succeeded(tmp_path, phase):
    sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(tmp_path, phase, effects=True)
    try:
        eid = pending["event"]["id"]
        original = legacy.lookup(eid)
        subscriptions = legacy.subscription_receipts(eid)
        receipt = adopt(port, snapshot["sources"][0]["id"])
        cid = receipt["creatorId"]
        assert receipt["legacyEvidence"]["subscriptions"] == subscriptions
        assert receipt["legacyEvidence"]["lastError"] == "lost original receipt"
        assert receipt["execution"] == "preserved_only"
        assert events.get(eid)["targets"] == []
        port.settle(cid)
        assert asyncio.run(CreatorRuntime(creators, EventBus(events, creators.get)).tick(at=at(600))) == 0
        assert legacy.lookup(eid) == original and legacy.subscription_receipts(eid) == subscriptions
        assert creators.get(cid)["pendingAdoption"]["legacyEvidence"]["subscriptions"]["old-trigger"]["status"] == "uncertain"
    finally:
        owner.release()


def test_missing_original_commit_after_advance_stays_explicitly_uncertain(tmp_path):
    sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(tmp_path, "before-ack")
    # A copy with the original commit missing is not permission to resend.
    with owner.writer(canonical=True), closing(sqlite3.connect(legacy.path)) as db, db:
        db.execute("DELETE FROM events")
    try:
        receipt = adopt(port, snapshot["sources"][0]["id"])
        assert receipt["state"] == "adopted_uncertain" and receipt["publicationState"] == "uncertain"
        assert port.settle(receipt["creatorId"]) == receipt
        item = creators.get(receipt["creatorId"])
        with pytest.raises(fc.ContractError) as exc:
            asyncio.run(EventBus(events, creators.get).publish(item["pendingPublication"]["envelope"], item["pendingPublication"]["fence"]))
        assert exc.value.code == "pending_publication"
        assert not receipt["eventStoreReleased"] and events.stats()["events"] == 0
    finally:
        owner.release()


def test_foreign_owner_and_changed_original_fact_are_refused(tmp_path):
    sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(tmp_path, "after-admit-before-cursor")
    try:
        foreign = EventStore(tmp_path / "foreign" / "automation")
        with pytest.raises(fc.ContractError, match="another profile"):
            adopt(PendingPublicationAdoption(creators, events, foreign), snapshot["sources"][0]["id"])
        with owner.writer(canonical=True), closing(sqlite3.connect(legacy.path)) as db, db:
            changed = copy.deepcopy(pending["event"])
            changed["payload"]["scheduledAt"] = "2026-01-01T00:02:00"
            db.execute("UPDATE events SET event=?", (json.dumps(changed),))
        with pytest.raises(fc.ContractError, match="conflicts"):
            adopt(port, snapshot["sources"][0]["id"])
        assert creators.list() == []
    finally:
        owner.release()


def test_missing_proof_can_only_reconcile_by_original_committed_fact(tmp_path):
    sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(tmp_path, "before-ack")
    with owner.writer(canonical=True), closing(sqlite3.connect(legacy.path)) as db, db:
        row = db.execute("SELECT * FROM events").fetchone()
        db.execute("DELETE FROM events")
    source_id = snapshot["sources"][0]["id"]
    try:
        first = adopt(port, source_id)
        assert first["state"] == "adopted_uncertain"
        assert adopt(port, source_id) == first
        with owner.writer(canonical=True), closing(sqlite3.connect(legacy.path)) as db, db:
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)", row)
        recovered = adopt(port, source_id)
        assert recovered["state"] == "adopted_pending" and recovered["publicationState"] == "committed"
        assert port.settle(first["creatorId"])["publicationSettled"]
    finally:
        owner.release()


@pytest.mark.parametrize("operation", ["advance", "ack", "changed_fact", "changed_timezone"])
def test_adoption_rejects_shortcuts_and_identity_rebinding(tmp_path, operation):
    sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(tmp_path, "after-admit-before-cursor")
    source_id = snapshot["sources"][0]["id"]
    try:
        result = adopt(port, source_id)
        item = creators.get(result["creatorId"])
        cid, eid = item["id"], pending["event"]["id"]
        with pytest.raises(fc.ContractError):
            if operation == "advance":
                creators.advance(cid, eid, item["ownerEpoch"], {"persisted": True})
            elif operation == "ack":
                creators.acknowledged(cid, eid, item["ownerEpoch"])
            elif operation == "changed_fact":
                changed = copy.deepcopy(item["pendingPublication"]["envelope"])
                changed["payload"]["emittedAt"] = at(120)
                events.append(changed, profile_id=creators.profile_id)
            else:
                port.adopt(import_key="history", source_id=source_id, timezone_name="+00:00")
        assert port.status(cid) == result and events.is_pinned(eid)
    finally:
        owner.release()


def test_ack_has_durable_cursor_and_both_pin_release_proofs(tmp_path, monkeypatch):
    sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(tmp_path, "after-admit-before-cursor")
    try:
        result = adopt(port, snapshot["sources"][0]["id"])
        cid, eid = result["creatorId"], pending["event"]["id"]
        original_release = port._release_original_pin
        original_ack = creators.acknowledged
        checks = []
        def release(adoption):
            item = creators.get(cid)
            assert item["pendingPublication"] is None
            assert item["cursor"]["rules"]["legacy"]["lastSettledDriveId"] == eid
            assert not item["lastPublication"]["eventStoreReleased"]
            assert events.is_pinned(eid)
            checks.append("cursor-before-original-release")
            return original_release(adoption)
        def ack(*args):
            assert not events.is_pinned(eid)
            with legacy.connection(readonly=True) as db:
                assert not db.execute("SELECT 1 FROM event_pins WHERE event_id=?", (eid,)).fetchone()
            checks.append("pins-before-ack")
            return original_ack(*args)
        monkeypatch.setattr(port, "_release_original_pin", release)
        monkeypatch.setattr(creators, "acknowledged", ack)
        assert port.settle(cid)["publicationSettled"]
        assert checks == ["cursor-before-original-release", "pins-before-ack"]
    finally:
        owner.release()


def test_runtime_internal_publish_and_ack_cannot_bypass_explicit_adoption(tmp_path, monkeypatch):
    sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(tmp_path, "before-admit")
    try:
        result = adopt(port, snapshot["sources"][0]["id"])
        cid, eid = result["creatorId"], pending["event"]["id"]
        item = creators.get(cid)
        runtime = CreatorRuntime(creators, EventBus(events, creators.get))
        with pytest.raises(fc.ContractError, match="automatically published"):
            asyncio.run(runtime._publish(cid, item["pendingPublication"]))
        assert events.stats() == {"events": 0, "pins": 0}
        asyncio.run(runtime.bus.publish(item["pendingPublication"]["envelope"], item["pendingPublication"]["fence"]))
        def crash(*args):
            raise OSError("after canonical cursor commit")
        monkeypatch.setattr(port, "_release_original_pin", crash)
        with pytest.raises(OSError):
            port.settle(cid)
        with pytest.raises(fc.ContractError, match="explicit pending adoption"):
            asyncio.run(runtime._ack(creators.get(cid)))
        assert events.is_pinned(eid) and not creators.get(cid)["lastPublication"]["eventStoreReleased"]
    finally:
        owner.release()


@pytest.mark.parametrize("phase", PHASES)
def test_only_explicit_unadmitted_identity_uses_existing_canonical_admission_and_executor(tmp_path, phase):
    from types import SimpleNamespace
    from packages.jobs.final_api import CanonicalUserAuth
    from packages.jobs.final_service import FinalService, object_ref
    from packages.jobs.final_runtime.executor import TypedExecutor
    from tests.test_automation_canonical import USER, SessionProvider, job_def

    sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(tmp_path, phase)
    try:
        provider = SessionProvider()
        service = FinalService(tmp_path / "canonical", CanonicalUserAuth(tmp_path / "auth"),
                               executor=TypedExecutor(session_provider=provider), owner=owner)
        service._initialize_schema()
        service._install_product_actions()
        result = adopt(port, snapshot["sources"][0]["id"])
        cid, eid = result["creatorId"], pending["event"]["id"]
        action = next(a for a in service.actions(actor=USER)["items"] if a["id"] == "product_session_message")
        h = SimpleNamespace(action=object_ref("action", action))
        def create_enabled(kind, value, key):
            item = service.write(kind, None, {"definition": value, "expectedRevision": 0, "requestKey": key}, actor=USER)["result"]["object"]
            return service.control(kind, item["id"], {"operation": "enable", "expectedRevision": item.get("configRevision", item.get("revision")),
                "expectedStateRevision": item["stateRevision"], "requestKey": key + ":enable", "parameters": {}}, actor=USER)["result"]["object"]
        job = create_enabled("job", job_def(h), "job")
        target = create_enabled("trigger", {"name": "explicit", "targetJobId": job["id"],
            "eventTypes": ["scheduler.time.v1"], "creatorSelector": {"mode": "ids", "ids": [cid]}}, "trigger")
        # Even a matching canonical Trigger cannot turn adoption into replay.
        asyncio.run(service.tick())
        assert service.final.runs(job["id"]) == [] and not provider.calls
        if phase == "before-admit":
            item = creators.get(cid)
            asyncio.run(service.bus.publish(item["pendingPublication"]["envelope"], item["pendingPublication"]["fence"]))
        port.settle(cid)
        asyncio.run(service.tick())
        asyncio.run(service.tick())
        expected = int(phase == "before-admit")
        assert len(service.final.runs(job["id"])) == expected and len(provider.calls) == expected
        if expected:
            run = service.final.runs(job["id"])[0]
            assert run["cause"] == {"type": "event", "eventId": eid, "triggerId": target["id"]}
            assert run["state"] == "completed"
            # A duplicate canonical publish returns the existing frozen targets;
            # ordinary Trigger admission and effect keys keep the same Run.
            envelope = creators.get(cid)["pendingAdoption"]["envelope"]
            fence = {"driveId": eid, "revision": 1, "epoch": 1, "generation": 1}
            asyncio.run(service.bus.publish(envelope, fence))
            asyncio.run(service.tick())
            assert len(service.final.runs(job["id"])) == 1 and len(provider.calls) == 1
    finally:
        owner.release()


def test_abrupt_process_exit_after_cursor_commit_cold_recovers_without_republication(tmp_path):
    from packages.jobs.window import command_window_options
    script = '''
import os, sys
from pathlib import Path
from tests.test_scheduler_pending_adoption import setup_case, adopt
root = Path(sys.argv[1])
sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(root, "after-admit-before-cursor")
result = adopt(port, snapshot["sources"][0]["id"])
def exit_after_cursor(adoption):
    os._exit(73)
port._release_original_pin = exit_after_cursor
port.settle(result["creatorId"])
raise AssertionError("crash boundary not reached")
'''
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                           capture_output=True, text=True, timeout=30, **command_window_options())
    assert child.returncode == 73, child.stdout + child.stderr
    owner = OwnerFence(tmp_path)
    owner.claim(1)
    owner.set_state("active")
    try:
        creators = CreatorStore(tmp_path / "canonical")
        events = CanonicalEventStore(tmp_path / "canonical")
        creators.owner_fence = events.owner_fence = owner
        legacy = EventStore(tmp_path / "automation")
        port = PendingPublicationAdoption(creators, events, legacy)
        item = creators.list()[0]
        eid = item["pendingAdoption"]["identity"]["eventId"]
        assert item["pendingPublication"] is None and not item["lastPublication"]["eventStoreReleased"]
        assert events.is_pinned(eid) and item["producedCount"] == 1
        result = adopt(port, item["pendingAdoption"]["identity"]["sourceId"])
        assert port.settle(result["creatorId"])["publicationSettled"]
        assert events.stats() == {"events": 1, "pins": 0} and creators.get(item["id"])["producedCount"] == 1
    finally:
        owner.release()


def test_explicit_future_activation_uses_normal_canonical_drives_and_repeated_adopt_keeps_original_binding(tmp_path):
    sources, pending, legacy, snapshot, owner, creators, events, port = setup_case(tmp_path, "before-ack")
    source_id = snapshot["sources"][0]["id"]
    try:
        receipt = adopt(port, source_id)
        cid, eid = receipt["creatorId"], pending["event"]["id"]
        settled = port.settle(cid)
        item = creators.get(cid)
        creators.set_state(cid, "enabled", expected_state_revision=item["stateRevision"])
        runtime = CreatorRuntime(creators, EventBus(events, creators.get))
        assert asyncio.run(runtime.tick(at=at(120))) == 1
        assert asyncio.run(runtime.tick(at=at(120))) == 0
        assert creators.get(cid)["producedCount"] == 2
        assert len(events.page()) == 2 and events.page()[1]["eventId"] != eid
        assert adopt(port, source_id) == settled
        assert creators.get(cid)["cursor"]["rules"]["legacy"]["nextFireAt"] == at(180)
        with events.connection(readonly=True) as db:
            assert [row[0] for row in db.execute("SELECT eventId FROM pending_adoptions")] == [eid]
    finally:
        owner.release()
