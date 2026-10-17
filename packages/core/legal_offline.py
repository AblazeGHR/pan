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
FIELDS = ("task_seq", "legal_offline", "offline_owner", "offline_run", "illegal_batches", "queue_pending",
          "queue_delivery_ledger", "queue_idempotency_index", "queue_revision")


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


def is_illegal(s) -> bool:
    return (getattr(s, "legal_offline", True) is False
            and (getattr(s, "offline_run", None) or {}).get("state") == "illegal")


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
    if (s.legal_offline is False and same_owner(run, identity)
            and run.get("state") == "running"
            and run.get("executionSeq") == identity["executionSeq"]):
        return
    if s.legal_offline is False and run and run.get("state") == "running":
        raise RuntimeError("Previous execution has not been safely finalized")
    s.offline_owner = dict(identity)
    s.offline_run = {**identity, "id": uuid.uuid4().hex, "state": "running",
                     "startedAt": time.time()}
    s.legal_offline = False


def finish(s, identity: dict, *, reason: str, state: str = "idle") -> bool:
    if not same_owner(s.offline_run, identity):
        return False
    if s.offline_run.get("executionSeq") != identity.get("executionSeq"):
        return False
    if not is_legal_offline(reason, state, {"safe": True}):
        return False
    if s.offline_run.get("state") == "illegal":
        return False
    s.legal_offline = True
    s.offline_run = {**s.offline_run, "state": state, "endedAt": time.time(), "reason": reason}
    return True


def detect(s, reason: str, *, eligible, remember) -> bool:
    run = s.offline_run
    if s.legal_offline is not False or not run:
        return False
    key = run["id"]
    if key in s.illegal_batches:
        return False  # Includes empty/recovered batches: never resnapshot a failed run.
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
    s.illegal_batches[key] = {"id": key, "run": copy.deepcopy(run), "messages": members,
                              "reason": reason, "createdAt": time.time(), "recovered": False}
    s.offline_run = {**run, "state": "illegal"}
    s.queue_revision += 1
    return True


def activate(s, identity: dict, *, remember, predecessors: set[str] | None = None) -> bool:
    """Called only with adapter readiness proof; preserve any admitted execution."""
    changed = False
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
        changed = True
    if not (same_owner(s.offline_run, identity) and s.legal_offline is False):
        s.legal_offline = True
        s.offline_run = {**identity, "id": uuid.uuid4().hex, "state": "idle"}
    if changed:
        s.queue_revision += 1
    return changed
