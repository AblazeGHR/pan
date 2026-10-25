"""Canonical coordinator and ports over the legacy JSON registry backend."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from packages.core import background_jobs as registry
from packages.jobs import final_contracts as contracts

from .ledger import Ledger, aggregate, now, public


class ExecutorPort(Protocol):
    """WP05 boundary: one fenced Attempt, never a new Job or Run."""

    async def execute(
        self,
        *,
        attempt: Mapping[str, Any],
        effect_key: Mapping[str, Any],
        frozen_plan: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class TriggerAdmissionPort(Protocol):
    """WP03 boundary: Trigger matching produces intents, not effects."""

    async def admit(self, event: Mapping[str, Any], target: Mapping[str, Any]) -> Mapping[str, Any]: ...


class JsonRegistryStore:
    """Atomic per-Job aggregate storage; no second Job/store is created."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def get(self, job_id: str) -> dict[str, Any]:
        job = registry.get(job_id, self.root)
        if not job:
            raise KeyError(job_id)
        return job

    def mutate(self, job_id: str, change: Callable[[dict[str, Any]], Any]) -> Any:
        with registry._lock, registry._job_lock(job_id, self.root):
            job = self.get(job_id)
            result = change(job)
            registry._write_job(registry._job_path(job_id, self.root), job)
            return copy.deepcopy(result)


class FinalRuntime:
    """Query/control facade for the canonical records in one Job aggregate."""

    def __init__(self, root: str | Path, store: JsonRegistryStore | None = None):
        self.root = Path(root).resolve()
        self.store = store or JsonRegistryStore(self.root)
        self.ledger = Ledger()

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
