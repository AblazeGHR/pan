"""Session Details legal-state routes use the shared persistence helpers."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from packages.core import legal_offline
from packages.core.adapters import CbcAdapter
from packages.core import worker
from packages.core import session as sess
from packages.web import server


@pytest.fixture
def session_lookup(monkeypatch):
    monkeypatch.setattr(server, "_summary_session_get", lambda session_id: SimpleNamespace(id=session_id))

    async def store_read(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(server, "_store_read", store_read)


@pytest.fixture
def isolated_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path)
    sess._cache.clear()
    sess._newline_terminated_jsonl.clear()
    yield tmp_path
    sess._cache.clear()
    sess._newline_terminated_jsonl.clear()


def test_details_sync_returns_shared_helper_actual_state(session_lookup, monkeypatch):
    calls = []
    expected = {
        "sessionId": "ses-details",
        "status": "updated",
        "legalWorkerState": "idle",
        "runtimeWorkerStatus": "idle",
    }

    async def sync(session_id, *, source):
        calls.append((session_id, source))
        return expected

    monkeypatch.setattr(worker, "sync_legal_worker_state_to_runtime", sync)

    result = asyncio.run(server.api_sync_session_legal_worker_state("ses-details"))

    assert result is expected
    assert calls == [("ses-details", "manual/mark-safe-offline")]


def test_details_sync_returns_helper_error_result(session_lookup, monkeypatch):
    expected = {
        "sessionId": "ses-details",
        "status": "error",
        "error": "Worker runtime is not stopped",
    }

    async def sync(_session_id, *, source):
        return expected

    monkeypatch.setattr(worker, "sync_legal_worker_state_to_runtime", sync)

    assert asyncio.run(server.api_sync_session_legal_worker_state("ses-details")) == expected


def test_details_sync_surfaces_unexpected_helper_exception(session_lookup, monkeypatch):
    async def sync(_session_id, *, source):
        raise RuntimeError("runtime inspection failed")

    monkeypatch.setattr(worker, "sync_legal_worker_state_to_runtime", sync)

    assert asyncio.run(server.api_sync_session_legal_worker_state("ses-details")) == {
        "sessionId": "ses-details",
        "status": "error",
        "error": "runtime inspection failed",
    }


def test_details_set_running_persists_and_broadcasts_after_success(
    isolated_sessions, monkeypatch,
):
    session = sess.create("explicit-running")
    session.last_legal_worker_state = "idle"
    sess.save(session)
    metadata_path = isolated_sessions / f"{session.id}.json"
    events = []

    runtime = worker.Worker(worker_id="details-explicit-runtime", session_id=session.id, adapter=CbcAdapter(), status="held")
    worker.workers["details-explicit-runtime"] = runtime

    async def record(event):
        data = json.loads(metadata_path.read_text())
        assert data["last_legal_worker_state"] == "idle"
        assert data["legal_offline"] is False
        assert any(op["state"] == "illegal" for op in data["execution_operations"].values())
        events.append(event)

    monkeypatch.setattr(server, "broadcast", record)
    try:
        result = asyncio.run(
            server.api_set_session_legal_worker_state_running(session.id)
        )
    finally:
        worker.workers.pop("details-explicit-runtime", None)

    assert result["sessionId"] == session.id and result["status"] == "updated"
    assert result["illegalOffline"] and not result["legalOffline"]
    assert legal_offline.is_illegal(session)
    assert session.last_legal_worker_state == "idle"
    assert runtime.status == "held"
    assert events == [{"type": "session.updated", "sessionId": session.id}]


def test_details_set_running_reports_missing_session_without_broadcast(
    isolated_sessions, monkeypatch,
):
    events = []

    async def record(event):
        events.append(event)

    monkeypatch.setattr(server, "broadcast", record)

    result = asyncio.run(
        server.api_set_session_legal_worker_state_running("missing-session")
    )

    assert result == {
        "sessionId": "missing-session",
        "status": "error",
        "error": "Session missing-session not found",
    }
    assert events == []


def test_details_set_running_route_has_no_request_body():
    route = next(
        route for route in server.app.routes
        if getattr(route, "path", None)
        == "/api/sessions/{session_id}/legal-state/running"
    )

    assert route.body_field is None


def test_details_set_running_rolls_back_and_does_not_broadcast_on_save_failure(
    isolated_sessions, monkeypatch,
):
    session = sess.create("failed-explicit-running")
    session.last_legal_worker_state = "offline"
    sess.save(session)
    metadata_path = isolated_sessions / f"{session.id}.json"
    persisted_before = metadata_path.read_bytes()
    events = []

    def fail_replace(*args, **kwargs):
        raise OSError("disk full")

    async def record(event):
        events.append(event)

    monkeypatch.setattr(sess.os, "replace", fail_replace)
    monkeypatch.setattr(server, "broadcast", record)

    result = asyncio.run(
        server.api_set_session_legal_worker_state_running(session.id)
    )

    assert result == {
        "sessionId": session.id,
        "status": "error",
        "error": "disk full",
    }
    assert session.last_legal_worker_state == "offline"
    assert metadata_path.read_bytes() == persisted_before
    assert events == []
