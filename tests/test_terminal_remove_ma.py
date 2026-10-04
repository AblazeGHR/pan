"""Removal never erases a running terminal or an unconfirmed cleanup owner."""
import asyncio
import sys
import time
import threading
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from packages.core.terminal.contracts import RuntimeState, TerminalRecord, UnknownTerminalError
from packages.core.terminal.service import CleanupUnconfirmed, TerminalService
from packages.web import terminal_api


@pytest.mark.parametrize("status", [RuntimeState.RUNNING, RuntimeState.EXITING,
                                  RuntimeState.CLEANUP_FAILED, RuntimeState.LOST])
def test_remove_preserves_unconfirmed_records(tmp_path, status):
    service = TerminalService(tmp_path)
    service._registry.create(TerminalRecord(terminal_id="term_remove", status=status))
    with pytest.raises(CleanupUnconfirmed):
        service.remove("term_remove")
    assert service.get("term_remove")["status"] == status.value


def test_remove_exited_record_and_reject_retained_secret(tmp_path, monkeypatch):
    service = TerminalService(tmp_path)
    service._registry.create(TerminalRecord(terminal_id="term_remove", status=RuntimeState.EXITED))
    store = service._store()
    monkeypatch.setattr(store, "exists", lambda _id: True)
    with pytest.raises(CleanupUnconfirmed):
        service.remove("term_remove")
    assert service.get("term_remove")["status"] == "exited"
    monkeypatch.setattr(store, "exists", lambda _id: False)
    assert service.remove("term_remove") == {"terminal_id": "term_remove", "removed": True}
    assert service.list() == []
    with pytest.raises(UnknownTerminalError):
        service.remove("term_remove")


def test_remove_retains_in_memory_cleanup_owner(tmp_path):
    service = TerminalService(tmp_path)
    record = service._registry.create(TerminalRecord(terminal_id="term_owner", status=RuntimeState.EXITED))
    state = SimpleNamespace(record=record, lock=threading.RLock(),
                            runner_identity_handle=object(), client=None, heartbeat=None)
    service._states["term_owner"] = state
    with pytest.raises(CleanupUnconfirmed):
        service.remove("term_owner")
    assert service._states["term_owner"] is state
    assert service._registry.get("term_owner").status is RuntimeState.EXITED


def test_remove_http_gate_body_and_confirmed_result():
    class Fake:
        calls = 0
        confirmed = True

        def reconcile(self):
            return {}

        def remove(self, _id):
            self.calls += 1
            return {"removed": self.confirmed, "token": "PRIVATE_SENTINEL"}

        def shutdown(self, **_kwargs):
            return {"unconfirmed": [], "secrets_retained": False, "budget_exhausted": False}

    async def run():
        fake = Fake()
        app = FastAPI()
        app.include_router(terminal_api.router)
        runtime = terminal_api.build_runtime(env={}, platform="win32", host="127.0.0.1",
            port=8767, service_factory=lambda: fake)
        await terminal_api.start_runtime(app, runtime=runtime)
        try:
            for _ in range(100):
                if runtime.state == terminal_api.STATE_READY:
                    break
                await asyncio.sleep(.01)
            assert runtime.state == terminal_api.STATE_READY
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://127.0.0.1:8767") as client:
                route = "/api/terminals/term_remove/remove"
                assert (await client.post(route, json={}, headers={"Origin": "http://evil.invalid"})).status_code == 403
                assert fake.calls == 0
                headers = {"Origin": "http://127.0.0.1:8767"}
                assert (await client.post(route, json={"force": True}, headers=headers)).status_code == 422
                assert fake.calls == 0
                response = await client.post(route, json={}, headers=headers)
                assert response.status_code == 200
                assert response.json()["result"] == {"terminal_id": "term_remove", "removed": True}
                assert "PRIVATE_SENTINEL" not in response.text
                fake.confirmed = "true"
                assert (await client.post(route, json={}, headers=headers)).status_code == 409
        finally:
            await terminal_api.stop_runtime(app)

    asyncio.run(run())


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows launcher")
def test_real_close_then_remove(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    if not (repo / "packages/core/terminal/emulator_sidecar/node_modules/@xterm/headless/package.json").exists():
        pytest.skip("sidecar dependencies unavailable")
    service = TerminalService(tmp_path)
    tid = service.create()["terminal_id"]
    try:
        with pytest.raises(CleanupUnconfirmed):
            service.remove(tid)
    finally:
        deadline = time.monotonic() + 25
        while True:
            try:
                service.close(tid)
                break
            except CleanupUnconfirmed:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
    assert service.remove(tid)["removed"] is True
    assert service.list() == []
    assert not service._store().exists(tid)
