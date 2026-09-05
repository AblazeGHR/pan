"""Small, durable supervisor helpers for the Pan main-service lifecycle.

This module intentionally owns no Session and never writes queue_pending.  The
PowerShell hop runs it from outside the Pan process tree, so it can record the
stop/start result after the old service has exited.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

from packages.core import background_jobs

STOP_TIMEOUT_SEC = 20.0
START_TIMEOUT_SEC = 45.0
READY_POLL_SEC = 0.5
SERVICE_ENTRY_MARKERS = ("main.py", "packages.web.server", "packages/web/server.py", "uvicorn")


def process_create_time(pid: int | None) -> float | None:
    if not pid:
        return None
    try:
        import psutil
        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        return None


def listener_owner(port: int) -> int | None:
    """Return the PID listening on the local TCP port, if any."""
    try:
        import psutil
        for conn in psutil.net_connections(kind="tcp"):
            address = conn.laddr
            local_port = getattr(address, "port", None)
            if local_port is None and isinstance(address, (tuple, list)) and len(address) > 1:
                local_port = address[1]
            if int(local_port or -1) == int(port) and str(conn.status).upper() in {
                "LISTEN", "LISTENING", str(getattr(psutil, "CONN_LISTEN", "LISTEN")).upper(),
            }:
                return int(conn.pid) if conn.pid else None
    except Exception:
        return None
    return None


def _normal_path(value: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(value))).rstrip("\\/")


def _under_root(value: str, root: str) -> bool:
    candidate, base = _normal_path(value), _normal_path(root)
    return candidate == base or candidate.startswith(base + os.sep)


def _has_root_marker(cmdline: list[str], cwd: str | None, root: str) -> bool:
    return any(_under_root(item, root) for item in (cmdline or []) if item) or bool(
        cwd and _under_root(cwd, root)
    )


def service_process_identity(pid: int | None, root: str,
                             expected_created_at: float | None = None) -> dict[str, Any]:
    """Validate a candidate Pan process without killing or mutating it."""
    result: dict[str, Any] = {
        "pid": pid, "createdAt": None, "rootMarker": False,
        "entryMarker": False, "ok": False, "error": None,
    }
    if not pid:
        result["error"] = "service PID is missing"
        return result
    try:
        import psutil
        proc = psutil.Process(int(pid))
        created = float(proc.create_time())
        cmdline = [str(value) for value in (proc.cmdline() or [])]
        cwd = None
        try:
            cwd = proc.cwd()
        except Exception:
            pass
        result.update(createdAt=created, cmdline=cmdline, cwd=cwd)
        if expected_created_at is None:
            result["error"] = "expected PID creation time is missing"
            return result
        if abs(created - float(expected_created_at)) > 1.0:
            result["error"] = "service PID creation time does not match"
            return result
        result["rootMarker"] = _has_root_marker(cmdline, cwd, root)
        haystack = " ".join(cmdline).replace("\\", "/").lower()
        result["entryMarker"] = any(marker in haystack for marker in SERVICE_ENTRY_MARKERS)
        if not result["rootMarker"]:
            result["error"] = "service process is outside the target checkout"
        elif not result["entryMarker"]:
            result["error"] = "service process command line has no Pan entry marker"
        elif not proc.is_running():
            result["error"] = "service process is not running"
        else:
            result["ok"] = True
    except Exception as exc:
        result["error"] = f"service process inspection failed: {exc}"
    return result


def _health_ready(port: int, timeout: float = 1.0) -> tuple[bool, str | None]:
    url = f"http://127.0.0.1:{int(port)}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return False, f"health returned HTTP {response.status}"
            body = json.loads(response.read().decode("utf-8"))
            if body.get("status") not in {"ok", "healthy"}:
                return False, f"health status is {body.get('status')!r}"
            return True, None
    except Exception as exc:
        return False, f"health probe failed: {exc}"


def ready_checks(*, root: str, port: int, old_pid: int | None,
                 old_pid_created_at: float | None) -> dict[str, Any]:
    """Require new process identity, port ownership, and HTTP readiness."""
    pid = listener_owner(port)
    result: dict[str, Any] = {"newPid": pid, "newPidCreatedAt": process_create_time(pid), "ok": False}
    if not pid:
        result["error"] = "target port has no listener"
        return result
    if old_pid and pid == old_pid:
        result["error"] = "target port is still owned by the old PID"
        return result
    if old_pid and old_pid_created_at is not None and process_create_time(old_pid) == old_pid_created_at:
        result["error"] = "old service PID is still alive"
        return result
    identity = service_process_identity(pid, root, result["newPidCreatedAt"])
    result["identity"] = identity
    if not identity["ok"]:
        result["error"] = identity["error"]
        return result
    healthy, error = _health_ready(port)
    result["health"] = healthy
    if not healthy:
        result["error"] = error
        return result
    result["ok"] = True
    return result


def _run_script(path: Path, root: Path, log_path: str | None, timeout: float) -> subprocess.CompletedProcess:
    log = open(log_path, "ab") if log_path else subprocess.DEVNULL
    try:
        return subprocess.run(
            ["cmd.exe", "/d", "/c", str(path)], cwd=str(root),
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            timeout=timeout, check=False,
        )
    finally:
        if log is not subprocess.DEVNULL:
            log.close()


def _fail(job_id: str, phase: str, message: str, registry_root: str | None = None) -> int:
    try:
        background_jobs.transition_service_job(job_id, phase, registry_root=registry_root, error=message)
    except Exception:
        # The original exception/exit code is still returned to the detached
        # caller; do not hide a stop/start failure behind a registry write.
        pass
    return 1


def run_supervisor(job_id: str, root: str, port: int, old_pid: int | None = None,
                   old_pid_created_at: float | None = None,
                   registry_root: str | None = None) -> int:
    """Run the durable requested -> stopping -> ... -> ready sequence."""
    root_path = Path(root).expanduser().resolve()
    job = background_jobs.get(job_id, registry_root) or {}
    log_path = job.get("logPath")
    try:
        if old_pid and not service_process_identity(old_pid, str(root_path), old_pid_created_at)["ok"]:
            return _fail(job_id, "failed", "old service identity could not be verified", registry_root)
        background_jobs.transition_service_job(job_id, "stopping", registry_root=registry_root)
        stop = _run_script(root_path / "scripts" / "stop_pan.bat", root_path, log_path, STOP_TIMEOUT_SEC)
        if stop.returncode != 0:
            return _fail(job_id, "failed", f"stop_pan.bat failed with exit code {stop.returncode}", registry_root)
        deadline = time.monotonic() + STOP_TIMEOUT_SEC
        while time.monotonic() < deadline:
            owner = listener_owner(port)
            if owner is None:
                break
            time.sleep(READY_POLL_SEC)
        else:
            return _fail(job_id, "failed", "old listener remained after stop", registry_root)
        if listener_owner(port) is not None:
            return _fail(job_id, "failed", "old listener remained after stop", registry_root)
        if old_pid and service_process_identity(
                old_pid, str(root_path), old_pid_created_at).get("ok"):
            return _fail(job_id, "failed", "old service process remained after stop", registry_root)
        background_jobs.transition_service_job(job_id, "stopped", registry_root=registry_root)
        background_jobs.transition_service_job(job_id, "starting", registry_root=registry_root)
        start = _run_script(root_path / "scripts" / "start_pan.bat", root_path, log_path, START_TIMEOUT_SEC)
        if start.returncode != 0:
            return _fail(job_id, "failed", f"start_pan.bat failed with exit code {start.returncode}", registry_root)
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
    except subprocess.TimeoutExpired as exc:
        return _fail(job_id, "timed_out", f"restart script timed out: {exc}", registry_root)
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
