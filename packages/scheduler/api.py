"""Pan 定时任务插件 — HTTP API 层。

契约来源：``data/workdirs/time_setting/PLAN_SCHEDULER.md`` §4（唯一契约源）。

职责边界：本模块只做参数校验、camelCase 字段映射、统一响应包络与 WS 广播；
历史读取委托给无副作用的 ``history``，预览使用 canonical TimeProvider，状态读取绑定的 FinalService。

**禁止 import ``packages.web.server``**（会与 server.py 形成循环依赖）：WS 广播
函数由 server.py 在启动时通过 :func:`bind` 注入。
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from packages.automation.authorization import may_read_jobs, principal, require_job

from packages.core import session as sess
from packages.core.config import load_config

from packages.jobs import cron as scheduler_cron
from .providers import TimeProvider, timestamp
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
    """广播兼容事件；不注册或启动旧 scheduler host。"""
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


async def _canonical_record(request, task_id):
    from packages.jobs.final_api import actor
    from packages.plugins.lifecycle import io
    from packages.scheduler.json_handoff import project, resolve
    service = getattr(request.app.state, 'job_final_service', None)
    if service is None:
        return None
    with service.transaction(readonly=True) as db:
        found = db.execute("SELECT 1 FROM final_documents WHERE kind='json_scheduler_handoff' AND (id=? OR json_extract(document,'$.jobId')=?)", (task_id, task_id)).fetchone()
        if not found:
            return None
        tid, binding = resolve(service, db, task_id)
    value = await io(project, service, tid, actor=await actor(request))
    if value['deleted']:
        return None
    return {**value['task'], 'history': {'source': binding['source'], 'originalId': tid,
        'recordType': 'canonicalCreated' if binding.get('origin') == 'canonical-create' else 'adoptedOriginal',
        'executable': True}}


def _history_error(exc):
    return _err("history_unavailable", str(exc))


async def _successor(request, task, *, limit=100, offset=0):
    from packages.jobs.final_api import actor
    from packages.jobs import final_contracts as fc
    from packages.plugins.lifecycle import io
    from packages.scheduler import retained
    service = getattr(getattr(request.app, 'state', None), 'job_final_service', None)
    if not callable(getattr(service, 'transaction', None)):
        return {}
    try:
        who = await actor(request, 'job', 'read')
        from packages.scheduler.json_handoff import project
        with service.transaction(readonly=True) as db:
            adopted = db.execute("SELECT 1 FROM final_documents WHERE kind='json_scheduler_handoff' AND (id=? OR json_extract(document,'$.jobId')=?)", (task['id'], task['id'])).fetchone()
        if adopted:
            value = await io(project, service, task['id'], actor=who)
            return {'canonicalSuccessor': value}
        return {'canonicalSuccessor': await io(retained.query, service, task, actor=who, limit=limit, offset=offset)}
    except fc.ContractError as exc:
        return {'canonicalSuccessor': {'status': 'blocked', 'code': exc.code,
                                      'historicalExecutionAdopted': False}}


@router.get("/tasks")
async def list_tasks(includeDisabled: bool = True, request: Request = None,
                     limit: int | None = None, offset: int = 0):
    await _history_access(request)
    if (limit is not None and not 1 <= limit <= 500) or offset < 0:
        return _err("invalid_argument", "limit must be between 1 and 500; offset must be nonnegative")
    try:
        tasks = _history(request).tasks(includeDisabled)
        service = getattr(request.app.state, 'job_final_service', None)
        if service is not None:
            from packages.scheduler.json_handoff import project
            from packages.jobs.final_api import actor
            from packages.plugins.lifecycle import io
            who = await actor(request)
            with service.transaction(readonly=True) as db:
                adopted_ids = {row[0] for row in db.execute("SELECT id FROM final_documents WHERE kind='json_scheduler_handoff'")}
            projected = []
            historical_ids = {task['id'] for task in tasks}
            for tid in sorted(adopted_ids - historical_ids):
                created = await _canonical_record(request, tid)
                if created is not None:
                    tasks.append(created)
            for task in tasks:
                if task['id'] in adopted_ids:
                    value = await io(project, service, task['id'], actor=who)
                    if value['deleted'] or not includeDisabled and not value['task']['enabled']:
                        continue
                    task = {**value['task'], 'history': task['history']}
                projected.append(task)
            tasks = projected
        if not await may_read_jobs(request):
            from packages.jobs.action_runtime import is_action_job
            tasks = [t for t in tasks if not is_action_job(t['history']['source'])
                     or t['history']['source'].get('legacySchedulerCompat')]
        return _ok(tasks=[_public(t) for t in tasks[offset:None if limit is None else offset + limit]], total=len(tasks))
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        return _history_error(exc)


@router.post("/tasks")
async def create_task(data: dict, request: Request = None):
    """创建定时任务。Body: {name?, targetSessionId, text, schedule, enabled?, maxRuns?, misfirePolicy?}"""
    from packages.jobs.final_api import actor
    from packages.jobs import final_contracts as fc
    from packages.plugins.lifecycle import io
    from packages.scheduler import canonical_tasks
    from packages.scheduler.json_handoff import project
    service = getattr(request.app.state, 'job_final_service', None)
    if service is None:
        from packages.jobs.final_api import retired_legacy_write
        return await retired_legacy_write(request)
    try:
        who = await actor(request, 'job', 'create')
    except fc.ContractError as exc:
        raise HTTPException(exc.status, {'code': exc.code, 'message': str(exc)}) from exc
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
        "description": data.get("description") if isinstance(data.get("description"), str) else "",
        "target_session_id": target,
        "text": text,
        "enabled": _as_bool(data.get("enabled"), True),
        "paused": False,
        "schedule": spec,
        "max_runs": max_runs,
        "misfire_policy": policy,
    }
    try:
        operation = await io(canonical_tasks.create, service, payload, actor=who,
            request_key=canonical_tasks.request_key(data.get('requestKey')))
        task = (await io(project, service, operation['result']['taskId'], actor=who))['task']
    except fc.ContractError as exc:
        raise HTTPException(exc.status, {'code': exc.code, 'message': str(exc)}) from exc
    except ValueError as exc:
        return _err("invalid_schedule", str(exc))
    _emit_task("scheduler.task.created", task)
    return _ok(task=_public(task), operation=operation)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request = None):
    await _history_access(request)
    try:
        history = _history(request)
        task = history.get(task_id) or await _canonical_record(request, task_id)
        if task is None:
            return _err("not_found", f"task {task_id} not found")
        source = task['history']['source']
        if not source.get('legacySchedulerCompat'):
            await require_job(request, source)
        extra = await _successor(request, task)
        evidence = history.evidence(task)
        if 'task' in extra.get('canonicalSuccessor', {}):
            task = extra['canonicalSuccessor']['task']
            if extra['canonicalSuccessor'].get('deleted'):
                return _err('not_found', 'Original task was deleted')
        return _ok(task=_public(task), **evidence, **extra)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        return _history_error(exc)


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, data: dict, request: Request = None):
    """局部更新：name / text / targetSessionId / schedule / enabled / paused / maxRuns / misfirePolicy。"""
    from packages.jobs.final_api import actor
    from packages.jobs import final_contracts as fc
    from packages.plugins.lifecycle import io
    from packages.scheduler import canonical_tasks
    from packages.scheduler.json_handoff import project
    service = getattr(request.app.state, 'job_final_service', None)
    if service is None:
        from packages.jobs.final_api import retired_legacy_write
        return await retired_legacy_write(request)
    try:
        who = await actor(request, 'job', 'updateConfig')
    except fc.ContractError as exc:
        raise HTTPException(exc.status, {'code': exc.code, 'message': str(exc)}) from exc
    if not isinstance(data, dict):
        return _err("invalid_argument", "request body must be a JSON object")

    patch: dict = {}
    if "name" in data:
        if not isinstance(data["name"], str):
            return _err("invalid_argument", "name must be a string")
        patch["name"] = data["name"]
    if "description" in data:
        if not isinstance(data["description"], str):
            return _err("invalid_argument", "description must be a string")
        patch["description"] = data["description"]
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
        operation = await io(canonical_tasks.update, service, task_id, patch, actor=who,
            request_key=canonical_tasks.request_key(data.get('requestKey')), expected_revision=data.get('expectedRevision'))
        task = (await io(project, service, task_id, actor=who))['task']
    except fc.ContractError as exc:
        raise HTTPException(exc.status, {'code': exc.code, 'message': str(exc)}) from exc
    except ValueError as exc:
        return _err("invalid_schedule", str(exc))
    if task is None:
        return _err("not_found", f"task {task_id} not found")
    _emit_task("scheduler.task.updated", task)
    return _ok(task=_public(task), operation=operation)


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, request: Request = None):
    return await _json_lifecycle(task_id, 'delete', request)


async def _json_lifecycle(task_id, operation, request):
    from packages.jobs.final_api import actor, body, retired_legacy_write
    from packages.jobs import final_contracts as fc
    from packages.plugins.lifecycle import io
    from packages.scheduler.json_handoff import control, project
    try:
        who = await actor(request, 'job', 'execute' if operation == 'run-now' else 'delete' if operation == 'delete' else 'lifecycle')
        service = getattr(request.app.state, 'job_final_service', None)
        if service is None:
            return await retired_legacy_write(request)
        # Original no-body aliases remain valid. CAS/key are additive controls.
        value = await body(request) if request.headers.get('content-length', '0') != '0' else {}
        if set(value) - ({'expectedRevision', 'requestKey', 'waitForDispatch'} if operation == 'run-now' else {'expectedRevision', 'requestKey'}):
            raise fc.ContractError('invalid_schema', 'Only original revision/key controls are accepted')
        if 'waitForDispatch' in value and type(value['waitForDispatch']) is not bool:
            raise fc.ContractError('invalid_schema', 'waitForDispatch must be boolean')
        result = await io(control, service, task_id, operation, actor=who,
                          request_key=value.get('requestKey'), expected_revision=value.get('expectedRevision'))
        if operation == 'run-now' and value.get('waitForDispatch', True) and not result.get('replayed') and result.get('runId'):
            # Drive only this newly admitted original Run through the existing
            # engine. A replay is query-only, including an unknown prior call.
            with service.transaction(readonly=True) as db:
                owned, _ = service._find_run(db, result['runId'])
            async with service._dispatch_lock:
                await service._drive_run(owned['jobId'], result['runId'])
        current = await io(project, service, task_id, actor=who,
                           capability='execute' if operation == 'run-now' else 'delete' if operation == 'delete' else 'lifecycle')
        task = _public(current['task'])
        payload = {'deleted': True} if operation == 'delete' else {}
        if operation == 'run-now':
            rid = result['result'].get('runId')
            run = next((row for row in current['runs'] if row['runId'] == rid), None)
            status = 'accepted' if run and run['state'] == 'queued' else run['state'] if run else 'skipped'
            payload['run'] = {'ok': True, 'taskId': task['id'], 'dispatchKey': result['result'].get('dispatchKey'),
                'status': status, 'run': {'run_id': rid, 'task_id': task['id'],
                'fire_at': run['createdAt'] if run else None, 'actual_at': run.get('startedAt') if run else None,
                'dispatch_key': result['result'].get('dispatchKey'), 'status': status,
                'session_id': task.get('targetSessionId'), 'worker_id': None, 'error': None,
                'result': {'operationId': result['operationId'], 'status': result['status']}}}
            if value.get('waitForDispatch', True):
                from packages.scheduler.json_handoff import manual_result
                payload['run'] = await io(manual_result, service, task_id, result, actor=who)
        _emit_task('scheduler.task.deleted' if operation == 'delete' else
                   'scheduler.task.fired' if operation == 'run-now' else 'scheduler.task.updated', task,
                   **({'run': payload['run']} if operation == 'run-now' else {}))
        return _ok(task=task, operation=result, **payload)
    except fc.ContractError as exc:
        raise HTTPException(exc.status, {'code': exc.code}) from exc


@router.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str, request: Request = None):
    return await _json_lifecycle(task_id, 'pause', request)


@router.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str, request: Request = None):
    return await _json_lifecycle(task_id, 'resume', request)


@router.post("/tasks/{task_id}/run-now")
async def run_task_now(task_id: str, request: Request = None):
    return await _json_lifecycle(task_id, 'run-now', request)


@router.get("/tasks/{task_id}/runs")
async def task_runs(task_id: str, limit: int = 100, request: Request = None, offset: int = 0):
    await _history_access(request)
    if not 1 <= limit <= 500 or offset < 0:
        return _err("invalid_argument", "limit must be between 1 and 500; offset must be nonnegative")
    try:
        history = _history(request)
        task = history.get(task_id) or await _canonical_record(request, task_id)
        if task is None:
            return _err("not_found", f"task {task_id} not found")
        source = task['history']['source']
        if not source.get('legacySchedulerCompat'):
            await require_job(request, source)
        runs = history.runs(task)
        extra = await _successor(request, task, limit=limit, offset=offset)
        if 'task' in extra.get('canonicalSuccessor', {}):
            from packages.scheduler.json_handoff import legacy_runs
            from packages.jobs.final_api import actor
            from packages.plugins.lifecycle import io
            current = await io(legacy_runs, request.app.state.job_final_service, task['id'], actor=await actor(request, 'job', 'read'))
            known = {row.get('runId', row.get('run_id')) for row in current}
            runs = current + [row for row in runs if row.get('runId', row.get('run_id')) not in known]
        return _ok(runs=[_public_run(r) for r in runs[offset:offset + limit]], total=len(runs), **extra)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        return _history_error(exc)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request):
    """Request cancellation of an explicitly reviewed successor Run only."""
    from packages.jobs.final_api import actor, body
    from packages.jobs import final_contracts as fc
    from packages.scheduler import retained
    try:
        who = await actor(request, 'job', 'execute')
        service = getattr(request.app.state, 'job_final_service', None)
        if service is None:
            raise HTTPException(503, 'Canonical service unavailable')
        task = _history(request).get(task_id) or await _canonical_record(request, task_id)
        if task is None:
            return _err('not_found', 'Original Scheduler task not found')
        return await retained.cancel(service, task, await body(request), actor=who)
    except fc.ContractError as exc:
        raise HTTPException(exc.status, {'code': exc.code}) from exc
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        raise HTTPException(503, 'Original Scheduler state unavailable; query original request') from exc


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
        items = _preview_next(history, task_id, count)
        items = [item for item in items if item['taskId'] in allowed]
    except ValueError as exc:
        return _err("invalid_schedule", str(exc))
    return _ok(next=items or [])


def _preview_next(history, task_id, count, *, at=None):
    """Read-only historical projection using the canonical Creator calculator.

    Legacy once/interval values are host-local instants; cron has its explicit
    wall-clock zone. No Creator, cursor or publication is created or advanced.
    """
    provider = TimeProvider()
    after = at or datetime.now().replace(microsecond=0)
    boundary = timestamp(after.astimezone(timezone.utc))
    tasks = [history.get(task_id)] if task_id else history.tasks(include_disabled=False)
    result = []
    for task in tasks:
        if not task or not task.get('enabled'):
            continue
        spec = task.get('schedule') or {}
        kind = spec.get('kind')
        rule = {'ruleId': 'preview', 'type': kind}
        local_wall = kind == 'cron' and not spec.get('timezone')
        def legacy_instant(value):
            point = scheduler_cron.parse_datetime(value)
            if point is None:
                raise ValueError('Historical schedule requires a valid instant')
            return timestamp(point.astimezone(timezone.utc))
        if kind == 'once':
            rule['at'] = legacy_instant(spec.get('at'))
        elif kind == 'interval':
            rule.update(seconds=str(spec.get('interval_sec', spec.get('intervalSec'))),
                        anchor=legacy_instant(spec.get('anchor') or task.get('createdAt', task.get('created_at'))),
                        advanceMode='grid')
        elif kind == 'cron':
            rule.update(expression=spec.get('cron'),
                        timezone=spec.get('timezone') or 'UTC',
                        dstPolicy='gap-shift-fold-first')
        provider.validate_config({'rules': [rule], 'misfirePolicy': 'skip',
                                  'graceSeconds': '0', 'pausePolicy': 'advance'})
        # A legacy cron without a zone uses naive local wall time. Evaluate
        # that wall calendar in UTC, then return it unchanged as naive time.
        cursor = timestamp(after.replace(tzinfo=timezone.utc)) if local_wall else boundary
        for _ in range(count):
            point = provider.next_after(rule, cursor)
            if point is None:
                break
            cursor = point['at']
            fire = datetime.fromisoformat(cursor.replace('Z', '+00:00'))
            if not local_wall:
                fire = fire.astimezone()
            fire = fire.replace(tzinfo=None, microsecond=0)
            result.append({'taskId': task['id'], 'fireAt': fire.isoformat()})
    result.sort(key=lambda item: (item['fireAt'], item['taskId']))
    return result[:count]


@router.get("/status")
async def engine_status(request: Request = None):
    """Health of the canonical runtime bound to this application."""
    service = getattr(getattr(getattr(request, 'app', None), 'state', None), 'job_final_service', None)
    if service is None:
        return _err('engine_not_running', 'Canonical runtime is not bound')
    try:
        task = service.task
        runtime = service.creator_runtime
        return _ok(status={'running': not service.closed and task is not None and not task.done(),
                           'tickSec': 0.25, 'dueScanned': runtime.due_scanned,
                           'lastTickAt': runtime.last_tick_at})
    except Exception as exc:
        return _err('engine_not_running', str(exc))


# R5: retire the old scheduler mutations at the route table.  Handler bodies
# remain for R6 source removal; task reads and run history stay available.
RETIRED_ROUTES = {
    ("POST", "/event-sources"),
    ("PUT", "/event-sources/{sid}"),
    ("DELETE", "/event-sources/{sid}"),
}

RETAINED_ROUTES = {
    ("POST", "/tasks"),
    ("PATCH", "/tasks/{task_id}"),
    ("DELETE", "/tasks/{task_id}"),
    ("POST", "/tasks/{task_id}/pause"),
    ("POST", "/tasks/{task_id}/resume"),
    ("POST", "/tasks/{task_id}/run-now"),
    ("GET", "/tasks"),
    ("GET", "/tasks/{task_id}"),
    ("GET", "/tasks/{task_id}/runs"),
    ("POST", "/tasks/{task_id}/cancel"),
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
