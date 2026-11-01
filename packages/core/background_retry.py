"""Durable opt-in retry policy for legacy background process Jobs only.

Commands may have side effects. Enabling retries requires an idempotency key
or a concrete deduplication guarantee supplied by the caller. The key is audit
metadata; the command remains responsible for enforcing it.
"""
from __future__ import annotations

import math
import secrets

REASONS = frozenset({"failed", "orphaned", "timeout", "interrupted", "cancelled",
                     "dependency-unavailable"})
TERMINAL = frozenset({"completed", "failed", "cancelled"})


def policy(retry_if=None, max_retries=0, backoff_sec=0, *,
           idempotency_key=None, deduplication=None):
    if retry_if is None:
        retry_if = []
    if (not isinstance(retry_if, list)
            or any(not isinstance(r, str) or r not in REASONS for r in retry_if)):
        raise ValueError("retryIf must be a list of stable terminal reasons")
    if type(max_retries) is not int or max_retries < 0:
        raise ValueError("maxRetries must be a nonnegative integer")
    if (type(backoff_sec) not in {int, float} or not math.isfinite(backoff_sec)
            or backoff_sec < 0):
        raise ValueError("backoffSec must be finite and nonnegative")
    for name, value in (("idempotencyKey", idempotency_key), ("deduplication", deduplication)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(name + " must be a nonempty string")
    if retry_if and not (idempotency_key or deduplication):
        raise ValueError("retryIf requires idempotencyKey or deduplication; command must enforce deduplication")
    return dict(retryIf=list(dict.fromkeys(retry_if)), maxRetries=max_retries,
                backoffSec=backoff_sec, idempotencyKey=idempotency_key,
                deduplication=deduplication)


def eligible(job):
    # Action/scheduler executors own their own attempts and side-effect rules.
    return (job.get("kind", "background-process") == "background-process"
            and not job.get("scheduledParentJobId") and not job.get("action")
            and job.get("executionOwner") != "independent-job-service")


def defaults(job):
    if eligible(job):
        for key, value in dict(retryIf=[], maxRetries=0, backoffSec=0, attempt=1,
                               lastReason=None, nextRetryAt=None).items():
            job.setdefault(key, value)
        job["remaining"] = max(0, job["maxRetries"] - (job["attempt"] - 1))
    return job


def terminal(job, now):
    """Finalize an attempt exactly once, in the caller's registry transaction."""
    defaults(job)
    if not eligible(job) or job.get("status") not in TERMINAL:
        return
    job.setdefault("attemptId", secrets.token_hex(16))
    job.setdefault("attemptCreatedAt", job.get("createdAt", now))
    if job.get("status") == "cancelled" or job.get("cancelRequestedAt"):
        job.update(status="cancelled", reason="cancelled", nextRetryAt=None)
    reason = job.get("reason")
    if job["status"] == "failed" and reason not in REASONS:
        reason = "failed"
    elif job["status"] == "completed":
        reason = None
    job["reason"] = reason
    if reason:
        job["lastReason"] = reason
    if job["attempt"] > 1:
        suffix = "cancelled:terminal" if reason == "cancelled" else "terminal"
        job["terminalEventId"] = f"{job['jobId']}:attempt:{job['attemptId']}:{suffix}"
    history = job.setdefault("retryAttempts", [])
    if not history or history[-1]["attempt"] != job["attempt"]:
        history.append(dict(attempt=job["attempt"], attemptId=job.get("attemptId"),
                            createdAt=job.get("attemptCreatedAt", job.get("createdAt")),
                            endedAt=now, status=job["status"], reason=reason,
                            exitCode=job.get("exitCode"), error=job.get("error")))
        if (reason and reason != "cancelled" and reason in job["retryIf"]
                and job["remaining"] > 0
                and (job.get("idempotencyKey") or job.get("deduplication"))):
            job["nextRetryAt"] = now + job["backoffSec"]
    if reason == "cancelled" or not reason or job["remaining"] == 0:
        job["nextRetryAt"] = None


def new_attempt(job, now):
    job["attempt"] += 1
    job.update(attemptId=secrets.token_hex(16), attemptCreatedAt=now,
               status="starting", nextRetryAt=None, reason=None,
               updatedAt=now, notificationState="pending", terminalEventId=None)
    for key in ("pid", "processCreatedAt", "processFiletime", "processJobHandle",
                "runnerPid", "runnerProcessCreatedAt", "runnerFiletime", "runnerClaim",
                "runnerSpawnStartedAt", "exitCode", "error", "processTreeEnded",
                "terminalBroadcastedAt", "terminalBroadcastEventId", "retryBlockedReason"):
        job.pop(key, None)
    defaults(job)
