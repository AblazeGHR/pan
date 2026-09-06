"""Contract tests for Session usage HTTP/MCP wrappers."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("dotenv", reason="python-dotenv is required for FastMCP/API tests")
pytest.importorskip("mcp", reason="FastMCP package is required for MCP/API tests")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.mcp.server as mcp_server
import packages.web.server as web_server


def test_http_usage_route_returns_projection_and_not_raw_payload(monkeypatch):
    target = SimpleNamespace(
        id="ses-http-usage",
        adapter="codex",
        raw_usage={"gpt": {"rawUsage": {
            "input_tokens": 10,
            "cached_input_tokens": 2,
            "output_tokens": 3,
        }}},
        total_usage={"prompt_tokens": 10},
        updated_at="2026-09-07T01:02:03+00:00",
    )
    monkeypatch.setattr(web_server.sess, "get", lambda sid: target)

    result = asyncio.run(web_server.api_get_session_usage("ses-http-usage"))
    assert result["ok"] is True
    assert result["input"] == 10
    assert result["output"] == 3
    assert result["cache"]["read"] == 2
    assert result["total"]["tokens"] == 13
    assert result["source"]["kind"] == "Session.rawUsage"
    assert "rawUsage" not in result
    assert "totalUsage" not in result


def test_http_usage_route_has_stable_not_found_error(monkeypatch):
    monkeypatch.setattr(web_server.sess, "get", lambda sid: None)
    result = asyncio.run(web_server.api_get_session_usage("ses-missing"))
    assert result == {
        "ok": False,
        "error": {
            "code": "session_not_found",
            "message": "Session ses-missing not found",
        },
    }


def test_mcp_usage_uses_bound_session_and_checks_explicit_access(monkeypatch):
    calls = []

    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses-bound-usage")
    monkeypatch.setattr(mcp_server, "_check_access", lambda sid: None)
    monkeypatch.setattr(
        mcp_server,
        "_api",
        lambda method, path, **kwargs: calls.append((method, path)) or {"ok": True},
    )
    assert mcp_server.session_usage() == {"ok": True}
    assert calls == [("GET", "/api/sessions/ses-bound-usage/usage")]

    denied_calls = []
    monkeypatch.setattr(mcp_server, "_check_access", lambda sid: {
        "ok": False, "error": {"code": "permission_denied"}
    })
    monkeypatch.setattr(mcp_server, "_api", lambda *args, **kwargs: denied_calls.append(args))
    denied = mcp_server.session_usage("ses-other")
    assert denied["error"]["code"] == "permission_denied"
    assert denied_calls == []


def test_mcp_usage_requires_bound_identity_when_target_is_omitted(monkeypatch):
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    result = mcp_server.session_usage()
    assert result["error"]["code"] == "missing_identity"
