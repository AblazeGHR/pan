"""Narrow regression tests for sessionId-first worker control routes."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.web import server as srv


def _cleanup() -> None:
    _sess._cache.clear()
    _sess._all_loaded = False


def test_restart_or_start_route_returns_runtime_details(monkeypatch):
    _cleanup()
    _sess._cache["ses-control"] = _sess.Session(
        id="ses-control", name="control", adapter="cbc",
    )
    live = SimpleNamespace(worker_id="worker-runtime", status="idle")
    control = AsyncMock(return_value=live)
    monkeypatch.setattr(srv.worker, "restart_or_start_worker", control)

    result = asyncio.run(srv.api_restart_or_start("ses-control"))

    assert result == {
        "workerId": "worker-runtime",
        "sessionId": "ses-control",
        "status": "idle",
    }
    control.assert_awaited_once_with("ses-control")
    _cleanup()


def test_session_kill_route_does_not_resolve_worker_id_in_business_path(monkeypatch):
    _cleanup()
    _sess._cache["ses-control"] = _sess.Session(
        id="ses-control", name="control", adapter="cbc",
    )
    killed = SimpleNamespace(worker_id="worker-runtime")
    control = AsyncMock(return_value=killed)
    monkeypatch.setattr(srv.worker, "kill_session_worker", control)

    result = asyncio.run(srv.api_session_kill("ses-control"))

    assert result == {
        "workerId": "worker-runtime",
        "sessionId": "ses-control",
        "status": "offline",
    }
    control.assert_awaited_once_with("ses-control")
    _cleanup()
