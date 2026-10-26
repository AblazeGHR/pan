"""Explicit same-identity handoff of a frozen scheduler v1 publication.

No startup scan, subscription dispatch, legacy writer, or Action is called here.
Creator prepare -> Event adoption -> Creator receipt is a recoverable saga.
Settlement is separately requested: durable Event -> cursor -> pins -> ack.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone

from packages.jobs import final_contracts as fc
from .providers import legacy_source_plan, timestamp


def fail(code, message):
    raise fc.ContractError(code, message)


def encoded(value):
    return fc.canonical_json(value)


class PendingPublicationAdoption:
    """Trusted, profile-fenced migration port; all roots must be explicit.

    The historical source/links/requests remain immutable. ``settle`` releases
    the original Event pin only after the canonical cursor has committed. It
    never rewrites the historical source's ack to pretend that v1 ran again.
    """

    def __init__(self, creators, events, legacy_events):
        self.creators, self.events, self.legacy_events = creators, events, legacy_events

    @contextmanager
    def owned(self):
        owner = self.creators.owner_fence
        if owner is None or owner.token is None:
            fail("revision_conflict", "Canonical owner required for pending adoption")
        if (self.events.path.resolve().parent != self.creators.root
                or self.events.owner_fence is not owner
                or self.legacy_events.path.resolve().parent.parent != owner.root):
            fail("reference_conflict", "Pending adoption stores belong to another profile or owner")
        with owner.writer(canonical=True):
            yield

    def legacy_evidence(self, event_id):
        """One read snapshot, including real commit/dispatch/pin evidence."""
        path = self.legacy_events.path.resolve()
        if not path.exists():
            return {"state": "unavailable", "eventId": event_id}
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if row is None:
                return {"state": "absent", "eventId": event_id}
            return {"state": "committed", "event": json.loads(row["event"]),
                    "receivedAt": row["received"], "targets": json.loads(row["targets"]),
                    "done": bool(row["done"]), "deliveryCount": row["deliveries"],
                    "lastError": row["last_error"],
                    "pinned": bool(db.execute("SELECT 1 FROM event_pins WHERE event_id=?", (event_id,)).fetchone()),
                    "subscriptions": {r[0]: json.loads(r[1]) for r in db.execute(
                        "SELECT target,receipt FROM subscriptions WHERE event_id=?", (event_id,))}}

    def _plan(self, history, source_id, timezone_name):
        source = next((s for s in history["snapshot"]["sources"] if s["id"] == source_id), None)
        if source is None:
            fail("reference_conflict", "Source missing from reviewed history")
        pending, cursor = source.get("pending"), source.get("publishCursor")
        advanced = not pending and bool(cursor) and cursor.get("eventStoreReleased") is False
        if not pending and not advanced:
            fail("reference_conflict", "Source has no pending publication to adopt")
        if pending:
            if set(pending) != {"event", "nextFireAt"}:
                fail("capability_unsupported", "Unknown pending publication fields")
            event = copy.deepcopy(pending["event"])
        else:
            if set(cursor) != {"eventId", "scheduleId", "scheduleRevision", "scheduledAt", "emittedAt", "eventStoreReleased"}:
                fail("capability_unsupported", "Unknown publication cursor fields")
            event = {"id": cursor["eventId"], "type": "scheduler.time.v1", "schemaVersion": 1,
                     "source": {"type": "core", "id": "scheduler"}, "occurredAt": cursor["emittedAt"],
                     "payload": {k: v for k, v in cursor.items() if k != "eventStoreReleased"}}
        if (set(event) != {"id", "type", "schemaVersion", "source", "occurredAt", "payload"}
                or event["type"] != "scheduler.time.v1" or type(event["schemaVersion"]) is not int
                or event["schemaVersion"] != 1 or event["source"] != {"type": "core", "id": "scheduler"}):
            fail("invalid_schema", "Only original scheduler v1 pending envelopes can be adopted")
        payload = event["payload"]
        if (not isinstance(payload, dict) or set(payload) != {
                "scheduleId", "scheduleRevision", "scheduledAt", "emittedAt", "eventId"}
                or payload["scheduleId"] != source_id or type(payload["scheduleRevision"]) is not int
                or payload["scheduleRevision"] != source["revision"] or payload["eventId"] != event["id"]
                or event["occurredAt"] != payload["emittedAt"]):
            fail("reference_conflict", "Legacy pending envelope does not match its source")
        drive_key = [source["owner"], source_id, source["revision"], payload["scheduledAt"]]
        event_id = "evt_schedule_" + hashlib.sha256(json.dumps(drive_key, separators=(",", ":")).encode()).hexdigest()
        if event["id"] != event_id or pending and source["nextFireAt"] != payload["scheduledAt"]:
            fail("reference_conflict", "Legacy occurrence identity changed")
        # Convert only the reviewed rule/cursor. Removing publication metadata
        # from this local calculator input does not settle or ack either ledger.
        clean = copy.deepcopy(source)
        clean.pop("pending", None)
        clean["publishCursor"] = None
        definition, current = legacy_source_plan(clean, timezone_name=timezone_name)
        following = current
        if pending:
            clean["nextFireAt"] = pending["nextFireAt"]
            if clean["nextFireAt"] is None:
                clean["enabled"] = False
            _, following = legacy_source_plan(clean, timezone_name=timezone_name)
        return source, event, definition, current, following, advanced, drive_key

    def adopt(self, *, import_key, source_id, timezone_name):
        with self.owned():
            history = self.creators.legacy_history(import_key)
            if history is None:
                fail("reference_conflict", "Reviewed history import required")
            source, event, definition, current, following, advanced, drive_key = self._plan(history, source_id, timezone_name)
            event_id = event["id"]
            cid = "creator_" + fc.digest({"legacySource": source_id, "profile": self.creators.profile_id})[:24]
            identity = {"sourceId": source_id, "eventId": event_id, "driveId": event_id,
                        "driveKey": drive_key, "occurrenceKey": "time:legacy:" + event["payload"]["scheduledAt"]}
            origin = {"importKey": import_key, "sourceDigest": history["sourceDigest"],
                      "legacyEventPath": str(self.legacy_events.path.resolve()), "timezone": timezone_name}
            with self.creators.transaction() as db:
                existing = db.execute("SELECT importKey,creatorId FROM legacy_scheduler_adoptions WHERE sourceId=?", (source_id,)).fetchone()
                if existing and tuple(existing) != (import_key, cid):
                    fail("reference_conflict", "Legacy source already has a canonical owner")
                item = self.creators._read(db, cid)
                if item:
                    adopted = item.get("pendingAdoption", {})
                    if adopted.get("origin") != origin or adopted.get("identity") != identity:
                        fail("idempotency_conflict", "Pending adoption identity changed")
                    if adopted["state"] == "adopted_uncertain":
                        evidence = self.legacy_evidence(event_id)
                        if evidence["state"] == "committed":
                            if evidence["event"] != event:
                                fail("idempotency_conflict", "Recovered original Event fact conflicts")
                            adopted.update(legacyEvidence=evidence, state="adopted_pending", execution="preserved_only")
                            self.creators._write(db, item)
                else:
                    evidence = self.legacy_evidence(event_id)
                    if evidence["state"] == "committed" and evidence["event"] != event:
                        fail("idempotency_conflict", "Original Event Store fact conflicts with pending envelope")
                    # Missing proof of an already advanced cursor remains unknown.
                    uncertain = evidence["state"] == "unavailable" or advanced and evidence["state"] != "committed"
                    item = self.creators.create(definition, request_key="pending-adoption:" + source_id,
                                                creator_id=cid, state="disabled")
                    emitted = timestamp(event["occurredAt"])
                    envelope = {"schemaVersion": 2, "eventId": event_id, "eventType": event["type"],
                                "eventSchemaRevision": 1, "creatorId": cid, "creatorRevision": item["revision"],
                                "occurrenceKey": identity["occurrenceKey"], "occurredAt": emitted,
                                "emittedAt": emitted, "payload": copy.deepcopy(event["payload"])}
                    fc.validate_event_publish(envelope)
                    drive = {"driveId": event_id, "eventId": event_id, "driveSeq": 1,
                             "occurrenceKey": identity["occurrenceKey"], "actualDriveAt": emitted,
                             "scheduledAt": event["payload"]["scheduledAt"], "reason": "scheduled", "ruleId": "legacy",
                             "outcome": "reserved"}
                    item["drives"] = [drive]
                    item["cursor"]["rules"]["legacy"].update(nextFireAt=current["at"] if current else None,
                        nextOccurrence=current, lastSettledDriveId=event_id if advanced else None)
                    item["pendingPublication"] = {"drive": drive, "envelope": envelope, "ruleId": "legacy",
                        "adoptedIdentity": event_id,
                        "rule": copy.deepcopy(definition["config"]["rules"][0]), "nextOccurrence": following,
                        "fence": {"revision": item["revision"], "epoch": item["ownerEpoch"],
                                  "generation": item["providerGeneration"], "driveId": event_id}}
                    item["pendingAdoption"] = {"recordType": "scheduler_pending_adoption", "version": 1,
                        "identity": identity, "origin": origin, "legacySource": copy.deepcopy(source),
                        "legacyEnvelope": event, "envelope": envelope, "legacyEvidence": evidence,
                        "sourceLinks": [l for l in history["snapshot"]["links"] if l["sourceId"] == source_id],
                        "sourceRequests": [r for r in history["snapshot"]["requests"] if r["sourceId"] == source_id],
                        "state": "adopted_uncertain" if uncertain else "adopted_pending", "phase": "prepared",
                        "cursorAdvanced": advanced, "eventStoreReleased": False, "publicationSettled": False,
                        "admissionAllowed": evidence["state"] == "absent" and not advanced,
                        "execution": "not_requested" if evidence["state"] == "absent" else "preserved_only"}
                    if advanced and not uncertain:
                        # Preserve the already advanced breakpoint. Only its
                        # pin/ack handoff remains; do not manufacture a second
                        # reserved drive or advance/count it again on settle.
                        receipt = {"persisted": True, "eventId": event_id,
                                   "factDigest": fc.event_fact_digest(envelope),
                                   "receivedAt": timestamp(datetime.fromtimestamp(evidence["receivedAt"], timezone.utc))}
                        drive.update(outcome="published", receipt=receipt)
                        item["producedCount"] = 1
                        item["pendingPublication"] = None
                        item["lastPublication"] = {"eventId": event_id, "factDigest": receipt["factDigest"],
                                                   "epoch": item["ownerEpoch"], "eventStoreReleased": False}
                    db.execute("INSERT INTO legacy_scheduler_adoptions VALUES(?,?,?)", (source_id, import_key, cid))
                    self.creators._write(db, item)
            # Never hold a Creator DB transaction across the other store commit.
            # Retrying the same handoff completes either side of this crash gap.
            self._adopt_event(item)
            with self.creators.transaction() as db:
                item = self.creators._read_db(db, cid)
                item["pendingAdoption"]["phase"] = "adopted"
                self.creators._write(db, item)
            return self.status(cid)

    def _adopt_event(self, item):
        adoption = item["pendingAdoption"]
        envelope = adoption["envelope"]
        binding = {"identity": adoption["identity"], "origin": adoption["origin"],
                   "envelope": envelope, "admissionAllowed": adoption["admissionAllowed"]}
        event_id = envelope["eventId"]
        with self.events.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT document FROM pending_adoptions WHERE eventId=?", (event_id,)).fetchone()
            if row and json.loads(row[0]) != binding:
                fail("idempotency_conflict", "Event adoption identity changed")
            retained = db.execute("SELECT record FROM events WHERE eventId=?", (event_id,)).fetchone()
            evidence = adoption["legacyEvidence"]
            if retained:
                fc.event_receipt_outcome(json.loads(retained[0]), envelope)
                if not row:
                    fail("reference_conflict", "Event already exists outside this adoption")
            elif adoption["publicationSettled"]:
                fail("reference_conflict", "Settled adopted Event is missing; cannot recreate it")
            db.execute("INSERT OR IGNORE INTO pending_adoptions VALUES(?,?)", (event_id, encoded(binding)))
            if not retained and evidence["state"] == "committed":
                received = timestamp(datetime.fromtimestamp(evidence["receivedAt"], timezone.utc))
                record = {**envelope, "receivedAt": received, "factDigest": fc.event_fact_digest(envelope),
                          "profileId": item["profileId"]}
                fc.validate_object("event", record)
                # Copying a committed fact does not select new subscriptions.
                # Original target/receipt evidence is retained in the adoption.
                db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (event_id, record["factDigest"], encoded(record), "[]", received))
                db.execute("INSERT INTO event_pins VALUES(?)", (event_id,))

    def status(self, creator_id):
        with self.owned():
            item = self.creators.get(creator_id)
            if not item or not item.get("pendingAdoption"):
                raise KeyError(creator_id)
            result = copy.deepcopy(item["pendingAdoption"])
            result["creatorId"] = creator_id
            result["adoptionComplete"] = result["phase"] == "adopted"
            retained = self.events.get(result["identity"]["eventId"])
            result["publicationState"] = "settled" if result["publicationSettled"] else (
                "committed" if retained else "awaiting_admission" if result["admissionAllowed"] else "uncertain")
            return result

    def settle(self, creator_id):
        """Reconcile the original identity; never publishes or runs an effect.

        An unadmitted reservation needs an explicit EventBus.publish first.
        Unknown legacy admission needs original evidence, never a blind resend.
        """
        with self.owned():
            item = self.creators.get(creator_id)
            if not item or not item.get("pendingAdoption"):
                raise KeyError(creator_id)
            adoption = item["pendingAdoption"]
            if adoption["publicationSettled"]:
                return self.status(creator_id)
            if adoption["phase"] != "adopted" or adoption["state"] == "adopted_uncertain":
                return self.status(creator_id)
            event_id = adoption["identity"]["eventId"]
            retained = self.events.get(event_id)
            if retained is None:
                return self.status(creator_id)
            fc.event_receipt_outcome(retained["event"], adoption["envelope"])
            pending = item["pendingPublication"]
            if pending:
                envelope = pending["envelope"]
                fc.event_receipt_outcome(retained["event"], envelope)
                receipt = {"persisted": True, "eventId": event_id, "factDigest": retained["event"]["factDigest"],
                           "receivedAt": retained["event"]["receivedAt"]}
                with self.creators.transaction() as db:
                    self.creators._joined.settling_adoption = True
                    try:
                        self.creators.advance(creator_id, event_id, pending["fence"]["epoch"], receipt)
                    finally:
                        del self.creators._joined.settling_adoption
                    item = self.creators._read_db(db, creator_id)
                    item["pendingAdoption"].update(cursorAdvanced=True, envelope=envelope)
                    self.creators._write(db, item)
            else:
                last = item.get("lastPublication") or {}
                if (not adoption["cursorAdvanced"] or last.get("eventId") != event_id
                        or last.get("factDigest") != retained["event"]["factDigest"]):
                    fail("reference_conflict", "Canonical cursor has no matching publication receipt")
            # The committed canonical cursor is the authority to release either
            # pin. Legacy source metadata stays historical and byte-identical.
            self._release_original_pin(adoption)
            self.events.release_pin(event_id)
            if self.events.is_pinned(event_id):
                fail("pending_publication", "Canonical Event pin release is unconfirmed")
            with self.creators.transaction() as db:
                item = self.creators._read_db(db, creator_id)
                last = item["lastPublication"]
                self.creators._joined.settling_adoption = True
                try:
                    self.creators.acknowledged(creator_id, event_id, last["epoch"])
                finally:
                    del self.creators._joined.settling_adoption
                item = self.creators._read_db(db, creator_id)
                item["pendingAdoption"].update(eventStoreReleased=True, publicationSettled=True, state="settled")
                self.creators._write(db, item)
            return self.status(creator_id)

    def _release_original_pin(self, adoption):
        if adoption["legacyEvidence"]["state"] != "committed":
            return
        # The owner lock excludes legacy mutation throughout adopt/settle. Pin
        # release changes no event, target, delivery, subscription or source row.
        with closing(sqlite3.connect(self.legacy_events.path, timeout=5)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT event FROM events WHERE id=?", (adoption["identity"]["eventId"],)).fetchone()
            if not row or json.loads(row[0]) != adoption["legacyEnvelope"]:
                fail("idempotency_conflict", "Original Event evidence changed before ack")
            db.execute("DELETE FROM event_pins WHERE event_id=?", (adoption["identity"]["eventId"],))
