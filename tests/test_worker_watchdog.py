"""Tests for watchdog (D1): timeout kill + idle reclamation + held skip.

_watchdog is a `while True` loop, so tests launch it as a task, let it tick
a few times, then cancel it. We shrink the tick + thresholds so checks fire
without waiting real time.
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core import config as _config
from packages.core.adapters import CbcAdapter


def _cleanup():
    worker.workers.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _use_kill_report_config(monkeypatch, tmp_path, ui):
    """Point the config module at a temp config.json carrying ``ui``.

    ``ui`` is written verbatim, so a missing ``reportWorkerKillErrors`` key
    exercises the old-config (default-off) path through the real
    ``load_config()`` deep merge.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"ui": ui}), encoding="utf-8")
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg_path)


def _setup_session(sid="ses_test"):
    s = _sess.Session(id=sid, name="test", model="test-model")
    _sess._cache[sid] = s
    return s


def _setup_worker(session_id, status="idle", last_activity=None, task_started_at=None):
    w = worker.Worker(
        worker_id="worker-test",
        session_id=session_id,
        adapter=CbcAdapter(),
        status=status,
        process=AsyncMock(),
        pending_signal=asyncio.Queue(),
        _replaying=False,
        last_activity=last_activity if last_activity is not None else time.monotonic(),
        _task_started_at=task_started_at if task_started_at is not None else time.monotonic(),
    )
    w.process.returncode = None
    worker.workers[w.worker_id] = w
    return w


async def _stop_fake_process_tree(w):
    """Supply the exit fact that the real tree-stop helper must confirm.

    These tests keep the real kill/watchdog control flow; only OS signals are
    stubbed. A no-op stub leaves returncode=None and correctly fails the new
    stop-confirmation gate instead of representing a successful fake stop.
    """
    for process in (w.process, w._mcp_proc):
        if process is not None:
            process.returncode = -9


async def _run_watchdog(w, ticks=5):
    """Run the watchdog loop for `ticks` ticks, then cancel."""
    task = asyncio.create_task(worker._watchdog(w))
    try:
        await asyncio.sleep(ticks * worker._WATCHDOG_TICK_SEC + 0.02)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── tests ──

def test_timeout_kills_running_worker():
    """running worker past task-duration timeout (task_started_at old) → killed."""
    _cleanup()
    killed = []

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TASK_TIMEOUT_SEC = 0.1
    worker._WORKER_IDLE_SEC = 999
    w = _setup_worker(_setup_session().id, status="running", task_started_at=0.0)

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_TASK_TIMEOUT_SEC = 1800.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [w.worker_id], f"running worker not killed: {killed}"
    _cleanup()


def test_active_running_worker_not_killed(monkeypatch):
    """Below task timeout, stale stdout does not kill an active running Worker.

    Keep the original 100ms threshold, but control task elapsed time separately
    from event-loop/OS scheduling. A slow CI tick must not silently turn this
    below-threshold scenario into an expired task.
    """
    _cleanup()
    killed = []
    clock = {"now": 0.0, "reads": 0}

    def monotonic():
        clock["reads"] += 1
        return clock["now"]

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)
        return None

    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(worker, "_WATCHDOG_TICK_SEC", .01)
    monkeypatch.setattr(worker, "_WORKER_TASK_TIMEOUT_SEC", .1)
    monkeypatch.setattr(worker, "_WORKER_TIMEOUT_SEC", .1)
    monkeypatch.setattr(worker, "_WORKER_IDLE_SEC", 999)
    monkeypatch.setattr(worker, "kill_worker", fake_kill)
    w = _setup_worker(_setup_session().id, status="running",
                      task_started_at=clock["now"], last_activity=-1000.0)
    clock["now"] = .05
    try:
        asyncio.run(_run_watchdog(w))
        assert clock["reads"] >= 2, "watchdog must evaluate task age"
        assert killed == [], f"active running worker wrongly killed: {killed}"
    finally:
        _cleanup()


@pytest.mark.parametrize("elapsed,expected_kill", [(.099, False), (.1, False), (.101, True)])
def test_running_watchdog_original_task_timeout_boundary(monkeypatch, elapsed, expected_kill):
    _cleanup()
    killed = []

    async def fake_kill(worker_id, *, recover=False):
        killed.append((worker_id, recover))

    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: elapsed))
    monkeypatch.setattr(worker, "_WATCHDOG_TICK_SEC", .01)
    monkeypatch.setattr(worker, "_WORKER_TASK_TIMEOUT_SEC", .1)
    monkeypatch.setattr(worker, "_WORKER_TIMEOUT_SEC", .1)
    monkeypatch.setattr(worker, "_WORKER_IDLE_SEC", 999)
    monkeypatch.setattr(worker, "kill_worker", fake_kill)
    w = _setup_worker(_setup_session().id, status="running",
                      task_started_at=0.0, last_activity=-1000.0)
    try:
        asyncio.run(_run_watchdog(w))
        assert killed == ([(w.worker_id, True)] if expected_kill else [])
    finally:
        _cleanup()


def test_queued_worker_timeout_killed():
    """queued worker silent past timeout_sec → killed (queued 静默超时保留)."""
    _cleanup()
    killed = []

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 0.1
    worker._WORKER_TASK_TIMEOUT_SEC = 999
    worker._WORKER_IDLE_SEC = 999
    w = _setup_worker(_setup_session().id, status="queued", last_activity=0.0)

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_TASK_TIMEOUT_SEC = 1800.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [w.worker_id], f"queued worker not killed: {killed}"
    _cleanup()


def test_idle_worker_reclaimed():
    """idle worker idle past idle_sec → killed."""
    _cleanup()
    killed = []

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 999
    worker._WORKER_IDLE_SEC = 0.1
    w = _setup_worker(_setup_session().id, status="idle", last_activity=0.0)

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [w.worker_id], f"idle worker not reclaimed: {killed}"
    _cleanup()


def test_idle_worker_with_pending_work_not_reclaimed():
    """idle 瞬间仍有队列任务时，不得抢在 consumer 前回收 worker。"""
    _cleanup()
    killed = []
    s = _setup_session()
    s.queue_pending = [{"type": "task", "id": "pending-1", "text": "job"}]

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 999
    worker._WORKER_TASK_TIMEOUT_SEC = 999
    worker._WORKER_IDLE_SEC = 0.1
    w = _setup_worker(s.id, status="idle", last_activity=0.0)

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w, ticks=4))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_TASK_TIMEOUT_SEC = 1800.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [], f"worker with pending work was reclaimed: {killed}"
    _cleanup()


def test_held_worker_skipped():
    """held (takeover) worker never reclaimed by watchdog."""
    _cleanup()
    killed = []

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 0.1
    worker._WORKER_IDLE_SEC = 0.1
    # held + very old last_activity — must still be skipped
    w = _setup_worker(_setup_session().id, status="held", last_activity=0.0)

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [], f"held worker wrongly killed: {killed}"
    _cleanup()


def test_watchdog_exits_when_worker_removed():
    """watchdog stops killing once the worker is gone from the dict."""
    _cleanup()
    killed = []

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 0.1
    worker._WORKER_IDLE_SEC = 999
    s = _setup_session()
    w = _setup_worker(s.id, status="running", last_activity=0.0)
    # Worker removed externally (as kill_worker would do) before watchdog runs
    worker.workers.pop(w.worker_id, None)

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0

    # watchdog saw worker absent on first tick → returned, no kill
    assert killed == [], f"removed worker wrongly killed again: {killed}"
    _cleanup()


def test_mcp_idle_worker_reclaimed():
    """MCP one-shot worker (process=None) idle past idle_sec → killed.

    MCP watchdog does idle reclamation but never timeout (running is left
    to the read timeout in _consumer_oneshot).
    """
    _cleanup()
    killed = []

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 0.1
    worker._WORKER_IDLE_SEC = 0.1
    # MCP mode: process=None
    w = _setup_worker(_setup_session().id, status="idle", last_activity=0.0)
    w.process = None

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [w.worker_id], f"MCP idle worker not reclaimed: {killed}"
    _cleanup()


def test_mcp_running_worker_not_timeout_killed():
    """MCP one-shot worker running past timeout is NOT killed (read-timeout owns it)."""
    _cleanup()
    killed = []

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 0.1
    worker._WORKER_IDLE_SEC = 999
    # MCP running, very old last_activity (would exceed timeout if stream)
    w = _setup_worker(_setup_session().id, status="running", last_activity=0.0)
    w.process = None

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [], f"MCP running worker wrongly timeout-killed: {killed}"
    _cleanup()


# ── watchdog zombie 报告（B2）──

async def _noop_save_async(s):
    pass


def _setup_managed_pair():
    """被管+订阅的 session 对：(child managed_by mgr, mgr 订阅 child)。"""
    child = _setup_session(sid="ses_child")
    child.managed_by = "ses_mgr"
    mgr = _setup_session(sid="ses_mgr")
    mgr.report_subscriptions = {"ses_child"}
    return child, mgr


def test_watchdog_task_timeout_reports_zombie(monkeypatch):
    """watchdog running 卡死超时 kill → 被管+订阅 session 收到 zombie 报告。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, mgr = _setup_managed_pair()
    killed = []

    async def no_wake(_session_id):
        return None

    # Keep the assertion focused on report enqueue; under receipt-ack semantics
    # an immediately recovered manager may consume the report in the same tick.
    monkeypatch.setattr(worker, "_wake_worker", no_wake)

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TASK_TIMEOUT_SEC = 0.1
    worker._WORKER_IDLE_SEC = 999
    w = _setup_worker("ses_child", status="running", task_started_at=0.0)

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_TASK_TIMEOUT_SEC = 1800.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [w.worker_id], f"running worker not killed: {killed}"
    assert len(mgr.queue_pending) == 1, f"zombie report missing: {mgr.queue_pending}"
    r = mgr.queue_pending[0]
    assert r["status"] == "error" and r["type"] == "zombie"
    assert "task timeout" in r["result"]
    _cleanup()


def test_explicit_kill_running_worker_reports_inflight_task(monkeypatch, tmp_path):
    """An explicit kill must leave one durable report for the active task."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _use_kill_report_config(monkeypatch, tmp_path, {"reportWorkerKillErrors": True})

    async def no_wake(_session_id):
        return None

    monkeypatch.setattr(worker, "_wake_worker", no_wake)
    _, mgr = _setup_managed_pair()
    w = _setup_worker("ses_child", status="running")
    w._current_task_id = "task-killed"
    worker._task_status["task-killed"] = {
        "status": "pending", "workerId": w.worker_id,
    }
    monkeypatch.setattr(worker, "_kill_process_tree", AsyncMock(side_effect=_stop_fake_process_tree))
    monkeypatch.setattr(worker, "_kill_takeover_terminal", AsyncMock())

    result = asyncio.run(worker.kill_worker(
        w.worker_id, report_abnormal=True,
    ))

    assert result is None
    assert len(mgr.queue_pending) == 1
    report = mgr.queue_pending[0]
    assert report["type"] == "zombie"
    assert report["kind"] == "report"
    assert report["status"] == "error"
    assert report["result"] == "worker died: worker killed"
    assert report["source"] == "report"
    assert report["sourceSessionId"] == "ses_child"
    assert report["sessionId"] == "ses_child"
    assert report["taskId"] == "task-killed"
    assert report["workerId"] == w.worker_id
    assert report["deliveryState"] == "queued"
    assert mgr.queue_delivery_ledger[report["queueItemId"]]["type"] == "zombie"
    assert worker._task_status["task-killed"]["status"] == "error"
    _cleanup()


def test_explicit_kill_does_not_duplicate_existing_zombie_report(monkeypatch, tmp_path):
    """A watchdog report followed by explicit cleanup remains single-shot."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _use_kill_report_config(monkeypatch, tmp_path, {"reportWorkerKillErrors": True})

    async def no_wake(_session_id):
        return None

    monkeypatch.setattr(worker, "_wake_worker", no_wake)
    _, mgr = _setup_managed_pair()
    w = _setup_worker("ses_child", status="running")
    w._current_task_id = "task-killed"
    monkeypatch.setattr(worker, "_kill_process_tree", AsyncMock(side_effect=_stop_fake_process_tree))
    monkeypatch.setattr(worker, "_kill_takeover_terminal", AsyncMock())

    async def scenario():
        await worker._enqueue_zombie_report(w, "task timeout")
        await worker.kill_worker(w.worker_id, report_abnormal=True)
        # Model the late EOF path after kill has started.
        await worker._enqueue_zombie_report(w, "process exited (returncode=1)")

    asyncio.run(scenario())

    assert len(mgr.queue_pending) == 1
    assert "task timeout" in mgr.queue_pending[0]["result"]
    _cleanup()


def test_explicit_kill_idle_worker_keeps_no_zombie_report(monkeypatch):
    """Explicitly killing an idle worker retains normal reclaim semantics."""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    async def no_wake(_session_id):
        return None

    monkeypatch.setattr(worker, "_wake_worker", no_wake)
    _, mgr = _setup_managed_pair()
    w = _setup_worker("ses_child", status="idle")
    monkeypatch.setattr(worker, "_kill_process_tree", AsyncMock(side_effect=_stop_fake_process_tree))
    monkeypatch.setattr(worker, "_kill_takeover_terminal", AsyncMock())

    asyncio.run(worker.kill_worker(w.worker_id, report_abnormal=True))

    assert mgr.queue_pending == []
    _cleanup()


def test_explicit_kill_default_config_no_zombie_report(monkeypatch, tmp_path):
    """旧配置缺省开关 → 用户 Kill 不报 zombie；kill 执行与任务清理不变。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    async def no_wake(_session_id):
        return None

    monkeypatch.setattr(worker, "_wake_worker", no_wake)
    _use_kill_report_config(monkeypatch, tmp_path, {})  # 缺 reportWorkerKillErrors 键
    _, mgr = _setup_managed_pair()
    w = _setup_worker("ses_child", status="running")
    w._current_task_id = "task-killed"
    worker._task_status["task-killed"] = {
        "status": "pending", "workerId": w.worker_id,
    }
    monkeypatch.setattr(worker, "_kill_process_tree", AsyncMock(side_effect=_stop_fake_process_tree))
    monkeypatch.setattr(worker, "_kill_takeover_terminal", AsyncMock())

    result = asyncio.run(worker.kill_worker(w.worker_id, report_abnormal=True))

    assert result is None
    assert mgr.queue_pending == []  # 无用户 Kill 报告
    assert w.worker_id not in worker.workers  # kill 仍真实执行
    assert worker._task_status["task-killed"]["status"] == "error"  # 任务清理不变
    _cleanup()


def test_explicit_kill_report_disabled_no_zombie_report(monkeypatch, tmp_path):
    """显式关闭 → 用户 Kill running/queued Worker 均不报 zombie。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    async def no_wake(_session_id):
        return None

    monkeypatch.setattr(worker, "_wake_worker", no_wake)
    _use_kill_report_config(monkeypatch, tmp_path, {"reportWorkerKillErrors": False})
    _, mgr = _setup_managed_pair()
    w = _setup_worker("ses_child", status="queued")
    w._current_task_id = "task-queued"
    worker._task_status["task-queued"] = {
        "status": "pending", "workerId": w.worker_id,
    }
    monkeypatch.setattr(worker, "_kill_process_tree", AsyncMock(side_effect=_stop_fake_process_tree))
    monkeypatch.setattr(worker, "_kill_takeover_terminal", AsyncMock())

    asyncio.run(worker.kill_worker(w.worker_id, report_abnormal=True))

    assert mgr.queue_pending == []
    _cleanup()


def test_explicit_kill_report_enabled_reports_zombie(monkeypatch, tmp_path):
    """显式开启 → 用户 Kill 恢复 zombie 报告（现行为）。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    async def no_wake(_session_id):
        return None

    monkeypatch.setattr(worker, "_wake_worker", no_wake)
    _use_kill_report_config(monkeypatch, tmp_path, {"reportWorkerKillErrors": True})
    _, mgr = _setup_managed_pair()
    w = _setup_worker("ses_child", status="running")
    w._current_task_id = "task-killed"
    worker._task_status["task-killed"] = {
        "status": "pending", "workerId": w.worker_id,
    }
    monkeypatch.setattr(worker, "_kill_process_tree", AsyncMock(side_effect=_stop_fake_process_tree))
    monkeypatch.setattr(worker, "_kill_takeover_terminal", AsyncMock())

    asyncio.run(worker.kill_worker(w.worker_id, report_abnormal=True))

    assert len(mgr.queue_pending) == 1
    assert mgr.queue_pending[0]["type"] == "zombie"
    assert mgr.queue_pending[0]["result"] == "worker died: worker killed"
    _cleanup()


def test_user_kill_switch_off_leaves_abnormal_death_reports(monkeypatch, tmp_path):
    """开关关闭只抑制用户 Kill 报告；异常死亡（EOF 路径）仍按原语义上报。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    async def no_wake(_session_id):
        return None

    monkeypatch.setattr(worker, "_wake_worker", no_wake)
    _use_kill_report_config(monkeypatch, tmp_path, {"reportWorkerKillErrors": False})
    _, mgr = _setup_managed_pair()
    w = _setup_worker("ses_child", status="running")

    asyncio.run(worker._enqueue_zombie_report(w, "process exited (returncode=1)"))

    assert len(mgr.queue_pending) == 1
    assert mgr.queue_pending[0]["result"] == "worker died: process exited (returncode=1)"
    _cleanup()


def test_session_kill_entry_respects_report_switch(monkeypatch, tmp_path):
    """session kill 入口（web session kill / MCP agent_kill 共用）遵循开关。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    async def no_wake(_session_id):
        return None

    monkeypatch.setattr(worker, "_wake_worker", no_wake)
    _use_kill_report_config(monkeypatch, tmp_path, {})  # 缺省 → 关
    _, mgr = _setup_managed_pair()
    w = _setup_worker("ses_child", status="queued")
    w._current_task_id = "task-queued"
    worker._task_status["task-queued"] = {
        "status": "pending", "workerId": w.worker_id,
    }
    monkeypatch.setattr(worker, "_kill_process_tree", AsyncMock(side_effect=_stop_fake_process_tree))
    monkeypatch.setattr(worker, "_kill_takeover_terminal", AsyncMock())

    result = asyncio.run(worker.kill_session_worker("ses_child", report_abnormal=True))

    assert result is w  # 成功时返回被 kill 的 Worker（无错误串）
    assert mgr.queue_pending == []
    assert w.worker_id not in worker.workers
    assert worker._task_status["task-queued"]["status"] == "error"
    _cleanup()


def test_watchdog_queued_timeout_reports_zombie(monkeypatch):
    """watchdog queued 静默超时 kill → zombie 报告。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, mgr = _setup_managed_pair()
    killed = []

    async def no_wake(_session_id):
        return None

    monkeypatch.setattr(worker, "_wake_worker", no_wake)

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 0.1
    worker._WORKER_TASK_TIMEOUT_SEC = 999
    worker._WORKER_IDLE_SEC = 999
    w = _setup_worker("ses_child", status="queued", last_activity=0.0)

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_TASK_TIMEOUT_SEC = 1800.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [w.worker_id], f"queued worker not killed: {killed}"
    assert len(mgr.queue_pending) == 1, f"zombie report missing: {mgr.queue_pending}"
    assert mgr.queue_pending[0]["type"] == "zombie"
    assert "queued timeout" in mgr.queue_pending[0]["result"]
    _cleanup()


def test_watchdog_idle_reclaim_no_zombie(monkeypatch):
    """watchdog idle 回收 → 不报 zombie（正常完成后的回收）。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _, mgr = _setup_managed_pair()
    killed = []

    async def fake_kill(worker_id, *, recover=False):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 999
    worker._WORKER_IDLE_SEC = 0.1
    w = _setup_worker("ses_child", status="idle", last_activity=0.0)

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [w.worker_id], f"idle worker not reclaimed: {killed}"
    assert mgr.queue_pending == [], "idle reclaim must NOT report zombie"
    _cleanup()


def test_watchdog_self_cancel_regression():
    """Watchdog must reclaim worker via REAL kill_worker (not a stub).

    Regression: kill_worker cancels the watchdog task, but when the watchdog
    itself called kill_worker, the self-cancel interrupted kill mid-way —
    process not killed, worker never popped from dict.
    """
    _cleanup()
    s = _setup_session()
    proc = AsyncMock()
    proc.returncode = None
    proc.pid = 99999
    w = worker.Worker(
        worker_id="worker-real",
        session_id=s.id,
        adapter=CbcAdapter(),
        status="idle",
        process=proc,
        pending_signal=asyncio.Queue(),
        _replaying=False,
        last_activity=0.0,  # very old → will exceed idle_sec
    )
    worker.workers[w.worker_id] = w

    # Use REAL kill_worker; stub only the process-tree kill to avoid real signals
    orig_kpt = worker._kill_process_tree
    orig_ktt = worker._kill_takeover_terminal
    worker._kill_process_tree = AsyncMock(side_effect=_stop_fake_process_tree)
    worker._kill_takeover_terminal = AsyncMock()

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 999
    worker._WORKER_IDLE_SEC = 0.1
    try:
        async def run():
            task = asyncio.create_task(worker._watchdog(w))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        asyncio.run(run())
    finally:
        worker._kill_process_tree = orig_kpt
        worker._kill_takeover_terminal = orig_ktt
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0

    assert w.worker_id not in worker.workers, \
        "watchdog self-cancel bug: worker not reclaimed via real kill_worker"
    print("PASS: watchdog reclaims via real kill_worker (self-cancel fixed)")
    _cleanup()


def test_consumer_oneshot_refreshes_last_activity_on_idle(monkeypatch, tmp_path):
    """M3 回归: _consumer_oneshot 置 idle 时必须刷新 last_activity。

    旧行为: MCP 任务全程不刷新 last_activity，任务耗时被算进 idle 时长，
    接近 timeout 的任务完成后 watchdog 下一 tick 就把它当空闲回收。
    修复后: 任务完成置 idle 时同步 last_activity = now。
    """
    _cleanup()
    s = _setup_session(sid="ses_mcp_idle")
    s.adapter_config = {}  # 无 mcp_servers → mcp_args 不写配置文件
    s.workdir = str(tmp_path)
    w = _setup_worker(s.id, status="running", last_activity=0.0)
    w.process = None  # MCP one-shot 模式

    result_line = (json.dumps({"type": "result", "result": "the answer"}) + "\n").encode()

    class _FakeStdout:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        async def read(self, n):
            if self._chunks:
                return self._chunks.pop(0)
            return b""

    class FakeMcpProc:
        def __init__(self):
            self._chunks = [result_line]
            self.stdout = _FakeStdout(self._chunks)
            self.returncode = 0
            self.pid = 12345

        async def wait(self):
            return 0

        def kill(self):
            self.returncode = -1

    async def fake_spawn(*args, **kwargs):
        return FakeMcpProc()

    async def fake_save(sess):
        return None

    monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)
    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(_sess, "save_async", fake_save)

    async def run():
        await worker._consumer_oneshot(w, "hello", "agent", s)

    asyncio.run(run())

    assert w.status == "idle", f"expected idle, got {w.status}"
    assert w.last_activity > 0, f"last_activity not refreshed: {w.last_activity}"
    print("PASS: _consumer_oneshot refreshes last_activity on idle")
    _cleanup()


def test_idle_reclaim_with_real_stdout_loop():
    """回归（252c41d）：stdout 读超时分支不得刷新 last_activity。

    真实组合：_read_stdout 循环（哑 stdout 永久静默，持续走 read timeout
    分支）+ _watchdog，复现生产 spawn 后的并发结构。
    旧行为：每 60s 读超时刷新一次 last_activity → idle_for 永远到不了
    阈值 → idle 回收永不触发（且 queued 静默超时一并失效）。
    修复后：last_activity 只由真实输出刷新；idle 超过 idle_sec 即被
    真 kill_worker 回收。
    """
    _cleanup()
    s = _setup_session()

    class _HangingStdout:
        """永不产出数据的哑 stdout：read 挂起直到被 wait_for 取消。"""

        async def read(self, n):
            await asyncio.Event().wait()  # 无 set 者 → 只能靠超时/取消离开

    class FakeProc:
        def __init__(self):
            self.stdout = _HangingStdout()
            self.returncode = None
            self.pid = 424242

    w = worker.Worker(
        worker_id="worker-stdout-loop",
        session_id=s.id,
        adapter=CbcAdapter(),
        status="idle",
        process=FakeProc(),
        pending_signal=asyncio.Queue(),
        _replaying=False,
        last_activity=time.monotonic(),  # 刚进入 idle（而非远古时间戳）
    )
    worker.workers[w.worker_id] = w

    # Real kill_worker；仅 stub 进程树/终端 kill（同 self-cancel 回归测试）
    orig_kpt = worker._kill_process_tree
    orig_ktt = worker._kill_takeover_terminal
    worker._kill_process_tree = AsyncMock(side_effect=_stop_fake_process_tree)
    worker._kill_takeover_terminal = AsyncMock()

    worker._STDOUT_READ_TIMEOUT_SEC = 0.01  # 哑 stdout 立刻走读超时分支
    worker._WATCHDOG_TICK_SEC = 0.02
    worker._WORKER_TIMEOUT_SEC = 999
    worker._WORKER_IDLE_SEC = 0.1

    la_before = w.last_activity
    try:
        async def run():
            # 生产组合：spawn 时同时启动 stdout 循环与 watchdog
            w._stdout_task = asyncio.create_task(worker._read_stdout(w))
            wd = asyncio.create_task(worker._watchdog(w))
            await asyncio.sleep(0.5)  # 覆盖 ≥25 个 watchdog tick、≥50 次读超时
            assert w.worker_id not in worker.workers, (
                "idle worker with silent-but-live stdout loop was not reclaimed "
                "(read-timeout branch must NOT refresh last_activity)"
            )

        asyncio.run(run())
    finally:
        if w._stdout_task:
            w._stdout_task.cancel()
        worker._kill_process_tree = orig_kpt
        worker._kill_takeover_terminal = orig_ktt
        worker._STDOUT_READ_TIMEOUT_SEC = 60.0
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0

    # 读取循环全程未刷新活性时间戳（刷新与否直接决定回收能否触发）
    assert w.last_activity == la_before, (
        f"last_activity was refreshed by the silent-stdout loop: "
        f"{la_before} -> {w.last_activity}"
    )
    print("PASS: idle reclaim works with a real silent stdout loop")
    _cleanup()


if __name__ == "__main__":
    test_timeout_kills_running_worker()
    test_active_running_worker_not_killed()
    test_queued_worker_timeout_killed()
    test_idle_worker_reclaimed()
    test_held_worker_skipped()
    test_watchdog_exits_when_worker_removed()
    test_mcp_idle_worker_reclaimed()
    test_mcp_running_worker_not_timeout_killed()
    test_watchdog_task_timeout_reports_zombie()
    test_explicit_kill_running_worker_reports_inflight_task()
    test_explicit_kill_does_not_duplicate_existing_zombie_report()
    test_explicit_kill_idle_worker_keeps_no_zombie_report()
    test_watchdog_queued_timeout_reports_zombie()
    test_watchdog_idle_reclaim_no_zombie()
    test_watchdog_self_cancel_regression()
    test_consumer_oneshot_refreshes_last_activity_on_idle()
    test_idle_reclaim_with_real_stdout_loop()
    print("\n=== ALL WATCHDOG TESTS PASSED ===")
