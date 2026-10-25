"""Durable lifecycle supervisor for the Pan main service.

The durable Job state remains here, but all Windows process work is delegated
to :mod:`packages.core.launcher`.  There is no stop/start batch-script hop:
the supervisor invokes the same verified Python launcher primitives used by
the one-click entry point.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import math
import time
from pathlib import Path
from typing import Any

from packages.core import background_jobs
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
    Old POST/CLI callers and their legacy writers remain unchanged until R4.
    """

    def __init__(self, *, job_id, store, authorize, launch=None):
        from packages.jobs.independent_executor import launch_execution
        self.job_id, self.store, self.authorize = job_id, store, authorize
        self.launch = launch or launch_execution

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
    identity, attempt, claim = doc["effectId"], doc["attempt"], doc["claim"]
    plan, key = doc["frozenPlan"], doc["effectKey"]
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
        "port": int(port), "listenerOwner": pid,
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


def _fail(job_id: str, phase: str, message: str, registry_root: str | None = None) -> int:
    try:
        background_jobs.transition_service_job(job_id, phase, registry_root=registry_root, error=message)
    except Exception:
        # The original exception/exit code is still returned to the detached
        # caller; do not hide a stop/start failure behind a registry write.
        pass
    return 1


class _ServiceStopFailure(RuntimeError):
    def __init__(self, phase: str, message: str):
        super().__init__(message)
        self.phase = phase


def _stop_service(*, job_id: str, root_path: Path, port: int, old_pid: int | None,
                  old_pid_created_at: float | None, log_path: str | None,
                  phase: str, require_verified_identity: bool,
                  registry_root: str | None) -> None:
    """Run the shared old-service stop stage for Exit and Restart.

    The caller owns the operation-specific phases before and after this
    helper.  In particular, this helper never changes Worker state and never
    starts a service; it only verifies and stops the current Pan service.
    """
    if require_verified_identity:
        if not old_pid or old_pid_created_at is None:
            raise _ServiceStopFailure("failed", "current service identity could not be verified")
        if not service_process_identity(old_pid, str(root_path), old_pid_created_at)["ok"]:
            raise _ServiceStopFailure("failed", "current service identity could not be verified")
    elif old_pid and not service_process_identity(
            old_pid, str(root_path), old_pid_created_at)["ok"]:
        raise _ServiceStopFailure("failed", "old service identity could not be verified")

    background_jobs.transition_service_job(job_id, phase, registry_root=registry_root)
    try:
        result = launcher.stop_service(
            root_path, port, old_pid, old_pid_created_at,
            log_path=Path(log_path) if log_path else None,
            require_identity=require_verified_identity,
        )
    except launcher.LauncherError as exc:
        message = str(exc)
        phase_name = "timed_out" if "timeout" in message.lower() or "remained" in message.lower() else "failed"
        raise _ServiceStopFailure(phase_name, message) from exc
    if not result.get("stopped"):
        raise _ServiceStopFailure("failed", "Pan launcher did not confirm service stop")


def run_exit_supervisor(job_id: str, root: str, port: int, old_pid: int | None = None,
                        old_pid_created_at: float | None = None,
                        registry_root: str | None = None) -> int:
    """Stop one verified Pan service and persist the legal offline terminal state."""
    root_path = Path(root).expanduser().resolve()
    job = background_jobs.get(job_id, registry_root) or {}
    log_path = job.get("logPath")
    try:
        old_pid = old_pid if old_pid is not None else job.get("oldPid")
        old_pid_created_at = (
            old_pid_created_at if old_pid_created_at is not None
            else job.get("oldPidCreatedAt")
        )
        _stop_service(
            job_id=job_id, root_path=root_path, port=port, old_pid=old_pid,
            old_pid_created_at=old_pid_created_at, log_path=log_path,
            phase="stopping_service", require_verified_identity=True,
            registry_root=registry_root,
        )
        background_jobs.transition_service_job(
            job_id, "offline", registry_root=registry_root,
            # The service being offline does not imply that the whole Exit
            # Job succeeded.  transition_service_job deliberately preserves
            # any Worker/step error already recorded on the Job.
            oldPid=old_pid, oldPidCreatedAt=old_pid_created_at,
        )
        return 0
    except _ServiceStopFailure as exc:
        return _fail(job_id, exc.phase, str(exc), registry_root)
    except Exception as exc:
        return _fail(job_id, "failed", str(exc), registry_root)


def run_supervisor(job_id: str, root: str, port: int, old_pid: int | None = None,
                   old_pid_created_at: float | None = None,
                   registry_root: str | None = None) -> int:
    """Run the durable requested -> stopping -> ... -> ready sequence."""
    root_path = Path(root).expanduser().resolve()
    job = background_jobs.get(job_id, registry_root) or {}
    # Operation and options are deliberately read from the durable Job here;
    # the detached process must not depend on request-process memory or on a
    # future caller re-supplying policy arguments.  Phase one has no active
    # options, but validating the persisted shape keeps the boundary explicit.
    persisted_options = job.get("options", {})
    if not isinstance(persisted_options, dict):
        return _fail(job_id, "failed", "persisted lifecycle options are invalid", registry_root)
    if job.get("operation") == "exit":
        return run_exit_supervisor(
            job_id, root, port, old_pid, old_pid_created_at, registry_root,
        )
    log_path = job.get("logPath")
    try:
        _stop_service(
            job_id=job_id, root_path=root_path, port=port, old_pid=old_pid,
            old_pid_created_at=old_pid_created_at, log_path=log_path,
            phase="stopping", require_verified_identity=False,
            registry_root=registry_root,
        )
        background_jobs.transition_service_job(job_id, "stopped", registry_root=registry_root)
        background_jobs.transition_service_job(job_id, "starting", registry_root=registry_root)
        launcher.start_service(
            # Keep the historical 30-second launcher readiness barrier; the
            # larger supervisor budget below covers durable phase polling.
            root_path, timeout=launcher.READY_TIMEOUT_SEC,
            log_path=Path(log_path) if log_path else None,
        )
        deadline = time.monotonic() + START_TIMEOUT_SEC
        last_error = "new service did not become ready"
        while time.monotonic() < deadline:
            checks = ready_checks(root=str(root_path), port=port, old_pid=old_pid,
                                  old_pid_created_at=old_pid_created_at)
            if checks["ok"]:
                background_jobs.transition_service_job(
                    job_id, "ready", registry_root=registry_root,
                    newPid=checks["newPid"],
                    newPidCreatedAt=checks["newPidCreatedAt"], error=None,
                )
                return 0
            last_error = str(checks.get("error") or last_error)
            time.sleep(READY_POLL_SEC)
        return _fail(job_id, "timed_out", last_error, registry_root)
    except _ServiceStopFailure as exc:
        return _fail(job_id, exc.phase, str(exc), registry_root)
    except Exception as exc:
        return _fail(job_id, "failed", str(exc), registry_root)


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
    return run_supervisor(args.job_id, args.root, args.port, args.old_pid,
                          args.old_pid_created_at, args.registry_root)


if __name__ == "__main__":
    raise SystemExit(main())
