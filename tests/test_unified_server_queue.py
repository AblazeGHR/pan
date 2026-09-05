"""第一版统一服务端队列的核心语义测试。"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as sess  # noqa: E402
from packages.core import worker  # noqa: E402
from packages.core.adapters import CbcAdapter  # noqa: E402
import packages.web.server as server  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_recovery_spawn(monkeypatch):
    """无活 worker 的入队路径会调度立即恢复（fire-and-forget 任务）。

    若让它真实 spawn cbc，asyncio.run 收尾取消 spawn 中途的 recovery 任务
    会让 Windows Proactor 的 waiter future 永不完成 → _cancel_all_tasks
    死锁 → 整个 pytest 进程挂死。本文件只测队列语义，统一 stub；
    recorder 供测试断言「recovery 确实被调度」的回归（yield 计数列表）。
    """
    spawned = []

    async def _no_spawn(session_id):
        spawned.append(session_id)
        return "spawn suppressed by test fixture"

    monkeypatch.setattr(worker, "create_worker", _no_spawn)
    yield spawned


async def _save(_session):
    return None


def _cleanup():
    for retry in list(worker._queue_retry_tasks.values()):
        retry.cancel()
    worker._queue_retry_tasks.clear()
    worker.workers.clear()
    worker._task_status.clear()
    worker._inflight_task_ids.clear()
    worker._queue_locks.clear()
    sess._cache.clear()
    worker.set_broadcaster(None)


def _session(sid="ses-unified", **kwargs):
    value = sess.Session(id=sid, name=sid, **kwargs)
    sess._cache[sid] = value
    return value


def test_user_enqueue_is_server_idempotent_and_uses_native_queue_id(
        monkeypatch, _no_real_recovery_spawn):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()

    async def scenario():
        first = await worker.enqueue_user_message(value.id, "一次", "browser-1")
        second = await worker.enqueue_user_message(value.id, "一次", "browser-1")
        return first, second

    first, second = asyncio.run(scenario())
    assert first["queueItemId"].startswith("q_")
    assert second["duplicate"] is True
    assert second["queueItemId"] == first["queueItemId"]
    assert len(value.queue_pending) == 1
    assert len(value.queue_delivery_ledger) == 1
    assert value.queue_pending[0]["source"] == "user"
    assert value.queue_pending[0]["kind"] == "task"
    assert _no_real_recovery_spawn == [value.id], \
        "offline user enqueue must schedule immediate recovery"
    _cleanup()


def test_task_id_stays_idempotent_after_pending_row_is_removed(monkeypatch):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()

    async def scenario():
        item, error = await worker._persist_task_item(
            value, "原任务", "agent", None, "task-once", None)
        assert error is None
        value.queue_pending.remove(item)
        value.queue_delivery_ledger[item["queueItemId"]]["deliveryState"] = "sent_to_cli"
        duplicate, duplicate_error = await worker._persist_task_item(
            value, "重试不应执行", "agent", None, "task-once", None)
        return duplicate, duplicate_error

    duplicate, duplicate_error = asyncio.run(scenario())
    assert duplicate is not None and duplicate_error is None
    assert duplicate["queueItemId"].startswith("q_")
    assert value.queue_pending == []
    _cleanup()


def test_ledger_receipts_deduplicate_client_and_task_keys_after_pending_delete(monkeypatch):
    _cleanup()


def test_task_id_retry_after_handoff_returns_durable_receipt(monkeypatch):
    """A stale in-memory error must not hide a task already handed to the CLI."""
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()
    item, error = asyncio.run(worker._persist_task_item(
        value, "首次发送", "agent", None, "task-receipt", None))
    assert error is None
    stdin = _Stdin()
    current = _write_worker(value, item, stdin)
    current._current_task_id = "task-receipt"
    asyncio.run(worker._consumer_stream(current, "首次发送", "agent", value))
    receipt = dict(value.queue_delivery_ledger[item["queueItemId"]])
    worker._task_status["task-receipt"] = {
        "status": "error", "result": "worker exited", "taskId": "task-receipt",
    }

    result = asyncio.run(worker.assign(
        value.id, "重复发送", source="agent", task_id="task-receipt"))

    assert result["status"] == "sent_to_cli"
    assert result["duplicate"] is True
    assert result["receipt"] == receipt
    assert value.queue_pending == []
    assert len(stdin.writes) == 1
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()
    item, error = asyncio.run(worker._persist_task_item(
        value, "首次发送", "agent", None, "task-once", "client-once"))
    assert error is None
    stdin = _Stdin()
    current = _write_worker(value, item, stdin)

    asyncio.run(worker._consumer_stream(current, "首次发送", "agent", value))
    receipt = dict(value.queue_delivery_ledger[item["queueItemId"]])

    duplicate = asyncio.run(worker.enqueue_user_message(
        value.id, "幂等重试", "client-once"))
    task_duplicate, task_error = asyncio.run(worker._persist_task_item(
        value, "幂等重试", "agent", None, "task-once", None))

    assert duplicate["duplicate"] is True
    assert duplicate["queueItemId"] == item["queueItemId"]
    assert duplicate["item"] == receipt
    assert task_duplicate is not None and task_error is None
    assert task_duplicate["queueItemId"] == item["queueItemId"]
    assert value.queue_pending == []
    assert len(value.queue_delivery_ledger) == 1
    assert len(stdin.writes) == 1
    _cleanup()


@pytest.mark.parametrize("state", ["sent_to_cli", "write_failed", "unknown_after_crash"])
def test_server_pending_snapshot_hides_nonretryable_ledger_rows(monkeypatch, state):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()
    item, error = asyncio.run(worker._persist_task_item(
        value, "已处理", "user", None, None, f"snapshot-{state}", None))
    assert error is None
    value.queue_pending.remove(item)
    value.queue_delivery_ledger[item["queueItemId"]]["deliveryState"] = state

    result = asyncio.run(server.api_session_queue(value.id))

    assert result["queueRevision"] == value.queue_revision
    assert result["items"] == []
    assert item["queueItemId"] in value.queue_delivery_ledger
    _cleanup()


def test_protocol_serialization_is_complete_utf8_before_delivery():
    encoded = worker._serialize_for_cli(CbcAdapter(), "中文🙂")
    decoded = encoded.decode("utf-8")
    assert json.loads(decoded)["message"]["content"][0]["text"] == "中文🙂"


def test_report_and_qq_same_content_get_distinct_native_ids(monkeypatch):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    child = _session("ses-child")
    manager = _session("ses-manager", managed=[child.id], report_subscriptions={child.id})
    child.managed_by = manager.id
    manager.qq_subscriptions.add("user:42")
    async def no_wake(*_args, **_kwargs):
        return None
    monkeypatch.setattr(worker, "_wake_worker", no_wake)

    async def scenario():
        await worker._enqueue_report(child.id, "done", "same", "t1", "w1")
        return await worker.enqueue_qq_reminder("user", "42", text="same")

    assert asyncio.run(scenario()) == 1
    assert len(manager.queue_pending) == 2
    assert {item["kind"] for item in manager.queue_pending} == {"report", "qq"}
    assert len({item["queueItemId"] for item in manager.queue_pending}) == 2
    _cleanup()


def test_queue_api_edits_only_queued_user_and_reorders_all_sources(monkeypatch):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()
    user, _ = asyncio.run(worker._persist_task_item(value, "user", "user", None, None, "u"))
    agent, _ = asyncio.run(worker._persist_task_item(value, "agent", "agent", None, "a", None))
    report = {"type": "report", "kind": "report", "id": "q-report",
              "queueItemId": "q-report", "source": "report", "result": "report",
              "deliveryState": "queued"}
    value.queue_pending.append(report)
    value.queue_delivery_ledger[report["id"]] = dict(report)
    user_id = user["queueItemId"]
    agent_id = agent["queueItemId"]
    events = []

    async def capture(event):
        events.append(event)

    worker.set_broadcaster(capture)

    edited = asyncio.run(server.api_session_queue_update(
        value.id, user_id, {"text": "user edited", "expectedRevision": 1}))
    assert edited["ok"] is True
    assert value.queue_pending[0]["text"] == "user edited"
    assert events[-1]["type"] == "queue.item_updated"
    assert events[-1]["queueItemId"] == user_id
    assert events[-1]["item"]["text"] == "user edited"
    denied = asyncio.run(server.api_session_queue_update(
        value.id, agent_id, {"text": "spoof"}))
    assert denied["error"]["code"] == "queue_item_readonly"
    report_denied = asyncio.run(server.api_session_queue_update(
        value.id, "q-report", {"text": "spoof"}))
    assert report_denied["error"]["code"] == "queue_item_readonly"

    ordered = asyncio.run(server.api_session_queue_order(
        value.id, {"orderedIds": ["q-report", agent_id, user_id],
                   "expectedQueueRevision": value.queue_revision}))
    assert [item["id"] for item in ordered["items"]] == ["q-report", agent_id, user_id]
    _cleanup()


class _Process:
    returncode = None


class _Stdin:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.writes = []

    def write(self, data):
        if self.mode == "fail":
            raise OSError("pipe closed")
        self.writes.append(data)
        if self.mode == "short":
            return len(data) - 1
        return len(data)

    async def drain(self):
        if self.mode == "drain_fail":
            raise OSError("drain failed")
        if self.mode == "cancel":
            raise asyncio.CancelledError()


def _write_worker(value, item, stdin):
    process = _Process()
    process.stdin = stdin
    value.queue_pending = [item]
    value.queue_delivery_ledger[item["queueItemId"]] = dict(item)
    value.queue_delivery_ledger[item["queueItemId"]]["deliveryState"] = "reserved"
    current = worker.Worker(
        worker_id="w-unified", session_id=value.id, adapter=CbcAdapter(),
        status="idle", process=process, pending_signal=asyncio.Queue(),
        _task_done=asyncio.Event(), _hist_flush_event=asyncio.Event(),
        _current_queue_item=item, _current_serialized=b'{"message":"x"}',
    )
    worker.workers[current.worker_id] = current
    return current


@pytest.mark.parametrize("mode,expected", [
    ("ok", "sent_to_cli"),
    ("fail", "queued"),
    ("short", "queued"),
    ("drain_fail", "queued"),
    ("cancel", "queued"),
])
def test_cli_write_outcomes_commit_or_requeue(monkeypatch, mode, expected):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()
    item = {"type": "task", "kind": "task", "queueItemId": "q-write",
            "id": "q-write", "source": "user", "text": "x",
            "deliveryState": "reserved", "revision": 1}
    current = _write_worker(value, item, _Stdin(mode))

    async def scenario():
        if mode == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await worker._consumer_stream(current, "x", "user", value)
        else:
            await worker._consumer_stream(current, "x", "user", value)

    asyncio.run(scenario())
    assert value.queue_delivery_ledger[item["queueItemId"]]["deliveryState"] == expected
    if expected == "sent_to_cli":
        assert value.queue_pending == []
    else:
        assert value.queue_pending == [item]
    _cleanup()


def test_successful_delivery_broadcasts_user_message_after_durable_commit(monkeypatch):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()
    item = {"type": "task", "kind": "task", "queueItemId": "q-event",
            "id": "q-event", "source": "user", "text": "shown",
            "deliveryState": "reserved", "revision": 1}
    current = _write_worker(value, item, _Stdin("ok"))
    events = []

    async def capture(event):
        events.append(event)

    worker.set_broadcaster(capture)
    asyncio.run(worker._consumer_stream(current, "shown", "user", value))

    delivered_index = next(i for i, event in enumerate(events)
                           if event.get("type") == "queue.item_delivered")
    delivered = events[delivered_index]
    assert value.queue_pending == []
    assert value.queue_delivery_ledger["q-event"]["deliveryState"] == "sent_to_cli"
    assert delivered["queueItemIds"] == ["q-event"]
    assert delivered["messages"] == [{
        "role": "user", "content": "shown", "queueItemIds": ["q-event"],
    }]
    assert delivered_index < next(i for i, event in enumerate(events)
                                  if event.get("type") == "queue.snapshot")
    _cleanup()


class _OneShotStdout:
    async def read(self, _size):
        return b""


class _OneShotProcess:
    returncode = 0
    stdout = _OneShotStdout()

    async def wait(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _queue_item(value, qid, *, source="user", kind="task", text="x"):
    item = {"type": kind, "kind": kind, "queueItemId": qid, "id": qid,
            "source": source, "text": text, "deliveryState": "reserved",
            "revision": 1}
    value.queue_pending.append(item)
    value.queue_delivery_ledger[qid] = dict(item)
    return item


def test_one_shot_spawn_success_dequeues_after_argv_handoff(monkeypatch):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()
    item = _queue_item(value, "q-one-shot")
    current = worker.Worker(
        worker_id="w-one-shot", session_id=value.id, adapter=CbcAdapter(),
        status="idle", process=None, pending_signal=asyncio.Queue(),
        _task_done=asyncio.Event(), _hist_flush_event=asyncio.Event(),
        _current_queue_item=item,
    )
    worker.workers[current.worker_id] = current
    spawned = []

    async def create(*args, **kwargs):
        spawned.append(args)
        return _OneShotProcess()

    monkeypatch.setattr(current.adapter, "oneshot_args", lambda _s, _text: ["fake-cli", "prompt"])
    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", create)

    asyncio.run(worker._consumer_oneshot(current, "x", "user", value))

    assert spawned == [("fake-cli", "prompt")]
    assert value.queue_pending == []
    assert value.queue_delivery_ledger["q-one-shot"]["deliveryState"] == "sent_to_cli"
    _cleanup()


def test_one_shot_spawn_failure_requeues_same_item(monkeypatch):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()
    item = _queue_item(value, "q-one-shot-fail")
    current = worker.Worker(
        worker_id="w-one-shot-fail", session_id=value.id, adapter=CbcAdapter(),
        status="idle", process=None, pending_signal=asyncio.Queue(),
        _task_done=asyncio.Event(), _hist_flush_event=asyncio.Event(),
        _current_queue_item=item,
    )
    worker.workers[current.worker_id] = current
    monkeypatch.setattr(current.adapter, "oneshot_args", lambda _s, _text: ["missing-cli"])

    async def fail(*_args, **_kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", fail)
    asyncio.run(worker._consumer_oneshot(current, "x", "user", value))

    assert [entry["queueItemId"] for entry in value.queue_pending] == ["q-one-shot-fail"]
    assert value.queue_pending[0]["deliveryState"] == "queued"
    assert value.queue_delivery_ledger["q-one-shot-fail"]["deliveryState"] == "queued"
    _cleanup()


def test_report_batch_write_failure_requeues_every_item(monkeypatch):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()
    first = _queue_item(value, "q-report-1", source="report", kind="report", text="r1")
    second = _queue_item(value, "q-report-2", source="report", kind="report", text="r2")
    current = _write_worker(value, first, _Stdin("fail"))
    current._current_queue_item = None
    current._current_report_items = [first, second]
    current._current_serialized = b'{"message":"batch"}'
    value.queue_pending = [first, second]
    asyncio.run(worker._consumer_stream(current, "batch", "report", value))

    assert [entry["queueItemId"] for entry in value.queue_pending] == [
        "q-report-1", "q-report-2"]
    assert all(entry["deliveryState"] == "queued" for entry in value.queue_pending)
    _cleanup()


def test_recovery_returns_reserved_to_queued_and_signals(monkeypatch):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()
    item = {"type": "task", "kind": "task", "queueItemId": "q-crash",
            "id": "q-crash", "source": "user", "text": "x",
            "deliveryState": "reserved", "revision": 1}
    value.queue_delivery_ledger[item["queueItemId"]] = dict(item)
    current = worker.Worker(
        worker_id="w-recover", session_id=value.id, adapter=CbcAdapter(),
        status="idle", process=None, pending_signal=asyncio.Queue(),
        _task_done=asyncio.Event(), _hist_flush_event=asyncio.Event(),
    )
    assert worker._recover_pending_signals(current, value) is True
    assert value.queue_delivery_ledger[item["queueItemId"]]["deliveryState"] == "queued"
    assert len(value.queue_pending) == 1
    assert value.queue_pending[0]["queueItemId"] == item["queueItemId"]
    assert value.queue_pending[0]["lastDeliveryState"] == "reserved"
    assert current.pending_signal.get_nowait() == {"type": "queue_signal"}
    _cleanup()


def test_claim_and_delete_are_serialized_without_double_dispatch(monkeypatch):
    _cleanup()
    monkeypatch.setattr(sess, "save_async", _save)
    value = _session()
    item, error = asyncio.run(worker._persist_task_item(
        value, "once", "user", None, None, "claim-delete"))
    assert error is None
    current = worker.Worker(
        worker_id="w-race", session_id=value.id, adapter=CbcAdapter(),
        status="idle", process=_Process(), pending_signal=asyncio.Queue(),
        _task_done=asyncio.Event(), _hist_flush_event=asyncio.Event(),
    )
    worker.workers[current.worker_id] = current

    async def scenario():
        return await asyncio.gather(
            worker._claim_pending_task(current, item["id"]),
            server.api_session_queue_delete(value.id, item["id"]),
        )

    claimed, deleted = asyncio.run(scenario())
    assert (claimed is not None) ^ (deleted.get("ok") is True)
    if claimed is not None:
        assert len(value.queue_pending) == 1
        assert value.queue_pending[0]["deliveryState"] == "reserved"
    else:
        assert value.queue_pending == []
    if claimed is not None:
        assert value.queue_delivery_ledger[item["id"]]["deliveryState"] == "reserved"
    _cleanup()
