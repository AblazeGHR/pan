"""packages/scheduler/engine.py（统一循环薄封装）的行为测试。

P1 统一后，真正的调度逻辑在 ``packages.core.background_jobs.run_due_scheduled_tasks``
（claim 状态机 + stale requeue + dispatch_key 幂等，DESIGN_DISPATCH_CLAIM_FUSION.md）；
engine.tick_once 只是薄封装。本文件验证统一循环保留 PR 语义（grace / misfire /
max_runs / paused 推进 / run_now / preview）并落地新语义（认领先于派发、顺序派发、
undeliverable 积压重投、多 entry 独立 dispatch_key）。

- 数据隔离：``store.DEFAULT_ROOT`` → tmp_path，绝不写真实 ``data/``。
- 派发隔离：monkeypatch 掉 ``packages.core.worker.assign``（统一循环通过
  ``background_jobs._worker`` 引用它），绝不真的 spawn CLI 进程。
- 无 pytest-asyncio（仓库未安装）：用例同步，内部用 ``asyncio.run`` 驱动。
"""

import asyncio
import time
from datetime import datetime, timedelta

import pytest

from packages.core import background_jobs
from packages.core import worker as _worker
from packages.scheduler import cron
from packages.scheduler import engine
from packages.scheduler import store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("PAN_SCHEDULER_DIR", raising=False)
    monkeypatch.delenv("PAN_BACKGROUND_JOBS_DIR", raising=False)
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path / "scheduler")
    monkeypatch.setattr(background_jobs, "_scheduled_task_stats",
                        {"dueScanned": 0, "lastTickAt": None})
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


def _entry_id_of(task_id: str) -> str:
    job = store._job_for_task(task_id)
    return job["schedule"][0]["id"]


def _expected_key(task: dict, fire_at: datetime) -> str:
    """统一 dispatch_key：taskId:entryId:fire_ts（DESIGN §3）。"""
    return f"{task['id']}:{_entry_id_of(task['id'])}:{int(fire_at.timestamp())}"


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
    assert call["task_id"] == _expected_key(task, fire_at)


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


def test_claim_persists_before_dispatch_completes(monkeypatch, fixed_config):
    """认领即「落盘先于派发」：派发被 gate 卡住时，running 已写进注册表。"""
    fixed_config(misfire_grace_sec=300)
    gate = asyncio.Event()
    fake = FakeAssign(gate=gate)
    monkeypatch.setattr(_worker, "assign", fake)
    task = due_task(seconds_ago=3)

    async def scenario():
        ticking = asyncio.create_task(engine.tick_once())
        for _ in range(100):  # 等派发真正开始（fake 先记调用再等 gate）
            await asyncio.sleep(0.01)
            if fake.calls:
                break
        assert fake.calls, "派发未开始"
        job = store._job_for_task(task["id"])
        assert job["status"] == "running"
        assert job["runStartedAt"] is not None
        gate.set()
        assert await ticking == 1

    run(scenario())
    assert len(fake.calls) == 1
    saved = store.get_task(task["id"])
    assert saved["last_status"] == "dispatched"
    assert saved["run_count"] == 1
    assert cron.parse_datetime(saved["next_fire_at"]) > _now()


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
    assert runs[0]["dispatch_key"] == _expected_key(task, fire_at)


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


# ── 派发并发模型（统一循环：claim 后内联顺序派发，DESIGN §2）──


def test_dispatches_are_sequential_within_a_pass(monkeypatch, fixed_config):
    """一轮内的派发是内联顺序执行：前一个不完成，后一个不开始。"""
    fixed_config()
    gate = asyncio.Event()
    fake = FakeAssign(gate=gate)
    monkeypatch.setattr(_worker, "assign", fake)
    for _ in range(3):
        due_task(seconds_ago=3)

    async def scenario():
        ticking = asyncio.create_task(engine.tick_once())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if fake.calls:
                break
        assert len(fake.calls) == 1  # 第一个派发被 gate 卡住，后续未开始
        gate.set()
        assert await ticking == 3

    run(scenario())
    assert len(fake.calls) == 3
    assert fake.max_concurrent == 1


# ── 生命周期（循环已统一；start/stop 为注册/兼容桩）──


def test_start_stop_loop_is_idempotent(fake_assign, fixed_config):
    fixed_config(tick_sec=1)

    async def scenario():
        await engine.start_loop()   # 注册 + 迁移，不抛
        await engine.start_loop()   # 幂等
        # 测试进程没有 recovery loop；生产环境由 server 启动统一循环
        assert engine.status()["running"] is False
        await engine.stop_loop()
        await engine.stop_loop()    # 幂等

    run(scenario())


def test_start_loop_respects_disabled_config(fake_assign, fixed_config):
    fixed_config(enabled=False)

    async def scenario():
        await engine.start_loop()
        assert engine.status()["running"] is False
        assert await engine.tick_once() == 0  # 配置关闭 → 扫描直接跳过

    run(scenario())


def test_status_shape(fixed_config):
    fixed_config(tick_sec=2)  # 已退役的配置项，不再影响统一循环
    snapshot = engine.status()
    assert set(snapshot) >= {"running", "tickSec", "dueScanned", "lastTickAt"}
    assert snapshot["tickSec"] == 1  # 统一 recovery loop 固定 1s
    assert snapshot["running"] is False


# ── 崩溃恢复（claim 状态机 + stale requeue，取代 PR 的 _recover）──


def test_stale_running_claim_requeues_and_refires(fake_assign, fixed_config):
    """认领后卡死（模拟崩溃）→ 下一轮 requeue → 重投（幂等键不变）。"""
    fixed_config()
    fake = fake_assign()
    task = due_task(seconds_ago=30)
    fire_at = cron.parse_datetime(task["next_fire_at"])
    job = store._job_for_task(task["id"])
    # 模拟：认领后进程死掉，runStartedAt 已超过判死窗口
    background_jobs._update(job["jobId"],
                            {"status": "running",
                             "runStartedAt": time.time() - 30},
                            registry_root=store.data_root())

    assert cycle() == [1]
    saved = store.get_task(task["id"])
    assert saved["last_status"] == "dispatched"
    # requeue 重投用的是同一个 dispatch_key（worker 幂等索引兜底，不双跑）
    assert fake.calls[0]["task_id"] == _expected_key(task, fire_at)


def test_long_late_interval_fires_once_and_regrids(fake_assign, fixed_config):
    """停机 10 小时：fire_now 只结算一次，nextFireAt 落回锚点网格。"""
    fixed_config()
    fake = fake_assign()
    anchor = _now() - timedelta(hours=10)
    task = make_task(
        next_fire_at=anchor,  # 停机期间早就过期
        schedule={
            "kind": "interval",
            "interval_sec": 1800,
            "anchor": anchor.isoformat(),
        },
    )

    assert cycle() == [1]
    saved = store.get_task(task["id"])
    assert saved["last_status"] == "dispatched"
    point = cron.parse_datetime(saved["next_fire_at"])
    assert point > _now()
    assert (point - anchor).total_seconds() % 1800 == 0  # 仍在锚点网格上


# ── undeliverable 积压与重投（PLAN §10）──


def test_undeliverable_backs_up_without_breaking_cadence(fake_assign, fixed_config):
    fixed_config()
    fake_assign(result={"status": "error",
                        "result": "Session ses_target not found"})
    task = due_task(seconds_ago=3)

    cycle()

    saved = store.get_task(task["id"])
    assert saved["last_status"] == "undeliverable"
    job = store._job_for_task(task["id"])
    assert len(job["undeliveredFires"]) == 1
    assert job["undeliveredFires"][0]["text"] == "去做事"
    assert store.list_runs(task_id=task["id"])[0]["status"] == "undeliverable"
    # 周期节奏照常推进
    assert cron.parse_datetime(saved["next_fire_at"]) > _now()


def test_undelivered_redelivered_when_target_returns(fake_assign, fixed_config,
                                                     monkeypatch):
    fixed_config()
    not_found = {"status": "error", "result": "Session ses_target not found"}
    fake = fake_assign(result=not_found)
    task = due_task(seconds_ago=3)
    cycle()
    assert len(fake.calls) == 1

    # target 恢复（或切换到已存在的 session）→ 自动重投，dispatch_key 原样复用
    monkeypatch.setattr(
        "packages.core.session.get",
        lambda session_id: object() if session_id == "ses_target" else None)
    fake.result = {"status": "queued"}
    first_key = fake.calls[0]["task_id"]

    assert cycle() == [1]
    assert len(fake.calls) == 2
    assert fake.calls[1]["task_id"] == first_key  # 幂等键复用，接收端去重兜底
    job = store._job_for_task(task["id"])
    assert job["undeliveredFires"] == []
    saved = store.get_task(task["id"])
    assert saved["last_status"] == "dispatched"


# ── 多 entry（schedule 列表）──


def test_multi_entry_same_job_fires_independently(fake_assign, fixed_config):
    """同 job 双 entry 同刻到期：两个 dispatch_key 各自独立（DESIGN §5.3）。"""
    fixed_config()
    fake = fake_assign()
    task = due_task(seconds_ago=10)
    fire_at = cron.parse_datetime(task["next_fire_at"])
    job = store._job_for_task(task["id"])
    entry0 = dict(job["schedule"][0])
    entry1 = dict(entry0, id="sce_second01", nextFireAt=store.iso(fire_at))
    background_jobs._update(job["jobId"], {"schedule": [entry0, entry1]},
                            registry_root=store.data_root())

    assert cycle() == [2]
    assert len(fake.calls) == 2
    keys = {call["task_id"] for call in fake.calls}
    assert len(keys) == 2  # 不撞键：各自派发一次
    expected = {f"{task['id']}:{entry0['id']}:{int(fire_at.timestamp())}",
                f"{task['id']}:{entry1['id']}:{int(fire_at.timestamp())}"}
    assert keys == expected
    saved = store.get_task(task["id"])
    assert saved["run_count"] == 2  # 每个 entry 各计一次


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
