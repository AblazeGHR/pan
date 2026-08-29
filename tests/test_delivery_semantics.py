"""投递语义回归测试（fix/delivery-semantics）。

核心不变量：**任务收到 terminal result 后才出队；执行前/执行中崩溃时保留队列，
由新 worker 重投。**

- 报告持久化在 Session.queue_pending（落盘真源），先落 history 投递标记，收到
  terminal result 后才从 queue_pending 移除；任务同样先持久化 history 投递标记，
  收到 result 后才从 queue_pending 移除。崩溃窗口内任务和报告均可重投，
  且已有 history 标记不会导致用户消息重复追加。
- save 失败（非崩溃）回滚内存态，item 留在队列可重投；消费前确认 worker
  进程存活（死 → 中止保留队列，respawn 后由 _recover_pending_signals 重投）。
- report 消费与 task 消费共享队列，互不误删（report 只消费非 task，task 按 id
  认领 + 按对象身份出队）。
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


class _DeadProc:
    """stream 模式死亡进程桩：_process_alive 只读 returncode。"""

    def __init__(self, returncode=1):
        self.returncode = returncode


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    worker._inflight_task_ids.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid="ses_mgr", **kwargs):
    s = _sess.Session(id=sid, name="test", **kwargs)
    _sess._cache[sid] = s
    return s


def _make_worker(sid, process=None):
    w = worker.Worker(
        worker_id="worker-mgr", session_id=sid,
        adapter=CbcAdapter(), status="idle", process=process,
        pending_signal=asyncio.Queue(),
        _task_done=asyncio.Event(),
        _hist_flush_event=asyncio.Event(),
    )
    worker.workers[w.worker_id] = w
    return w


def _make_report(i=1):
    return {"status": "done", "result": f"r{i}", "sessionId": "ses_child",
            "taskId": f"t{i}", "workerId": "worker-1"}


def _make_task(i=1):
    return {"type": "task", "id": f"task{i}", "text": f"job {i}",
            "source": "agent", "seq": i, "taskId": f"tid{i}"}


def _recording_save(log):
    """save_async 桩：记录每次落盘时 (history 快照, queue 快照)。"""

    async def save(s):
        log.append((list(s.history), list(s.queue_pending)))

    return save


async def _complete_task(ww, sess):
    """测试桩：模拟 adapter 已收到 terminal result。"""
    task_id = ww._current_task_id
    worker._ack_current_task(ww, sess)
    worker._ack_current_reports(ww, sess)
    if task_id and task_id in worker._task_status:
        worker._task_status[task_id] = {
            "status": "done", "result": "recovered",
            "workerId": ww.worker_id, "taskId": task_id,
        }


def _run_consumer(w):
    async def scenario():
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())


# ── 报告路径：result 后确认出队 ──


def test_report_atomic_dequeue(monkeypatch):
    """报告 handoff 先落盘 history，terminal result 后才确认整批出队。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    s.queue_pending = [_make_report(1), _make_report(2)]
    w = _make_worker("ses_mgr")
    save_log = []
    received = []
    monkeypatch.setattr(_sess, "save_async", _recording_save(save_log))

    async def fake_stream(ww, text, source, sess):
        received.append((text, source))
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "report_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert len(received) == 1
    assert "r1" in received[0][0] and "r2" in received[0][0]
    assert "[delivered:" not in received[0][0], "mark must not leak into message content"
    assert s.queue_pending == []
    assert len(s.history) == 1
    # 投递标记记为 history 条目元数据，正文不含前缀
    assert s.history[0].get("delivered_keys") == [
        worker._delivery_key(_make_report(1)), worker._delivery_key(_make_report(2))]
    # handoff save 恰好一次；落盘瞬间报告仍在队列，等待 terminal result 确认
    consumer_saves = [e for e in save_log if e[0]]
    assert len(consumer_saves) == 1, f"exactly one atomic save expected: {save_log}"
    hist_at_save, queue_at_save = consumer_saves[0]
    assert len(hist_at_save) == 1, "history must be appended in the same save"
    assert queue_at_save == [_make_report(1), _make_report(2)]
    _cleanup()


def test_report_kept_when_worker_dead_before_handoff(monkeypatch):
    """worker 进程已死 → 报告保留队列、不注入、不执行；respawn 后重投成功。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    s.queue_pending = [_make_report(1)]
    w = _make_worker("ses_mgr", process=_DeadProc(returncode=1))
    monkeypatch.setattr(_sess, "save_async", _recording_save([]))
    received = []

    async def fake_stream(ww, text, source, sess):
        received.append((text, source))
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "report_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert received == [], "dead process must not execute"
    assert s.queue_pending == [_make_report(1)], "report must be kept for redelivery"
    assert s.history == [], "no history injection on dead process"

    # respawn：新 worker（process=None 视为存活）+ _recover_pending_signals 重发信号
    worker.workers.clear()
    w2 = _make_worker("ses_mgr")
    worker._recover_pending_signals(w2, s)
    _run_consumer(w2)

    assert len(received) == 1, "report must be redelivered after respawn"
    assert s.queue_pending == [], "queue drained after successful redelivery"
    assert len(s.history) == 1
    _cleanup()


def test_report_rollback_on_save_failure(monkeypatch):
    """原子 save 失败 → 回滚内存态（history 弹回、队列复原），可重投、不执行。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    s.queue_pending = [_make_report(1)]
    w = _make_worker("ses_mgr")
    received = []

    async def failing_save(s):
        raise RuntimeError("disk full")

    async def fake_stream(ww, text, source, sess):
        received.append((text, source))
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)
    monkeypatch.setattr(_sess, "save_async", failing_save)

    async def scenario():
        await w.pending_signal.put({"type": "report_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert received == [], "failed save must not execute"
    assert s.queue_pending == [_make_report(1)], "queue restored on rollback"
    assert s.history == [], "history append rolled back"
    _cleanup()


# ── 任务路径：result 后确认出队 ──


def test_task_atomic_dequeue(monkeypatch):
    """任务 handoff 先落盘 history；terminal result 到达后才确认 item 出队。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    task = _make_task(1)
    s.queue_pending = [task]
    w = _make_worker("ses_mgr")
    save_log = []
    received = []
    monkeypatch.setattr(_sess, "save_async", _recording_save(save_log))

    async def fake_stream(ww, text, source, sess):
        received.append((text, source, ww._current_seq, ww._current_task_id))
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "task_signal", "id": "task1"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert len(received) == 1
    assert "[delivered:" not in received[0][0], "mark must not leak into message content"
    assert "job 1" in received[0][0], "delivery mark must not replace the task text"
    assert received[0][2] == 1 and received[0][3] == "tid1", "seq/taskId must propagate"
    # 投递标记记为 history 条目元数据，正文不含前缀
    assert s.history[0].get("delivered_keys") == [worker._delivery_key(task)]
    # handoff save 恰好一次；落盘瞬间 history 已含消息，但 task 仍待 result 确认
    consumer_saves = [e for e in save_log if e[0]]
    assert len(consumer_saves) == 1, f"exactly one atomic save expected: {save_log}"
    hist_at_save, queue_at_save = consumer_saves[0]
    assert len(hist_at_save) == 1
    assert queue_at_save == [task]
    assert s.queue_pending == []
    assert worker._inflight_task_ids == set(), "claim must be released after confirm"
    _cleanup()


def test_task_kept_when_worker_dead_before_handoff(monkeypatch):
    """worker 进程已死 → 任务保留队列、标记释放；respawn 后重投成功。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    s.queue_pending = [_make_task(1)]
    w = _make_worker("ses_mgr", process=_DeadProc(returncode=1))
    monkeypatch.setattr(_sess, "save_async", _recording_save([]))
    received = []

    async def fake_stream(ww, text, source, sess):
        received.append(text)
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "task_signal", "id": "task1"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert received == []
    assert s.queue_pending == [_make_task(1)], "task must be kept for redelivery"
    assert s.history == []
    assert worker._inflight_task_ids == set(), "abort must release the in-flight claim"

    # respawn 重投
    worker.workers.clear()
    w2 = _make_worker("ses_mgr")

    async def recovered_stream(ww, text, source, sess):
        received.append(text)
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", recovered_stream)
    worker._recover_pending_signals(w2, s)
    _run_consumer(w2)

    assert len(received) == 1 and "job 1" in received[0], "task must be redelivered after respawn"
    assert s.queue_pending == []
    assert len(s.history) == 1
    _cleanup()


def test_task_requeued_after_crash_after_handoff(monkeypatch):
    """stdin 已写入但 result 丢失：重启后重投，history 用户消息不重复。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    task = _make_task(1)
    s.queue_pending = [task]
    w = _make_worker("ses_mgr")
    monkeypatch.setattr(_sess, "save_async", _recording_save([]))
    attempts = []

    async def crashing_stream(ww, text, source, sess):
        attempts.append("crash")
        raise RuntimeError("CLI crashed after stdin write")

    monkeypatch.setattr(worker, "_consumer_stream", crashing_stream)

    async def first_attempt():
        await w.pending_signal.put({"type": "task_signal", "id": "task1"})
        await worker._consumer(w)

    with pytest.raises(RuntimeError, match="after stdin"):
        asyncio.run(first_attempt())

    assert attempts == ["crash"]
    assert s.queue_pending == [task], "in-flight task must remain durable after crash"
    assert len(s.history) == 1, "handoff history should be persisted once"
    assert worker._inflight_task_ids == set()

    # 新 worker 恢复同一个持久 item；第二次执行模拟收到 terminal result。
    worker.workers.clear()
    w2 = _make_worker("ses_mgr")

    async def recovered_stream(ww, text, source, sess):
        attempts.append("recovered")
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", recovered_stream)
    worker._recover_pending_signals(w2, s)
    _run_consumer(w2)

    assert attempts == ["crash", "recovered"]
    assert s.queue_pending == []
    assert len(s.history) == 1, "recovery must reuse the existing user history entry"
    _cleanup()


def test_timeout_kill_then_same_task_id_retries(monkeypatch):
    """超时/kill 后仍在队列的 taskId 保持 pending，重启 worker 可继续执行。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    task = _make_task(1)
    s.queue_pending = [task]
    w = _make_worker("ses_mgr")
    worker._task_status["tid1"] = {
        "status": "pending", "workerId": w.worker_id, "taskId": "tid1",
    }

    # watchdog/kill 路径不应把仍在持久队列中的任务短路成永久 error。
    assert worker._mark_worker_tasks_error(w.worker_id, "worker killed") == 0
    assert worker._task_status["tid1"]["status"] == "pending"
    assert worker._task_status["tid1"]["workerId"] is None

    worker.workers.clear()
    w2 = _make_worker("ses_mgr")
    received = []

    async def recovered_stream(ww, text, source, sess):
        received.append(text)
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", recovered_stream)
    monkeypatch.setattr(_sess, "save_async", _recording_save([]))
    worker._recover_pending_signals(w2, s)
    _run_consumer(w2)

    assert received == ["job 1"]
    assert s.queue_pending == []
    assert worker._task_status["tid1"]["status"] == "done"
    _cleanup()


def test_task_rollback_on_save_failure(monkeypatch):
    """原子 save 失败 → 回滚内存态（history 弹回、claimed item 复原），可重投。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    t1, t2 = _make_task(1), _make_task(2)
    s.queue_pending = [t1, t2]
    w = _make_worker("ses_mgr")
    received = []

    async def failing_save(s):
        raise RuntimeError("disk full")

    async def fake_stream(ww, text, source, sess):
        received.append(text)
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)
    monkeypatch.setattr(_sess, "save_async", failing_save)

    async def scenario():
        await w.pending_signal.put({"type": "task_signal", "id": "task1"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert received == []
    assert s.queue_pending == [t1, t2], "queue restored with claimed item at original spot"
    assert s.history == [], "history append rolled back"
    assert worker._inflight_task_ids == set(), "claim released on rollback"
    _cleanup()


def test_duplicate_task_signal_no_double_claim(monkeypatch):
    """同一 id 重复信号：认领期间第二次被拒；确认出队后再认领 → not found（不双跑）。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    task = _make_task(1)
    s.queue_pending = [task]
    w = _make_worker("ses_mgr")
    monkeypatch.setattr(_sess, "save_async", _recording_save([]))

    async def scenario():
        # 第一次认领成功并打标记
        claimed = await worker._claim_pending_task(w, "task1")
        assert claimed is task
        assert "task1" in worker._inflight_task_ids
        # 认领期间重复信号 → 拒绝
        assert await worker._claim_pending_task(w, "task1") is None
        # 不存在的 id → None
        assert await worker._claim_pending_task(w, "nope") is None
        # 模拟移交确认后的标记释放（_consumer 的 finally 路径）
        worker._inflight_task_ids.discard("task1")

    asyncio.run(scenario())

    # 正常消费出队后，重复信号再次到达 → item 不在队列 → 不再消费
    received = []

    async def fake_stream(ww, text, source, sess):
        received.append(text)
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario2():
        await w.pending_signal.put({"type": "task_signal", "id": "task1"})
        await w.pending_signal.put({"type": "task_signal", "id": "task1"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario2())

    assert len(received) == 1 and "job 1" in received[0], "exactly one execution, no double-run"
    assert s.queue_pending == []
    assert len(s.history) == 1
    _cleanup()


def test_task_dequeue_removes_by_identity_not_value(monkeypatch):
    """出队按对象身份：值相等（同 id）的替身 item 不会被误删。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    task = _make_task(1)
    impostor = _make_task(1)  # 同 id 同内容、不同对象
    s.queue_pending = [task, impostor]
    w = _make_worker("ses_mgr")
    monkeypatch.setattr(_sess, "save_async", _recording_save([]))
    received = []

    async def fake_stream(ww, text, source, sess):
        received.append(text)
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "task_signal", "id": "task1"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert len(received) == 1 and "job 1" in received[0]
    assert len(s.queue_pending) == 1 and s.queue_pending[0] is impostor, \
        "only the claimed object is dequeued (by identity, not value)"
    _cleanup()


# ── report / task 互不误删 ──


def test_mixed_queue_report_and_task_mutual_exclusion(monkeypatch):
    """混合队列：report 消费不动 task item；task 认领不动 report item。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    task = _make_task(1)
    s.queue_pending = [_make_report(1), task, _make_report(2)]
    w = _make_worker("ses_mgr")
    monkeypatch.setattr(_sess, "save_async", _recording_save([]))
    received = []

    async def fake_stream(ww, text, source, sess):
        received.append((source, list(sess.queue_pending)))
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "report_signal"})
        await w.pending_signal.put({"type": "task_signal", "id": "task1"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert [src for src, _ in received] == ["report", "agent"]
    # report 交给模型时仍在队列中，task item 也原样保留；报告终态后才确认出队
    assert received[0][1] == [_make_report(1), task, _make_report(2)]
    assert received[1][1] == [task], "report acknowledgement must not touch task item"
    # task 消费后：队列清空
    assert s.queue_pending == []
    _cleanup()


# ── 恢复对账（jsonl-先写崩溃窗口去重）──


def test_reconcile_report_already_delivered_retries_without_duplicate_history(monkeypatch):
    """jsonl-先写崩溃恢复：复用 history 用户项，但重试报告执行。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    report = _make_report(1)
    # 模拟崩溃现场：jsonl 已写（history 条目含投递标记元数据）、主文件未写（队列项还在）
    s.history.append({"role": "user", "content": "@@@@by agent : ses_child",
                      "delivered_keys": [worker._delivery_key(report)]})
    s.queue_pending = [report]
    w = _make_worker("ses_mgr")
    monkeypatch.setattr(_sess, "save_async", _recording_save([]))
    received = []

    async def fake_stream(ww, text, source, sess):
        received.append(text)
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "report_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert len(received) == 1 and "r1" in received[0], "in-flight report must be retried"
    assert s.queue_pending == [], "report is cleared only after terminal result"
    assert len(s.history) == 1, "history must not grow (no duplicate injection)"
    _cleanup()


def test_reconcile_task_already_delivered_retries_without_duplicate_history(monkeypatch):
    """jsonl-先写崩溃恢复：复用已有 history 用户项，但重试执行。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    task = _make_task(1)
    # 模拟崩溃现场：jsonl 已写（含标记元数据）、主文件未写（队列项还在）
    s.history.append({"role": "user", "content": "job 1",
                      "delivered_keys": [worker._delivery_key(task)]})
    s.queue_pending = [task]
    w = _make_worker("ses_mgr")
    monkeypatch.setattr(_sess, "save_async", _recording_save([]))
    received = []

    async def fake_stream(ww, text, source, sess):
        received.append(text)
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "task_signal", "id": "task1"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert received == ["job 1"], "in-flight task must be retried after crash"
    assert s.queue_pending == [], "task is cleared only after terminal result"
    assert len(s.history) == 1
    assert worker._inflight_task_ids == set()
    _cleanup()


def test_reconcile_partial_delivered_reports(monkeypatch):
    """部分已投递：整批重试，只有新报告新增 history 标记。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    delivered, fresh = _make_report(1), _make_report(2)
    s.history.append({"role": "user", "content": "x",
                      "delivered_keys": [worker._delivery_key(delivered)]})
    s.queue_pending = [delivered, fresh]
    w = _make_worker("ses_mgr")
    save_log = []
    monkeypatch.setattr(_sess, "save_async", _recording_save(save_log))
    received = []

    async def fake_stream(ww, text, source, sess):
        received.append(text)
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "report_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert len(received) == 1
    assert "r1" in received[0] and "r2" in received[0], \
        "the in-flight batch must be retried as a whole"
    assert s.queue_pending == [], "batch is cleared only after terminal result"
    consumer_saves = [e for e in save_log if e[0]]
    assert len(consumer_saves) == 1, "single handoff save before batch execution"
    assert consumer_saves[0][1] == [delivered, fresh]
    _cleanup()


def test_reconcile_mismatched_content_redelivers(monkeypatch):
    """对账边界：同 taskId 但内容不同（status 变了）→ 指纹不同 → 不误判、正常重投；
    标记格式差异（大小写）→ 不命中 → 正常重投（宁可重复不丢）。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    # 内容不同（status: done vs error），taskId 相同 → key 不同 → 不命中
    changed = dict(_make_report(1), status="error", result="boom")
    s.history.append({"role": "user", "content": "x",
                      "delivered_keys": [worker._delivery_key(_make_report(1))]})
    # 格式差异：history 里是错误 key（大小写/指纹不同），不会命中真实项
    s.history.append({"role": "user", "content": "y",
                      "delivered_keys": ["Report:whatever:000000000000"]})
    s.queue_pending = [changed]
    w = _make_worker("ses_mgr")
    monkeypatch.setattr(_sess, "save_async", _recording_save([]))
    received = []

    async def fake_stream(ww, text, source, sess):
        received.append(text)
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "report_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert len(received) == 1, "content/format mismatch must not suppress redelivery"
    assert "boom" in received[0]
    assert s.queue_pending == []
    assert len(s.history) == 3
    _cleanup()


def test_reconcile_scans_tail_window_only(monkeypatch):
    """对账只扫 history 尾部 _DELIVERY_SCAN_DEPTH 条：标记在窗口外 → 视为未投递重投。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    report = _make_report(1)
    # 标记埋在扫描窗口之外
    depth = worker._DELIVERY_SCAN_DEPTH
    for i in range(depth):
        s.history.append({"role": "user", "content": f"filler {i}"})
    s.history.append({"role": "user", "content": "old",
                      "delivered_keys": [worker._delivery_key(report)]})
    for i in range(depth):
        s.history.append({"role": "user", "content": f"newer {i}"})
    s.queue_pending = [report]
    w = _make_worker("ses_mgr")
    monkeypatch.setattr(_sess, "save_async", _recording_save([]))
    received = []

    async def fake_stream(ww, text, source, sess):
        received.append(text)
        await _complete_task(ww, sess)

    monkeypatch.setattr(worker, "_consumer_stream", fake_stream)

    async def scenario():
        await w.pending_signal.put({"type": "report_signal"})
        await w.pending_signal.put(None)
        await worker._consumer(w)

    asyncio.run(scenario())

    assert len(received) == 1, "mark outside tail window → treated as undelivered"
    assert s.queue_pending == []
    _cleanup()


def test_recover_pending_signals_mixed_queue(monkeypatch):
    """混合积压队列 respawn：task 按 id 发信号、report 合并发一个 report_signal。"""
    _cleanup()
    s = _setup_session("ses_mgr")
    s.queue_pending = [_make_report(1), _make_task(1), _make_task(2)]
    w = _make_worker("ses_mgr")

    worker._recover_pending_signals(w, s)

    signals = []
    while not w.pending_signal.empty():
        signals.append(w.pending_signal.get_nowait())
    task_ids = [sig.get("id") for sig in signals if sig.get("type") == "task_signal"]
    assert task_ids == ["task1", "task2"]
    assert {"type": "report_signal"} in signals
    _cleanup()


# ── 存量清理：加载时剥离旧版正文标记行 ──


def test_strip_delivery_marks_helper():
    """旧版 `[delivered: ...]` 独立行被剥离；行内提及与非标记格式保留。"""
    from packages.core.session import _strip_delivery_marks

    hist = _strip_delivery_marks([
        # task 单标记行 + 正文 → 前缀剥离、正文保留
        {"role": "user", "content": f"[delivered: task:task1:{'a' * 12}]\njob 1"},
        # report 批量多标记行 + 空行 + 正文 → 剥离后不残留前导空行
        {"role": "user", "content":
            f"[delivered: report:t9:{'b' * 12}]\n[delivered: report:anon:{'c' * 12}]"
            "\n\n@@@@by agent : x"},
        # 行内提及（非独立行）→ 保留，不误删
        {"role": "user", "content": "see the mark [delivered: task:x:aab] here"},
        # 指纹非 12 位十六进制 → 不命中，保留
        {"role": "user", "content": "[delivered: task:weird:zzz]"},
        # 无标记 → 原样
        {"role": "user", "content": "plain"},
    ])

    assert hist[0]["content"] == "job 1"
    assert hist[1]["content"] == "@@@@by agent : x"
    assert hist[2]["content"] == "see the mark [delivered: task:x:aab] here"
    assert hist[3]["content"] == "[delivered: task:weird:zzz]"
    assert hist[4]["content"] == "plain"


def test_legacy_marks_stripped_on_session_load(tmp_path, monkeypatch):
    """加载路径集成：主文件 history（旧格式）经 get() 加载后正文标记行已剥离。"""
    import json
    from packages.core import session as sess_mod

    hist = [{"role": "user", "content": f"[delivered: task:task1:{'a' * 12}]\njob 1"},
            {"role": "user", "content": "untouched"}]
    monkeypatch.setattr(sess_mod, "SESSION_DIR", tmp_path)
    sess_mod._cache.clear()
    (tmp_path / "ses_legacy.json").write_text(
        json.dumps({"id": "ses_legacy", "name": "t", "history": hist}), encoding="utf-8")

    try:
        s = sess_mod.get("ses_legacy")
        assert s is not None
        assert s.history[0]["content"] == "job 1"
        assert s.history[1]["content"] == "untouched"
    finally:
        sess_mod._cache.clear()
