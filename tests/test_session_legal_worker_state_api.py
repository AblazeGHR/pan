"""Session Details legal-state API delegates to the runtime sync helper."""

import asyncio
from types import SimpleNamespace

import pytest

from packages.core import worker
from packages.web import server


@pytest.fixture
def session_lookup(monkeypatch):
    monkeypatch.setattr(server, "_summary_session_get", lambda session_id: SimpleNamespace(id=session_id))

    async def store_read(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(server, "_store_read", store_read)


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
    assert calls == [("ses-details", "session-details/sync-actual")]


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
