"""Durable jobs that are independent from Pan Worker processes.

The registry is deliberately file based for the MVP.  One JSON file per job
means a Runner can update its own state while Pan is down without touching
Session history or queue_pending.  Pan only projects a terminal outbox event
into queue_pending after the job fact has been committed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
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
    # Windows file scanners and a concurrently closing reader can briefly
    # deny the replace even while the registry lock is held. Retry the atomic
    # rename; never fall back to truncating the canonical record.
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.01 * (attempt + 1))


@contextmanager
def _job_lock(job_id: str):
    """Cross-process lock for a single job's read/modify/write transaction."""
    lock_path = _root() / "jobs" / f"{job_id}.lock"
    if os.name == "nt":
        # msvcrt byte-range locks are not reliable for this workload when
        # several fresh Python processes open/replace the same JSON quickly.
        # A named kernel mutex is process-wide, automatically released when a
        # process dies, and does not add a dependency to the MVP.
        import ctypes
        digest = hashlib.sha256(str(lock_path.resolve()).encode("utf-8")).hexdigest()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel32.CreateMutexW(None, False, f"Local\\PanBackgroundJob_{digest}")
        if not mutex:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        wait = kernel32.WaitForSingleObject(mutex, 0xFFFFFFFF)
        if wait not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            kernel32.CloseHandle(mutex)
            raise OSError(f"WaitForSingleObject failed: {wait}")
        try:
            yield
        finally:
            kernel32.ReleaseMutex(mutex)
            kernel32.CloseHandle(mutex)
        return
    lock_path.touch(exist_ok=True)
    handle = open(lock_path, "r+b")
    try:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _load_path(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save(job: dict) -> dict:
    with _lock, _job_lock(job["jobId"]):
        _atomic_write(_job_path(job["jobId"]), job)
    return job


def _create(job: dict) -> dict:
    """Create a job record atomically before its Runner is spawned."""
    with _lock, _job_lock(job["jobId"]):
        path = _job_path(job["jobId"])
        if path.exists():
            raise ValueError("job id already exists")
        _atomic_write(path, job)
    return job


def _update(job_id: str, changes: dict[str, Any], *, replace: dict | None = None) -> dict:
    """Read, modify, and atomically replace one job while holding its lock."""
    with _lock, _job_lock(job_id):
        path = _job_path(job_id)
        current = replace if replace is not None else _load_path(path)
        if not current:
            raise ValueError("job not found")
        current.update(changes)
        _atomic_write(path, current)
        return current


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
    _create(job)
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
        _update(job_id, {"status": "failed", "error": "runner spawn failed",
                         "notificationState": "pending", "updatedAt": time.time()})
        raise
    finally:
        log.close()
    return _update(job_id, {"status": "running", "runnerPid": proc.pid,
                             "runnerProcessCreatedAt": _process_create_time(proc.pid),
                             "updatedAt": time.time()})


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
        if expected is None or abs(p.create_time() - float(expected)) > 1.0:
            return None
        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
            return None
        return p
    except Exception:
        return None


def cancel(job_id: str) -> dict:
    job = get(job_id)
    if not job:
        raise ValueError("job not found")
    if job.get("status") in {"completed", "failed", "cancelled"}:
        return job
    proc = _owns_process(job)
    runner = _owns_process({"pid": job.get("runnerPid"),
                            "processCreatedAt": job.get("runnerProcessCreatedAt")})
    if job.get("pid") and proc is None:
        raise ValueError("cannot safely cancel: task PID identity is unavailable or reused")
    if not proc and not runner:
        raise ValueError("cannot safely cancel: Runner PID identity is unavailable")
    if proc:
        _kill_tree(proc)
    if runner and getattr(runner, "pid", None) != getattr(proc, "pid", None):
        _kill_tree(runner)
    with _lock, _job_lock(job_id):
        current = _load_path(_job_path(job_id))
        if not current:
            raise ValueError("job not found")
        if current.get("status") in {"completed", "failed", "cancelled"}:
            return current
        current.update(status="cancelled", exitCode=None, updatedAt=time.time(),
                       terminalEventId=f"{job_id}:terminal", notificationState="pending")
        _atomic_write(_job_path(job_id), current)
        return current


def _kill_tree(proc: Any) -> None:
    """Best-effort descendant-first termination for an owned process."""
    try:
        children = proc.children(recursive=True)
        for child in reversed(children):
            child.kill()
        proc.kill()
    except (OSError, AttributeError):
        pass


def retry(job_id: str) -> dict:
    old = get(job_id)
    if not old:
        raise ValueError("job not found")
    if old.get("status") in {"starting", "running"}:
        raise ValueError("running jobs cannot be retried; cancel them first")
    if old.get("status") not in {"completed", "failed", "cancelled"}:
        raise ValueError("job is not retryable")
    new_job = start(old["targetSessionId"], old["argv"], old["cwd"], label=old.get("label"))
    new_job["retryOf"] = job_id
    return _update(new_job["jobId"], {"retryOf": job_id})


def runner_update(job_id: str, **changes: Any) -> dict:
    job = get(job_id)
    if not job:
        raise ValueError("job not found")
    changes = dict(changes)
    if changes.get("status") in {"completed", "failed", "cancelled"}:
        changes.setdefault("terminalEventId", f"{job_id}:terminal")
        changes["notificationState"] = "pending"
    changes["updatedAt"] = time.time()
    return _update(job_id, changes)


def reconcile_running() -> int:
    """Resolve running records whose independent Runner disappeared.

    A live Runner is the safe re-attach case. Missing identity, unavailable
    liveness inspection, PID reuse, or a dead Runner is persisted as failed;
    we never kill a process when its creation time cannot be verified.
    """
    changed = 0
    for job in list_jobs():
        if job.get("status") not in {"starting", "running"}:
            continue
        runner_pid = job.get("runnerPid")
        runner_created = job.get("runnerProcessCreatedAt")
        runner = _owns_process({"pid": runner_pid, "processCreatedAt": runner_created})
        if runner is not None:
            continue
        reason = "runner identity missing, unavailable, dead, or PID reused"
        _update(job["jobId"], {"status": "failed", "error": "orphaned: " + reason,
                                "terminalEventId": f"{job['jobId']}:terminal",
                                "notificationState": "pending", "updatedAt": time.time()})
        changed += 1
    return changed


async def recover_notifications() -> int:
    reconcile_running()
    delivered = 0
    for job in list_jobs():
        if job.get("status") not in {"completed", "failed", "cancelled"} or job.get("notificationState") == "delivered":
            continue
        # Hold the cross-process job lock through projection and the delivered
        # mark. A second Pan instance cannot perform the same terminal event
        # concurrently, while enqueue_notice remains idempotent after a crash.
        with _lock, _job_lock(job["jobId"]):
            current = _load_path(_job_path(job["jobId"]))
            if (not current or current.get("notificationState") == "delivered"
                    or current.get("status") not in {"completed", "failed", "cancelled"}):
                continue
            target = current.get("targetSessionId")
            event_id = current.get("terminalEventId") or f"{current['jobId']}:terminal"
            text = json.dumps({"jobId": current["jobId"], "status": current["status"], "exitCode": current.get("exitCode"), "logPath": current.get("logPath")}, ensure_ascii=False)
            result = await _worker.enqueue_notice(target, text, source="automation", event_id=event_id)
            if result.get("ok"):
                current["notificationState"] = "delivered"
                current["terminalEventId"] = event_id
                current["updatedAt"] = time.time()
                _atomic_write(_job_path(current["jobId"]), current)
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
