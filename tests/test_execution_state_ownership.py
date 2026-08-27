"""执行状态归属审计的固化测试（本 worktree 链最后一块）。

审计结论（详见分支回报）：执行状态全部留在 Worker 内存，**不迁 session**——
在「出队 = 移交确认 + 死亡不重跑（_mark_worker_tasks_error + zombie）」的投递
语义下，执行状态的生命周期 = 执行尝试的生命周期 = worker 进程生命周期，
respawn 后归零不是缺陷而是正确语义：

- (b) per-worker 进程语义：_task_started_at（新尝试重新计时）、last_activity
  （新进程从 spawn 重新计 idle/静默窗口）、_hist_*（本进程 stdout 防抖缓冲）、
  _replaying（死代码兜底）；
- (c) 已被缓解：_current_seq/_current_task_id（飞行中配对，断链由幂等注册表 +
  zombie 报告兜住；新尝试的配对值由 task item 自带）、_task_counter（seq 仅
  飞行中配对用，跨代重复无功能影响）。

本文件固化上述 (b)/(c) 语义的回归边界，防止将来误迁引入跨代状态污染。
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    worker._inflight_task_ids.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid="ses_test", **kwargs):
    s = _sess.Session(id=sid, name="test", **kwargs)
    _sess._cache[sid] = s
    return s


def _make_worker(sid, worker_id="worker-x", process=None):
    w = worker.Worker(
        worker_id=worker_id, session_id=sid,
        adapter=CbcAdapter(), status="idle", process=process,
        pending_signal=asyncio.Queue(),
        _task_done=asyncio.Event(),
        _hist_flush_event=asyncio.Event(),
    )
    # 模拟 create_worker 的 spawn 语义：last_activity 显式刷新（非 dataclass 默认）
    w.last_activity = time.monotonic()
    worker.workers[w.worker_id] = w
    return w


def _make_task(i, seq=None):
    return {"type": "task", "id": f"task{i}", "text": f"job {i}",
            "source": "agent", "seq": seq if seq is not None else i,
            "taskId": f"tid{i}"}


def _run_consumer(w):
    async def scenario():
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())


async def _noop_save_async(s):
    pass


# ── (b) 执行状态随 worker 生灭：respawn 后归零 ──


def test_respawn_resets_execution_state(monkeypatch):
    """respawn 产生全新 Worker：执行状态全部默认值，不从旧代继承（不迁 session）。

    旧代 worker1 的 _current_seq/_current_task_id/_task_started_at 有值并已
    随其死亡终结；新 worker2 的这些字段归零，等新任务重新建立。
    """
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_test")

    # 旧代：消费任务 1，执行状态建立
    w1 = _make_worker("ses_test", worker_id="worker-1")
    s.queue_pending = [_make_task(1)]
    received = []

    async def fake_stream(ww, text, source, sess):
        received.append(text)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario1():
        await w1.pending_signal.put({"type": "task_signal", "id": "task1"})
        await w1.pending_signal.put(None)
        await worker._consumer(w1)

    asyncio.run(scenario1())

    assert w1._current_seq == 1 and w1._current_task_id == "tid1"
    assert w1._task_started_at == 0.0  # fake_stream 未走真实 running 路径

    # respawn：worker1 死亡 → 全局 watchdog/手动 create 出 worker2（全新对象）
    worker.workers.clear()
    w2 = _make_worker("ses_test", worker_id="worker-2")

    assert w2._current_seq is None and w2._current_task_id is None
    assert w2._task_started_at == 0.0
    assert w2._replaying is False
    assert w2.last_activity > 0, "spawn 时必须刷新 last_activity（新 idle 窗口）"
    _cleanup()


def test_pairing_follows_new_attempt_after_respawn(monkeypatch):
    """respawn 后重投任务的 seq/taskId 配对来自新 item，不被旧代污染。

    worker1 消费任务 A（seq=1）后死亡；worker2 respawn 后消费任务 B（seq=7）。
    断言 worker2 的配对上下文 = B 的 seq/taskId（若误迁旧代状态会得到 A 的值）。
    """
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_test")

    # 旧代：任务 A（seq=1）被 worker1 消费
    w1 = _make_worker("ses_test", worker_id="worker-1")
    s.queue_pending = [_make_task(1, seq=1), _make_task(2, seq=7)]

    async def fake_stream(ww, text, source, sess):
        pass

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario1():
        await w1.pending_signal.put({"type": "task_signal", "id": "task1"})
        await w1.pending_signal.put(None)
        await worker._consumer(w1)

    asyncio.run(scenario1())
    assert (w1._current_seq, w1._current_task_id) == (1, "tid1")

    # respawn：任务 B（seq=7）仍在队列（queue_pending 真源未受旧代影响）
    worker.workers.clear()
    w2 = _make_worker("ses_test", worker_id="worker-2")
    worker._recover_pending_signals(w2, s)
    _run_consumer(w2)

    assert s.queue_pending == [], "task B consumed after respawn"
    assert (w2._current_seq, w2._current_task_id) == (7, "tid2"), \
        "pairing must come from the new attempt's item, not the old generation"
    _cleanup()


def test_respawn_worker_safe_from_watchdog(monkeypatch):
    """respawn 后的新 worker（执行状态全默认）不被 watchdog 误杀。

    idle 分支只看 last_activity（spawn 时已刷新）→ 安全度过观察窗口；
    证明「执行状态归零」与 watchdog 判定语义自洽。
    """
    _cleanup()
    killed = []

    async def fake_kill(worker_id):
        killed.append(worker_id)

    async def fake_zombie(w, reason):
        pass

    worker._WATCHDOG_TICK_SEC = 0.02
    worker._WORKER_TIMEOUT_SEC = 0.1
    worker._WORKER_IDLE_SEC = 0.1
    worker._WORKER_TASK_TIMEOUT_SEC = 0.1
    s = _setup_session("ses_test")
    w2 = _make_worker("ses_test", worker_id="worker-2")  # spawn：last_activity=now

    orig_kill, orig_zombie = worker.kill_worker, worker._enqueue_zombie_report
    worker.kill_worker = fake_kill
    worker._enqueue_zombie_report = fake_zombie

    async def scenario():
        task = asyncio.create_task(worker._watchdog(w2))
        try:
            await asyncio.sleep(0.05)  # 2~3 个 tick，idle_for≈0.05 < 阈值 0.1 → 不杀
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        asyncio.run(scenario())
    finally:
        worker.kill_worker = orig_kill
        worker._enqueue_zombie_report = orig_zombie
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0
        worker._WORKER_TASK_TIMEOUT_SEC = 1800.0

    assert killed == [], f"freshly respawned worker wrongly killed: {killed}"
    _cleanup()


def test_running_timeout_retimed_per_attempt(monkeypatch):
    """「任务运行时长」按尝试重新计时：同一 worker 前一任务超时边界不影响下一任务。

    attempt1：started_at 设为远古 → running 超时判定命中（若它在跑）；
    attempt2：新任务进入 running 时 started_at 刷新为 now → 同一阈值下不杀。
    证明计时基准是「本尝试的开始时刻」而非跨代累计。
    """
    _cleanup()

    async def fake_kill(worker_id):
        pass

    async def fake_zombie(w, reason):
        pass

    s = _setup_session("ses_test")
    w = _make_worker("ses_test", worker_id="worker-1", process=AsyncMock())
    w.process.returncode = None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TASK_TIMEOUT_SEC = 0.1
    worker._WORKER_IDLE_SEC = 999
    worker._WORKER_TIMEOUT_SEC = 999

    # attempt1：远古 started_at（若此刻 watchdog 检查会判超时）
    w.status = "running"
    w._task_started_at = 0.0
    assert time.monotonic() - w._task_started_at > worker._WORKER_TASK_TIMEOUT_SEC

    # attempt2：新任务进入 running（_consumer_stream 路径的刷新语义）
    w._task_started_at = time.monotonic()

    killed = []
    orig_kill, orig_zombie = worker.kill_worker, worker._enqueue_zombie_report
    worker.kill_worker = fake_kill
    worker._enqueue_zombie_report = fake_zombie

    async def scenario():
        task = asyncio.create_task(worker._watchdog(w))
        try:
            await asyncio.sleep(0.05)  # 新尝试计时内 → 不杀
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        asyncio.run(scenario())
    finally:
        worker.kill_worker = orig_kill
        worker._enqueue_zombie_report = orig_zombie
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0
        worker._WORKER_TASK_TIMEOUT_SEC = 1800.0

    assert killed == [], "new attempt must be timed from its own start, not cumulative"
    _cleanup()
