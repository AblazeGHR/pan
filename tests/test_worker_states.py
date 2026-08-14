"""Tests for Worker state machine transitions.

Phase A: distinguish spawning / queued / zombie from idle/running/error.

- send_task on idle worker → status "queued" + worker.status broadcast
- _read_stdout init event on spawning worker → status "idle" + broadcast
- _read_stdout EOF → status "zombie" + worker.zombie broadcast + dict removal
- create_worker stream mode → initial status "spawning"

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
        self.stdout = self._async_iter(hold_open)

    async def _async_iter(self, hold_open: bool):
        for e in self._events:
            yield e
        if hold_open:
            # simulate long-running CLI: block forever (no EOF)
            await asyncio.Event().wait()


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
        queue=asyncio.Queue(),
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


def test_init_event_transitions_spawning_to_idle():
    """init event on spawning worker → idle + worker.status broadcast."""
    _cleanup()
    broadcast_calls = []

    async def fake_broadcast(data):
        broadcast_calls.append(data)

    worker.set_broadcaster(fake_broadcast)
    s = _setup_session()
    w = _setup_worker(s.id, status="spawning")
    w.process = MockProcess([_system_init_event()], hold_open=True)

    async def run():
        task = asyncio.create_task(worker._read_stdout(w))
        await asyncio.sleep(0.05)
        return task

    task = asyncio.run(run())
    task.cancel()

    assert w.status == "idle", f"expected idle after init, got {w.status}"
    idle_bc = [c for c in broadcast_calls if c.get("type") == "worker.status"
               and c.get("status") == "idle"]
    assert len(idle_bc) == 1, f"missing idle broadcast: {broadcast_calls}"
    print("PASS: init event spawning → idle + broadcast")
    _cleanup()


def test_init_event_does_not_touch_non_spawning():
    """init event on non-spawning worker leaves status unchanged."""
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
    print("PASS: init event leaves non-spawning status alone")
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


def test_create_worker_stream_starts_spawning(monkeypatch):
    """create_worker (stream mode) → initial status "spawning"."""
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
    assert w.status == "spawning", f"expected spawning, got {w.status}"
    # cleanup tasks to avoid warnings
    for t in (w._stdout_task, w._consume_task):
        if t:
            t.cancel()
    _cleanup()


if __name__ == "__main__":
    test_send_task_sets_queued()
    test_send_task_does_not_override_running()
    test_init_event_transitions_spawning_to_idle()
    test_init_event_does_not_touch_non_spawning()
    test_eof_sets_zombie_and_removes()
    test_eof_normal_exit_still_zombie()
    print("\n=== ALL STATE TESTS PASSED ===")
