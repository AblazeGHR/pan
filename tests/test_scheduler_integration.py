"""定时任务跨层集成测试（HTTP → 内核 → 派发 → 执行历史）。

与 unit 测试的区别：这里**全程走 HTTP**，串联四层一致性——``api.py`` 的 camelCase
出口、``store.py`` 的落盘、``engine.py`` 的 tick/派发、``runs.jsonl`` 执行历史。

覆盖 PLAN §4.1 的两条触发路径：
1. tick 扫描到期 —— 断言「执行历史有记录」且 ``nextFireAt`` **已推进到未来**；
2. ``POST /tasks/{id}/run-now`` —— 断言立即执行、**不**推进 ``nextFireAt``（PLAN §4.1）。

隔离：``store.DEFAULT_ROOT`` / ``PAN_SCHEDULER_DIR`` 重定向到 tmp_path，
``worker.assign`` 替换成记录调用的假实现——不 spawn 真 worker、不写真实 ``data/``。
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.core import worker as core_worker
from packages.scheduler import api as scheduler_api
from packages.scheduler import engine as scheduler_engine
from packages.scheduler import store as scheduler_store


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离的 scheduler 数据根 + 假派发 + 挂了路由的测试用 app。"""
    root = tmp_path / "scheduler"
    monkeypatch.setattr(scheduler_store, "DEFAULT_ROOT", root)
    monkeypatch.setenv("PAN_SCHEDULER_DIR", str(root))
    # 单实例选主：测试内固定是 leader，不受本机其它 Pan 实例影响
    monkeypatch.setattr(scheduler_store, "claim_leader", lambda: True)
    # HTTP 层只做「目标 session 是否存在」的校验，这里放行而不碰真实会话库
    monkeypatch.setattr(scheduler_api, "_session_exists", lambda session_id: True)

    calls: list[dict] = []

    async def fake_assign(session_id, text, source=None, task_id=None):
        calls.append({
            "session_id": session_id,
            "text": text,
            "source": source,
            "task_id": task_id,
        })
        return {"status": "queued", "workerId": None}

    monkeypatch.setattr(core_worker, "assign", fake_assign)

    app = FastAPI()
    app.include_router(scheduler_api.router)
    return TestClient(app), calls


def _async(fn, *args):
    """在独立事件循环里跑协程，并等在途派发结束。"""
    async def runner():
        result = await fn(*args)
        await scheduler_engine.drain()
        return result
    return asyncio.run(runner())


def _create(client, **overrides):
    body = {
        "name": "集成任务",
        "targetSessionId": "ses_ok",
        "text": "把日报跑出来",
    }
    body.update(overrides)
    response = client.post("/api/scheduler/tasks", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True, payload
    return payload["task"]


def _force_due(task_id: str, seconds: int = 2) -> str:
    """把任务的 next_fire_at 拨到过去（在 misfire 宽限内），让它下一 tick 到期。"""
    due = (datetime.now() - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    scheduler_store.update_task(task_id, {"next_fire_at": due})
    return due


def _expected_dispatch_key(task_id: str, fire_at_iso: str) -> str:
    """统一幂等键：taskId:entryId:fire_ts（DESIGN §3，兼容层 entry 可寻址）。"""
    entry_id = scheduler_store._job_for_task(task_id)["schedule"][0]["id"]
    return f"{task_id}:{entry_id}:{int(datetime.fromisoformat(fire_at_iso).timestamp())}"


# ── 1. tick 触发 interval 任务：历史有记录 + nextFireAt 推进 ──


def test_tick_dispatches_and_advances_next_fire(env):
    client, calls = env
    task = _create(client, schedule={"kind": "interval", "intervalSec": 60})
    task_id = task["id"]

    # interval 任务创建时 nextFireAt 必在未来，因此手动拨表使其到期
    due = _force_due(task_id)
    handled = _async(scheduler_engine.tick_once)
    assert handled == 1, "到期任务应被 tick 扫到"

    # HTTP 出口：执行历史里有且仅有一条 dispatched 记录
    runs = client.get(f"/api/scheduler/tasks/{task_id}/runs").json()
    assert runs["ok"] is True
    assert len(runs["runs"]) == 1, runs
    run = runs["runs"][0]
    assert run["status"] == "dispatched", run
    assert run["taskId"] == task_id
    # 命名纪律（DESIGN §3）：幂等键 = taskId:entryId:fire_at 的 epoch 秒
    expected_key = _expected_dispatch_key(task_id, due)
    assert run["dispatchKey"] == expected_key, run

    # nextFireAt 已推进（> 触发点，且回到未来）
    after = client.get(f"/api/scheduler/tasks/{task_id}").json()["task"]
    next_fire = datetime.fromisoformat(after["nextFireAt"])
    assert next_fire > datetime.fromisoformat(due), "nextFireAt 未推进"
    assert next_fire > datetime.now(), "nextFireAt 未推进到未来"
    assert after["runCount"] == 1
    assert after["lastStatus"] == "dispatched"

    # 派发原语核对（PLAN §0）
    assert len(calls) == 1, calls
    assert calls[0]["source"] == "automation"
    assert calls[0]["session_id"] == "ses_ok"
    assert calls[0]["text"] == "把日报跑出来"
    assert calls[0]["task_id"] == expected_key


# ── 2. run-now 触发 cron 任务：立即执行，但不推进 nextFireAt ──


def test_run_now_dispatches_without_advancing_next_fire(env):
    client, calls = env
    task = _create(client, schedule={"kind": "cron", "cron": "*/5 * * * *"})
    task_id = task["id"]
    original_next = task["nextFireAt"]

    response = client.post(f"/api/scheduler/tasks/{task_id}/run-now")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True, payload
    assert payload["run"]["status"] == "dispatched", payload
    assert payload["run"]["taskId"] == task_id

    # run-now 只写历史与 last*_ 字段，nextFireAt 保持原值（PLAN §4.1）
    after = payload["task"]
    assert after["nextFireAt"] == original_next
    assert after["runCount"] == 1
    assert after["lastStatus"] == "dispatched"

    runs = client.get(f"/api/scheduler/tasks/{task_id}/runs").json()
    assert len(runs["runs"]) == 1, runs
    assert runs["runs"][0]["fireAt"] is not None

    assert len(calls) == 1
    assert calls[0]["source"] == "automation"
    # 手动触发用「当下」构造幂等键，与后续自动触发不冲突
    assert calls[0]["task_id"].startswith(f"{task_id}:")
    assert calls[0]["task_id"] != f"{task_id}:{int(datetime.fromisoformat(original_next).timestamp())}"


# ── 3. 列表 / 预览出口字段与前端取值一致（PLAN §4.4 / B.1 / B.5）──


def test_http_exports_camel_case_fields_frontend_reads(env):
    client, _ = env
    task = _create(client, schedule={"kind": "interval", "intervalSec": 1800})
    task_id = task["id"]

    task = client.get(f"/api/scheduler/tasks/{task_id}").json()["task"]
    # 前端 ScheduledTask 读取的字段必须存在且非空
    for key in ("targetSessionId", "nextFireAt", "lastFireAt", "lastStatus",
                "runCount", "maxRuns", "misfirePolicy", "createdAt"):
        assert key in task, f"缺少字段 {key}"
    # §4.1 提到的 nextRunAt/lastRunAt 别名也在，两种契约都取得到值
    assert task["nextRunAt"] == task["nextFireAt"]
    # interval 只出 intervalSec，不泄漏内部 snake_case
    assert task["schedule"]["intervalSec"] == 1800
    assert "interval_sec" not in task["schedule"]

    listed = client.get("/api/scheduler/tasks").json()
    assert listed["ok"] is True
    assert {t["id"] for t in listed["tasks"]} == {task_id}

    # GET /next → {"ok":true,"next":[{"taskId","fireAt"}]}
    preview = client.get("/api/scheduler/next", params={"task_id": task_id, "count": 3}).json()
    assert preview["ok"] is True
    assert len(preview["next"]) == 3
    for item in preview["next"]:
        assert set(item) == {"taskId", "fireAt"}


# ── 4. 错误出口与前端 schedulerErrorMessage() 对齐（PLAN §4.3）──


def test_error_envelope_shape(env):
    client, _ = env
    response = client.post("/api/scheduler/tasks", json={"text": "", "targetSessionId": "ses_ok"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    # 前端 schedulerErrorMessage() 读 error.message / error.code
    assert set(payload["error"]) == {"code", "message"}
    assert payload["error"]["code"] == "invalid_argument"

    missing = client.get("/api/scheduler/tasks/sch_nope").json()
    assert missing["ok"] is False
    assert missing["error"]["code"] == "not_found"
