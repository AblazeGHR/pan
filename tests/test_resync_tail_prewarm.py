"""Resync cold scans leave the loop free and final reads retain invalidation."""
import asyncio
import threading
import time

import pytest

from packages.core import session
from packages.web import server


@pytest.mark.parametrize("changed", [False, True])
def test_resync_prewarm_preserves_fresh_history_and_loop_progress(tmp_path, monkeypatch, changed):
    monkeypatch.setattr(session, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session, "_cache", {})
    monkeypatch.setattr(session, "_all_loaded", False)
    s = session.create("resync", history=[{"role": "user", "content": "old"}])
    session._cache.clear()
    reader = session._history_page_from_jsonl
    calls = []
    def slow(*args, **kwargs):
        calls.append(threading.current_thread().name)
        time.sleep(0.1)
        return reader(*args, **kwargs)
    monkeypatch.setattr(session, "_history_page_from_jsonl", slow)
    snapshots = []
    async def send(_ws, payload, **_kwargs):
        snapshots.append(payload)
        return True
    monkeypatch.setattr(server, "_send_ws", send)
    warm = server._warm_resync_history
    def prepare(ids):
        warm(ids)
        if changed:
            target = session.get(s.id)
            session.append_history(target, {"role": "assistant", "content": "new"})
            session.save(target)
    monkeypatch.setattr(server, "_warm_resync_history", prepare)
    async def run():
        task = asyncio.create_task(server._send_resync_snapshot(object(), [s.id], include_identity=True))
        ticks = 0
        while not task.done():
            await asyncio.sleep(0.005)
            ticks += 1
        assert await task
        assert ticks >= 5, "heartbeat progresses during the cold read"
    asyncio.run(run())
    rows = snapshots[0]["details"][s.id]["history"]
    assert [row["content"] for row in rows] == (["old", "new"] if changed else ["old"])
    assert len(calls) == 1
    assert calls[0].startswith("pan-store-read")


def test_hot_resync_keeps_existing_synchronous_snapshot_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session, "_cache", {})
    monkeypatch.setattr(session, "_all_loaded", False)
    s = session.create("hot", history=[{"role": "assistant", "content": "live"}])
    async def unexpected(*args, **kwargs):
        raise AssertionError("hot history must not enter asynchronous prewarming")
    monkeypatch.setattr(server, "_store_read", unexpected)
    snapshots = []
    async def send(_ws, payload, **kwargs):
        snapshots.append(payload)
        return True
    monkeypatch.setattr(server, "_send_ws", send)
    assert asyncio.run(server._send_resync_snapshot(object(), [s.id]))
    assert snapshots[0]["details"][s.id]["history"][0]["content"] == "live"
