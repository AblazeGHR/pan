"""Verification for backend perf / realtime optimizations.

A1: streaming-block debounced save (500ms window / block-count cap) + flush on result
A3: idle status broadcast on result processing
A4: broadcast() parallel sends via asyncio.gather (slow client only times itself out)
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _make_event(event_type: str, **fields) -> bytes:
    return (json.dumps({"type": event_type, **fields}) + "\n").encode("utf-8")


def _assistant_event(text: str = None) -> bytes:
    content = []
    if text:
        content.append({"type": "text", "text": text})
    return _make_event("assistant", message={"role": "assistant", "content": content})


def _result_event(result: str = "ok") -> bytes:
    return _make_event("result", result=result, is_error=False)


class MockProcess:
    def __init__(self, events, returncode=None):
        self._events = list(events)
        self.returncode = returncode
        self.pid = 1000
        self.stdin = AsyncMock()
        self.stdout = self

    async def read(self, n=-1):
        if self._events:
            return self._events.pop(0)
        return b""

    async def readline(self):
        if self._events:
            return self._events.pop(0)
        return b""


def _make_worker(sid="ses_t"):
    s = _sess.Session(id=sid, name="t", model="m")
    _sess._cache[sid] = s
    w = worker.Worker(
        worker_id="worker-t",
        session_id=sid,
        adapter=CbcAdapter(),
        status="idle",
        process=MagicMock(),
        pending_signal=asyncio.Queue(),
        _replaying=False,
        _hist_flush_event=asyncio.Event(),
    )
    worker.workers[w.worker_id] = w
    return s, w


# ── A1: 防抖落盘 ──

def test_stream_blocks_batch_into_single_save(monkeypatch):
    """3 个流式块在窗口内 append → 不逐块 save；result flush 恰好一次落盘 3 块。"""
    _cleanup()
    s, w = _make_worker()
    saved = []

    async def fake_save(sess):
        saved.append(len(sess.history))

    monkeypatch.setattr(_sess, "save_async", fake_save)

    async def scenario():
        for i in range(3):
            s.history.append({"role": "assistant", "content": f"b{i}"})
            worker._mark_history_dirty(w)
        assert saved == [], f"saved before flush: {saved}"
        await worker._flush_history_now(w)
        assert saved == [3], f"expected single save of 3 blocks, got {saved}"
        assert w._hist_dirty is False and w._hist_block_count == 0

    asyncio.run(scenario())
    _cleanup()


def test_stream_debounce_window_flushes(monkeypatch):
    """窗口超时（无新块）→ 防抖任务自动落盘。"""
    _cleanup()
    s, w = _make_worker()
    saved = []

    async def fake_save(sess):
        saved.append(len(sess.history))

    monkeypatch.setattr(_sess, "save_async", fake_save)
    old_debounce = worker._STREAM_SAVE_DEBOUNCE_SEC
    worker._STREAM_SAVE_DEBOUNCE_SEC = 0.1
    try:
        async def scenario():
            s.history.append({"role": "assistant", "content": "x"})
            worker._mark_history_dirty(w)
            assert saved == []
            await asyncio.sleep(0.25)  # > 防抖窗口
            assert saved == [1], f"debounce window should auto-flush, got {saved}"

        asyncio.run(scenario())
    finally:
        worker._STREAM_SAVE_DEBOUNCE_SEC = old_debounce
    _cleanup()


def test_stream_block_count_cap_flushes(monkeypatch):
    """累计块数达上限 → 提前唤醒防抖任务落盘（长流不至于久不落盘）。"""
    _cleanup()
    s, w = _make_worker()
    saved = []

    async def fake_save(sess):
        saved.append(len(sess.history))

    monkeypatch.setattr(_sess, "save_async", fake_save)
    old_cap = worker._STREAM_SAVE_MAX_BLOCKS
    worker._STREAM_SAVE_MAX_BLOCKS = 2
    try:
        async def scenario():
            s.history.append({"role": "assistant", "content": "a"})
            worker._mark_history_dirty(w)
            s.history.append({"role": "assistant", "content": "b"})
            worker._mark_history_dirty(w)  # 达上限 → 提前唤醒
            await asyncio.sleep(0.1)
            assert saved == [2], f"cap should flush at 2 blocks, got {saved}"

        asyncio.run(scenario())
    finally:
        worker._STREAM_SAVE_MAX_BLOCKS = old_cap
    _cleanup()


def test_result_flushes_debounced_blocks_through_read_stdout(monkeypatch):
    """集成：assistant 块防抖 + result 强制 flush → 全程恰好 1 次落盘。"""
    _cleanup()
    s, w = _make_worker()
    saved = []

    async def fake_save(sess):
        saved.append(len(sess.history))

    monkeypatch.setattr(_sess, "save_async", fake_save)
    w.process = MockProcess([_assistant_event(text="hi"), _result_event(result="hi")])

    asyncio.run(worker._read_stdout(w))

    assert s.history == [{"role": "assistant", "content": "hi"}], s.history
    assert s.last_result["status"] == "done"
    assert saved == [1], f"expected exactly 1 save (result flush), got {saved}"
    _cleanup()


# ── A3: idle 广播 ──

def test_idle_status_broadcast_on_result(monkeypatch):
    """result 处理置 idle → 广播 worker.status idle（mock _bcast 断言）。"""
    _cleanup()
    s, w = _make_worker()
    calls = []

    async def fake_bcast(data):
        calls.append(data)

    async def fake_save(sess):
        pass

    worker.set_broadcaster(fake_bcast)
    monkeypatch.setattr(_sess, "save_async", fake_save)
    w.process = MockProcess([_assistant_event(text="hi"), _result_event(result="hi")])

    asyncio.run(worker._read_stdout(w))

    idle = [c for c in calls
            if c.get("type") == "worker.status" and c.get("status") == "idle"]
    assert len(idle) == 1, f"expected idle status broadcast, got {calls}"
    assert idle[0]["workerId"] == w.worker_id
    assert idle[0]["sessionId"] == s.id
    _cleanup()


# ── A4: broadcast 并行 ──

def test_broadcast_sends_clients_in_parallel():
    import packages.web.server as srv
    srv.ws_clients.clear()
    srv.agent_clients.clear()
    srv.agent_subscriptions.clear()

    class SlowWS:
        def __init__(self, delay):
            self.delay = delay
            self.sent = []

        async def send_json(self, data):
            await asyncio.sleep(self.delay)
            self.sent.append(data)

    async def scenario():
        ws1, ws2, ws3 = SlowWS(0.2), SlowWS(0.2), SlowWS(0.2)
        srv.ws_clients.update([ws1, ws2, ws3])
        t0 = time.monotonic()
        await srv.broadcast({"type": "perf.test"})
        elapsed = time.monotonic() - t0
        # 串行 = 0.6s；并行 ≈ 0.2s。阈值为 0.45s 足以区分且抗慢机抖动。
        assert elapsed < 0.45, f"broadcast not parallel: {elapsed:.3f}s"
        assert all(len(w.sent) == 1 for w in (ws1, ws2, ws3))
        return elapsed

    elapsed = asyncio.run(scenario())
    print(f"    parallel broadcast 3 clients x 200ms = {elapsed:.3f}s (serial would be ~0.6s)")


def test_broadcast_slow_client_pruned_and_does_not_block_others():
    import packages.web.server as srv
    srv.ws_clients.clear()
    srv.agent_clients.clear()
    srv.agent_subscriptions.clear()

    class BlockingWS:
        async def send_json(self, data):
            await asyncio.sleep(10)

    class FastWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, data):
            self.sent.append(data)

    async def scenario():
        slow = BlockingWS()
        fast = FastWS()
        srv.ws_clients.update([slow, fast])
        t0 = time.monotonic()
        await srv.broadcast({"type": "perf.test"})
        elapsed = time.monotonic() - t0
        # 慢客户端被 2s 超时剔除；fast 立即送达，broadcast 总时长 ≈ 2s（慢客户端自己的超时）。
        assert elapsed < 4, f"slow client blocked broadcast: {elapsed:.2f}s"
        assert slow not in srv.ws_clients, "blocked client not pruned"
        assert fast in srv.ws_clients and len(fast.sent) == 1
        return elapsed

    elapsed = asyncio.run(scenario())
    print(f"    slow client timed out after 2s; broadcast total {elapsed:.2f}s, fast delivered")


if __name__ == "__main__":
    test_stream_blocks_batch_into_single_save()
    test_stream_debounce_window_flushes()
    test_stream_block_count_cap_flushes()
    test_result_flushes_debounced_blocks_through_read_stdout()
    test_idle_status_broadcast_on_result()
    test_broadcast_sends_clients_in_parallel()
    test_broadcast_slow_client_pruned_and_does_not_block_others()
    print("\n=== ALL BACKEND PERF OPT TESTS PASSED ===")
