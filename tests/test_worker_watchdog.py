"""Tests for watchdog (D1): timeout kill + idle reclamation + held skip.

_watchdog is a `while True` loop, so tests launch it as a task, let it tick
a few times, then cancel it. We shrink the tick + thresholds so checks fire
without waiting real time.
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


def _cleanup():
    worker.workers.clear()
    worker._result_waiters.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid="ses_test"):
    s = _sess.Session(id=sid, name="test", model="test-model")
    _sess._cache[sid] = s
    return s


def _setup_worker(session_id, status="idle", last_activity=None):
    w = worker.Worker(
        worker_id="worker-test",
        session_id=session_id,
        adapter=CbcAdapter(),
        status=status,
        process=AsyncMock(),
        pending_signal=asyncio.Queue(),
        _replaying=False,
        last_activity=last_activity if last_activity is not None else time.monotonic(),
    )
    w.process.returncode = None
    worker.workers[w.worker_id] = w
    return w


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
    """running worker silent past timeout → killed."""
    _cleanup()
    killed = []

    async def fake_kill(worker_id):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 0.1
    worker._WORKER_IDLE_SEC = 999
    w = _setup_worker(_setup_session().id, status="running", last_activity=0.0)

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [w.worker_id], f"running worker not killed: {killed}"
    _cleanup()


def test_active_running_worker_not_killed():
    """running worker with recent activity (last_activity=now) → not killed."""
    _cleanup()
    killed = []

    async def fake_kill(worker_id):
        killed.append(worker_id)
        return None

    worker._WATCHDOG_TICK_SEC = 0.01
    worker._WORKER_TIMEOUT_SEC = 0.1
    worker._WORKER_IDLE_SEC = 999
    w = _setup_worker(_setup_session().id, status="running")  # last_activity=now

    orig_kill = worker.kill_worker
    worker.kill_worker = fake_kill
    try:
        asyncio.run(_run_watchdog(w))
    finally:
        worker.kill_worker = orig_kill
        worker._WATCHDOG_TICK_SEC = 30.0
        worker._WORKER_TIMEOUT_SEC = 300.0
        worker._WORKER_IDLE_SEC = 300.0

    assert killed == [], f"active running worker wrongly killed: {killed}"
    _cleanup()


def test_idle_worker_reclaimed():
    """idle worker idle past idle_sec → killed."""
    _cleanup()
    killed = []

    async def fake_kill(worker_id):
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


def test_held_worker_skipped():
    """held (takeover) worker never reclaimed by watchdog."""
    _cleanup()
    killed = []

    async def fake_kill(worker_id):
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

    async def fake_kill(worker_id):
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
    to the read timeout in _consumer_mcp).
    """
    _cleanup()
    killed = []

    async def fake_kill(worker_id):
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

    async def fake_kill(worker_id):
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
    worker._kill_process_tree = AsyncMock()
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


if __name__ == "__main__":
    test_timeout_kills_running_worker()
    test_active_running_worker_not_killed()
    test_idle_worker_reclaimed()
    test_held_worker_skipped()
    test_watchdog_exits_when_worker_removed()
    test_mcp_idle_worker_reclaimed()
    test_mcp_running_worker_not_timeout_killed()
    test_watchdog_self_cancel_regression()
    print("\n=== ALL WATCHDOG TESTS PASSED ===")
