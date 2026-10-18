"""Durable execution legality and Illegal batches (mutations require queue exclusion).

No old legal-state field participates in these decisions. Persistence is owned
by worker.py's existing per-Session ordered writer, in the same JSON as queue
rows, receipts and batches. A run UUID survives Pan restarts; a Worker token
fences replacement objects and generations independently of reusable ids.
"""
from __future__ import annotations

import copy
import time
import uuid

import psutil

from . import queue_locks

SERVICE_ID = uuid.uuid4().hex
FIELDS = ("recovery_requested", "task_seq", "legal_offline", "offline_owner", "offline_run", "illegal_batches", "queue_pending",
          "queue_delivery_ledger", "queue_idempotency_index", "queue_revision",
          "legality_schema_version", "operation_revision", "execution_operations", "owner_retirements")


def recovery_needed(s) -> bool:
    """New metadata wins, even when False; legacy is read only compatibility."""
    if hasattr(s, "recovery_requested"):
        return s.recovery_requested
    return getattr(s, "last_legal_worker_state", None) == "running"


def recovery_intent(s) -> bool:
    return getattr(s, "recovery_requested", getattr(s, "last_legal_worker_state", None) == "running")


def snapshot(s) -> dict:
    return {key: copy.deepcopy(getattr(s, key)) for key in FIELDS}


def restore(s, values: dict) -> None:
    # Preserve live row references held by consumers/editors on failed writes.
    for key, value in values.items():
        current = getattr(s, key)
        if key == "queue_pending":
            old = {row.get("id"): row for row in current if isinstance(row, dict)}
            for index, row in enumerate(value):
                if isinstance(row, dict) and row.get("id") in old:
                    original = old[row.get("id")]
                    original.clear()
                    original.update(row)
                    value[index] = original
        setattr(s, key, value)
    attach_foreground(s)


def attach_foreground(s) -> None:
    run = s.offline_run
    if run and run.get("id") in s.execution_operations:
        s.offline_run = s.execution_operations[run["id"]]


def outstanding(s, identity: dict | None = None) -> list[dict]:
    return [op for op in getattr(s, "execution_operations", {}).values()
            if not op.get("safeTerminal") and not op.get("retirement", {}).get("safe")
            and (identity is None or same_owner(op, identity))]


def aggregate(s) -> None:
    s.legal_offline = not outstanding(s)


def migrate(s) -> None:
    """Import a stable v1 run once; v2 records always win over projections."""
    run = s.offline_run
    if s.legality_schema_version < 2 and run:
        record = copy.deepcopy(run)
        record.setdefault("kind", "foreground")
        record.setdefault("admittedRevision", s.operation_revision + 1)
        record["safeTerminal"] = bool(s.legal_offline and record.get("state") != "illegal")
        s.execution_operations.setdefault(record["id"], record)
        s.operation_revision = max(s.operation_revision, record["admittedRevision"])
    s.legality_schema_version = 2
    attach_foreground(s)
    aggregate(s)


def is_illegal(s) -> bool:
    return any(op.get("state") == "illegal" for op in outstanding(s))


def admit(s, identity: dict, *, kind: str, operation_id: str | None = None, **metadata) -> dict:
    key = operation_id or uuid.uuid4().hex
    old = s.execution_operations.get(key)
    if old:
        if not same_owner(old, identity) or old.get("kind") != kind:
            raise RuntimeError("Operation identity conflict")
        return old
    if any(same_owner(batch.get("run"), identity) for batch in s.illegal_batches.values()):
        raise RuntimeError("Execution owner already failed; a successor is required")
    s.operation_revision += 1
    record = {**identity, **metadata, "id": key, "kind": kind,
              "admittedRevision": s.operation_revision, "state": "running",
              "startedAt": time.time(), "safeTerminal": False}
    s.execution_operations[key] = record
    s.offline_owner = dict(identity)
    s.recovery_requested = True
    aggregate(s)
    return record


def finish_operation(s, operation_id: str, identity: dict, *, reason: str,
                     state: str = "idle", proof: str = "safe-terminal") -> bool:
    record = s.execution_operations.get(operation_id)
    if not same_owner(record, identity) or record.get("state") == "illegal":
        return False
    if record.get("safeTerminal"):
        return True
    record.update(state=state, endedAt=time.time(), reason=reason,
                  safeTerminal=True, terminalProof=proof)
    s.operation_revision += 1
    attach_foreground(s)
    aggregate(s)
    if s.legal_offline:
        s.recovery_requested = False
    return True


def retire_owner(s, identity: dict, *, reason: str, proof: dict) -> None:
    """Caller must establish proof for the whole captured runtime scope."""
    for record in outstanding(s, identity):
        if record.get("state") == "illegal":
            continue  # Incident recovery remains conditional on successor readiness.
        record.update(state="offline", retirement={"safe": True, "reason": reason,
                      "proof": copy.deepcopy(proof), "committedAt": time.time()})
        s.operation_revision += 1
    aggregate(s)
    if s.legal_offline:
        s.recovery_requested = False


def has_batches(s) -> bool:
    return any(not batch.get("recovered") for batch in s.illegal_batches.values())


def is_legal_offline(reason: str, next_state: str, context: dict) -> bool:
    """Central future Error policy seam. Captured Error/429 -> safe Idle is legal."""
    return bool(context.get("safe")) and next_state in {"idle", "offline", "held"}


def owner(w) -> dict:
    processes = []
    for handle in (w.process, w._mcp_proc):
        pid = getattr(handle, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            process = psutil.Process(pid)
            for candidate in [process, *process.children(recursive=True)]:
                processes.append({"pid": candidate.pid, "createdAt": candidate.create_time()})
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            processes.append({"pid": pid, "createdAt": None})
    expected = any(isinstance(getattr(handle, "pid", None), int)
                   and getattr(handle, "pid", 0) > 0
                   and getattr(handle, "returncode", None) is None
                   for handle in (w.process, w._mcp_proc))
    return {"runtimeExpected": expected, "token": w.offline_token, "generation": w.generation,
            "workerId": w.worker_id, "serviceId": SERVICE_ID,
            "executionSeq": w._execution_seq, "processes": processes}


def same_owner(run: dict | None, identity: dict) -> bool:
    return bool(run and run.get("token") == identity["token"]
                and run.get("generation") == identity["generation"])


def previous_owner_status(run: dict) -> str:
    """An untracked surviving provider is not permission to create another writer."""
    unknown = bool(run.get("ownershipUnknown")
                   or (run.get("runtimeExpected") and not run.get("processes")))
    for identity in run.get("processes", []):
        try:
            process = psutil.Process(identity["pid"])
            created = identity.get("createdAt")
            if created is None:
                unknown = True
            elif abs(process.create_time() - created) < .01 and process.is_running():
                return "alive"
        except psutil.NoSuchProcess:
            pass
        except (psutil.AccessDenied, KeyError, TypeError):
            unknown = True
    return "unknown" if unknown else "dead"


def begin(s, identity: dict) -> None:
    run = s.offline_run
    if any(not same_owner(op, identity) and op.get("state") != "illegal" for op in outstanding(s)):
        raise RuntimeError("Another owner retains execution responsibility")
    if (same_owner(run, identity) and run.get("state") == "running"
            and run.get("executionSeq") == identity["executionSeq"]):
        return
    if run and run.get("kind") == "foreground" and run in outstanding(s) and run.get("state") == "running":
        raise RuntimeError("Previous execution has not been safely finalized")
    s.offline_run = admit(s, identity, kind="foreground")


def finish(s, identity: dict, *, reason: str, state: str = "idle") -> bool:
    run = s.offline_run
    if not same_owner(run, identity) or run.get("executionSeq") != identity.get("executionSeq"):
        return False
    if not is_legal_offline(reason, state, {"safe": True}):
        return False
    return finish_operation(s, run["id"], identity, reason=reason, state=state)


def detect(s, reason: str, *, eligible, remember, identity: dict | None = None) -> bool:
    run = s.offline_run
    identity = identity or run or s.offline_owner
    cohort = sorted((op for op in outstanding(s, identity)
                     if op["id"] not in s.illegal_batches),
                    key=lambda op: (op["admittedRevision"], op["id"]))
    if not cohort:
        return False
    # A durable carrier incident is shared by all responsibilities of this
    # failed owner. Later EOFs cannot take another queue snapshot.
    key = cohort[0]["id"]
    members = []
    seen = set()
    for item in s.queue_pending:
        if not isinstance(item, dict) or not eligible(item):
            continue
        item_id = item.get("queueItemId") or item.get("id")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        queue_locks.set_lock(item, "illegal", batch=key)
        remember(item)
        members.append(item_id)
    for record in cohort:
        op_id = record["id"]
        s.illegal_batches[op_id] = {"id": op_id, "run": copy.deepcopy(record),
            "messages": members if op_id == key else [], "incidentId": key,
            "reason": reason, "createdAt": time.time(), "recovered": False}
        record.update(state="illegal", incidentId=key, reason=reason)
    s.operation_revision += 1
    attach_foreground(s)
    aggregate(s)
    s.queue_revision += 1
    return True


def activate(s, identity: dict, *, remember, predecessors: set[str] | None = None) -> bool:
    """Called only with adapter readiness proof; preserve any admitted execution."""
    changed = False
    previous_recovery = s.recovery_requested
    s.offline_owner = dict(identity)
    for batch in s.illegal_batches.values():
        if batch.get("recovered") or (predecessors is not None and batch["id"] not in predecessors):
            continue
        # All pending predecessors in this Session precede this activation.
        if batch["run"].get("token") == identity["token"]:
            continue
        members = set(batch["messages"])
        for item in s.queue_pending:
            if (isinstance(item, dict)
                    and (item.get("queueItemId") or item.get("id")) in members
                    and queue_locks.source(item) == "illegal"
                    and item.get("queueLockIllegalBatch") == batch["id"]):
                queue_locks.set_lock(item, None)
                remember(item)
        batch.update(recovered=True, recoveredAt=time.time(), recoveredBy=dict(identity))
        record = s.execution_operations.get(batch["id"])
        if record is not None and record.get("state") == "illegal":
            record["retirement"] = {"safe": True, "proof": "successor-ready-after-owner-reconciliation",
                                    "committedAt": time.time()}
        changed = True
    if not (same_owner(s.offline_run, identity) and s.legal_offline is False):
        s.offline_run = {**identity, "id": uuid.uuid4().hex, "state": "idle"}
    # A ready successor fences callbacks from old runtime tokens. Retain all
    # unfinished responsibility and incident tombstones, but no full safe
    # foreground history for every earlier Worker generation.
    removable = [key for key, op in s.execution_operations.items()
                 if not same_owner(op, identity) and op.get("safeTerminal")
                 and key not in s.illegal_batches]
    for key in removable:
        del s.execution_operations[key]
    aggregate(s)
    if s.legal_offline:
        s.recovery_requested = False
    if changed or removable or previous_recovery != s.recovery_requested:
        s.operation_revision += 1
    if changed:
        s.queue_revision += 1
    return changed


def diagnostics(s) -> dict:
    """False is unresolved responsibility; it does not imply a diagnosed incident."""
    return {"recoveryRequested": recovery_intent(s), "migrationUncertainty": s.offline_migration_uncertainty, "coverage": "external-owner-only" if (s.offline_owner or {}).get("externalTakeover") else "pan-managed",
            "revision": s.operation_revision,
            "outstanding": [{"operationId": op["id"], "kind": op.get("kind"),
                "state": op.get("state"), "reason": op.get("uncertainty") or op.get("reason")}
                for op in outstanding(s)]}
