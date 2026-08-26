"""Tests for global watchdog (立项 4.4) + spawn 防重复 (立项 4.5).

- _global_watchdog_tick: queue_pending 非空 && 无活 worker → create_worker 恢复
- create_worker dedup: 已有活 worker → 复用不重复建
- create_worker 并发（同一 session）→ per-session lock 串行化，只 spawn 一次
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    worker._spawn_locks.clear()  # per-session asyncio.Lock 绑定事件循环，测试间需清掉
    _sess._cache.clear()
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


if __name__ == "__main__":
    test_global_watchdog_tick_spawns_for_pending_queue()
    test_global_watchdog_tick_skips_live_worker()
    test_global_watchdog_tick_skips_empty_queue()
    test_create_worker_dedup_reuses_live_worker()
    test_create_worker_concurrent_no_double_spawn()
    print("\n=== ALL GLOBAL WATCHDOG TESTS PASSED ===")
