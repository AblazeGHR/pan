"""Durable lifecycle supervisor for the Pan main service.

Canonical state lives in the independent execution receipt store. Protected
Windows stop/start uses the verified launcher primitives. Legacy supervisor
names are retained as read-only compatibility entry points until R6.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import math
import time
from pathlib import Path
from typing import Any

from packages.core import launcher

# The launcher waits for the API and may also start the optional tunnel. Keep
# this budget separate from later lifecycle polling.
START_TIMEOUT_SEC = 90.0
READY_POLL_SEC = 0.5


class ProtectedLifecycleExecutor:
    """One product-owned, single-step canonical service.restart/exit effect.

    ``authorize`` is a trusted host callback, never a Job-supplied flag. It
    rechecks privilege and returns the canonical scope with stepCount=1,
    jobId/runId/stepId/operation/root/port. WP06 owns transport authorization.
    Legacy supervisor entry points are read-only; product transports use this port.
    """

    def __init__(self, *, job_id, store, authorize, launch=None):
        from packages.jobs.independent_executor import launch_execution
        self.job_id, self.store, self.authorize = job_id, store, authorize
        self.launch = launch or launch_execution

    async def lookup_receipt(self, *, attempt, effect_key):
        """Recover the original operation while the main service is offline."""
        from packages.jobs.independent_executor import ExecutionHost, IndependentExecutor
        return await IndependentExecutor(job_id=self.job_id, client=ExecutionHost(self.store)).lookup_receipt(
            attempt=attempt, effect_key=effect_key)

    async def execute(self, *, attempt, effect_key, frozen_plan, retry=False):
        from packages.actions.typed_executor import EffectReceipt
        from packages.core.platform import supports
        from packages.jobs.independent_executor import effect_id
        operation = frozen_plan.get("operation")
        args = frozen_plan.get("arguments", {})
        if not isinstance(args, dict):
            raise ValueError("lifecycle arguments must be an object")
        expected = {"jobId": self.job_id, "runId": effect_key["runId"],
                    "stepId": effect_key["stepId"], "operation": operation,
                    "root": args.get("root"), "port": args.get("port"), "stepCount": 1}
        grant = self.authorize(copy.deepcopy(expected))
        if (operation not in {"service.restart", "service.exit"}
                or not isinstance(grant, dict) or grant != expected):
            return EffectReceipt(dict(effect_key), "rejected", {"code": "authorization_denied"}).as_dict()
        if not supports("service.lifecycle"):
            return EffectReceipt(dict(effect_key), "rejected", {"code": "capability_unsupported"}).as_dict()
        if (not isinstance(args.get("root"), str) or not Path(args["root"]).is_absolute()
                or not Path(args["root"]).is_dir() or type(args.get("port")) is not int
                or not 1 <= args["port"] <= 65535
                or type(args.get("oldPid")) is not int or args["oldPid"] <= 0
                or type(args.get("oldPidCreatedAt")) not in (int, float)
                or not math.isfinite(args["oldPidCreatedAt"]) or args["oldPidCreatedAt"] <= 0):
            raise ValueError("verified root/port/old PID and birth are required")
        doc, created = await asyncio.to_thread(self.store.admit, job_id=self.job_id,
            attempt=attempt, effect_key=effect_key, frozen_plan=frozen_plan, retry=retry)
        if created:
            try:
                await asyncio.to_thread(self.launch, self.store.root, doc["effectId"])
            except Exception:
                # A detached spawn error is ambiguous; never start it again.
                await asyncio.to_thread(self.store.launch_failed, doc["effectId"], doc["attempt"])
        doc = self.store.get(effect_id(effect_key))
        return doc["result"] or EffectReceipt(dict(effect_key), "unknown", {
            "code": "execution_pending", "effectId": doc["effectId"], "phase": doc["phase"]}).as_dict()


def offline_checks(*, root, port, old_pid, old_pid_created_at):
    """Absence is proven by a process status probe, not a missing timestamp."""
    from packages.core.platform.execution_host import service_offline_evidence
    return service_offline_evidence(root=root, port=port, old_pid=old_pid,
                                    old_pid_created_at=old_pid_created_at)


def run_canonical_supervisor(store, doc):
    """Execute a sealed canonical admission; every effect boundary is durable.

    A recovered supervisor never repeats stop/start. Recovery queries the
    original receipt/evidence; an interrupted effect stays uncertain.
    """
    from packages.actions.typed_executor import EffectReceipt
    from packages.core.platform import supports
    identity, attempt, claim = doc["effectId"], doc["attempt"], doc["claim"]
    plan, key = doc["frozenPlan"], doc["effectKey"]
    if not supports("service.lifecycle"):
        return EffectReceipt(key, "rejected", {"code": "capability_unsupported"},
                             {"confirmed": True, "effectStarted": False}).as_dict()
    args = plan["arguments"]
    root, port = str(Path(args["root"]).resolve()), args["port"]
    old_pid, birth = args["oldPid"], args["oldPidCreatedAt"]
    if plan["operation"] not in {"service.restart", "service.exit"}:
        return EffectReceipt(key, "rejected", {"code": "authorization_denied"}).as_dict()
    if not service_process_identity(old_pid, root, birth).get("ok"):
        return EffectReceipt(key, "rejected", {"code": "identity_unverified", "phase": "failed"},
                             {"confirmed": True, "effectStarted": False}).as_dict()
    # The transaction closes cancellation before the first irreversible call.
    store.phase(identity, attempt, claim, "stopping", root=root, port=port, oldPid=old_pid, oldPidCreatedAt=birth)
    stopped = launcher.stop_service(Path(root), port, old_pid, birth, require_identity=True)
    offline = offline_checks(root=root, port=port, old_pid=old_pid, old_pid_created_at=birth)
    store.phase(identity, attempt, claim, "stopped", stopReceipt=stopped, offlineProof=offline)
    if stopped.get("stopped") is not True or offline.get("ok") is not True:
        return EffectReceipt(key, "unknown", {"code": "offline_unconfirmed", "phase": "uncertain", "offlineProof": offline},
                             {"confirmed": False}).as_dict()
    if plan["operation"] == "service.exit":
        return EffectReceipt(key, "succeeded", {"phase": "offline", "root": root, "port": port,
                             "oldPid": old_pid, "oldPidCreatedAt": birth, "offlineProof": offline},
                             {"confirmed": True}).as_dict()
    store.phase(identity, attempt, claim, "starting")
    launcher.start_service(Path(root), timeout=launcher.READY_TIMEOUT_SEC)
    store.phase(identity, attempt, claim, "checking_ready")
    deadline = time.monotonic() + START_TIMEOUT_SEC
    checks = {}
    while time.monotonic() < deadline:
        checks = ready_checks(root=root, port=port, old_pid=old_pid, old_pid_created_at=birth)
        # launcher.readiness proves the new listener's root and HTTP readiness.
        if (checks.get("ok") is True and type(checks.get("newPid")) is int
                and checks["newPid"] > 0 and checks["newPid"] != old_pid
                and type(checks.get("newPidCreatedAt")) in (int, float)
                and math.isfinite(checks["newPidCreatedAt"]) and checks["newPidCreatedAt"] > birth
                and checks.get("port") == port and checks.get("http") is True
                and checks.get("identity", {}).get("ok") is True
                and checks["identity"].get("root") == root
                and checks.get("listenerOwner") == checks.get("newPid")):
            return EffectReceipt(key, "succeeded", {"phase": "ready", "root": root, "port": port,
                "oldPid": old_pid, "oldPidCreatedAt": birth, "readyProof": checks,
                "offlineProof": offline}, {"confirmed": True}).as_dict()
        time.sleep(READY_POLL_SEC)
    return EffectReceipt(key, "unknown", {"phase": "uncertain", "code": "ready_unconfirmed", "readyProof": checks},
                         {"confirmed": False}).as_dict()


def process_create_time(pid: int | None) -> float | None:
    return launcher.process_create_time(pid)


def listener_owner(port: int) -> int | None:
    return launcher.listener_owner(port)


def service_process_identity(pid: int | None, root: str,
                             expected_created_at: float | None = None) -> dict[str, Any]:
    return launcher.service_process_identity(pid, root, expected_created_at)


def ready_checks(*, root: str, port: int, old_pid: int | None,
                 old_pid_created_at: float | None) -> dict[str, Any]:
    """Require a new verified listener and the historical HTTP readiness path."""
    pid = launcher.listener_owner(port)
    created = process_create_time(pid)
    result: dict[str, Any] = {
        "root": str(Path(root).resolve()), "port": int(port), "listenerOwner": pid,
        "listenerOwnerCreatedAt": created, "newPid": pid,
        "newPidCreatedAt": created, "ok": False,
    }
    if pid is None:
        result["error"] = "target port has no listener"
        return result
    if old_pid and pid == old_pid:
        result.update(ok=False, error="target port is still owned by the old PID")
        return result
    if old_pid and old_pid_created_at is not None and process_create_time(old_pid) == old_pid_created_at:
        result.update(ok=False, error="old service PID is still alive")
        return result
    checked = launcher.readiness(
        root=root, port=port, expected_pid=pid, expected_created_at=created,
    )
    result.update(checked)
    result["newPid"] = result.get("listenerOwner")
    result["newPidCreatedAt"] = result.get("listenerOwnerCreatedAt")
    return result


def legacy_status(job_id: str, registry_root: str | None = None):
    """Read the original historical lifecycle record without advancing it."""
    from packages.core import background_jobs
    return background_jobs.get(job_id, registry_root)


def run_exit_supervisor(job_id: str, root: str, port: int, old_pid: int | None = None,
                        old_pid_created_at: float | None = None,
                        registry_root: str | None = None) -> int:
    """Retired entry point; 1 signals refusal, even for completed records."""
    legacy_status(job_id, registry_root)
    return 1


def run_supervisor(job_id: str, root: str, port: int, old_pid: int | None = None,
                   old_pid_created_at: float | None = None,
                   registry_root: str | None = None) -> int:
    """Retained CLI/import compatibility, with no lifecycle write or effect."""
    legacy_status(job_id, registry_root)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Pan main-service lifecycle supervisor")
    parser.add_argument("--supervise", action="store_true")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--old-pid", type=int)
    parser.add_argument("--old-pid-created-at", type=float)
    parser.add_argument("--registry-root")
    args = parser.parse_args()
    if not args.supervise:
        parser.error("--supervise is required")
    import sys
    print("legacy_lifecycle_retired: query the original operation; use the protected canonical executor", file=sys.stderr)
    return run_supervisor(args.job_id, args.root, args.port, args.old_pid,
                          args.old_pid_created_at, args.registry_root)


if __name__ == "__main__":
    raise SystemExit(main())
