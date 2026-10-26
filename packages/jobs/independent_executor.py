"""Canonical Attempt host: durable leases and receipts, never Job/Run creation.

The SQLite records below are executor receipts for caller-owned identities.
They are deliberately separate from the R4 legacy JobStore and its /jobs API.
A launch with no receipt is queried, never automatically launched again.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import time
from typing import Any, Mapping

from packages.jobs import final_contracts as contracts


class ExecutionConflict(ValueError):
    """Identity, lease, or irreversible-phase conflict; no effect admitted."""


def effect_id(key: Mapping[str, Any]) -> str:
    from packages.actions.typed_executor import _effect_key
    return "effect_" + contracts.digest(_effect_key(key))


def fence(attempt: Mapping[str, Any]) -> tuple:
    if not isinstance(attempt, Mapping):
        raise ValueError("attempt must be an object")
    for field in ("attemptId", "leaseId", "runId", "stepId"):
        if not isinstance(attempt.get(field), str) or not attempt[field]:
            raise ValueError(f"attempt.{field} is required")
    for field in ("ownerEpoch", "generation"):
        if type(attempt.get(field)) is not int or attempt[field] < 1:
            raise ValueError(f"attempt.{field} must be an integer >= 1")
    return tuple(attempt[field] for field in
                 ("attemptId", "leaseId", "runId", "stepId", "ownerEpoch", "generation"))


class ExecutionStore:
    """Atomic intent, supervisor phase, external receipt and ack persistence."""

    def __init__(self, root: str | Path):
        path = Path(root)
        if not path.is_absolute():
            raise ValueError("executor root must be absolute")
        self.root = path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "executions.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS executions (
                    effect_id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_outbox (
                    event_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    acked INTEGER NOT NULL DEFAULT 0);
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _load(db, identity):
        row = db.execute("SELECT document FROM executions WHERE effect_id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        return json.loads(row[0])

    @staticmethod
    def _save(db, doc):
        doc["updatedAt"] = time.time()
        db.execute("INSERT OR REPLACE INTO executions VALUES(?,?)",
                   (doc["effectId"], json.dumps(doc, ensure_ascii=False)))

    def get(self, identity):
        with self.connect() as db:
            return self._load(db, identity)

    def admit(self, *, job_id, attempt, effect_key, frozen_plan, retry=False):
        """Persist an admitted intent before dispatch; return (record, is_new)."""
        current_fence = fence(attempt)
        if attempt.get("state") != "running":
            raise ExecutionConflict("Attempt is not running")
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("canonical jobId is required")
        identity = effect_id(effect_key)
        if (attempt["runId"], attempt["stepId"]) != (effect_key["runId"], effect_key["stepId"]):
            raise ExecutionConflict("Attempt and effect identities differ")
        plan_digest = contracts.digest(dict(frozen_plan))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = self._load(db, identity)
            except KeyError:
                previous = None
            history = []
            if previous is not None:
                if previous["jobId"] != job_id or previous["planDigest"] != plan_digest:
                    raise ExecutionConflict("effectKey already belongs to a different Job or frozen plan")
                if fence(previous["attempt"]) == current_fence:
                    db.commit()
                    return previous, False
                result = previous.get("result") or {}
                safe_retry = (retry and result.get("outcome") == "rejected"
                              and result.get("receipt", {}).get("code") != "cancelled_before_effect"
                              and result.get("cleanupProof", {}).get("confirmed") is True
                              and attempt["attemptId"] != previous["attempt"]["attemptId"]
                              and all(item["attempt"]["attemptId"] != attempt["attemptId"] for item in previous["history"])
                              and attempt["generation"] > previous["attempt"]["generation"]
                              and attempt["ownerEpoch"] >= previous["attempt"]["ownerEpoch"])
                if not safe_retry:
                    raise ExecutionConflict("lease fence conflict; query/reconcile original effect before retry")
                history = previous["history"] + [{k: v for k, v in previous.items() if k != "history"}]
            doc = {"effectId": identity, "jobId": job_id, "attempt": dict(attempt),
                   "effectKey": dict(effect_key), "frozenPlan": copy.deepcopy(dict(frozen_plan)),
                   "planDigest": plan_digest, "phase": "admitted", "cancellable": True,
                   "claim": None, "result": None, "history": history, "phases": ["admitted"]}
            self._save(db, doc)
            db.commit()
            return doc, True

    @staticmethod
    def _check(doc, attempt, claim=None, *, require_claim=False):
        if fence(doc["attempt"]) != fence(attempt):
            raise ExecutionConflict("stale Attempt lease/epoch/generation")
        if require_claim and (not isinstance(claim, str) or not claim or doc["claim"] != claim):
            raise ExecutionConflict("supervisor claim lost")

    def claim(self, identity, attempt, supervisor_identity, *, bootstrap_receipt=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._load(db, identity)
            self._check(doc, attempt)
            if doc["claim"] or doc["result"] is not None:
                raise ExecutionConflict("effect already claimed or settled; never relaunch")
            if not supervisor_identity.get("pid") or not supervisor_identity.get("birth"):
                raise ExecutionConflict("supervisor process identity unavailable")
            doc.update(claim=secrets.token_hex(24), supervisorIdentity=dict(supervisor_identity), phase="preparing")
            if bootstrap_receipt is not None:
                doc["bootstrapReceipt"] = dict(bootstrap_receipt)
            doc["phases"].append("preparing")
            self._save(db, doc)
            db.commit()
            return doc

    def phase(self, identity, attempt, claim, phase, **evidence):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._load(db, identity)
            self._check(doc, attempt, claim, require_claim=True)
            if doc["result"] is not None:
                raise ExecutionConflict("execution already settled")
            transitions = ({"preparing": {"stopping"}, "stopping": {"stopped"},
                            "stopped": {"starting"}, "starting": {"checking_ready"}}
                           if doc["frozenPlan"].get("operation") in {"service.restart", "service.exit"}
                           else {"preparing": {"executing"}, "executing": {"running", "cleaning"},
                                 "running": {"cleaning"}})
            if phase not in transitions.get(doc["phase"], set()):
                raise ExecutionConflict("invalid or repeated execution phase")
            if doc["frozenPlan"].get("operation") == "service.exit" and phase == "starting":
                raise ExecutionConflict("exit execution cannot start a service")
            doc.update(phase=phase, cancellable=False)
            doc.setdefault("evidence", {}).update(evidence)
            doc["phases"].append(phase)
            self._save(db, doc)
            db.commit()
            return doc

    @classmethod
    def _settle(cls, db, doc, result, *, event_suffix=""):
        if result.get("effectKey") != doc["effectKey"] or result.get("outcome") not in {"succeeded", "rejected", "unknown", "partial"}:
            raise ExecutionConflict("invalid external receipt identity/outcome")
        doc.update(result=copy.deepcopy(result), cancellable=False)
        doc["phase"] = result.get("receipt", {}).get("phase", result["outcome"])
        doc["phases"].append(doc["phase"])
        cls._save(db, doc)
        event_id = doc["effectId"] + ":" + doc["attempt"]["attemptId"] + event_suffix
        event = {"eventId": event_id, "effectId": doc["effectId"], "jobId": doc["jobId"],
                 "attempt": doc["attempt"], "result": doc["result"]}
        db.execute("INSERT OR IGNORE INTO execution_outbox(event_id,payload) VALUES(?,?)",
                   (event_id, json.dumps(event)))

    def finish(self, identity, attempt, claim, result):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._load(db, identity)
            self._check(doc, attempt, claim, require_claim=True)
            if doc["result"] is not None:
                if doc["result"] != result:
                    raise ExecutionConflict("receipt already settled")
                return doc
            self._settle(db, doc, result)
            db.commit()
            return doc

    def cancel(self, identity, attempt):
        """Only pre-effect cancellation can be proven without a backend handle."""
        from packages.actions.typed_executor import EffectReceipt
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._load(db, identity)
            self._check(doc, attempt)
            if not doc["cancellable"] or doc["result"] is not None:
                raise ExecutionConflict("cancellation window closed; effect completion must be queried")
            result = EffectReceipt(doc["effectKey"], "rejected", {"code": "cancelled_before_effect", "phase": "cancelled"},
                                   {"confirmed": True, "effectStarted": False}).as_dict()
            self._settle(db, doc, result)
            db.commit()
            return doc

    def reconcile(self, identity, attempt, lookup):
        """Explicit trusted lookup of the ORIGINAL external identity, no replay.

        The host supplies a read-only provider/proof callback, never a payload
        that self-asserts success. Reconciled evidence gets a distinct outbox
        event, so an acked unknown notification cannot swallow the resolution.
        """
        snapshot = self.get(identity)
        self._check(snapshot, attempt)
        if (snapshot.get("result") or {}).get("outcome") not in {"unknown", "partial"}:
            return snapshot
        result = lookup(copy.deepcopy(snapshot))
        if (not isinstance(result, Mapping) or result.get("effectKey") != snapshot["effectKey"]
                or result.get("outcome") not in {"succeeded", "rejected", "partial"}):
            raise ExecutionConflict("reconciliation requires an authoritative original-effect receipt")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._load(db, identity)
            self._check(doc, attempt)
            if doc["result"] != snapshot["result"]:
                raise ExecutionConflict("receipt changed during external lookup")
            if doc["result"] == result:
                return doc
            doc.setdefault("reconciliationHistory", []).append(doc["result"])
            self._settle(db, doc, result, event_suffix=":reconciled:" + contracts.digest(dict(result)))
            db.commit()
            return doc

    def events(self):
        with self.connect() as db:
            return {"events": [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM execution_outbox WHERE acked=0 ORDER BY rowid")]}

    def ack(self, event_id):
        with self.connect() as db:
            if db.execute("UPDATE execution_outbox SET acked=1 WHERE event_id=?", (event_id,)).rowcount != 1:
                raise KeyError(event_id)
        return {"ok": True, "eventId": event_id}

    def launch_failed(self, identity, attempt):
        """Fence an ambiguous spawn without changing unrelated in-flight intents."""
        from packages.actions.typed_executor import EffectReceipt
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._load(db, identity)
            self._check(doc, attempt)
            if doc["claim"] is None and doc["result"] is None:
                self._settle(db, doc, EffectReceipt(doc["effectKey"], "unknown",
                    {"code": "launch_unconfirmed", "phase": "uncertain"}, {"confirmed": False}).as_dict())
            db.commit()
            return doc

    def recover(self, verify):
        """Read-only process probes. Lost supervisors become unknown, never retries.

        Intents without a claim also remain queryable: a launch may already be
        in flight. A delayed runner cannot claim after recovery settles unknown.
        """
        from packages.actions.typed_executor import EffectReceipt
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT document FROM executions").fetchall():
                doc = json.loads(row[0])
                if doc["result"] is not None:
                    continue
                identity = doc.get("supervisorIdentity")
                try:
                    alive = identity is not None and verify(identity) is True
                except Exception:
                    alive = False
                if alive:
                    continue
                self._settle(db, doc, EffectReceipt(doc["effectKey"], "unknown",
                    {"code": "supervisor_unconfirmed", "phase": "uncertain",
                     "evidence": doc.get("evidence", {}), "supervisorIdentity": identity},
                    {"confirmed": False}).as_dict())
            db.commit()


def launch_execution(root, identity):
    """Launch the fixed one-shot worker, with no inherited Pan profile/settings."""
    from packages.core.platform.execution_host import launch_detached
    return launch_detached([sys.executable, "-m", "packages.jobs.independent_execution_runner",
                            "--root", str(root), "--effect-id", identity], receipt_root=root)


class IndependentExecutor:
    """ExecutorPort adapter; HTTP clients and an in-process host share the port."""

    def __init__(self, *, job_id, client):
        self.job_id, self.client = job_id, client

    async def lookup_receipt(self, *, attempt, effect_key):
        """Query the original identity after owner restart; never submit again."""
        doc = await asyncio.to_thread(self.client.get_execution, effect_id(effect_key))
        if (doc.get("jobId") != self.job_id or doc.get("effectKey") != dict(effect_key)
                or fence(doc.get("attempt", {})) != fence(attempt)
                or doc.get("effectId") != effect_id(effect_key)):
            raise ExecutionConflict("recovered receipt identity/fence mismatch")
        result = doc.get("result")
        if result is not None and result.get("effectKey") != dict(effect_key):
            raise ExecutionConflict("recovered receipt effectKey mismatch")
        return result

    async def execute(self, *, attempt, effect_key, frozen_plan, retry=False):
        doc = await asyncio.to_thread(self.client.submit_execution, {
            "jobId": self.job_id, "attempt": dict(attempt), "effectKey": dict(effect_key),
            "frozenPlan": dict(frozen_plan), "retry": retry})
        if (doc.get("jobId") != self.job_id or doc.get("effectKey") != dict(effect_key)
                or fence(doc.get("attempt", {})) != fence(attempt)
                or doc.get("effectId") != effect_id(effect_key)
                or doc.get("planDigest") != contracts.digest(dict(frozen_plan))):
            raise ExecutionConflict("executor response does not match the admitted identity/fence/plan")
        if doc.get("result") is not None:
            if doc["result"].get("effectKey") != dict(effect_key):
                raise ExecutionConflict("external receipt effectKey mismatch")
            return doc["result"]
        # ExecutorPort has only final outcomes: accepted/pending is NOT success.
        from packages.actions.typed_executor import EffectReceipt
        return EffectReceipt(dict(effect_key), "unknown", {
            "code": "execution_pending", "effectId": doc["effectId"],
            "attemptId": attempt["attemptId"], "phase": doc["phase"]}).as_dict()


class ExecutionHost:
    """New canonical admission, independent of legacy JobStore.create/retry."""

    def __init__(self, store: ExecutionStore, launch=launch_execution):
        self.store, self.launch = store, launch

    def execution_events(self):
        return self.store.events()

    def ack_execution(self, event_id):
        return self.store.ack(event_id)

    def get_execution(self, identity):
        return self.store.get(identity)

    def cancel_execution(self, identity, attempt):
        return self.store.cancel(identity, attempt)

    def submit_execution(self, request):
        if not isinstance(request.get("frozenPlan"), dict) or type(request.get("retry", False)) is not bool:
            raise ValueError("frozenPlan must be an object and retry must be boolean")
        operation = request.get("frozenPlan", {}).get("operation")
        if operation not in {"process", "background-process", "command"}:
            raise ValueError("independent public admission supports typed process only")
        if request["frozenPlan"].get("effect") == "high_risk":
            raise ValueError("protected effects require the lifecycle supervisor admission")
        # Validate process arguments before persisting or launching a worker.
        args = request["frozenPlan"].get("arguments", request["frozenPlan"])
        if (not isinstance(args, Mapping) or not isinstance(args.get("argv"), list) or not args["argv"]
                or not args["argv"][0]
                or any(not isinstance(item, str) or "\x00" in item for item in args["argv"])
                or not isinstance(args.get("cwd"), str) or not Path(args["cwd"]).is_absolute()
                or not Path(args["cwd"]).is_dir()
                or type(args.get("hideWindow", True)) is not bool
                or args.get("shell", False) is not False or "command" in args):
            raise ValueError("invalid typed process arguments")
        doc, created = self.store.admit(job_id=request["jobId"], attempt=request["attempt"],
            effect_key=request["effectKey"], frozen_plan=request["frozenPlan"], retry=request.get("retry", False))
        if created:
            try:
                self.launch(self.store.root, doc["effectId"])
            except Exception:
                # The spawn may have happened: never retry a failed launch.
                doc = self.store.launch_failed(doc["effectId"], doc["attempt"])
        return doc


async def recover_execution_events(client, persist):
    """Ack after the canonical consumer durably applies the fenced receipt.

    The consumer must be idempotent by eventId, including ack-loss recovery.
    Refused/late/stale receipts remain unacked for explicit reconciliation.
    """
    count = 0
    events = await asyncio.to_thread(client.execution_events)
    for event in events["events"]:
        if await persist(copy.deepcopy(event)) is True:
            await asyncio.to_thread(client.ack_execution, event["eventId"])
            count += 1
    return count
