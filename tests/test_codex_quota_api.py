"""Contract tests for the HTTP route and MCP wrapper of Codex quota."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# FastMCP currently imports python-dotenv during module import. Keep this
# dependency boundary explicit: the API/MCP tests run when the normal server
# test environment is provisioned, while Core quota tests remain runnable.
pytest.importorskip("dotenv", reason="python-dotenv is required for FastMCP/API tests")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.mcp.server as mcp_server
import packages.web.server as web_server


def _rate_limits():
    return {
        "primary": {"usedPercent": 16, "windowDurationMins": 300},
        "secondary": {"usedPercent": 70, "windowDurationMins": 10080},
    }


def test_codex_quota_http_route_selects_requested_session_and_window(monkeypatch):
    session = SimpleNamespace(adapter="codex")
    live_worker = SimpleNamespace(
        session_id="ses-http-quota",
        worker_id="worker-http-quota",
        adapter=SimpleNamespace(name="codex"),
        native_rate_limits=_rate_limits(),
        native_rate_limits_updated_at="2026-09-07T01:02:03+00:00",
    )
    monkeypatch.setattr(web_server.sess, "get", lambda sid: session)
    monkeypatch.setattr(
        web_server.worker,
        "find_alive_worker_by_session",
        lambda sid: live_worker,
    )

    result = asyncio.run(web_server.api_codex_quota(
        session_id="ses-http-quota", window="first"))
    assert result["ok"] is True
    assert result["window"] == "first"
    assert set(result["windows"]) == {"first"}
    assert result["windows"]["first"]["name"] == "5h"


def test_codex_quota_http_route_reports_non_codex_and_missing_snapshot(monkeypatch):
    monkeypatch.setattr(
        web_server.sess,
        "get",
        lambda sid: SimpleNamespace(adapter="cbc"),
    )
    unsupported = asyncio.run(web_server.api_codex_quota(session_id="ses-cbc"))
    assert unsupported["ok"] is False
    assert unsupported["error"]["code"] == "unsupported_provider"

    monkeypatch.setattr(
        web_server.sess,
        "get",
        lambda sid: SimpleNamespace(adapter="codex"),
    )
    monkeypatch.setattr(
        web_server.worker,
        "find_alive_worker_by_session",
        lambda sid: SimpleNamespace(
            session_id=sid,
            worker_id="worker-no-snapshot",
            native_rate_limits=None,
        ),
    )
    missing = asyncio.run(web_server.api_codex_quota(session_id="ses-codex"))
    assert missing["ok"] is False
    assert missing["error"]["code"] == "quota_unavailable"


def test_codex_quota_mcp_uses_bound_session_and_preserves_window_parameter(monkeypatch):
    calls = []

    def fake_api(method, path, body=None, timeout=30.0):
        calls.append((method, path))
        return {"ok": True, "window": "secondary"}

    monkeypatch.setattr(mcp_server, "_api", fake_api)
    monkeypatch.setattr(mcp_server, "_check_access", lambda sid: None)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses-bound-quota")

    result = mcp_server.codex_quota(window="secondary")
    assert result == {"ok": True, "window": "secondary"}
    assert calls == [
        ("GET", "/api/codex/quota?window=secondary&session_id=ses-bound-quota")
    ]
