"""Canonical registry, metadata and runtime ports.

The JSON store is the pre-cutover compatibility backend. Activated profiles use
FinalService's aggregate store and its owner-fenced SQLite transaction. Callers
must obtain this facade from the owning service (``service.final``); constructing
a JSON store never grants the canonical owner's authority.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import weakref
from typing import Any, Callable, Mapping, Protocol

from packages.jobs import final_contracts as contracts
from packages.jobs.runtime_owner import OwnerFence, legacy_writer

from .ledger import Ledger, aggregate, now, public
from .executor import TypedExecutor


REGISTRY_LOCK = threading.RLock()
_joined = threading.local()
_lock_guard = threading.Lock()
_thread_locks = weakref.WeakValueDictionary()


@contextmanager
def registry_lock(root, key):
    """Reentrant local/cross-process exclusion, using SQLite on every OS.

    Never remove a lock database: doing so would split concurrent writers. The
    owner fence serializes cutover before these short storage transactions.
    """
    root = Path(root).resolve()
    directory = root / "jobs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (".lock-" + contracts.digest(str(key)) + ".sqlite3")
    identity = str(path)
    with _lock_guard:
        mutex = _thread_locks.setdefault(identity, threading.RLock())
    with mutex:
        joined = getattr(_joined, "locks", set())
        if identity in joined:
            yield
            return
        db = sqlite3.connect(path, timeout=30)
        try:
            db.execute("BEGIN IMMEDIATE")
            _joined.locks = joined | {identity}
            yield
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            _joined.locks = joined
            db.close()


def atomic_write(path, value):
    """Durable atomic replace; a failed commit never truncates the old record."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + "." + secrets.token_hex(4) + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)


class PinMetadataUnavailable(ValueError):
    """Unreadable pins must never be interpreted as an empty set for GC."""


class JsonPins:
    """Compatibility metadata backend, fenced once a profile has an owner."""
    def __init__(self, root):
        self.root = Path(root).resolve()

    def read(self):
        try:
            value = json.loads((self.root / "pins.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict) or any(type(v) is not bool for v in value.values()):
            raise PinMetadataUnavailable("invalid Job pin metadata")
        return value

    def set(self, job_id, pinned):
        if type(pinned) is not bool:
            raise ValueError("pinned must be boolean")
        owner = OwnerFence(self.root.parent)
        with owner.legacy_pass(), registry_lock(self.root, "pins"), owner.writer():
            try:
                value = self.read()
            except (OSError, ValueError, UnicodeError) as exc:
                raise PinMetadataUnavailable("Job pin metadata unavailable; restore it before editing pins") from exc
            if pinned:
                value[job_id] = True
            else:
                value.pop(job_id, None)
            atomic_write(self.root / "pins.json", value)
        return pinned


class ExecutorPort(Protocol):
    """WP05 boundary: one fenced Attempt, never a new Job or Run."""

    async def execute(
        self,
        *,
        attempt: Mapping[str, Any],
        effect_key: Mapping[str, Any],
        frozen_plan: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


# Default implementation exported beside the port so callers can depend on
# one canonical executor without constructing a second Job/Run coordinator.
EffectExecutor = TypedExecutor


class TriggerAdmissionPort(Protocol):
    """WP03 boundary: Trigger matching produces intents, not effects."""

    async def admit(self, event: Mapping[str, Any], target: Mapping[str, Any]) -> Mapping[str, Any]: ...


class JsonRegistryStore:
    """Atomic compatibility storage; never an escape from the owner fence."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def path(self, job_id):
        if not isinstance(job_id, str) or not job_id.startswith("job_") or Path(job_id).name != job_id:
            raise ValueError("invalid job id")
        return self.root / "jobs" / (job_id + ".json")

    def get(self, job_id: str) -> dict[str, Any]:
        try:
            job = json.loads(self.path(job_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise KeyError(job_id)
        return job

    def list(self):
        return [self.get(path.stem) for path in sorted((self.root / "jobs").glob("job_*.json"))]

    def mutate(self, job_id: str, change: Callable[[dict[str, Any]], Any]) -> Any:
        owner = OwnerFence(self.root.parent)
        with owner.legacy_pass(), registry_lock(self.root, "job:" + job_id), owner.writer():
            job = self.get(job_id)
            result = change(job)
            atomic_write(self.path(job_id), job)
            return copy.deepcopy(result)

    def compare_and_swap(self, job_id, expected_revision, change):
        def checked(job):
            contracts.check_revision(job["configRevision"], expected_revision)
            return change(job)
        return self.mutate(job_id, checked)

    def publish(self, job, *, create=False, publisher=None):
        """One owner/lock boundary for compatibility create and save."""
        job_id = job["jobId"]
        self.path(job_id).parent.mkdir(parents=True, exist_ok=True)
        owner = OwnerFence(self.root.parent)
        with owner.legacy_pass(), registry_lock(self.root, "job:" + job_id), owner.writer():
            path = self.path(job_id)
            if create and path.exists():
                raise ValueError("job id already exists")
            if publisher is None:
                atomic_write(path, job)
            else:
                publisher(path, job, created=create)
        return job

    def update(self, job_id, changes, *, replace=None, publisher=None):
        owner = OwnerFence(self.root.parent)
        with owner.legacy_pass(), registry_lock(self.root, "job:" + job_id), owner.writer():
            try:
                current = copy.deepcopy(replace) if replace is not None else self.get(job_id)
            except KeyError as exc:
                raise ValueError("job not found") from exc
            current.update(changes)
            if publisher is None:
                atomic_write(self.path(job_id), current)
            else:
                publisher(self.path(job_id), current)
            return current


class FinalRuntime:
    """Query/control facade for the canonical records in one Job aggregate."""

    def __init__(self, root: str | Path, store: JsonRegistryStore | None = None):
        self.root = Path(root).resolve()
        self.store = store or JsonRegistryStore(self.root)
        self.ledger = Ledger()

    def _jobs(self, *, archived=False):
        """Read through the owning store, including retained provenance."""
        service = getattr(self.store, "service", None)
        if service is None:
            return self.store.list()
        with service.transaction(readonly=True) as db:
            result = service._all(db, "job")
            if archived:
                result += service._all(db, "runtime_archive")
            return copy.deepcopy(result)

    def compare_and_swap(self, job_id, expected_revision, change):
        """Same owner fence and storage transaction as admission/receipts."""
        def checked(job):
            contracts.check_revision(job["configRevision"], expected_revision)
            return change(job)
        return self.store.mutate(job_id, checked)

    def metadata(self, job_id):
        job = self.store.get(job_id)
        metadata = copy.deepcopy(job.get("runtimeMetadata", {}))
        metadata.setdefault("revision", 0)
        pinned = metadata.get("pinned", job.get("config", {}).get("retention", {}).get("pinned", False))
        if type(pinned) is not bool:
            raise PinMetadataUnavailable("invalid canonical pin metadata")
        return {**metadata, "pinned": pinned}

    def set_pin(self, job_id, pinned, *, expected_revision):
        """Metadata CAS is independent from the immutable Run/config snapshot."""
        if type(pinned) is not bool:
            raise contracts.ContractError("invalid_schema", "pinned must be boolean")
        def change(job):
            metadata = job.setdefault("runtimeMetadata", {})
            contracts.check_revision(metadata.get("revision", 0), expected_revision)
            metadata.update(pinned=pinned, revision=expected_revision + 1)
            return metadata
        return self.store.mutate(job_id, change)

    def register_original(self, job_id, original_id, *, source_digest, source_path, expected_revision):
        """Attach reviewed provenance; no legacy loader or implicit execution.

        P2/P3 import adapters supply the original ID and reviewed hash/path from
        their migration manifest. A conflicting mapping is never overwritten.
        """
        if (not isinstance(original_id, str) or not original_id or not isinstance(source_path, str) or not source_path
                or not isinstance(source_digest, str) or len(source_digest) != 64
                or any(c not in "0123456789abcdef" for c in source_digest)):
            raise contracts.ContractError("invalid_schema", "Reviewed source ID/path/SHA-256 required")
        value = {"originalId": original_id, "sourceDigest": source_digest, "sourcePath": source_path}
        def change(job):
            originals = job.setdefault("runtimeMetadata", {}).setdefault("originals", {})
            if original_id in originals and originals[original_id] != value:
                raise contracts.ContractError("reference_conflict", "Original provenance differs")
            originals[original_id] = value
            return value
        return self.compare_and_swap(job_id, expected_revision, change)

    def query_original(self, original_id):
        """Resolve Job/Run/Attempt/event/notification IDs without resubmission.

        Returns all matching Jobs rather than guessing when IDs are ambiguous.
        Unknown/pending admissions are included with the original effect key.
        """
        matches = []
        for job in self._jobs(archived=True):
            state = aggregate(job, self.root)
            metadata = job.get("runtimeMetadata", {})
            full = original_id == job["jobId"] or original_id in metadata.get("originals", {})
            run_ids = {rid for rid in state["runs"] if rid == original_id}
            run_ids.update(a["runId"] for a in state["admissions"].values()
                           if original_id in {a.get("admissionId"), a.get("cause", {}).get("eventId")})
            run_ids.update(a["runId"] for a in state["attempts"].values() if a["attemptId"] == original_id)
            run_ids.update(row["runId"] for row in state["outbox"].values()
                           if original_id in {row.get("legacyId"), row["outboxId"]})
            for row in list(state["effects"]) + list(state["effectAdmissions"].values()):
                if original_id in {contracts.digest(row["effectKey"]), "effect_" + contracts.digest(row["effectKey"])}:
                    run_ids.add(row["effectKey"]["runId"])
            effects = [copy.deepcopy(e) for e in state["effects"] if full or e["effectKey"]["runId"] in run_ids]
            pending = [copy.deepcopy(e) for e in state["effectAdmissions"].values()
                       if full or e["effectKey"]["runId"] in run_ids]
            if full or run_ids:
                matches.append({"jobId": job["jobId"], "originalId": original_id,
                    "provenance": copy.deepcopy(metadata.get("originals", {})), "archived": metadata.get("archived", False),
                    "runs": [copy.deepcopy(r) for r in state["runs"].values() if full or r["runId"] in run_ids],
                    "effects": effects, "pendingEffects": pending})
        return matches

    def history(self, job_id):
        """Detached canonical history, available after live definition deletion."""
        job = next((j for j in self._jobs(archived=True) if j["jobId"] == job_id), None)
        if job is None:
            raise KeyError(job_id)
        state = aggregate(job, self.root)
        return copy.deepcopy({"jobId": job_id, "runs": list(state["runs"].values()),
            "events": [a["cause"] for a in state["admissions"].values() if a["cause"].get("type") == "event"],
            "effects": state["effects"], "outbox": list(state["outbox"].values()),
            "cancellations": job.get("runtimeMetadata", {}).get("cancellations", {})})

    @staticmethod
    def legacy_history(root, *, task_id=None, limit=100):
        """Read archived JSONL facts without importing or rewriting old Jobs."""
        return read_legacy_history(root, task_id=task_id, limit=limit)

    def handoff(self, job_id, old_session_id, new_session_id, *, expected_revision, request_key):
        """Retarget future typed Session literals; frozen Runs/notices stay put."""
        if not all(isinstance(v, str) and v for v in (old_session_id, new_session_id, request_key)):
            raise contracts.ContractError("invalid_schema", "Session IDs and request key required")
        payload = {"old": old_session_id, "new": new_session_id, "expectedRevision": expected_revision}
        def change(job):
            metadata = job.setdefault("runtimeMetadata", {})
            receipts = metadata.setdefault("handoffs", {})
            if request_key in receipts:
                if receipts[request_key]["payload"] != payload:
                    raise contracts.ContractError("idempotency_conflict", "Handoff key payload differs")
                return receipts[request_key]
            contracts.check_revision(job["configRevision"], expected_revision)
            changed = False
            def replace(value):
                nonlocal changed
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in {"sessionId", "targetSessionId"}:
                            if item == old_session_id:
                                value[key] = new_session_id
                                changed = changed or old_session_id != new_session_id
                            elif isinstance(item, dict) and item.get("from") == "literal" and item.get("value") == old_session_id:
                                item["value"] = new_session_id
                                changed = changed or old_session_id != new_session_id
                        elif key in {"sessionIds", "targetSessionIds"}:
                            target = item.get("value") if isinstance(item, dict) and item.get("from") == "literal" else item
                            if isinstance(target, list):
                                replaced = list(dict.fromkeys(new_session_id if s == old_session_id else s for s in target))
                                changed = changed or target != replaced
                                if isinstance(item, dict):
                                    item["value"] = replaced
                                else:
                                    value[key] = replaced
                        else:
                            replace(item)
                elif isinstance(value, list):
                    for item in value:
                        replace(item)
            replace(job["config"])
            # Compatibility target fields are live selectors too, never history.
            for key in ("targetSessionId", "targetSessionIds", "targetStruct"):
                if key in job:
                    target = {key: job[key]}
                    replace(target)
                    job[key] = target[key]
            job["configRevision"] += int(changed)
            if changed and "canonical" in job:
                job["canonical"].update(copy.deepcopy(job["config"]), configRevision=job["configRevision"], updatedAt=now())
            receipt = {"payload": payload, "jobId": job_id, "changed": changed, "configRevision": job["configRevision"], "at": now()}
            receipts[request_key] = receipt
            return receipt
        return self.store.mutate(job_id, change)

    def settle_notification(self, job_id, original_key, *, receipt, outbox_id=None):
        """Settle by original key, optionally narrowed to one recipient row."""
        return self.store.mutate(job_id, lambda job: self.ledger.settle_notification(
            job, self.root, original_key, receipt=receipt, outbox_id=outbox_id))

    @staticmethod
    def deletion_blockers(job, *, pinned=False):
        """Fail closed for every kind, regardless of its top-level projection."""
        blockers = []
        metadata = job.get("runtimeMetadata", {})
        pin = metadata.get("pinned", job.get("config", {}).get("retention", {}).get("pinned", pinned))
        if type(pin) is not bool or type(pinned) is not bool:
            blockers.append("pin_metadata_unknown")
        elif pin or pinned:
            blockers.append("pinned")
        if job.get("status") in {"starting", "running", "unknown", "uncertain", "cleanup_pending", "cleaning", "deleting"}:
            blockers.append("active_or_unknown")
        if job.get("execution") or job.get("activeExecutions") or job.get("pendingRuns"):
            blockers.append("active_resources")
        if job.get("cleanupPending") or job.get("unknownOwnership"):
            blockers.append("cleanup_or_ownership_unknown")
        state = job.get("finalRuntime", {})
        if any(r["state"] not in {"completed", "failed", "cancelled", "skipped"} for r in state.get("runs", {}).values()):
            blockers.append("active_or_unknown_run")
        if any(a["state"] not in {"succeeded", "failed", "cancelled"} for a in state.get("attempts", {}).values()):
            blockers.append("active_or_unknown_attempt")
        if any(row.get("status") != "confirmed" for row in metadata.get("cancellations", {}).values()):
            blockers.append("unsettled_cancellation")
        if state.get("effectAdmissions") or any(e["outcome"] in {"unknown", "partial"} for e in state.get("effects", [])):
            blockers.append("unsettled_effect")
        if job.get("terminalOutbox") or any(row["status"] not in {"acked", "suppressed"} for row in state.get("outbox", {}).values()):
            blockers.append("unacknowledged_outbox")
        return blockers

    def delete(self, job_id, *, expected_revision, expected_digest=None):
        """Archive settled history and remove only the live canonical definition."""
        service = getattr(self.store, "service", None)
        if service is None:
            raise contracts.ContractError("unsupported_operation", "Canonical service store required for deletion")
        with service.transaction() as db:
            job = service._get(db, "job", job_id)
            contracts.check_revision(job["configRevision"], expected_revision)
            if expected_digest is not None and contracts.digest(job) != expected_digest:
                raise contracts.ContractError("revision_conflict", "Retention candidate changed")
            blockers = self.deletion_blockers(job)
            if job["canonical"]["state"] not in {"disabled", "retired"}:
                blockers.append("definition_enabled")
            if any(t.get("targetJobId") == job_id and t.get("state") != "retired" for t in service._all(db, "trigger")):
                blockers.append("referenced_by_trigger")
            if blockers:
                raise contracts.ContractError("reference_conflict", "Cannot delete Job: " + ", ".join(blockers))
            job.setdefault("runtimeMetadata", {}).update(archived=True, archivedAt=now())
            service._save(db, "runtime_archive", job_id, job)
            db.execute("DELETE FROM final_documents WHERE kind='job' AND id=?", (job_id,))
            return {"jobId": job_id, "deleted": True, "historyRetained": True}

    def retention(self, *, retention_days, at=None):
        """Explicit scan; every candidate is rechecked in its delete transaction."""
        if type(retention_days) is not int or retention_days <= 0:
            raise contracts.ContractError("invalid_schema", "Positive retention days required")
        from datetime import datetime, timezone
        point = time.time() if at is None else float(at)
        if not math.isfinite(point):
            raise contracts.ContractError("invalid_schema", "Finite retention time required")
        cutoff = point - retention_days * 86400
        result = {"scanned": 0, "deleted": [], "retained": {}}
        for job in self._jobs():
            result["scanned"] += 1
            try:
                raw = job.get("updatedAt", job.get("canonical", {}).get("updatedAt"))
                if type(raw) in (int, float):
                    stamp = float(raw)
                else:
                    wall = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if wall.tzinfo is None:
                        raise ValueError("Retention timestamp timezone unknown")
                    stamp = wall.astimezone(timezone.utc).timestamp()
                if not math.isfinite(stamp) or stamp > cutoff:
                    continue
                self.delete(job["jobId"], expected_revision=job["configRevision"], expected_digest=contracts.digest(job))
                result["deleted"].append(job["jobId"])
            except (contracts.ContractError, KeyError, TypeError, ValueError, AttributeError, OverflowError, OSError) as exc:
                result["retained"][job["jobId"]] = str(exc)
        return result

    async def cancel_resource(self, job_id, attempt_id, *, executor, expected_epoch, expected_generation):
        """Use only the original admitted key/fence; unknown is never success."""
        job = self.store.get(job_id)
        state = aggregate(job, self.root)
        attempt = copy.deepcopy(state["attempts"][attempt_id])
        if (type(expected_epoch) is not int or type(expected_generation) is not int
                or (attempt["ownerEpoch"], attempt["generation"]) != (expected_epoch, expected_generation)):
            raise contracts.ContractError("revision_conflict", "Cancellation Attempt fence differs")
        admitted = next((e for e in state["effectAdmissions"].values() if e["attemptId"] == attempt_id), None)
        if admitted is None or attempt["state"] != "running":
            raise contracts.ContractError("cancellation_window_closed", "No active original effect admission")
        executor = getattr(executor, "independent_executor", None) or executor
        cancel = getattr(executor, "cancel_effect", None)
        if cancel is None:
            raise contracts.ContractError("unsupported_operation", "Executor cannot cancel live resources")
        key = copy.deepcopy(admitted["effectKey"])
        # Validate owner authority before any external cancellation, and reserve
        # the original identity durably. Do not hold a storage lock across await.
        def reserve(current):
            current_state = aggregate(current, self.root)
            if current_state["attempts"][attempt_id] != attempt or current_state["effectAdmissions"].get(contracts.digest(key)) != admitted:
                raise contracts.ContractError("revision_conflict", "Cancellation admission changed")
            cancellations = current.setdefault("runtimeMetadata", {}).setdefault("cancellations", {})
            if attempt_id in cancellations:
                raise contracts.ContractError("reconciliation_required", "Query original cancellation; do not resend")
            cancellations[attempt_id] = {"effectKey": key, "status": "sending", "at": now()}
        self.store.mutate(job_id, reserve)
        try:
            receipt = await cancel(attempt=attempt, effect_key=key)
        except Exception:
            receipt = {"effectKey": key, "outcome": "unknown"}
        if not isinstance(receipt, dict) or receipt.get("effectKey") != key:
            receipt = {"effectKey": key, "outcome": "unknown"}
        def settle(current):
            row = current["runtimeMetadata"]["cancellations"][attempt_id]
            payload = receipt.get("receipt")
            proof = payload.get("cleanupProof") if isinstance(payload, dict) else None
            confirmed = receipt.get("outcome") == "rejected" and isinstance(proof, dict) and proof.get("confirmed") is True
            row.update(status="confirmed" if confirmed else "uncertain", receipt=copy.deepcopy(receipt))
            return row
        return self.store.mutate(job_id, settle)

    def state(self, job: dict[str, Any]) -> dict[str, Any]:
        return public(aggregate(job, self.root))

    def get(self, job_id: str) -> dict[str, Any]:
        return self.state(self.store.get(job_id))

    def run(self, job_id: str, run_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.ledger.run(self.store.get(job_id), self.root, run_id))

    def runs(self, job_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(list(aggregate(self.store.get(job_id), self.root)["runs"].values()))

    def operation(self, job_id: str, operation_id: str) -> dict[str, Any]:
        return copy.deepcopy(aggregate(self.store.get(job_id), self.root)["operations"][operation_id])

    def operation_for_key(self, job_id: str, operation: str, request_key: str, authority: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        job = self.store.get(job_id)
        state = aggregate(job, self.root)
        from .ledger import principal
        scope = contracts.idempotency_scope(principal(authority), state["profileId"], operation)
        index = contracts.digest({"scope": scope, "requestKey": request_key})
        operation_id = state["operationIndex"].get(index)
        return copy.deepcopy(state["operations"].get(operation_id)) if operation_id else None

    def admit(self, job_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.store.mutate(job_id, lambda job: self.ledger.admit(job, self.root, **kwargs))

    def begin_operation(self, job_id: str, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        return self.store.mutate(job_id, lambda job: self.ledger.begin_operation(job, self.root, **kwargs))

    def apply_receipt(
        self,
        job_id: str,
        attempt_id: str,
        *,
        owner_epoch: int,
        generation: int,
        outcome: str,
        receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """WP05 fenced receipt entry; late/wrong-epoch receipts never settle."""

        def change(job: dict[str, Any]) -> dict[str, Any]:
            attempt = self.ledger.finish_attempt(
                job,
                self.root,
                attempt_id,
                outcome,
                expected_epoch=owner_epoch,
                expected_generation=generation,
            )
            self.ledger.record_effect(job, self.root, attempt_id, outcome, receipt)
            return attempt

        return self.store.mutate(job_id, change)

    def reconcile_effect(self, job_id: str, effect_key: Mapping[str, Any], outcome: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        if outcome not in {"succeeded", "rejected", "partial"}:
            raise contracts.ContractError("invalid_schema", "Reconciliation outcome must be terminal or partial")

        def change(job: dict[str, Any]) -> dict[str, Any]:
            state = aggregate(job, self.root)
            matches = [item for item in state["effects"] if item["effectKey"] == dict(effect_key)]
            if not matches:
                raise KeyError("effect")
            effect = matches[-1]
            contracts.check_transition("effect", effect["outcome"], outcome, operation="reconcile")
            effect["outcome"] = outcome
            effect["receipt"] = copy.deepcopy(dict(receipt))
            effect["updatedAt"] = contracts.check_timestamp(now(), "updatedAt")
            contracts.validate_record("effect", effect)
            run_id = effect["effectKey"]["runId"]
            step_id = effect["effectKey"]["stepId"]
            step = state["steps"][run_id][step_id]
            if step["state"] == "uncertain" and outcome != "partial":
                target = "completed" if outcome == "succeeded" else "failed"
                contracts.check_transition("step", step["state"], target, operation="reconcile")
                step["state"] = target
                step["stateRevision"] += 1
                contracts.validate_record("step", step)
            run = state["runs"][run_id]
            if run["state"] != "uncertain" or outcome == "partial":
                return copy.deepcopy(effect)
            steps = state["steps"][run_id]
            target = "failed" if outcome == "rejected" else "completed" if all(item["state"] == "completed" for item in steps.values()) else "retry_wait"
            self.ledger.transition_run(job, self.root, run_id, target, operation="reconcile")
            summary = copy.deepcopy(job.get("runSummaries", {}).get(run_id) or
                (job.get("lastExecution") if job.get("lastExecution", {}).get("runId") == run_id else None) or
                {"id": "reconcile:" + run_id, "runId": run_id, "notificationPolicy": state["plans"][run_id].get("notification", {})})
            legacy_steps = summary.get("stepExecutions", [])
            for legacy_step in legacy_steps:
                canonical = steps.get(legacy_step.get("stepId"))
                if canonical is None or legacy_step.get("stepId") != step_id:
                    continue
                legacy_step["status"] = canonical["state"]
                if outcome == "succeeded":
                    legacy_step["result"] = copy.deepcopy(dict(receipt))
                    legacy_step["output"] = copy.deepcopy(receipt.get("structuredContent", receipt))
            if target == "retry_wait" and not legacy_steps:
                raise contracts.ContractError("reconciliation_required", "Executable Step projection is unavailable")
            summary["status"] = "restarting" if target == "retry_wait" else target
            if legacy_steps:
                summary["stepExecutions"] = legacy_steps
            job.setdefault("runSummaries", {})[run_id] = copy.deepcopy(summary)
            if job.get("lastExecution", {}).get("runId") == run_id:
                job["lastExecution"] = copy.deepcopy(summary)
            job["lastStatus"] = summary["status"]
            if target in {"retry_wait", "failed"} and legacy_steps:
                recovery = {"snapshot": copy.deepcopy(state["plans"][run_id]["frozen"]), "steps": copy.deepcopy(legacy_steps), "safeBoundary": True}
                job["chainRecovery"] = recovery
                job.setdefault("runRecoveries", {})[run_id] = copy.deepcopy(recovery)
            if target == "retry_wait":
                job["status"] = "restarting"
                job["runRecoveryEligible"] = True
                checkpoint = {key: copy.deepcopy(summary[key]) for key in ("targetIds", "targetResults", "selection", "triggerTargetIds", "triggerSelection") if key in summary}
                job["recoveryConfirmation"] = {"status": "reserved", "runId": run_id, "configRevision": job["configRevision"], "checkpoint": checkpoint}
            else:
                job["status"] = target
                job.pop("recoveryConfirmation", None)
                if target == "completed":
                    job.setdefault("runRecoveries", {}).pop(run_id, None)
            notification = copy.deepcopy(summary)
            notification["id"] = summary["id"] + ":reconciled"
            job.setdefault("terminalOutbox", {})[notification["id"]] = notification
            self.ledger.ensure_outbox(job, self.root, run_id, notification["id"], notification)
            return copy.deepcopy(effect)

        return self.store.mutate(job_id, change)


@legacy_writer(lambda registry, *, registry_root, **kwargs: Path(registry_root).resolve().parent, reservation=True)
def cleanup_legacy_jobs(registry, *, registry_root: str | Path, retention_days_by_status: dict[str, int], now: float | None=None, emit_events: bool=True) -> dict[str, dict]:
    """Clean exact top-level statuses, with one snapshot and locked rechecks."""
    for status, days in retention_days_by_status.items():
        if status not in registry._RETENTION_JOB_STATUSES:
            raise ValueError(f'unsupported Job retention status: {status}')
        registry._validate_retention_days(days)
    now = registry.time.time() if now is None else float(now)
    if not registry.math.isfinite(now):
        raise ValueError('now must be finite')
    candidates = [job for job in registry.list_jobs(registry_root) if not job.get('scheduledParentJobId')]
    results = {status: registry._retention_result(now, len(candidates)) for status in retention_days_by_status}
    on_deleted = registry._completed_retention_hooks.get('on_deleted')
    for result in results.values():
        result['_deletedJobIds'] = []
    for candidate in candidates:
        status = candidate.get('status')
        if status not in retention_days_by_status:
            continue
        result = results[status]
        cutoff = now - retention_days_by_status[status] * 24 * 60 * 60
        if not registry._job_is_expired_for_status(candidate, status, cutoff):
            continue
        job_id = candidate.get('jobId')
        if not isinstance(job_id, str):
            result['skipped'] += 1
            continue
        try:
            with registry._lock, registry._job_lock(job_id, registry_root), registry_lock(registry_root, "pins"):
                path = registry._job_path(job_id, registry_root)
                current = registry._load_path(path)
                if not registry._job_is_expired_for_status(current, status, cutoff):
                    result['skipped'] += 1
                    continue
                pin = JsonPins(registry_root).read().get(job_id, False)
                if FinalRuntime.deletion_blockers(current, pinned=pin):
                    result['skipped'] += 1
                    continue
                if current.get('kind') == registry.BACKGROUND_PROCESS_KIND and current.get('status') in registry._RETENTION_JOB_STATUSES and (current.get('notificationState') not in {'delivered', 'not_applicable'}):
                    result['skipped'] += 1
                    continue
                action = current.get('action')
                if current.get('kind') == registry.SCHEDULED_TASK_KIND and isinstance(action, dict) and (action.get('api') == 'shell'):
                    if current.get('status') == 'running' or any((child.get('scheduledParentJobId') == job_id and child.get('status') in {'starting', 'running'} for child in registry.list_jobs(registry_root))):
                        result['skipped'] += 1
                        continue
                try:
                    path.unlink()
                except OSError as exc:
                    registry._add_retention_error(result, f'{job_id}: {exc}')
                    continue
            result['deleted'] += 1
            result['_deletedJobIds'].append(job_id)
            if emit_events and callable(on_deleted):
                try:
                    on_deleted(job_id)
                except Exception:
                    pass
        except (OSError, ValueError) as exc:
            result['skipped'] += 1
            registry._add_retention_error(result, f'{job_id}: {exc}')
    return results

@legacy_writer(lambda registry, old_session_id, new_session_id, registry_root=None: registry._root(registry_root).resolve().parent, reservation=True)
def retarget_legacy_jobs(registry, old_session_id: str, new_session_id: str, registry_root: str | Path | None=None) -> dict:
    """Retarget every matching record in the live Jobs registry roots.

    Jobs currently stores background-process (including scheduled-shell child),
    session-message, session-broadcast, scheduled-task, and targetless
    main-lifecycle records in the same ``jobs/job_*.json`` directory.  These
    kinds have no other persisted target paths: scheduled actions carry their
    target at the Job level, while delivery/run data is historical.  The
    default scan resolves and de-duplicates both roots used by current APIs:
    ``scheduler_store.data_root()`` and ``background_jobs._root()``.  It reads
    only each root's live ``jobs/job_*.json`` records and edits only the four
    current target fields.  It includes records in every status; completed
    delivery/run history and already queued messages remain untouched.

    Each record is read and atomically replaced while holding its existing
    cross-process Job lock.  A failed record is reported by job ID so callers
    can diagnose it by root and Job ID; repeating this operation is safe because
    replacements are exact and idempotent.
    """
    summary = {'oldSessionId': old_session_id, 'newSessionId': new_session_id, 'scanned': 0, 'updated': 0, 'unchanged': 0, 'errors': []}
    if not isinstance(old_session_id, str) or not old_session_id or (not isinstance(new_session_id, str)) or (not new_session_id):
        summary['errors'].append({'jobId': None, 'error': 'old and new Session IDs must be non-empty strings'})
        return summary
    if old_session_id == new_session_id:
        return summary
    candidate_roots: list[Path] = []
    if registry_root is not None:
        candidate_roots.append(Path(registry_root).expanduser())
    else:
        try:
            from packages.scheduler import store as scheduler_store
            candidate_roots.append(scheduler_store.data_root())
        except Exception as exc:
            summary['errors'].append({'root': None, 'jobId': None, 'error': f'scheduler registry resolution failed: {type(exc).__name__}: {exc}'})
        background_root_hint = Path(registry.os.environ.get('PAN_BACKGROUND_JOBS_DIR') or registry.DEFAULT_ROOT).expanduser()
        try:
            candidate_roots.append(registry._root())
        except Exception as exc:
            summary['errors'].append({'root': str(background_root_hint), 'jobId': None, 'error': f'background-job registry resolution failed: {type(exc).__name__}: {exc}'})
    roots: list[Path] = []
    seen_roots: set[str] = set()
    for candidate in candidate_roots:
        root = Path(candidate).expanduser()
        try:
            key = registry.os.path.normcase(str(root.resolve()))
        except Exception as exc:
            summary['errors'].append({'root': str(root), 'jobId': None, 'error': f'registry resolution failed: {type(exc).__name__}: {exc}'})
            continue
        if key not in seen_roots:
            seen_roots.add(key)
            roots.append(root)
    for root in roots:
        try:
            paths = sorted((registry._root(root) / 'jobs').glob('job_*.json'))
        except Exception as exc:
            summary['errors'].append({'root': str(root), 'jobId': None, 'error': f'registry scan failed: {type(exc).__name__}: {exc}'})
            continue
        for path in paths:
            job_id = path.stem
            summary['scanned'] += 1
            try:
                with registry._lock, registry._job_lock(job_id, root):
                    current = registry._load_path(path)
                    if current is None:
                        if path.exists():
                            raise ValueError('Job record could not be read')
                        summary['unchanged'] += 1
                        continue
                    if not registry._retarget_job_record(current, old_session_id, new_session_id):
                        summary['unchanged'] += 1
                        continue
                    registry._write_job(path, current)
                    summary['updated'] += 1
            except Exception as exc:
                summary['errors'].append({'root': str(root), 'jobId': job_id, 'error': f'{type(exc).__name__}: {exc}'})
    return summary


@legacy_writer(lambda registry, *, registry_root, **kwargs: Path(registry_root).resolve().parent, reservation=True)
def cleanup_legacy_logs(registry, *, registry_root: str | Path, retention_days: int, now: float | None=None) -> dict:
    """Remove only expired, uniquely owned direct Job log files."""
    registry._validate_retention_days(retention_days)
    now = registry.time.time() if now is None else float(now)
    if not registry.math.isfinite(now):
        raise ValueError('now must be finite')
    cutoff = now - retention_days * 24 * 60 * 60
    result = registry._retention_result(now)
    logs_dir, error = registry._controlled_logs_dir(registry_root)
    if error:
        registry._add_retention_error(result, error)
        return result
    if logs_dir is None:
        return result
    try:
        jobs = registry.list_jobs(registry_root)
        entries = list(logs_dir.iterdir())
    except (OSError, ValueError) as exc:
        registry._add_retention_error(result, f'cannot list Job logs: {exc}')
        return result
    owners: dict[str, list[str]] = {}
    for job in jobs:
        key = registry._canonical_log_path(job.get('logPath'), job, logs_dir)
        if key is not None:
            owners.setdefault(key, []).append(str(job.get('jobId')))
    for path in entries:
        result['scanned'] += 1
        try:
            file_stat = path.lstat()
        except OSError as exc:
            result['skipped'] += 1
            registry._add_retention_error(result, f'{path.name}: {exc}')
            continue
        if registry._is_reparse_stat(file_stat) or not registry.stat.S_ISREG(file_stat.st_mode) or getattr(file_stat, 'st_nlink', 1) != 1:
            result['skipped'] += 1
            continue
        if file_stat.st_mtime > cutoff:
            continue
        key = registry.os.path.normcase(registry.os.path.abspath(path))
        owner_ids = owners.get(key, [])
        if len(owner_ids) != 1:
            result['skipped'] += 1
            continue
        job_id = owner_ids[0]
        try:
            with registry._lock, registry._job_lock(job_id, registry_root), registry_lock(registry_root, "pins"):
                current = registry._load_path(registry._job_path(job_id, registry_root))
                if not current or current.get('jobId') != job_id or registry._canonical_log_path(current.get('logPath'), current, logs_dir) != key or (current.get('status') not in registry._RETENTION_LOG_SAFE_STATUSES) or registry._runner_active_or_unknown(current):
                    result['skipped'] += 1
                    continue
                if FinalRuntime.deletion_blockers(current, pinned=JsonPins(registry_root).read().get(job_id, False)):
                    result['skipped'] += 1
                    continue
                latest_logs_dir, latest_error = registry._controlled_logs_dir(registry_root)
                if latest_error or latest_logs_dir != logs_dir:
                    result['skipped'] += 1
                    if latest_error:
                        registry._add_retention_error(result, latest_error)
                    continue
                latest_stat = path.lstat()
                if registry._is_reparse_stat(latest_stat) or not registry.stat.S_ISREG(latest_stat.st_mode) or getattr(latest_stat, 'st_nlink', 1) != 1 or (latest_stat.st_mtime > cutoff) or ((latest_stat.st_dev, latest_stat.st_ino, latest_stat.st_size, latest_stat.st_mtime_ns) != (file_stat.st_dev, file_stat.st_ino, file_stat.st_size, file_stat.st_mtime_ns)):
                    result['skipped'] += 1
                    continue
                path.unlink()
            result['deleted'] += 1
        except (OSError, ValueError) as exc:
            result['skipped'] += 1
            registry._add_retention_error(result, f'{path.name}: {exc}')
    return result


def read_legacy_history(root, *, task_id: str | None = None, limit: int = 100) -> list[dict]:
    """Read run history, newest first; optional per-task filter."""
    path = Path(root) / "runs.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if task_id and record.get("task_id") != task_id:
            continue
        records.append(record)
    records.reverse()
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    if limit > 0:
        records = records[:limit]
    return records


@legacy_writer(lambda registry, job_id, registry_root=None: registry._root(registry_root).resolve().parent, reservation=True)
def delete_legacy_job(registry, job_id: str, registry_root: str | Path | None=None) -> bool:
    """Serialize explicit delete/recheck; never unlink unconfirmed process owners.

    Only the per-delete mutex spans OS waits. Registry/global locks are released
    before cleanup, so unrelated Jobs and runner facts can continue to commit.
    """
    with registry._registry_lock('delete-' + job_id, registry_root), registry._registry_lock('spawn-' + job_id, registry_root), registry._registry_lock('task-spawn-' + job_id, registry_root):
        with registry._lock, registry._job_lock(job_id, registry_root):
            path = registry._job_path(job_id, registry_root)
            current = registry._load_path(path)
            if not current:
                return False
            if current.get('executionOwner') == 'independent-job-service':
                raise ValueError('independent Job deletion is unsupported')
            action = current.get('action')
            if current.get('kind') == registry.SCHEDULED_TASK_KIND and isinstance(action, dict) and (action.get('api') == 'shell'):
                if current.get('status') in {'running', 'starting'} or any((child.get('scheduledParentJobId') == job_id and child.get('status') not in {'completed', 'failed', 'cancelled'} for child in registry.list_jobs(registry_root))):
                    raise ValueError('cannot delete scheduled shell Job with an active dispatch/child')
            active = current.get('status') in {'starting', 'running', 'cleanup_pending', 'deleting'}
            if current.get('kind', registry.BACKGROUND_PROCESS_KIND) == registry.BACKGROUND_PROCESS_KIND:
                active = active or str(current.get('status', '')).lower() in {'unknown', 'uncertain'}
            if active:
                current.setdefault('cleanupOriginalStatus', current.get('status'))
                current.update(status='cleanup_pending', cleanupAttemptAt=registry.time.time(), updatedAt=registry.time.time(), cleanupError='process cleanup in progress')
                registry._write_job(path, current)
            else:
                path.unlink()
                return True

        def persist_proof(proof):
            with registry._lock, registry._job_lock(job_id, registry_root):
                latest = registry._load_path(path)
                if not latest or latest.get('status') != 'cleanup_pending':
                    raise ValueError('Job cleanup admission changed')
                latest.update(proof, updatedAt=registry.time.time())
                registry._write_job(path, latest)
        try:
            frozen = dict(current)
            frozen['_cleanupPersist'] = persist_proof
            receipt = registry._terminate_job_processes([frozen])
            if not isinstance(receipt, dict) or receipt.get('confirmed') is not True:
                raise ValueError((receipt or {}).get('reason') or 'process cleanup receipt unknown')
        except Exception as exc:
            persist_proof({'cleanupError': str(exc)[:500]})
            raise ValueError(f'cannot safely delete: {exc}') from exc
        with registry._lock, registry._job_lock(job_id, registry_root):
            latest = registry._load_path(path)
            if not latest:
                return False
            if latest.get('status') != 'cleanup_pending':
                raise ValueError('Job cleanup state changed; record retained')
            path.unlink()
            return True
