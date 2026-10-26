"""Read-only migration fallback for the pre-P5b lifecycle transport.

This module is intentionally not imported by the normal CLI path.  It remains
available for an explicitly requested fallback while the old supervisor and
registry records are being drained.
"""
from __future__ import annotations

import re
import time

from . import launcher


def run(root, operation, timeout, choice, *, verified, request):
    port, record, _ = verified(root)
    payload = {"options": {"markRunningSessionsOffline": choice}} if choice is not None else {}
    result = request(port, f"/api/main/{operation}", payload, record)
    job_id = result.get("jobId", "")
    if not result.get("ok") or not re.fullmatch(r"job_[a-zA-Z0-9_-]+", job_id):
        raise launcher.LauncherError(f"{operation} was not accepted by legacy fallback: {result}")
    path = root / "data/background_jobs/jobs" / (job_id + ".json")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = launcher._read_json(path) or {}
        if job and (job.get("operation") != operation or job.get("oldPid") != record["pid"]
                    or job.get("oldPidCreatedAt") != record["createdAt"]
                    or job.get("requestId") != result.get("requestId")):
            raise launcher.LauncherError("Legacy lifecycle Job identity mismatch")
        phase = job.get("phase")
        if phase in {"failed", "timed_out"} or job.get("error"):
            raise launcher.LauncherError(f"{operation} legacy fallback failed: {job}")
        if phase == ("offline" if operation == "exit" else "ready"):
            return {"ok": True, "phase": phase, "jobId": job_id}
        time.sleep(0.2)
    raise launcher.LauncherError(f"{operation} legacy fallback outcome unknown after {timeout:g}s")
