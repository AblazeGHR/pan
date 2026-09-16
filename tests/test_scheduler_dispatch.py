"""定时任务「到点派发」链路测试。

覆盖 PLAN §3.3 的两条派发路径：
1. ``engine.run_now(task_id)`` —— 手动立即触发；
2. tick 扫描到期任务 —— 起停调度循环，等待一次真实派发。

断言点：确实调用了 ``worker.assign(..., source="automation", task_id=<幂等键>)``，
并且结果写进了 ``runs.jsonl`` 执行历史。

隔离：``store.DEFAULT_ROOT`` 重定向到 tmp_path，``worker.assign`` 被替换成记录调用
的假实现，绝不真的 spawn worker，也绝不污染真实 ``data/``。
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from packages.core import worker as core_worker
from packages.scheduler import engine as scheduler_engine
from packages.scheduler import store as scheduler_store


@pytest.fixture
def dispatch_env(tmp_path, monkeypatch):
    root = tmp_path / "scheduler"
    monkeypatch.setattr(scheduler_store, "DEFAULT_ROOT", root)
    monkeypatch.setenv("PAN_SCHEDULER_DIR", str(root))
    # 单实例选主：测试内固定拿到「leader」，避免本机其它 Pan 实例影响结果
    monkeypatch.setattr(scheduler_store, "claim_leader", lambda: True)

    calls: list[dict] = []

    async def fake_assign(*args, **kwargs):
        calls.append({
            "session_id": args[0] if len(args) > 0 else kwargs.get("session_id"),
            "text": args[1] if len(args) > 1 else kwargs.get("text"),
            "source": args[2] if len(args) > 2 else kwargs.get("source"),
            "task_id": args[3] if len(args) > 3 else kwargs.get("task_id"),
        })
        return {"status": "queued", "taskId": kwargs.get("task_id")}

    monkeypatch.setattr(core_worker, "assign", fake_assign)
    return calls


def _create_once_task(**overrides):
    payload = {
        "name": "到点派发",
        "target_session_id": "ses_target",
        "text": "把日报跑出来",
        "enabled": True,
        "paused": False,
        "schedule": {
            "kind": "once",
            "at": (datetime.now() - timedelta(seconds=2)).isoformat(timespec="seconds"),
        },
        "max_runs": None,
        "misfire_policy": "fire_now",
    }
    payload.update(overrides)
    return scheduler_store.create_task(payload)


def test_run_now_dispatches_and_records_history(dispatch_env):
    task = _create_once_task()
    result = asyncio.run(scheduler_engine.run_now(task["id"]))
    assert not (isinstance(result, dict) and result.get("error")), result

    assert len(dispatch_env) == 1
    call = dispatch_env[0]
    assert call["session_id"] == "ses_target"
    assert call["text"] == "把日报跑出来"
    assert call["source"] == "automation"
    # 幂等键 = f"{task_id}:{int(fire_at.timestamp())}"（PLAN §2.2 命名纪律）
    assert str(call["task_id"]).startswith(f"{task['id']}:")

    runs = scheduler_store.list_runs(task_id=task["id"])
    assert runs, "派发后必须写入执行历史"
    assert runs[-1]["task_id"] == task["id"]
    assert runs[-1]["status"] in {"dispatched", "queued", "ok"}


def test_run_now_reports_unknown_task(dispatch_env):
    result = asyncio.run(scheduler_engine.run_now("sch_missing"))
    assert result is None or result.get("error"), result
    assert dispatch_env == []


def test_due_task_is_dispatched_by_tick_loop(dispatch_env):
    # 锚点放在过去 → create 时算出的 next_fire_at 立即到期（落在宽限窗口内）
    anchor = datetime.now() - timedelta(seconds=3)
    task = scheduler_store.create_task({
        "name": "每 1 秒一次",
        "target_session_id": "ses_target",
        "text": "tick 派发",
        "enabled": True,
        "paused": False,
        "schedule": {"kind": "interval", "intervalSec": 1,
                     "interval_sec": 1,
                     "anchor": anchor.isoformat(timespec="seconds")},
        "max_runs": 1,
        "misfire_policy": "fire_now",
    })

    async def scenario():
        await scheduler_engine.start_loop()
        try:
            for _ in range(60):  # 最多等 ~15s（tick_sec 默认 1s）
                if dispatch_env:
                    break
                await asyncio.sleep(0.25)
        finally:
            await scheduler_engine.stop_loop()

    asyncio.run(asyncio.wait_for(scenario(), timeout=60))

    assert dispatch_env, "到期任务必须在 tick 内被派发"
    call = dispatch_env[0]
    assert call["session_id"] == "ses_target"
    assert call["text"] == "tick 派发"
    assert call["source"] == "automation"
    assert str(call["task_id"]).startswith(f"{task['id']}:")

    runs = scheduler_store.list_runs(task_id=task["id"])
    assert runs, "派发后必须写入执行历史"


def test_paused_task_is_not_dispatched_by_tick_loop(dispatch_env):
    anchor = datetime.now() - timedelta(seconds=3)
    scheduler_store.create_task({
        "name": "暂停中的任务",
        "target_session_id": "ses_target",
        "text": "不该被派发",
        "enabled": True,
        "paused": True,
        "schedule": {"kind": "interval", "intervalSec": 1,
                     "interval_sec": 1,
                     "anchor": anchor.isoformat(timespec="seconds")},
        "max_runs": None,
        "misfire_policy": "fire_now",
    })

    async def scenario():
        await scheduler_engine.start_loop()
        try:
            await asyncio.sleep(3)
        finally:
            await scheduler_engine.stop_loop()

    asyncio.run(asyncio.wait_for(scenario(), timeout=60))
    assert dispatch_env == [], "paused 任务必须跳过触发"
