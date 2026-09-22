"""Durable lifecycle supervisor for the Pan main service.

The durable Job state remains here, but all Windows process work is delegated
to :mod:`packages.core.launcher`.  There is no stop/start batch-script hop:
the supervisor invokes the same verified Python launcher primitives used by
the one-click entry point.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from packages.core import background_jobs
from packages.core import launcher

# The launcher waits for the API and may also start the optional tunnel. Keep
# this budget separate from later lifecycle polling.
START_TIMEOUT_SEC = 90.0
READY_POLL_SEC = 0.5


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
