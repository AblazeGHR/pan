"""Durable jobs that are independent from Pan Worker processes.

The registry is deliberately file based for the MVP.  One JSON file per job
means a Runner can update its own state while Pan is down without touching
Session history or queue_pending.  Pan only projects a terminal outbox event
into queue_pending after the job fact has been committed.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from packages.core import session as _sessions
from packages.core import worker as _worker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = PROJECT_ROOT / "data" / "background_jobs"
_lock = threading.RLock()
_recovery_task: asyncio.Task | None = None
_stop_recovery = asyncio.Event()


def _root() -> Path:
    value = os.environ.get("PAN_BACKGROUND_JOBS_DIR")
    root = Path(value).expanduser() if value else DEFAULT_ROOT
    (root / "jobs").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root


def _job_path(job_id: str) -> Path:
    if not job_id or Path(job_id).name != job_id or not job_id.startswith("job_"):
        raise ValueError("invalid job id")
    return _root() / "jobs" / f"{job_id}.json"


def _atomic_write(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + f".{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load_path(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save(job: dict) -> dict:
    with _lock:
        _atomic_write(_job_path(job["jobId"]), job)
    return job


def get(job_id: str) -> dict | None:
    try:
        return _load_path(_job_path(job_id))
    except ValueError:
        return None


def list_jobs() -> list[dict]:
    root = _root() / "jobs"
    jobs = [_load_path(p) for p in root.glob("job_*.json")]
    return sorted((j for j in jobs if j), key=lambda j: j.get("createdAt", ""), reverse=True)


def _validate_command(argv: Any, cwd: Any) -> tuple[list[str], Path]:
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
        raise ValueError("argv must be a non-empty string array")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("cwd is required")
    path = Path(cwd).expanduser().resolve()
    allowed = PROJECT_ROOT.resolve()
    if not (path == allowed or allowed in path.parents):
        raise ValueError("cwd must be inside the Pan project directory")
    if not path.is_dir():
        raise ValueError("cwd does not exist or is not a directory")
    return list(argv), path


def _runner_command(job_id: str) -> list[str]:
    return [sys.executable, "-m", "packages.core.background_runner", "--job-id", job_id]


def start(target_session_id: str, argv: list[str], cwd: str, *, label: str | None = None) -> dict:
    if not _sessions.get(target_session_id):
        raise ValueError("target session does not exist")
    argv, cwd_path = _validate_command(argv, cwd)
    job_id = "job_" + secrets.token_hex(12)
    now = time.time()
    log_path = _root() / "logs" / f"{job_id}.log"
    job = {
        "jobId": job_id, "targetSessionId": target_session_id, "argv": argv,
        "commandSummary": " ".join(argv[:3]) + (" …" if len(argv) > 3 else ""),
        "cwd": str(cwd_path), "label": label, "status": "starting",
        "createdAt": now, "updatedAt": now, "pid": None, "processCreatedAt": None,
        "logPath": str(log_path), "notificationState": "pending", "terminalEventId": None,
    }
    _save(job)
    log = open(log_path, "ab")
    kwargs: dict[str, Any] = {"cwd": str(cwd_path), "stdout": log, "stderr": subprocess.STDOUT,
                              "stdin": subprocess.DEVNULL, "close_fds": True,
                              "env": {**os.environ, "PYTHONPATH": str(PROJECT_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(_runner_command(job_id), **kwargs)
    except Exception:
        log.close()
        job.update(status="failed", error="runner spawn failed", notificationState="pending", updatedAt=time.time())
        _save(job)
        raise
    finally:
        log.close()
    job.update(status="running", pid=proc.pid, processCreatedAt=_process_create_time(proc.pid), updatedAt=time.time())
    return _save(job)


def _process_create_time(pid: int | None) -> float | None:
    if not pid:
        return None
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except Exception:
        return None


def _owns_process(job: dict) -> Any:
    try:
        import psutil
        p = psutil.Process(int(job["pid"]))
        expected = job.get("processCreatedAt")
        if expected is not None and abs(p.create_time() - float(expected)) > 1.0:
            return None
        return p
    except (ImportError, ValueError, KeyError, OSError):
        return None


def cancel(job_id: str) -> dict:
    job = get(job_id)
    if not job:
        raise ValueError("job not found")
    if job.get("status") in {"completed", "failed", "cancelled"}:
        return job
    proc = _owns_process(job)
    if proc:
        try:
            children = proc.children(recursive=True)
            for child in reversed(children):
                child.kill()
            proc.kill()
        except OSError:
            pass
    job.update(status="cancelled", exitCode=None, updatedAt=time.time(),
                terminalEventId=f"{job_id}:terminal")
    return _save(job)


def retry(job_id: str) -> dict:
    old = get(job_id)
    if not old:
        raise ValueError("job not found")
    new_job = start(old["targetSessionId"], old["argv"], old["cwd"], label=old.get("label"))
    new_job["retryOf"] = job_id
    return _save(new_job)


def runner_update(job_id: str, **changes: Any) -> dict:
    job = get(job_id)
    if not job:
        raise ValueError("job not found")
    job.update(changes, updatedAt=time.time())
    if job.get("status") in {"completed", "failed", "cancelled"}:
        job.setdefault("terminalEventId", f"{job_id}:terminal")
        job["notificationState"] = "pending"
    return _save(job)


async def recover_notifications() -> int:
    delivered = 0
    for job in list_jobs():
        if job.get("status") not in {"completed", "failed", "cancelled"} or job.get("notificationState") == "delivered":
            continue
        target = job.get("targetSessionId")
        event_id = job.get("terminalEventId") or f"{job['jobId']}:terminal"
        text = json.dumps({"jobId": job["jobId"], "status": job["status"], "exitCode": job.get("exitCode"), "logPath": job.get("logPath")}, ensure_ascii=False)
        result = await _worker.enqueue_notice(target, text, source="background_job", event_id=event_id)
        if result.get("ok"):
            job["notificationState"] = "delivered"
            _save(job)
            delivered += 1
    return delivered


async def _recovery_loop() -> None:
    while not _stop_recovery.is_set():
        try:
            await recover_notifications()
        except Exception:
            pass
        try:
            await asyncio.wait_for(_stop_recovery.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass


def start_recovery_loop() -> asyncio.Task:
    global _recovery_task, _stop_recovery
    if _recovery_task and not _recovery_task.done():
        return _recovery_task
    _stop_recovery = asyncio.Event()
    _recovery_task = asyncio.create_task(_recovery_loop(), name="background-job-recovery")
    return _recovery_task


async def stop_recovery_loop() -> None:
    if _recovery_task and not _recovery_task.done():
        _stop_recovery.set()
        await _recovery_task
