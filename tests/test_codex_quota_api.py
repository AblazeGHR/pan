"""Contract tests for the HTTP route and MCP wrapper of Codex quota."""

import asyncio
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# FastMCP currently imports python-dotenv during module import. Keep this
# dependency boundary explicit: the API/MCP tests run when the normal server
# test environment is provisioned, while Core quota tests remain runnable.
pytest.importorskip("dotenv", reason="python-dotenv is required for FastMCP/API tests")
pytest.importorskip("mcp", reason="FastMCP package is required for MCP/API tests")

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
        native_rate_limits_received_at="2026-09-07T01:02:03+00:00",
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
    assert result["receivedAt"] == "2026-09-07T01:02:03+00:00"
    assert result["updatedAt"] == "2026-09-07T01:02:03+00:00"
    assert result["source"]["providerUpdatedAt"] is None


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


def test_codex_quota_http_route_reports_remaining_contract_errors(monkeypatch):
    invalid = asyncio.run(web_server.api_codex_quota(window="month"))
    assert invalid["error"]["code"] == "invalid_window"

    monkeypatch.setattr(web_server.sess, "get", lambda sid: None)
    missing_session = asyncio.run(web_server.api_codex_quota(session_id="ses-missing"))
    assert missing_session["error"]["code"] == "session_not_found"

    workers = [
        SimpleNamespace(session_id="ses-a", worker_id="worker-a", adapter=SimpleNamespace(name="codex")),
        SimpleNamespace(session_id="ses-b", worker_id="worker-b", adapter=SimpleNamespace(name="codex")),
    ]
    monkeypatch.setattr(web_server.worker, "list_live_workers", lambda: workers)
    ambiguous = asyncio.run(web_server.api_codex_quota())
    assert ambiguous["error"]["code"] == "quota_ambiguous"


def test_http_and_mcp_quota_permission_boundaries_are_explicit(monkeypatch):
    http_doc = web_server.api_codex_quota.__doc__ or ""
    mcp_doc = mcp_server.codex_quota.__doc__ or ""
    assert "no manager" in http_doc.lower()
    assert "does not apply managed-session isolation" in http_doc
    assert "_check_access" in mcp_doc
    assert "loopback HTTP" in mcp_doc
    assert "account/rateLimits/read" in mcp_doc
    assert "receivedAt" in mcp_doc
    assert "manager" not in inspect.signature(web_server.api_codex_quota).parameters

    calls = []
    monkeypatch.setattr(mcp_server, "_check_access", lambda sid: {
        "ok": False, "error": {"code": "permission_denied"}
    })
    monkeypatch.setattr(mcp_server, "_api", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses-managed-target")
    denied = mcp_server.codex_quota()
    assert denied["error"]["code"] == "permission_denied"
    assert calls == []


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


def test_codex_quota_mcp_reports_invalid_window_before_http(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_api", lambda *args, **kwargs: calls.append(args))
    result = mcp_server.codex_quota(window="month")
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_window"
    assert calls == []
