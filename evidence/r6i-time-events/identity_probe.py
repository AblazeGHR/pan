"""Negative cutover gate. Explicit temporary roots; no service or Action runs.

Run through scripts/run_tests_isolated.py from the repository. Exit 1 means
the required same-identity migration is unavailable, even if safety checks pass.
"""
import asyncio
import copy
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from packages.automation.event_store import EventStore
from packages.jobs import final_contracts as contracts
from packages.jobs.runtime_owner import OwnerFence
from packages.scheduler.composition import CompositionCoordinator
from packages.scheduler.creators import CanonicalEventStore, CreatorRuntime, CreatorStore, EventBus
from packages.scheduler.providers import legacy_source_plan
from packages.scheduler.time_events import Sources


def probe(root, phase):
    root.mkdir()
    start = datetime(2026, 1, 1)
    sources = Sources(root / "jobs")
    source = sources.put({
        "name": "synthetic cutover gate", "enabled": True,
        "schedule": {"kind": "interval", "intervalSec": 60, "anchor": start.isoformat()},
    }, 0, at=start, creation_key="synthetic-source")
    definition, _ = legacy_source_plan(source, timezone_name="UTC")
    sources.bind_link(source["id"], "old-trigger", "old-job")
    pending = sources.reserve(source["id"], start + timedelta(seconds=60))
    old_events = EventStore(root / "automation")
    old_events.initialize()
    receipt = None
    if phase != "before-admit":
        receipt = old_events.append(copy.deepcopy(pending["event"]), [])
        assert receipt["event"]["id"] == pending["event"]["id"]
    if phase == "before-ack":
        sources.advance(source["id"], pending["event"]["id"])
    snapshot = sources.snapshot()
    old_bytes = {sources.path: sources.path.read_bytes(), old_events.path: old_events.path.read_bytes()}
    owner = OwnerFence(root)
    owner.claim(0)
    owner.set_state("active")
    creators = CreatorStore(root / "canonical")
    creators.owner_fence = owner
    events = CanonicalEventStore(root / "canonical")
    events.owner_fence = owner
    try:
        history = creators.import_legacy_sources(
            sources, request_key="history", expected_digest=contracts.digest(snapshot))
        assert history["snapshot"] == snapshot
        request = dict(request_key="adopt", activate=True, desired_graph={
            "creators": [{"mode": "create", "key": "source", "definition": definition}],
            "triggers": [{"key": "new-trigger", "definition": {"name": "synthetic"},
                          "targetJobKey": "job", "creatorKeys": ["source"]}],
        }, legacy_sources=[{
            "creatorKey": "source", "importKey": "history", "sourceId": source["id"],
            "timezone": "UTC", "triggerKeys": {"old-trigger": "new-trigger"},
        }])
        try:
            CompositionCoordinator(creators).compose(**request)
        except contracts.ContractError as exc:
            migration_error = {"code": exc.code, "message": str(exc)}
        else:
            raise AssertionError("Baseline unexpectedly supports unsettled-source adoption; review gate")
        try:
            events.append(copy.deepcopy(pending["event"]), profile_id=creators.profile_id)
        except contracts.ContractError as exc:
            envelope_error = {"code": exc.code, "message": str(exc)}
        else:
            raise AssertionError("Baseline unexpectedly accepts the original v1 envelope; review gate")
        assert asyncio.run(CreatorRuntime(creators, EventBus(events, creators.get)).tick(
            at="2026-01-01T00:02:00Z")) == 0
        assert events.stats() == {"events": 0, "pins": 0}
        assert all(item["state"] == "disabled" and not item["pendingPublication"] and not item["drives"]
                   for item in creators.list())
        assert all(path.read_bytes() == before for path, before in old_bytes.items())
        assert creators.legacy_history("history")["snapshot"] == snapshot
        return {
            "phase": phase, "sameIdentityMigration": False,
            "legacyEventId": pending["event"]["id"],
            "legacyReceiptPresent": receipt is not None,
            "legacySnapshotPreserved": True, "legacyDatabaseBytesUnchanged": True,
            "sourceLinksPreservedInHistory": snapshot["links"],
            "sourceRequestsPreservedInHistory": snapshot["requests"],
            "migrationRejection": migration_error, "directEnvelopeRejection": envelope_error,
            "canonicalEvents": 0, "canonicalDrives": 0,
        }
    finally:
        owner.release()


if __name__ == "__main__":
    with TemporaryDirectory(prefix="pan-r6i-identity-") as temporary:
        result = {"gate": "blocked", "sourceSha": "f0970e23df5108eabd7580847346129d1714ba14",
                  "cases": [probe(Path(temporary) / phase, phase) for phase in
                            ("before-admit", "after-admit-before-cursor", "before-ack")]}
    destination = Path(sys.argv[1])
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1)
