"""Regression tests for MCP model discovery across registered adapters."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.mcp import server as mcp_server  # noqa: E402


ADAPTERS = [
    {"name": "cbc", "defaultModel": "deepseek-v4-flash", "supportsResume": True},
    {"name": "codex", "defaultModel": "gpt-5.6-luna", "supportsResume": True},
]


class _FakeAPI:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body=None, timeout=30.0):
        self.calls.append((method, path, body, timeout))
        if path == "/api/adapters":
            return {"adapters": ADAPTERS, "default": "cbc"}
        if path == "/api/models?adapter=codex":
            return {"models": ["gpt-5.6-luna"], "default": "gpt-5.6-luna"}
        raise AssertionError(f"unexpected API call: {method} {path}")


@pytest.mark.parametrize("adapter", [None, "", "   "])
def test_model_list_without_adapter_returns_inventory_error(monkeypatch, adapter):
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)

    result = mcp_server.model_list(adapter=adapter)

    assert result["ok"] is False
    assert result["error"]["code"] == "adapter_required"
    assert result["availableAdapters"] == [
        {"name": "cbc", "defaultModel": "deepseek-v4-flash", "supportsResume": True},
        {"name": "codex", "defaultModel": "gpt-5.6-luna", "supportsResume": True},
    ]
    assert "default" not in result
    assert "cbc" not in result.get("models", [])
    assert result["next"] == {"tool": "model_list", "adapter": "codex"}
    assert "model_list(adapter='codex')" in result["callHint"]
    assert [call[1] for call in fake.calls] == ["/api/adapters"]


def test_model_list_codex_returns_models_and_default(monkeypatch):
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)

    result = mcp_server.model_list(adapter=" codex ")

    assert result == {"models": ["gpt-5.6-luna"], "default": "gpt-5.6-luna"}
    assert [call[1] for call in fake.calls] == [
        "/api/adapters",
        "/api/models?adapter=codex",
    ]


def test_model_list_unknown_adapter_is_actionable_and_does_not_fallback(monkeypatch):
    fake = _FakeAPI()
    monkeypatch.setattr(mcp_server, "_api", fake)

    result = mcp_server.model_list(adapter="not-registered")

    assert result["ok"] is False
    assert result["adapter"] == "not-registered"
    assert result["error"]["code"] == "unknown_adapter"
    assert "not-registered" in result["error"]["message"]
    assert result["availableAdapters"] == [
        {"name": "cbc", "defaultModel": "deepseek-v4-flash", "supportsResume": True},
        {"name": "codex", "defaultModel": "gpt-5.6-luna", "supportsResume": True},
    ]
    assert "model_list(adapter='codex')" in result["callHint"]
    assert [call[1] for call in fake.calls] == ["/api/adapters"]
