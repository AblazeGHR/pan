"""Queue recovery retains its bounded receipts without parsing old JSONL rows."""
import hashlib
import json

import pytest

from packages.core import session as store
from packages.core import worker


@pytest.mark.parametrize("projection_mode", ["complete", "stale", "incomplete", "crash_tail"])
def test_shallow_queue_receipts_match_compatibility_tail(tmp_path, monkeypatch, projection_mode):
    monkeypatch.setattr(store, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(store, "_cache", {})
    rows = [{"role": "user", "content": f"row {i} 中文🙂", "taskId": f"task-{i}",
             "clientMessageId": f"client-{i}"} for i in range(5000)]
    s = store.create("queue recovery", history=rows)
    store._cache.clear()
    s = store.get(s.id, load_history=False)
    history = store._history_path(s.id)
    if projection_mode == "stale":
        s.summary_projection["history_total"] += 1
    elif projection_mode == "incomplete":
        s.summary_projection["history_total"] = None
    elif projection_mode == "crash_tail":
        with history.open("ab") as handle:
            handle.write(b'{"role":')
    original_bytes = hashlib.sha256(history.read_bytes()).digest()
    limit = max(store.ACCEPTED_INPUT_ID_MAX, store.QUEUE_IDEMPOTENCY_INDEX_MAX_ENTRIES)
    expected, _ = store._history_page_from_jsonl(history, before=0, limit=limit)
    assert worker._history_idempotency_rows(s) == expected == rows[-limit:]
    worker._ensure_idempotency_index(s)
    assert s.queue_idempotency_index["taskId"]["task-4999"]
    assert s.queue_idempotency_index["clientMessageId"]["client-4999"]
    assert not s._history_loaded
    assert hashlib.sha256(history.read_bytes()).digest() == original_bytes


def test_complete_queue_projection_decodes_only_recovery_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(store, "_cache", {})
    rows = [{"role": "user", "content": str(i)} for i in range(5000)]
    s = store.create("bounded decoder", history=rows)
    store._cache.clear()
    s = store.get(s.id, load_history=False)
    decode = json.loads
    calls = []
    def counted(value, *args, **kwargs):
        calls.append(1)
        return decode(value, *args, **kwargs)
    monkeypatch.setattr(json, "loads", counted)
    result = worker._history_idempotency_rows(s)
    assert result == rows[-store.QUEUE_IDEMPOTENCY_INDEX_MAX_ENTRIES:]
    assert len(calls) == len(result)
