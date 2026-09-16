"""调度引擎 —— 到点把任务文本派发给目标 session 的 worker。

生命周期范式照抄 ``packages/core/background_jobs.py:524-548``
（``asyncio.create_task`` + ``Event`` 停止 + 单轮异常兜底）。

派发原语（唯一）::

    await worker.assign(session_id, text, source="automation", task_id=dispatch_key)

``packages/core/worker.py:5185``；``"automation"`` 已在 SOURCE_TYPES 白名单，
``task_id`` 三级去重保证 Pan 重启重放不双跑。

三条铁律：
1. **落盘先于派发** —— 先推进 next_fire_at、写 last_fire_at/last_status，
   再 create_task 扇出 assign。崩溃点只有两个，都被 misfire 语义覆盖。
2. **命名纪律** —— 主键 ``task_id``，幂等键 ``dispatch_key``，绝不混用。
3. **绝不 import ``packages.web.server``** （循环依赖），事件一律走 ``on_event``。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from datetime import datetime

from packages.core import worker as _worker
from packages.scheduler import cron
from packages.scheduler import store

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

_EVENT_FIRED = "scheduler.task.fired"

_loop_task: asyncio.Task | None = None
_stop = asyncio.Event()
_on_event = None
_pending: set[asyncio.Task] = set()
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


def _now_dt() -> datetime:
    """本地朴素当前时刻（秒精度）——判定一律用墙钟，不用 monotonic。"""
    return datetime.now().replace(microsecond=0)


def _tick_sec(cfg: dict | None = None) -> float:
    cfg = cfg or scheduler_config()
    try:
        return max(0.2, float(cfg.get("tick_sec") or 1))
    except (TypeError, ValueError):
        return 1.0


def _grace_sec(cfg: dict | None = None) -> float:
    cfg = cfg or scheduler_config()
    try:
        return max(0.0, float(cfg.get("misfire_grace_sec") or 0))
    except (TypeError, ValueError):
        return 0.0


def _max_concurrent(cfg: dict | None = None) -> int:
    cfg = cfg or scheduler_config()
    try:
        return max(1, int(cfg.get("max_concurrent_dispatch") or 1))
    except (TypeError, ValueError):
        return 1


# ── 生命周期 ──


async def start_loop(on_event=None) -> None:
    """启动调度循环；幂等，已在跑则复用。

    拿不到 leader 锁的实例只服务读请求，不起循环。
    """
    global _loop_task, _stop, _on_event
    if on_event is not None:
        _on_event = on_event
    if _loop_task and not _loop_task.done():
        return
    if not scheduler_config().get("enabled", True):
        _log.info("scheduler: 配置关闭（scheduler.enabled=false），不起循环")
        return
    if not store.claim_leader():
        _log.info("scheduler: 未取得 leader 锁，本实例只服务读请求")
        return
    _recover()
    _stop = asyncio.Event()
    _loop_task = asyncio.create_task(_loop(), name="scheduler-loop")


async def stop_loop() -> None:
    """停止循环并等待在途派发结束。幂等。"""
    global _loop_task
    task = _loop_task
    if task and not task.done():
        _stop.set()
        try:
            await task
        except Exception:  # 单轮异常已在 _loop 内兜底，这里只防取消
            pass
    _loop_task = None
    await drain()


def status() -> dict:
    """引擎健康快照。"""
    cfg = scheduler_config()
    try:
        tick_sec = int(cfg.get("tick_sec") or 1)
    except (TypeError, ValueError):
        tick_sec = 1
    return {
        "running": bool(_loop_task and not _loop_task.done()),
        "tickSec": tick_sec,
        "dueScanned": int(_stats.get("due_scanned") or 0),
        "lastTickAt": _stats.get("last_tick_at"),
    }


async def _loop() -> None:
    cfg = scheduler_config()
    while not _stop.is_set():
        try:
            await tick_once()
        except Exception:
            _log.exception("scheduler: tick 异常，下一轮继续")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=_tick_sec(cfg))
        except asyncio.TimeoutError:
            pass


# ── 扫描 ──


async def tick_once() -> int:
    """扫一轮到期任务：落盘 → 扇出派发。返回本轮处理的到期条数。

    单轮内只做「扫描 + 落盘」，派发交给后台 task + 信号量，绝不阻塞 tick。
    """
    global _stats
    cfg = scheduler_config()
    grace = _grace_sec(cfg)
    now = _now_dt()
    semaphore = asyncio.Semaphore(_max_concurrent(cfg))

    handled = 0
    for task in store.list_tasks(include_disabled=False):
        if task.get("paused"):
            # 暂停：跳过触发，但 next_fire_at 仍按锚点推进 —— 恢复后不爆发补跑。
            _advance_paused(task, now)
            continue
        fire_at = cron.parse_datetime(task.get("next_fire_at"))
        if fire_at is None or fire_at > now:
            continue
        handled += 1
        _handle_due(task, fire_at, now, grace, semaphore)
    _stats["due_scanned"] = int(_stats.get("due_scanned") or 0) + handled
    _stats["last_tick_at"] = store.iso(now)
    return handled


def _handle_due(task: dict, fire_at: datetime, now: datetime,
                grace: float, semaphore: asyncio.Semaphore) -> None:
    """处理一条到期任务（同步部分：落盘 + 扇出）。"""
    task_id = task.get("id")
    late = (now - fire_at).total_seconds()
    kind = (task.get("schedule") or {}).get("kind")
    dispatch_key = f"{task_id}:{int(fire_at.timestamp())}"

    if late > grace:
        if kind == "once":
            # 一次性超宽限：记 expired 并自动 disable，绝不追补。
            _patch(task_id, {
                "enabled": False,
                "next_fire_at": None,
                "last_fire_at": store.iso(fire_at),
                "last_status": "expired",
                "last_error": f"misfire {int(late)}s 超过宽限 {int(grace)}s，已过期",
            })
            _record(task, fire_at, dispatch_key, "expired",
                    error=f"misfire {int(late)}s > grace {int(grace)}s")
            _emit({"type": _EVENT_FIRED, "taskId": task_id, "fireAt": store.iso(fire_at),
                   "dispatchKey": dispatch_key, "status": "expired",
                   "error": "misfire expired"})
            return
        if str(task.get("misfire_policy") or "fire_now") == "skip":
            _skip(task, fire_at, dispatch_key, late, grace)
            return
        # fire_now：宽限外也补派一次（休眠唤醒的「一次结算」）

    # ---- 落盘先于派发 ----
    next_fire = _next_after_fire(task, fire_at, now)
    patch: dict = {
        "next_fire_at": store.iso(next_fire),
        "last_fire_at": store.iso(fire_at),
        "last_status": "dispatched",
        "last_error": None,
        "run_count": int(task.get("run_count") or 0) + 1,
    }
    max_runs = task.get("max_runs")
    if isinstance(max_runs, int) and patch["run_count"] >= max_runs:
        patch["enabled"] = False
        patch["next_fire_at"] = None
    if kind == "once":
        # 一次性任务触发即完成：next_fire_at 置空并自动 disable，不再参与扫描。
        patch["enabled"] = False
        patch["next_fire_at"] = None
    saved = _patch(task_id, patch)

    _emit({"type": _EVENT_FIRED, "taskId": task_id, "fireAt": store.iso(fire_at),
           "dispatchKey": dispatch_key, "status": "dispatched", "error": None})
    _spawn(_dispatch(saved or task, fire_at, dispatch_key, semaphore))

def _skip(task: dict, fire_at: datetime, dispatch_key: str,
          late: float, grace: float) -> None:
    """misfire 且策略为 skip：记 skipped 并按锚点推进到下一次。"""
    task_id = task.get("id")
    _patch(task_id, {
        "next_fire_at": store.iso(_next_after_fire(task, fire_at, _now_dt())),
        "last_fire_at": store.iso(fire_at),
        "last_status": "skipped",
        "last_error": f"misfire {int(late)}s 超过宽限 {int(grace)}s，按策略跳过",
    })
    _record(task, fire_at, dispatch_key, "skipped",
            error=f"misfire {int(late)}s > grace {int(grace)}s")
    _emit({"type": _EVENT_FIRED, "taskId": task_id, "fireAt": store.iso(fire_at),
           "dispatchKey": dispatch_key, "status": "skipped",
           "error": "misfire skipped"})


def _advance_paused(task: dict, now: datetime) -> None:
    """暂停任务到期：只把 next_fire_at 推到 now 之后，不派发、不写历史。"""
    fire_at = cron.parse_datetime(task.get("next_fire_at"))
    if fire_at is None or fire_at > now:
        return
    spec = store.effective_spec(task)
    tz = (task.get("schedule") or {}).get("timezone")
    try:
        point = cron.next_fire_after(spec, now, tz_name=tz)
    except ValueError:
        return
    _patch(task.get("id"), {"next_fire_at": store.iso(point)})


def _next_after_fire(task: dict, fire_at: datetime,
                     now: datetime | None = None) -> datetime | None:
    """从 ``max(fire_at, now)`` 推进下一次触发（interval 用锚点，绝不 now+interval）。

    基准取 ``max`` 是 misfire 场景的关键：只从 fire_at 推进会算出一个仍在过去
    的点，下一轮又被判成到期 —— 那就变成「循环补跑」了。锚点网格不受影响。
    """
    if (task.get("schedule") or {}).get("kind") == "once":
        return None
    spec = store.effective_spec(task)
    tz = (task.get("schedule") or {}).get("timezone")
    base = fire_at if now is None else max(fire_at, now)
    try:
        return cron.next_fire_after(spec, base, tz_name=tz)
    except ValueError:
        return None


def _patch(task_id: str, patch: dict) -> dict | None:
    try:
        return store.update_task(task_id, patch)
    except Exception:
        _log.exception("scheduler: 任务 %s 状态落盘失败", task_id)
        return None


# ── 派发 ──


async def _dispatch(task: dict, fire_at: datetime, dispatch_key: str,
                    semaphore: asyncio.Semaphore,
                    emit_on_success: bool = False) -> dict:
    """真正派发一次；结果写 runs.jsonl 与任务状态。"""
    async with semaphore:
        try:
            result = await _worker.assign(
                task.get("target_session_id"),
                task.get("text") or "",
                source="automation",
                task_id=dispatch_key,
            )
        except Exception as exc:  # 派发异常不得掀翻 tick / 调用方
            result = {"status": "error", "result": str(exc)}
    if not isinstance(result, dict):
        result = {"status": "error", "result": f"unexpected assign result: {result!r}"}

    status = "error" if str(result.get("status")) == "error" else "dispatched"
    error = None if status == "dispatched" else str(result.get("result") or "派发失败")
    record = _record(task, fire_at, dispatch_key, status, error=error,
                     worker_id=result.get("workerId"))
    if status == "error":
        _patch(task.get("id"), {"last_status": "error", "last_error": error})
    # tick 路径已在「落盘后、派发前」抛过一次 fired（dispatched），这里只在
    # 出错或 run_now（没有前置事件）时补抛，保证一次触发至多两条且语义不重复。
    if emit_on_success or status == "error":
        _emit({"type": _EVENT_FIRED, "taskId": task.get("id"),
               "fireAt": store.iso(fire_at), "dispatchKey": dispatch_key,
               "status": status, "error": error, "terminal": True})
    return record


def _record(task: dict, fire_at: datetime, dispatch_key: str, status: str,
            error: str | None = None, worker_id=None) -> dict:
    record = {
        "run_id": uuid.uuid4().hex[:12],
        "task_id": task.get("id"),
        "fire_at": store.iso(fire_at),
        "actual_at": store.iso(_now_dt()),
        "dispatch_key": dispatch_key,
        "status": status,
        "session_id": task.get("target_session_id"),
        "worker_id": worker_id,
        "error": error,
    }
    try:
        store.append_run(record)
    except Exception:
        _log.exception("scheduler: 写执行历史失败")
    return record


# ── 在途派发 ──


def _spawn(coro) -> asyncio.Task | None:
    """后台扇出派发；保留强引用防 GC。"""
    global _pending
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return None
    task = loop.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return task


async def drain() -> None:
    """等待全部在途派发结束（测试与关闭流程用）。"""
    global _pending
    pending = [t for t in _pending if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    _pending = set()


# ── 事件外抛 ──


def _emit(event: dict) -> None:
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


# ── 启动恢复 ──


def _recover() -> None:
    """Pan 重启后：未知的终态记 unknown（不补派），并按需重算 next_fire_at。"""
    now = _now_dt()
    for task in store.list_tasks(include_disabled=False):
        patch: dict = {}
        # 1) 上次已派发但查不到终态 → unknown，绝不自动补派（宁漏勿重）
        if task.get("last_status") == "dispatched":
            last_fire = cron.parse_datetime(task.get("last_fire_at"))
            if last_fire is not None:
                dispatch_key = f"{task.get('id')}:{int(last_fire.timestamp())}"
                if not _terminal_seen(task.get("target_session_id"), dispatch_key):
                    patch["last_status"] = "unknown"
                    patch["last_error"] = "重启后未确认终态，不自动补派"
        # 2) next_fire_at 缺失或已过期 → 从锚点重算（不用 now + interval）
        next_fire = cron.parse_datetime(task.get("next_fire_at"))
        if next_fire is None or next_fire <= now:
            spec = store.effective_spec(task)
            tz = (task.get("schedule") or {}).get("timezone")
            try:
                recomputed = cron.next_fire_after(spec, now, tz_name=tz)
            except ValueError:
                recomputed = None
            patch["next_fire_at"] = store.iso(recomputed)
            if recomputed is None and (task.get("schedule") or {}).get("kind") == "once":
                patch["enabled"] = False
                if not patch.get("last_status"):
                    patch["last_status"] = "expired"
        if patch:
            _patch(task.get("id"), patch)


def _terminal_seen(session_id: str | None, dispatch_key: str) -> bool:
    """该 dispatch_key 是否已在 session 的持久队列/历史中留下痕迹。"""
    if not session_id or not dispatch_key:
        return False
    try:
        from packages.core import session as _sessions

        target = _sessions.get(session_id)
        if target is None:
            return False
        seen = getattr(_worker, "_durable_task_id_seen", None)
        if seen is None:
            return False
        return bool(seen(target, dispatch_key))
    except Exception:
        return False


# ── 对外动作 ──


async def run_now(task_id: str) -> dict:
    """手动立即触发一次；**不推进** next_fire_at。"""
    task = store.get_task(task_id)
    if task is None:
        return {"ok": False, "error": {"code": "not_found",
                                       "message": f"任务不存在：{task_id}"}}
    now = _now_dt()
    dispatch_key = f"{task_id}:{int(now.timestamp())}"
    record = await _dispatch(task, now, dispatch_key, asyncio.Semaphore(1),
                             emit_on_success=True)

    patch: dict = {
        "last_fire_at": store.iso(now),
        "last_status": record.get("status"),
        "last_error": record.get("error"),
        "run_count": int(task.get("run_count") or 0) + 1,
    }
    max_runs = task.get("max_runs")
    if isinstance(max_runs, int) and patch["run_count"] >= max_runs:
        patch["enabled"] = False
        patch["next_fire_at"] = None
    _patch(task_id, patch)
    return {
        "ok": True,
        "taskId": task_id,
        "dispatchKey": dispatch_key,
        "status": record.get("status"),
        "run": record,
    }


async def preview_next(task_id: str | None = None, count: int = 5) -> list[dict]:
    """预览接下来的触发点，形如 ``[{"taskId": ..., "fireAt": ...}]``。"""
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 5
    count = max(1, min(count, cron.MAX_PREVIEW))
    now = _now_dt()

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
