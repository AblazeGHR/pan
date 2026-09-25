"""Synthetic Steer/history ordering probes for T-062.9b."""

import asyncio
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CodexAdapter


def _cleanup() -> None:
    worker.workers.clear()
    _sess._cache.clear()
    _sess._all_loaded = False


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_steer_control_stream_result_history_barrier_is_ordered(tmp_path, monkeypatch):
    """Control success + concurrent stream/result appends must all persist."""
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    session = _sess.create(name="steer-order", adapter="codex")
    session.history.append({"role": "user", "content": "question"})
    _sess.save(session)

    live = worker.Worker(
        worker_id="worker-steer-order",
        session_id=session.id,
        adapter=CodexAdapter(),
        process=MagicMock(),
    )
    worker.workers[live.worker_id] = live
    timeline: list[str] = []

    async def fake_control(worker_id, control):
        assert worker_id == live.worker_id
        assert control == {"type": "steer", "text": "steer now"}
        timeline.append("control-written")
        return None

    monkeypatch.setattr(worker, "send_control_message", fake_control)
    entered = threading.Event()
    release = threading.Event()
    original_append = _sess._append_jsonl

    def blocked_append(path, items):
        entered.set()
        assert release.wait(2), "history write was not released"
        return original_append(path, items)

    monkeypatch.setattr(_sess, "_append_jsonl", blocked_append)

    async def scenario():
        steer_task = asyncio.create_task(worker.steer_worker(
            live.worker_id, "steer now", "steer:one"))
        assert await asyncio.to_thread(entered.wait, 2)
        while len(session.history) < 2:
            await asyncio.sleep(0)
        timeline.append("steer-history-append")
        session.history.append({"role": "assistant", "content": "delta"})
        timeline.append("stream-history-append")
        release.set()
        assert await steer_task is None
        timeline.append("steer-save-returned")
        session.history.append({"role": "assistant", "content": "final"})
        timeline.append("result-history-append")
        await _sess.save_async(session)
        timeline.append("result-save-returned")

    asyncio.run(scenario())
    assert timeline == [
        "control-written",
        "steer-history-append",
        "stream-history-append",
        "steer-save-returned",
        "result-history-append",
        "result-save-returned",
    ]
    # ts 由 append_history 打点；barrier 测试关注的是条目顺序，投影掉时间字段再比
    assert [{k: m[k] for k in ("role", "content", "messageId") if k in m}
            for m in _jsonl(_sess._history_path(session.id))] == [
        {"role": "user", "content": "question"},
        {"role": "user", "content": "steer now", "messageId": "steer:one"},
        {"role": "assistant", "content": "delta"},
        {"role": "assistant", "content": "final"},
    ]
    _cleanup()
