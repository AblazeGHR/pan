"""MCP Send authorization needs metadata once per call, never full history."""
import pytest

from packages.mcp import server as mcp


@pytest.mark.parametrize("legacy_worker", [False, True])
def test_send_reads_caller_metadata_once_and_keeps_source_identity(monkeypatch, legacy_worker):
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses-caller")
    monkeypatch.setenv("PAN_AGENT_SESSION_TITLE", "caller")
    calls = []
    caller = {"id": "ses-caller", "managed": ["ses-target"],
              "panAccess": {"restrictToManaged": True, "canClaimUnmanaged": False}}

    def api(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if path.startswith("/api/sessions/ses-caller"):
            return caller
        if path == "/api/list":
            return {"workers": [{"workerId": "worker-target", "sessionId": "ses-target"}]}
        assert method == "POST"
        assert path == ("/api/task" if legacy_worker else "/api/send")
        assert body["sourceSessionId"] == "ses-caller"
        assert body["text"] == "////by agent : ses-caller | caller\nmessage"
        return {"status": "queued"}

    monkeypatch.setattr(mcp, "_api", api)
    result = (mcp.worker_send(worker_id="worker-target", text="message") if legacy_worker
              else mcp.agent_send_to_queue("ses-target", "message"))
    assert result["status"] == "queued"
    caller_reads = [path for method, path, _ in calls if path.startswith("/api/sessions/ses-caller")]
    assert caller_reads == ["/api/sessions/ses-caller?view=metadata"]


@pytest.mark.parametrize("foreign_manager,can_claim", [("other", True), (None, False), (None, True)])
def test_send_metadata_preserves_claim_and_foreign_manager_denial(monkeypatch, foreign_manager, can_claim):
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses-caller")
    posted = []

    def api(method, path, body=None, **kwargs):
        if path.startswith("/api/sessions/ses-caller"):
            return {"id": "ses-caller", "managed": [],
                    "panAccess": {"restrictToManaged": True, "canClaimUnmanaged": can_claim}}
        if path.startswith("/api/sessions/ses-target"):
            return {"id": "ses-target", "managedBy": foreign_manager}
        posted.append(path)
        if path == "/api/claim":
            assert body == {"managerId": "ses-caller", "sessionId": "ses-target"}
            return {"ok": True}
        assert path == "/api/send" and body["sourceSessionId"] == "ses-caller"
        return {"status": "queued"}

    monkeypatch.setattr(mcp, "_api", api)
    result = mcp.agent_send_to_queue("ses-target", "message")
    if foreign_manager or not can_claim:
        assert result["error"]["code"] == "permission_denied"
        assert posted == []
    else:
        assert result["status"] == "queued"
        assert posted == ["/api/claim", "/api/send"]
