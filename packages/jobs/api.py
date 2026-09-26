"""Job 统一 HTTP API —— ``/api/jobs/*``（P2，PLAN §5）。

全 kind 一览/详情/管理的统一入口（GUI JobsView 的后端契约）：

- ``GET  /api/jobs``            列表（kind/status 过滤，active-first 排序）
- ``POST /api/jobs``           创建可创建的 kind（按 kind 严格校验字段）
- ``GET  /api/jobs/{id}``       详情（结构化 source/target 视图）
- ``PATCH /api/jobs/{id}``      按 kind 更新其允许字段
- ``DELETE /api/jobs/{id}``     删除
- ``GET  /api/jobs/{id}/runs``  执行历史（runs.jsonl，最新在前）
- ``POST /api/jobs/{id}/run-now`` 手动触发 scheduled-task action（含 shell）
- ``GET  /api/jobs/next``       下次触发预览（全 kind 中最近的到期点）
- ``GET  /api/jobs/templates`` schedule 模板清单（快捷创建用）
- ``GET  /api/jobs/kinds``      kind 元数据（label + 是否有 schedule/进程）

职责边界：只做参数校验、出口视图（job_public_view）与统一包络；持久化/求值/
调度一律委托 ``packages.core.background_jobs`` 与 scheduler 兼容层。
**禁止 import packages.web.server**（循环依赖）；WS 广播经 bind 注入。

``/api/scheduler/*`` 兼容别名照旧（PLAN §9：暂不退役），二者共用同一内核。
"""

from __future__ import annotations

import asyncio
import inspect
import math
from datetime import datetime

from fastapi import APIRouter

from packages.core import background_jobs
from packages.jobs import cron as _job_cron
from packages.jobs import templates as _job_templates

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_KIND_ORDER = {"running": 0, "starting": 1, "scheduled": 2, "pending": 3,
               "completed": 4, "failed": 5, "cancelled": 6}

_state: dict = {"broadcast": None}


def bind(broadcast=None) -> None:
    """注入 WS 广播函数（server.py 启动时调用）。"""
    _state["broadcast"] = broadcast


def _ok(**payload):
    return {"ok": True, **payload}


def _err(code: str, message: str):
    return {"ok": False, "error": {"code": code, "message": message}}


def _view(job: dict) -> dict:
    return background_jobs.job_public_view(job)


def _job_root():
    from packages.scheduler import store as scheduler_store

    return scheduler_store.data_root()


def _find_job(job_id: str) -> dict | None:
    """按 jobId（或 scheduled-task 的 taskId）取记录。"""
    try:
        job = background_jobs.get(job_id, registry_root=_job_root())
    except ValueError:
        job = None
    if job is not None and (job.get("jobId") == job_id or job_id in {
            job.get("taskId")}):
        return job
    # 对外主键 taskId（sch_ 前缀）兜底寻址
    for candidate in background_jobs.list_jobs(registry_root=_job_root()):
        if candidate.get("taskId") == job_id:
            return candidate
    return None


def _emit(event: dict) -> None:
    fn = _state.get("broadcast")
    if fn is None:
        return
    try:
        result = fn(event)
    except Exception:
        return
    if inspect.isawaitable(result):
        # broadcast 是异步函数（server.py 注入）：挂到当前循环消费，
        # 异常吞掉不打扰调用方（与 scheduler.api._emit 同范式）。
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            close = getattr(result, "close", None)
            if callable(close):
                close()
            return
        loop.create_task(_drain(result))


async def _drain(awaitable) -> None:
    try:
        await awaitable
    except Exception:
        pass


# ── 列表 / 详情 ──


@router.get("")
@router.get("/")
async def list_jobs(kind: str | None = None, status: str | None = None,
                    includeCompleted: bool = True):
    """全部 kind 的 job 列表；active-first 排序，可按 kind/status 过滤。"""
    jobs = [background_jobs.job_public_view(j)
            for j in background_jobs.list_jobs(registry_root=_job_root())
            if not j.get("scheduledParentJobId")]
    if kind:
        jobs = [j for j in jobs if j.get("kind") == kind]
    if status:
        jobs = [j for j in jobs if j.get("status") == status]
    if not includeCompleted:
        jobs = [j for j in jobs if j.get("status") != "completed"]
    jobs.sort(key=lambda j: (_KIND_ORDER.get(str(j.get("status")), 9),
                             str(j.get("updatedAt") or "")), reverse=False)
    return _ok(jobs=jobs)


def _session_exists(session_id: str) -> bool:
    try:
        from packages.core import session as sess

        return sess.get(session_id) is not None
    except Exception:
        return False


def _source_target_access_error(source_session_id: str | None,
                                target_session_id: str) -> str | None:
    try:
        from packages.core import session as sess

        target = sess.get(target_session_id)
        source = sess.get(source_session_id) if source_session_id else None
    except Exception:
        return "unable to validate target Session access"
    if not target:
        return f"Session {target_session_id} not found"
    if source_session_id and not source:
        return f"source Session {source_session_id} not found"
    if (source_session_id and source_session_id != target.id
            and target.readonly_session and target.managed_by == source_session_id):
        return "source Session is not allowed to write to this managed read-only Session"
    return None


def _normalize_scheduled_action(action: object):
    if not isinstance(action, dict) or not isinstance(action.get("api"), str):
        return None, "action must contain a string api"
    api = action["api"]
    if api in {"assign", "send_session"}:
        error = _unknown_fields(action, {"api"}, "session action")
        return ({"api": api}, None) if not error else (None, error)
    if api != "shell":
        return None, "action.api must be assign, send_session, or shell"
    error = _unknown_fields(action, {"api", "args"}, "shell action")
    args = action.get("args")
    if not isinstance(args, dict):
        return None, "shell action requires an args object"
    error = error or _unknown_fields(args, {"command", "cwd"}, "shell action args")
    if error:
        return None, error
    try:
        command, cwd = background_jobs.validate_shell_action(
            args.get("command"), args.get("cwd"))
    except ValueError as exc:
        return None, str(exc)
    return {"api": "shell", "args": {
        "command": command, "cwd": str(cwd)}}, None


def _unknown_fields(value: dict, allowed: set[str], scope: str) -> str | None:
    unknown = sorted(set(value) - allowed)
    return f"unsupported {scope} field(s): {', '.join(unknown)}" if unknown else None


def _name_description_error(data: dict) -> str | None:
    if "name" in data and not isinstance(data["name"], str):
        return "name must be a string"
    if "description" in data and not isinstance(data["description"], str):
        return "description must be a string"
    return None


def _session_target(data: dict, *, required: bool, allow_many: bool = False):
    if "target" not in data:
        return None, "target is required" if required else None
    raw = data.get("target")
    if isinstance(raw, str):
        sid = raw.strip()
        return ({"sessionId": sid}, None) if sid else (
            None, "target.sessionId must be a non-empty string")
    if raw is None and not required:
        return {"sessionId": None}, None
    if not isinstance(raw, dict):
        return None, "target must be an object"
    allowed = {"sessionId", "sessionIds"} if allow_many else {"sessionId"}
    unknown = _unknown_fields(raw, allowed, "target")
    if unknown:
        return None, unknown
    ids = raw.get("sessionIds") if allow_many else None
    sid = raw.get("sessionId")
    if ids is not None:
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(item, str) or not item.strip() for item in ids)):
            return None, "target.sessionIds must be a non-empty array of session IDs"
        ids = list(dict.fromkeys(item.strip() for item in ids))
        if sid is not None and (not isinstance(sid, str) or sid.strip() not in ids):
            return None, "target.sessionId must be included in target.sessionIds"
        return {"sessionId": sid.strip() if isinstance(sid, str) else ids[0],
                "sessionIds": ids}, None
    if sid is None and not required:
        return {"sessionId": None}, None
    if not isinstance(sid, str) or not sid.strip():
        return None, "target.sessionId must be a non-empty string"
    return {"sessionId": sid.strip()}, None


def _validate_scheduled_specs(schedule: object) -> str | None:
    specs = schedule if isinstance(schedule, list) else [schedule]
    if not specs:
        return "schedule must not be an empty list"
    common = {"kind", "timezone", "enabled", "misfirePolicy", "graceSec"}
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict):
            return f"schedule[{index}] must be an object"
        kind = spec.get("kind")
        if not isinstance(kind, str):
            return f"schedule[{index}].kind must be a string"
        kind_fields = {
            "once": {"at"},
            "interval": {"anchor", "intervalSec", "interval_sec"},
            "cron": {"cron"},
        }.get(kind)
        if kind_fields is None:
            return f"schedule[{index}].kind must be once, interval, or cron"
        unknown = _unknown_fields(spec, common | kind_fields, f"schedule[{index}]")
        if unknown:
            return unknown
        if "timezone" in spec and not isinstance(spec["timezone"], str):
            return f"schedule[{index}].timezone must be a string"
        if "timezone" in spec and not spec["timezone"].strip():
            return f"schedule[{index}].timezone must not be empty"
        if "enabled" in spec and not isinstance(spec["enabled"], bool):
            return f"schedule[{index}].enabled must be a boolean"
        if "misfirePolicy" in spec:
            if (not isinstance(spec["misfirePolicy"], str)
                    or spec["misfirePolicy"] not in {"fire_now", "skip"}):
                return f"schedule[{index}].misfirePolicy must be fire_now or skip"
        if "graceSec" in spec:
            grace = spec["graceSec"]
            if (isinstance(grace, bool) or not isinstance(grace, (int, float))
                    or not math.isfinite(float(grace))
                    or not 0 <= float(grace) <= 86400):
                return f"schedule[{index}].graceSec must be between 0 and 86400"
        if kind == "once":
            at = spec.get("at")
            if not isinstance(at, str) or not at.strip():
                return f"schedule[{index}].at is required"
            parsed_at = _job_cron.parse_datetime(at)
            if parsed_at is None:
                return f"schedule[{index}].at must be a valid ISO-8601 local time"
            if parsed_at <= datetime.now().replace(microsecond=0):
                return f"schedule[{index}].at must be in the future"
        if kind == "interval":
            if "intervalSec" in spec and "interval_sec" in spec:
                return f"schedule[{index}] must use only one intervalSec field spelling"
            interval = spec.get("intervalSec", spec.get("interval_sec"))
            if (isinstance(interval, bool) or not isinstance(interval, (int, float))
                    or not math.isfinite(float(interval)) or interval <= 0):
                return f"schedule[{index}].intervalSec must be a positive number"
            if "anchor" in spec and (
                    not isinstance(spec["anchor"], str) or not spec["anchor"].strip()
                    or _job_cron.parse_datetime(spec["anchor"]) is None):
                return f"schedule[{index}].anchor must be a valid ISO-8601 local time"
        if kind == "cron":
            expression = spec.get("cron")
            if not isinstance(expression, str) or not expression.strip():
                return f"schedule[{index}].cron is required"
            try:
                _job_cron.parse_cron(expression)
            except ValueError as exc:
                return f"schedule[{index}].cron is invalid: {exc}"
        try:
            _job_cron.next_fire_after(spec, datetime.now().replace(microsecond=0),
                                      tz_name=spec.get("timezone"))
        except ValueError as exc:
            return f"schedule[{index}] is invalid: {exc}"
    return None


def _validate_message_schedule(schedule: object) -> str | None:
    if not isinstance(schedule, dict):
        return "schedule must be an object"
    kind = schedule.get("type")
    if not isinstance(kind, str):
        return "schedule.type must be a string"
    fields = {
        "once": {"at", "delaySeconds"},
        "interval": {"intervalSeconds"},
        "weekly": {"weekday", "time", "timezone"},
    }.get(kind)
    if fields is None:
        return "schedule.type must be once, interval, or weekly"
    unknown = _unknown_fields(schedule, {"type"} | fields, "schedule")
    if unknown:
        return unknown
    if kind == "once":
        if (("at" in schedule) == ("delaySeconds" in schedule)
                or ("at" in schedule and (
                    not isinstance(schedule["at"], str) or not schedule["at"].strip()))):
            return "once schedule requires exactly one of at or delaySeconds"
        if "at" in schedule:
            try:
                at = background_jobs._parse_at(schedule["at"])
            except ValueError as exc:
                return str(exc)
            if at <= datetime.now().timestamp():
                return "schedule.at must be in the future"
        if "delaySeconds" in schedule and (
                isinstance(schedule["delaySeconds"], bool)
                or not isinstance(schedule["delaySeconds"], (int, float))
                or not math.isfinite(float(schedule["delaySeconds"]))
                or schedule["delaySeconds"] <= 0):
            return "schedule.delaySeconds must be a positive number"
    elif kind == "interval":
        value = schedule.get("intervalSeconds")
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value)) or value <= 0):
            return "schedule.intervalSeconds must be a positive number"
    else:
        if (isinstance(schedule.get("weekday"), bool)
                or not isinstance(schedule.get("weekday"), int)
                or schedule["weekday"] not in range(7)):
            return "schedule.weekday must be an integer from 0 to 6"
        clock = schedule.get("time")
        if not isinstance(clock, str) or not background_jobs._CLOCK_RE.fullmatch(clock):
            return "schedule.time must be HH:MM or HH:MM:SS"
        if "timezone" in schedule and (
                not isinstance(schedule["timezone"], str)
                or not schedule["timezone"].strip()):
            return "schedule.timezone must be a non-empty string"
    return None


@router.post("")
@router.post("/")
async def create_job(data: dict):
    """Create a user-creatable Job using a strict schema selected by kind."""
    if not isinstance(data, dict):
        return _err("invalid_argument", "request body must be a JSON object")
    kind = data.get("kind", background_jobs.SCHEDULED_TASK_KIND)
    if not isinstance(kind, str):
        return _err("invalid_argument", "kind must be a string")
    common_error = _name_description_error(data)
    if common_error:
        return _err("invalid_argument", common_error)
    root = _job_root()

    if kind == background_jobs.SCHEDULED_TASK_KIND:
        unknown = _unknown_fields(
            data, {"kind", "name", "description", "target", "text", "action",
                   "schedule", "maxRuns", "misfirePolicy", "enabled", "paused"},
            kind)
        if unknown:
            return _err("invalid_argument", unknown)
        action = data.get("action", {"api": "assign"})
        if not isinstance(action, dict) or not isinstance(action.get("api"), str):
            return _err("invalid_argument", "action must contain a string api")
        action_api = action["api"]
        if action_api in {"assign", "send_session"}:
            action_error = _unknown_fields(action, {"api"}, "session action")
            required_target = True
            text = data.get("text")
            if not isinstance(text, str) or not text.strip():
                return _err("invalid_argument", f"text is required for {action_api}")
        elif action_api == "shell":
            action_error = _unknown_fields(action, {"api", "args"}, "shell action")
            args = action.get("args")
            if not isinstance(args, dict):
                return _err("invalid_argument", "shell action requires an args object")
            action_error = action_error or _unknown_fields(
                args, {"command", "cwd"}, "shell action args")
            try:
                command, cwd = background_jobs.validate_shell_action(
                    args.get("command"), args.get("cwd"))
            except ValueError as exc:
                return _err("invalid_argument", str(exc))
            action = {"api": "shell", "args": {
                "command": command, "cwd": str(cwd)}}
            required_target = False
            if "text" in data:
                return _err("invalid_argument", "text is not applicable to shell actions")
        else:
            return _err("invalid_argument",
                        "action.api must be assign, send_session, or shell")
        if action_error:
            return _err("invalid_argument", action_error)
        target, target_error = _session_target(data, required=required_target)
        if target_error:
            return _err("invalid_argument", target_error)
        sid = target.get("sessionId") if target else None
        if sid and not _session_exists(sid):
            return _err("session_not_found", f"Session {sid} not found")
        if "schedule" not in data:
            return _err("invalid_schedule", "schedule is required")
        schedule_error = _validate_scheduled_specs(data["schedule"])
        if schedule_error:
            return _err("invalid_schedule", schedule_error)
        misfire = data.get("misfirePolicy", "fire_now")
        if not isinstance(misfire, str) or misfire not in {"fire_now", "skip"}:
            return _err("invalid_argument", "misfirePolicy must be fire_now or skip")
        max_runs = data.get("maxRuns")
        if max_runs is not None and (
                isinstance(max_runs, bool) or not isinstance(max_runs, int) or max_runs <= 0):
            return _err("invalid_argument", "maxRuns must be a positive integer or null")
        enabled = data.get("enabled", True)
        paused = data.get("paused", False)
        if not isinstance(enabled, bool) or not isinstance(paused, bool):
            return _err("invalid_argument", "enabled and paused must be booleans")
        from packages.scheduler import store as scheduler_store

        try:
            task = scheduler_store.create_task({
                "name": data.get("name", ""),
                "description": data.get("description", ""),
                "target_session_id": sid,
                "text": data.get("text", ""),
            "schedule": (data["schedule"] if isinstance(data["schedule"], list)
                         else [data["schedule"]]),
                "misfire_policy": misfire,
                "max_runs": max_runs,
                "enabled": enabled,
                "paused": paused,
                "action": action,
            })
        except ValueError as exc:
            return _err("invalid_schedule", str(exc))
        job = _find_job(task["id"]) or {}
        view = background_jobs.job_public_view(job) if job else None
    elif kind == background_jobs.BACKGROUND_PROCESS_KIND:
        unknown = _unknown_fields(
            data, {"kind", "name", "description", "label", "target", "argv", "cwd"},
            kind)
        if unknown:
            return _err("invalid_argument", unknown)
        target, target_error = _session_target(data, required=True)
        if target_error:
            return _err("invalid_argument", target_error)
        sid = target["sessionId"]
        if not _session_exists(sid):
            return _err("session_not_found", f"Session {sid} not found")
        if "label" in data and not isinstance(data["label"], str):
            return _err("invalid_argument", "label must be a string")
        try:
            job = background_jobs.start(
                sid, data.get("argv"), data.get("cwd"),
                label=data.get("label"),
                name=data.get("name"),
                description=data.get("description"),
                creator_session_id=None,
                registry_root=root,
            )
        except ValueError as exc:
            return _err("invalid_argument", str(exc))
        except OSError as exc:
            return _err("runner_spawn_failed", str(exc))
        view = background_jobs.job_public_view(job)
    elif kind in {background_jobs.SESSION_MESSAGE_KIND,
                  background_jobs.SESSION_BROADCAST_KIND}:
        unknown = _unknown_fields(
            data, {"kind", "name", "description", "target", "text", "schedule"},
            kind)
        if unknown:
            return _err("invalid_argument", unknown)
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return _err("invalid_argument", "text is required")
        target, target_error = _session_target(
            data, required=True, allow_many=kind == background_jobs.SESSION_BROADCAST_KIND)
        if target_error:
            return _err("invalid_argument", target_error)
        ids = (target.get("sessionIds") if kind == background_jobs.SESSION_BROADCAST_KIND
               else [target["sessionId"]])
        if not ids:
            return _err("invalid_argument", "broadcast target.sessionIds is required")
        for sid in ids:
            if not _session_exists(sid):
                return _err("session_not_found", f"Session {sid} not found")
        if "schedule" not in data:
            return _err("invalid_schedule", "schedule is required")
        schedule_error = _validate_message_schedule(data["schedule"])
        if schedule_error:
            return _err("invalid_schedule", schedule_error)
        try:
            if kind == background_jobs.SESSION_MESSAGE_KIND:
                job = background_jobs.start_message(
                    ids[0], text, data["schedule"], name=data.get("name"),
                    description=data.get("description"), source="agent",
                    source_session_id=None, creator_session_id=None,
                    registry_root=root)
            else:
                job = background_jobs.start_broadcast(
                    ids, text, data["schedule"], name=data.get("name"),
                    description=data.get("description"), source="agent",
                    source_session_id=None, creator_session_id=None,
                    registry_root=root)
        except ValueError as exc:
            return _err("invalid_argument", str(exc))
        view = background_jobs.job_public_view(job)
    else:
        message = ("main-lifecycle is system-managed and cannot be created from Jobs"
                   if kind == background_jobs.SERVICE_LIFECYCLE_KIND
                   else f"unsupported creatable Job kind: {kind}")
        return _err("invalid_argument", message)

    _emit({"type": "job.created", "jobId": job.get("jobId"), "job": view})
    return _ok(job=view)


@router.get("/kinds")
async def job_kinds():
    """kind 元数据：GUI 徽标/筛选用。"""
    return _ok(kinds=[
        {"kind": background_jobs.BACKGROUND_PROCESS_KIND, "label": "后台进程",
         "hasSchedule": False, "hasProcess": True, "creatable": True,
         "createMode": "immediate", "createFields": ["name", "description",
             "label", "target.sessionId", "argv", "cwd"]},
        {"kind": background_jobs.SESSION_MESSAGE_KIND, "label": "定时消息",
         "hasSchedule": True, "hasProcess": False, "creatable": True,
         "createMode": "scheduled", "createFields": ["name", "description",
             "target.sessionId", "text", "schedule.type", "schedule.at",
             "schedule.delaySeconds", "schedule.intervalSeconds",
             "schedule.weekday", "schedule.time", "schedule.timezone"]},
        {"kind": background_jobs.SESSION_BROADCAST_KIND, "label": "群发消息",
         "hasSchedule": True, "hasProcess": False, "creatable": True,
         "createMode": "scheduled", "createFields": ["name", "description",
             "target.sessionIds", "text", "schedule.type", "schedule.at",
             "schedule.delaySeconds", "schedule.intervalSeconds",
             "schedule.weekday", "schedule.time", "schedule.timezone"]},
        {"kind": background_jobs.SCHEDULED_TASK_KIND, "label": "定时任务",
         "hasSchedule": True, "hasProcess": False, "creatable": True,
         "createMode": "scheduled", "createFields": ["name", "description",
             "action.api", "action.args.command", "action.args.cwd",
             "target.sessionId", "text", "schedule[].kind", "schedule[].at",
             "schedule[].intervalSec", "schedule[].anchor", "schedule[].cron",
             "schedule[].timezone", "schedule[].enabled",
             "schedule[].misfirePolicy", "schedule[].graceSec", "maxRuns",
             "misfirePolicy", "enabled", "paused"]},
        {"kind": background_jobs.SERVICE_LIFECYCLE_KIND, "label": "服务生命周期",
         "hasSchedule": False, "hasProcess": False, "creatable": False,
         "createMode": "system", "createFields": []},
    ])


@router.get("/templates")
async def list_templates():
    """schedule 模板清单（快捷创建引用，PLAN §4）。"""
    return _ok(templates=_job_templates.list_templates())


@router.post("/templates")
async def create_template(data: dict):
    """新增自定义 schedule 模板。"""
    if not isinstance(data, dict):
        return _err("invalid_argument", "request body must be a JSON object")
    try:
        template = _job_templates.create_template(data)
    except ValueError as exc:
        return _err("invalid_argument", str(exc))
    return _ok(template=template)


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    ok = _job_templates.delete_template(template_id)
    if not ok:
        return _err("not_found", f"template {template_id} not found (builtin templates cannot be deleted)")
    return _ok(deleted=True, templateId=template_id)


@router.get("/next")
async def next_preview(count: int = 5):
    """全 kind 下一次触发点预览（scheduled-task entries + message nextRunAt）。"""
    try:
        count = max(1, min(int(count), _job_cron.MAX_PREVIEW))
    except (TypeError, ValueError):
        count = 5
    now = datetime.now().replace(microsecond=0)
    items: list[dict] = []
    for job in background_jobs.list_jobs(registry_root=_job_root()):
        kind = job.get("kind")
        if kind == background_jobs.SCHEDULED_TASK_KIND:
            spec_entries = [entry for entry in (job.get("schedule") or [])
                            if entry.get("enabled", True)]
            for entry in spec_entries:
                pr = {k: v for k, v in entry.items()
                      if k not in ("id", "enabled", "nextFireAt", "lastFireAt",
                                   "misfirePolicy", "graceSec")}
                try:
                    point = _job_cron.next_fire_after(
                        pr, now, tz_name=pr.get("timezone"))
                except ValueError:
                    continue
                if point is not None:
                    items.append({"jobId": job.get("jobId"),
                                  "taskId": job.get("taskId"),
                                  "name": job.get("name"),
                                  "fireAt": background_jobs._iso_local(point)})
        elif kind in (background_jobs.SESSION_MESSAGE_KIND,
                      background_jobs.SESSION_BROADCAST_KIND):
            raw = job.get("nextRunAt")
            if raw:
                items.append({"jobId": job.get("jobId"), "taskId": None,
                              "name": job.get("name"), "fireAt": raw})
    items.sort(key=lambda item: str(item["fireAt"]))
    return _ok(next=items[:count])


@router.get("/{job_id}")
async def get_job(job_id: str):
    job = _find_job(job_id)
    if job is None:
        return _err("not_found", f"job {job_id} not found")
    return _ok(job=_view(job))


# ── 管理 ──


@router.patch("/{job_id}")
async def patch_job(job_id: str, data: dict):
    """Update fields supported by the immutable kind of an existing Job."""
    if not isinstance(data, dict):
        return _err("invalid_argument", "request body must be a JSON object")
    job = _find_job(job_id)
    if job is None:
        return _err("not_found", f"job {job_id} not found")

    kind = job.get("kind")
    editable_fields = {
        background_jobs.SCHEDULED_TASK_KIND: {
            "name", "description", "text", "enabled", "paused", "target",
            "schedule", "maxRuns", "misfirePolicy", "action",
        },
        background_jobs.SESSION_MESSAGE_KIND: {
            "name", "description", "text", "target", "schedule",
        },
        background_jobs.SESSION_BROADCAST_KIND: {
            "name", "description", "text", "target", "schedule",
        },
        background_jobs.BACKGROUND_PROCESS_KIND: {
            "name", "description", "target",
        },
        background_jobs.SERVICE_LIFECYCLE_KIND: {"name", "description"},
    }
    unknown = _unknown_fields(data, editable_fields.get(kind, set()),
                              f"{kind} edit")
    if unknown:
        return _err("invalid_argument", unknown)

    if kind in {background_jobs.SESSION_MESSAGE_KIND,
                background_jobs.SESSION_BROADCAST_KIND}:
        target_ids = None
        if "target" in data:
            target, target_error = _session_target(
                data, required=True,
                allow_many=kind == background_jobs.SESSION_BROADCAST_KIND)
            if target_error:
                return _err("invalid_argument", target_error)
            target_ids = (target.get("sessionIds")
                          if kind == background_jobs.SESSION_BROADCAST_KIND
                          else [target["sessionId"]])
            for sid in target_ids or []:
                if not _session_exists(sid):
                    return _err("session_not_found", f"Session {sid} not found")
                access_error = _source_target_access_error(
                    job.get("sourceSessionId"), sid)
                if access_error:
                    return _err("readonly_session", access_error)
        if "schedule" in data:
            schedule_error = _validate_message_schedule(data["schedule"])
            if schedule_error:
                return _err("invalid_schedule", schedule_error)
        try:
            updated = background_jobs.update_message(
                job["jobId"],
                name=data.get("name"),
                description=data.get("description"),
                text=data.get("text"),
                schedule=data.get("schedule") if "schedule" in data else None,
                target_session_ids=target_ids,
                registry_root=_job_root(),
            )
        except ValueError as exc:
            code = "not_found" if "not found" in str(exc) else "invalid_argument"
            return _err(code, str(exc))
        view = background_jobs.job_public_view(updated)
        _emit({"type": "job.updated", "jobId": job["jobId"], "job": view})
        return _ok(job=view)

    changes: dict = {}
    if "name" in data:
        name = background_jobs.normalize_name(data.get("name"))
        if name is None:
            return _err("invalid_argument",
                        "name must be a non-empty string (blank falls back to default at creation)")
        changes["name"] = name
    if "description" in data:
        if not isinstance(data.get("description"), str):
            return _err("invalid_argument", "description must be a string")
        changes["description"] = background_jobs.normalize_description(
            data.get("description"))
    if "text" in data:
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return _err("invalid_argument", "text must be a non-empty string")
        if (kind == background_jobs.SCHEDULED_TASK_KIND
                and isinstance(job.get("action"), dict)
                and job["action"].get("api") == "shell"
                and "action" not in data):
            return _err("invalid_argument", "text is not applicable to shell actions")
        changes["text"] = text
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            return _err("invalid_argument", "enabled must be a boolean")
        changes["enabled"] = data["enabled"]
    if "paused" in data:
        if not isinstance(data["paused"], bool):
            return _err("invalid_argument", "paused must be a boolean")
        changes["paused"] = data["paused"]
    if "target" in data:
        target, target_error = _session_target(data, required=False)
        if target_error:
            return _err("invalid_argument", target_error)
        sid = target.get("sessionId") if target else None
        if sid:
            if not _session_exists(sid):
                return _err("session_not_found", f"Session {sid} not found")
            access_error = _source_target_access_error(
                job.get("sourceSessionId"), sid)
            if access_error:
                return _err("readonly_session", access_error)
        changes["target"] = {"sessionId": sid}
    if "action" in data:
        action, action_error = _normalize_scheduled_action(data["action"])
        if action_error:
            return _err("invalid_argument", action_error)
        action_api = action["api"]
        old_action = job.get("action")
        old_api = (old_action.get("api", "assign")
                   if isinstance(old_action, dict) else "assign")
        if action_api != old_api and job.get("undeliveredFires"):
            return _err(
                "invalid_argument",
                "deliver or resolve undelivered Session fires before changing the action",
            )
        proposed_target = changes.get(
            "target", {"sessionId": job.get("targetSessionId")})
        target_sid = (proposed_target.get("sessionId")
                      if isinstance(proposed_target, dict) else None)
        if action_api in {"assign", "send_session"}:
            if not isinstance(target_sid, str) or not target_sid.strip():
                return _err("invalid_argument",
                            f"target.sessionId is required for {action_api}")
            if not _session_exists(target_sid):
                return _err("session_not_found", f"Session {target_sid} not found")
        elif target_sid and not _session_exists(target_sid):
            return _err("session_not_found", f"Session {target_sid} not found")
        merged_text = data.get("text", job.get("text"))
        if action_api in {"assign", "send_session"}:
            if not isinstance(merged_text, str) or not merged_text.strip():
                return _err("invalid_argument", f"text is required for {action_api}")
        else:
            if "text" in data:
                return _err("invalid_argument", "text is not applicable to shell actions")
            changes["text"] = ""
        changes["action"] = action
    if "maxRuns" in data:
        value = data["maxRuns"]
        if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0):
            return _err("invalid_argument", "maxRuns must be a positive integer or null")
        changes["maxRuns"] = value
    if "misfirePolicy" in data:
        if (not isinstance(data["misfirePolicy"], str)
                or data["misfirePolicy"] not in {"fire_now", "skip"}):
            return _err("invalid_argument", "misfirePolicy must be fire_now or skip")
        changes["misfirePolicy"] = data["misfirePolicy"]
    replaced: dict | None = None
    if "schedule" in data:
        # 多 entry 整体替换（仅 scheduled-task；其余 kind 无 entry 列表）
        if job.get("kind") != background_jobs.SCHEDULED_TASK_KIND:
            return _err("invalid_argument",
                        "schedule patch is only supported for scheduled-task jobs")
        if not isinstance(data.get("schedule"), list):
            return _err("invalid_schedule", "schedule patch must be an array of specs")
        schedule_error = _validate_scheduled_specs(data.get("schedule"))
        if schedule_error:
            return _err("invalid_schedule", schedule_error)
        from packages.scheduler import store as scheduler_store

        task_key = job.get("taskId") or job["jobId"]
        try:
            replaced = scheduler_store.replace_task_schedule(task_key,
                                                             data.get("schedule"))
        except ValueError as exc:
            return _err("invalid_schedule", str(exc))
        if replaced is None:
            return _err("not_found", f"job {job_id} not found")
    misfire_policy_change = changes.pop("misfirePolicy", None)
    if not changes and misfire_policy_change is None:
        if replaced is None:
            return _err("invalid_argument", "no updatable fields in request body")
        # 仅 schedule 替换：已生效，直接返回
        view = background_jobs.job_public_view(replaced)
        _emit({"type": "job.updated", "jobId": job["jobId"], "job": view})
        return _ok(job=view)

    try:
        updated = (background_jobs.update_job_field(
            job["jobId"], changes, registry_root=_job_root())
            if changes else (replaced or job))
        if misfire_policy_change is not None:
            from packages.scheduler import store as scheduler_store

            updated = scheduler_store.set_task_misfire_policy(
                job.get("taskId") or job["jobId"], misfire_policy_change)
            if updated is None:
                return _err("not_found", f"job {job_id} not found")
    except ValueError as exc:
        return _err("not_found", str(exc))
    _emit({"type": "job.updated", "jobId": job["jobId"],
           "job": background_jobs.job_public_view(updated)})
    return _ok(job=background_jobs.job_public_view(updated))


@router.delete("/{job_id}")
async def delete_job(job_id: str):
    job = _find_job(job_id)
    if job is None:
        return _err("not_found", f"job {job_id} not found")
    try:
        if job.get("kind") == background_jobs.SCHEDULED_TASK_KIND:
            from packages.scheduler import store as scheduler_store
            task_id = job.get("taskId") or job["jobId"]
            ok = scheduler_store.delete_task(task_id)
            if not ok:
                ok = background_jobs.delete_job(job["jobId"], registry_root=_job_root())
        else:
            ok = background_jobs.delete_job(job["jobId"], registry_root=_job_root())
    except ValueError as exc:
        return _err("job_busy", str(exc))
    if not ok:
        return _err("not_found", f"job {job_id} not found")
    _emit({"type": "job.deleted", "jobId": job["jobId"]})
    return _ok(deleted=True, jobId=job["jobId"])


@router.get("/{job_id}/runs")
async def job_runs(job_id: str, limit: int = 100):
    job = _find_job(job_id)
    if job is None:
        return _err("not_found", f"job {job_id} not found")
    if not isinstance(limit, int) or limit < 1 or limit > 500:
        return _err("invalid_argument", "limit must be between 1 and 500")
    task_key = job.get("taskId") or job["jobId"]
    runs = background_jobs.list_run_records(
        task_id=task_key, limit=limit, registry_root=_job_root())
    return _ok(runs=runs)


@router.post("/{job_id}/run-now")
async def run_job_now(job_id: str):
    """手动触发一次：scheduled-task 走兼容层 run_now；message 类到点重投。"""
    job = _find_job(job_id)
    if job is None:
        return _err("not_found", f"job {job_id} not found")
    if job.get("kind") != background_jobs.SCHEDULED_TASK_KIND:
        return _err("invalid_argument",
                    f"run-now is not supported for kind {job.get('kind')}")
    from packages.scheduler import engine as scheduler_engine
    task_id = job.get("taskId") or job["jobId"]
    result = await scheduler_engine.run_now(task_id)
    if not result.get("ok"):
        error = result.get("error") or {}
        return _err(str(error.get("code") or "engine_error"),
                    str(error.get("message") or "run-now failed"))
    job = _find_job(job_id) or job
    # 注意：内核 run_now 已发 scheduler.task.fired（含 tick 循环统一契约），
    # 这里不再重复发 job.fired——一次触发至多一个 fire 事件，GUI 只订阅前者。
    return _ok(run=result.get("run"), job=_view(job))
