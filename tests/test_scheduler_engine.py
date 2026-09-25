"""packages/scheduler/engine.py 的单元测试。

- 数据隔离：``store.DEFAULT_ROOT`` → tmp_path，绝不写真实 ``data/``。
- 派发隔离：monkeypatch 掉 ``packages.core.worker.assign``（engine 通过
  ``engine._worker`` 引用它），绝不真的 spawn CLI 进程。
- 无 pytest-asyncio（仓库未安装）：用例同步，内部用 ``asyncio.run`` 驱动。
  注意「扫一轮 + 等在途派发」必须在**同一个** event loop 里完成，故统一走
  :func:`cycle` 助手。
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from packages.core import worker as _worker
from packages.scheduler import cron
from packages.scheduler import engine
from packages.scheduler import store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("PAN_SCHEDULER_DIR", raising=False)
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path / "scheduler")
    monkeypatch.setattr(engine, "_loop_task", None)
    monkeypatch.setattr(engine, "_pending", set())
    monkeypatch.setattr(engine, "_on_event", None)
    monkeypatch.setattr(engine, "_stats", {"due_scanned": 0, "last_tick_at": None})
    yield
    store.release_leader()


def _cfg(**overrides) -> dict:
    cfg = dict(engine.DEFAULT_SCHEDULER_CONFIG)
    cfg.update(overrides)
    return cfg


@pytest.fixture
def fixed_config(monkeypatch):
    def apply(**overrides):
        monkeypatch.setattr(engine, "scheduler_config", lambda: _cfg(**overrides))

    return apply


# ── 假派发 ──


class FakeAssign:
    """记录调用、可控阻塞的 worker.assign 替身。"""

    def __init__(self, result=None, gate: asyncio.Event | None = None):
        self.calls: list[dict] = []
        self.result = result or {"status": "queued"}
        self.gate = gate
        self.concurrent = 0
        self.max_concurrent = 0

    async def __call__(self, session_id, text, source=None, task_id=None,
                       source_session_id=None):
        self.calls.append(
            {
                "session_id": session_id,
                "text": text,
                "source": source,
                "task_id": task_id,
            }
        )
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self.gate is not None:
                await self.gate.wait()
            await asyncio.sleep(0)
            if isinstance(self.result, Exception):
                raise self.result
            return dict(self.result)
        finally:
            self.concurrent -= 1


@pytest.fixture
def fake_assign(monkeypatch):
    def install(result=None, gate=None):
        fake = FakeAssign(result=result, gate=gate)
        monkeypatch.setattr(_worker, "assign", fake)
        return fake

    return install


# ── 造任务 ──


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def make_task(next_fire_at: datetime | None = None, **overrides) -> dict:
    payload = {
        "name": "测试任务",
        "target_session_id": "ses_target",
        "text": "去做事",
        "schedule": {
            "kind": "interval",
            "interval_sec": 3600,
            "anchor": (_now() - timedelta(hours=1)).isoformat(),
        },
    }
    payload.update(overrides)
    task = store.create_task(payload)
    if next_fire_at is not None:
        task = store.update_task(task["id"], {"next_fire_at": store.iso(next_fire_at)})
    return task


def due_task(seconds_ago: int = 5, **overrides) -> dict:
    return make_task(next_fire_at=_now() - timedelta(seconds=seconds_ago), **overrides)


def cycle(times: int = 1) -> list[int]:
    """在同一个 event loop 内跑 n 轮「扫描 + 等在途派发结束」。"""
    async def main():
        counts = []
        for _ in range(times):
            counts.append(await engine.tick_once())
            await engine.drain()
        return counts

    return asyncio.run(main())


def run(coro):
    return asyncio.run(coro)


# ── 到期派发 ──


def test_due_task_dispatches_once_with_correct_dispatch_key(fake_assign, fixed_config):
    fixed_config(misfire_grace_sec=300)
    fake = fake_assign()
    task = due_task(seconds_ago=5)
    fire_at = cron.parse_datetime(task["next_fire_at"])

    assert cycle() == [1]

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["session_id"] == "ses_target"
    assert call["text"] == "去做事"
    assert call["source"] == "automation"
    assert call["task_id"] == f"{task['id']}:{int(fire_at.timestamp())}"


def test_not_due_task_is_untouched(fake_assign, fixed_config):
    fixed_config()
    fake = fake_assign()
    make_task(next_fire_at=_now() + timedelta(minutes=5))
    assert cycle() == [0]
    assert fake.calls == []


def test_disabled_and_paused_tasks_are_not_dispatched(fake_assign, fixed_config):
    fixed_config()
    fake = fake_assign()
    due_task(seconds_ago=10, enabled=False)
    paused = due_task(seconds_ago=10, paused=True)

    assert cycle() == [0]
    assert fake.calls == []
    # paused 的 next_fire_at 仍按锚点推进，恢复后不爆发补跑
    assert cron.parse_datetime(store.get_task(paused["id"])["next_fire_at"]) > _now()


def test_once_task_disabled_after_firing(fake_assign, fixed_config):
    fixed_config(misfire_grace_sec=300)
    fake = fake_assign()
    task = make_task(
        next_fire_at=_now() - timedelta(seconds=5),
        schedule={"kind": "once", "at": (_now() - timedelta(seconds=5)).isoformat()},
    )

    cycle()

    assert len(fake.calls) == 1
    saved = store.get_task(task["id"])
    assert saved["enabled"] is False
    assert saved["next_fire_at"] is None
    assert saved["last_status"] == "dispatched"
    assert saved["run_count"] == 1


def test_next_fire_advances_and_persists_before_dispatch(monkeypatch, fixed_config):
    """落盘先于派发：派发被 gate 卡住时，状态已经写进文件。"""
    fixed_config(misfire_grace_sec=300)
    gate = asyncio.Event()
    fake = FakeAssign(gate=gate)
    monkeypatch.setattr(_worker, "assign", fake)
    anchor = _now() - timedelta(hours=3)
    task = make_task(
        next_fire_at=anchor + timedelta(hours=3),
        schedule={
            "kind": "interval",
            "interval_sec": 3600,
            "anchor": anchor.isoformat(),
        },
    )

    async def scenario():
        assert await engine.tick_once() == 1
        saved = store.get_task(task["id"])
        assert saved["run_count"] == 1
        assert cron.parse_datetime(saved["next_fire_at"]) == anchor + timedelta(hours=4)
        assert saved["last_status"] == "dispatched"
        gate.set()
        await engine.drain()

    run(scenario())
    assert len(fake.calls) == 1


# ── misfire ──


def test_once_past_grace_expires_without_dispatch(fake_assign, fixed_config):
    fixed_config(misfire_grace_sec=300)
    fake = fake_assign()
    fire_at = _now() - timedelta(seconds=600)
    task = make_task(
        next_fire_at=fire_at,
        schedule={"kind": "once", "at": fire_at.isoformat()},
    )

    cycle()

    assert fake.calls == []  # 超宽限一律不补派
    saved = store.get_task(task["id"])
    assert saved["enabled"] is False
    assert saved["last_status"] == "expired"
    assert saved["next_fire_at"] is None
    runs = store.list_runs(task_id=task["id"])
    assert runs and runs[0]["status"] == "expired"
    assert runs[0]["dispatch_key"] == f"{task['id']}:{int(fire_at.timestamp())}"


def test_interval_past_grace_fires_now_by_default(fake_assign, fixed_config):
    fixed_config(misfire_grace_sec=300)
    fake = fake_assign()
    task = due_task(seconds_ago=600)  # 默认 fire_now

    cycle()

    assert len(fake.calls) == 1  # 只补一次，绝不循环补跑
    saved = store.get_task(task["id"])
    assert saved["last_status"] == "dispatched"


def test_interval_past_grace_skip_policy(fake_assign, fixed_config):
    fixed_config(misfire_grace_sec=300)
    fake = fake_assign()
    task = due_task(seconds_ago=600, misfire_policy="skip")

    cycle()

    assert fake.calls == []
    saved = store.get_task(task["id"])
    assert saved["last_status"] == "skipped"
    # 按锚点推进到未来，下一轮不会重复判 skip
    assert cron.parse_datetime(saved["next_fire_at"]) > _now()
    assert store.list_runs(task_id=task["id"])[0]["status"] == "skipped"


def test_misfire_never_replays(fake_assign, fixed_config):
    """休眠唤醒：一次结算，第二轮不再重复处理同一个 fire_at。"""
    fixed_config(misfire_grace_sec=300)
    fake = fake_assign()
    due_task(seconds_ago=600)

    assert cycle(2) == [1, 0]
    assert len(fake.calls) == 1


# ── 派发失败 ──


def test_dispatch_error_is_recorded(fake_assign, fixed_config):
    fixed_config()
    fake_assign(result={"status": "error", "result": "no worker"})
    task = due_task(seconds_ago=2)

    cycle()

    saved = store.get_task(task["id"])
    assert saved["last_status"] == "error"
    assert "no worker" in (saved["last_error"] or "")
    assert store.list_runs(task_id=task["id"])[0]["status"] == "error"


def test_dispatch_exception_is_recorded(fake_assign, fixed_config):
    fixed_config()
    fake_assign(result=RuntimeError("boom"))
    task = due_task(seconds_ago=2)

    cycle()

    saved = store.get_task(task["id"])
    assert saved["last_status"] == "error"
    assert "boom" in (saved["last_error"] or "")


# ── max_runs ──


def test_max_runs_auto_disables(fake_assign, fixed_config):
    fixed_config()
    fake_assign()
    task = due_task(seconds_ago=2, max_runs=1)

    cycle()

    saved = store.get_task(task["id"])
    assert saved["enabled"] is False
    assert saved["next_fire_at"] is None


# ── 并发限流 ──


def test_semaphore_limits_concurrent_dispatch(monkeypatch, fixed_config):
    fixed_config(max_concurrent_dispatch=1)

    async def scenario():
        gate = asyncio.Event()
        fake = FakeAssign(gate=gate)
        monkeypatch.setattr(_worker, "assign", fake)
        for _ in range(3):
            due_task(seconds_ago=3)
        assert await engine.tick_once() == 3
        await asyncio.sleep(0)  # 让三个派发协程都跑到信号量前
        assert fake.max_concurrent == 1
        gate.set()
        await engine.drain()
        assert len(fake.calls) == 3

    run(scenario())


def test_semaphore_allows_configured_parallelism(monkeypatch, fixed_config):
    fixed_config(max_concurrent_dispatch=2)

    async def scenario():
        gate = asyncio.Event()
        fake = FakeAssign(gate=gate)
        monkeypatch.setattr(_worker, "assign", fake)
        for _ in range(4):
            due_task(seconds_ago=3)
        await engine.tick_once()
        await asyncio.sleep(0)
        assert fake.max_concurrent == 2
        gate.set()
        await engine.drain()
        assert len(fake.calls) == 4

    run(scenario())


# ── 生命周期 ──


def test_start_stop_loop_is_idempotent(fake_assign, fixed_config):
    fixed_config(tick_sec=1)

    async def scenario():
        await engine.start_loop()
        first = engine._loop_task
        assert engine.status()["running"] is True
        await engine.start_loop()  # 幂等：复用同一个 task
        assert engine._loop_task is first
        await engine.stop_loop()
        assert engine.status()["running"] is False
        await engine.stop_loop()  # 幂等

    run(scenario())


def test_start_loop_respects_disabled_config(fake_assign, fixed_config):
    fixed_config(enabled=False)

    async def scenario():
        await engine.start_loop()
        assert engine.status()["running"] is False

    run(scenario())


def test_start_loop_skips_when_not_leader(fake_assign, fixed_config, monkeypatch):
    fixed_config()
    monkeypatch.setattr(store, "claim_leader", lambda *a, **k: False)

    async def scenario():
        await engine.start_loop()
        assert engine.status()["running"] is False

    run(scenario())


def test_status_shape(fixed_config):
    fixed_config(tick_sec=2)
    snapshot = engine.status()
    assert set(snapshot) >= {"running", "tickSec", "dueScanned", "lastTickAt"}
    assert snapshot["tickSec"] == 2
    assert snapshot["running"] is False


# ── 恢复语义 ──


def test_recovery_marks_unknown_without_redispatch(fake_assign, fixed_config,
                                                   monkeypatch):
    fixed_config()
    fake = fake_assign()
    fire_at = _now() - timedelta(seconds=30)
    task = make_task(next_fire_at=fire_at)
    store.update_task(
        task["id"],
        {"last_status": "dispatched", "last_fire_at": store.iso(fire_at)},
    )
    # 目标 session 不存在 → 查不到终态
    monkeypatch.setattr(engine, "_terminal_seen", lambda *a, **k: False)

    engine._recover()
    saved = store.get_task(task["id"])
    assert saved["last_status"] == "unknown"
    assert fake.calls == []  # 绝不自动补派


def test_recovery_recomputes_next_fire_from_anchor(fixed_config):
    fixed_config()
    anchor = _now() - timedelta(hours=10)
    task = make_task(
        next_fire_at=anchor,  # 停机期间早就过期
        schedule={
            "kind": "interval",
            "interval_sec": 1800,
            "anchor": anchor.isoformat(),
        },
    )
    engine._recover()
    saved = store.get_task(task["id"])
    point = cron.parse_datetime(saved["next_fire_at"])
    assert point > _now()
    assert (point - anchor).total_seconds() % 1800 == 0  # 仍在锚点网格上


# ── run_now / preview_next ──


def test_run_now_dispatches_without_touching_next_fire(fake_assign, fixed_config):
    fixed_config()
    fake = fake_assign()
    task = make_task(next_fire_at=_now() + timedelta(hours=1))
    before = task["next_fire_at"]

    result = run(engine.run_now(task["id"]))

    assert result["ok"] is True
    assert result["status"] == "dispatched"
    assert len(fake.calls) == 1
    assert result["dispatchKey"].startswith(task["id"] + ":")
    saved = store.get_task(task["id"])
    assert saved["next_fire_at"] == before  # 不影响推进
    assert saved["run_count"] == 1


def test_run_now_missing_task(fake_assign, fixed_config):
    fixed_config()
    fake_assign()
    result = run(engine.run_now("sch_nope"))
    assert result["ok"] is False
    assert result["error"]["code"] == "not_found"


def test_preview_next_single_task(fixed_config):
    fixed_config()
    task = make_task(
        schedule={"kind": "cron", "cron": "0 9 * * *"},
    )
    preview = run(engine.preview_next(task_id=task["id"], count=3))
    assert len(preview) == 3
    assert all(set(item) == {"taskId", "fireAt"} for item in preview)
    assert all(item["taskId"] == task["id"] for item in preview)
    points = [cron.parse_datetime(item["fireAt"]) for item in preview]
    assert points == sorted(points)
    assert all((p.hour, p.minute) == (9, 0) for p in points)


def test_preview_next_across_tasks_sorted(fixed_config):
    fixed_config()
    early = make_task(next_fire_at=_now() + timedelta(minutes=5))
    late = make_task(next_fire_at=_now() + timedelta(hours=5))
    preview = run(engine.preview_next(count=5))
    assert preview
    assert [item["fireAt"] for item in preview] == sorted(
        item["fireAt"] for item in preview
    )
    ids = {item["taskId"] for item in preview}
    assert ids == {early["id"], late["id"]}


# ── on_event ──


def test_on_event_receives_fired_event(fake_assign, fixed_config):
    fixed_config()
    fake_assign()
    events: list[dict] = []
    engine._on_event = events.append
    task = due_task(seconds_ago=3)

    cycle()

    assert events
    assert events[0]["type"] == "scheduler.task.fired"
    assert events[0]["taskId"] == task["id"]
    assert events[0]["status"] == "dispatched"
    assert events[0]["dispatchKey"].startswith(task["id"] + ":")


def test_on_event_exception_does_not_break_tick(fake_assign, fixed_config):
    fixed_config()
    fake = fake_assign()

    def boom(_event):
        raise RuntimeError("ws down")

    engine._on_event = boom
    due_task(seconds_ago=3)

    cycle()
    assert len(fake.calls) == 1
