"""Durable jobs that are independent from Pan Worker processes.

The registry is deliberately file based for the MVP.  One JSON file per job
means a Runner can update its own state while Pan is down without touching
Session history or queue_pending.  Pan only projects a terminal outbox event
into queue_pending after the job fact has been committed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
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

# The registry is shared by the generic background-process Jobs and the small
# number of service lifecycle Jobs.  Keep the latter deliberately data-only:
# they do not have a target Session and never participate in queue_pending.
BACKGROUND_PROCESS_KIND = "background-process"
SESSION_MESSAGE_KIND = "session-message"
SESSION_BROADCAST_KIND = "session-broadcast"
SERVICE_LIFECYCLE_KIND = "main-lifecycle"
SERVICE_ACTIVE_PHASES = frozenset({
    "requested", "stopping", "stopping_workers", "stopping_service",
    "stopped", "starting",
})
SERVICE_TERMINAL_PHASES = frozenset({"ready", "offline", "failed", "timed_out"})


def _root(registry_root: str | Path | None = None) -> Path:
    value = registry_root or os.environ.get("PAN_BACKGROUND_JOBS_DIR")
    root = Path(value).expanduser() if value else DEFAULT_ROOT
    (root / "jobs").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root


def _job_path(job_id: str, registry_root: str | Path | None = None) -> Path:
    if not job_id or Path(job_id).name != job_id or not job_id.startswith("job_"):
        raise ValueError("invalid job id")
    return _root(registry_root) / "jobs" / f"{job_id}.json"


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
def _windows_named_mutex(lock_path: Path, namespace: str):
    """Acquire a named Windows mutex with correctly typed Win32 handles."""
    import ctypes
    from ctypes import wintypes

    digest = hashlib.sha256(str(lock_path.resolve()).encode("utf-8")).hexdigest()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    mutex = kernel32.CreateMutexW(
        None, False, f"{namespace}{digest}")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    wait = kernel32.WaitForSingleObject(mutex, 0xFFFFFFFF)
    if wait not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
        error = ctypes.get_last_error()
        kernel32.CloseHandle(mutex)
        if wait == 0xFFFFFFFF:  # WAIT_FAILED
            raise ctypes.WinError(error)
        raise OSError(f"WaitForSingleObject failed: {wait}")
    try:
        yield
    finally:
        try:
            if not kernel32.ReleaseMutex(mutex):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if not kernel32.CloseHandle(mutex):
                raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def _job_lock(job_id: str, registry_root: str | Path | None = None):
    """Cross-process lock for a single job's read/modify/write transaction."""
    lock_path = _root(registry_root) / "jobs" / f"{job_id}.lock"
    if os.name == "nt":
        # msvcrt byte-range locks are not reliable for this workload when
        # several fresh Python processes open/replace the same JSON quickly.
        # A named kernel mutex is process-wide, automatically released when a
        # process dies, and does not add a dependency to the MVP.
        with _windows_named_mutex(lock_path, "Local\\PanBackgroundJob_"):
            yield
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


@contextmanager
def _registry_lock(name: str, registry_root: str | Path | None = None):
    """Cross-process lock for a registry-wide key (for example root+port)."""
    lock_path = _root(registry_root) / "jobs" / f".{name}.lock"
    if os.name == "nt":
        with _windows_named_mutex(lock_path, "Local\\PanBackgroundRegistry_"):
            yield
        return
    lock_path.touch(exist_ok=True)
    handle = open(lock_path, "r+b")
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _load_path(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _normalize_job(job: dict | None) -> dict | None:
    """Add read-time defaults without rewriting legacy JSON files."""
    if not job:
        return job
    result = dict(job)
    result.setdefault("kind", BACKGROUND_PROCESS_KIND)
    result.setdefault("operation", "run")
    # T-046: creator and target are independent identities.  Old records did
    # not persist creatorSessionId, so keep them readable with a null creator.
    if result.get("kind") in {SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}:
        result.setdefault("creatorSessionId", result.get("sourceSessionId"))
    else:
        result.setdefault("creatorSessionId", None)
    if result.get("kind") == SERVICE_LIFECYCLE_KIND:
        result.setdefault("options", {})
        # ``errors`` was added after the first lifecycle Job schema.  Keep
        # old JSON readable and expose a legacy scalar error as one item,
        # without rewriting the persisted record just by reading it.
        errors = result.get("errors")
        if not isinstance(errors, list):
            errors = []
        if result.get("error") and result["error"] not in errors:
            errors.append(result["error"])
        result["errors"] = errors
    return result


def _save(job: dict, registry_root: str | Path | None = None) -> dict:
    with _lock, _job_lock(job["jobId"], registry_root):
        _atomic_write(_job_path(job["jobId"], registry_root), job)
    return job


def _create(job: dict, registry_root: str | Path | None = None) -> dict:
    """Create a job record atomically before its Runner is spawned."""
    with _lock, _job_lock(job["jobId"], registry_root):
        path = _job_path(job["jobId"], registry_root)
        if path.exists():
            raise ValueError("job id already exists")
        _atomic_write(path, job)
    return job


def _update(job_id: str, changes: dict[str, Any], *, replace: dict | None = None,
            registry_root: str | Path | None = None) -> dict:
    """Read, modify, and atomically replace one job while holding its lock."""
    with _lock, _job_lock(job_id, registry_root):
        path = _job_path(job_id, registry_root)
        current = replace if replace is not None else _load_path(path)
        if not current:
            raise ValueError("job not found")
        current.update(changes)
        _atomic_write(path, current)
        return current


def get(job_id: str, registry_root: str | Path | None = None) -> dict | None:
    try:
        return _normalize_job(_load_path(_job_path(job_id, registry_root)))
    except ValueError:
        return None


def list_jobs(registry_root: str | Path | None = None) -> list[dict]:
    root = _root(registry_root) / "jobs"
    jobs = [_normalize_job(_load_path(p)) for p in root.glob("job_*.json")]
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
    from packages.core.config import resolve_pan_python_argv

    return [*resolve_pan_python_argv(), "-m", "packages.core.background_runner", "--job-id", job_id]


def start(target_session_id: str, argv: list[str], cwd: str, *,
          label: str | None = None,
          creator_session_id: str | None = None) -> dict:
    if not _sessions.get(target_session_id):
        raise ValueError("target session does not exist")
    creator_sid, creator_error = _worker._normalize_source_session_id(creator_session_id)
    if creator_error:
        raise ValueError(creator_error)
    argv, cwd_path = _validate_command(argv, cwd)
    job_id = "job_" + secrets.token_hex(12)
    now = time.time()
    log_path = _root() / "logs" / f"{job_id}.log"
    job = {
        "jobId": job_id, "targetSessionId": target_session_id, "argv": argv,
        "kind": BACKGROUND_PROCESS_KIND, "operation": "run",
        "creatorSessionId": creator_sid,
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


# ---------------------------------------------------------------------------
# Durable Session-message Jobs
# ---------------------------------------------------------------------------

_CLOCK_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")
MESSAGE_JOB_TERMINAL = frozenset({"completed", "failed", "cancelled"})
MESSAGE_JOB_ACTIVE = frozenset({"pending", "scheduled", "running"})
MESSAGE_JOB_REQUEUE_AFTER_SEC = 5.0


def _iso_utc(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_at(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("schedule.at must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        result = parsed.timestamp()
    else:
        raise ValueError("schedule.at must be an ISO-8601 timestamp")
    if not math.isfinite(result):
        raise ValueError("schedule.at must be finite")
    return result


def _positive_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be a positive number")
    return result


def _weekly_timezone(name: Any):
    if name in (None, "", "local"):
        return datetime.now().astimezone().tzinfo
    if name in ("UTC", "Z", "+00:00"):
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(name))
    except Exception as exc:
        raise ValueError("schedule.timezone must be local, UTC, or a valid IANA zone") from exc


def _next_weekly(schedule: dict, after: float) -> float:
    weekday = schedule["weekday"]
    clock = schedule["time"]
    parts = [int(part) for part in clock.split(":")]
    hour, minute = parts[:2]
    second = parts[2] if len(parts) == 3 else 0
    tz = _weekly_timezone(schedule.get("timezone"))
    current = datetime.fromtimestamp(after, tz)
    delta = (weekday - current.weekday()) % 7
    candidate = current.replace(hour=hour, minute=minute, second=second, microsecond=0)
    candidate += timedelta(days=delta)
    if candidate.timestamp() <= after:
        candidate += timedelta(days=7)
    return candidate.timestamp()


def _normalize_message_schedule(schedule: Any, now: float) -> tuple[dict, float]:
    if not isinstance(schedule, dict):
        raise ValueError("schedule must be an object")
    kind = schedule.get("type")
    if kind == "once":
        has_at = schedule.get("at") is not None
        has_delay = schedule.get("delaySeconds") is not None
        if has_at == has_delay:
            raise ValueError("once schedule requires exactly one of at or delaySeconds")
        if has_at:
            at = _parse_at(schedule.get("at"))
            if at <= now:
                raise ValueError("schedule.at must be in the future")
            normalized = {"type": "once", "at": _iso_utc(at)}
            return normalized, at
        delay = _positive_seconds(schedule.get("delaySeconds"), "schedule.delaySeconds")
        return {"type": "once", "delaySeconds": delay}, now + delay
    if kind == "interval":
        interval = _positive_seconds(schedule.get("intervalSeconds"), "schedule.intervalSeconds")
        return {"type": "interval", "intervalSeconds": interval}, now + interval
    if kind == "weekly":
        try:
            weekday = int(schedule.get("weekday"))
        except (TypeError, ValueError) as exc:
            raise ValueError("schedule.weekday must be an integer from 0 (Monday) to 6 (Sunday)") from exc
        if weekday not in range(7):
            raise ValueError("schedule.weekday must be an integer from 0 (Monday) to 6 (Sunday)")
        clock = schedule.get("time")
        if not isinstance(clock, str) or not _CLOCK_RE.match(clock):
            raise ValueError("schedule.time must be HH:MM or HH:MM:SS")
        # Validate the timezone now, so a typo cannot create a permanently
        # un-runnable persisted Job.
        _weekly_timezone(schedule.get("timezone"))
        normalized = {"type": "weekly", "weekday": weekday, "time": clock}
        if schedule.get("timezone") not in (None, ""):
            normalized["timezone"] = schedule.get("timezone")
        return normalized, _next_weekly(normalized, now)
    raise ValueError("schedule.type must be once, interval, or weekly")


def _message_next_run(schedule: dict, after: float) -> float | None:
    if schedule.get("type") == "interval":
        return after + float(schedule["intervalSeconds"])
    if schedule.get("type") == "weekly":
        return _next_weekly(schedule, after)
    return None


def _normalize_target_session_ids(target_session_ids: Any) -> list[str]:
    if not isinstance(target_session_ids, list) or not target_session_ids:
        raise ValueError("targetSessionIds must be a non-empty array")
    result: list[str] = []
    seen: set[str] = set()
    for value in target_session_ids:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("targetSessionIds must contain non-empty strings")
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _message_job_common(text: str, schedule: dict, *,
                        source: str, source_session_id: str | None,
                        creator_session_id: str | None) -> tuple[str, str | None, str | None, dict, float, float]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is required")
    source_type, source_error = _worker._normalize_source_type(source)
    if source_error:
        raise ValueError(source_error)
    source_sid, source_error = _worker._normalize_source_session_id(source_session_id)
    if source_error:
        raise ValueError(source_error)
    creator_sid, creator_error = _worker._normalize_source_session_id(creator_session_id)
    if creator_error:
        raise ValueError(creator_error)
    now = time.time()
    normalized, next_run = _normalize_message_schedule(schedule, now)
    return source_type, source_sid, creator_sid, normalized, next_run, now


def start_message(target_session_id: str, text: str, schedule: dict, *,
                  description: str | None = None, source: str = "agent",
                  source_session_id: str | None = None,
                  creator_session_id: str | None = None,
                  registry_root: str | Path | None = None) -> dict:
    """Create a durable one-target Session message schedule.

    ``text`` is always message text delivered through ``worker.send_session``;
    it is never parsed as or executed through an operating-system shell.
    """
    if not isinstance(target_session_id, str) or not target_session_id:
        raise ValueError("target session does not exist")
    if not _sessions.get(target_session_id):
        raise ValueError("target session does not exist")
    source_type, source_sid, creator_sid, normalized, next_run, now = _message_job_common(
        text, schedule, source=source, source_session_id=source_session_id,
        creator_session_id=creator_session_id)
    job_id = "job_" + secrets.token_hex(12)
    job = {
        "jobId": job_id, "kind": SESSION_MESSAGE_KIND, "operation": "send",
        "targetSessionId": target_session_id, "text": text,
        "description": description if description is not None else "",
        "source": source_type, "sourceSessionId": source_sid,
        "creatorSessionId": creator_sid,
        "schedule": normalized, "nextRunAt": _iso_utc(next_run),
        "status": "pending", "runCount": 0, "lastRunAt": None,
        "lastDelivery": None, "lastError": None,
        "createdAt": now, "updatedAt": now,
    }
    return _create(job, registry_root)


def start_broadcast(target_session_ids: list[str], text: str, schedule: dict, *,
                    description: str | None = None, source: str = "agent",
                    source_session_id: str | None = None,
                    creator_session_id: str | None = None,
                    registry_root: str | Path | None = None) -> dict:
    """Create one durable scheduled fan-out Job for an ordered target list."""
    target_ids = _normalize_target_session_ids(target_session_ids)
    if any(not _sessions.get(session_id) for session_id in target_ids):
        raise ValueError("target session does not exist")
    source_type, source_sid, creator_sid, normalized, next_run, now = _message_job_common(
        text, schedule, source=source, source_session_id=source_session_id,
        creator_session_id=creator_session_id)
    job_id = "job_" + secrets.token_hex(12)
    job = {
        "jobId": job_id, "kind": SESSION_BROADCAST_KIND, "operation": "broadcast",
        "targetSessionIds": target_ids, "text": text,
        "description": description if description is not None else "",
        "source": source_type, "sourceSessionId": source_sid,
        "creatorSessionId": creator_sid,
        "schedule": normalized, "nextRunAt": _iso_utc(next_run),
        "status": "pending", "runCount": 0, "lastRunAt": None,
        "lastDelivery": None, "lastError": None,
        "createdAt": now, "updatedAt": now,
    }
    return _create(job, registry_root)


def update_message(job_id: str, *, text: str | None = None,
                   schedule: dict | None = None, description: str | None = None,
                   target_session_ids: list[str] | None = None,
                   registry_root: str | Path | None = None) -> dict:
    """Edit a non-terminal message or broadcast Job."""
    changes: dict[str, Any] = {}
    if text is not None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text is required")
        changes["text"] = text
    if description is not None:
        if not isinstance(description, str):
            raise ValueError("description must be a string")
        changes["description"] = description
    normalized_targets = None
    if target_session_ids is not None:
        normalized_targets = _normalize_target_session_ids(target_session_ids)
        if any(not _sessions.get(session_id) for session_id in normalized_targets):
            raise ValueError("target session does not exist")
        changes["targetSessionIds"] = normalized_targets
    if schedule is not None:
        normalized, next_run = _normalize_message_schedule(schedule, time.time())
        changes.update(schedule=normalized, nextRunAt=_iso_utc(next_run),
                       status="pending", lastError=None)
    if not changes:
        raise ValueError("one of text, description, or schedule is required")
    changes["updatedAt"] = time.time()
    with _lock, _job_lock(job_id, registry_root):
        path = _job_path(job_id, registry_root)
        current = _load_path(path)
        if not current or current.get("kind") not in {
            SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}:
            raise ValueError("message Job not found")
        if normalized_targets is not None and current.get("kind") != SESSION_BROADCAST_KIND:
            raise ValueError("targetSessionIds are only valid for broadcast Jobs")
        if current.get("status") in MESSAGE_JOB_TERMINAL:
            raise ValueError("terminal message Jobs cannot be edited")
        current.update(changes)
        _atomic_write(path, current)
        return current


def cancel_message(job_id: str, registry_root: str | Path | None = None) -> dict:
    current = get(job_id, registry_root)
    if not current or current.get("kind") not in {
        SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}:
        raise ValueError("message Job not found")
    if current.get("status") in MESSAGE_JOB_TERMINAL:
        return current
    return _update(job_id, {"status": "cancelled", "nextRunAt": None,
                            "cancelRequestedAt": time.time(),
                            "updatedAt": time.time()}, registry_root=registry_root)


async def run_due_message_jobs(now: float | None = None) -> int:
    """Claim and deliver due message Jobs once; safe across Pan processes."""
    current_time = time.time() if now is None else float(now)
    # A service crash can leave a message Job between claim and the normal
    # post-send update.  Requeue only stale claims, allowing a fresh service
    # to recover them without treating an active in-process send as orphaned.
    for candidate in list_jobs():
        if (candidate.get("kind") in {SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}
                and candidate.get("status") == "running"):
            started = candidate.get("runStartedAt")
            if isinstance(started, (int, float)) and current_time - started < MESSAGE_JOB_REQUEUE_AFTER_SEC:
                continue
            with _lock, _job_lock(candidate["jobId"]):
                path = _job_path(candidate["jobId"])
                current = _load_path(path)
                if current and current.get("status") == "running":
                    current.update(status=("scheduled" if current.get("schedule", {}).get("type")
                                   in {"interval", "weekly"} else "pending"),
                                   # Keep the recovered occurrence due even
                                   # after ISO serialization rounds the float.
                                   nextRunAt=_iso_utc(current_time - 0.001),
                                   lastError="recovered after scheduler restart",
                                   updatedAt=current_time)
                    _atomic_write(path, current)
    claimed: list[dict] = []
    for candidate in list_jobs():
        if candidate.get("kind") not in {SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}:
            continue
        if candidate.get("status") not in {"pending", "scheduled"}:
            continue
        try:
            due = _parse_at(candidate.get("nextRunAt")) <= current_time
        except (TypeError, ValueError):
            due = False
        if not due:
            continue
        with _lock, _job_lock(candidate["jobId"]):
            path = _job_path(candidate["jobId"])
            job = _load_path(path)
            if (not job or job.get("kind") not in {SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}
                    or job.get("status") not in {"pending", "scheduled"}):
                continue
            try:
                if _parse_at(job.get("nextRunAt")) > current_time:
                    continue
            except (TypeError, ValueError):
                continue
            job.update(status="running", runStartedAt=current_time,
                       updatedAt=current_time)
            _atomic_write(path, job)
            claimed.append(job)
    delivered = 0
    for job in claimed:
        # A cancel racing the claim wins before the actual send whenever
        # possible; an already in-flight send cannot be retracted.
        latest = get(job["jobId"])
        if not latest or latest.get("status") != "running":
            continue
        target_ids = ([job["targetSessionId"]]
                      if job.get("kind") == SESSION_MESSAGE_KIND
                      else list(job.get("targetSessionIds") or []))
        target_results: list[dict] = []
        for target_id in target_ids:
            try:
                result = await _worker.send_session(
                    target_id, job["text"], source=job.get("source", "agent"),
                    source_session_id=job.get("sourceSessionId"))
                if not isinstance(result, dict):
                    result = {"status": "error", "result": "send returned an invalid result"}
            except Exception as exc:  # isolate one target and keep the fan-out alive
                result = {"status": "error", "result": str(exc)}
            item = dict(result)
            item["sessionId"] = target_id
            target_results.append(item)
        if job.get("kind") == SESSION_MESSAGE_KIND:
            result = target_results[0] if target_results else {
                "status": "error", "result": "no target session"
            }
        else:
            failures = [item for item in target_results if item.get("status") == "error"]
            successes = len(target_results) - len(failures)
            result = {
                "status": ("error" if not successes else ("partial" if failures else "queued")),
                "results": target_results,
            }
            if failures:
                result["errors"] = [
                    f"{item['sessionId']}: {item.get('result') or item.get('error') or 'send failed'}"
                    for item in failures
                ]
        finished = time.time()
        recurring = job.get("schedule", {}).get("type") in {"interval", "weekly"}
        ok = isinstance(result, dict) and result.get("status") != "error"
        changes: dict[str, Any] = {
            "lastRunAt": _iso_utc(finished), "runCount": int(job.get("runCount", 0)) + 1,
            "lastDelivery": result if isinstance(result, dict) else {"status": "error"},
            "updatedAt": finished,
        }
        if ok:
            if recurring:
                changes.update(status="scheduled",
                               nextRunAt=_iso_utc(_message_next_run(job["schedule"], finished)),
                               lastError=("; ".join(result.get("errors", []))
                                          if result.get("status") == "partial" else None))
            else:
                changes.update(
                    status="completed", nextRunAt=None,
                    lastError=("; ".join(result.get("errors", []))
                               if result.get("status") == "partial" else None))
            delivered += 1
        elif recurring:
            changes.update(status="scheduled",
                           nextRunAt=_iso_utc(_message_next_run(job["schedule"], finished)),
                           lastError=("; ".join(result.get("errors", []))
                                      if isinstance(result, dict) and result.get("status") == "partial"
                                      else (result.get("result") if isinstance(result, dict) else "send failed")))
        else:
            changes.update(status="failed", nextRunAt=None,
                           lastError=("; ".join(result.get("errors", []))
                                      if isinstance(result, dict) and result.get("status") == "partial"
                                      else (result.get("result") if isinstance(result, dict) else "send failed")))
        with _lock, _job_lock(job["jobId"]):
            path = _job_path(job["jobId"])
            latest = _load_path(path)
            if latest and latest.get("status") == "running":
                latest.update(changes)
                _atomic_write(path, latest)
    return delivered


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
    if job.get("kind") in {SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}:
        return cancel_message(job_id)
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
    if old.get("kind") in {SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}:
        raise ValueError("message Jobs are edited or recreated, not retried")
    if old.get("status") in {"starting", "running"}:
        raise ValueError("running jobs cannot be retried; cancel them first")
    if old.get("status") not in {"completed", "failed", "cancelled"}:
        raise ValueError("job is not retryable")
    new_job = start(
        old["targetSessionId"], old["argv"], old["cwd"], label=old.get("label"),
        creator_session_id=old.get("creatorSessionId"),
    )
    new_job["retryOf"] = job_id
    return _update(new_job["jobId"], {"retryOf": job_id})


class ServiceJobBusy(ValueError):
    """Raised when a durable lifecycle Job already owns root+port."""

    def __init__(self, job: dict):
        super().__init__("a service lifecycle Job is already active")
        self.job = job


def _service_key(root: str, port: int) -> str:
    digest = hashlib.sha256(f"{Path(root).resolve()}:{int(port)}".encode("utf-8")).hexdigest()
    return f"service-{digest}"


def get_active_service_job(root: str, port: int,
                           registry_root: str | Path | None = None) -> dict | None:
    """Return the persisted in-flight lifecycle Job for one checkout/port."""
    root_value = str(Path(root).expanduser().resolve())
    for job in list_jobs(registry_root):
        if (job.get("kind") == SERVICE_LIFECYCLE_KIND
                and str(Path(str(job.get("root", ""))).expanduser().resolve()) == root_value
                and int(job.get("port", -1)) == int(port)
                and (job.get("phase") in SERVICE_ACTIVE_PHASES
                     or job.get("status") in {"pending", "running"})):
            return job
    return None


def create_service_job(*, request_id: str, operation: str, root: str, port: int,
                       old_pid: int | None = None,
                       old_pid_created_at: float | None = None,
                       log_path: str | None = None,
                       options: dict | None = None,
                       registry_root: str | Path | None = None) -> dict:
    """Atomically reserve a service lifecycle operation before spawning it."""
    if not request_id or not isinstance(request_id, str):
        raise ValueError("request_id is required")
    if not operation or not isinstance(operation, str):
        raise ValueError("operation is required")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("lifecycle options must be an object")
    # Persist a detached JSON snapshot.  The supervisor must be able to read
    # the same value in a different process after the request has returned.
    frozen_options = json.loads(json.dumps(options, ensure_ascii=False, sort_keys=True))
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError("service root does not exist")
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("service port is invalid")
    job_id = "job_" + secrets.token_hex(12)
    now = time.time()
    if log_path is None:
        log_path = str(_root(registry_root) / "logs" / f"{job_id}.log")
    registry_path = str(_root(registry_root))
    job = {
        "jobId": job_id, "kind": SERVICE_LIFECYCLE_KIND, "operation": operation,
        "options": frozen_options,
        "requestId": request_id, "phase": "requested", "status": "pending",
        "root": str(root_path), "port": port, "registryRoot": registry_path,
        "oldPid": old_pid, "oldPidCreatedAt": old_pid_created_at,
        "newPid": None, "newPidCreatedAt": None, "error": None,
        "errors": [],
        "createdAt": now, "updatedAt": now, "logPath": str(log_path),
    }
    with _lock, _registry_lock(_service_key(str(root_path), port), registry_path):
        active = get_active_service_job(str(root_path), port, registry_path)
        if active:
            raise ServiceJobBusy(active)
        _create(job, registry_path)
    return job


def transition_service_job(job_id: str, phase: str, *, registry_root: str | Path | None = None,
                           **changes: Any) -> dict:
    """Persist one legal lifecycle transition with a single atomic write."""
    if phase not in SERVICE_ACTIVE_PHASES | SERVICE_TERMINAL_PHASES:
        raise ValueError(f"invalid service lifecycle phase: {phase}")
    with _lock, _job_lock(job_id, registry_root):
        path = _job_path(job_id, registry_root)
        current = _load_path(path)
        if not current or current.get("kind", BACKGROUND_PROCESS_KIND) != SERVICE_LIFECYCLE_KIND:
            raise ValueError("service lifecycle Job not found")
        previous = current.get("phase")
        if current.get("operation") == "exit":
            allowed = {
                "requested": {"stopping_workers", "failed", "timed_out"},
                "stopping_workers": {"stopping_service", "failed", "timed_out"},
                "stopping_service": {"offline", "failed", "timed_out"},
                "offline": set(), "failed": set(), "timed_out": set(),
            }
        else:
            allowed = {
                "requested": {"stopping", "failed", "timed_out"},
                "stopping": {"stopped", "failed", "timed_out"},
                "stopped": {"starting", "failed", "timed_out"},
                "starting": {"ready", "failed", "timed_out"},
                "ready": set(), "failed": set(), "timed_out": set(),
            }
        if phase != previous and phase not in allowed.get(previous, set()):
            raise ValueError(f"invalid service lifecycle transition: {previous} -> {phase}")
        # A successful service stop and a successful *Exit Job* are separate
        # facts.  In particular, worker shutdown may have failed before the
        # detached supervisor confirmed that the service itself is offline.
        # Do not let the offline confirmation erase that failure.
        requested_error = changes.get("error", ...)
        if phase == "offline" and requested_error is None:
            changes.pop("error", None)
        errors = current.get("errors")
        if not isinstance(errors, list):
            errors = []
        legacy_error = current.get("error")
        if legacy_error and legacy_error not in errors:
            errors.append(legacy_error)
        new_error = changes.get("error")
        if new_error and new_error not in errors:
            errors.append(new_error)
        if errors:
            changes["errors"] = errors

        current.update(changes)
        has_error = bool(current.get("error")) or bool(current.get("errors"))
        status = ("failed" if phase == "offline" and has_error else "completed") if phase in {
            "ready", "offline"
        } else (
            phase if phase in {"failed", "timed_out"} else "running")
        current.update(phase=phase, status=status, updatedAt=time.time())
        _atomic_write(path, current)
        return _normalize_job(current)


def find_service_job(request_id: str, registry_root: str | Path | None = None) -> dict | None:
    """Find a lifecycle Job by the API-facing request id."""
    for job in list_jobs(registry_root):
        if job.get("kind") == SERVICE_LIFECYCLE_KIND and job.get("requestId") == request_id:
            return job
    return None


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
        if job.get("kind") in {
            SERVICE_LIFECYCLE_KIND, SESSION_MESSAGE_KIND, SESSION_BROADCAST_KIND}:
            continue
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
    # Message Jobs are delivered through the normal Session send queue and do
    # not create a second terminal notice.  Running them here makes service
    # restart recovery share the existing one-second lifecycle loop.
    await run_due_message_jobs()
    reconcile_running()
    delivered = 0
    for job in list_jobs():
        if job.get("kind") in {SERVICE_LIFECYCLE_KIND, SESSION_MESSAGE_KIND,
                                SESSION_BROADCAST_KIND}:
            continue
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
            text = json.dumps({
                "jobId": current["jobId"],
                "status": current["status"],
                "exitCode": current.get("exitCode"),
                "logPath": current.get("logPath"),
            }, ensure_ascii=False)
            result = await _worker.enqueue_notice(
                target, text, source="automation", event_id=event_id,
                notice_kind="background_job_terminal",
                job_id=current["jobId"],
                notice_status=current["status"],
                creator_session_id=current.get("creatorSessionId"),
                target_session_ids=[target] if target else [],
            )
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
