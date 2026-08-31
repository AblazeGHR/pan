"""Tests for orchestration primitives assign / send (Phase C).

- assign: returns queued immediately, worker receives task
- assign taskId 幂等（B3）: 同一 taskId 重发不重复入队
- send: sends to existing worker, errors on unknown/dead worker
- ensure_worker auto-spawns when no worker exists
"""

import asyncio
import sys
import time
from pathlib import Path

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess


def _cleanup():
    worker.workers.clear()
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
        pending_signal=asyncio.Queue(),
        _replaying=False,
    )
    worker.workers[w.worker_id] = w
    return w


# ── tests ──

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
    assert w.pending_signal.qsize() == 1, f"task not queued, qsize={w.pending_signal.qsize()}"
    print("PASS: assign returns queued")
    _cleanup()


# ── assign taskId 幂等（B3）──

def test_assign_task_id_queued_and_registered():
    """assign 带 task_id → queued + taskId 已登记 pending。"""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)
    tid = "assign-task-1"

    async def scenario():
        return await worker.assign(s.id, "job", task_id=tid)

    result = asyncio.run(scenario())

    assert result["status"] == "queued", f"got {result}"
    assert result["taskId"] == tid
    assert worker._task_status[tid]["status"] == "pending", worker._task_status
    assert w.pending_signal.qsize() == 1
    print("PASS: assign with taskId queues and registers pending")
    _cleanup()


def test_assign_task_id_idempotent_pending():
    """同一 taskId 进行中重复 assign → 返回 pending，不重复入队（防双跑）。"""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)
    tid = "assign-task-2"

    async def scenario():
        r1 = await worker.assign(s.id, "job", task_id=tid)
        r2 = await worker.assign(s.id, "job", task_id=tid)
        return r1, r2

    r1, r2 = asyncio.run(scenario())

    assert r1["status"] == "queued", f"got {r1}"
    assert r2["status"] == "pending", f"got {r2}"
    assert r2["taskId"] == tid
    assert w.pending_signal.qsize() == 1, f"task re-enqueued: qsize={w.pending_signal.qsize()}"
    print("PASS: assign taskId idempotent while pending")
    _cleanup()


def test_assign_task_id_idempotent_after_complete():
    """已完成 taskId 重复 assign → 返回缓存结果，不重复入队。"""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)
    tid = "assign-task-3"

    async def scenario():
        r1 = await worker.assign(s.id, "job", task_id=tid)
        # 模拟完成路径：worker.py 完成时把 _task_status 更新为 done
        worker._task_status[tid] = {"status": "done", "result": "the answer",
                                    "workerId": w.worker_id, "taskId": tid,
                                    "ts": time.monotonic()}
        r2 = await worker.assign(s.id, "job", task_id=tid)
        return r1, r2

    r1, r2 = asyncio.run(scenario())

    assert r1["status"] == "queued", f"got {r1}"
    assert r2["status"] == "done" and r2["result"] == "the answer", f"got {r2}"
    assert w.pending_signal.qsize() == 1, f"task re-enqueued: qsize={w.pending_signal.qsize()}"
    print("PASS: assign taskId idempotent after complete")
    _cleanup()


def test_assign_task_id_error_after_send_failure(monkeypatch):
    """send_task 失败 → taskId 标 error，重试返回确定性 error。"""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)
    tid = "assign-task-4"
    orig_send = worker.send_task

    async def fake_send(worker_id, text, source="agent", seq=None, task_id=None):
        return "Worker process dead"

    worker.send_task = fake_send
    try:
        async def scenario():
            return await worker.assign(s.id, "job", task_id=tid)
        result = asyncio.run(scenario())
    finally:
        worker.send_task = orig_send

    assert result["status"] == "error", f"got {result}"
    assert worker._task_status[tid]["status"] == "error"
    print("PASS: assign taskId marked error on send failure")
    _cleanup()


def test_assign_task_id_ttl_prunes_but_durable_queue_still_deduplicates():
    """注册表过期不等于任务可重跑：持久队列仍拒绝第二次执行。"""
    _cleanup()
    s = _setup_session()
    w = _setup_worker(s.id)
    tid = "assign-task-ttl"
    orig_ttl = worker._TASK_STATUS_TTL_SEC
    worker._TASK_STATUS_TTL_SEC = 0.001
    try:
        async def scenario():
            await worker.assign(s.id, "job", task_id=tid)
            assert tid in worker._task_status
            worker._task_status[tid]["ts"] = 0.0  # 人为调旧，模拟超 TTL
            r2 = await worker.assign(s.id, "job", task_id=tid)
            return w.pending_signal.qsize(), r2
        qsize, r2 = asyncio.run(scenario())
    finally:
        worker._TASK_STATUS_TTL_SEC = orig_ttl

    assert qsize == 1, f"durable task id was duplicated after TTL: qsize={qsize}"
    assert r2["status"] == "pending", f"got {r2}"
    print("PASS: assign taskId TTL expiry still respects durable idempotency")
    _cleanup()


def test_assign_no_task_id_unchanged():
    """不带 task_id → 行为不变：queued、无 taskId 字段、不进注册表。"""
    _cleanup()
    s = _setup_session()
    _setup_worker(s.id)

    async def scenario():
        return await worker.assign(s.id, "job")

    result = asyncio.run(scenario())

    assert result["status"] == "queued", f"got {result}"
    assert "taskId" not in result
    assert worker._task_status == {}, "no task_id → no registry entry"
    print("PASS: assign without taskId unchanged")
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
    assert w.pending_signal.qsize() == 1
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
    test_assign_returns_queued()
    test_assign_task_id_queued_and_registered()
    test_assign_task_id_idempotent_pending()
    test_assign_task_id_idempotent_after_complete()
    test_assign_task_id_error_after_send_failure()
    test_assign_task_id_ttl_prunes()
    test_assign_no_task_id_unchanged()
    test_send_to_existing_worker()
    test_send_unknown_worker_errors()
    test_ensure_worker_autospawns()
    test_memory_injection_disabled_skips_embedding()
    print("\n=== ALL PRIMITIVE TESTS PASSED ===")
