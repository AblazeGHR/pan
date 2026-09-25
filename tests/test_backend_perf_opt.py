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

from packages.core import notifications, worker, session as _sess
from packages.core.adapters import CbcAdapter


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _make_event(event_type: str, **fields) -> bytes:
    return (json.dumps({"type": event_type, **fields}) + "\n").encode("utf-8")


def _no_ts(entries):
    """剥掉 append_history 打的 ts 字段，便于断言消息本体。"""
    return [{k: v for k, v in e.items() if k != "ts"} for e in entries]


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

    assert _no_ts(s.history) == [{"role": "assistant", "content": "hi"}], s.history
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
    assert "taskSeq" in idle[0]
    _cleanup()


def test_terminal_result_and_idle_do_not_wait_for_slow_or_failed_notification(monkeypatch):
    """终态广播只走非阻塞 sender，result/idle 各出现一次。"""
    _cleanup()
    s, w = _make_worker()
    s.notification_settings = {"browser": True, "system": True}
    calls = []

    def slow_sender(title, body):
        time.sleep(0.15)
        calls.append((title, body))
        raise RuntimeError("desktop sender failed")

    broadcasts = []

    async def fake_bcast(data):
        broadcasts.append(data)

    async def fake_save(sess):
        pass

    worker.set_broadcaster(fake_bcast)
    monkeypatch.setattr(_sess, "save_async", fake_save)
    notifications.set_system_sender(slow_sender)
    try:
        w.process = MockProcess([_assistant_event(text="hi"), _result_event(result="hi")])

        async def scenario():
            started = time.monotonic()
            await worker._read_stdout(w)
            elapsed = time.monotonic() - started
            # Let the background sender finish so failure handling is observed.
            await asyncio.sleep(0.25)
            return elapsed

        elapsed = asyncio.run(scenario())
        assert elapsed < 0.12, f"terminal path waited for notification: {elapsed:.3f}s"
        assert len([e for e in broadcasts if e.get("type") == "worker.result"]) == 1
        assert len([e for e in broadcasts if e.get("type") == "worker.status" and e.get("status") == "idle"]) == 1
        assert len(calls) == 1
    finally:
        notifications.set_system_sender(notifications.default_system_sender)
        _cleanup()


# ── A4/T-062.4: broadcast enqueue isolation ──

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
        # The producer only enqueues; it must not wait for the 200ms socket.
        assert elapsed < 0.1, f"broadcast waited for socket: {elapsed:.3f}s"
        await asyncio.sleep(0.25)
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
        def __init__(self):
            self.closed = []

        async def send_json(self, data):
            await asyncio.sleep(10)

        async def close(self, code=None, reason=None):
            self.closed.append((code, reason))

    class FastWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, data):
            self.sent.append(data)

    async def scenario():
        slow = BlockingWS()
        fast = FastWS()
        srv.ws_clients.update([slow, fast])
        old_timeout = srv._WS_SEND_TIMEOUT_SEC
        srv._WS_SEND_TIMEOUT_SEC = 0.05
        try:
            t0 = time.monotonic()
            await srv.broadcast({"type": "perf.test"})
            elapsed = time.monotonic() - t0
            # The slow client's timeout belongs to its sender task, not the
            # producer.  Give that task time to evict it explicitly.
            assert elapsed < 0.1, f"slow client blocked broadcast: {elapsed:.2f}s"
            await asyncio.sleep(0.1)
            assert slow not in srv.ws_clients, "blocked client not pruned"
            assert slow.closed == [(1013, "resync_required")]
            assert fast in srv.ws_clients and len(fast.sent) == 1
            return elapsed
        finally:
            srv._WS_SEND_TIMEOUT_SEC = old_timeout

    elapsed = asyncio.run(scenario())
    print(f"    slow client isolated; broadcast total {elapsed:.2f}s, fast delivered")


if __name__ == "__main__":
    test_stream_blocks_batch_into_single_save()
    test_stream_debounce_window_flushes()
    test_stream_block_count_cap_flushes()
    test_result_flushes_debounced_blocks_through_read_stdout()
    test_idle_status_broadcast_on_result()
    test_broadcast_sends_clients_in_parallel()
    test_broadcast_slow_client_pruned_and_does_not_block_others()
    print("\n=== ALL BACKEND PERF OPT TESTS PASSED ===")
