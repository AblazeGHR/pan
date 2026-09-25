"""定时任务 HTTP API 层测试。

范式照 ``tests/test_background_job_api.py``：直接调 handler（不走 TestClient），
monkeypatch 掉会话存在性校验与引擎函数，断言统一错误包络的 ``error.code``。

隔离：把内核 ``store.DEFAULT_ROOT`` 重定向到 tmp_path，绝不污染真实 ``data/``。
"""

import asyncio

import pytest

from packages.scheduler import api
from packages.scheduler import store as scheduler_store


@pytest.fixture
def scheduler_env(tmp_path, monkeypatch):
    """给每个测试一个独立的 scheduler 数据根 + 一个「存在」的 session。"""
    root = tmp_path / "scheduler"
    monkeypatch.setattr(scheduler_store, "DEFAULT_ROOT", root)
    monkeypatch.setenv("PAN_SCHEDULER_DIR", str(root))
    # HTTP 层不做鉴权，只按既有写法校验目标 session 是否存在
    monkeypatch.setattr(api, "_session_exists", lambda session_id: session_id == "ses_ok")
    return root


@pytest.fixture(autouse=True)
def reset_broadcast():
    yield
    api.bind(broadcast=None)


def _payload(**overrides):
    body = {
        "name": "每天 9 点跑数据",
        "targetSessionId": "ses_ok",
        "text": "跑一下日报",
        "schedule": {"kind": "cron", "cron": "0 9 * * 1-5"},
    }
    body.update(overrides)
    return body


# ── CRUD 全通 ──


def test_create_list_get_roundtrip(scheduler_env):
    created = asyncio.run(api.create_task(_payload()))
    assert created["ok"], created
    task = created["task"]
    assert task["id"].startswith("sch_")
    assert task["targetSessionId"] == "ses_ok"
    assert task["text"] == "跑一下日报"
    assert task["enabled"] is True
    assert task["paused"] is False
    assert task["schedule"]["kind"] == "cron"
    assert "nextFireAt" in task and "createdAt" in task

    listed = asyncio.run(api.list_tasks())
    assert [t["id"] for t in listed["tasks"]] == [task["id"]]

    fetched = asyncio.run(api.get_task(task["id"]))
    assert fetched["ok"]
    assert fetched["task"]["runCount"] == 0
    assert fetched["task"]["maxRuns"] is None
    assert fetched["task"]["misfirePolicy"] == "fire_now"


def test_update_task_changes_fields(scheduler_env):
    task_id = asyncio.run(api.create_task(_payload()))["task"]["id"]
    updated = asyncio.run(api.update_task(task_id, {
        "name": "改名",
        "text": "新任务文本",
        "schedule": {"kind": "interval", "intervalSec": 1800},
        "enabled": False,
        "maxRuns": 3,
        "misfirePolicy": "skip",
    }))
    assert updated["ok"], updated
    task = updated["task"]
    assert task["name"] == "改名"
    assert task["text"] == "新任务文本"
    assert task["schedule"]["intervalSec"] == 1800
    assert task["enabled"] is False
    assert task["maxRuns"] == 3
    assert task["misfirePolicy"] == "skip"


def test_delete_task_removes_it(scheduler_env):
    task_id = asyncio.run(api.create_task(_payload()))["task"]["id"]
    deleted = asyncio.run(api.delete_task(task_id))
    assert deleted["ok"] and deleted["taskId"] == task_id
    assert asyncio.run(api.list_tasks())["tasks"] == []


def test_pause_and_resume_toggle_paused(scheduler_env):
    task_id = asyncio.run(api.create_task(_payload()))["task"]["id"]
    assert asyncio.run(api.pause_task(task_id))["task"]["paused"] is True
    assert asyncio.run(api.resume_task(task_id))["task"]["paused"] is False


def test_task_runs_starts_empty(scheduler_env):
    task_id = asyncio.run(api.create_task(_payload()))["task"]["id"]
    runs = asyncio.run(api.task_runs(task_id))
    assert runs["ok"] and runs["runs"] == []


# ── 参数校验错误码 ──


def test_create_rejects_unknown_schedule_kind(scheduler_env):
    result = asyncio.run(api.create_task(_payload(schedule={"kind": "weekly"})))
    assert result["error"]["code"] == "invalid_schedule"


def test_create_rejects_invalid_cron(scheduler_env):
    result = asyncio.run(api.create_task(
        _payload(schedule={"kind": "cron", "cron": "every morning"})))
    assert result["error"]["code"] == "invalid_schedule"


def test_create_rejects_non_positive_interval(scheduler_env):
    result = asyncio.run(api.create_task(
        _payload(schedule={"kind": "interval", "intervalSec": 0})))
    assert result["error"]["code"] == "invalid_schedule"


def test_create_rejects_once_without_at(scheduler_env):
    result = asyncio.run(api.create_task(_payload(schedule={"kind": "once"})))
    assert result["error"]["code"] == "invalid_schedule"


def test_create_rejects_missing_text(scheduler_env):
    result = asyncio.run(api.create_task({"targetSessionId": "ses_ok",
                                          "schedule": {"kind": "cron", "cron": "0 9 * * *"}}))
    assert result["error"]["code"] == "invalid_argument"


def test_create_rejects_unknown_session(scheduler_env):
    result = asyncio.run(api.create_task(_payload(targetSessionId="ses_missing")))
    assert result["error"]["code"] == "session_not_found"


def test_update_rejects_invalid_schedule_and_unknown_session(scheduler_env):
    task_id = asyncio.run(api.create_task(_payload()))["task"]["id"]
    bad = asyncio.run(api.update_task(task_id, {"schedule": {"kind": "cron", "cron": "nope"}}))
    assert bad["error"]["code"] == "invalid_schedule"
    missing = asyncio.run(api.update_task(task_id, {"targetSessionId": "ses_missing"}))
    assert missing["error"]["code"] == "session_not_found"


def test_next_preview_rejects_bad_count(scheduler_env):
    assert asyncio.run(api.next_preview(count=0))["error"]["code"] == "invalid_argument"
    assert asyncio.run(api.next_preview(count=99))["error"]["code"] == "invalid_argument"


# ── 不存在的 id ──


def test_unknown_task_id_reports_not_found(scheduler_env):
    results = [
        asyncio.run(api.get_task("sch_missing")),
        asyncio.run(api.update_task("sch_missing", {"name": "x"})),
        asyncio.run(api.delete_task("sch_missing")),
        asyncio.run(api.pause_task("sch_missing")),
        asyncio.run(api.resume_task("sch_missing")),
        asyncio.run(api.run_task_now("sch_missing")),
        asyncio.run(api.task_runs("sch_missing")),
        asyncio.run(api.next_preview(task_id="sch_missing", count=3)),
    ]
    assert [r["error"]["code"] for r in results] == ["not_found"] * len(results)


# ── 引擎委托 ──


def test_run_now_delegates_to_engine(monkeypatch, scheduler_env):
    calls = []

    async def fake_run_now(task_id):
        calls.append(task_id)
        return {"runId": "run_1", "taskId": task_id, "status": "dispatched"}

    monkeypatch.setattr(api.scheduler_engine, "run_now", fake_run_now)
    task_id = asyncio.run(api.create_task(_payload()))["task"]["id"]
    result = asyncio.run(api.run_task_now(task_id))
    assert result["ok"], result
    assert calls == [task_id]
    assert result["run"]["runId"] == "run_1"


def test_run_now_reports_engine_not_running(monkeypatch, scheduler_env):
    async def down(task_id):
        raise RuntimeError("scheduler loop is not running")

    monkeypatch.setattr(api.scheduler_engine, "run_now", down)
    task_id = asyncio.run(api.create_task(_payload()))["task"]["id"]
    assert asyncio.run(api.run_task_now(task_id))["error"]["code"] == "engine_not_running"


def test_status_and_next_preview(monkeypatch, scheduler_env):
    monkeypatch.setattr(api.scheduler_engine, "status",
                        lambda: {"running": False, "tickSec": 1, "dueScanned": 0})

    async def fake_preview(task_id=None, count=5):
        return [{"taskId": task_id or "sch_any", "fireAt": "2026-09-16T09:00:00"}
                for _ in range(count)]

    monkeypatch.setattr(api.scheduler_engine, "preview_next", fake_preview)
    assert asyncio.run(api.engine_status())["status"]["running"] is False

    preview = asyncio.run(api.next_preview(count=2))
    assert preview["ok"] and len(preview["next"]) == 2
    assert preview["next"][0]["fireAt"] == "2026-09-16T09:00:00"


# ── WS 广播注入 ──


def test_bind_broadcasts_task_events(scheduler_env):
    events = []
    api.bind(broadcast=events.append)
    created = asyncio.run(api.create_task(_payload()))
    task_id = created["task"]["id"]
    asyncio.run(api.update_task(task_id, {"name": "改名"}))
    asyncio.run(api.delete_task(task_id))

    types = [e["type"] for e in events]
    assert "scheduler.task.created" in types
    assert "scheduler.task.updated" in types
    assert "scheduler.task.deleted" in types
    assert all(e["sessionId"] == "ses_ok" for e in events if "sessionId" in e)


def test_on_event_supports_async_broadcast(scheduler_env):
    seen = []

    async def async_broadcast(event):
        seen.append(event)

    api.bind(broadcast=async_broadcast)

    async def scenario():
        api.on_event({"type": "scheduler.task.fired", "taskId": "sch_x"})
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert [e["type"] for e in seen] == ["scheduler.task.fired"]
    assert seen[0]["taskId"] == "sch_x"
