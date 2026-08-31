"""Mock tests for Claude's dashboard permission bridge.

No Claude process or Pan HTTP service is started.  The tests exercise the
same Worker future/control path used by the MCP permission-prompt tool.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters.claude.adapter import ClaudeAdapter
import packages.mcp.server as mcp_server


class _LiveProcess:
    returncode = None


def _cleanup() -> None:
    worker._claude_permission_requests.clear()
    worker.workers.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def test_permission_request_round_trip():
    _cleanup()
    s = _sess.Session(id="ses_claude_permission", name="permission", adapter="claude")
    _sess._cache[s.id] = s
    w = worker.Worker(
        worker_id="worker-permission",
        session_id=s.id,
        adapter=ClaudeAdapter(),
        process=_LiveProcess(),
        pending_signal=asyncio.Queue(),
    )
    worker.workers[w.worker_id] = w
    events: list[dict] = []

    async def broadcast(event: dict) -> None:
        events.append(event)

    async def run() -> None:
        worker.set_broadcaster(broadcast)
        pending = asyncio.create_task(
            worker.request_claude_permission(
                w.worker_id, "Bash", {"command": "pytest -q"}
            )
        )
        for _ in range(50):
            if worker._claude_permission_requests:
                break
            await asyncio.sleep(0)
        assert worker._claude_permission_requests
        request_id = next(iter(worker._claude_permission_requests))
        approval = events[0]["event"]
        assert approval["type"] == "approval.request"
        assert approval["method"] == "claude/permission"
        assert approval["params"]["tool_name"] == "Bash"
        assert approval["params"]["input"] == {"command": "pytest -q"}

        assert await worker.send_control_message(w.worker_id, {
            "type": "permission_response",
            "request_id": request_id,
            "decision": "accept",
        }) is None
        result = await asyncio.wait_for(pending, timeout=1)
        assert result == {
            "behavior": "allow",
            "updatedInput": {"command": "pytest -q"},
        }
        assert any(
            event["event"]["type"] == "claude.permission_resolved"
            for event in events[1:]
        )

    try:
        asyncio.run(run())
    finally:
        _cleanup()


def test_permission_request_is_denied_when_worker_is_stopped():
    _cleanup()
    s = _sess.Session(id="ses_claude_permission_dead", name="permission-dead", adapter="claude")
    _sess._cache[s.id] = s
    w = worker.Worker(
        worker_id="worker-permission-dead",
        session_id=s.id,
        adapter=ClaudeAdapter(),
        process=_LiveProcess(),
        pending_signal=asyncio.Queue(),
    )
    worker.workers[w.worker_id] = w

    async def run() -> None:
        pending = asyncio.create_task(
            worker.request_claude_permission(w.worker_id, "Write", {"file_path": "x"})
        )
        for _ in range(50):
            if worker._claude_permission_requests:
                break
            await asyncio.sleep(0)
        worker._cancel_claude_permission_requests(w.worker_id, "worker stopped")
        result = await asyncio.wait_for(pending, timeout=1)
        assert result["behavior"] == "deny"
        assert result["message"] == "worker stopped"

    try:
        asyncio.run(run())
    finally:
        _cleanup()


def test_mcp_permission_tool_uses_session_worker(monkeypatch):
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_manager")
    monkeypatch.setattr(mcp_server, "_session_worker_id", lambda sid: "worker-7")
    calls: list[tuple] = []

    def fake_api(method, path, body=None, timeout=30.0):
        calls.append((method, path, body, timeout))
        return {"behavior": "allow", "updatedInput": {"command": "git status"}}

    monkeypatch.setattr(mcp_server, "_api", fake_api)
    result = json.loads(mcp_server.permission_prompt("Bash", {"command": "git status"}))
    assert result == {"behavior": "allow", "updatedInput": {"command": "git status"}}
    assert calls == [
        (
            "POST",
            "/api/worker/worker-7/claude-permission",
            {"toolName": "Bash", "input": {"command": "git status"}},
            360.0,
        )
    ]
