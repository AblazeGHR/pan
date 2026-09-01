"""Tests for global watchdog (立项 4.4) + spawn 防重复 (立项 4.5).

- _global_watchdog_tick: queue_pending 非空 && 无活 worker → create_worker 恢复
- create_worker dedup: 已有活 worker → 复用不重复建
- create_worker 并发（同一 session）→ per-session lock 串行化，只 spawn 一次
- _wake_worker auto_spawn: 无活 worker + auto_spawn=True → 立即 create_worker
  （QQ 消息入队即恢复，方案 B）；缺省 False 保持 _enqueue_report 静默语义
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


@pytest.fixture(autouse=True)
def _isolate_session_dir(tmp_path, monkeypatch):
    """把 session 落盘重定向到临时目录，避免测试污染真实 data/sessions/。"""
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(_sess, "_all_loaded", False)
    yield
    _cleanup()


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    worker._spawn_locks.clear()  # per-session asyncio.Lock 绑定事件循环，测试间需清掉
    worker._recovery_required.clear()
    _sess._cache.clear()
    _sess._all_loaded = False  # 重置磁盘加载标记，避免残留 session 混入
    worker.set_broadcaster(None)


def _setup_session(sid, queue_pending=None):
    s = _sess.Session(id=sid, name=sid)
    if queue_pending:
        s.queue_pending = list(queue_pending)
    _sess._cache[sid] = s
    return s


def _setup_worker(session_id, worker_id="worker-test"):
    proc = AsyncMock()
    proc.returncode = None
    w = worker.Worker(
        worker_id=worker_id,
        session_id=session_id,
        adapter=CbcAdapter(),
        status="idle",
        process=proc,
        pending_signal=asyncio.Queue(),
    )
    worker.workers[w.worker_id] = w
    return w


def test_restart_or_start_creates_when_session_has_no_live_worker(monkeypatch):
    _setup_session("ses_control")
    created = object()
    create = AsyncMock(return_value=created)
    restart = AsyncMock()
    monkeypatch.setattr(worker, "_create_worker", create)
    monkeypatch.setattr(worker, "_restart_worker_unlocked", restart)
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda _: None)

    result = asyncio.run(worker.restart_or_start_worker("ses_control"))

    assert result is created
    create.assert_awaited_once_with("ses_control")
    restart.assert_not_awaited()


def test_restart_or_start_restarts_live_worker_without_creating(monkeypatch):
    _setup_session("ses_control")
    live = SimpleNamespace(worker_id="worker-live")
    create = AsyncMock()
    restart = AsyncMock(return_value=None)
    monkeypatch.setattr(worker, "_create_worker", create)
    monkeypatch.setattr(worker, "_restart_worker_unlocked", restart)
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda _: live)

    result = asyncio.run(worker.restart_or_start_worker("ses_control"))

    assert result is live
    restart.assert_awaited_once_with("worker-live")
    create.assert_not_awaited()


# ── global watchdog tick ──


def test_global_watchdog_tick_spawns_for_pending_queue(monkeypatch):
    """queue_pending 非空 + 无活 worker → tick 自动 spawn 恢复。"""
    _cleanup()
    _setup_session("ses_mgr", queue_pending=[{"status": "done", "result": "r"}])
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id, worker_id="worker-new")

    monkeypatch.setattr(worker, "create_worker", fake_create)

    asyncio.run(worker._global_watchdog_tick())
    assert spawned == ["ses_mgr"], f"expected spawn for ses_mgr, got {spawned}"
    _cleanup()


def test_global_watchdog_tick_skips_live_worker(monkeypatch):
    """queue_pending 非空 + 已有活 worker → 不重复 spawn。"""
    _cleanup()
    _setup_session("ses_mgr", queue_pending=[{"status": "done", "result": "r"}])
    _setup_worker("ses_mgr")
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id, worker_id="worker-new")

    monkeypatch.setattr(worker, "create_worker", fake_create)

    asyncio.run(worker._global_watchdog_tick())
    assert spawned == [], f"must NOT spawn when live worker exists, got {spawned}"
    _cleanup()


def test_global_watchdog_tick_skips_active_oneshot_worker(monkeypatch):
    """CBC one-shot 执行期间 process=None，不能被 watchdog 重复 spawn。"""
    _cleanup()
    s = _setup_session("ses_oneshot", queue_pending=[{"type": "task", "text": "in flight"}])
    spawned = []

    async def scenario():
        consumer = asyncio.create_task(asyncio.Event().wait())
        w = worker.Worker(
            worker_id="worker-oneshot",
            session_id=s.id,
            adapter=CbcAdapter(),
            status="running",
            process=None,
            pending_signal=asyncio.Queue(),
            _consume_task=consumer,
        )
        worker.workers[w.worker_id] = w

        async def fake_create(session_id):
            spawned.append(session_id)
            return w

        monkeypatch.setattr(worker, "create_worker", fake_create)
        await worker._global_watchdog_tick()
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    asyncio.run(scenario())
    assert spawned == [], "active one-shot consumer must not be replaced"
    _cleanup()


def test_global_watchdog_tick_skips_empty_queue(monkeypatch):
    """queue_pending 为空 → 不 spawn。"""
    _cleanup()
    _setup_session("ses_plain")
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id, worker_id="worker-new")

    monkeypatch.setattr(worker, "create_worker", fake_create)

    asyncio.run(worker._global_watchdog_tick())
    assert spawned == [], "must NOT spawn when queue_pending is empty"
    _cleanup()


def test_abnormal_recovery_restarts_session_without_queue(monkeypatch):
    """An active-turn death still recreates the runtime after its receipt is closed."""
    _cleanup()
    _setup_session("ses_abnormal")
    created = object()
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(worker, "create_worker", create)
    monkeypatch.setattr(worker, "find_worker_by_session", lambda _: None)
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda _: None)

    asyncio.run(worker._recover_session("ses_abnormal", force=True))

    create.assert_awaited_once_with("ses_abnormal")
    _cleanup()


def test_idle_recovery_without_pending_work_does_not_recreate(monkeypatch):
    """Normal idle reclamation must remain terminal when no durable work exists."""
    _cleanup()
    _setup_session("ses_idle")
    create = AsyncMock()
    monkeypatch.setattr(worker, "create_worker", create)

    asyncio.run(worker._recover_session("ses_idle"))

    create.assert_not_awaited()
    _cleanup()


def test_recovery_scheduler_deduplicates_lifecycle_and_watchdog_requests(monkeypatch):
    """Multiple recovery triggers for one session share one create attempt."""
    _cleanup()
    _setup_session("ses_race")
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        await asyncio.sleep(0.01)
        return object()

    monkeypatch.setattr(worker, "create_worker", fake_create)
    monkeypatch.setattr(worker, "find_worker_by_session", lambda _: None)
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda _: None)

    async def scenario():
        first = worker._schedule_session_recovery("ses_race", force=True)
        second = worker._schedule_session_recovery("ses_race", force=True)
        assert first is second
        await asyncio.gather(first, second)

    asyncio.run(scenario())
    assert spawned == ["ses_race"]
    _cleanup()


def test_stdout_death_rebuilds_running_session_once(monkeypatch):
    """An EOF during a turn schedules force recovery and removes the corpse."""
    _cleanup()
    session = _setup_session(
        "ses_crash",
        queue_pending=[{"type": "task", "text": "continue", "deliveryState": "queued"}],
    )

    class EofStdout:
        async def read(self, _size):
            return b""

    process = SimpleNamespace(returncode=1, stdout=EofStdout())
    dead = worker.Worker(
        worker_id="worker-crash",
        session_id=session.id,
        adapter=CbcAdapter(),
        status="running",
        process=process,
        pending_signal=asyncio.Queue(),
    )
    worker.workers[dead.worker_id] = dead
    rebuilt = []

    async def fake_create(session_id):
        rebuilt.append(session_id)
        return _setup_worker(session_id, worker_id="worker-rebuilt")

    monkeypatch.setattr(worker, "create_worker", fake_create)
    monkeypatch.setattr(worker, "_enqueue_zombie_report", AsyncMock())
    monkeypatch.setattr(worker, "_bcast", AsyncMock())

    async def scenario():
        await worker._read_stdout(dead)
        recovery = worker._recovery_tasks.get(session.id)
        if recovery is not None:
            await recovery

    asyncio.run(scenario())
    assert rebuilt == [session.id]
    assert worker.find_worker_by_session(session.id).worker_id == "worker-rebuilt"
    _cleanup()


# ── create_worker dedup（立项 4.5）──


def test_create_worker_dedup_reuses_live_worker(monkeypatch):
    """已有活 worker → create_worker 直接复用，不重复建。"""
    _cleanup()
    _setup_session("ses_mgr")
    existing = _setup_worker("ses_mgr", worker_id="worker-1")
    calls = []

    async def fake_spawn_process(session_id, adapter, extra_args=None):
        calls.append(session_id)
        return AsyncMock()

    monkeypatch.setattr(worker, "_spawn_process", fake_spawn_process)

    w = asyncio.run(worker.create_worker("ses_mgr"))
    assert w is existing, "create_worker must reuse existing live worker"
    assert calls == [], f"must not spawn new process, got {calls}"
    _cleanup()


def test_create_worker_concurrent_no_double_spawn(monkeypatch):
    """同一 session 并发 create_worker → per-session lock 串行化，只 spawn 一次。"""
    _cleanup()
    _setup_session("ses_mgr")
    spawned = []

    async def fake_spawn_process(session_id, adapter, extra_args=None):
        spawned.append(session_id)
        await asyncio.sleep(0.05)  # 模拟 spawn 耗时，制造并发窗口
        proc = AsyncMock()
        proc.returncode = None  # 活进程：returncode is None 才被判定为 alive
        return proc

    # 防 _read_stdout 因 mock stdout 立刻 EOF 而回收刚 spawn 的 worker
    # （测试环境 artifact；真实进程 stdout 长驻，worker 会留在 registry）
    async def fake_read_stdout(w):
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "_spawn_process", fake_spawn_process)
    monkeypatch.setattr(worker, "_read_stdout", fake_read_stdout)

    async def scenario():
        t1 = asyncio.create_task(worker.create_worker("ses_mgr"))
        t2 = asyncio.create_task(worker.create_worker("ses_mgr"))
        r1, r2 = await asyncio.gather(t1, t2)
        # 清理内部任务，避免 loop close 的 pending 警告
        for w in {id(r1): r1, id(r2): r2}.values():
            for t in (w._stdout_task, w._consume_task, w._watchdog_task):
                if t:
                    t.cancel()
        return r1, r2

    r1, r2 = asyncio.run(scenario())
    assert len(spawned) == 1, f"double spawn! spawned={spawned}"
    assert r1.worker_id == r2.worker_id, "both calls should resolve to the same worker"
    _cleanup()


# ── _wake_worker auto_spawn（QQ 消息入队即恢复，方案 B）──


def test_wake_worker_default_silent_when_dead(monkeypatch):
    """无活 worker 且 auto_spawn 缺省 → 静默返回（_enqueue_report 语义不变）。"""
    _cleanup()
    _setup_session("ses_qq")
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id, worker_id="worker-new")

    monkeypatch.setattr(worker, "create_worker", fake_create)

    asyncio.run(worker._wake_worker("ses_qq"))
    assert spawned == [], "default must stay silent (watchdog handles recovery)"
    _cleanup()


def test_wake_worker_auto_spawn_when_dead(monkeypatch):
    """无活 worker + auto_spawn=True → 立即 create_worker（不等 watchdog tick）。"""
    _cleanup()
    _setup_session("ses_qq")
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id, worker_id="worker-new")

    monkeypatch.setattr(worker, "create_worker", fake_create)

    asyncio.run(worker._wake_worker("ses_qq", auto_spawn=True))
    assert spawned == ["ses_qq"], f"expected immediate spawn, got {spawned}"
    _cleanup()


def test_wake_worker_auto_spawn_registered_dead_process(monkeypatch):
    """worker 已注册但进程已死（_read_stdout 未及 pop）→ auto_spawn 仍触发。"""
    _cleanup()
    _setup_session("ses_qq")
    proc = AsyncMock()
    proc.returncode = 1  # 进程已退出
    w = worker.Worker(
        worker_id="worker-dead",
        session_id="ses_qq",
        adapter=CbcAdapter(),
        status="idle",
        process=proc,
        pending_signal=asyncio.Queue(),
    )
    worker.workers[w.worker_id] = w
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id, worker_id="worker-new")

    monkeypatch.setattr(worker, "create_worker", fake_create)

    asyncio.run(worker._wake_worker("ses_qq", auto_spawn=True))
    assert spawned == ["ses_qq"], f"dead process must trigger auto-spawn, got {spawned}"
    _cleanup()


def test_wake_worker_auto_spawn_skips_live_worker(monkeypatch):
    """有活 worker + auto_spawn=True → 只发信号不 spawn。"""
    _cleanup()
    _setup_session("ses_qq")
    w = _setup_worker("ses_qq")
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id, worker_id="worker-new")

    monkeypatch.setattr(worker, "create_worker", fake_create)

    asyncio.run(worker._wake_worker("ses_qq", auto_spawn=True))
    assert spawned == [], "live worker must only be woken, not re-spawned"
    assert w.pending_signal.qsize() == 1
    assert w.pending_signal.get_nowait() == {"type": "queue_signal"}
    _cleanup()


def test_wake_worker_auto_spawn_failure_swallowed(monkeypatch):
    """create_worker 失败（返回错误串）→ 打 warning 不抛异常，不阻塞入队链路。"""
    _cleanup()
    _setup_session("ses_qq")

    async def fake_create(session_id):
        return "spawn boom"

    monkeypatch.setattr(worker, "create_worker", fake_create)

    asyncio.run(worker._wake_worker("ses_qq", auto_spawn=True))  # 不应抛异常
    _cleanup()


def test_enqueue_qq_reminder_auto_spawns_for_dead_session(monkeypatch):
    """QQ 消息入队端到端：订阅 session 无活 worker → 立即 spawn（方案 B 核心）。"""
    _cleanup()
    s = _setup_session("ses_sub")
    s.qq_subscriptions = {"group:123"}
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id, worker_id="worker-new")

    monkeypatch.setattr(worker, "create_worker", fake_create)

    delivered = asyncio.run(
        worker.enqueue_qq_reminder("group", "123", text="hi")
    )
    assert delivered == 1
    assert spawned == ["ses_sub"], f"QQ enqueue must auto-spawn, got {spawned}"
    assert s.queue_pending[-1]["type"] == "qq", "item must be persisted to queue_pending"
    _cleanup()


def test_enqueue_qq_reminder_live_worker_wake_only(monkeypatch):
    """QQ 消息入队：订阅 session 有活 worker → 只唤醒不 spawn。"""
    _cleanup()
    s = _setup_session("ses_sub")
    s.qq_subscriptions = {"group:123"}
    w = _setup_worker("ses_sub")
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id, worker_id="worker-new")

    monkeypatch.setattr(worker, "create_worker", fake_create)

    delivered = asyncio.run(
        worker.enqueue_qq_reminder("group", "123", text="hi")
    )
    assert delivered == 1
    assert spawned == [], "live worker must not be re-spawned"
    assert w.pending_signal.qsize() == 1
    _cleanup()


if __name__ == "__main__":
    test_global_watchdog_tick_spawns_for_pending_queue()
    test_global_watchdog_tick_skips_live_worker()
    test_global_watchdog_tick_skips_empty_queue()
    test_create_worker_dedup_reuses_live_worker()
    test_create_worker_concurrent_no_double_spawn()
    test_wake_worker_default_silent_when_dead()
    test_wake_worker_auto_spawn_when_dead()
    test_wake_worker_auto_spawn_registered_dead_process()
    test_wake_worker_auto_spawn_skips_live_worker()
    test_wake_worker_auto_spawn_failure_swallowed()
    test_enqueue_qq_reminder_auto_spawns_for_dead_session()
    test_enqueue_qq_reminder_live_worker_wake_only()
    print("\n=== ALL GLOBAL WATCHDOG TESTS PASSED ===")
