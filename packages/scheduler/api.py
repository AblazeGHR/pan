"""Pan 定时任务插件 — HTTP API 层。

契约来源：``data/workdirs/time_setting/PLAN_SCHEDULER.md`` §4（唯一契约源）。

职责边界：本模块只做参数校验、camelCase 字段映射、统一响应包络与 WS 广播；
历史读取委托给无副作用的 ``history``，预览 / 状态委托给 ``engine``。

**禁止 import ``packages.web.server``**（会与 server.py 形成循环依赖）：WS 广播
函数由 server.py 在启动时通过 :func:`bind` 注入。
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from packages.automation.authorization import may_read_jobs, principal, require_job

from packages.core import session as sess
from packages.core.config import load_config

from . import cron as scheduler_cron
from . import engine as scheduler_engine
from .history import History

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])
from .event_api import router as event_sources_router
router.include_router(event_sources_router)


RETIRED = {"code": "retired",
           "message": "Legacy scheduler write entrypoint retired; use canonical event/job operations"}


def retire(endpoint):
    """Authenticate, then refuse a pre-canonical scheduler mutation.

    Historical task reads and execution history stay served so existing
    schedules remain inspectable until their owner is drained; create, update,
    delete, pause/resume, run-now and event-source writes are not.
    R6-C removed owner-switch entirely; its old address is not found.
    """
    async def retired_route(request: Request):
        await principal(request)
        raise HTTPException(status_code=410, detail=RETIRED)
    retired_route.__name__ = endpoint.__name__
    retired_route.__doc__ = endpoint.__doc__
    retired_route.retirement_boundary = True
    return retired_route


DEFAULT_TIMEZONE = "Asia/Shanghai"
_SCHEDULE_KINDS = ("once", "interval", "cron")
_MISFIRE_POLICIES = ("fire_now", "skip")

# 内部 snake_case → 出口 camelCase（PLAN §4.4）
_FIELD_RENAMES = {
    "target_session_id": "targetSessionId",
    "next_fire_at": "nextFireAt",
    "last_fire_at": "lastFireAt",
    "last_status": "lastStatus",
    "last_error": "lastError",
    "run_count": "runCount",
    "max_runs": "maxRuns",
    "misfire_policy": "misfirePolicy",
    "created_at": "createdAt",
    "updated_at": "updatedAt",
}  # name / description 两侧同名，无需映射

_RUN_RENAMES = {
    "run_id": "runId",
    "task_id": "taskId",
    "fire_at": "fireAt",
    "actual_at": "actualAt",
    "dispatch_key": "dispatchKey",
    "session_id": "sessionId",
    "worker_id": "workerId",
}


def _compat(request):
    return getattr(getattr(getattr(request, 'app', None), 'state', None), 'automation', None) if request is not None else None


# 注入的 WS 广播函数（server.py → bind(broadcast=broadcast)）
_state: dict = {"broadcast": None}


def bind(broadcast=None) -> None:
    """注入 WS 广播函数（由 ``packages/web/server.py`` 在启动时调用）。"""
    _state["broadcast"] = broadcast


# ── 响应包络 ──


def _ok(**payload):
    return {"ok": True, **payload}


def _err(code: str, message: str):
    return {"ok": False, "error": {"code": code, "message": message}}


# ── 字段映射 ──


def _public(task: dict | None) -> dict | None:
    """内核 snake_case 任务对象 → 出口 camelCase。"""
    if not isinstance(task, dict):
        return task
    out: dict = {}
    for key, value in task.items():
        out[_FIELD_RENAMES.get(key, key)] = value
    schedule = out.get("schedule")
    if isinstance(schedule, dict):
        sch = dict(schedule)
        # 内核可能回 snake 的 interval_sec，统一收敛到 intervalSec
        if "interval_sec" in sch:
            sch.setdefault("intervalSec", sch.pop("interval_sec"))
        else:
            sch.pop("interval_sec", None)
        out["schedule"] = sch
    # PLAN §4.1 用 nextRunAt/lastRunAt 描述列表字段，§4.4 用 nextFireAt/lastFireAt；
    # 两处都发，避免下游按其中任意一份契约取字段时取空。
    out.setdefault("nextRunAt", out.get("nextFireAt"))
    out.setdefault("lastRunAt", out.get("lastFireAt"))
    return out


def _public_run(run: dict) -> dict:
    return {_RUN_RENAMES.get(k, k): v for k, v in run.items()} if isinstance(run, dict) else run


# ── 配置与会话存在性 ──


def _cfg() -> dict:
    try:
        cfg = load_config().get("scheduler")
    except Exception:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _session_exists(session_id: str) -> bool:
    try:
        return sess.get(session_id) is not None
    except Exception:
        return False


# ── 校验 ──


def _as_bool(value, default=False) -> bool:
    """HTTP/MCP 边界上的布尔归一化（字符串 "false" 不能算 True）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
    return default


def _validate_schedule(raw) -> tuple[dict | None, str | None]:
    """校验 schedule；返回 (spec, None) 或 (None, 原因)。"""
    if not isinstance(raw, dict):
        return None, "schedule must be an object"
    kind = raw.get("kind")
    if kind not in _SCHEDULE_KINDS:
        return None, f"schedule.kind must be one of {', '.join(_SCHEDULE_KINDS)}"

    spec: dict = {
        "kind": kind,
        "timezone": raw.get("timezone") or _cfg().get("default_timezone") or DEFAULT_TIMEZONE,
    }

    if kind == "once":
        at = raw.get("at")
        try:
            datetime.fromisoformat(str(at))
        except (TypeError, ValueError):
            return None, "schedule.at must be an ISO-8601 datetime string"
        spec["at"] = str(at)
    elif kind == "interval":
        sec = raw.get("intervalSec", raw.get("interval_sec"))
        try:
            sec = int(sec)
        except (TypeError, ValueError):
            return None, "schedule.intervalSec must be a positive integer"
        if sec <= 0:
            return None, "schedule.intervalSec must be a positive integer"
        # PLAN §2.1 写 interval_sec、§4.4 出口写 intervalSec；两个键都给内核，
        # 内核读哪个都不会解析失败（出口时 _public() 收敛为 intervalSec）。
        spec["intervalSec"] = sec
        spec["interval_sec"] = sec
        anchor = raw.get("anchor")
        if anchor:
            try:
                datetime.fromisoformat(str(anchor))
            except (TypeError, ValueError):
                return None, "schedule.anchor must be an ISO-8601 datetime string"
            spec["anchor"] = str(anchor)
    else:  # cron
        expr = raw.get("cron")
        if not isinstance(expr, str) or not expr.strip():
            return None, "schedule.cron is required for kind=cron"
        try:
            scheduler_cron.parse_cron(expr)
        except ValueError as exc:
            return None, f"invalid cron expression: {exc}"
        spec["cron"] = expr
    return spec, None


def _validate_max_runs(raw, key: str) -> tuple[object, str | None]:
    """maxRuns：null 或 >=1 的整数。返回 (值, 原因)。"""
    if raw is None:
        return None, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, f"{key} must be null or a positive integer"
    if value < 1:
        return None, f"{key} must be null or a positive integer"
    return value, None


# ── WS 广播 ──


async def _drain(awaitable) -> None:
    try:
        await awaitable
    except Exception:
        pass


def _emit(event: dict) -> None:
    fn = _state.get("broadcast")
    if fn is None:
        return
    try:
        result = fn(event)
    except Exception:
        return
    if inspect.isawaitable(result):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            close = getattr(result, 'close', None)
            if callable(close):close()
            return
        loop.create_task(_drain(result))


async def emit_strict(event: dict) -> bool:
    """严格发射契约：返回事件是否已成功入队 WS 广播通道。

    _emit 是 fire-and-forget（吞异常、无返回值），无法向需要可靠成功信号的
    调用方（Job 终态补发）表达失败。本契约：无通道 / 同步异常 / awaitable
    异常 = False；其余 = True。True 的精确含义：投影管线与对每个已连接
    客户端 channel 的 enqueue 已在本轮 await 内全部完成（含一次事件循环
    让步）；每连接 sender 的 socket 写入按设计不 await（见 server.broadcast
    的隔离契约），传输层尽力，断线由重连 REST 水合收敛。job.updated 是
    状态 upsert，客户端对重复事件幂等，成功后崩溃导致的重发是允许的
    收敛路径。
    """
    fn = _state.get("broadcast")
    if not callable(fn):
        return False
    try:
        result = fn(event)
        if inspect.isawaitable(result):
            await result
        return True
    except Exception:
        return False


def on_event(event: dict) -> None:
    """engine 的事件回调（server.py 用 ``start_loop(on_event=scheduler_api.on_event)`` 注入）。"""
    if isinstance(event, dict) and event.get("type"):
        _emit(event)


def _emit_task(event_type: str, task: dict, **extra) -> None:
    _emit({
        "type": event_type,
        "taskId": task.get("id"),
        "sessionId": task.get("target_session_id"),
        "task": _public(task),
        **extra,
    })


# ── 路由 ──


def _history(request):
    service = getattr(getattr(getattr(request, 'app', None), 'state', None), 'job_final_service', None)
    return History(getattr(service, 'root', None))


async def _history_access(request):
    # Preserve legacy task visibility, while rejecting unauthenticated reads.
    if request is not None:
        await principal(request)


def _history_error(exc):
    return _err("history_unavailable", str(exc))


@router.get("/tasks")
async def list_tasks(includeDisabled: bool = True, request: Request = None,
                     limit: int | None = None, offset: int = 0):
    await _history_access(request)
    if (limit is not None and not 1 <= limit <= 500) or offset < 0:
        return _err("invalid_argument", "limit must be between 1 and 500; offset must be nonnegative")
    try:
        tasks = _history(request).tasks(includeDisabled)
        if not await may_read_jobs(request):
            from packages.jobs.action_runtime import is_action_job
            tasks = [t for t in tasks if not is_action_job(t['history']['source'])
                     or t['history']['source'].get('legacySchedulerCompat')]
        return _ok(tasks=[_public(t) for t in tasks[offset:None if limit is None else offset + limit]], total=len(tasks))
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        return _history_error(exc)


@router.post("/tasks")
async def create_task(data: dict, request: Request = None):
    """Retired legacy producer; authentication and refusal precede all work."""
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request = None):
    await _history_access(request)
    try:
        history = _history(request)
        task = history.get(task_id)
        if task is None:
            return _err("not_found", f"task {task_id} not found")
        source = task['history']['source']
        if not source.get('legacySchedulerCompat'):
            await require_job(request, source)
        return _ok(task=_public(task), **history.evidence(task))
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        return _history_error(exc)


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, data: dict, request: Request = None):
    """Retired legacy producer; authentication and refusal precede all work."""
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, request: Request = None):
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)


@router.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str, request: Request = None):
    """Retired legacy producer; authentication and refusal precede all work."""
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)


@router.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str, request: Request = None):
    """Retired legacy producer; authentication and refusal precede all work."""
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)


@router.post("/tasks/{task_id}/run-now")
async def run_task_now(task_id: str, request: Request = None):
    """Retired legacy producer; authentication and refusal precede all work."""
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)


@router.get("/tasks/{task_id}/runs")
async def task_runs(task_id: str, limit: int = 100, request: Request = None, offset: int = 0):
    await _history_access(request)
    if not 1 <= limit <= 500 or offset < 0:
        return _err("invalid_argument", "limit must be between 1 and 500; offset must be nonnegative")
    try:
        history = _history(request)
        task = history.get(task_id)
        if task is None:
            return _err("not_found", f"task {task_id} not found")
        source = task['history']['source']
        if not source.get('legacySchedulerCompat'):
            await require_job(request, source)
        runs = history.runs(task)
        return _ok(runs=[_public_run(r) for r in runs[offset:offset + limit]], total=len(runs))
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        return _history_error(exc)


@router.get("/next")
async def next_preview(task_id: str | None = None, count: int = 5, request: Request = None):
    """下次触发时间预览（前端「下次运行」列用）。"""
    if not isinstance(count, int) or count < 1 or count > 20:
        return _err("invalid_argument", "count must be between 1 and 20")
    await _history_access(request)
    if task_id and _history(request).get(task_id) is None:
        return _err("not_found", f"task {task_id} not found")
    try:
        history = _history(request)
        tasks = history.tasks()
        if not await may_read_jobs(request):
            from packages.jobs.action_runtime import is_action_job
            allowed = {t['id'] for t in tasks if not is_action_job(t['history']['source'])}
        else:
            allowed = {t['id'] for t in tasks}
        items = await scheduler_engine.preview_next(task_id=task_id, count=count, history=history)
        items = [item for item in items if item['taskId'] in allowed]
    except ValueError as exc:
        return _err("invalid_schedule", str(exc))
    return _ok(next=items or [])


@router.get("/status")
async def engine_status():
    """调度引擎健康状态（running / tickSec / dueScanned / lastTickAt）。"""
    try:
        state = scheduler_engine.status()
    except Exception as exc:  # 引擎不可用时不能让健康检查 500
        return _err("engine_not_running", str(exc))
    return _ok(status=state if isinstance(state, dict) else {})


# R5: retire the old scheduler mutations at the route table.  Handler bodies
# remain for R6 source removal; task reads and run history stay available.
RETIRED_ROUTES = {
    ("POST", "/tasks"),
    ("PATCH", "/tasks/{task_id}"),
    ("DELETE", "/tasks/{task_id}"),
    ("POST", "/tasks/{task_id}/pause"),
    ("POST", "/tasks/{task_id}/resume"),
    ("POST", "/tasks/{task_id}/run-now"),
    ("POST", "/event-sources"),
    ("PUT", "/event-sources/{sid}"),
    ("DELETE", "/event-sources/{sid}"),
}

RETAINED_ROUTES = {
    ("GET", "/tasks"),
    ("GET", "/tasks/{task_id}"),
    ("GET", "/tasks/{task_id}/runs"),
    ("GET", "/next"),
    ("GET", "/status"),
    ("GET", "/event-sources"),
}

for _route in router.routes:
    _path = getattr(_route, "path", None)
    if _path is None:
        # Included sub-router: retire the old event-source mutations on the
        # sub-router itself so both mount points see the same boundary.
        for _sub in _route.original_router.routes:
            _retire = any((method, _sub.path) in RETIRED_ROUTES for method in _sub.methods)
            _known = any((method, _sub.path) in RETIRED_ROUTES | RETAINED_ROUTES
                         for method in _sub.methods)
            if not _known:
                raise RuntimeError(f"Unclassified scheduler route: {sorted(_sub.methods)} {_sub.path}")
            if _retire:
                _sub.endpoint = retire(_sub.endpoint)
                _sub.deprecated = True
                _sub.response_model = None
        continue
    _path = _path.removeprefix(router.prefix)
    _retire = any((method, _path) in RETIRED_ROUTES for method in _route.methods)
    _known = any((method, _path) in RETIRED_ROUTES | RETAINED_ROUTES
                 for method in _route.methods)
    if not _known:
        raise RuntimeError(f"Unclassified scheduler route: {sorted(_route.methods)} {_route.path}")
    if _retire:
        _route.endpoint = retire(_route.endpoint)
        _route.deprecated = True
        # The retirement handler has its own signature; drop any response model
        # FastAPI inferred for the replaced business handler.
        _route.response_model = None
