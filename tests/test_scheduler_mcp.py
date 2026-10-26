"""MCP layer tests for the scheduled-task (定时任务) tools.

Scope: packages/mcp/server.py only. No HTTP server, no real ``data/`` — every
test replaces ``mcp_server._api`` and never lets it reach the network.

Coverage (mirrors tests/test_background_job_api.py:28-47):
- missing PAN_AGENT_SESSION_ID → {"error": "missing_identity"} and no HTTP
- _check_access denial → the tool returns the denial and never calls _api
- happy path → _api receives the right method / path / body
"""

import pytest

import packages.mcp.server as mcp_server


DENIED = {"ok": False, "error": {"code": "permission_denied", "message": "not managed"}}
NOT_FOUND = {"ok": False, "error": {"code": "not_found", "message": "task not found"}}
TASK = {"ok": True, "task": {
    "id": "sch_1", "name": "daily", "targetSessionId": "ses_target",
    "text": "do it", "schedule": {"kind": "cron", "cron": "0 9 * * 1-5"},
    "nextFireAt": "2026-09-17T09:00:00"}}


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_self")


def _recorder(monkeypatch, response=None):
    """Patch _api with a call recorder; each entry is (method, path, body)."""
    calls = []

    def fake(method, path, body=None, timeout=30.0):
        calls.append((method, path, body))
        if callable(response):
            return response(method, path, body)
        return TASK if response is None else response

    monkeypatch.setattr(mcp_server, "_api", fake)
    return calls


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def test_missing_identity_short_circuits_every_tool(monkeypatch):
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    calls = _recorder(monkeypatch)
    results = [
        mcp_server.scheduler_list(),
        mcp_server.scheduler_list("ses_x"),
        mcp_server.scheduler_get("sch_1"),
        mcp_server.scheduler_runs("sch_1"),
    ]
    assert results == [{"error": "missing_identity"}] * len(results)
    assert calls == []


# --------------------------------------------------------------------------
# access denial → no HTTP
# --------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda: mcp_server.scheduler_list("ses_other"),
    lambda: mcp_server.scheduler_get("sch_1", "ses_other"),
    lambda: mcp_server.scheduler_runs("sch_1", "ses_other"),
])
def test_denied_target_never_issues_http(identity, monkeypatch, call):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: DENIED)
    calls = _recorder(monkeypatch)
    assert call() == DENIED
    assert calls == []


def test_read_tools_never_claim(identity, monkeypatch):
    seen = []

    def fake_check(target, claim=False):
        seen.append((target, claim))
        return None

    monkeypatch.setattr(mcp_server, "_check_access", fake_check)
    calls = _recorder(monkeypatch)

    mcp_server.scheduler_list("ses_t")
    mcp_server.scheduler_get("sch_1", "ses_t")
    mcp_server.scheduler_runs("sch_1", "ses_t")

    assert seen == [("ses_t", False)] * 3


# --------------------------------------------------------------------------
# happy path: method / path / body
# --------------------------------------------------------------------------


def test_list_and_get_paths(identity, monkeypatch):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    calls = _recorder(monkeypatch)
    mcp_server.scheduler_list()
    mcp_server.scheduler_list("ses_target")
    mcp_server.scheduler_get("sch_1", "ses_target")
    assert [c[:2] for c in calls] == [
        ("GET", "/api/scheduler/tasks"),
        ("GET", "/api/scheduler/tasks"),
        ("GET", "/api/scheduler/tasks/sch_1"),
    ]


def test_list_filters_by_target_session(identity, monkeypatch):
    """后端无 targetSessionId 过滤参数，MCP 层本地过滤。"""
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    _recorder(monkeypatch, response={"ok": True, "tasks": [
        {"id": "sch_1", "targetSessionId": "ses_target"},
        {"id": "sch_2", "targetSessionId": "ses_other"},
    ]})
    result = mcp_server.scheduler_list("ses_target")
    assert [t["id"] for t in result["tasks"]] == ["sch_1"]

    _recorder(monkeypatch, response=NOT_FOUND)
    assert mcp_server.scheduler_list("ses_target") == NOT_FOUND


def test_runs_path_carries_limit(identity, monkeypatch):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    calls = _recorder(monkeypatch)
    mcp_server.scheduler_runs("sch_1", "ses_target", limit=10)
    assert calls[0][:2] == ("GET", "/api/scheduler/tasks/sch_1/runs?limit=10")


def test_task_id_is_url_quoted(identity, monkeypatch):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    calls = _recorder(monkeypatch)
    mcp_server.scheduler_get("sch 1/x", "ses_target")
    assert calls[0][1] == "/api/scheduler/tasks/sch%201%2Fx"


# --------------------------------------------------------------------------
# error pass-through
# --------------------------------------------------------------------------

def test_backend_error_is_passed_through_unchanged(identity, monkeypatch):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    _recorder(monkeypatch, response=NOT_FOUND)
    assert mcp_server.scheduler_get("sch_1", "ses_target") == NOT_FOUND


def test_network_error_is_returned_as_error(identity, monkeypatch):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    _recorder(monkeypatch, response={"ok": False, "error": {
        "code": "connection_error", "message": "refused"}})
    result = mcp_server.scheduler_list("ses_target")
    assert result["ok"] is False
    assert result["error"]["code"] == "connection_error"
