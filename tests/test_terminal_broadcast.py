"""T-041 terminal persistence/broadcast and eventual usage tests."""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CbcAdapter


class _Process:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.returncode = None
        self.pid = 1001
        self.stdin = AsyncMock()
        self.stdout = self

    async def read(self, _size=65536):
        return self._chunks.pop(0) if self._chunks else b""


def _line(value):
    return (json.dumps(value) + "\n").encode()


def _reset():
    worker.workers.clear()
    worker._task_status.clear()
    worker._usage_enrichment_tasks.clear()
    worker._usage_enrichment_adapters.clear()
    worker._usage_enrichment_locks.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _stream_setup(sid="ses_t041_stream"):
    s = _sess.Session(id=sid, name="t041", adapter="cbc", model="m")
    s.cli_session_id = "cli-t041"
    _sess._cache[sid] = s
    w = worker.Worker(
        worker_id="worker-t041",
        session_id=sid,
        adapter=CbcAdapter(),
        status="idle",
        process=MagicMock(),
        pending_signal=asyncio.Queue(),
        _hist_flush_event=asyncio.Event(),
    )
    worker.workers[w.worker_id] = w
    return s, w


def test_stream_persists_before_result_then_idle_and_does_not_wait_for_enrich(monkeypatch):
    _reset()
    s, w = _stream_setup()
    saves = []
    events = []
    timeline = []
    enrich_started = []

    async def save(value):
        saves.append((value.last_result is not None, len(value.history)))
        timeline.append(("save", value.last_result is not None))

    async def broadcast(value):
        events.append(value)
        timeline.append(("broadcast", value.get("type"), value.get("status")))

    def slow_enrich(_session):
        enrich_started.append(time.monotonic())
        time.sleep(0.15)
        return None

    monkeypatch.setattr(_sess, "save_async", save)
    monkeypatch.setattr(w.adapter, "enrich_after_result", slow_enrich)
    worker.set_broadcaster(broadcast)
    w.process = _Process([
        _line({"type": "system", "subtype": "init", "session_id": "cli-t041"}),
        _line({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "answer"},
        ]}}),
        _line({"type": "result", "result": "answer", "is_error": False}),
    ])

    async def scenario():
        started = time.monotonic()
        await worker._read_stdout(w)
        elapsed = time.monotonic() - started
        result_index = next(i for i, e in enumerate(events)
                            if e.get("type") == "worker.result")
        idle_index = next(i for i, e in enumerate(events)
                          if e.get("type") == "worker.status"
                          and e.get("status") == "idle")
        assert result_index < idle_index
        assert any(has_result for has_result, _ in saves)
        saved_terminal_index = next(
            i for i, item in enumerate(timeline)
            if item == ("save", True)
        )
        result_timeline_index = next(
            i for i, item in enumerate(timeline)
            if item[0:2] == ("broadcast", "worker.result")
        )
        assert saved_terminal_index < result_timeline_index
        assert elapsed < 0.12, f"terminal path waited for enrich: {elapsed:.3f}s"
        # Let the scheduled task finish so this test also proves the call was
        # moved to a thread and remains eventual rather than being dropped.
        await worker._run_usage_enrichment(s.id)
        assert enrich_started

    try:
        asyncio.run(scenario())
    finally:
        _reset()


def test_oneshot_uses_same_terminal_result_idle_pair(monkeypatch, tmp_path):
    _reset()
    s = _sess.Session(id="ses_t041_oneshot", name="t041", adapter="cbc", model="m")
    s.workdir = str(tmp_path)
    _sess._cache[s.id] = s
    w = worker.Worker(
        worker_id="worker-t041-oneshot", session_id=s.id, adapter=CbcAdapter(),
        status="idle", process=None, pending_signal=asyncio.Queue(),
    )
    worker.workers[w.worker_id] = w
    events = []
    async def broadcast(value):
        events.append(value)
    worker.set_broadcaster(broadcast)

    class Proc(_Process):
        async def wait(self):
            return 0

        def kill(self):
            self.returncode = -1

    async def spawn(*_args, **_kwargs):
        return _oneshot_proc()

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)
    monkeypatch.setattr(_sess, "save_async", AsyncMock())

    def _oneshot_proc():
        return Proc([
            _line({"type": "system", "subtype": "init", "session_id": "cli-t041"}),
            _line({"type": "result", "result": "answer", "is_error": False}),
        ])

    try:
        asyncio.run(worker._consumer_oneshot(w, "hello", "user", s))
        result_index = next(i for i, e in enumerate(events)
                            if e.get("type") == "worker.result")
        idle_index = next(i for i, e in enumerate(events)
                          if e.get("type") == "worker.status"
                          and e.get("status") == "idle")
        assert result_index < idle_index
        assert s.last_result["result"] == "answer"
    finally:
        _reset()


def test_usage_failure_is_persisted_and_retried_serially(monkeypatch, tmp_path):
    _reset()
    s = _sess.Session(id="ses_t041_retry", name="t041", adapter="cbc", model="m")
    s.cli_session_id = "cli-t041"
    s.workdir = str(tmp_path)
    _sess._cache[s.id] = s
    attempts = []
    active = 0
    max_active = 0

    class Adapter:
        name = "cbc"

        def enrich_after_result(self, _session):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            attempts.append(1)
            try:
                time.sleep(0.01)
                if len(attempts) == 1:
                    raise RuntimeError("usage file is still being written")
                return [{"model": "m", "rawUsage": {"completion_tokens": 2}}]
            finally:
                active -= 1

    adapter = Adapter()
    worker._queue_usage_enrichment(
        s, adapter, task_id="task-t041", task_seq=1,
        worker_id="worker-t041", generation=1,
    )
    worker._queue_usage_enrichment(
        s, adapter, task_id="task-t041-2", task_seq=2,
        worker_id="worker-t041", generation=1,
    )
    monkeypatch.setattr(worker, "_ENRICH_RETRY_BASE_SEC", 0.001)
    monkeypatch.setattr(worker, "_ENRICH_RETRY_MAX_SEC", 0.001)
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")

    async def scenario():
        await asyncio.wait_for(
            asyncio.gather(
                worker._run_usage_enrichment(s.id),
                worker._run_usage_enrichment(s.id),
            ),
            timeout=1,
        )

    try:
        asyncio.run(scenario())
        assert attempts == [1, 1, 1]
        assert max_active == 1
        assert not s.usage_enrichment_pending
        assert s.total_usage["completion_tokens"] == 4
    finally:
        _reset()


def test_pending_usage_recovers_without_a_live_worker(monkeypatch, tmp_path):
    _reset()
    s = _sess.Session(id="ses_t041_recover", name="t041", adapter="cbc", model="m")
    s.cli_session_id = "cli-t041"
    s.usage_enrichment_pending = [{
        "key": "task:recover", "adapter": "cbc", "taskId": "recover",
        "taskSeq": 1, "workerId": "old-worker", "generation": 1,
        "state": "retrying", "attempts": 1, "nextAttemptAt": 0.0,
    }]
    _sess._cache[s.id] = s
    _sess.SESSION_DIR = tmp_path / "sessions"
    _sess.save(s)
    _sess._cache.clear()
    adapter = MagicMock()
    adapter.enrich_after_result.return_value = None
    monkeypatch.setattr(worker, "get_adapter", lambda _name: adapter)

    async def scenario():
        loaded = _sess.get(s.id)
        assert loaded is not None
        assert worker.recover_pending_usage_enrichment() == 1
        task = worker._usage_enrichment_tasks[s.id]
        await asyncio.wait_for(task, timeout=1)

    try:
        asyncio.run(scenario())
        assert not _sess.get(s.id).usage_enrichment_pending
        adapter.enrich_after_result.assert_called_once()
    finally:
        _reset()


def test_base_terminal_save_failure_sends_no_completion(monkeypatch):
    _reset()
    s, w = _stream_setup("ses_t041_save_fail")
    events = []

    async def fail_save(_session):
        raise OSError("disk unavailable")

    async def broadcast(value):
        events.append(value)

    monkeypatch.setattr(_sess, "save_async", fail_save)
    worker.set_broadcaster(broadcast)

    async def scenario():
        with pytest.raises(OSError):
            await worker._persist_terminal_state(w, s, "done", "answer")

    try:
        asyncio.run(scenario())
        assert not any(e.get("type") == "worker.result" for e in events)
    finally:
        _reset()


def test_duplicate_stream_result_is_published_once(monkeypatch):
    _reset()
    s, w = _stream_setup("ses_t041_duplicate")
    events = []

    async def broadcast(value):
        events.append(value)

    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    worker.set_broadcaster(broadcast)
    w.process = _Process([
        _line({"type": "result", "result": "same", "is_error": False}),
        _line({"type": "result", "result": "same", "is_error": False}),
    ])

    try:
        asyncio.run(worker._read_stdout(w))
        assert len([e for e in events if e.get("type") == "worker.result"]) == 1
        assert len([e for e in events if e.get("type") == "worker.status"
                    and e.get("status") == "idle"]) == 1
    finally:
        _reset()
