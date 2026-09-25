"""定时任务插件的落盘层 —— 统一 job 内核的**兼容读写层**（P1 统一）。

契约源：``docs/design/job-unification/PLAN_JOB_UNIFICATION.md``。task 的持久化
形态自本版本起是 ``packages.core.background_jobs`` 注册表里
``kind="scheduled-task"`` 的 job 记录；本模块只做三件事：

1. **双向映射**：job 记录 ↔ 旧 task dict（保持 ``/api/scheduler/*`` 与 9 个
   MCP 工具的 HTTP 契约、字段名、校验错误完全不变）；
2. **迁移**：旧 ``data/scheduler/tasks/*.json`` 一次性幂等迁入注册表
   （:func:`migrate_legacy_tasks`，源文件保留不删）；
3. **入口收口**：``data_root()`` 决定 scheduled-task 的注册表根 ——
   ``PAN_SCHEDULER_DIR`` > ``PAN_BACKGROUND_JOBS_DIR`` > 默认
   ``data/background_jobs``（与 message/process job 同一注册表）。

命名纪律：对外主键仍是 **task_id**（``sch_`` 前缀；dispatch key 前缀、
runs 归属、HTTP/MCP 寻址）。``jobId``（``job_`` 前缀）是注册表主键，
两者绝不混用。

所有读-改-写都经 ``background_jobs._update``（跨进程 job 锁 + 原子替换）——
PR 评审 P0「RMW 锁覆盖」在统一模型里结构性成立。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

from packages.core import background_jobs
from packages.jobs import cron

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: scheduled-task 的注册表根（默认与 message/process job 同一张表）。
DEFAULT_ROOT = PROJECT_ROOT / "data" / "background_jobs"

#: 旧版 scheduler 数据根（迁移源，只读）。
LEGACY_TASKS_ROOT = PROJECT_ROOT / "data" / "scheduler"

#: 任务 id 前缀 + 12 位 hex（对外主键，兼容层保持不变）
TASK_ID_PREFIX = "sch_"

#: schedule entry id 前缀
ENTRY_ID_PREFIX = "sce_"

#: 可选 misfire 策略
MISFIRE_POLICIES = ("fire_now", "skip")

#: runs.jsonl 的滚动上限（background_jobs.append_run_record 同值）
RUNS_MAX_ENTRIES = 500

_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-]")


# ── 注册表根 ──


def data_root() -> Path:
    """scheduled-task 注册表根：环境变量优先，测试 monkeypatch DEFAULT_ROOT 即隔离。

    ``PAN_SCHEDULER_DIR`` > ``PAN_BACKGROUND_JOBS_DIR`` > ``DEFAULT_ROOT``。
    生产环境两个 env 都缺省时与 message/process job 共用 ``data/background_jobs``。
    """
    env = os.environ.get("PAN_SCHEDULER_DIR") or os.environ.get("PAN_BACKGROUND_JOBS_DIR")
    return Path(env) if env else Path(DEFAULT_ROOT)


def legacy_root() -> Path:
    """旧版 scheduler 数据根（迁移源）：``PAN_SCHEDULER_DIR`` > 旧默认。"""
    env = os.environ.get("PAN_SCHEDULER_DIR")
    return Path(env) if env else Path(LEGACY_TASKS_ROOT)


def sanitize(value) -> str:
    """把 id 消毒成安全文件名片段（防目录穿越；兼容保留）。"""
    return _SAFE_RE.sub("_", str(value))[:128] or "_"


def iso(value: datetime | None) -> str | None:
    """本地朴素 datetime → ISO-8601 字符串（秒精度）。"""
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat()


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _new_task_id() -> str:
    return TASK_ID_PREFIX + secrets.token_hex(6)


def _new_entry_id() -> str:
    return ENTRY_ID_PREFIX + secrets.token_hex(4)


def _epoch_to_iso(value) -> str | None:
    """注册表 epoch 时间戳 → 本地朴素 ISO（task dict 出口用）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return iso(datetime.fromtimestamp(float(value)))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


# ── schedule 校验（单条 entry 视角，逻辑照抄 PR store）──


def _normalize_schedule(raw) -> dict:
    """校验并清洗 schedule；非法一律 ValueError（API 层映射 invalid_schedule）。

    ``interval_sec`` 与 ``intervalSec`` 同时接受，落盘两个键都写。
    """
    if not isinstance(raw, dict):
        raise ValueError("schedule 必须是对象")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in ("once", "interval", "cron"):
        raise ValueError("schedule.kind 必须是 once / interval / cron")

    out: dict = {"kind": kind}
    tz = raw.get("timezone")
    if tz:
        tz = str(tz).strip()
        if tz:
            out["timezone"] = tz

    if kind == "once":
        at = cron.parse_datetime(raw.get("at"))
        if at is None:
            raise ValueError("kind=once 需要合法的 at（ISO-8601 本地时间）")
        out["at"] = iso(at)
    elif kind == "interval":
        raw_sec = raw.get("interval_sec", raw.get("intervalSec"))
        try:
            sec = float(raw_sec)
        except (TypeError, ValueError):
            raise ValueError("kind=interval 需要 interval_sec（> 0）") from None
        if sec <= 0:
            raise ValueError(f"interval_sec 必须 > 0，收到 {raw_sec!r}")
        sec = int(sec) if float(sec).is_integer() else sec
        out["interval_sec"] = sec
        out["intervalSec"] = sec
        anchor = cron.parse_datetime(raw.get("anchor"))
        if anchor is not None:
            out["anchor"] = iso(anchor)
    else:
        expr = str(raw.get("cron") or "").strip()
        cron.parse_cron(expr)  # 非法表达式 -> ValueError
        out["cron"] = expr
    return out


def _schedule_from_entry(entry: dict) -> dict:
    """entry → PR 归一化 schedule dict（task dict 出口）。"""
    schedule = dict(entry or {})
    for key in ("id", "enabled", "nextFireAt", "lastFireAt", "misfirePolicy", "graceSec"):
        schedule.pop(key, None)
    return schedule


def _entry_from_schedule(schedule: dict, *, misfire_policy: str,
                         next_fire_at: str | None = None,
                         last_fire_at: str | None = None) -> dict:
    """归一化 schedule dict → job 记录里的 entry。"""
    return {
        "id": _new_entry_id(),
        "misfirePolicy": misfire_policy,
        "enabled": True,
        "nextFireAt": next_fire_at,
        "lastFireAt": last_fire_at,
        **dict(schedule),
    }


def effective_spec(task: dict) -> dict:
    """给 cron 求值时用的 spec：补上 interval 缺失的 anchor（= created_at）。"""
    schedule = dict(task.get("schedule") or {})
    if schedule.get("kind") == "interval" and not schedule.get("anchor"):
        created = cron.parse_datetime(task.get("created_at"))
        if created is not None:
            schedule["anchor"] = iso(created)
    return schedule


def compute_next_fire(task: dict, after: datetime | None = None) -> str | None:
    """按锚点算出 next_fire_at（ISO 字符串或 None）。disabled 恒为 None。"""
    if not task.get("enabled"):
        return None
    when = after or _now()
    spec = effective_spec(task)
    tz = (task.get("schedule") or {}).get("timezone")
    try:
        point = cron.next_fire_after(spec, when, tz_name=tz)
    except ValueError:
        return None
    return iso(point)


# ── job 记录 ↔ task dict ──


def _task_from_job(job: dict | None) -> dict | None:
    if not isinstance(job, dict):
        return None
    entries = job.get("schedule") or []
    entry = dict(entries[0]) if entries and isinstance(entries[0], dict) else {}
    misfire = (entry.get("misfirePolicy") or job.get("misfirePolicy") or "fire_now")
    return {
        "id": job.get("taskId") or job.get("jobId"),
        "name": job.get("name") or "",
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


def _job_from_payload(payload: dict) -> dict:
    """create_task 的 payload（已过校验，带 ``_normalized_schedule`` 等内参）→ job 记录。"""
    now_ts = time.time()
    schedule = payload["_normalized_schedule"]
    created_iso = payload.get("created_at") or iso(_now())
    if schedule["kind"] == "interval" and not schedule.get("anchor"):
        anchor = cron.parse_datetime(created_iso) or _now()
        schedule["anchor"] = iso(anchor)
    task_id = str(payload.get("id") or _new_task_id()).strip()
    enabled = bool(payload.get("enabled", True))
    task_view = {"id": task_id, "enabled": enabled, "schedule": schedule,
                 "created_at": created_iso}
    next_fire = compute_next_fire(task_view, _now())
    entry = _entry_from_schedule(schedule, misfire_policy=payload["_misfire"],
                                 next_fire_at=next_fire)
    created_dt = cron.parse_datetime(created_iso)
    status = "scheduled" if (enabled and next_fire) else "pending"
    if not enabled:
        status = "completed"
    return {
        "jobId": "job_" + secrets.token_hex(6),
        "kind": background_jobs.SCHEDULED_TASK_KIND,
        "taskId": task_id,
        "name": str(payload.get("name") or "").strip() or "未命名定时任务",
        "targetSessionId": payload["target_session_id"],
        "text": payload["text"],
        "source": "automation",
        "creatorSessionId": None,
        "enabled": enabled,
        "paused": bool(payload.get("paused", False)),
        "schedule": [entry],
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


def _job_for_task(task_id: str, registry_root: Path | None = None) -> dict | None:
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


# ── CRUD（对外签名与 PR store 完全一致）──


def list_tasks(include_disabled: bool = True) -> list[dict]:
    """列出全部任务；``include_disabled=False`` 时只返回 enabled 的。"""
    jobs = [job for job in background_jobs.list_jobs(registry_root=data_root())
            if job.get("kind") == background_jobs.SCHEDULED_TASK_KIND]
    tasks = [t for t in (_task_from_job(j) for j in jobs) if t]
    if not include_disabled:
        tasks = [t for t in tasks if t.get("enabled")]
    tasks.sort(key=lambda t: (str(t.get("created_at") or ""), str(t.get("id") or "")))
    return tasks


def get_task(task_id: str) -> dict | None:
    job = _job_for_task(task_id)
    return _task_from_job(job) if job else None


def create_task(payload: dict) -> dict:
    """创建任务：校验 → job 记录 → 注册表原子落盘。

    Raises:
        ValueError: 目标 session、任务文本、schedule 或 misfire_policy 非法。
    """
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是对象")

    target = str(payload.get("target_session_id") or "").strip()
    if not target:
        raise ValueError("target_session_id 不能为空")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("text 不能为空")

    misfire = str(payload.get("misfire_policy") or "fire_now")
    if misfire not in MISFIRE_POLICIES:
        raise ValueError("misfire_policy 必须是 fire_now / skip")

    max_runs = payload.get("max_runs")
    if max_runs is not None and max_runs != "":
        try:
            max_runs = int(max_runs)
        except (TypeError, ValueError):
            raise ValueError("max_runs 必须是整数或 null") from None
    else:
        max_runs = None

    prepared = dict(payload)
    prepared["_normalized_schedule"] = _normalize_schedule(payload.get("schedule"))
    prepared["_misfire"] = misfire
    prepared["_max_runs"] = max_runs
    job = _job_from_payload(prepared)
    background_jobs._create(job, registry_root=data_root())
    return _task_from_job(job)


def update_task(task_id: str, patch: dict) -> dict | None:
    """局部更新；任务不存在返回 ``None``。next_fire_at 语义与 PR 逐条对齐：

    - patch 显式给了 ``next_fire_at`` → 原样采用，不重算；
    - ``enabled`` 置 False → next_fire_at 置 None（不参与扫描）；
    - ``schedule`` 变更、``enabled`` 置 True → 按锚点重算。
    """
    job = _job_for_task(task_id)
    if job is None:
        return None
    if not isinstance(patch, dict):
        return _task_from_job(job)

    entries = list(job.get("schedule") or [])
    entry = dict(entries[0]) if entries else {}
    entry_id = entry.get("id") or "entry0"

    schedule_changed = False
    job_changes: dict = {}
    entry_changes: dict = {}

    if "name" in patch:
        job_changes["name"] = str(patch.get("name") or "").strip() or "未命名定时任务"
    if "text" in patch:
        text = str(patch.get("text") or "").strip()
        if not text:
            raise ValueError("text 不能为空")
        job_changes["text"] = text
    if "target_session_id" in patch:
        target = str(patch.get("target_session_id") or "").strip()
        if not target:
            raise ValueError("target_session_id 不能为空")
        job_changes["targetSessionId"] = target
        # PLAN §10：切换 target → 积压便条由统一循环自动重投新 target。
    if "paused" in patch:
        job_changes["paused"] = bool(patch.get("paused"))
    if "max_runs" in patch:
        value = patch.get("max_runs")
        if value is None or value == "":
            value = None
        else:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValueError("max_runs 必须是整数或 null") from None
        job_changes["maxRuns"] = value
    if "misfire_policy" in patch and patch.get("misfire_policy") is not None:
        policy = str(patch.get("misfire_policy"))
        if policy not in MISFIRE_POLICIES:
            raise ValueError("misfire_policy 必须是 fire_now / skip")
        job_changes["misfirePolicy"] = policy
        entry_changes["misfirePolicy"] = policy
    if "run_count" in patch:
        job_changes["runCount"] = int(patch.get("run_count") or 0)
    if "last_fire_at" in patch:
        job_changes["lastFireAt"] = patch.get("last_fire_at")
        entry_changes["lastFireAt"] = patch.get("last_fire_at")
    if "last_status" in patch:
        job_changes["lastStatus"] = patch.get("last_status")
    if "last_error" in patch:
        job_changes["lastError"] = patch.get("last_error")
    if "schedule" in patch:
        schedule = _normalize_schedule(patch.get("schedule"))
        if schedule["kind"] == "interval" and not schedule.get("anchor"):
            created_iso = _epoch_to_iso(job.get("createdAt"))
            anchor = cron.parse_datetime(created_iso) or _now()
            schedule["anchor"] = iso(anchor)
        entry_changes = dict(schedule)
        entry_changes["id"] = entry_id
        entry_changes["misfirePolicy"] = str(
            job_changes.get("misfirePolicy", job.get("misfirePolicy") or "fire_now"))
        entry_changes["enabled"] = bool(job_changes.get("enabled", job.get("enabled")))
        schedule_changed = True
    if "enabled" in patch:
        job_changes["enabled"] = bool(patch.get("enabled"))
        if not schedule_changed:
            entry_changes["enabled"] = job_changes["enabled"]

    # next_fire_at 推导（PR 语义逐条对齐）
    if "next_fire_at" in patch:
        entry_changes["nextFireAt"] = patch.get("next_fire_at")
    elif patch.get("enabled") is False:
        entry_changes["nextFireAt"] = None
    elif schedule_changed or patch.get("enabled") is True:
        merged = dict(entry)
        merged.update(entry_changes)
        pr_schedule = _schedule_from_entry(merged)
        enabled = job_changes.get("enabled", job.get("enabled"))
        try:
            point = (cron.next_fire_after(
                effective_spec({"schedule": pr_schedule,
                                "created_at": _epoch_to_iso(job.get("createdAt"))}),
                _now(), tz_name=pr_schedule.get("timezone")) if enabled else None)
        except ValueError:
            point = None
        entry_changes["nextFireAt"] = iso(point)

    # 组装一次 _update 落盘（跨进程锁内的读-改-写）
    new_entries = list(entries)
    if entry_changes:
        if schedule_changed:
            new_entries = [dict(entry_changes)]
        else:
            merged = dict(entry)
            merged.update(entry_changes)
            new_entries[0] = merged
        preview = dict(job)
        preview["schedule"] = new_entries
        preview.update(job_changes)
        job_changes["nextFireAt"] = background_jobs._job_next_fire(preview)
        job_changes["schedule"] = new_entries
    if job_changes:
        job_changes["updatedAt"] = time.time()
        if patch.get("enabled") is False:
            job_changes["status"] = "completed"
        elif patch.get("enabled") is True and entry_changes.get("nextFireAt"):
            job_changes["status"] = "scheduled"
    if not job_changes and not entry_changes:
        return _task_from_job(job)
    updated = background_jobs._update(job["jobId"], job_changes, registry_root=data_root())
    return _task_from_job(updated)


def delete_task(task_id: str) -> bool:
    job = _job_for_task(task_id)
    if job is None:
        return False
    return background_jobs.delete_job(job["jobId"], registry_root=data_root())


# ── 执行历史 ──


def append_run(record: dict) -> None:
    """兼容入口：append 一行到注册表 runs.jsonl（滚动上限 500）。"""
    background_jobs.append_run_record(record, registry_root=data_root(),
                                      max_entries=RUNS_MAX_ENTRIES)


def list_runs(task_id: str | None = None, limit: int = 100) -> list[dict]:
    """执行历史，最新在前；给定 task_id 只返回该任务的记录。"""
    return background_jobs.list_run_records(task_id=task_id, limit=limit,
                                             registry_root=data_root())


# ── leader 选主（已退役，兼容桩）──


def claim_leader(timeout: float = 0.5) -> bool:
    """兼容桩：统一后 leader 锁退役（per-job 认领模型），恒为 True。"""
    return True


def release_leader() -> None:
    """兼容桩：无锁可释放。"""


# ── 迁移 ──


def _load_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default
    if default is None:
        return data
    return data if isinstance(data, type(default)) else default


def migrate_legacy_tasks() -> int:
    """旧 ``<legacy_root>/tasks/*.json`` → 统一注册表。幂等，源文件保留。

    迁移映射：task.id → ``taskId``；schedule → 单 entry 列表；
    next_fire_at / last_fire_at 原样保留（不补跑、不丢节奏）。旧 runs.jsonl
    按 run_id 去重合并。已在注册表中的 taskId 跳过。
    """
    tasks_dir = legacy_root() / "tasks"
    if not tasks_dir.is_dir():
        return 0
    existing = {t.get("id") for t in list_tasks()}
    migrated = 0
    for path in sorted(tasks_dir.glob("*.json")):
        data = _load_json(path, None)
        if not isinstance(data, dict) or not data.get("id"):
            continue
        task_id = str(data["id"])
        if task_id in existing:
            continue
        try:
            job = _job_from_legacy_task(data)
        except ValueError:
            continue
        try:
            background_jobs._create(job, registry_root=data_root())
        except ValueError:
            continue
        existing.add(task_id)
        migrated += 1
    _merge_legacy_runs()
    return migrated


def _job_from_legacy_task(data: dict) -> dict:
    """PR task dict（旧文件）→ job 记录。字段残缺一律按默认收敛，不抛。"""
    schedule = _normalize_schedule(data.get("schedule"))
    task_id = str(data.get("id"))
    enabled = bool(data.get("enabled", True))
    paused = bool(data.get("paused", False))
    next_fire = data.get("next_fire_at")
    misfire = str(data.get("misfire_policy") or "fire_now")
    if misfire not in MISFIRE_POLICIES:
        misfire = "fire_now"
    created_dt = cron.parse_datetime(data.get("created_at")) or _now()
    entry = _entry_from_schedule(schedule, misfire_policy=misfire,
                                 next_fire_at=next_fire,
                                 last_fire_at=data.get("last_fire_at"))
    status = "scheduled" if (enabled and next_fire) else "pending"
    if not enabled:
        status = "completed"
    max_runs = data.get("max_runs")
    if isinstance(max_runs, str) and max_runs.strip().isdigit():
        max_runs = int(max_runs)
    elif not isinstance(max_runs, int):
        max_runs = None
    run_count = data.get("run_count")
    if not isinstance(run_count, int):
        try:
            run_count = int(run_count)
        except (TypeError, ValueError):
            run_count = 0
    return {
        "jobId": "job_" + secrets.token_hex(6),
        "kind": background_jobs.SCHEDULED_TASK_KIND,
        "taskId": task_id,
        "name": str(data.get("name") or "").strip() or "未命名定时任务",
        "targetSessionId": str(data.get("target_session_id") or "").strip(),
        "text": str(data.get("text") or "").strip(),
        "source": "automation",
        "creatorSessionId": None,
        "enabled": enabled,
        "paused": paused,
        "schedule": [entry],
        "misfirePolicy": misfire,
        "nextFireAt": next_fire,
        "lastFireAt": data.get("last_fire_at"),
        "lastStatus": data.get("last_status"),
        "lastError": data.get("last_error"),
        "runCount": run_count,
        "maxRuns": max_runs,
        "status": status,
        "runStartedAt": None,
        "undeliveredFires": [],
        "createdAt": created_dt.timestamp(),
        "updatedAt": created_dt.timestamp(),
    }


def _merge_legacy_runs() -> None:
    """旧 runs.jsonl → 注册表 runs.jsonl（按 run_id 去重，只补不删）。"""
    legacy_path = legacy_root() / "runs.jsonl"
    if not legacy_path.exists():
        return
    registry_path = data_root() / "runs.jsonl"
    known: set[str] = set()
    try:
        for record in background_jobs.list_run_records(limit=0,
                                                        registry_root=data_root()):
            if record.get("run_id"):
                known.add(str(record["run_id"]))
    except Exception:
        return
    try:
        lines = legacy_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    fresh: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            fresh.append(line)  # 残行也照搬，不丢数据
            continue
        run_id = record.get("run_id") if isinstance(record, dict) else None
        if run_id and str(run_id) in known:
            continue
        if run_id:
            known.add(str(run_id))
        fresh.append(line)
    if fresh:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        with open(registry_path, "a", encoding="utf-8") as handle:
            for line in fresh:
                handle.write(line + "\n")
