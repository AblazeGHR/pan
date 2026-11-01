"""Archive is metadata, not permission to erase an unconfirmed cleanup owner."""
import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from packages.core.terminal.contracts import RuntimeState, TerminalRecord
from packages.core.terminal.service import CleanupUnconfirmed, TerminalService, _TerminalState
from packages.web import terminal_api


@pytest.mark.parametrize("status", [RuntimeState.RUNNING, RuntimeState.CLEANUP_FAILED, RuntimeState.EXITED])
def test_archive_roundtrip_preserves_owner_secret_and_scope(tmp_path, status):
    service = TerminalService(tmp_path)
    record = TerminalRecord(terminal_id="term_archive", status=status, cleanup_pending=True)
    service._registry.create(record)
    owner = object()
    # Archive must not probe, stop, discard, or recreate a live owner.
    state = _TerminalState(terminal_id=record.terminal_id, record=record)
    state.runner_identity_handle = owner
    service._states[record.terminal_id] = state
    view = service.archive(record.terminal_id)
    assert view["archived"] is True
    assert view["cleanup_pending"] is True
    assert service._states[record.terminal_id] is state
    assert state.runner_identity_handle is owner
    assert service.archive(record.terminal_id, archived=False)["archived"] is False
    assert service._registry.get(record.terminal_id).status is status
    with pytest.raises(CleanupUnconfirmed):
        service.remove(record.terminal_id)


def test_old_record_defaults_and_binding_roundtrip(tmp_path):
    record = TerminalRecord.from_dict({"terminal_id": "term_scope", "status": "exited"})
    assert record.archived is False and record.cleanup_pending is False
    service = TerminalService(tmp_path)
    service._registry.create(record)
    assert service.bind_scope("term_scope", workspace_id="ws_a", session_id="ses_a")["scope"] == {
        "workspace_id": "ws_a", "session_id": "ses_a"}
    assert service.bind_scope("term_scope", workspace_id="ws_b", session_id=None)["scope"] == {
        "workspace_id": "ws_b", "session_id": None}


def test_session_binding_uses_effective_workspace_and_unknown_fails(monkeypatch):
    from packages.core import session
    target = object()
    monkeypatch.setattr(session, "get", lambda sid, **_kw: target if sid == "ses_bound" else None)
    monkeypatch.setattr(session, "effective_workspace_ids", lambda value: ["ws_session"] if value is target else [])
    assert terminal_api.resolve_terminal_scope("ws_forged", "ses_bound") == ("ws_session", "ses_bound")
    assert terminal_api.resolve_terminal_scope("ws_manual", None) == ("ws_manual", None)
    with pytest.raises(terminal_api.GateRejected):
        terminal_api.resolve_terminal_scope(None, "ses_unknown")


@pytest.mark.parametrize("proven", [False, True])
def test_retained_death_wins_over_fresh_unknown_but_cleanup_is_separate(tmp_path, monkeypatch, proven):
    service = TerminalService(tmp_path)
    record = service._registry.create(TerminalRecord(terminal_id="term_dead", status=RuntimeState.RUNNING))
    state = _TerminalState(terminal_id=record.terminal_id, record=record)
    service._states[record.terminal_id] = state
    monkeypatch.setattr(service, "_spawn_handle_exit_evidence", lambda _s: {"exited": True, "source": "runner-retained-handle"})
    monkeypatch.setattr(service, "_identity_evidence", lambda *_args: {"status": "unattributable"})
    monkeypatch.setattr(service, "_runner_cleanup_record_confirmed", lambda _s: proven)
    monkeypatch.setattr(service, "_launcher_status_evidence", lambda _s: {"engine_converged": proven})
    monkeypatch.setattr(service, "_secret_present", lambda _id: True)
    deleted = []
    monkeypatch.setattr(service._store(), "delete_secret", lambda *args, **kwargs: deleted.append((args, kwargs)))
    assert service._reconcile_live(state, record) == "dead-confirmed"
    view = service.get(record.terminal_id)
    assert view["status"] == "exited"
    assert view["cleanup_pending"] is (not proven)
    assert bool(deleted) is proven


@pytest.mark.skipif(sys.platform != "win32", reason="real retained Windows process handle")
def test_real_external_kill_is_exited_and_archivable(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    if not (repo / "packages/core/terminal/emulator_sidecar/node_modules/@xterm/headless/package.json").exists():
        pytest.skip("sidecar dependencies unavailable")
    from packages.core.terminal.identity import kill_verified
    from packages.core.terminal.win_pipe import ProcessIdentityHandle
    from packages.core.terminal.contracts import ProcessStatus
    import psutil
    service = TerminalService(tmp_path)
    view = service.create()
    tid = view["terminal_id"]
    state = service._states[tid]
    children = []
    try:
        for child in psutil.Process(state.runner_pid).children(recursive=True):
            handle = ProcessIdentityHandle.open(child.pid)
            if handle is not None:
                children.append(handle)
        result = kill_verified(state.runner_pid, state.runner_filetime)
        assert result.killed is True
        # A fresh lookup can fail after death; the original retained handle is authoritative.
        monkeypatch.setattr(service, "_identity_evidence", lambda *_args: {"status": "unattributable"})
        deadline = time.monotonic() + 5
        while not service._spawn_handle_exit_evidence(state).get("exited"):
            assert time.monotonic() < deadline
            time.sleep(.05)
        dead = service.get(tid)
        assert dead["status"] == "exited" and dead["cleanup_pending"] is True
        assert service._store().exists(tid)
        assert service.archive(tid)["archived"] is True
        assert service.archive(tid, archived=False)["archived"] is False
        with pytest.raises(CleanupUnconfirmed):
            service.remove(tid)
    finally:
        service._stop_heartbeat(state)
        service._release_client(state)
        # Release only our verified dead root handle; do not pretend product cleanup is proven.
        assert service._spawn_handle_exit_evidence(state).get("exited") is True
        assert service._release_runner_identity_handle(state)
        state.process.wait(timeout=5)
        deadline = time.monotonic() + 5
        try:
            for handle in children:
                while handle.probe().status is not ProcessStatus.DEAD:
                    assert time.monotonic() < deadline, "owned descendant did not exit"
                    time.sleep(.05)
        finally:
            for handle in children:
                assert handle.close()


def test_archive_and_scope_http_gates_and_binding(monkeypatch, tmp_path):
    service = TerminalService(tmp_path)
    service._registry.create(TerminalRecord(terminal_id="term_scope", status=RuntimeState.EXITED))
    monkeypatch.setattr(service, "reconcile", lambda: {})
    monkeypatch.setattr(service, "shutdown", lambda **_kw: {"unconfirmed": [], "secrets_retained": False, "budget_exhausted": False})
    from packages.core import session
    monkeypatch.setattr(session, "get", lambda sid, **_kw: object() if sid == "ses_bound" else None)
    monkeypatch.setattr(session, "effective_workspace_ids", lambda _value: ["ws_session"])

    async def run():
        app = FastAPI()
        app.include_router(terminal_api.router)
        runtime = terminal_api.build_runtime(env={}, platform="win32", host="127.0.0.1", port=8767, service_factory=lambda: service)
        await terminal_api.start_runtime(app, runtime=runtime)
        try:
            for _ in range(100):
                if runtime.state == terminal_api.STATE_READY:
                    break
                await asyncio.sleep(.01)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8767") as client:
                root = "/api/terminals/term_scope"
                headers = {"Origin": "http://127.0.0.1:8767"}
                assert (await client.post(root + "/archive", json={}, headers={"Origin": "http://evil.invalid"})).status_code == 403
                assert service._registry.get("term_scope").archived is False
                assert (await client.post(root + "/archive", json={"archived": "true"}, headers=headers)).status_code == 422
                assert (await client.post(root + "/archive", json={}, headers=headers)).json()["result"]["archived"] is True
                assert (await client.post(root + "/archive", json={"archived": False}, headers=headers)).json()["result"]["archived"] is False
                response = await client.post(root + "/scope", json={"session_id": "ses_bound", "workspace_id": "ws_wrong"}, headers=headers)
                assert response.status_code == 200
                assert response.json()["result"]["scope"] == {"session_id": "ses_bound", "workspace_id": "ws_session"}
                response = await client.post(root + "/scope", json={"workspace_id": "ws_manual"}, headers=headers)
                assert response.json()["result"]["scope"] == {"session_id": None, "workspace_id": "ws_manual"}
                assert (await client.post(root + "/scope", json={"session_id": "ses_bad"}, headers=headers)).status_code == 422
                assert service.get("term_scope")["scope"]["workspace_id"] == "ws_manual"
        finally:
            await terminal_api.stop_runtime(app)
    asyncio.run(run())


@pytest.mark.parametrize("proven", [False, True])
def test_persisted_death_keeps_archive_and_requires_tree_cleanup_before_secret_deletion(tmp_path, monkeypatch, proven):
    service = TerminalService(tmp_path)
    record = service._registry.create(TerminalRecord(
        terminal_id="term_persisted", status=RuntimeState.CLEANUP_FAILED,
        pid=123, process_created_at_filetime=456, archived=True))
    monkeypatch.setattr(service, "_secret_present", lambda _id: True)
    monkeypatch.setattr(service, "_identity_evidence", lambda *_args: {"status": "dead-confirmed"})
    monkeypatch.setattr(service, "_runner_cleanup_record_confirmed", lambda _s: proven)
    monkeypatch.setattr(service, "_launcher_status_evidence", lambda _s: {"engine_converged": proven})
    deleted = []
    monkeypatch.setattr(service, "_delete_secret", lambda *args: deleted.append(args))
    assert service._reconcile_persisted(record) == "dead-confirmed"
    view = service.get(record.terminal_id)
    assert view["status"] == "exited"
    assert view["cleanup_pending"] is (not proven)
    assert view["archived"] is True
    assert bool(deleted) is proven
