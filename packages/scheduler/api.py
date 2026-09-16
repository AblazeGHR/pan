"""Pan 定时任务插件 — HTTP API 层。

契约来源：``data/workdirs/time_setting/PLAN_SCHEDULER.md`` §4（唯一契约源）。

职责边界：本模块只做参数校验、camelCase 字段映射、统一响应包络与 WS 广播；
持久化 / cron 求值 / 调度循环全部委托给内核（``store`` / ``cron`` / ``engine``）。

**禁止 import ``packages.web.server``**（会与 server.py 形成循环依赖）：WS 广播
函数由 server.py 在启动时通过 :func:`bind` 注入。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime

from fastapi import APIRouter

from packages.core import session as sess
from packages.core.config import load_config

from . import cron as scheduler_cron
from . import engine as scheduler_engine
from . import store as scheduler_store

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


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
}

_RUN_RENAMES = {
    "run_id": "runId",
    "task_id": "taskId",
    "fire_at": "fireAt",
    "actual_at": "actualAt",
    "dispatch_key": "dispatchKey",
    "session_id": "sessionId",
    "worker_id": "workerId",
}

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
            return
        loop.create_task(_drain(result))


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


@router.get("/tasks")
async def list_tasks(includeDisabled: bool = True):
    """列出全部定时任务（含 nextFireAt / lastFireAt / lastStatus）。"""
    tasks = scheduler_store.list_tasks(include_disabled=includeDisabled)
    return _ok(tasks=[_public(t) for t in tasks])


@router.post("/tasks")
async def create_task(data: dict):
    """创建定时任务。Body: {name?, targetSessionId, text, schedule, enabled?, maxRuns?, misfirePolicy?}"""
    if not isinstance(data, dict):
        return _err("invalid_argument", "request body must be a JSON object")

    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return _err("invalid_argument", "text is required")

    target = data.get("targetSessionId") or data.get("target_session_id")
    if not isinstance(target, str) or not target.strip():
        return _err("invalid_argument", "targetSessionId is required")
    if not _session_exists(target):
        return _err("session_not_found", f"Session {target} not found")

    spec, why = _validate_schedule(data.get("schedule"))
    if why:
        return _err("invalid_schedule", why)

    max_runs, why = _validate_max_runs(data.get("maxRuns", data.get("max_runs")), "maxRuns")
    if why:
        return _err("invalid_argument", why)

    policy = data.get("misfirePolicy", data.get("misfire_policy"))
    if policy is None:
        policy = "fire_now"
    if policy not in _MISFIRE_POLICIES:
        return _err("invalid_argument",
                    f"misfirePolicy must be one of {', '.join(_MISFIRE_POLICIES)}")

    payload = {
        "name": data.get("name") if isinstance(data.get("name"), str) else "",
        "target_session_id": target,
        "text": text,
        "enabled": _as_bool(data.get("enabled"), True),
        "paused": False,
        "schedule": spec,
        "max_runs": max_runs,
        "misfire_policy": policy,
    }
    try:
        task = scheduler_store.create_task(payload)
    except ValueError as exc:
        return _err("invalid_schedule", str(exc))
    _emit_task("scheduler.task.created", task)
    return _ok(task=_public(task))


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """单个定时任务详情。"""
    task = scheduler_store.get_task(task_id)
    if task is None:
        return _err("not_found", f"task {task_id} not found")
    return _ok(task=_public(task))


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, data: dict):
    """局部更新：name / text / targetSessionId / schedule / enabled / paused / maxRuns / misfirePolicy。"""
    if not isinstance(data, dict):
        return _err("invalid_argument", "request body must be a JSON object")
    if scheduler_store.get_task(task_id) is None:
        return _err("not_found", f"task {task_id} not found")

    patch: dict = {}
    if "name" in data:
        if not isinstance(data["name"], str):
            return _err("invalid_argument", "name must be a string")
        patch["name"] = data["name"]
    if "text" in data:
        if not isinstance(data["text"], str) or not data["text"].strip():
            return _err("invalid_argument", "text must be a non-empty string")
        patch["text"] = data["text"]
    if "targetSessionId" in data or "target_session_id" in data:
        target = data.get("targetSessionId") or data.get("target_session_id")
        if not isinstance(target, str) or not target.strip():
            return _err("invalid_argument", "targetSessionId is required")
        if not _session_exists(target):
            return _err("session_not_found", f"Session {target} not found")
        patch["target_session_id"] = target
    if "schedule" in data:
        spec, why = _validate_schedule(data["schedule"])
        if why:
            return _err("invalid_schedule", why)
        patch["schedule"] = spec
    if "enabled" in data:
        patch["enabled"] = _as_bool(data["enabled"], False)
    if "paused" in data:
        patch["paused"] = _as_bool(data["paused"], False)
    if "maxRuns" in data or "max_runs" in data:
        max_runs, why = _validate_max_runs(data.get("maxRuns", data.get("max_runs")), "maxRuns")
        if why:
            return _err("invalid_argument", why)
        patch["max_runs"] = max_runs
    if "misfirePolicy" in data or "misfire_policy" in data:
        policy = data.get("misfirePolicy", data.get("misfire_policy"))
        if policy not in _MISFIRE_POLICIES:
            return _err("invalid_argument",
                        f"misfirePolicy must be one of {', '.join(_MISFIRE_POLICIES)}")
        patch["misfire_policy"] = policy

    try:
        task = scheduler_store.update_task(task_id, patch)
    except ValueError as exc:
        return _err("invalid_schedule", str(exc))
    if task is None:
        return _err("not_found", f"task {task_id} not found")
    _emit_task("scheduler.task.updated", task)
    return _ok(task=_public(task))


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """删除定时任务。"""
    task = scheduler_store.get_task(task_id)
    if task is None:
        return _err("not_found", f"task {task_id} not found")
    scheduler_store.delete_task(task_id)
    _emit({"type": "scheduler.task.deleted", "taskId": task_id,
           "sessionId": task.get("target_session_id")})
    return _ok(deleted=True, taskId=task_id)


@router.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str):
    """暂停（跳过触发，但 next_fire_at 仍按锚点推进，恢复后不爆发补跑）。"""
    task = scheduler_store.get_task(task_id)
    if task is None:
        return _err("not_found", f"task {task_id} not found")
    updated = scheduler_store.update_task(task_id, {"paused": True})
    if updated is None:
        return _err("not_found", f"task {task_id} not found")
    _emit_task("scheduler.task.updated", updated)
    return _ok(task=_public(updated))


@router.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    """恢复暂停的任务。"""
    task = scheduler_store.get_task(task_id)
    if task is None:
        return _err("not_found", f"task {task_id} not found")
    updated = scheduler_store.update_task(task_id, {"paused": False})
    if updated is None:
        return _err("not_found", f"task {task_id} not found")
    _emit_task("scheduler.task.updated", updated)
    return _ok(task=_public(updated))


@router.post("/tasks/{task_id}/run-now")
async def run_task_now(task_id: str):
    """立即触发一次（不影响 next_fire_at 的正常推进）。"""
    task = scheduler_store.get_task(task_id)
    if task is None:
        return _err("not_found", f"task {task_id} not found")
    try:
        result = await scheduler_engine.run_now(task_id)
    except RuntimeError as exc:
        return _err("engine_not_running", str(exc))
    except ValueError as exc:
        return _err("invalid_schedule", str(exc))
    if isinstance(result, dict) and result.get("error"):
        error = result["error"]
        if isinstance(error, dict):
            return _err(str(error.get("code") or "engine_not_running"),
                        str(error.get("message") or "run-now failed"))
        return _err("engine_not_running", str(error))
    current = scheduler_store.get_task(task_id) or task
    _emit_task("scheduler.task.fired", current, run=result)
    return _ok(run=result, task=_public(current))


@router.get("/tasks/{task_id}/runs")
async def task_runs(task_id: str, limit: int = 100):
    """某任务的执行历史（runs.jsonl，最新在前）。"""
    if scheduler_store.get_task(task_id) is None:
        return _err("not_found", f"task {task_id} not found")
    if not isinstance(limit, int) or limit < 1 or limit > 500:
        return _err("invalid_argument", "limit must be between 1 and 500")
    runs = scheduler_store.list_runs(task_id=task_id, limit=limit)
    return _ok(runs=[_public_run(r) for r in runs])


@router.get("/next")
async def next_preview(task_id: str | None = None, count: int = 5):
    """下次触发时间预览（前端「下次运行」列用）。"""
    if not isinstance(count, int) or count < 1 or count > 20:
        return _err("invalid_argument", "count must be between 1 and 20")
    if task_id and scheduler_store.get_task(task_id) is None:
        return _err("not_found", f"task {task_id} not found")
    try:
        items = await scheduler_engine.preview_next(task_id=task_id, count=count)
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
