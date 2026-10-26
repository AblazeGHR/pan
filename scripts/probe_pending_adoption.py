"""R6-I positive identity gate for the explicit R6-J adoption port.

Only owned temporary SQLite stores are used; no server, Worker or provider.
Run through run_tests_isolated.py. Any failed assertion/error is a failed gate.
The original negative composition/v1-writer gate remains valid and unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from packages.automation.event_store import EventStore
from packages.jobs import final_contracts as fc
from packages.jobs.runtime_owner import OwnerFence
from packages.scheduler.creators import CanonicalEventStore, CreatorRuntime, CreatorStore, EventBus
from packages.scheduler.pending_adoption import PendingPublicationAdoption


def probe(root, phase):
    from packages.scheduler.time_events import Sources
    start = datetime(2026, 1, 1)
    sources = Sources(root / "jobs")
    source = sources.put({"name": "synthetic cutover gate", "enabled": True,
        "schedule": {"kind": "interval", "intervalSec": 60, "anchor": start.isoformat()}},
        0, at=start, creation_key="synthetic-source")
    sources.bind_link(source["id"], "old-trigger", "old-job")
    pending = sources.reserve(source["id"], start + timedelta(seconds=60))
    legacy = EventStore(root / "automation")
    legacy.initialize()
    if phase != "before-admit":
        legacy.append(copy.deepcopy(pending["event"]), [])
    if phase == "before-ack":
        sources.advance(source["id"], pending["event"]["id"])
    snapshot = sources.snapshot()
    before = {p: p.read_bytes() for p in (sources.path, legacy.path)}
    owner = OwnerFence(root)
    owner.claim(0)
    owner.set_state("active")
    creators = CreatorStore(root / "canonical")
    events = CanonicalEventStore(root / "canonical")
    creators.owner_fence = events.owner_fence = owner
    try:
        creators.import_legacy_sources(sources, request_key="history", expected_digest=fc.digest(snapshot))
        port = PendingPublicationAdoption(creators, events, legacy)
        def adopt():
            return creators.adopt_legacy_pending(events, legacy, import_key="history",
                source_id=source["id"], timezone_name="UTC")
        adopted = adopt()
        cid, eid = adopted["creatorId"], pending["event"]["id"]
        assert adopted["adoptionComplete"] and adopted["state"] == "adopted_pending"
        assert adopted["identity"]["eventId"] == adopted["identity"]["driveId"] == eid
        assert adopted["legacyEnvelope"] == pending["event"] and adopt() == adopted
        assert not adopted["publicationSettled"] and not adopted["eventStoreReleased"]
        assert adopted["cursorAdvanced"] == (phase == "before-ack")
        assert adopted["sourceLinks"] == snapshot["links"] and adopted["sourceRequests"] == snapshot["requests"]
        assert all(p.read_bytes() == data for p, data in before.items())
        bus = EventBus(events, creators.get)
        assert asyncio.run(CreatorRuntime(creators, bus).tick(at="2026-01-01T00:02:00Z")) == 0
        assert all(events.get(e["eventId"])["targets"] == [] for e in events.page())
        try:
            events.append(copy.deepcopy(pending["event"]), profile_id=creators.profile_id)
        except fc.ContractError as exc:
            assert exc.code == "invalid_schema"
        else:
            raise AssertionError("Generic writer accepted v1")
        if phase == "before-admit":
            assert port.settle(cid)["publicationState"] == "awaiting_admission"
            item = creators.get(cid)
            asyncio.run(bus.publish(item["pendingPublication"]["envelope"], item["pendingPublication"]["fence"]))
        settled = port.settle(cid)
        assert settled["publicationSettled"] and settled["cursorAdvanced"] and settled["eventStoreReleased"]
        assert adopt() == port.settle(cid) == settled
        assert creators.get(cid)["producedCount"] == 1 and events.stats() == {"events": 1, "pins": 0}
        assert sources.path.read_bytes() == before[sources.path]
        return {"phase": phase, "sameIdentityAdoption": True, "adoptionState": adopted["state"],
            "publicationStateAfterAdopt": adopted["publicationState"], "pendingStatePreserved": True,
            "cursorAdvancedAtAdopt": adopted["cursorAdvanced"], "ackAtAdopt": adopted["eventStoreReleased"],
            "legacyDatabaseBytesUnchangedAtAdopt": True, "sourceBytesUnchangedAfterSettlement": True,
            "linksAndRequestsPreserved": True, "duplicateAdoptIdempotent": True,
            "directV1WriterRejection": "invalid_schema", "runtimePublicationsFromAdoption": 0,
            "explicitFirstCanonicalPublication": phase == "before-admit",
            "sameIdentitySettlement": True, "canonicalEvents": 1, "canonicalDrives": 1,
            "canonicalPinsAfterSettlement": 0}
    finally:
        owner.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="pan-r6j-identity-") as temporary:
        cases = [probe(Path(temporary) / phase, phase) for phase in
                 ("before-admit", "after-admit-before-cursor", "before-ack")]
    repo = Path(__file__).resolve().parents[1]
    files = ("packages/scheduler/creators.py", "packages/scheduler/pending_adoption.py",
             "tests/test_scheduler_pending_adoption.py", "scripts/probe_pending_adoption.py")
    result = {"gate": "passed", "baseSha": "f0970e23df5108eabd7580847346129d1714ba14",
              "sourceFilesSha256": {p: hashlib.sha256((repo / p).read_bytes()).hexdigest() for p in files},
              "cases": cases}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
