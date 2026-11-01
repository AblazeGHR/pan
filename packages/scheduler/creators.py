"""Canonical Event Creator storage, durable Event Bus, and drive coordinator.

Production drives use this ledger and the composition activation gate. Legacy
snapshots enter only through an explicit fenced import, never startup scanning.
Historical pending envelopes remain audit facts and never execute an Action.
"""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Mapping

from packages.jobs import final_contracts as contracts
from packages.jobs.runtime_owner import OwnerFence

from .providers import ProviderRegistry, TimeProvider, instant, now, timestamp


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _definition(item: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "name", "description", "providerRef", "configSchemaRef", "config",
        "eventTypes", "requiredCapabilities",
    )
    return {field: copy.deepcopy(item[field]) for field in fields}


class CanonicalEventStore:
    """WP01 schemaVersion=2 facts with stable digest, receipts, and source pins."""

    def __init__(self, root, *, clock=now):
        self.path = Path(root) / "canonical-events.sqlite3"
        self.clock = clock
        self.lock = threading.RLock()
        self.owner_fence = OwnerFence(Path(root).resolve().parent)

    @contextmanager
    def connection(self, *, readonly=False):
        readonly = readonly and self.path.exists()
        guard = self.owner_fence.writer(canonical=True) if self.owner_fence is not None and not readonly else nullcontext()
        with guard, self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA synchronous=FULL")
                with db:
                    if readonly:
                        db.execute("PRAGMA query_only=ON")
                        yield db
                        return
                    db.execute("""CREATE TABLE IF NOT EXISTS events(
                        eventId TEXT PRIMARY KEY, factDigest TEXT NOT NULL,
                        record TEXT NOT NULL, targets TEXT NOT NULL, receivedAt TEXT NOT NULL)""")
                    db.execute("CREATE TABLE IF NOT EXISTS event_pins(eventId TEXT PRIMARY KEY)")
                    db.execute("""CREATE TABLE IF NOT EXISTS pending_adoptions(
                        eventId TEXT PRIMARY KEY, document TEXT NOT NULL)""")
                    yield db
            finally:
                db.close()

    def append(self, envelope, *, profile_id: str, targets=()):
        envelope = contracts.validate_event_publish(envelope)
        if "emittedAt" not in envelope:
            raise contracts.ContractError("invalid_schema", "emittedAt must be fixed before publication")
        digest = contracts.event_fact_digest(envelope)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            adoption = db.execute("SELECT document FROM pending_adoptions WHERE eventId=?",
                                  (envelope["eventId"],)).fetchone()
            if adoption:
                binding = json.loads(adoption[0])
                if binding["envelope"] != envelope:
                    raise contracts.ContractError("idempotency_conflict", "Adopted Event identity changed")
                if not binding["admissionAllowed"]:
                    retained = db.execute("SELECT 1 FROM events WHERE eventId=?", (envelope["eventId"],)).fetchone()
                    if not retained:
                        raise contracts.ContractError("pending_publication", "Original admission evidence is uncertain")
            old = db.execute("SELECT record,targets FROM events WHERE eventId=?", (envelope["eventId"],)).fetchone()
            if old:
                record = json.loads(old["record"])
                contracts.event_receipt_outcome(record, envelope)
                return {"eventId": record["eventId"], "factDigest": record["factDigest"],
                        "persisted": True, "receivedAt": record["receivedAt"],
                        "duplicate": True, "event": record, "targets": json.loads(old["targets"])}
            received = timestamp(self.clock())
            record = {
                **copy.deepcopy(envelope), "receivedAt": received,
                "factDigest": digest, "profileId": profile_id,
            }
            contracts.validate_object("event", record)
            target_list = copy.deepcopy(list(targets))
            db.execute("INSERT INTO events VALUES(?,?,?,?,?)",
                       (record["eventId"], digest, _json(record), _json(target_list), received))
            # Every Creator has an unsettled cursor until its explicit ack. The
            # pin is therefore provider-independent, unlike the legacy hardcode.
            db.execute("INSERT INTO event_pins VALUES(?)", (record["eventId"],))
            return {"eventId": record["eventId"], "factDigest": digest,
                    "persisted": True, "receivedAt": received,
                    "duplicate": False, "event": record, "targets": target_list}

    def get(self, event_id):
        with self.connection(readonly=True) as db:
            row = db.execute("SELECT record,targets FROM events WHERE eventId=?", (event_id,)).fetchone()
            if not row:
                return None
            return {"event": json.loads(row["record"]), "targets": json.loads(row["targets"])}

    def page(self):
        with self.connection(readonly=True) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT record FROM events ORDER BY rowid")]

    def stats(self):
        with self.connection(readonly=True) as db:
            count = db.execute("SELECT count(*) FROM events").fetchone()[0]
            pins = db.execute("SELECT count(*) FROM event_pins").fetchone()[0]
            return {"events": count, "pins": pins}

    def is_pinned(self, event_id):
        with self.connection(readonly=True) as db:
            return bool(db.execute("SELECT 1 FROM event_pins WHERE eventId=?", (event_id,)).fetchone())

    def release_pin(self, event_id):
        with self.connection() as db:
            db.execute("DELETE FROM event_pins WHERE eventId=?", (event_id,))


class EventBus:
    """Trusted publish port; durable admission precedes any WP03 subscription work."""

    def __init__(self, events: CanonicalEventStore, resolve_creator):
        self.events = events
        self.resolve_creator = resolve_creator
        self.subscriptions = {}

    def subscribe(self, subscription_id: str, event_types, *, creator_ids=()):
        if not subscription_id or not event_types:
            raise ValueError("Subscription identity and eventTypes are required")
        self.subscriptions[subscription_id] = {
            "subscriptionId": subscription_id,
            "eventTypes": list(event_types), "creatorIds": list(creator_ids),
        }

    def targets(self, envelope):
        return [
            copy.deepcopy(target) for target in self.subscriptions.values()
            if envelope["eventType"] in target["eventTypes"]
            and (not target["creatorIds"] or envelope["creatorId"] in target["creatorIds"])
        ]

    async def publish(self, envelope, fence: Mapping[str, Any]):
        envelope = contracts.validate_event_publish(envelope)
        creator = await asyncio.to_thread(self.resolve_creator, envelope["creatorId"])
        if creator is None:
            raise contracts.ContractError("reference_conflict", "Event Creator does not exist")
        digest = contracts.event_fact_digest(envelope)
        pending = creator.get("pendingPublication")
        accepted = bool(
            pending and pending["drive"]["driveId"] == fence.get("driveId")
            and pending["envelope"]["eventId"] == envelope["eventId"]
            and contracts.event_fact_digest(pending["envelope"]) == digest
        )
        if accepted and dict(fence) != pending["fence"]:
            raise contracts.ContractError("revision_conflict", "Pending Creator publication fence rejected")
        if not accepted:
            # A concurrent publisher may have advanced this pending already.
            # Only an existing identical receipt can be retried after that CAS;
            # current owner metadata alone cannot authorize a new occurrence.
            retained = await asyncio.to_thread(self.events.get, envelope["eventId"])
            if not retained or not any(
                drive.get("driveId") == fence.get("driveId") and drive.get("eventId") == envelope["eventId"]
                for drive in creator["drives"]
            ):
                raise contracts.ContractError("revision_conflict", "Creator publication has no reserved drive")
            contracts.event_receipt_outcome(retained["event"], envelope)
        return await asyncio.to_thread(
            self.events.append, envelope,
            profile_id=creator["profileId"], targets=self.targets(envelope),
        )

    async def release_pin(self, event_id):
        await asyncio.to_thread(self.events.release_pin, event_id)


class CreatorStore:
    """SQLite-backed first-class Creator instances and their drive ledgers."""

    def __init__(self, root, registry: ProviderRegistry | None = None, *, clock=now):
        self.root = Path(root).resolve()
        self.path = self.root / "event-creators" / "creators.sqlite3"
        self.registry = registry or ProviderRegistry.standard()
        self.clock = clock
        self.lock = threading.RLock()
        self.owner_fence = OwnerFence(self.root.parent)
        self._joined = threading.local()
        self.profile_id = "profile_" + contracts.digest(str(self.root))[:24]

    @contextmanager
    def connection(self, *, readonly=False):
        readonly = readonly and self.path.exists()
        borrowed = getattr(self._joined, "db", None)
        if borrowed is not None:
            yield borrowed
            return
        guard = self.owner_fence.writer(canonical=True) if self.owner_fence is not None and not readonly else nullcontext()
        with guard, self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA synchronous=FULL")
                with db:
                    if readonly:
                        db.execute("PRAGMA query_only=ON")
                        yield db
                        return
                    db.execute("CREATE TABLE IF NOT EXISTS creators(id TEXT PRIMARY KEY,document TEXT NOT NULL)")
                    db.execute("""CREATE TABLE IF NOT EXISTS requests(
                        key TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,objectId TEXT NOT NULL)""")
                    db.execute("""CREATE TABLE IF NOT EXISTS creator_links(
                        triggerId TEXT NOT NULL,creatorId TEXT NOT NULL,
                        jobId TEXT NOT NULL,operationId TEXT,
                        PRIMARY KEY(triggerId,creatorId))""")
                    db.execute("""CREATE TABLE IF NOT EXISTS compositions(
                        id TEXT PRIMARY KEY,requestKey TEXT UNIQUE,document TEXT NOT NULL)""")
                    db.execute("""CREATE TABLE IF NOT EXISTS legacy_scheduler_history(
                        requestKey TEXT PRIMARY KEY,sourcePath TEXT UNIQUE NOT NULL,
                        sourceDigest TEXT NOT NULL,document TEXT NOT NULL)""")
                    db.execute("""CREATE TABLE IF NOT EXISTS legacy_scheduler_adoptions(
                        sourceId TEXT PRIMARY KEY,importKey TEXT NOT NULL,creatorId TEXT UNIQUE NOT NULL)""")
                    yield db
            finally:
                db.close()

    @contextmanager
    def transaction(self):
        """Join Creator, composition and canonical control writes atomically.

        Borrowing is thread-local and scoped; nested provider/store calls use
        the caller's transaction without committing an intermediate phase.
        """
        with self.connection() as db:
            if getattr(self._joined, "db", None) is not None:
                yield db
                return
            db.execute("BEGIN IMMEDIATE")
            self._joined.db = db
            try:
                yield db
            finally:
                del self._joined.db

    @staticmethod
    def _read(db, creator_id):
        row = db.execute("SELECT document FROM creators WHERE id=?", (creator_id,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _write(db, item):
        db.execute("INSERT OR REPLACE INTO creators VALUES(?,?)", (item["id"], _json(item)))

    def list(self):
        with self.connection(readonly=True) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT document FROM creators ORDER BY id")]

    def get(self, creator_id):
        with self.connection(readonly=True) as db:
            item = self._read(db, creator_id)
            return copy.deepcopy(item) if item else None

    def ref(self, creator_id):
        item = self.get(creator_id)
        if item is None:
            raise KeyError(creator_id)
        return {"id": creator_id, "revision": item["revision"], "digest": contracts.digest(_definition(item))}

    def import_legacy_sources(self, sources, *, request_key, expected_digest):
        """Copy reviewed source/pending/link/cursor facts without executing them.

        Activation must already exclude legacy writers through the profile's
        fencing authority. Complex/unsettled rows are retained verbatim here;
        they are deliberately not CreatorRuntime inputs. No source is modified.
        """
        contracts.check_request_key(request_key)
        if sources.owner_fence.root != self.owner_fence.root:
            raise contracts.ContractError("reference_conflict", "Legacy source belongs to another profile")
        if self.owner_fence.token is None:
            raise contracts.ContractError("revision_conflict", "Canonical owner required for Scheduler import")
        with self.transaction() as db:
            snapshot = sources.snapshot()
            digest = contracts.digest(snapshot)
            if digest != expected_digest:
                raise contracts.ContractError("revision_conflict", "Legacy Scheduler snapshot changed")
            path = str(sources.path.resolve())
            old = db.execute("SELECT document FROM legacy_scheduler_history WHERE requestKey=? OR sourcePath=?",
                             (request_key, path)).fetchone()
            if old:
                record = json.loads(old[0])
                if (record["requestKey"] != request_key or record["sourceDigest"] != digest
                        or record["sourcePath"] != path):
                    raise contracts.ContractError("idempotency_conflict", "Scheduler history import identity changed")
                return record
            record = {"schemaVersion": 2, "recordType": "legacy_scheduler_history",
                      "requestKey": request_key, "sourceDigest": digest, "sourcePath": path,
                      "ownerEpoch": self.owner_fence.token[0], "snapshot": copy.deepcopy(snapshot)}
            db.execute("INSERT INTO legacy_scheduler_history VALUES(?,?,?,?)",
                       (request_key, path, digest, _json(record)))
            return record

    def legacy_history(self, request_key):
        with self.connection(readonly=True) as db:
            row = db.execute("SELECT document FROM legacy_scheduler_history WHERE requestKey=?", (request_key,)).fetchone()
            return json.loads(row[0]) if row else None

    def adopt_legacy_pending(self, events, legacy_events, *, import_key, source_id, timezone_name):
        """Explicit R6 pending handoff; never a generic v1 publication port."""
        from .pending_adoption import PendingPublicationAdoption
        return PendingPublicationAdoption(self, events, legacy_events).adopt(
            import_key=import_key, source_id=source_id, timezone_name=timezone_name)

    def _request(self, db, key, fingerprint):
        old = db.execute("SELECT fingerprint,objectId FROM requests WHERE key=?", (key,)).fetchone()
        if not old:
            return None
        if old[0] != fingerprint:
            raise contracts.ContractError("idempotency_conflict", "requestKey belongs to another request")
        return old[1]

    def create(self, definition, *, request_key: str, creator_id=None, state="draft",
               created_by=None, owner=None, at=None, migration=False, activation=None):
        contracts.check_request_key(request_key)
        value = self.registry.validate_definition(definition, migration=migration)
        at = timestamp(at or self.clock())
        if state not in {"draft", "enabled", "disabled"}:
            raise ValueError("Invalid initial Creator state")
        fingerprint = contracts.request_digest("event-creator.create", value)
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            prior = self._request(db, request_key, fingerprint)
            if prior:
                return copy.deepcopy(self._read(db, prior))
            creator_id = creator_id or "creator_" + contracts.digest({"requestKey": request_key, "definition": value})[:24]
            if self._read(db, creator_id):
                raise contracts.ContractError("idempotency_conflict", "Creator ID already exists")
            provider = self.registry.get(value["providerRef"])
            limit = value["config"].get("driveLimit", {})
            item = {
                "schemaVersion": 2, "id": creator_id, "profileId": self.profile_id,
                **copy.deepcopy(value),
                "createdBy": copy.deepcopy(created_by or {"type": "user", "id": "local"}),
                "owner": copy.deepcopy(owner or {"type": "profile", "id": self.profile_id}),
                "createdAt": at, "updatedAt": at, "state": state, "stateRevision": 0,
                "revision": 1, "ownerEpoch": 1,
                "providerGeneration": value["providerRef"]["generation"],
                "cursor": {"rules": {}}, "drives": [], "pendingPublication": None,
                "producedCount": limit.get("initialProducedCount", 0),
                "health": provider.health(), "activation": activation or {"committed": True, "token": "standalone"},
                "settlementReceipts": {}, "lease": None,
            }
            item["cursor"] = provider.initial_cursor(item, at)
            self._write(db, item)
            db.execute("INSERT INTO requests VALUES(?,?,?)", (request_key, fingerprint, creator_id))
            return copy.deepcopy(item)

    def update(self, creator_id, definition, *, expected_revision: int, request_key: str, at=None):
        contracts.check_request_key(request_key)
        value = self.registry.validate_definition(definition)
        at = timestamp(at or self.clock())
        fingerprint = contracts.request_digest("event-creator.update", {"id": creator_id, "definition": value})
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            prior = self._request(db, request_key, fingerprint)
            if prior:
                item = copy.deepcopy(self._read(db, prior))
                item["affectedReferences"] = self._references(db, creator_id)
                return item
            item = self._read(db, creator_id)
            if item is None:
                raise KeyError(creator_id)
            contracts.check_revision(item["revision"], expected_revision)
            if item.get("pendingPublication"):
                raise contracts.ContractError("pending_publication", "Settle pending publication before editing")
            provider = self.registry.get(value["providerRef"])
            previous_rules = item["cursor"].get("rules", {})
            item.update(copy.deepcopy(value), revision=item["revision"] + 1, updatedAt=at,
                        providerGeneration=value["providerRef"]["generation"])
            item["cursor"] = provider.initial_cursor(item, at, previous_rules)
            item["health"] = provider.health()
            self._write(db, item)
            db.execute("INSERT INTO requests VALUES(?,?,?)", (request_key, fingerprint, creator_id))
            result = copy.deepcopy(item)
            result["affectedReferences"] = self._references(db, creator_id)
            return result

    def set_state(self, creator_id, state, *, expected_state_revision: int):
        if state not in {"enabled", "paused", "disabled", "retired"}:
            raise ValueError("Unsupported Creator state")
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read(db, creator_id)
            if item is None:
                raise KeyError(creator_id)
            if item["stateRevision"] != expected_state_revision:
                raise contracts.ContractError("revision_conflict", "Creator stateRevision changed")
            if state == "enabled" and not item.get("activation", {}).get("committed"):
                raise contracts.ContractError("reference_conflict", "Composition activation has not committed")
            item["state"] = state
            item["autoDisabled"] = False
            item["stateRevision"] += 1
            item["ownerEpoch"] += 1
            self._write(db, item)
            return copy.deepcopy(item)

    def commit_activation(self, creator_id, operation_id, token):
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read(db, creator_id)
            activation = item.get("activation") or {}
            if activation.get("operationId") not in (None, operation_id):
                raise contracts.ContractError("reference_conflict", "Creator belongs to another composition")
            item["activation"] = {"committed": True, "operationId": operation_id, "token": token}
            self._write(db, item)

    def activate_composition(self, creator_id, operation_id, token, enabled):
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read(db, creator_id)
            activation = item.get("activation") or {}
            if activation.get("operationId") != operation_id or activation.get("token") != token:
                raise contracts.ContractError("reference_conflict", "Unified activation token mismatch")
            item["activation"]["committed"] = True
            item["state"] = "enabled" if enabled else "disabled"
            item["stateRevision"] += 1
            item["ownerEpoch"] += 1
            self._write(db, item)

    def deactivate_composition(self, creator_id, operation_id):
        """Fence partial activation before a failed setup can be resumed/cancelled."""
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read(db, creator_id)
            activation = item.get("activation") or {}
            if activation.get("operationId") != operation_id:
                return
            item["activation"]["committed"] = False
            item["state"] = "disabled"
            item["autoDisabled"] = False
            item["stateRevision"] += 1
            item["ownerEpoch"] += 1
            self._write(db, item)

    @staticmethod
    def _references(db, creator_id):
        return [dict(row) for row in db.execute(
            "SELECT triggerId,creatorId,jobId,operationId FROM creator_links WHERE creatorId=? ORDER BY triggerId",
            (creator_id,),
        )]

    def references(self, creator_id):
        with self.connection(readonly=True) as db:
            return self._references(db, creator_id)

    def bind_link(self, creator_id, trigger_id, job_id, operation_id=None):
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read(db, creator_id)
            if item is None or item["state"] == "retired":
                raise contracts.ContractError("reference_conflict", "Creator cannot be referenced")
            old = db.execute("SELECT * FROM creator_links WHERE triggerId=? AND creatorId=?", (trigger_id, creator_id)).fetchone()
            values = (trigger_id, creator_id, job_id, operation_id)
            if old and tuple(old) != values:
                raise contracts.ContractError("reference_conflict", "Trigger link belongs to another graph")
            db.execute("INSERT OR REPLACE INTO creator_links VALUES(?,?,?,?)", values)

    def detach_operation_links(self, operation_id):
        with self.connection() as db:
            db.execute("DELETE FROM creator_links WHERE operationId=?", (operation_id,))

    def delete(self, creator_id, *, expected_revision: int):
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read(db, creator_id)
            if item is None:
                raise KeyError(creator_id)
            contracts.check_revision(item["revision"], expected_revision)
            cursor = item.get("lastPublication")
            if (item["state"] != "disabled" or item.get("pendingPublication") or item.get("lease")
                    or (cursor and not cursor.get("eventStoreReleased")) or self._references(db, creator_id)):
                raise contracts.ContractError("reference_conflict", "Creator still has state, references, lease, pending publication, or pin")
            db.execute("DELETE FROM creators WHERE id=?", (creator_id,))

    def _occurrence_ids(self, item, occurrence_key):
        identity = {"creatorId": item["id"], "revision": item["revision"], "occurrenceKey": occurrence_key}
        drive_id = "drive_" + contracts.digest(identity)[:48]
        return drive_id, "evt_" + contracts.digest({"driveId": drive_id})[:48]

    def reserve(self, creator_id, revision, epoch, occurrence_key, at=None):
        """Persist one actual drive and its immutable envelope before publication."""
        at = timestamp(at or self.clock())
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read(db, creator_id)
            if item is None:
                return None
            if item.get("pendingPublication"):
                pending_fence = item["pendingPublication"]["fence"]
                if revision != pending_fence["revision"] or epoch != pending_fence["epoch"]:
                    raise contracts.ContractError("revision_conflict", "Pending Creator reservation fence rejected")
                return copy.deepcopy(item["pendingPublication"])
            if item["state"] != "enabled" or not item.get("activation", {}).get("committed"):
                return None
            contracts.check_revision(item["revision"], revision)
            if item["ownerEpoch"] != epoch:
                raise contracts.ContractError("revision_conflict", "Creator ownerEpoch fence rejected")
            provider = self.registry.get(item["providerRef"])
            if item["providerGeneration"] != provider.generation:
                raise contracts.ContractError("provider_unavailable", "Provider generation fence rejected")
            if hasattr(provider, "refresh_host_cursor") and provider.refresh_host_cursor(item, at):
                self._write(db, item)
                return None
            limit = item["config"].get("driveLimit") if item["providerRef"]["type"] == "time" else None
            if limit and item["producedCount"] >= limit["maxEvents"]:
                item["state"] = "disabled"
                item["stateRevision"] += 1
                item["health"] = {"status": "limit_reached", "producedCount": item["producedCount"]}
                self._write(db, item)
                return None
            drive_id, event_id = self._occurrence_ids(item, occurrence_key)
            prepared = provider.prepare(item, occurrence_key, at, drive_id, len(item["drives"]) + 1)
            if prepared.get("disposition"):
                self._settle_without_event(db, item, prepared)
                return copy.deepcopy(prepared)
            drive = prepared["drive"]
            drive["eventId"] = event_id
            envelope = {
                "schemaVersion": 2, "eventId": event_id,
                "eventType": item["eventTypes"][0], "eventSchemaRevision": 1,
                "creatorId": creator_id, "creatorRevision": item["revision"],
                "occurrenceKey": occurrence_key, "occurredAt": at, "emittedAt": at,
                "payload": prepared["payload"],
            }
            contracts.validate_event_publish(envelope)
            pending = {
                "drive": drive, "envelope": envelope, "ruleId": prepared["ruleId"],
                "nextOccurrence": prepared.get("nextOccurrence"),
                "rule": copy.deepcopy(prepared.get("rule")),
                "fence": {"revision": revision, "epoch": epoch, "generation": item["providerGeneration"],
                          "driveId": drive_id},
            }
            item["drives"].append(drive)
            item["pendingPublication"] = pending
            self._write(db, item)
            return copy.deepcopy(pending)

    def _apply_cursor(self, item, rule_id, occurrence, drive_id):
        cursor = item["cursor"]["rules"][rule_id]
        cursor.update(nextFireAt=occurrence["at"] if occurrence else None,
                      nextOccurrence=copy.deepcopy(occurrence), lastSettledDriveId=drive_id)

    def _complete_if_done(self, item):
        if item["pendingPublication"] or any(c.get("nextFireAt") for c in item["cursor"]["rules"].values()):
            return
        item["state"] = "disabled"
        item["autoDisabled"] = True
        item["stateRevision"] += 1

    def _settle_without_event(self, db, item, prepared):
        drive = prepared["drive"]
        item["drives"].append(drive)
        self._apply_cursor(item, prepared["ruleId"], prepared.get("nextOccurrence"), drive["driveId"])
        item["health"] = {**item.get("health", {}), "lastOutcome": prepared["disposition"],
                          "lastScheduledAt": drive["scheduledAt"]}
        self._complete_if_done(item)
        self._write(db, item)

    def advance_paused(self, creator_id, epoch, occurrence_key, at=None):
        at = timestamp(at or self.clock())
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read_db(db, creator_id)
            if item["state"] != "paused" or item["ownerEpoch"] != epoch or item["config"].get("pausePolicy") != "advance":
                return None
            provider = self.registry.get(item["providerRef"])
            if hasattr(provider, "refresh_host_cursor") and provider.refresh_host_cursor(item, at):
                self._write(db, item)
                return None
            drive_id, unused_event_id = self._occurrence_ids(item, occurrence_key)
            prepared = provider.pause(item, occurrence_key, at, drive_id, len(item["drives"]) + 1)
            self._settle_without_event(db, item, prepared)
            return copy.deepcopy(prepared)

    def advance(self, creator_id, event_id, epoch, receipt: Mapping[str, Any]):
        """CAS pending eventId+epoch; only a matching persisted receipt settles."""
        if receipt.get("persisted") is not True:
            raise ValueError("Creator advance requires a persisted Event receipt")
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read_db(db, creator_id)
            pending = item.get("pendingPublication")
            if (item.get("pendingAdoption") and not item["pendingAdoption"]["publicationSettled"]
                    and not getattr(self._joined, "settling_adoption", False)):
                raise contracts.ContractError("unsupported_operation", "Use explicit pending adoption settlement")
            if not pending or pending["envelope"]["eventId"] != event_id:
                return False
            fence = pending["fence"]
            if fence["epoch"] != epoch:
                raise contracts.ContractError("revision_conflict", "Pending publication epoch mismatch")
            envelope = pending["envelope"]
            digest = contracts.event_fact_digest(envelope)
            if receipt.get("eventId") != event_id or receipt.get("factDigest") != digest:
                raise ValueError("Durable Event receipt identity mismatch")
            # Settlement uses only the frozen pending rule. A newer provider
            # generation may settle an accepted handle, but cannot reserve anew.
            occurrence = pending.get("nextOccurrence")
            rule = pending.get("rule")
            if rule and rule["type"] == "interval" and rule["advanceMode"] == "afterPublication":
                occurrence = TimeProvider().next_after(rule, receipt["receivedAt"])
            cursor = item["cursor"]["rules"][pending["ruleId"]]
            if (item["providerRef"]["type"] == "core" and cursor.get("nextFireAt")
                    and cursor["nextFireAt"] != pending["drive"]["scheduledAt"]):
                occurrence = copy.deepcopy(cursor["nextOccurrence"])
            drive = next(drive for drive in item["drives"] if drive["driveId"] == pending["drive"]["driveId"])
            if drive["outcome"] == "reserved":
                drive.update(outcome="published", receipt=copy.deepcopy(dict(receipt)))
                item["producedCount"] += 1
            self._apply_cursor(item, pending["ruleId"], occurrence, drive["driveId"])
            item["lastPublication"] = {"eventId": event_id, "factDigest": digest,
                                       "epoch": epoch, "eventStoreReleased": False}
            item["pendingPublication"] = None
            item["health"] = {**item.get("health", {}), "status": "available", "lastOutcome": "published"}
            limit = item["config"].get("driveLimit") if item["providerRef"]["type"] == "time" else None
            if limit and item["producedCount"] >= limit["maxEvents"]:
                item["state"] = "disabled"
                item["stateRevision"] += 1
                item["health"]["status"] = "limit_reached"
            else:
                self._complete_if_done(item)
            self._write(db, item)
            return True

    def acknowledged(self, creator_id, event_id, epoch):
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read_db(db, creator_id)
            if (item.get("pendingAdoption") and not item["pendingAdoption"]["publicationSettled"]
                    and not getattr(self._joined, "settling_adoption", False)):
                raise contracts.ContractError("unsupported_operation", "Use explicit pending adoption settlement")
            cursor = item.get("lastPublication")
            if cursor and cursor["eventId"] == event_id and cursor["epoch"] == epoch:
                cursor["eventStoreReleased"] = True
                self._write(db, item)
                return True
            return False

    def accept_settlement(self, creator_id, event):
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read_db(db, creator_id)
            provider = self.registry.get(item["providerRef"])
            if not hasattr(provider, "accept_settlement"):
                raise ValueError("Creator does not accept Job settlement")
            changed = provider.accept_settlement(item, copy.deepcopy(event))
            if changed:
                if item["state"] == "disabled" and item.get("autoDisabled"):
                    item["state"] = "enabled"
                    item["autoDisabled"] = False
                    item["stateRevision"] += 1
                    item["ownerEpoch"] += 1
                self._write(db, item)
            return changed

    def mark_health(self, creator_id, phase, error):
        with self.connection() as db:
            item = self._read_db(db, creator_id)
            if item:
                item["health"] = {**item.get("health", {}), "status": "degraded",
                                  "phase": phase, "error": str(error)[:1000]}
                self._write(db, item)

    def refresh_time_zone(self, creator_id, at):
        """Invalidate only unreserved calendar occurrences on host zone change."""
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            item = self._read_db(db, creator_id)
            provider = self.registry.get(item["providerRef"])
            if hasattr(provider, "refresh_host_cursor") and provider.refresh_host_cursor(item, at):
                self._write(db, item)
            return copy.deepcopy(item)

    @staticmethod
    def _read_db(db, creator_id):
        item = CreatorStore._read(db, creator_id)
        if item is None:
            raise KeyError(creator_id)
        return item

    def composition_for_key(self, request_key):
        with self.connection() as db:
            row = db.execute("SELECT document FROM compositions WHERE requestKey=?", (request_key,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_composition(self, operation):
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT document FROM compositions WHERE requestKey=?", (operation["requestKey"],)).fetchone()
            if old:
                existing = json.loads(old[0])
                if (existing["operationId"] != operation["operationId"]
                        or existing["requestDigest"] != operation["requestDigest"]):
                    raise contracts.ContractError("idempotency_conflict", "Composition requestKey changed")
            db.execute("INSERT OR REPLACE INTO compositions VALUES(?,?,?)",
                       (operation["operationId"], operation["requestKey"], _json(operation)))
            return copy.deepcopy(operation)


class CreatorRuntime:
    """One unified wake/tick surface for all providers; no per-Creator watchdog."""

    def __init__(self, creators: CreatorStore, bus: EventBus, diagnostics=None, authorize=None):
        self.creators = creators
        self.bus = bus
        self.diagnostics = diagnostics if diagnostics is not None else []
        self.authorize = authorize
        self.due_scanned = 0
        self.last_tick_at = None

    async def _ack(self, item):
        if item.get("pendingAdoption") and not item["pendingAdoption"]["publicationSettled"]:
            raise contracts.ContractError("unsupported_operation", "Use explicit pending adoption settlement")
        cursor = item.get("lastPublication")
        if cursor and not cursor.get("eventStoreReleased"):
            await self.bus.release_pin(cursor["eventId"])
            await asyncio.to_thread(self.creators.acknowledged, item["id"], cursor["eventId"], cursor["epoch"])

    async def _publish(self, creator_id, pending):
        if pending.get("adoptedIdentity"):
            raise contracts.ContractError("unsupported_operation", "Pending adoption cannot be automatically published")
        if self.authorize is not None:
            await asyncio.to_thread(self.authorize, await asyncio.to_thread(self.creators.get, creator_id))
        fence = pending["fence"]
        receipt = await self.bus.publish(copy.deepcopy(pending["envelope"]), fence)
        await asyncio.to_thread(self.creators.advance, creator_id, pending["envelope"]["eventId"], fence["epoch"], receipt)
        await self.bus.release_pin(pending["envelope"]["eventId"])
        await asyncio.to_thread(self.creators.acknowledged, creator_id, pending["envelope"]["eventId"], fence["epoch"])

    async def tick(self, *, at=None):
        at = timestamp(at or self.creators.clock())
        handled = 0
        try:
            items = await asyncio.to_thread(self.creators.list)
        except Exception as exc:
            self.diagnostics.append({"phase": "creator-store", "error": str(exc)[:1000]})
            return 0
        self.due_scanned += len(items)
        self.last_tick_at = at
        for item in items:
            try:
                if item.get("pendingAdoption") and not item["pendingAdoption"].get("publicationSettled"):
                    # Adoption is a handoff of facts, never permission to replay
                    # a v1 publication, advance its cursor, or fabricate an ack.
                    continue
                await self._ack(item)
                if item.get("pendingPublication"):
                    await self._publish(item["id"], item["pendingPublication"])
                    handled += 1
                    continue
                if item["state"] not in {"enabled", "paused"} or not item.get("activation", {}).get("committed"):
                    continue
                if self.authorize is not None:
                    await asyncio.to_thread(self.authorize, item)
                provider = self.creators.registry.get(item["providerRef"])
                item = await asyncio.to_thread(self.creators.refresh_time_zone, item["id"], at)
                occurrence_keys = provider.due_occurrences(item, at)
                for occurrence_key in occurrence_keys:
                    if item["state"] == "paused":
                        await asyncio.to_thread(self.creators.advance_paused, item["id"], item["ownerEpoch"], occurrence_key, at)
                        continue
                    pending = await asyncio.to_thread(
                        self.creators.reserve, item["id"], item["revision"], item["ownerEpoch"], occurrence_key, at,
                    )
                    if not pending or pending.get("disposition"):
                        continue
                    await self._publish(item["id"], pending)
                    handled += 1
            except Exception as exc:
                self.diagnostics.append({"phase": "creator", "creatorId": item["id"], "error": str(exc)[:1000]})
                try:
                    await asyncio.to_thread(self.creators.mark_health, item["id"], "drive", exc)
                except Exception:
                    pass
        return handled
