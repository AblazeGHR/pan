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
    worker._task_status.clear()
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
        # Simulate the worker finishing: resolve with the handoff-allocated seq
        # (handoff allocated seq=1; result must carry that seq to match)
        worker._resolve_result_waiter(w.worker_id, "done", "the answer", task_seq=1)
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
        # Simulate EOF/zombie: resolve with error (task_seq=None → force resolve)
        worker._resolve_result_waiter(w.worker_id, "error", "worker exited (returncode=1)")
        return await task

    result = asyncio.run(scenario())

    assert result["status"] == "error", f"got {result}"
    assert "worker exited" in result["result"], f"got {result}"
    print("PASS: handoff resolves on worker death")
    _cleanup()


def test_waiter_ignores_other_tasks_result():
    """result of a different task (wrong seq) must NOT resolve the waiter."""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)

    async def scenario():
        # handoff allocates seq=1 for its task
        task = asyncio.create_task(worker.handoff(s.id, "my job"))
        await asyncio.sleep(0.05)
        # A previous task (seq=0) finishes first — must NOT resolve handoff's waiter
        worker._resolve_result_waiter(w.worker_id, "done", "other task result", task_seq=0)
        # waiter should still be registered
        assert w.worker_id in worker._result_waiters, "waiter wrongly removed by other task result"
        # Now handoff's own task (seq=1) finishes
        worker._resolve_result_waiter(w.worker_id, "done", "my job result", task_seq=1)
        return await task

    result = asyncio.run(scenario())

    assert result["status"] == "done", f"got {result}"
    assert result["result"] == "my job result", f"got {result}"
    print("PASS: waiter ignores other tasks' results")
    _cleanup()


def test_waiter_force_resolve_ignores_seq():
    """task_seq=None (worker killed) force-resolves regardless of seq."""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)

    async def scenario():
        task = asyncio.create_task(worker.handoff(s.id, "job"))
        await asyncio.sleep(0.05)
        # Force resolve (kill path) — even though waiter expects seq=1
        worker._resolve_result_waiter(w.worker_id, "error", "worker killed")
        return await task

    result = asyncio.run(scenario())

    assert result["status"] == "error", f"got {result}"
    assert "worker killed" in result["result"], f"got {result}"
    print("PASS: force resolve ignores seq")
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


def test_handoff_task_id_idempotent_after_complete():
    """Same taskId re-handoff returns existing result, does NOT re-enqueue."""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)
    tid = "task-abc-123"

    async def scenario():
        # First handoff with taskId
        t1 = asyncio.create_task(worker.handoff(s.id, "job", task_id=tid))
        await asyncio.sleep(0.05)
        # Simulate worker finishing (handoff allocated seq=1)
        worker._resolve_result_waiter(w.worker_id, "done", "the answer", task_seq=1)
        r1 = await t1
        # Second handoff with SAME taskId → should return stored result
        r2 = await worker.handoff(s.id, "job", task_id=tid)
        return r1, r2

    r1, r2 = asyncio.run(scenario())

    assert r1["status"] == "done" and r1["result"] == "the answer"
    assert r2["status"] == "done" and r2["result"] == "the answer", f"re-handoff wrong: {r2}"
    # taskId propagated
    assert r1.get("taskId") == tid
    assert r2.get("taskId") == tid
    # Second handoff did NOT enqueue another task (still only the original queued item)
    assert w.queue.qsize() == 1, f"task re-enqueued: qsize={w.queue.qsize()}"
    print("PASS: handoff taskId idempotent after complete")
    _cleanup()


def test_handoff_task_id_pending_on_timeout():
    """Timed-out handoff with taskId returns pending; retry returns pending not re-enqueue."""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)
    tid = "task-pending-1"

    async def scenario():
        r1 = await worker.handoff(s.id, "slow job", task_id=tid, timeout=0.05)
        # Second attempt with same taskId
        r2 = await worker.handoff(s.id, "slow job", task_id=tid, timeout=0.05)
        return r1, r2

    r1, r2 = asyncio.run(scenario())

    assert r1["status"] == "pending", f"got {r1}"
    assert r2["status"] == "pending", f"got {r2}"
    # Only one task in queue (idempotent)
    assert w.queue.qsize() == 1, f"task re-enqueued: qsize={w.queue.qsize()}"
    print("PASS: handoff taskId pending on timeout")
    _cleanup()


def test_handoff_timeout_then_kill_then_retry_same_task_id():
    """H2 回归: 超时→kill→同 taskId 重试不再永久卡 pending。

    旧行为: handoff 超时后 taskId 留在 pending；worker 被杀时 kill_worker 不更新
    _task_status → 重试同 taskId 永远被幂等拦截、永不执行。
    修复后: kill_worker 把名下 pending taskId 标 error → 重试返回确定性 error，
    且不重入队。
    """
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)
    tid = "task-killed-1"

    async def scenario():
        r1 = await worker.handoff(s.id, "slow job", task_id=tid, timeout=0.05)
        assert r1["status"] == "pending", f"got {r1}"
        assert worker._task_status[tid]["status"] == "pending"

        # worker 被杀 → 名下 pending taskId 应被标 error
        await worker.kill_worker(w.worker_id)
        assert worker._task_status[tid]["status"] == "error", \
            f"pending task not marked error: {worker._task_status[tid]}"

        # 同 taskId 重试（worker 已重建）→ 返回 error，不重入队
        orig_create = worker.create_worker
        worker.create_worker = lambda session_id: _setup_worker(session_id)
        try:
            r2 = await worker.handoff(s.id, "slow job", task_id=tid, timeout=0.05)
        finally:
            worker.create_worker = orig_create
        return r1, r2

    r1, r2 = asyncio.run(scenario())

    assert r1["status"] == "pending", f"got {r1}"
    assert r2["status"] == "error", f"retry after kill should be error, got {r2}"
    assert "worker killed" in r2.get("result", ""), f"got {r2}"
    # 重试被幂等拦截：在 worker 重建之前就返回 error → 没有创建新 worker、没有入队
    assert w.worker_id not in worker.workers, \
        "retry created a new worker despite idempotent error"
    print("PASS: handoff timeout → kill → retry same taskId returns error")
    _cleanup()


def test_task_status_ttl_prunes_expired_entries():
    """H2 回归: 超过 TTL 的 _task_status 条目在下次 handoff 访问时被惰性清除。

    注册表是全局 dict，无清理会无界增长；过期条目应被当作不存在 → 同 taskId
    重新入队执行。
    """
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)
    tid = "task-ttl-1"
    orig_ttl = worker._TASK_STATUS_TTL_SEC
    worker._TASK_STATUS_TTL_SEC = 0.001
    try:
        async def scenario():
            await worker.handoff(s.id, "job", task_id=tid, timeout=0.05)
            assert tid in worker._task_status
            # 人为调旧，模拟条目存活超过 TTL
            worker._task_status[tid]["ts"] = 0.0
            # 同 taskId 重试：过期条目先被 prune → 视为不存在 → 重新入队执行
            r2 = await worker.handoff(s.id, "job", task_id=tid, timeout=0.05)
            return w.queue.qsize(), r2
        qsize, r2 = asyncio.run(scenario())
    finally:
        worker._TASK_STATUS_TTL_SEC = orig_ttl

    assert qsize == 2, f"expired entry not pruned/re-enqueued: qsize={qsize}"
    assert worker._task_status[tid]["status"] == "pending", \
        f"entry not re-registered: {worker._task_status[tid]}"
    assert r2["status"] == "pending", f"got {r2}"
    print("PASS: _task_status TTL prunes expired entries")
    _cleanup()


def test_memory_injection_disabled_skips_embedding():
    """memory.enabled=false → _maybe_inject_memory returns raw text without
    touching the embedding model (no character lookup, no search)."""
    _cleanup()
    s = _setup_session(sid="ses_mem")
    s.character_id = "char_test"

    orig_enabled = worker._MEMORY_ENABLED
    worker._MEMORY_ENABLED = False
    try:
        async def scenario():
            return await worker._maybe_inject_memory(s, "hello")
        result = asyncio.run(scenario())
        assert result == "hello", f"expected raw text, got {result!r}"
    finally:
        worker._MEMORY_ENABLED = orig_enabled
    print("PASS: memory injection disabled returns raw text")
    _cleanup()


if __name__ == "__main__":
    test_handoff_waits_for_result()
    test_handoff_timeout()
    test_handoff_dead_worker_resolves_error()
    test_assign_returns_queued()
    test_send_to_existing_worker()
    test_send_unknown_worker_errors()
    test_ensure_worker_autospawns()
    test_handoff_task_id_idempotent_after_complete()
    test_handoff_task_id_pending_on_timeout()
    test_handoff_timeout_then_kill_then_retry_same_task_id()
    test_task_status_ttl_prunes_expired_entries()
    test_memory_injection_disabled_skips_embedding()
    print("\n=== ALL PRIMITIVE TESTS PASSED ===")
