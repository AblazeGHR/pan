"""Job 统一 HTTP API —— ``/api/jobs/*``（P2，PLAN §5）。

全 kind 一览/详情/管理的统一入口（GUI JobsView 的后端契约）：

- ``GET  /api/jobs``            列表（kind/status 过滤，active-first 排序）
- ``POST /api/jobs``           创建可创建的 kind（按 kind 严格校验字段）
- ``GET  /api/jobs/{id}``       详情（结构化 source/target 视图）
- ``PATCH /api/jobs/{id}``      按 kind 更新其允许字段
- ``PUT /api/jobs/{id}/pin``    持久化 Pan 展示置顶元数据
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

from fastapi import APIRouter, Request
from packages.automation.authorization import may_read_jobs, require_job

from packages.core import background_jobs
from packages.core import config as _config
from packages.jobs.actions import normalize_scheduled_qq_action
from packages.jobs import cron as _job_cron
from packages.jobs import templates as _job_templates
from packages.jobs import pan_bridge, pins

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_KIND_ORDER = {"running": 0, "starting": 1, "scheduled": 2, "pending": 3,
               "completed": 4, "failed": 5, "cancelled": 6}

_state: dict = {"broadcast": None}


def bind(broadcast=None) -> None:
    """注入 WS 广播函数（server.py 启动时调用）。"""
    _state["broadcast"] = broadcast
    background_jobs.register_completed_job_retention(
        on_deleted=lambda job_id: _emit({"type": "job.deleted", "jobId": job_id}))


def _ok(**payload):
    return {"ok": True, **payload}


def _err(code: str, message: str):
    return {"ok": False, "error": {"code": code, "message": message}}


def _view(job: dict) -> dict:
    from packages.jobs.action_runtime import for_root, is_action_job
    runtime=for_root(_job_root())
    if is_action_job(job) and runtime:
        job=runtime.view(job)
    value, status = pins.snapshot(_job_root())
    from packages.jobs.unified import project
    return {**background_jobs.job_public_view(job),'unified':job.get('unified') or project(job),
            **pins.presentation(job["jobId"], value, status)}


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
    remote = pan_bridge.request("get", job_id)
    return pan_bridge.public_job(remote) if remote is not None else None


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
                    includeCompleted: bool = True, request: Request = None):
    """全部 kind 的 job 列表；active-first 排序，可按 kind/status 过滤。"""
    jobs = [background_jobs.job_public_view(j)
            for j in background_jobs.list_jobs(registry_root=_job_root())
            if not j.get("scheduledParentJobId")]
    remote = pan_bridge.request("list")
    if remote is not None:
        if not isinstance(remote, dict) or not isinstance(remote.get("jobs"), list):
            raise pan_bridge.JobServiceUnavailable("invalid independent Job service response")
        local_ids = {job["jobId"] for job in jobs}
        for item in remote["jobs"]:
            job = pan_bridge.public_job(item)
            if job["jobId"] not in local_ids:
                jobs.append(background_jobs.job_public_view(job))
    from packages.jobs.action_runtime import for_root, is_action_job
    if not await may_read_jobs(request):
        jobs=[j for j in jobs if not is_action_job(j) or j.get('legacyCompat')]
    runtime=for_root(_job_root())
    if runtime:
        jobs=[await asyncio.to_thread(runtime.view,j) if is_action_job(j) else j for j in jobs]
    pinned, pin_status = pins.snapshot(_job_root())
    from packages.jobs.unified import project
    jobs = [{**job, 'unified':job.get('unified') or project(job), **pins.presentation(job["jobId"], pinned, pin_status)} for job in jobs]
    if kind:
        jobs = [j for j in jobs if j.get("kind") == kind]
    if status:
        jobs = [j for j in jobs if j.get("status") == status]
    if not includeCompleted:
        jobs = [j for j in jobs if j.get("status") != "completed"]
    jobs.sort(key=lambda j: (not j.get("pinned", False), _KIND_ORDER.get(str(j.get("status")), 9),
                             str(j.get("updatedAt") or "")), reverse=False)
    return _ok(jobs=jobs, pinMetadataStatus=pin_status)


def _all_retention_settings() -> tuple[dict, dict[str, bool]]:
    try:
        return _config.job_retention_settings(_config.load_config())
    except Exception:
        defaults = {rule: dict(_config.COMPLETED_JOB_RETENTION_DEFAULT)
                    for rule in _config.JOB_RETENTION_CONFIG_KEYS}
        return defaults, {rule: False for rule in defaults}


def _retention_response(**extra):
    rules, validity = _all_retention_settings()
    status = background_jobs.completed_job_retention_status()
    return _ok(settings=rules["completed"], rules=rules,
               configValid=all(validity.values()), configValidity=validity,
               lastRun=status["lastRun"], lastRuns=status["lastRuns"], **extra)


def _persist_retention_patches(validated_patches: dict) -> dict:
    """Persist settings away from the asyncio event loop while waiting on locks."""
    with background_jobs._registry_lock("completed_retention_config"):
        raw = _config.read_config_file()
        jobs = raw.get("jobs", {})
        if not isinstance(jobs, dict):
            return _err("invalid_config", "config.json jobs must be an object")
        jobs = dict(jobs)
        for rule, patch in validated_patches.items():
            section_key = _config.JOB_RETENTION_CONFIG_KEYS[rule]
            retention = jobs.get(section_key, {})
            if not isinstance(retention, dict):
                return _err("invalid_config", f"config.json jobs.{section_key} must be an object")
            retention = dict(retention)
            retention.update(patch)
            _, section_valid = _config.parse_retention_settings(retention)
            if not section_valid:
                return _err("invalid_config", f"Existing {rule} settings are invalid; update both fields")
            jobs[section_key] = retention
        raw["jobs"] = jobs
        try:
            _config.save_config(raw)
        except OSError as exc:
            return _err("persistence_failed", str(exc))
    return _retention_response()


@router.get("/settings/completed-retention")
async def get_completed_retention_settings():
    """Read all independent Job record and Job log retention rules."""
    return _retention_response()


@router.put("/settings/completed-retention")
async def put_completed_retention_settings(data: dict):
    """Update requested retention rules while preserving every other setting.

    The flat ``enabled``/``days`` body remains supported as the phase-one
    completed-rule compatibility form. New callers send ``{"rules": {...}}``.
    """
    if not isinstance(data, dict):
        return _err("invalid_argument", "request body must be an object")
    if "rules" in data:
        if set(data) != {"rules"} or not isinstance(data["rules"], dict):
            return _err("invalid_argument", "rules must be the only top-level key and an object")
        patches = data["rules"]
    else:
        if set(data) - {"enabled", "days"}:
            return _err("invalid_argument", "Only rules or completed enabled/days may be updated")
        patches = {"completed": data}
    if not patches:
        return _err("invalid_argument", "Provide at least one retention rule")

    validated_patches = {}
    for rule, patch in patches.items():
        if rule not in _config.JOB_RETENTION_CONFIG_KEYS:
            return _err("invalid_argument", f"unknown retention rule: {rule}")
        if not isinstance(patch, dict) or not patch or set(patch) - {"enabled", "days"}:
            return _err("invalid_argument", f"{rule} must contain enabled and/or days")
        if "enabled" in patch and type(patch["enabled"]) is not bool:
            return _err("invalid_argument", f"{rule}.enabled must be a boolean")
        if "days" in patch:
            days = patch["days"]
            if (days is not None and (type(days) is not int
                    or not (_config.COMPLETED_JOB_RETENTION_MIN_DAYS
                            <= days <= _config.COMPLETED_JOB_RETENTION_MAX_DAYS))):
                return _err("invalid_argument", f"{rule}.days must be null or an integer between 1 and 36500")
        validated_patches[rule] = patch

    return await asyncio.to_thread(_persist_retention_patches, validated_patches)


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
    if api == background_jobs.RESUME_LEGAL_RUNNING_ACTION:
        error = _unknown_fields(action, {"api"}, "resume legal running action")
        return ({"api": api}, None) if not error else (None, error)
    if api == "send_qq":
        return normalize_scheduled_qq_action(action)
    if api != "shell":
        return None, ("action.api must be assign, send_session, "
                      "resume_legal_running, send_qq, or shell")
    error = _unknown_fields(action, {"api", "args"}, "shell action")
    args = action.get("args")
    if not isinstance(args, dict):
        return None, "shell action requires an args object"
    error = error or _unknown_fields(args, {"command", "cwd", "hideWindow"}, "shell action args")
    if error:
        return None, error
    try:
        command, cwd = background_jobs.validate_shell_action(
            args.get("command"), args.get("cwd"))
        from packages.jobs.window import validate_hide_window
        validate_hide_window(args.get("hideWindow", True))
    except ValueError as exc:
        return None, str(exc)
    return {"api": "shell", "args": {
        "command": command, "cwd": str(cwd),
        **({"hideWindow": args["hideWindow"]} if "hideWindow" in args else {})}}, None


def _unknown_fields(value: dict, allowed: set[str], scope: str) -> str | None:
    unknown = sorted(set(value) - allowed)
    return f"unsupported {scope} field(s): {', '.join(unknown)}" if unknown else None


def _name_description_error(data: dict) -> str | None:
    if "name" in data and not isinstance(data["name"], str):
        return "name must be a string"
    if isinstance(data.get("name"), str) and len(data["name"].strip()) > 200:
        return "name must be at most 200 characters"
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
async def create_job(data: dict, request: Request = None):
    """Retired legacy producer; authentication and refusal precede all work."""
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)


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
             "action.args.targetType", "action.args.targetId", "action.args.botUin",
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
async def next_preview(count: int = 5, request: Request = None):
    """全 kind 下一次触发点预览（scheduled-task entries + message nextRunAt）。"""
    try:
        count = max(1, min(int(count), _job_cron.MAX_PREVIEW))
    except (TypeError, ValueError):
        count = 5
    now = datetime.now().replace(microsecond=0)
    items: list[dict] = []
    from packages.jobs.action_runtime import is_action_job
    permitted=await may_read_jobs(request)
    for job in background_jobs.list_jobs(registry_root=_job_root()):
        if is_action_job(job) and not permitted:continue
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
async def get_job(job_id: str, request: Request = None):
    job = _find_job(job_id)
    if job is None:
        return _err("not_found", f"job {job_id} not found")
    if not job.get('legacyCompat'):
        await require_job(request, job)
    return _ok(job=await asyncio.to_thread(_view,job))


# ── 管理 ──


@router.put("/{job_id}/pin")
async def pin_job(job_id: str, data: dict, request: Request = None):
    if set(data) != {"pinned"} or type(data.get("pinned")) is not bool:
        return _err("invalid_argument", "pinned must be a boolean and the only field")
    job = _find_job(job_id)
    if job is None:
        return _err("not_found", f"job {job_id} not found")
    await require_job(request, job, 'updateConfig')
    # Cancelling this await cannot stop the writer thread or roll back its commit.
    # Clients with an interrupted response must GET the Job/list to read durable state.
    try:
        await asyncio.to_thread(pins.set_pin, _job_root(), job["jobId"], data["pinned"])
        view = _view(job)
    except pins.PinMetadataUnavailable:
        return _err("pin_metadata_unavailable", "Job pin metadata unavailable; restore it before editing pins")
    except (OSError, ValueError, UnicodeError):
        return _err("persistence_failed", "Could not persist Job pin metadata; read the Job to confirm current state")
    _emit({"type": "job.updated", "jobId": job["jobId"], "job": view})
    return _ok(job=view)


@router.patch("/{job_id}")
async def patch_job(job_id: str, data: dict, request: Request = None):
    """Retired legacy producer; authentication and refusal precede all work."""
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)


@router.delete("/{job_id}")
async def delete_job(job_id: str):
    job = _find_job(job_id)
    from packages.jobs.action_runtime import is_protected_job as is_action_job
    if is_action_job(job) and job.get('legacyCompat'):
        from packages.jobs.action_runtime import for_root
        runtime = for_root(_job_root())
        if runtime is None:
            return _err('not_found', f"job {job_id} not found")
        try:
            result = await asyncio.to_thread(runtime.delete, job_id, job['configRevision'])
        except ValueError as exc:
            return _err('job_busy', str(exc))
        _emit({'type': 'job.deleted', 'jobId': job_id})
        return _ok(**result)
    if is_action_job(job):
        return _err('user_confirmation_required','Use protected /api/jobs/action-jobs endpoints')
    if job is None:
        return _err("not_found", f"job {job_id} not found")
    if job.get("executionOwner") == "independent-job-service":
        return _err("unsupported_operation", "independent Job record deletion is not available in this phase")
    try:
        if job.get("kind") == background_jobs.SCHEDULED_TASK_KIND:
            from packages.scheduler import store as scheduler_store
            task_id = job.get("taskId") or job["jobId"]
            ok = await asyncio.to_thread(scheduler_store.delete_task, task_id)
            if not ok:
                ok = await asyncio.to_thread(background_jobs.delete_job, job["jobId"], registry_root=_job_root())
        else:
            ok = await asyncio.to_thread(background_jobs.delete_job, job["jobId"], registry_root=_job_root())
    except ValueError as exc:
        return _err("job_busy", str(exc))
    if not ok:
        return _err("not_found", f"job {job_id} not found")
    _emit({"type": "job.deleted", "jobId": job["jobId"]})
    return _ok(deleted=True, jobId=job["jobId"])


@router.get("/{job_id}/runs")
async def job_runs(job_id: str, limit: int = 100, request: Request = None):
    job = _find_job(job_id)
    if job is None:
        return _err("not_found", f"job {job_id} not found")
    if not isinstance(limit, int) or limit < 1 or limit > 500:
        return _err("invalid_argument", "limit must be between 1 and 500")
    task_key = job.get("taskId") or job["jobId"]
    if not job.get('legacyCompat'):
        await require_job(request, job)
    runs = background_jobs.list_run_records(
        task_id=task_key, limit=limit, registry_root=_job_root())
    return _ok(runs=runs)


@router.post("/{job_id}/run-now")
async def run_job_now(job_id: str, request: Request = None):
    """Retired legacy producer; authentication and refusal precede all work."""
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)
