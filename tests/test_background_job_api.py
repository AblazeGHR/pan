import asyncio

import pytest

import packages.mcp.server as mcp_server
import packages.web.server as web_server


def test_http_start_reports_missing_session(monkeypatch):
    def missing(*args, **kwargs):
        raise ValueError("target session does not exist")
    monkeypatch.setattr(web_server.background_jobs, "start", missing)
    result = asyncio.run(web_server.api_background_job_start({
        "targetSessionId": "ses_missing", "argv": ["python"], "cwd": "."}))
    assert result["error"]["code"] == "invalid_job"
    assert "target session" in result["error"]["message"]


def test_http_start_invalid_argv_and_cwd(monkeypatch):
    def invalid(*args, **kwargs):
        raise ValueError("argv must be a non-empty string array")
    monkeypatch.setattr(web_server.background_jobs, "start", invalid)
    result = asyncio.run(web_server.api_background_job_start({
        "targetSessionId": "ses_ok", "argv": [], "cwd": "C:\\outside"}))
    assert result["error"]["code"] == "invalid_job"


def test_mcp_start_defaults_to_current_session_and_checks_access(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_caller_identity", lambda: {"id": "ses_self"})
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    monkeypatch.setattr(mcp_server, "_api", lambda method, path, body=None, timeout=30.0:
                        calls.append((method, path, body)) or {"jobId": "job_1"})
    result = mcp_server.agent_background_start(["python", "train.py"], "C:\\Pan")
    assert result["jobId"] == "job_1"
    assert calls[0][2]["targetSessionId"] == "ses_self"


def test_mcp_start_denies_unmanaged_target(monkeypatch):
    monkeypatch.setattr(mcp_server, "_caller_identity", lambda: {"id": "ses_self"})
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False:
                        {"ok": False, "error": {"code": "permission_denied"}})
    called = []
    monkeypatch.setattr(mcp_server, "_api", lambda *args, **kwargs: called.append(args))
    result = mcp_server.agent_background_start(["python"], "C:\\Pan", "ses_other")
    assert result["error"]["code"] == "permission_denied"
    assert called == []


def test_mcp_get_list_cancel_retry_use_managed_target(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_caller_identity", lambda: {"id": "ses_self"})
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)

    def api(method, path, body=None, timeout=30.0):
        calls.append((method, path, body))
        if method == "GET" and path.endswith("job_1"):
            return {"jobId": "job_1", "targetSessionId": "ses_managed"}
        if method == "GET":
            return {"jobs": []}
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "_api", api)
    assert mcp_server.agent_background_get("job_1")["jobId"] == "job_1"
    assert mcp_server.agent_background_list()["jobs"] == []
    assert mcp_server.agent_background_cancel("job_1")["ok"]
    assert mcp_server.agent_background_retry("job_1")["ok"]
    assert any(path.endswith("/cancel") for _, path, _ in calls)
    assert any(path.endswith("/retry") for _, path, _ in calls)


def test_http_retry_rejects_running_job(monkeypatch):
    def running(job_id):
        raise ValueError("running jobs cannot be retried; cancel them first")
    monkeypatch.setattr(web_server.background_jobs, "retry", running)
    result = asyncio.run(web_server.api_background_job_retry("job_running"))
    assert result["error"]["code"] == "job_not_retryable"


def test_http_cancel_reports_unsafe_pid(monkeypatch):
    def unsafe(job_id):
        raise ValueError("cannot safely cancel: task PID identity is unavailable or reused")
    monkeypatch.setattr(web_server.background_jobs, "cancel", unsafe)
    result = asyncio.run(web_server.api_background_job_cancel("job_reused"))
    assert result["error"]["code"] == "cancel_unsafe"
