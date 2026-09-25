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
        mcp_server.scheduler_create("ses_x", "text", "once", at="2026-09-16T09:00:00"),
        mcp_server.scheduler_list(),
        mcp_server.scheduler_list("ses_x"),
        mcp_server.scheduler_get("sch_1"),
        mcp_server.scheduler_update("sch_1", name="n"),
        mcp_server.scheduler_delete("sch_1"),
        mcp_server.scheduler_pause("sch_1"),
        mcp_server.scheduler_resume("sch_1"),
        mcp_server.scheduler_run_now("sch_1"),
        mcp_server.scheduler_runs("sch_1"),
    ]
    assert results == [{"error": "missing_identity"}] * len(results)
    assert calls == []


# --------------------------------------------------------------------------
# access denial → no HTTP
# --------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda: mcp_server.scheduler_create("ses_other", "text", "once"),
    lambda: mcp_server.scheduler_list("ses_other"),
    lambda: mcp_server.scheduler_get("sch_1", "ses_other"),
    lambda: mcp_server.scheduler_update("sch_1", "ses_other", name="n"),
    lambda: mcp_server.scheduler_delete("sch_1", "ses_other"),
    lambda: mcp_server.scheduler_pause("sch_1", "ses_other"),
    lambda: mcp_server.scheduler_resume("sch_1", "ses_other"),
    lambda: mcp_server.scheduler_run_now("sch_1", "ses_other"),
    lambda: mcp_server.scheduler_runs("sch_1", "ses_other"),
])
def test_denied_target_never_issues_http(identity, monkeypatch, call):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: DENIED)
    calls = _recorder(monkeypatch)
    assert call() == DENIED
    assert calls == []


def test_write_tools_claim_and_read_tools_do_not(identity, monkeypatch):
    seen = []

    def fake_check(target, claim=False):
        seen.append((target, claim))
        return None

    monkeypatch.setattr(mcp_server, "_check_access", fake_check)
    calls = _recorder(monkeypatch)

    mcp_server.scheduler_create("ses_t", "text", "cron", cron="0 9 * * 1-5")
    mcp_server.scheduler_update("sch_1", "ses_t", enabled=False)
    mcp_server.scheduler_delete("sch_1", "ses_t")
    mcp_server.scheduler_pause("sch_1", "ses_t")
    mcp_server.scheduler_resume("sch_1", "ses_t")
    mcp_server.scheduler_run_now("sch_1", "ses_t")
    mcp_server.scheduler_list("ses_t")
    mcp_server.scheduler_get("sch_1", "ses_t")
    mcp_server.scheduler_runs("sch_1", "ses_t")

    assert seen == [("ses_t", True)] * 6 + [("ses_t", False)] * 3


def test_omitted_target_is_resolved_then_authorized_before_mutation(identity, monkeypatch):
    """No target passed: a read-only GET resolves it; denial blocks the mutation."""
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: DENIED)
    calls = _recorder(monkeypatch)
    assert mcp_server.scheduler_delete("sch_1") == DENIED
    assert calls == [("GET", "/api/scheduler/tasks/sch_1", None)]


# --------------------------------------------------------------------------
# happy path: method / path / body
# --------------------------------------------------------------------------

def test_create_posts_schedule_body(identity, monkeypatch):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    calls = _recorder(monkeypatch)
    result = mcp_server.scheduler_create(
        "ses_target", "检查构建结果", "interval", name="每半小时自检",
        interval_sec=1800, timezone="Asia/Shanghai", max_runs=3,
        misfire_policy="skip")
    assert result == TASK
    method, path, body = calls[0]
    assert (method, path) == ("POST", "/api/scheduler/tasks")
    assert body["targetSessionId"] == "ses_target"
    assert body["text"] == "检查构建结果"
    assert body["schedule"] == {"kind": "interval", "intervalSec": 1800,
                                "timezone": "Asia/Shanghai"}
    assert body["name"] == "每半小时自检"
    assert body["maxRuns"] == 3
    assert body["misfirePolicy"] == "skip"
    assert body["enabled"] is True


def test_create_once_uses_at(identity, monkeypatch):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    calls = _recorder(monkeypatch)
    mcp_server.scheduler_create("ses_target", "回来检查", "once", at="2026-09-16T18:30:00")
    assert calls[0][2]["schedule"] == {"kind": "once", "at": "2026-09-16T18:30:00"}


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


def test_update_patches_only_provided_fields(identity, monkeypatch):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    calls = _recorder(monkeypatch)
    mcp_server.scheduler_update("sch_1", "ses_target", enabled=False, max_runs=1)
    method, path, body = calls[0]
    assert (method, path) == ("PATCH", "/api/scheduler/tasks/sch_1")
    assert body == {"enabled": False, "maxRuns": 1}


def test_delete_pause_resume_run_now_paths(identity, monkeypatch):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    calls = _recorder(monkeypatch)
    mcp_server.scheduler_delete("sch_1", "ses_target")
    mcp_server.scheduler_pause("sch_1", "ses_target")
    mcp_server.scheduler_resume("sch_1", "ses_target")
    mcp_server.scheduler_run_now("sch_1", "ses_target")
    assert [c[:2] for c in calls] == [
        ("DELETE", "/api/scheduler/tasks/sch_1"),
        ("POST", "/api/scheduler/tasks/sch_1/pause"),
        ("POST", "/api/scheduler/tasks/sch_1/resume"),
        ("POST", "/api/scheduler/tasks/sch_1/run-now"),
    ]


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
    assert mcp_server.scheduler_delete("sch_1", "ses_target") == NOT_FOUND


def test_network_error_is_returned_as_error(identity, monkeypatch):
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    _recorder(monkeypatch, response={"ok": False, "error": {
        "code": "connection_error", "message": "refused"}})
    result = mcp_server.scheduler_list("ses_target")
    assert result["ok"] is False
    assert result["error"]["code"] == "connection_error"
