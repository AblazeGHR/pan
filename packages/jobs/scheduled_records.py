"""Scheduled record projections and explicit legacy control helpers.

These helpers neither migrate nor recover a registry. Mutations keep the
existing legacy reservation boundary and protected Action checks.
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime
from pathlib import Path
from packages.core import background_jobs
from packages.core.platform.data_paths import job_root as data_root
from packages.jobs import cron
from packages.jobs.runtime_owner import legacy_writer
from packages.jobs.schedule_calculations import (
    iso, _now, _epoch_to_iso, _schedule_from_entry, _spec_to_entry,
    _entries_next_fire, _entry_from_schedule, compute_next_fire,
)


def _new_task_id():
    return 'sch_' + secrets.token_hex(6)

def task_from_job(job: dict | None) -> dict | None:
    if not isinstance(job, dict):
        return None
    entries = job.get("schedule") or []
    entry = dict(entries[0]) if entries and isinstance(entries[0], dict) else {}
    misfire = (entry.get("misfirePolicy") or job.get("misfirePolicy") or "fire_now")
    return {
        "id": job.get("taskId") or job.get("jobId"),
        "name": job.get("name") or "",
        "description": job.get("description") or "",
        "target_session_id": job.get("targetSessionId"),
        "text": job.get("text") or "",
        "enabled": bool(job.get("enabled")),
        "paused": bool(job.get("paused")),
        "schedule": _schedule_from_entry(entry),
        "next_fire_at": job.get("nextFireAt"),
        "last_fire_at": job.get("lastFireAt"),
        "last_status": job.get("lastStatus"),
        "last_error": job.get("lastError"),
        "run_count": int(job.get("runCount") or 0),
        "max_runs": job.get("maxRuns"),
        "misfire_policy": misfire,
        "created_at": _epoch_to_iso(job.get("createdAt")),
        "updated_at": _epoch_to_iso(job.get("updatedAt")),
    }


def build_record(payload: dict, *, root=None) -> dict:
    """create_task 的 payload（已过校验，带 ``_normalized_schedule`` 等内参）→ job 记录。"""
    now_ts = time.time()
    created_iso = payload.get("created_at") or iso(_now())
    specs = payload.get("_schedule_specs")
    if specs is not None:
        # 多 entry 路径（/api/jobs POST）：列表里每项一个 entry
        entries = [_spec_to_entry(raw, default_misfire=payload["_misfire"],
                                  created_iso=created_iso) for raw in specs]
        next_fire = _entries_next_fire(entries)
    else:
        # 单 spec 路径（PR 契约 /api/scheduler/tasks）
        schedule = payload["_normalized_schedule"]
        if schedule["kind"] == "interval" and not schedule.get("anchor"):
            anchor = cron.parse_datetime(created_iso) or _now()
            schedule["anchor"] = iso(anchor)
        task_view = {"id": str(payload.get("id") or _new_task_id()).strip(),
                     "enabled": bool(payload.get("enabled", True)),
                     "schedule": schedule, "created_at": created_iso}
        next_fire = compute_next_fire(task_view, _now())
        entries = [_entry_from_schedule(schedule,
                                        misfire_policy=payload["_misfire"],
                                        next_fire_at=next_fire)]
    task_id = str(payload.get("id") or _new_task_id()).strip()
    enabled = bool(payload.get("enabled", True))
    created_dt = cron.parse_datetime(created_iso)
    status = "scheduled" if (enabled and next_fire) else "pending"
    if not enabled:
        status = "completed"
    target = payload.get("target_session_id")
    action = payload.get("action") or {"api": "assign"}
    return {
        "jobId": "job_" + secrets.token_hex(6),
        "kind": background_jobs.SCHEDULED_TASK_KIND,
        "taskId": task_id,
        "name": (background_jobs.normalize_name(payload.get("name"))
                 or background_jobs.default_job_name(Path(root) if root is not None else data_root())),
        "description": background_jobs.normalize_description(payload.get("description")),
        "targetSessionId": target,
        "sourceStruct": background_jobs.normalize_source({"type": "system"}),
        "targetStruct": background_jobs.normalize_target(target),
        "text": payload.get("text", ""),
        "action": action,
        "source": "automation",
        "creatorSessionId": None,
        "enabled": enabled,
        "paused": bool(payload.get("paused", False)),
        "schedule": entries,
        "misfirePolicy": payload["_misfire"],
        "nextFireAt": next_fire,
        "lastFireAt": None,
        "lastStatus": None,
        "lastError": None,
        "runCount": int(payload.get("run_count") or 0),
        "maxRuns": payload["_max_runs"],
        "status": status,
        "runStartedAt": None,
        "undeliveredFires": [],
        "createdAt": created_dt.timestamp() if created_dt else now_ts,
        "updatedAt": now_ts,
    }


def find_task(task_id: str, registry_root: Path | None = None) -> dict | None:
    """按对外主键 task_id 找 job 记录（注册表线性扫，n 小）。"""
    if not task_id:
        return None
    root = Path(registry_root) if registry_root else data_root()
    for job in background_jobs.list_jobs(registry_root=root):
        if job.get("kind") != background_jobs.SCHEDULED_TASK_KIND:
            continue
        if job.get("taskId") == task_id or job.get("jobId") == task_id:
            return job
    return None


@legacy_writer(lambda task_id, *, root=None: (Path(root) if root is not None else data_root()).resolve().parent, reservation=True)
def delete_task(task_id, *, root=None):
    root = Path(root) if root is not None else data_root()
    job = find_task(task_id, root)
    from packages.jobs.action_runtime import is_protected_job
    if is_protected_job(job):
        raise ValueError('Use protected Action Job configuration API')
    return False if job is None else background_jobs.delete_job(job['jobId'], registry_root=root)


@legacy_writer(lambda record, *, root=None: (Path(root) if root is not None else data_root()).resolve().parent, reservation=True)
def append_run(record, *, root=None):
    background_jobs.append_run_record(record, registry_root=Path(root) if root is not None else data_root(), max_entries=500)
