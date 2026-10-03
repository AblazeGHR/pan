"""Unread acknowledgements stay responsive and commit before becoming visible."""

import asyncio
import json
import threading
from unittest.mock import MagicMock

import httpx
import pytest

from packages.core import session as sess, worker
from packages.core.adapters import CbcAdapter
from packages.web import server


@pytest.fixture
def target(monkeypatch):
    monkeypatch.setattr(worker, "_unread_done_locks", {})
    monkeypatch.setattr(worker, "_queue_usage_enrichment", lambda *a, **kw: None)

    async def no_broadcast(event):
        pass

    monkeypatch.setattr(server, "broadcast", no_broadcast)
    s = sess.create(name="unread-ack")
    s.unread_done_generation = 2
    s.unread_done_read_generation = 1
    s.unread_done_count = 1
    sess.save(s)
    return s


def _disk(s):
    return json.loads(sess._path(s.id).read_text(encoding="utf-8"))


def _block_commit(monkeypatch, s, *, fail=False):
    entered = threading.Event()
    release = threading.Event()
    replace = sess.os.replace
    blocked = False

    def before_replace(source, destination):
        nonlocal blocked
        if destination == sess._path(s.id) and not blocked:
            blocked = True
            entered.set()
            assert release.wait(3), "metadata commit was not released"
            if fail:
                raise OSError("synthetic ack commit failure")
        return replace(source, destination)

    monkeypatch.setattr(sess.os, "replace", before_replace)
    return entered, release


def _task_worker(s):
    w = worker.Worker(worker_id="unread-test-worker", session_id=s.id,
                      adapter=CbcAdapter(), process=MagicMock(), status="running")
    w._current_seq = 1
    w._current_task_id = "new-done"
    return w


def test_http_health_and_readers_respond_while_ack_waits_for_disk(target, monkeypatch):
    entered, release = _block_commit(monkeypatch, target)

    async def scenario():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://diagnostic") as client:
            ack = asyncio.create_task(client.post(
                f"/api/sessions/{target.id}/unread-done/ack", json={"observed": 2}))
            assert await asyncio.to_thread(entered.wait, 2)
            try:
                health = await asyncio.wait_for(client.get("/api/health"), 1)
                assert health.status_code == 200
                view = await asyncio.wait_for(server.api_get_session(target.id), 1)
                assert view["unreadDoneCount"] == 1
                assert target.unread_done_read_generation == 1
                assert _disk(target)["unread_done_read_generation"] == 1
            finally:
                release.set()
                response = await ack
            assert response.json()["unreadDoneCount"] == 0

    asyncio.run(scenario())
    assert _disk(target)["unread_done_read_generation"] == 2
    assert _disk(target)["summary_projection"]["revision"] == target.summary_projection["revision"]


def test_done_during_ack_stays_unread(target, monkeypatch):
    entered, release = _block_commit(monkeypatch, target)
    w = _task_worker(target)

    async def scenario():
        ack = asyncio.create_task(server.api_ack_session_unread_done(target.id, {"observed": 2}))
        assert await asyncio.to_thread(entered.wait, 2)
        done = asyncio.create_task(worker._persist_terminal_state(w, target, "done", "new result"))
        try:
            await asyncio.sleep(0)
            assert not done.done()
            assert target.unread_done_generation == 2
        finally:
            release.set()
            await asyncio.gather(ack, done)

    asyncio.run(scenario())
    assert target.unread_done_generation == 3
    assert target.unread_done_read_generation == 2
    assert target.unread_done_count == 1
    assert _disk(target)["unread_done_count"] == 1


def test_ack_failure_cannot_erase_a_concurrent_done(target, monkeypatch):
    entered, release = _block_commit(monkeypatch, target, fail=True)
    w = _task_worker(target)

    async def scenario():
        ack = asyncio.create_task(server.api_ack_session_unread_done(target.id, {"observed": 2}))
        assert await asyncio.to_thread(entered.wait, 2)
        done = asyncio.create_task(worker._persist_terminal_state(w, target, "done", "new result"))
        release.set()
        failed, terminal = await asyncio.gather(ack, done)
        assert failed["error"]["code"] == "ack_persist_failed"
        assert terminal is not None

    asyncio.run(scenario())
    assert target.unread_done_generation == 3
    assert target.unread_done_read_generation == 1
    assert target.unread_done_count == 2
    assert _disk(target)["unread_done_count"] == 2


def test_queued_unrelated_save_keeps_the_committed_read_cursor(target, monkeypatch):
    entered, release = _block_commit(monkeypatch, target)

    async def scenario():
        ack = asyncio.create_task(server.api_ack_session_unread_done(target.id, {"observed": 2}))
        assert await asyncio.to_thread(entered.wait, 2)
        target.model = "new-model"
        other = asyncio.create_task(sess.save_async(target))
        try:
            await asyncio.sleep(0)
            assert target.unread_done_read_generation == 1
        finally:
            release.set()
            await asyncio.gather(ack, other)

    asyncio.run(scenario())
    persisted = _disk(target)
    assert persisted["model"] == "new-model"
    assert persisted["unread_done_read_generation"] == 2
    assert persisted["unread_done_count"] == 0


def test_disconnect_does_not_rollback_a_successful_ack(target, monkeypatch):
    entered, release = _block_commit(monkeypatch, target)
    events = []

    async def broadcast(event):
        events.append(event)

    monkeypatch.setattr(server, "broadcast", broadcast)

    async def scenario():
        ack = asyncio.create_task(server.api_ack_session_unread_done(target.id, {"observed": 2}))
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            for _ in range(2):
                ack.cancel()
                await asyncio.sleep(0)
            assert not ack.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await ack
        assert target.unread_done_read_generation == 2

    asyncio.run(scenario())
    assert _disk(target)["unread_done_count"] == 0
    assert len(events) == 1


def test_duplicate_and_stale_acks_never_clear_new_generations(target):
    async def scenario():
        first = await server.api_ack_session_unread_done(target.id, {"observed": 2})
        duplicate = await server.api_ack_session_unread_done(target.id, {"observed": 2})
        assert first == duplicate
        target.unread_done_generation = 3
        target.unread_done_count = 1
        await sess.save_async(target)
        stale = await server.api_ack_session_unread_done(target.id, {"observed": 1})
        assert stale["unreadDoneReadGeneration"] == 2
        assert stale["unreadDoneCount"] == 1

    asyncio.run(scenario())


def test_ack_cannot_resurrect_a_session_deleted_after_lookup(target, monkeypatch):
    async def delete_after_read(func, *args, **kwargs):
        result = func(*args, **kwargs)
        sess.delete(target.id)
        return result

    monkeypatch.setattr(server, "_store_read", delete_after_read)
    result = asyncio.run(server.api_ack_session_unread_done(target.id, {"observed": 2}))
    assert result["error"]["code"] == "session_not_found"
    assert not sess._path(target.id).exists()
    assert target.id not in sess._cache


def test_cold_ack_preserves_history_and_session_identity(target):
    sess.append_history(target, {"role": "assistant", "content": "existing history"})
    sess.save(target)
    sess._cache.clear()
    result = asyncio.run(server.api_ack_session_unread_done(target.id, {"observed": 2}))
    assert result["unreadDoneCount"] == 0
    cold = sess.get(target.id, load_history=False)
    assert not cold._history_loaded
    loaded = sess.get(target.id)
    assert loaded is cold
    assert loaded.history[-1]["content"] == "existing history"
    assert loaded.unread_done_read_generation == 2


def test_history_append_during_ack_preserves_newer_projection(target, monkeypatch):
    entered, release = _block_commit(monkeypatch, target)

    async def scenario():
        ack = asyncio.create_task(server.api_ack_session_unread_done(target.id, {"observed": 2}))
        assert await asyncio.to_thread(entered.wait, 2)
        sess.append_history(target, {"role": "user", "content": "newer history"})
        history_save = asyncio.create_task(sess.save_async(target))
        release.set()
        await asyncio.gather(ack, history_save)

    asyncio.run(scenario())
    sess._cache.clear()
    loaded = sess.get(target.id)
    assert loaded.history[-1]["content"] == "newer history"
    assert loaded.summary_projection["last_user_preview"] == "newer history"
    assert loaded.unread_done_read_generation == 2


@pytest.mark.parametrize("route", [server.api_qq_subscribe, server.api_wechat_subscribe])
def test_channel_subscription_does_not_block_health_during_save(target, monkeypatch, route):
    entered, release = _block_commit(monkeypatch, target)

    async def scenario():
        subscribing = asyncio.create_task(route({
            "sessionId": target.id, "target_type": "user", "target_id": "test-user"}))
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            transport = httpx.ASGITransport(app=server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://diagnostic") as client:
                health = await asyncio.wait_for(client.get("/api/health"), 1)
                assert health.status_code == 200
        finally:
            release.set()
            result = await subscribing
        assert result["subscribed"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize("data", [None, {}, {"observed": False}, {"observed": -1},
                                  {"observed": "2"}, {"observed": []}])
def test_ack_requires_an_explicit_valid_generation(target, data):
    result = asyncio.run(server.api_ack_session_unread_done(target.id, data))
    assert result["error"]["code"] == "invalid_params"
    assert target.unread_done_count == 1
