"""HTTP boundary, identity fencing and spawn admission (no model traffic)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from packages.core import session, worker
from packages.web import native_control_api as api


@pytest.fixture
def target(monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    s = session.Session(id="ses-native-api", name="native", adapter="codex")
    s.adapter_config["native_control"] = True
    w = SimpleNamespace(worker_id="w-native", generation=7, native_control_mode="native", native_control_generation=4,
        native_control_error=None, native_output=bytearray(b"abcdef"), native_output_total=10,
        native_kernel_ready=True, process=SimpleNamespace(returncode=None))
    monkeypatch.setattr(api, "_enter", lambda request: (None, None))
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: s)
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda sid: w)
    return app, s, w


def request(app, method, path="", **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as c:
            return await c.request(method, "/api/sessions/ses-native-api/native-control" + path, **kwargs)
    return asyncio.run(run())


def test_default_and_other_adapters_reject_without_spawning(target, monkeypatch):
    app, s, w = target
    spawn = AsyncMock()
    monkeypatch.setattr(worker, "create_worker", spawn)
    s.adapter_config = {}
    assert request(app, "POST", "/mode", json={"mode": "native"}).status_code == 409
    s.adapter_config["native_control"] = True
    s.adapter = "cbc"
    assert request(app, "GET").status_code == 409
    assert spawn.await_count == 0


def test_pan_recovery_during_pending_upgrade_does_not_spawn_or_mutate(target, monkeypatch):
    app, s, w = target
    s.adapter_config = {"native_control_next": True}
    w.native_control_mode = "pan"
    w.native_kernel_ready = False
    switch = AsyncMock()
    monkeypatch.setattr(worker, "switch_native_control", switch)
    result = request(app, "POST", "/mode", json={"mode": "pan"})
    assert result.status_code == 200
    assert result.json()["result"]["mode"] == "pan"
    assert s.adapter_config == {"native_control_next": True}
    switch.assert_not_awaited()
    assert request(app, "POST", "/mode", json={"mode": "native"}).status_code == 409


@pytest.mark.parametrize("body", [{"mode": []}, {"mode": True}, {"mode": "native", "token": "fake"}])
def test_invalid_modes_do_not_call_worker(target, monkeypatch, body):
    switch = AsyncMock()
    monkeypatch.setattr(worker, "switch_native_control", switch)
    assert request(target[0], "POST", "/mode", json=body).status_code == 422
    assert switch.await_count == 0


def test_output_offsets_gap_and_no_private_transport(target):
    app, _, w = target
    result = request(app, "GET", "/output?cursor=0").json()["result"]
    assert result["gap"] is True and result["cursor"] == "10" and result["data"] == "YWJjZGVm"
    assert set(result) == {"workerId", "workerGeneration", "mode", "generation", "error", "cursor", "gap", "data"}
    assert request(app, "GET", "/output?cursor=11").status_code == 409
    for cursor in ("true", "-1", "1.0", "18446744073709551616"):
        assert request(app, "GET", "/output?cursor=" + cursor).status_code == 422


def test_write_requires_current_worker_and_real_ack(target, monkeypatch):
    app, _, w = target
    write = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(worker, "write_native_control", write)
    body = {"workerId": w.worker_id, "generation": 4, "text": "中文\r"}
    assert request(app, "POST", "/write", json=body).status_code == 200
    assert write.await_args.args[1]["text"] == "中文\r"
    assert request(app, "POST", "/write", json={**body, "workerId": "old"}).status_code == 409
    assert request(app, "POST", "/write", json={**body, "generation": True}).status_code == 422
    count = write.await_count
    assert request(app, "POST", "/write", json={**body, "workerGeneration": 6}).status_code == 409
    assert write.await_count == count
    assert request(app, "POST", "/write", json={**body, "workerGeneration": 7}).status_code == 200
    write.return_value = {"ok": False}
    assert request(app, "POST", "/write", json=body).status_code == 409


def test_native_spawn_fences_queue_before_consumer_is_created(target, monkeypatch):
    app, s, w = target
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda sid: None)
    async def spawn(sid):
        assert sid in worker.native_starting_sessions
        assert worker._native_handoff_blocked(s)
        return w
    async def switch(wid, mode):
        assert w.native_control_mode == "switching"
        w.native_control_mode = "native"
        return {"ok": True}
    monkeypatch.setattr(worker, "create_worker", spawn)
    monkeypatch.setattr(worker, "switch_native_control", switch)
    assert request(app, "POST", "/mode", json={"mode": "native"}).status_code == 200
    assert s.id not in worker.native_starting_sessions


def test_origin_gate_runs_before_session_lookup(target, monkeypatch):
    from packages.web.terminal_api import fail
    monkeypatch.setattr(api, "_enter", lambda request: (None, fail(403, "forbidden-origin")))
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: pytest.fail("looked up before security gate"))
    assert request(target[0], "POST", "/mode", json={"mode": "native"}).status_code == 403


def test_invalid_unicode_input_is_rejected_without_worker_write(target, monkeypatch):
    write = AsyncMock()
    monkeypatch.setattr(worker, "write_native_control", write)
    response = request(target[0], "POST", "/write", content=b'{"workerId":"w-native","generation":4,"text":"\\ud800"}', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert write.await_count == 0


def test_creation_is_explicit_codex_only_and_old_session_data_stays_unchanged(monkeypatch):
    from packages.web import server
    monkeypatch.setattr(server, "_ensure_manifest_fresh", lambda: None)
    for adapter in ("codex", "cbc", "kimi"):
        params = server._build_session_params({"name": "legacy", "adapter": adapter, "mcpServers": []}, resolve_workdir=False)
        assert "native_control" not in params["adapter_config"]
        old = session.Session(id="ses-legacy", name="legacy", adapter=adapter)
        before = dict(old.adapter_config)
        assert server._session_to_api(old)["nativeControl"] is False
        assert old.adapter_config == before
    params = server._build_session_params({"name": "native", "adapter": "codex", "nativeControl": True, "mcpServers": []}, resolve_workdir=False)
    assert params["adapter_config"]["native_control"] is True
    for adapter, value in (("cbc", True), ("codex", "true"), ("codex", 1)):
        with pytest.raises(ValueError):
            server._build_session_params({"name": "bad", "adapter": adapter, "nativeControl": value, "mcpServers": []}, resolve_workdir=False)
