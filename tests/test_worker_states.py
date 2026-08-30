"""Tests for Worker state machine transitions.

Phase A: distinguish queued / zombie from idle/running/error.

- send_task on idle worker → status "queued" + worker.status broadcast
- _read_stdout init event extracts metadata but does NOT change status
  (stream mode has no init event; worker is idle from spawn)
- _read_stdout EOF → status "zombie" + worker.zombie broadcast + dict removal
- create_worker stream mode → initial status "idle"

Uses mock cbc process (no real cbc needed).
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


# ── fixtures ──

def _make_event(event_type: str, **fields) -> bytes:
    event = {"type": event_type, **fields}
    return (json.dumps(event) + "\n").encode("utf-8")


def _system_init_event(cbc_sid: str = "cbc-123", model: str = "test-model") -> bytes:
    return _make_event("system", subtype="init", session_id=cbc_sid, model=model)


class MockProcess:
    def __init__(self, events: list[bytes], pid: int = 1000, returncode=None,
                 hold_open: bool = False):
        self._events = list(events)
        self.returncode = returncode
        self.pid = pid
        self.stdin = AsyncMock()
        self._hold_open = hold_open
        self.stdout = self

    async def readline(self):
        if self._events:
            return self._events.pop(0)
        if self._hold_open:
            # simulate long-running CLI: block forever (no EOF)
            await asyncio.Event().wait()
        return b""

    async def read(self, n=-1):
        # 分块读兼容：一次返回一个事件行（含换行）；EOF 返回 b""
        if self._events:
            return self._events.pop(0)
        if self._hold_open:
            await asyncio.Event().wait()
        return b""


def _setup_session():
    s = _sess.Session(id="ses_test", name="test", model="test-model")
    _sess._cache[s.id] = s
    return s


def _setup_worker(session_id: str, status: str = "idle"):
    w = worker.Worker(
        worker_id="worker-test",
        session_id=session_id,
        adapter=CbcAdapter(),
        status=status,
        process=MagicMock(),
        pending_signal=asyncio.Queue(),
        _replaying=False,
    )
    worker.workers[w.worker_id] = w
    return w


def _cleanup():
    worker.workers.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


# ── tests ──

def test_send_task_sets_queued():
    """send_task on idle worker → status "queued" + worker.status broadcast."""
    _cleanup()
    broadcast_calls = []

    async def fake_broadcast(data):
        broadcast_calls.append(data)

    worker.set_broadcaster(fake_broadcast)
    s = _setup_session()
    w = _setup_worker(s.id, status="idle")
    w.process.returncode = None

    asyncio.run(worker.send_task(w.worker_id, "hi", source="agent"))

    assert w.status == "queued", f"expected queued, got {w.status}"
    queued_bc = [c for c in broadcast_calls if c.get("type") == "worker.status"
                 and c.get("status") == "queued"]
    assert len(queued_bc) == 1, f"missing queued broadcast: {broadcast_calls}"
    print("PASS: send_task sets queued + broadcasts")
    _cleanup()


def test_send_task_does_not_override_running():
    """If worker already running, send_task keeps running (no queued downgrade)."""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id, status="running")
    w.process.returncode = None

    asyncio.run(worker.send_task(w.worker_id, "more", source="agent"))

    assert w.status == "running", f"expected running unchanged, got {w.status}"
    print("PASS: send_task keeps running status")
    _cleanup()


def test_send_task_seq_survives_respawn():
    """taskSeq 计数持久化在 session 上：worker respawn 后继续 +1，不回退到 1。"""
    _cleanup()

    async def noop_save_async(s):
        pass

    orig_save = _sess.save_async
    _sess.save_async = noop_save_async
    try:
        s = _setup_session()
        assert s.task_seq == 0  # 老落盘文件无该字段时默认 0（向后兼容）

        # 第 1 代 worker：派第 1 个任务 → seq 1
        w1 = _setup_worker(s.id, status="idle")
        w1.process.returncode = None
        err = asyncio.run(worker.send_task(w1.worker_id, "task one", source="agent"))
        assert err is None
        assert s.queue_pending[-1]["seq"] == 1 and s.task_seq == 1

        # respawn：worker 空闲回收后重新 spawn（全新 Worker 实例，session 不变）
        worker.workers.clear()
        w2 = _setup_worker(s.id, status="idle")
        w2.process.returncode = None
        err = asyncio.run(worker.send_task(w2.worker_id, "task two", source="agent"))
        assert err is None
        assert s.queue_pending[-1]["seq"] == 2 and s.task_seq == 2, \
            "taskSeq must keep incrementing across worker respawn"
        print("PASS: send_task taskSeq survives respawn")
    finally:
        _sess.save_async = orig_save
        _cleanup()


def test_init_event_extracts_metadata_only():
    """init event extracts cli_session_id/model but leaves status unchanged.

    stream mode has no init event on startup; when one does arrive
    (e.g. MCP one-shot), it must not clobber worker status.
    """
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id, status="running")
    w.process = MockProcess([_system_init_event()], hold_open=True)

    async def run():
        task = asyncio.create_task(worker._read_stdout(w))
        await asyncio.sleep(0.05)
        return task

    task = asyncio.run(run())
    task.cancel()

    assert w.status == "running", f"expected running unchanged, got {w.status}"
    assert s.cli_session_id == "cbc-123", f"cli_session_id not extracted: {s.cli_session_id}"
    assert s.model == "test-model", f"model not extracted: {s.model}"
    print("PASS: init event extracts metadata, keeps status")
    _cleanup()


def test_eof_sets_zombie_and_removes():
    """EOF with error → status zombie + worker.zombie broadcast + removed from dict."""
    _cleanup()
    broadcast_calls = []

    async def fake_broadcast(data):
        broadcast_calls.append(data)

    worker.set_broadcaster(fake_broadcast)
    s = _setup_session()
    w = _setup_worker(s.id, status="idle")
    # No init event; process exits with non-zero code and no valid last_result
    w.process = MockProcess([], returncode=1)

    async def run():
        await worker._read_stdout(w)

    asyncio.run(run())

    # worker removed from dict
    assert w.worker_id not in worker.workers, "zombie worker not removed from dict"
    zombie_bc = [c for c in broadcast_calls if c.get("type") == "worker.zombie"]
    assert len(zombie_bc) == 1, f"missing zombie broadcast: {broadcast_calls}"
    assert zombie_bc[0]["returncode"] == 1
    print("PASS: EOF sets zombie, broadcasts, removes from dict")
    _cleanup()


def test_eof_normal_exit_still_zombie():
    """Even a normal exit (valid last_result) goes through zombie state."""
    _cleanup()
    broadcast_calls = []

    async def fake_broadcast(data):
        broadcast_calls.append(data)

    worker.set_broadcaster(fake_broadcast)
    s = _setup_session()
    s.last_result = {"status": "done", "result": "ok"}
    w = _setup_worker(s.id, status="idle")
    w.process = MockProcess([], returncode=1)

    async def run():
        await worker._read_stdout(w)

    asyncio.run(run())

    zombie_bc = [c for c in broadcast_calls if c.get("type") == "worker.zombie"]
    assert len(zombie_bc) == 1, f"missing zombie broadcast: {broadcast_calls}"
    assert w.worker_id not in worker.workers, "normal-exit worker not removed"
    print("PASS: normal exit also emits zombie + removal")
    _cleanup()


def test_eof_reports_zombie_for_running(monkeypatch):
    """B2: running 中进程异常退出（EOF 检测路径）→ 被管+订阅 session 收到 zombie 报告。"""
    _cleanup()

    async def noop_save(s):
        pass

    monkeypatch.setattr(_sess, "save_async", noop_save)
    child = _sess.Session(id="ses_child", name="child")
    child.managed_by = "ses_mgr"
    _sess._cache["ses_child"] = child
    mgr = _sess.Session(id="ses_mgr", name="mgr")
    mgr.report_subscriptions = {"ses_child"}
    _sess._cache["ses_mgr"] = mgr

    w = _setup_worker("ses_child", status="running")
    w.process = MockProcess([], returncode=1)

    async def run():
        await worker._read_stdout(w)

    asyncio.run(run())

    assert len(mgr.queue_pending) == 1, f"zombie report missing: {mgr.queue_pending}"
    r = mgr.queue_pending[0]
    assert r["status"] == "error" and r["type"] == "zombie"
    assert "process exited" in r["result"]
    assert r["sessionId"] == "ses_child"
    assert w.worker_id not in worker.workers, "zombie worker not removed from dict"
    print("PASS: EOF for running worker reports zombie to managed manager")
    _cleanup()


def test_eof_idle_no_zombie_report(monkeypatch):
    """B2: idle 状态进程退出（正常完成后的退出）→ 不报 zombie。"""
    _cleanup()

    async def noop_save(s):
        pass

    monkeypatch.setattr(_sess, "save_async", noop_save)
    child = _sess.Session(id="ses_child", name="child")
    child.managed_by = "ses_mgr"
    _sess._cache["ses_child"] = child
    mgr = _sess.Session(id="ses_mgr", name="mgr")
    mgr.report_subscriptions = {"ses_child"}
    _sess._cache["ses_mgr"] = mgr

    w = _setup_worker("ses_child", status="idle")
    w.process = MockProcess([], returncode=1)

    async def run():
        await worker._read_stdout(w)

    asyncio.run(run())

    assert mgr.queue_pending == [], "idle exit must NOT report zombie"
    assert w.worker_id not in worker.workers
    print("PASS: idle exit does not report zombie")
    _cleanup()


def test_create_worker_stream_starts_idle(monkeypatch):
    """create_worker (stream mode) → initial status "idle".

    stream mode has no init event on startup, so the worker is idle
    from spawn (matching main branch behavior).
    """
    _cleanup()
    s = _setup_session()
    # stream mode: no mcp_servers in adapter_config
    s.adapter_config = {}

    async def fake_spawn(session_id, adapter, extra_args=None):
        return MockProcess([], returncode=None, hold_open=True)

    async def fake_send(worker_id, text, source="agent"):
        return None

    async def fake_save(sess):
        return None

    monkeypatch.setattr(worker, "_spawn_process", fake_spawn)
    monkeypatch.setattr(worker, "send_task", fake_send)
    monkeypatch.setattr(_sess, "save_async", fake_save)

    async def run():
        return await worker.create_worker(s.id)

    w = asyncio.run(run())

    assert isinstance(w, worker.Worker), f"create_worker failed: {w}"
    assert w.status == "idle", f"expected idle, got {w.status}"
    # cleanup tasks to avoid warnings
    for t in (w._stdout_task, w._consume_task, w._watchdog_task):
        if t:
            t.cancel()
    _cleanup()


def test_create_worker_injects_system_prompt_only_for_fresh_session(monkeypatch):
    """system_prompt 注入去重：全新会话首次 spawn 注入一次；已 resume/fork 会话不注入。

    修复：fork 会话同时继承 system_prompt + cli_session_id，create_worker 若再以
    消息注入 system_prompt，会把 system_prompt 当作一条 user 消息塞进对话——
    fork 首句话前 / takeover 恢复后重复出现系统提示词（worker.py _create_worker
    注入守卫）。本测试锁定两种路径的行为。
    """
    _cleanup()
    sent_calls = []
    spawn_calls = []

    async def fake_spawn(session_id, adapter, extra_args=None):
        spawn_calls.append(extra_args)
        return MockProcess([], returncode=None, hold_open=True)

    async def fake_send(worker_id, text, source="agent"):
        sent_calls.append((text, source))
        return None

    async def fake_save(sess):
        return None

    monkeypatch.setattr(worker, "_spawn_process", fake_spawn)
    monkeypatch.setattr(worker, "send_task", fake_send)
    monkeypatch.setattr(_sess, "save_async", fake_save)

    # 1) 全新会话（无 cli_session_id）→ 优先通过 spawn 参数注入 system_prompt
    s = _setup_session()
    s.system_prompt = "You are a test assistant."
    s.adapter_config = {}
    w1 = asyncio.run(worker.create_worker(s.id))
    assert isinstance(w1, worker.Worker), f"create_worker failed: {w1}"
    assert spawn_calls[0] == ["--system-prompt", s.system_prompt], \
        "fresh session 首次 spawn 必须注入 system_prompt"
    assert not any(src == "system_prompt" for _, src in sent_calls), \
        "支持 spawn 注入的 adapter 不应再发送重复的 system_prompt 消息"
    for t in (w1._stdout_task, w1._consume_task, w1._watchdog_task):
        if t:
            t.cancel()
    _cleanup()

    # 2) fork/resume 会话（cli_session_id 已存在）→ 不重复注入
    sent_calls.clear()
    s2 = _setup_session()
    s2.system_prompt = "You are a test assistant."
    s2.adapter_config = {"cli_session_id": "forked-cli-1"}
    w2 = asyncio.run(worker.create_worker(s2.id))
    assert isinstance(w2, worker.Worker), f"create_worker failed: {w2}"
    assert spawn_calls[1] is None
    assert not any(src == "system_prompt" for _, src in sent_calls), \
        "fork/resume 会话不应重复注入 system_prompt"
    for t in (w2._stdout_task, w2._consume_task, w2._watchdog_task):
        if t:
            t.cancel()
    _cleanup()


if __name__ == "__main__":
    test_send_task_sets_queued()
    test_send_task_does_not_override_running()
    test_send_task_seq_survives_respawn()
    test_init_event_extracts_metadata_only()
    test_eof_sets_zombie_and_removes()
    test_eof_normal_exit_still_zombie()
    test_eof_reports_zombie_for_running()
    test_eof_idle_no_zombie_report()
    test_create_worker_injects_system_prompt_only_for_fresh_session()
    print("\n=== ALL STATE TESTS PASSED ===")
