"""Job 统一 HTTP API —— ``/api/jobs/*``（P2，PLAN §5）。

全 kind 一览/详情/管理的统一入口（GUI JobsView 的后端契约）：

- ``GET  /api/jobs``            列表（kind/status 过滤，active-first 排序）
- ``GET  /api/jobs/{id}``       详情（结构化 source/target 视图）
- ``PATCH /api/jobs/{id}``      通用字段更新（name/description/enabled/paused/target 切换）
- ``DELETE /api/jobs/{id}``     删除
- ``GET  /api/jobs/{id}/runs``  执行历史（runs.jsonl，最新在前）
- ``POST /api/jobs/{id}/run-now`` 手动触发（scheduled-task / message 类）
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


def _find_job(job_id: str) -> dict | None:
    """按 jobId（或 scheduled-task 的 taskId）取记录。"""
    try:
        job = background_jobs.get(job_id)
    except ValueError:
        job = None
    if job is not None and (job.get("jobId") == job_id or job_id in {
            job.get("taskId")}):
        return job
    # 对外主键 taskId（sch_ 前缀）兜底寻址
    for candidate in background_jobs.list_jobs():
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
            for j in background_jobs.list_jobs()]
    if kind:
        jobs = [j for j in jobs if j.get("kind") == kind]
    if status:
        jobs = [j for j in jobs if j.get("status") == status]
    if not includeCompleted:
        jobs = [j for j in jobs if j.get("status") != "completed"]
    jobs.sort(key=lambda j: (_KIND_ORDER.get(str(j.get("status")), 9),
                             str(j.get("updatedAt") or "")), reverse=False)
    return _ok(jobs=jobs)


@router.get("/kinds")
async def job_kinds():
    """kind 元数据：GUI 徽标/筛选用。"""
    return _ok(kinds=[
        {"kind": background_jobs.BACKGROUND_PROCESS_KIND, "label": "后台进程",
         "hasSchedule": False, "hasProcess": True},
        {"kind": background_jobs.SESSION_MESSAGE_KIND, "label": "定时消息",
         "hasSchedule": True, "hasProcess": False},
        {"kind": background_jobs.SESSION_BROADCAST_KIND, "label": "群发消息",
         "hasSchedule": True, "hasProcess": False},
        {"kind": background_jobs.SCHEDULED_TASK_KIND, "label": "定时任务",
         "hasSchedule": True, "hasProcess": False},
        {"kind": background_jobs.SERVICE_LIFECYCLE_KIND, "label": "服务生命周期",
         "hasSchedule": False, "hasProcess": False},
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
    for job in background_jobs.list_jobs():
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
    """通用更新：name / description / enabled / paused / target（归属切换）。"""
    if not isinstance(data, dict):
        return _err("invalid_argument", "request body must be a JSON object")
    job = _find_job(job_id)
    if job is None:
        return _err("not_found", f"job {job_id} not found")

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
    if "enabled" in data:
        changes["enabled"] = bool(data.get("enabled"))
    if "paused" in data:
        changes["paused"] = bool(data.get("paused"))
    if "target" in data:
        raw = data.get("target")
        sid = raw.get("sessionId") if isinstance(raw, dict) else raw
        if not isinstance(sid, str) or not sid.strip():
            return _err("invalid_argument", "target.sessionId is required")
        changes["target"] = {"sessionId": sid.strip()}
    if not changes:
        return _err("invalid_argument", "no updatable fields in request body")

    try:
        updated = background_jobs.update_job_field(
            job["jobId"], changes, registry_root=None)
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
    if job.get("kind") == background_jobs.SCHEDULED_TASK_KIND:
        from packages.scheduler import store as scheduler_store
        task_id = job.get("taskId") or job["jobId"]
        ok = scheduler_store.delete_task(task_id)
        if not ok:
            ok = background_jobs.delete_job(job["jobId"])
    else:
        ok = background_jobs.delete_job(job["jobId"])
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
    runs = background_jobs.list_run_records(task_id=task_key, limit=limit)
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
    _emit({"type": "job.fired", "jobId": job["jobId"],
           "taskId": task_id, "run": result.get("run")})
    return _ok(run=result.get("run"), job=_view(job))
