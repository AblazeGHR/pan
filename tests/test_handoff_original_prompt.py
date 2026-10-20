"""Prompt selection is independent of settings and validated before any writes."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from packages.core import session as sess
from packages.mcp import server as mcp
from packages.web import server


@pytest.mark.parametrize("copy_settings", [True, False])
@pytest.mark.parametrize("options,expected", [
    ({}, "old rules"),
    ({"originalPrompt": "ignored without flag"}, "old rules"),
    ({"overrideOriginalPrompt": False, "originalPrompt": "stale draft"}, "old rules"),
    ({"overrideOriginalPrompt": True, "originalPrompt": " new rules\n"}, " new rules\n"),
    ({"overrideOriginalPrompt": True, "originalPrompt": ""}, ""),
])
def test_http_prompt_selection_and_persistence(monkeypatch, copy_settings, options, expected):
    a = sess.create("source", adapter="codex", original_prompt="old rules",
                    handoff_prompt="OLD BRIEF", cli_session_id="old-cli")
    a.offline_owner = {"generation": 9, "runId": "old-run"}
    a.owner_retirements = {"old": {"reason": "retired"}}
    a.adapter_config["_steer_requests"] = {"old": {"status": "accepted"}}
    child = sess.create("child", adapter="codex")
    a.managed = [child.id]
    a.report_subscriptions = {child.id}
    child.managed_by = a.id
    sess.save(a)
    sess.save(child)
    monkeypatch.setattr(server, "broadcast", AsyncMock())
    retarget = Mock(return_value={"updated": 0})
    monkeypatch.setattr(server.background_jobs, "retarget_session_jobs", retarget)
    # Real HTTP routing/JSON boundary, without starting application workers.
    response = TestClient(server.app).post(f"/api/sessions/{a.id}/handoff", json={
        "handoffPrompt": " new brief ", "copySettings": copy_settings,
        "adapter": "codex", **options,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True, data
    b = sess.get(data["session"]["id"])
    assert b.original_prompt == expected
    assert b.handoff_prompt == "new brief"
    if expected:
        assert b.system_prompt == ("【交接上下文（由被交接 session A 的 agent 编写）】\n"
            "new brief\n\n【原 session 的 system prompt】\n" + expected)
    else:
        assert b.system_prompt == "new brief"
    assert "OLD BRIEF" not in b.system_prompt
    assert a.original_prompt == "old rules" and a.handoff_prompt == "OLD BRIEF"
    assert b.cli_session_id is None and b.history == []
    assert b.offline_owner is None and b.owner_retirements == {}
    assert "_steer_requests" not in b.adapter_config
    assert a.offline_owner == {"generation": 9, "runId": "old-run"}
    assert b.managed == [child.id, a.id] and child.managed_by == b.id
    assert b.report_subscriptions == {child.id, a.id}
    retarget.assert_called_once_with(a.id, b.id)
    sess._cache.clear()
    assert sess.get(b.id).original_prompt == expected
    assert sess.get(a.id).handoff_prompt == "OLD BRIEF"


@pytest.mark.parametrize("switch,value", [(None, "x"), (0, "x"), (1, "x"), ("false", "x"),
    (True, None), (True, 7), (True, False), (True, []), (True, {})])
def test_invalid_prompt_has_no_side_effects(monkeypatch, switch, value):
    a = sess.create("source", adapter="codex", original_prompt="rules")
    before = (sess.SESSION_DIR / f"{a.id}.json").read_bytes()
    retarget = Mock()
    monkeypatch.setattr(server.background_jobs, "retarget_session_jobs", retarget)
    broadcast = AsyncMock()
    monkeypatch.setattr(server, "broadcast", broadcast)
    result = sess.handoff_session(a.id, "brief", override_original_prompt=switch, original_prompt=value)
    assert isinstance(result, str)
    result = asyncio.run(server.api_session_handoff(a.id, {
        "handoffPrompt": "brief", "overrideOriginalPrompt": switch, "originalPrompt": value,
    }))
    assert "error" in result
    access = Mock()
    api = Mock()
    monkeypatch.setattr(mcp, "_check_access", access)
    monkeypatch.setattr(mcp, "_api", api)
    assert mcp.session_handoff(a.id, "brief", override_original_prompt=switch,
                               original_prompt=value)["ok"] is False
    access.assert_not_called()
    api.assert_not_called()
    retarget.assert_not_called()
    broadcast.assert_not_called()
    assert len(sess._cache) == 1
    assert (sess.SESSION_DIR / f"{a.id}.json").read_bytes() == before
    assert len(list(sess.SESSION_DIR.glob("*.json"))) == 1


@pytest.mark.parametrize("copy_settings", [True, False])
@pytest.mark.parametrize("switch,value", [(False, "ignored"), (True, "new"), (True, "")])
def test_mcp_mapping(monkeypatch, copy_settings, switch, value):
    monkeypatch.setattr(mcp, "_check_access", Mock(return_value=None))
    api = Mock(return_value={"ok": True})
    monkeypatch.setattr(mcp, "_api", api)
    assert mcp.session_handoff("a", "brief", copy_settings=copy_settings, adapter="codex",
        override_original_prompt=switch, original_prompt=value)["ok"]
    expected = {"handoffPrompt": "brief", "copySettings": copy_settings, "adapter": "codex"}
    if switch:
        expected.update(overrideOriginalPrompt=True, originalPrompt=value)
    api.assert_called_once_with("POST", "/api/sessions/a/handoff", expected)


def test_enabled_override_requires_provided_string(monkeypatch):
    a = sess.create("source", adapter="codex")
    assert isinstance(sess.handoff_session(a.id, "brief", override_original_prompt=True), str)
    assert "error" in asyncio.run(server.api_session_handoff(a.id, {
        "handoffPrompt": "brief", "overrideOriginalPrompt": True,
    }))
    access = Mock()
    monkeypatch.setattr(mcp, "_check_access", access)
    assert mcp.session_handoff(a.id, "brief", override_original_prompt=True)["ok"] is False
    access.assert_not_called()


@pytest.mark.parametrize("options", [
    {"override_original_prompt": "true", "original_prompt": "x"},
    {"override_original_prompt": 1, "original_prompt": "x"},
    {"override_original_prompt": None},
    {"override_original_prompt": True, "original_prompt": 42},
])
def test_mcp_dispatch_does_not_coerce_invalid_types(monkeypatch, options):
    access = Mock()
    monkeypatch.setattr(mcp, "_check_access", access)
    with pytest.raises(ToolError, match="validation error"):
        asyncio.run(mcp.mcp.call_tool("session_handoff", {
            "session_id": "a", "handoff_prompt": "brief", **options,
        }))
    access.assert_not_called()
