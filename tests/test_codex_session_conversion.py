"""Session upgrades/downgrades use the proven idle restart boundary, not interrupts."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from packages.core import session, worker
from packages.core.adapters import CodexAdapter
from packages.core.adapters.cbc.adapter import CbcAdapter
from packages.web import native_control_api as api


@pytest.fixture(params=["codex", "cbc"])
def target(monkeypatch, request):
    app = FastAPI()
    app.include_router(api.router)
    s = session.Session(id="ses-convert", name="existing", adapter=request.param)
    s.cli_session_id = "same-thread"
    s.history = [{"role": "user", "content": "keep"}]
    s.queue_pending = [{"id": "keep-queue", "text": "later"}]
    session._cache[s.id] = s
    w = worker.Worker(worker_id="w-convert", session_id=s.id, adapter=CodexAdapter() if request.param == "codex" else CbcAdapter(),
                      status="running", process=SimpleNamespace(returncode=None), generation=2)
    monkeypatch.setattr(api, "_enter", lambda request: (None, None))
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: s)
    monkeypatch.setattr(session, "save_async", AsyncMock())
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda sid: w)
    monkeypatch.setattr(worker, "_bcast", AsyncMock())
    monkeypatch.setattr(worker, "_shutdown_started", False)
    worker._spawn_locks.clear()
    worker._delayed_restarts.clear()
    worker.workers[w.worker_id] = w
    yield app, s, w
    worker.workers.pop(w.worker_id, None)
    worker._spawn_locks.clear()
    worker._delayed_restarts.clear()


def request(app, body=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return await client.request("POST" if body is not None else "GET", "/api/sessions/ses-convert/native-control" + ("/configuration" if body is not None else ""), json=body)
    return asyncio.run(run())


@pytest.mark.parametrize("enabled", [True, False])
def test_live_conversion_stages_config_preserves_thread_history_queue_and_never_interrupts(target, monkeypatch, enabled):
    app, s, w = target
    if not enabled:
        s.adapter_config["native_control"] = True
        w.native_control_enabled = True
    kill = AsyncMock()
    spawn = AsyncMock()
    monkeypatch.setattr(worker, "kill_worker", kill)
    monkeypatch.setattr(worker, "create_worker", spawn)
    before = (s.cli_session_id, list(s.history), list(s.queue_pending))
    result = request(app, {"enabled": enabled})
    assert result.status_code == 200
    assert result.json()["result"]["pendingEnabled"] is enabled
    assert s.adapter_config.get("native_control", False) is (not enabled)
    assert w.pending_restart and w.status == "running"
    assert (s.cli_session_id, s.history, s.queue_pending) == before
    assert kill.await_count == spawn.await_count == 0
    assert request(app, {"enabled": enabled}).status_code == 200
    assert request(app, {"enabled": not enabled}).status_code == 409


def test_offline_upgrade_then_downgrade_is_config_only(target, monkeypatch):
    app, s, w = target
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda sid: None)
    spawn = AsyncMock()
    monkeypatch.setattr(worker, "create_worker", spawn)
    assert request(app, {"enabled": True}).json()["result"] == {"enabled": True, "pendingEnabled": None, "delayedRestart": None}
    assert s.adapter_config["output_mode"] == "stream"
    assert request(app, {"enabled": False}).json()["result"]["enabled"] is False
    assert "native_control" not in s.adapter_config
    assert spawn.await_count == 0 and s.cli_session_id == "same-thread"


@pytest.mark.parametrize("body", [{"enabled": "true"}, {"enabled": 1}, {"enabled": True, "workerId": "fake"}])
def test_strict_configuration_body_has_zero_mutation(target, body):
    app, s, _ = target
    before = dict(s.adapter_config)
    assert request(app, body).status_code == 422
    assert s.adapter_config == before


def test_save_failure_rolls_back_without_restart(target, monkeypatch):
    app, s, w = target
    monkeypatch.setattr(session, "save_async", AsyncMock(side_effect=OSError("owned disk failure")))
    with pytest.raises(OSError):
        request(app, {"enabled": True})
    assert "native_control_next" not in s.adapter_config and not w.pending_restart


def test_pending_promotion_is_atomic_and_preserves_other_settings(target, monkeypatch):
    _, s, _ = target
    s.adapter_config.update(native_control_next=True, effort="high")
    before = dict(s.adapter_config)
    monkeypatch.setattr(session, "save_async", AsyncMock(side_effect=OSError("disk")))
    with pytest.raises(OSError):
        asyncio.run(worker._apply_pending_native_configuration(s))
    assert s.adapter_config == before
    monkeypatch.setattr(session, "save_async", AsyncMock())
    asyncio.run(worker._apply_pending_native_configuration(s))
    assert s.adapter_config == {"native_control": True, "effort": "high", "output_mode": "stream", "cli_session_id": "same-thread"}
    assert s.cli_session_id == "same-thread" and s.queue_pending[0]["id"] == "keep-queue"


def test_native_busy_idle_projection_cannot_restart_early(target, monkeypatch):
    _, s, w = target
    spawned = []
    async def respawn(*args):
        spawned.append(args)
    monkeypatch.setattr(worker, "_respawn_worker", respawn)
    async def run():
        w.status = "idle"
        w._native_external_turn_active = True
        worker.request_delayed_restart(s.id)
        await asyncio.sleep(0)
        assert w.status == "idle" and spawned == []
        w._native_external_turn_active = False
        w._terminal_finishing = True
        worker._maybe_restart_pending(w)
        await asyncio.sleep(0)
        assert spawned == []
        w._terminal_finishing = False
        worker._maybe_restart_pending(w)
        await asyncio.sleep(0)
        assert len(spawned) == 1 and w.status == "restarting"
    asyncio.run(run())


def test_other_adapter_readonly_and_security_gate_reject(target, monkeypatch):
    app, s, _ = target
    # Capability gate, not a hardcoded name set: kimi declares
    # supports_native_control since the Terminal TUI landed (3bf174b1), so the
    # "other adapter" case must use one that genuinely has no native transport.
    s.adapter = "claude"
    assert request(app, {"enabled": True}).status_code == 409
    s.adapter = "codex"; s.readonly_session = True
    assert request(app, {"enabled": True}).status_code == 409
    monkeypatch.setattr(api, "_enter", lambda req: (None, api.fail(403, "forbidden-origin")))
    assert request(app, {"enabled": True}).status_code == 403
    assert "native_control_next" not in s.adapter_config


@pytest.mark.parametrize("enabled", [True, False])
def test_idle_coordinator_stops_old_runtime_before_promoting_transport(target, monkeypatch, enabled):
    _, s, w = target
    s.adapter_config.update(native_control=not enabled, native_control_next=enabled)
    events = []
    async def kill(wid, **kwargs):
        assert s.adapter_config["native_control"] is (not enabled)
        assert kwargs["preserve_restart_intent"] is True
        events.append("old-stopped")
        w.process.returncode = 0
        worker.workers.pop(wid)
    async def create(sid):
        assert events == ["old-stopped"]
        await worker._apply_pending_native_configuration(s)
        assert s.adapter_config.get("native_control", False) is enabled
        assert s.cli_session_id == "same-thread" and s.history[0]["content"] == "keep"
        assert s.queue_pending[0]["id"] == "keep-queue"
        events.append("new-transport")
        return SimpleNamespace(worker_id="replacement", generation=3)
    monkeypatch.setattr(worker, "_kill_worker_unlocked", kill)
    monkeypatch.setattr(worker, "_create_worker", create)
    async def run():
        worker.request_delayed_restart(s.id)
        await asyncio.sleep(0)
        assert events == []  # A running task is never stopped by requesting conversion.
        assert worker._native_handoff_blocked(s)
        w.status = "idle"
        worker._maybe_restart_pending(w)
        for _ in range(4):
            await asyncio.sleep(0)
        assert events == ["old-stopped", "new-transport"]
        assert worker.delayed_restart_state(s.id)["status"] == "completed"
    asyncio.run(run())


def test_unstopped_runtime_keeps_pending_config_and_owner(target, monkeypatch):
    _, s, w = target
    s.adapter_config["native_control_next"] = True
    async def kill(*args, **kwargs):
        worker.workers.pop(w.worker_id)
    monkeypatch.setattr(worker, "_kill_worker_unlocked", kill)
    monkeypatch.setattr(worker, "_register_worker", lambda value: None)
    create = AsyncMock()
    monkeypatch.setattr(worker, "_create_worker", create)
    async def run():
        w.status = "idle"
        worker.request_delayed_restart(s.id)
        for _ in range(4):
            await asyncio.sleep(0)
        assert worker.delayed_restart_state(s.id)["status"] == "failed"
        assert worker.workers[w.worker_id] is w and w.status == "error"
        assert s.adapter_config["native_control_next"] is True
        assert "native_control" not in s.adapter_config and create.await_count == 0
    asyncio.run(run())


@pytest.mark.parametrize("adapter", ["codex", "cbc"])
def test_pending_configuration_survives_disk_reload(monkeypatch, adapter):
    s = session.Session(id="ses-disk-convert", name="disk", adapter=adapter)
    s.cli_session_id = "durable-thread"
    s.adapter_config["native_control_next"] = True
    s.queue_pending = [{"id": "durable-queue", "text": "keep"}]
    session.save_full(s)
    session._cache.pop(s.id, None)
    restored = session.get(s.id)
    assert restored.adapter_config["native_control_next"] is True
    monkeypatch.setattr(worker, "_bcast", AsyncMock())
    asyncio.run(worker._apply_pending_native_configuration(restored))
    session._cache.pop(s.id, None)
    final = session.get(s.id)
    assert final.adapter_config["native_control"] is True
    assert "native_control_next" not in final.adapter_config
    assert final.cli_session_id == "durable-thread" and final.queue_pending[0]["id"] == "durable-queue"


def test_pending_intent_allows_post_restart_queue_recovery_but_fences_retained_owner(target, monkeypatch):
    _, s, w = target
    s.adapter_config["native_control_next"] = True
    assert worker._queue_recovery_blocked(s)
    worker.workers.pop(w.worker_id)
    monkeypatch.setattr(worker, "find_worker_by_session", lambda sid: None)
    assert not worker._queue_recovery_blocked(s)
    s.queue_paused = True
    assert worker._queue_recovery_blocked(s)


def test_native_turn_handback_does_not_interrupt_or_restart_before_idle(target, monkeypatch):
    app, s, w = target
    s.adapter_config["native_control"] = True
    w.native_control_enabled = True
    w.native_control_mode = "native"
    w._native_external_turn_active = True
    switch = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(worker, "switch_native_control", switch)
    spawn = AsyncMock()
    monkeypatch.setattr(worker, "_respawn_worker", spawn)
    assert request(app, {"enabled": False}).status_code == 200
    switch.assert_awaited_once_with(w.worker_id, "pan")
    assert w._native_external_turn_active and w.status == "running"
    assert spawn.await_count == 0 and w.pending_restart
