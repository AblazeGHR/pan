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


def test_http_message_job_creation_preserves_source_and_text(monkeypatch):
    calls = []

    class Target:
        def __init__(self, sid):
            self.id = sid
            self.readonly_session = False
            self.managed_by = None

    monkeypatch.setattr(web_server.sess, "get", lambda sid: Target(sid) if sid in {
        "ses_target", "ses_caller"} else None)

    def create(target, text, schedule, **kwargs):
        calls.append((target, text, schedule, kwargs))
        return {"jobId": "job_message", "kind": "session-message"}

    monkeypatch.setattr(web_server.background_jobs, "start_message", create)
    result = asyncio.run(web_server.api_session_message_job_start({
        "targetSessionId": "ses_target", "text": "message text",
        "schedule": {"type": "once", "delaySeconds": 10},
        "source": "agent", "sourceSessionId": "ses_caller",
        "description": "a job"}))
    assert result["kind"] == "session-message"
    assert calls == [("ses_target", "message text",
                      {"type": "once", "delaySeconds": 10},
                      {"description": "a job", "source": "agent",
                       "source_session_id": "ses_caller"})]


def test_http_selected_session_broadcast_reuses_send_session(monkeypatch):
    class Target:
        id = "session"
        readonly_session = False
        managed_by = None

    monkeypatch.setattr(web_server.sess, "get", lambda sid: Target())
    calls = []

    async def send(session_id, text, **kwargs):
        calls.append((session_id, text, kwargs))
        return {"status": "queued", "workerId": None, "sessionId": session_id}

    monkeypatch.setattr(web_server.worker, "send_session", send)
    result = asyncio.run(web_server.api_sessions_broadcast({
        "sessionIds": ["ses_a", "ses_a", "ses_b"], "text": "hello",
        "source": "agent", "sourceSessionId": "ses_caller"}))
    assert result["ok"] is True
    assert [item[0] for item in calls] == ["ses_a", "ses_b"]
    assert all(item[2] == {"source": "agent", "force": False,
                           "source_session_id": "ses_caller"} for item in calls)


def test_mcp_message_job_shortcuts_preserve_caller_prefix(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_caller_identity",
                        lambda: {"id": "ses_caller"})
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    monkeypatch.setattr(mcp_server, "_agent_message_prefix",
                        lambda text: "PREFIX\n" + text)
    monkeypatch.setattr(mcp_server, "_api", lambda method, path, body=None,
                        timeout=30.0: calls.append((method, path, body)) or {
                            "jobId": "job_message", "kind": "session-message"})
    result = mcp_server.agent_message_job_create(
        "do it", {"type": "once", "delaySeconds": 5},
        target_session_id="ses_target", description="desc")
    assert result["jobId"] == "job_message"
    assert calls[0] == ("POST", "/api/session-message-jobs", {
        "targetSessionId": "ses_target", "text": "PREFIX\ndo it",
        "schedule": {"type": "once", "delaySeconds": 5},
        "description": "desc", "source": "agent",
        "sourceSessionId": "ses_caller"})
