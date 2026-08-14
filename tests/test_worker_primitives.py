"""Tests for orchestration primitives handoff / assign / send (Phase C).

- handoff: blocks until worker.result, returns final result dict
- handoff timeout: returns error after timeout
- handoff on dead worker: resolves with error (via zombie path)
- assign: returns queued immediately, worker receives task
- send: sends to existing worker, errors on unknown/dead worker
- ensure_worker auto-spawns when no worker exists
"""

import asyncio
import sys
from pathlib import Path

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess


def _cleanup():
    worker.workers.clear()
    worker._result_waiters.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid="ses_test", adapter="cbc"):
    s = _sess.Session(id=sid, name="test", adapter=adapter, model="test-model")
    _sess._cache[sid] = s
    return s


def _setup_worker(session_id, status="idle"):
    w = worker.Worker(
        worker_id="worker-test",
        session_id=session_id,
        adapter=__import__("packages.core.adapters", fromlist=["CbcAdapter"]).CbcAdapter(),
        status=status,
        process=None,
        queue=asyncio.Queue(),
        _replaying=False,
    )
    worker.workers[w.worker_id] = w
    return w


# ── tests ──

def test_handoff_waits_for_result():
    """handoff blocks until the worker's result event fires, then returns it."""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)

    async def scenario():
        # Start handoff in a task; it will block on the result future
        task = asyncio.create_task(worker.handoff(s.id, "compute something"))
        await asyncio.sleep(0.05)
        # Simulate the worker finishing: resolve the result via _resolve_result_waiter
        worker._resolve_result_waiter(w.worker_id, "done", "the answer")
        return await task

    result = asyncio.run(scenario())

    assert result["status"] == "done", f"got {result}"
    assert result["result"] == "the answer", f"got {result}"
    assert result["workerId"] == w.worker_id
    print("PASS: handoff waits for result")
    _cleanup()


def test_handoff_timeout():
    """handoff returns error if no result within timeout."""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)

    async def scenario():
        result = await worker.handoff(s.id, "will never finish", timeout=0.1)
        return result

    result = asyncio.run(scenario())

    assert result["status"] == "error", f"got {result}"
    assert "timed out" in result["result"], f"got {result}"
    # waiter cleaned up
    assert w.worker_id not in worker._result_waiters
    print("PASS: handoff times out")
    _cleanup()


def test_handoff_dead_worker_resolves_error():
    """If worker dies while handoff waits, future resolves with error (no hang)."""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)

    async def scenario():
        task = asyncio.create_task(worker.handoff(s.id, "job"))
        await asyncio.sleep(0.05)
        # Simulate EOF/zombie: resolve with error
        worker._resolve_result_waiter(w.worker_id, "error", "worker exited (returncode=1)")
        return await task

    result = asyncio.run(scenario())

    assert result["status"] == "error", f"got {result}"
    assert "worker exited" in result["result"], f"got {result}"
    print("PASS: handoff resolves on worker death")
    _cleanup()


def test_assign_returns_queued():
    """assign dispatches and returns immediately with queued status."""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)

    async def scenario():
        return await worker.assign(s.id, "parallel job")

    result = asyncio.run(scenario())

    assert result["status"] == "queued", f"got {result}"
    assert result["workerId"] == w.worker_id
    # task is actually queued on the worker
    assert w.queue.qsize() == 1, f"task not queued, qsize={w.queue.qsize()}"
    print("PASS: assign returns queued")
    _cleanup()


def test_send_to_existing_worker():
    """send delivers a message to an existing live worker."""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)

    async def scenario():
        return await worker.send(w.worker_id, "multi-turn followup")

    result = asyncio.run(scenario())

    assert result["status"] == "queued", f"got {result}"
    assert w.queue.qsize() == 1
    print("PASS: send to existing worker")
    _cleanup()


def test_send_unknown_worker_errors():
    """send to an unknown worker returns error."""
    _cleanup()

    async def scenario():
        return await worker.send("worker-nope", "hi")

    result = asyncio.run(scenario())
    assert result["status"] == "error", f"got {result}"
    print("PASS: send unknown worker errors")
    _cleanup()


def test_ensure_worker_autospawns():
    """_ensure_worker creates a worker when none exists for the session."""
    _cleanup()
    s = _setup_session(sid="ses_none")
    created = []

    async def fake_create(session_id):
        w = _setup_worker(session_id)
        created.append(w)
        return w

    async def scenario():
        # swap create_worker to avoid real process spawn
        orig = worker.create_worker
        worker.create_worker = fake_create
        try:
            w, err = await worker._ensure_worker(s.id)
            return w, err
        finally:
            worker.create_worker = orig

    w, err = asyncio.run(scenario())

    assert err is None, f"got err {err}"
    assert w is not None and len(created) == 1, "worker not auto-spawned"
    print("PASS: ensure_worker auto-spawns")
    _cleanup()


if __name__ == "__main__":
    test_handoff_waits_for_result()
    test_handoff_timeout()
    test_handoff_dead_worker_resolves_error()
    test_assign_returns_queued()
    test_send_to_existing_worker()
    test_send_unknown_worker_errors()
    test_ensure_worker_autospawns()
    print("\n=== ALL PRIMITIVE TESTS PASSED ===")
