"""Product-owned lifecycle Action transport.

The public CLI and host console deliberately share this small adapter.  It
does not create a second executor: protected service effects are admitted by
``ProtectedLifecycleExecutor`` and their receipts live in the independent
execution store so they remain queryable after the Pan process goes offline.
"""
from __future__ import annotations

import hashlib
import math
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from packages.actions.store import system_definition


ACTION_ID = "system_main_lifecycle"
ACTION_ROUTE = "/api/system-actions/main-lifecycle"
OPERATIONS = frozenset({"start", "exit", "restart", "show", "hide"})
PROTECTED_OPERATIONS = frozenset({"exit", "restart"})


def receipt_root(root: str | Path) -> Path:
    """Return the checkout-local receipt directory used across service reloads."""
    return Path(root).expanduser().resolve() / "data" / "lifecycle-executions"


def _token(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:24]


def validate_operation(operation: Any) -> str:
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise ValueError("unsupported lifecycle Action operation")
    return operation


def fixed_definition() -> dict[str, Any]:
    """Read the immutable product definition, never a caller supplied Action."""
    definition = system_definition("main-lifecycle")
    if definition.get("id") != ACTION_ID or definition.get("system") is not True:
        raise ValueError("system_main_lifecycle definition is not canonical")
    return definition


def protected_plan(*, root: str | Path, port: int, old_pid: int,
                   old_pid_created_at: float, operation: str,
                   options: Mapping[str, Any] | None = None,
                   request_id: str | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Build one fixed Action plan and its caller-owned Attempt identity."""
    operation = validate_operation(operation)
    if operation not in PROTECTED_OPERATIONS:
        raise ValueError("only exit/restart are protected lifecycle effects")
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_absolute() or not root_path.is_dir():
        raise ValueError("lifecycle root must be an existing absolute directory")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("lifecycle port is invalid")
    if type(old_pid) is not int or old_pid <= 0:
        raise ValueError("lifecycle old PID is invalid")
    if type(old_pid_created_at) not in (int, float) or not math.isfinite(old_pid_created_at) or old_pid_created_at <= 0:
        raise ValueError("lifecycle old PID birth is invalid")
    request_id = request_id or _token("request_")
    # The host may retry a request after losing its HTTP response.  Stable
    # caller-owned identities make that retry a receipt lookup/admission fence,
    # never a second stop/start attempt.
    seed = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    job_id = "job_" + seed[:24]
    run_id = "run_" + seed[24:48]
    step_id = "step_main_lifecycle"
    attempt_id = "attempt_" + seed[48:72]
    key = {"runId": run_id, "stepId": step_id, "targetId": "main", "operationOrdinal": 0}
    attempt = {"attemptId": attempt_id, "runId": run_id, "stepId": step_id,
               "leaseId": "lease_" + seed[72:96], "ownerEpoch": 1, "generation": 1,
               "state": "running"}
    plan = {"operation": "service." + operation, "effect": "high_risk",
            "actionId": ACTION_ID, "arguments": {
                "root": str(root_path), "port": port, "oldPid": old_pid,
                "oldPidCreatedAt": float(old_pid_created_at),
                "options": dict(options or {}), "requestId": request_id,
            }}
    return job_id, {"attempt": attempt, "effectKey": key}, plan


def execute_local(operation: str, *, root: str | Path, timeout: float = 30,
                  hidden: bool | None = None) -> dict[str, Any]:
    """Execute a non-protected fixed Action locally (start/show/hide).

    Start/show/hide cannot be run by the detached service supervisor: start has
    no live service to call and console visibility belongs to the live host.
    Keeping them here still gives every CLI/host caller one fixed Action gate.
    """
    operation = validate_operation(operation)
    fixed_definition()
    if operation == "start":
        from packages.core import launcher
        return launcher.start_service(Path(root), timeout=timeout, hidden=hidden)
    if operation in {"show", "hide"}:
        from packages.core.host_console import console
        return {"ok": True, "console": console.change(operation)}
    raise ValueError("protected lifecycle operation requires canonical admission")


async def submit_protected(*, root: str | Path, operation: str, port: int,
                           old_pid: int, old_pid_created_at: float,
                           options: Mapping[str, Any] | None = None,
                           request_id: str | None = None, authorize=None,
                           launch=None) -> dict[str, Any]:
    """Admit a protected fixed Action through the canonical executor."""
    from packages.actions.typed_executor import TypedExecutor
    from packages.core.main_lifecycle import ProtectedLifecycleExecutor
    from packages.jobs.independent_executor import ExecutionStore, effect_id

    fixed_definition()
    job_id, identity, plan = protected_plan(
        root=root, port=port, old_pid=old_pid, old_pid_created_at=old_pid_created_at,
        operation=operation, options=options, request_id=request_id,
    )
    expected = {"jobId": job_id, "runId": identity["effectKey"]["runId"],
                "stepId": identity["effectKey"]["stepId"],
                "operation": plan["operation"], "root": plan["arguments"]["root"],
                "port": port, "stepCount": 1}
    grant = authorize or (lambda scope: dict(scope))
    protected = ProtectedLifecycleExecutor(
        job_id=job_id, store=ExecutionStore(receipt_root(root)),
        authorize=grant, launch=launch,
    )
    result = await TypedExecutor(lifecycle_executor=protected).execute(
        attempt=identity["attempt"], effect_key=identity["effectKey"],
        frozen_plan=plan,
    )
    pending = (result.get("outcome") == "unknown"
               and (result.get("receipt") or {}).get("code") == "execution_pending")
    return {"ok": result.get("outcome") == "succeeded" or pending,
            "accepted": result.get("outcome") == "succeeded" or pending,
            "actionId": ACTION_ID, "jobId": job_id,
            "requestId": plan["arguments"]["requestId"],
            "effectId": effect_id(identity["effectKey"]),
            "operation": operation, "receipt": result}


def wait_for_receipt(*, root: str | Path, effect_id: str, operation: str,
                     timeout: float, poll: float = .2) -> dict[str, Any]:
    """Read the original receipt; never submit or launch a second effect."""
    from packages.jobs.independent_executor import ExecutionStore
    validate_operation(operation)
    store = ExecutionStore(receipt_root(root))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            doc = store.get(effect_id)
        except KeyError:
            doc = None
        if doc is not None:
            if doc.get("effectId") != effect_id:
                raise RuntimeError("lifecycle receipt identity mismatch")
            result = doc.get("result")
            if result is not None:
                return result
        time.sleep(poll)
    raise TimeoutError(f"{operation} outcome unknown after {timeout:g}s; canonical receipt={effect_id}")


def run_wait_for_receipt(**kwargs):
    """Sync bridge kept separate so CLI/console do not own asyncio loops."""
    return wait_for_receipt(**kwargs)
