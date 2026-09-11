import pytest

pytest.importorskip("mcp")

from packages.mcp import server as mcp_server


def test_mcp_session_create_forwards_optional_codex_context_settings(monkeypatch):
    calls = []

    monkeypatch.setattr(mcp_server, "_check_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(mcp_server, "_auto_claim", lambda *args, **kwargs: None)

    def fake_api(method, path, body=None, timeout=30.0):
        calls.append((method, path, body))
        return {"id": "ses_context"}

    monkeypatch.setattr(mcp_server, "_api", fake_api)
    mcp_server.session_create(
        name="context",
        adapter="codex",
        model_context_window=64000,
        model_auto_compact_token_limit=60800,
    )
    assert calls == [
        (
            "POST",
            "/api/sessions",
            {
                "name": "context",
                "adapter": "codex",
                "modelContextWindow": 64000,
                "modelAutoCompactTokenLimit": 60800,
            },
        )
    ]


def test_mcp_session_update_preserves_omitted_and_can_clear_each_setting(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_check_access", lambda *args, **kwargs: None)

    def fake_api(method, path, body=None, timeout=30.0):
        calls.append((method, path, body))
        return {"id": "ses_context", "requireRestart": True}

    monkeypatch.setattr(mcp_server, "_api", fake_api)
    mcp_server.session_update("ses_context")
    mcp_server.session_update(
        "ses_context",
        clear_model_context_window=True,
        clear_model_auto_compact_token_limit=True,
    )
    assert calls == [
        ("PATCH", "/api/sessions/ses_context", {}),
        (
            "PATCH",
            "/api/sessions/ses_context",
            {
                "modelContextWindow": None,
                "modelAutoCompactTokenLimit": None,
            },
        ),
    ]
