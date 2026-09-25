"""调度引擎 shim —— 独立 tick 循环已并入统一 job 内核（P1 统一）。

``packages.core.background_jobs`` 的 recovery loop 现在每轮调用
``run_due_scheduled_tasks``（认领 → 派发 → stale requeue），本模块只保留：

- :func:`start_loop` / :func:`stop_loop`：注册数据根/配置/事件回调 + 触发
  迁移；**不再自起循环，leader 锁退役**（per-job 认领天然多实例安全）；
- :func:`tick_once`：统一一轮的薄封装（测试 / 手动驱动用）；
- :func:`run_now` / :func:`preview_next` / :func:`status`：契约不变的引擎动作。

事件负载形状与 PR 完全一致（``scheduler.task.fired`` 等），前端面板无感。
设计文档：``docs/design/job-unification/DESIGN_DISPATCH_CLAIM_FUSION.md``。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from datetime import datetime

from packages.core import background_jobs
from packages.core import worker as _worker
from packages.jobs import cron
from . import store

_log = logging.getLogger(__name__)

#: config.json 里 ``scheduler`` 段的默认值（ts-api 会补进 DEFAULT_CONFIG，
#: 这里保留一份以便配置缺失时也能跑）
DEFAULT_SCHEDULER_CONFIG: dict = {
    "enabled": True,
    "tick_sec": 1,
    "misfire_grace_sec": 300,
    "max_concurrent_dispatch": 5,
    "default_timezone": "Asia/Shanghai",
}

# ── 兼容模块状态（既有测试 patch 这些名字）──

_loop_task: asyncio.Task | None = None
_stop = asyncio.Event()
_pending: set = set()
_on_event = None
_stats: dict = {"due_scanned": 0, "last_tick_at": None}


# ── 配置 ──


def scheduler_config() -> dict:
    """读 ``config.scheduler``，缺失用默认值补齐。读失败不抛。"""
    cfg = dict(DEFAULT_SCHEDULER_CONFIG)
    try:
        from packages.core import config as _config

        raw = _config.load_config().get("scheduler")
    except Exception:
        raw = None
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in cfg and value is not None:
                cfg[key] = value
    return cfg


def _emit(event: dict) -> None:
    """统一循环的事件回调入口 → start_loop 注入的 ``_on_event``。"""
    callback = _on_event
    if callback is None:
        return
    try:
        result = callback(event)
    except Exception:
        _log.exception("scheduler: on_event 回调异常")
        return
    if inspect.isawaitable(result):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(_consume(result))
        else:
            close = getattr(result, "close", None)
            if callable(close):
                close()


async def _consume(awaitable) -> None:
    try:
        await awaitable
    except Exception:
        _log.exception("scheduler: on_event 异步回调异常")


def _ensure_registered() -> None:
    """把本插件的数据根 / 配置 / 事件回调注册进统一内核（幂等）。

    resolver 用 lambda 延迟求值，测试期 monkeypatch ``store.DEFAULT_ROOT`` /
    ``scheduler_config`` 仍然生效。
    """
    background_jobs.register_scheduled_tasks(
        root_resolver=lambda: str(store.data_root()),
        config_resolver=lambda: scheduler_config(),
        on_event=_emit,
    )


# ── 生命周期（循环已统一，这里只做注册 + 迁移）──


async def start_loop(on_event=None) -> None:
    """注册进统一循环；幂等。leader 锁已退役。"""
    global _on_event
    if on_event is not None:
        _on_event = on_event
    _ensure_registered()
    try:
        migrated = store.migrate_legacy_tasks()
        if migrated:
            _log.info("scheduler: 迁移了 %d 个旧任务到统一注册表", migrated)
    except Exception:
        _log.exception("scheduler: 旧任务迁移失败（下次启动重试）")
    if not scheduler_config().get("enabled", True):
        _log.info("scheduler: 配置关闭（scheduler.enabled=false），统一循环将跳过扫描")


async def stop_loop() -> None:
    """兼容桩：统一循环随 Pan 服务生命周期，无独立循环可停。"""
    return


async def drain() -> None:
    """兼容桩：统一循环的派发在 pass 内联 await，无在途集合。"""
    return


def status() -> dict:
    """引擎健康快照（契约不变；running = 统一 recovery loop 存活）。"""
    stats = background_jobs.scheduled_task_stats()
    return {
        "running": background_jobs.is_recovery_running(),
        "tickSec": 1,
        "dueScanned": int(stats.get("dueScanned") or 0),
        "lastTickAt": stats.get("lastTickAt"),
    }


async def tick_once() -> int:
    """扫一轮到期任务（统一循环同一入口；测试/手动驱动用）。"""
    _ensure_registered()
    handled = await background_jobs.run_due_scheduled_tasks()
    _stats["due_scanned"] = int(_stats.get("due_scanned") or 0) + handled
    _stats["last_tick_at"] = background_jobs.scheduled_task_stats().get("lastTickAt")
    return handled


# ── 手动触发 / 预览 ──


async def run_now(task_id: str) -> dict:
    """手动立即触发一次；**不推进** next_fire_at（契约不变）。"""
    task = store.get_task(task_id)
    if task is None:
        return {"ok": False, "error": {"code": "not_found",
                                       "message": f"任务不存在：{task_id}"}}
    job = store._job_for_task(task_id)
    now = datetime.now().replace(microsecond=0)
    dispatch_key = f"{task_id}:{int(now.timestamp())}"
    try:
        result = await _worker.assign(task.get("target_session_id"),
                                      task.get("text") or "",
                                      source="automation",
                                      task_id=dispatch_key)
    except Exception as exc:
        result = {"status": "error", "result": str(exc)}
    if not isinstance(result, dict):
        result = {"status": "error", "result": f"unexpected assign result: {result!r}"}
    ok = str(result.get("status")) != "error"
    record_status = "dispatched" if ok else "error"
    error = None if ok else str(result.get("result") or "派发失败")
    record = {
        "run_id": uuid.uuid4().hex[:12],
        "task_id": task_id,
        "fire_at": store.iso(now),
        "actual_at": store.iso(datetime.now().replace(microsecond=0)),
        "dispatch_key": dispatch_key,
        "status": record_status,
        "session_id": task.get("target_session_id"),
        "worker_id": result.get("workerId"),
        "error": error,
    }
    try:
        store.append_run(record)
    except Exception:
        pass

    run_count = int(task.get("run_count") or 0) + 1
    patch: dict = {
        "last_fire_at": store.iso(now),
        "last_status": record_status,
        "last_error": error,
        "run_count": run_count,
    }
    max_runs = task.get("max_runs")
    if isinstance(max_runs, int) and run_count >= max_runs:
        patch["enabled"] = False
        patch["next_fire_at"] = None
    updated = store.update_task(task_id, patch)

    _emit({"type": "scheduler.task.fired", "taskId": task_id,
           "fireAt": store.iso(now), "dispatchKey": dispatch_key,
           "status": record_status, "error": error, "terminal": True})
    return {
        "ok": True,
        "taskId": task_id,
        "dispatchKey": dispatch_key,
        "status": record_status,
        "run": record,
    }


async def preview_next(task_id: str | None = None, count: int = 5) -> list[dict]:
    """预览接下来的触发点，形如 ``[{"taskId": ..., "fireAt": ...}]``（契约不变）。"""
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 5
    count = max(1, min(count, cron.MAX_PREVIEW))
    now = datetime.now().replace(microsecond=0)

    if task_id:
        task = store.get_task(task_id)
        tasks = [task] if task else []
    else:
        tasks = store.list_tasks(include_disabled=False)

    preview: list[dict] = []
    for task in tasks:
        if not task or not task.get("enabled"):
            continue
        spec = store.effective_spec(task)
        tz = (task.get("schedule") or {}).get("timezone")
        for point in cron.next_n(spec, now, count, tz_name=tz):
            preview.append({"taskId": task.get("id"), "fireAt": store.iso(point)})
    preview.sort(key=lambda item: (str(item["fireAt"]), str(item["taskId"])))
    return preview[:count]
