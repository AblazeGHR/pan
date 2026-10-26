"""Legacy scheduler configuration, status and nonexecuting preview facade.

R6-A removed the unmounted legacy start/tick/run-now host. Canonical time
providers own scheduling; historical tasks and runs remain in the store.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from packages.core import background_jobs
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


# ── 生命周期（循环已统一，这里只做注册 + 迁移）──


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


# ── 手动触发 / 预览 ──


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


# Removed R6-A leaves are absent; stale Python callers receive an explicit refusal.
_RETIRED_ENTRYPOINTS = frozenset({
    '_emit',
    '_emit_strict',
    '_consume',
    '_ensure_registered',
    'start_loop',
    'tick_once',
    'run_now',
})


def __getattr__(name):
    from packages.jobs.retired import missing
    return missing(name, _RETIRED_ENTRYPOINTS)
