"""T-062.6b: bounded receipts, indexed idempotency, and cold history pages."""

import asyncio
import json
import time

from packages.core import session as _sess
from packages.core import worker
from packages.web import server


def _session(session_id: str) -> _sess.Session:
    value = _sess.Session(
        id=session_id,
        name=session_id,
        adapter="cbc",
        model="test-model",
    )
    _sess._cache[session_id] = value
    return value


def _task_item(queue_item_id: str, *, client_id: str | None = None,
               task_id: str | None = None, state: str = "sent_to_cli",
               receipt_at: float | None = None) -> dict:
    return {
        "type": "task",
        "kind": "task",
        "id": queue_item_id,
        "queueItemId": queue_item_id,
        "text": "a large provider body that may be compacted",
        "source": "agent",
        "taskId": task_id,
        "taskIdSource": "assign" if task_id else None,
        "clientMessageId": client_id,
        "deliveryState": state,
        "dispatchState": state,
        "createdAt": receipt_at if receipt_at is not None else time.time(),
        "receiptAt": receipt_at if receipt_at is not None else time.time(),
    }


def test_large_ledger_compaction_keeps_pending_and_retained_retry_receipts(monkeypatch):
    value = _session("ses-retention")
    monkeypatch.setattr(_sess, "QUEUE_RECEIPT_MAX_ENTRIES", 2)
    old = time.time() - 3600

    pending = _task_item("q-pending", client_id="cm-pending",
                         state=worker._DELIVERY_RESERVED, receipt_at=old)
    value.queue_pending = [pending]
    worker._remember_queue_item(value, pending, worker._DELIVERY_RESERVED)
    for index in range(5):
        item = _task_item(
            f"q-{index}", client_id=f"cm-{index}", task_id=f"task-{index}",
            receipt_at=old - index,
        )
        worker._remember_queue_item(value, item, worker._DELIVERY_SENT)

    changed = worker._compact_delivery_receipts(value)

    assert changed is True
    assert "q-pending" in value.queue_delivery_ledger
    assert value.queue_delivery_ledger["q-pending"]["deliveryState"] == "reserved"
    terminal_ids = {
        key for key, record in value.queue_delivery_ledger.items()
        if record.get("deliveryState") == worker._DELIVERY_SENT
    }
    assert terminal_ids == {"q-0", "q-1"}
    assert all(
        value.queue_delivery_ledger[key].get("receiptOnly") is True
        and "text" not in value.queue_delivery_ledger[key]
        for key in terminal_ids
    )
    assert worker._find_queue_item_by_idempotency(
        value, client_message_id="cm-0")["receiptOnly"] is True
    assert worker._find_queue_item_by_idempotency(
        value, task_id="task-0")["queueItemId"] == "q-0"
    assert worker._find_queue_item_by_idempotency(
        value, client_message_id="cm-4") is None
    assert "cm-4" not in value.queue_idempotency_index["clientMessageId"]
    assert "task-4" not in value.queue_idempotency_index["taskId"]


def test_fresh_receipt_survives_zero_ttl_and_count_pressure(monkeypatch):
    value = _session("ses-fresh-receipt")
    monkeypatch.setattr(_sess, "QUEUE_RECEIPT_MAX_ENTRIES", 0)
    monkeypatch.setattr(_sess, "QUEUE_RECEIPT_TTL_SEC", 0)
    item = _task_item("q-fresh", client_id="cm-fresh", task_id="task-fresh")
    worker._remember_queue_item(value, item, worker._DELIVERY_SENT)

    worker._compact_delivery_receipts(value)

    assert "q-fresh" in value.queue_delivery_ledger
    assert worker._find_queue_item_by_idempotency(
        value, client_message_id="cm-fresh")["queueItemId"] == "q-fresh"
    assert worker._find_queue_item_by_idempotency(
        value, task_id="task-fresh")["queueItemId"] == "q-fresh"


def test_cold_load_count_bound_protects_fresh_terminal_receipts(monkeypatch):
    monkeypatch.setattr(_sess, "QUEUE_RECEIPT_MAX_ENTRIES", 1)
    now = time.time()
    ledger = {
        f"q-{index}": _task_item(
            f"q-{index}", client_id=f"cm-{index}", task_id=f"task-{index}",
            receipt_at=now,
        )
        for index in range(3)
    }

    restored = _sess.Session._from_data({
        "id": "ses-cold-fresh",
        "name": "cold-fresh",
        "queue_delivery_ledger": ledger,
    })

    # The cold loader may defer count pressure, but it must not throw away a
    # receipt that is still inside the crash/retry protection window.
    assert set(restored.queue_delivery_ledger) == set(ledger)


def test_old_session_migrates_to_o1_task_and_client_indexes():
    value = _session("ses-index-migration")
    item = _task_item("q-legacy", client_id="cm-legacy", task_id="task-legacy")
    value.queue_delivery_ledger[item["queueItemId"]] = dict(item)
    assert value.queue_idempotency_index == {}
    assert value._idempotency_index_built is False

    by_client = worker._find_queue_item_by_idempotency(
        value, client_message_id="cm-legacy")
    by_task = worker._find_queue_item_by_idempotency(
        value, task_id="task-legacy")

    assert by_client["queueItemId"] == "q-legacy"
    assert by_task["queueItemId"] == "q-legacy"
    assert value.queue_idempotency_index["clientMessageId"]["cm-legacy"] == {
        "queueItemId": "q-legacy",
        "seenAt": item["receiptAt"],
    }
    assert value.queue_idempotency_index["taskId"]["task-legacy"]["queueItemId"] == "q-legacy"

    restored = _sess.Session._from_data(value.to_dict())
    assert restored.queue_idempotency_index["clientMessageId"]["cm-legacy"] == {
        "queueItemId": "q-legacy",
        "seenAt": item["receiptAt"],
    }
    assert worker._find_queue_item_by_idempotency(
        restored, task_id="task-legacy")["queueItemId"] == "q-legacy"

    class NoScanList(list):
        def __iter__(self):
            raise AssertionError("hot idempotency lookup scanned queue_pending")

    # The first lookup above performs the one-time compatibility build.  Once
    # the durable index is marked built, retries must not inspect the pending
    # list again, even if it has become very large.
    value.queue_pending = NoScanList()
    assert worker._find_queue_item_by_idempotency(
        value, client_message_id="cm-legacy")["queueItemId"] == "q-legacy"


def test_same_client_message_and_task_id_concurrently_create_one_item(monkeypatch):
    value = _session("ses-concurrent-index")

    async def no_save(_value):
        return None

    monkeypatch.setattr(_sess, "save_async", no_save)
    monkeypatch.setattr(worker, "_schedule_session_recovery", lambda *_args, **_kwargs: None)

    async def scenario():
        browser_results = await asyncio.gather(*(
            worker.enqueue_user_message(
                value.id, f"browser retry {index}", "cm-concurrent",
            )
            for index in range(20)
        ))
        task_results = await asyncio.gather(*(
            worker._persist_task_item(
                value, f"assign retry {index}", "agent", None,
                "task-concurrent", None,
            )
            for index in range(20)
        ))
        return browser_results, task_results

    browser_results, task_results = asyncio.run(scenario())

    browser_ids = {result["queueItemId"] for result in browser_results}
    task_items = [item for item, error in task_results if item is not None and error is None]
    assert len(browser_ids) == 1
    assert len({item["queueItemId"] for item in task_items}) == 1
    assert len(value.queue_pending) == 2
    assert len(value.queue_delivery_ledger) == 2
    assert value.queue_idempotency_index["clientMessageId"]["cm-concurrent"]
    assert value.queue_idempotency_index["taskId"]["task-concurrent"]


def test_failed_receipt_save_rolls_back_index_and_allows_retry(monkeypatch):
    value = _session("ses-retry-index")
    calls = 0

    async def fail_once(_value):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic receipt write failure")

    monkeypatch.setattr(_sess, "save_async", fail_once)

    async def scenario():
        first = await worker._persist_task_item(
            value, "first attempt", "user", None, None, "cm-retry",
        )
        second = await worker._persist_task_item(
            value, "retry after failed write", "user", None, None, "cm-retry",
        )
        return first, second

    first, second = asyncio.run(scenario())

    assert first[0] is None
    assert "Failed to persist queued task" in (first[1] or "")
    assert second[1] is None
    assert second[0]["clientMessageId"] == "cm-retry"
    assert len(value.queue_pending) == 1
    assert len(value.queue_delivery_ledger) == 1
    assert value.queue_idempotency_index["clientMessageId"]["cm-retry"][
        "queueItemId"] == second[0]["queueItemId"]


def test_history_page_and_default_session_list_stay_shallow_for_long_history():
    value = _sess.create(name="long-history", adapter="cbc", model="test-model")
    for index in range(1000):
        _sess.append_history(value, {
            "role": "assistant" if index % 2 else "user",
            "content": f"history-{index}",
        })
    _sess.save(value)
    session_id = value.id
    _sess._cache.clear()
    _sess._all_loaded = False

    shallow = _sess.get(session_id, load_history=False)
    assert shallow is not None
    assert shallow._history_loaded is False
    assert shallow.history == []

    page = _sess.history_page(session_id, limit=30)
    assert page["total"] == 1000
    assert [row["content"] for row in page["history"]] == [
        f"history-{index}" for index in range(970, 1000)
    ]
    assert _sess.get(session_id, load_history=False)._history_loaded is False

    response = asyncio.run(server.api_list_sessions(summary=0))
    listed = next(item for item in response["sessions"] if item["id"] == session_id)
    assert len(listed["history"]) == 50
    assert listed["historyTotal"] == 1000
    assert listed["historyTruncated"] is True
    assert _sess.get(session_id, load_history=False)._history_loaded is False

    history_response = asyncio.run(
        server.api_session_history(session_id, before=100, limit=10)
    )
    assert history_response["total"] == 1000
    assert history_response["start"] == 90
    assert history_response["history"][0]["content"] == "history-90"
    assert _sess.get(session_id, load_history=False)._history_loaded is False

    bounded = asyncio.run(server.api_get_session(session_id, historyLimit=12))
    assert len(bounded["history"]) == 12
    assert bounded["historyTotal"] == 1000
    assert bounded["historyTruncated"] is True
    assert _sess.get(session_id, load_history=False)._history_loaded is False


def test_shallow_queue_save_preserves_legacy_main_file_history():
    session_id = "ses-legacy-save"
    _sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    history = [
        {"role": "user", "content": "legacy question"},
        {"role": "assistant", "content": "legacy answer"},
        {"role": "user", "content": "legacy follow-up"},
    ]
    (_sess._path(session_id)).write_text(json.dumps({
        "id": session_id,
        "name": "legacy-save",
        "adapter": "cbc",
        "model": "test-model",
        "history": history,
    }), encoding="utf-8")

    shallow = _sess.get(session_id, load_history=False)
    assert shallow is not None and shallow._history_loaded is False
    item = _task_item("q-legacy-save", client_id="cm-legacy-save",
                      state=worker._DELIVERY_QUEUED)
    shallow.queue_pending.append(item)
    worker._remember_queue_item(shallow, item, worker._DELIVERY_QUEUED)

    asyncio.run(worker._save_receipt(shallow))
    _sess._cache.clear()
    loaded = _sess.get(session_id)

    assert loaded is not None
    assert loaded.history == history
    assert loaded.queue_pending[0]["queueItemId"] == "q-legacy-save"
