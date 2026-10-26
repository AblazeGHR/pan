"""Canonical runtime records embedded in the existing JSON Job aggregate.

The records in this module are authoritative. Legacy execution fields are only
projections maintained by ``action_runtime`` for the current transports.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping
import uuid

from packages.jobs import final_contracts as contracts


TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "uncertain"}
ACTIVE_RUN_STATES = {"queued", "running", "retry_wait", "paused_at_boundary", "cancelling", "settling", "uncertain"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:24]


def profile_id(root: Any) -> str:
    return "profile_" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:24]


def principal(authority: Mapping[str, Any] | None) -> dict[str, str]:
    value = authority or {"type": "user"}
    kind = value.get("type", "user")
    if kind == "legacy-mcp":
        kind = "agent"
    if kind not in contracts.PRINCIPAL_TYPES:
        raise ValueError("Unknown canonical authority type")
    identity = value.get("id") or value.get("sessionId") or ("local" if kind == "user" else kind)
    return {"type": kind, "id": str(identity)}


def aggregate(job: dict[str, Any], root: Any) -> dict[str, Any]:
    value = job.setdefault("finalRuntime", {})
    value.setdefault("schemaVersion", contracts.CONTRACT_VERSION)
    value.setdefault("profileId", profile_id(root))
    for name in ("admissions", "runs", "plans", "steps", "attempts", "effects", "operations", "operationIndex", "outbox", "effectAdmissions"):
        value.setdefault(name, [] if name == "effects" else {})
    return value


def public(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("plans", None)
    result.pop("effectAdmissions", None)
    return result


def job_ref(job: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": job["jobId"], "revision": int(job["configRevision"]), "digest": contracts.digest(job["config"])}


def cause(event: Mapping[str, Any] | None = None, target: Mapping[str, Any] | None = None, *, job_id: str = "") -> dict[str, Any]:
    if event is None:
        return {"type": "manual"}
    trigger_id = (target or {}).get("triggerId") or (target or {}).get("id") or "trigger:" + job_id
    return {"type": "event", "eventId": event.get("eventId", event.get("id")), "triggerId": trigger_id}


def admission_for_cause(state: Mapping[str, Any], value: Mapping[str, Any]) -> dict[str, Any] | None:
    if value.get("type") != "event":
        return None
    for record in state.get("admissions", {}).values():
        if record["cause"].get("eventId") == value["eventId"] and record["cause"].get("triggerId") == value["triggerId"]:
            return record
    return None


class Ledger:
    """Pure aggregate transitions; callers commit through one registry lock."""

    def admit(
        self,
        job: dict[str, Any],
        root: Any,
        *,
        plan: Mapping[str, Any],
        cause: Mapping[str, Any],
        authority: Mapping[str, Any] | None,
        admission_id: str | None = None,
        run_id: str | None = None,
        rerun_of: str | None = None,
        state: str = "admitted",
        reason: str | None = None,
        operation_id: str | None = None,
        notification: Mapping[str, Any] | None = None,
        authorization_revision: int | None = None,
    ) -> dict[str, Any]:
        ledger = aggregate(job, root)
        existing = admission_for_cause(ledger, cause)
        if existing is not None:
            return existing
        admission_id = admission_id or new_id("adm_")
        run_id = run_id or new_id("run_")
        timestamp = now()
        admission = {
            "admissionId": admission_id,
            "profileId": ledger["profileId"],
            "jobId": job["jobId"],
            "configRevision": int(job["configRevision"]),
            "cause": copy.deepcopy(dict(cause)),
            "authority": principal(authority),
            "authorizationRevision": authorization_revision or 1,
            "state": state,
            "createdAt": timestamp,
            "decidedAt": timestamp,
        }
        if reason is not None:
            admission["reason"] = reason
        ledger["admissions"][admission_id] = contracts.validate_record("admission", admission)
        if state != "admitted":
            return admission
        run = {
            "runId": run_id,
            "profileId": ledger["profileId"],
            "jobId": job["jobId"],
            "configRevision": int(job["configRevision"]),
            "planDigest": contracts.digest(plan),
            "admissionId": admission_id,
            "cause": copy.deepcopy(dict(cause)),
            "state": "queued",
            "stateRevision": 0,
            "transitionSeq": 0,
            "createdAt": timestamp,
        }
        if rerun_of is not None:
            run["rerunOf"] = rerun_of
        ledger["runs"][run_id] = contracts.validate_record("run", run)
        ledger["plans"][run_id] = {
            "frozen": copy.deepcopy(dict(plan)),
            "operationId": operation_id,
            "notification": copy.deepcopy(dict(notification if notification is not None else job.get("runPolicy", job["config"]).get("notification", {}))),
        }
        step_ids = [step["stepId"] for step in plan.get("steps", [])] or ["main"]
        ledger["steps"][run_id] = {}
        for step_id in step_ids:
            step = {"runId": run_id, "stepId": step_id, "state": "pending", "stateRevision": 0, "attemptCount": 0}
            ledger["steps"][run_id][step_id] = contracts.validate_record("step", step)
        return admission

    def run(self, job: dict[str, Any], root: Any, run_id: str) -> dict[str, Any]:
        try:
            return aggregate(job, root)["runs"][run_id]
        except KeyError as exc:
            raise KeyError(run_id) from exc

    def transition_run(self, job: dict[str, Any], root: Any, run_id: str, target: str, *, operation: str | None = None) -> dict[str, Any]:
        ledger = aggregate(job, root)
        run = self.run(job, root, run_id)
        if run["state"] == target:
            return run
        contracts.check_transition("run", run["state"], target, operation=operation)
        run["state"] = target
        run["stateRevision"] += 1
        run["transitionSeq"] += 1
        if target == "running" and "startedAt" not in run:
            run["startedAt"] = now()
        if target in TERMINAL_RUN_STATES:
            run["finishedAt"] = now()
        else:
            run.pop("finishedAt", None)
        ledger["runs"][run_id] = contracts.validate_record("run", run)
        return run

    def start(self, job: dict[str, Any], root: Any, run_id: str, *, retry: bool = False) -> dict[str, Any]:
        run = self.run(job, root, run_id)
        if run["state"] == "uncertain":
            raise contracts.ContractError("reconciliation_required", "Uncertain Run requires reconciliation")
        if retry and run["state"] == "failed":
            self.transition_run(job, root, run_id, "retry_wait", operation="retry")
        return self.transition_run(job, root, run_id, "running")

    def begin_attempt(
        self,
        job: dict[str, Any],
        root: Any,
        run_id: str,
        step_id: str,
        *,
        attempt_id: str | None = None,
        lease_id: str | None = None,
        retry: bool = False,
    ) -> dict[str, Any]:
        ledger = aggregate(job, root)
        run = self.run(job, root, run_id)
        if run["state"] != "running":
            raise contracts.ContractError("unsupported_operation", "Run is not accepting an Attempt")
        step = ledger["steps"][run_id][step_id]
        if step["state"] in {"completed", "uncertain", "cancelled"}:
            code = "reconciliation_required" if step["state"] == "uncertain" else "not_retryable"
            raise contracts.ContractError(code, f"Step {step_id} cannot start another Attempt")
        if step["state"] == "running":
            raise contracts.ContractError("unsupported_operation", "Step already has an active Attempt")
        operation = "retry" if step["state"] == "failed" or retry else None
        contracts.check_transition("step", step["state"], "running", operation=operation)
        step["state"] = "running"
        step["stateRevision"] += 1
        step["attemptCount"] += 1
        step["startedAt"] = now()
        step.pop("finishedAt", None)
        attempt_id = attempt_id or new_id("attempt_")
        attempt = {
            "attemptId": attempt_id,
            "runId": run_id,
            "stepId": step_id,
            "state": "preparing",
            "ownerEpoch": 1,
            "generation": step["attemptCount"],
            "leaseId": lease_id or new_id("lease_"),
            "startedAt": now(),
        }
        contracts.validate_record("attempt", attempt)
        contracts.check_transition("attempt", "preparing", "running")
        attempt["state"] = "running"
        ledger["attempts"][attempt_id] = contracts.validate_record("attempt", attempt)
        ledger["steps"][run_id][step_id] = contracts.validate_record("step", step)
        return attempt

    def finish_attempt(
        self,
        job: dict[str, Any],
        root: Any,
        attempt_id: str,
        outcome: str,
        *,
        expected_epoch: int | None = None,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        ledger = aggregate(job, root)
        attempt = ledger["attempts"][attempt_id]
        if expected_epoch is not None and attempt["ownerEpoch"] != expected_epoch:
            raise contracts.ContractError("revision_conflict", "Attempt ownerEpoch fence rejected the receipt")
        if expected_generation is not None and attempt["generation"] != expected_generation:
            raise contracts.ContractError("revision_conflict", "Attempt generation fence rejected the receipt")
        if attempt["state"] != "running":
            raise contracts.ContractError("revision_conflict", "Late receipt does not match the active Attempt")
        target = {"completed": "succeeded", "failed": "failed", "restarting": "failed", "uncertain": "uncertain", "unknown": "uncertain", "cancelled": "cancelled"}[outcome]
        contracts.check_transition("attempt", attempt["state"], target)
        attempt["state"] = target
        attempt["endedAt"] = now()
        ledger["attempts"][attempt_id] = contracts.validate_record("attempt", attempt)
        step = ledger["steps"][attempt["runId"]][attempt["stepId"]]
        step_target = {"succeeded": "completed", "failed": "failed", "uncertain": "uncertain", "cancelled": "cancelled"}[target]
        contracts.check_transition("step", step["state"], step_target)
        step["state"] = step_target
        step["stateRevision"] += 1
        step["finishedAt"] = now()
        ledger["steps"][attempt["runId"]][attempt["stepId"]] = contracts.validate_record("step", step)
        return attempt

    def fail_step_without_attempt(self, job: dict[str, Any], root: Any, run_id: str, step_id: str) -> dict[str, Any]:
        """Mapping/validation failure has a failed Step but no effect Attempt."""
        ledger = aggregate(job, root)
        step = ledger["steps"][run_id][step_id]
        if step["state"] != "pending":
            return step
        contracts.check_transition("step", "pending", "running")
        step["state"] = "running"
        step["stateRevision"] += 1
        contracts.check_transition("step", "running", "failed")
        step["state"] = "failed"
        step["stateRevision"] += 1
        step["finishedAt"] = now()
        ledger["steps"][run_id][step_id] = contracts.validate_record("step", step)
        return step

    def effect_key(self, run_id: str, step_id: str, target_id: str, operation_ordinal: int = 0) -> dict[str, Any]:
        return {"runId": run_id, "stepId": step_id, "targetId": target_id, "operationOrdinal": operation_ordinal}

    @staticmethod
    def _effect_id(key: Mapping[str, Any]) -> str:
        return contracts.digest(key)

    def admit_effect(self, job: dict[str, Any], root: Any, attempt_id: str, target_id: str, operation_ordinal: int = 0) -> dict[str, Any]:
        ledger = aggregate(job, root)
        attempt = ledger["attempts"][attempt_id]
        if attempt["state"] != "running":
            raise contracts.ContractError("revision_conflict", "Effect admission requires a running Attempt")
        key = self.effect_key(attempt["runId"], attempt["stepId"], target_id, operation_ordinal)
        effect_id = self._effect_id(key)
        for effect in ledger["effects"]:
            if self._effect_id(effect["effectKey"]) != effect_id:
                continue
            if effect["outcome"] in {"unknown", "partial"}:
                raise contracts.ContractError("reconciliation_required", "Unknown Effect must be reconciled before retry")
            if effect["outcome"] == "succeeded":
                raise contracts.ContractError("not_retryable", "Successful Effect cannot be repeated in the same Run")
        ledger["effectAdmissions"][effect_id] = {"attemptId": attempt_id, "effectKey": key, "createdAt": now()}
        return key

    def record_effect(self, job: dict[str, Any], root: Any, attempt_id: str, outcome: str, receipt: Mapping[str, Any] | None = None) -> dict[str, Any]:
        ledger = aggregate(job, root)
        attempt = ledger["attempts"][attempt_id]
        effect_id, key = next(
            ((effect_id, item["effectKey"]) for effect_id, item in ledger["effectAdmissions"].items() if item["attemptId"] == attempt_id),
            (None, None),
        )
        if key is None:
            key = self.effect_key(attempt["runId"], attempt["stepId"], attempt["stepId"], 0)
            effect_id = self._effect_id(key)
        canonical_outcome = {"completed": "succeeded", "failed": "rejected", "restarting": "rejected", "cancelled": "rejected", "uncertain": "unknown", "unknown": "unknown", "partial": "partial"}[outcome]
        effect = {
            "effectKey": copy.deepcopy(key),
            "attemptId": attempt_id,
            "outcome": canonical_outcome,
            "receipt": copy.deepcopy(dict(receipt or {})),
            "createdAt": now(),
            "updatedAt": now(),
        }
        ledger["effects"].append(contracts.validate_record("effect", effect))
        ledger["effectAdmissions"].pop(effect_id, None)
        return effect

    def ensure_outbox(self, job: dict[str, Any], root: Any, run_id: str, legacy_id: str, summary: Mapping[str, Any]) -> list[dict[str, Any]]:
        ledger = aggregate(job, root)
        run = self.run(job, root, run_id)
        policy = ledger["plans"].get(run_id, {}).get("notification", {})
        configured = summary.get("notificationPolicy", policy)
        deliveries = [("job:" + job["jobId"], "ws")]
        recipients = {}
        if summary.get("status") in configured.get("on", []):
            for recipient in configured.get("recipients", []):
                key = (recipient["recipientId"], recipient["channelId"])
                if key not in recipients:
                    recipients[key] = recipient["targetRef"]
                    if key not in deliveries:
                        deliveries.append(key)
        if summary.get("status") in configured.get("on", []) and configured.get("target") == "job":
            deliveries.append(("job:" + job["jobId"], "job"))
            deliveries.extend((session_id, "session") for session_id in configured.get("sessionIds", []))
        created = []
        for recipient_id, channel in deliveries:
            outbox_id = "outbox_" + contracts.digest({"runId": run_id, "transitionSeq": run["transitionSeq"], "recipientId": recipient_id, "channel": channel})[:40]
            if outbox_id in ledger["outbox"]:
                continue
            item = {
                "outboxId": outbox_id,
                "runId": run_id,
                "transitionSeq": run["transitionSeq"],
                "recipientId": recipient_id,
                "channel": channel,
                **({"targetRef": recipients[(recipient_id, channel)]} if (recipient_id, channel) in recipients else {}),
                "legacyId": legacy_id,
                "status": "pending",
                "summary": copy.deepcopy(dict(summary)),
                "createdAt": now(),
            }
            ledger["outbox"][outbox_id] = item
            created.append(item)
        return created

    def ack_outbox(self, job: dict[str, Any], root: Any, legacy_id: str, *, suppressed: set[str] | None = None, receipts: Mapping[str, Any] | None = None) -> None:
        suppressed = suppressed or set()
        receipts = receipts or {}
        for item in aggregate(job, root)["outbox"].values():
            if item["legacyId"] != legacy_id or item["status"] != "pending":
                continue
            if item["channel"] == "session" and item["recipientId"] in suppressed:
                item["status"] = "suppressed"
                item["settledAt"] = now()
                continue
            item["status"] = "acked"
            item["ackedAt"] = now()
            if item["recipientId"] in receipts:
                item["receipt"] = copy.deepcopy(receipts[item["recipientId"]])

    def begin_operation(
        self,
        job: dict[str, Any],
        root: Any,
        *,
        operation: str,
        request_key: str,
        authority: Mapping[str, Any] | None,
        expected_revision: int,
        payload: Any,
        operation_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        ledger = aggregate(job, root)
        actor = principal(authority)
        scope = contracts.idempotency_scope(actor, ledger["profileId"], operation)
        request_digest = contracts.request_digest(operation, payload)
        index = contracts.digest({"scope": scope, "requestKey": request_key})
        existing_id = ledger["operationIndex"].get(index)
        if existing_id:
            existing = ledger["operations"][existing_id]
            outcome = contracts.idempotency_outcome(
                {"scope": scope, "requestDigest": existing["requestDigest"]},
                scope=scope,
                request_digest=request_digest,
            )
            return existing, outcome == "replay"
        contracts.check_request_key(request_key)
        operation_id = operation_id or new_id("op_")
        record = {
            "operationId": operation_id,
            "requestKey": request_key,
            "principal": actor,
            "profileId": ledger["profileId"],
            "operation": operation,
            "state": "accepted",
            "requestDigest": request_digest,
            "expectedRevision": expected_revision,
            "objectRefs": [job_ref(job)],
            "createdAt": now(),
        }
        ledger["operations"][operation_id] = contracts.validate_record("operation", record)
        ledger["operationIndex"][index] = operation_id
        return record, False

    def complete_operation(self, job: dict[str, Any], root: Any, operation_id: str, result: Mapping[str, Any], *, run_id: str | None = None, admission_id: str | None = None) -> dict[str, Any]:
        ledger = aggregate(job, root)
        record = ledger["operations"][operation_id]
        if record["state"] != "completed":
            contracts.check_transition("operation", record["state"], "completed")
            record["state"] = "completed"
        record["result"] = copy.deepcopy(dict(result))
        record["updatedAt"] = now()
        if run_id is not None:
            record["runId"] = run_id
        if admission_id is not None:
            record["admissionId"] = admission_id
        ledger["operations"][operation_id] = contracts.validate_record("operation", record)
        return record

    def request_cancel(self, job: dict[str, Any], root: Any, run_id: str, *, request_key: str, authority: Mapping[str, Any] | None) -> dict[str, Any]:
        operation, replay = self.begin_operation(
            job,
            root,
            operation="cancel",
            request_key=request_key,
            authority=authority,
            expected_revision=job["configRevision"],
            payload={"runId": run_id},
        )
        run = self.run(job, root, run_id)
        if run["state"] not in TERMINAL_RUN_STATES and run["state"] != "cancelling":
            self.transition_run(job, root, run_id, "cancelling", operation="cancel")
        if not replay:
            self.complete_operation(job, root, operation["operationId"], {"scope": "run", "state": run["state"]}, run_id=run_id)
        return run
