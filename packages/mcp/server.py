"""Pan MCP Server — wraps Pan's HTTP API as MCP tools for agent consumption.

Usage:
    python -m packages.mcp.server                 # stdio (default)
    python -m packages.mcp.server --transport sse --port 9740   # SSE transport

Tools exposed:
    - session_create: Create a new session
    - session_list: List all sessions
    - session_get: Get session details
    - session_delete: Delete a session
    - worker_spawn: Spawn a worker for a session
    - worker_task: Send a task to a worker
    - worker_kill: Kill a worker
    - session_history: Get paginated conversation history
    - model_list: List available AI models

Environment variables:
    PAN_API_URL: Pan API base URL (default: http://127.0.0.1:8768)
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
import urllib.error

from mcp.server.fastmcp import FastMCP

_pan_api_url = os.environ.get("PAN_API_URL", "http://127.0.0.1:8768")

mcp = FastMCP("Pan")


def _api(method: str, path: str, body: dict | None = None) -> dict:
    """Call Pan's HTTP API and return parsed JSON response."""
    url = f"{_pan_api_url}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(error_body)
        except json.JSONDecodeError:
            return {"ok": False, "error": {"code": e.code, "message": error_body}}
    except urllib.error.URLError as e:
        return {"ok": False, "error": {"code": "connection_error", "message": str(e.reason)}}


# ---------------------------------------------------------------------------
# Session management tools
# ---------------------------------------------------------------------------

@mcp.tool()
def session_create(
    name: str,
    adapter: str = "cbc",
    model: str | None = None,
    permission_mode: str | None = None,
) -> dict:
    """Create a new session (persistent conversation container).

    Args:
        name: Session name (unique, no spaces)
        adapter: CLI adapter to use ("cbc" or "kimi")
        model: AI model name (e.g. "hy3", "deepseek-v4-flash")
        permission_mode: Permission mode ("bypassPermissions", "acceptEdits", "default", "plan")
    """
    body: dict = {"name": name, "adapter": adapter}
    if model:
        body["model"] = model
    if permission_mode:
        body["permissionMode"] = permission_mode
    return _api("POST", "/api/sessions", body)


@mcp.tool()
def session_list() -> dict:
    """List all sessions with their worker status."""
    return _api("GET", "/api/sessions")


@mcp.tool()
def session_get(session_id: str) -> dict:
    """Get full session details including history and last result.

    Args:
        session_id: Session ID (e.g. "ses_abc123def4567890")
    """
    return _api("GET", f"/api/sessions/{session_id}")


@mcp.tool()
def session_delete(session_id: str) -> dict:
    """Delete a session and kill its worker if running.

    Args:
        session_id: Session ID to delete
    """
    return _api("DELETE", f"/api/sessions/{session_id}")


@mcp.tool()
def session_history(session_id: str, limit: int = 50, before: int | None = None) -> dict:
    """Get paginated conversation history for a session.

    Args:
        session_id: Session ID
        limit: Max history entries to return (default 50)
        before: Only return entries before this index (for pagination)
    """
    path = f"/api/sessions/{session_id}/history?limit={limit}"
    if before is not None:
        path += f"&before={before}"
    return _api("GET", path)


# ---------------------------------------------------------------------------
# Worker management tools
# ---------------------------------------------------------------------------

@mcp.tool()
def worker_spawn(session_id: str | None = None, name: str | None = None,
                 adapter: str = "cbc", model: str | None = None) -> dict:
    """Spawn a worker (CLI process) for a session. Creates session if name given.

    Args:
        session_id: Existing session ID to spawn worker for
        name: Or create a new session with this name
        adapter: CLI adapter (default "cbc")
        model: Model override
    """
    body: dict = {"adapter": adapter}
    if session_id:
        body["sessionId"] = session_id
    if name:
        body["name"] = name
    if model:
        body["model"] = model
    return _api("POST", "/api/spawn", body)


@mcp.tool()
def worker_task(session_id: str | None = None, worker_id: str | None = None,
                text: str = "", source: str = "agent") -> dict:
    """Send a task to a worker. Auto-spawns if session has no worker.

    Args:
        session_id: Session ID (finds worker by session if worker_id not given)
        worker_id: Worker ID (e.g. "worker-1")
        text: Task text / prompt to send
        source: Source label (default "agent")
    """
    body: dict = {"text": text, "source": source}
    if worker_id:
        body["workerId"] = worker_id
    if session_id:
        body["sessionId"] = session_id
    return _api("POST", "/api/task", body)


@mcp.tool()
def worker_kill(worker_id: str) -> dict:
    """Kill a worker process (session data persists).

    Args:
        worker_id: Worker ID to kill (e.g. "worker-1")
    """
    return _api("POST", f"/api/kill/{worker_id}")


@mcp.tool()
def worker_list() -> dict:
    """List all running workers."""
    return _api("GET", "/api/list")


# ---------------------------------------------------------------------------
# Model / adapter info
# ---------------------------------------------------------------------------

@mcp.tool()
def model_list(adapter: str = "cbc") -> dict:
    """List available AI models for an adapter.

    Args:
        adapter: Adapter name ("cbc" or "kimi")
    """
    return _api("GET", f"/api/models?adapter={adapter}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global _pan_api_url  # module-level override; __main__ attr would be a no-op when imported (#41)
    parser = argparse.ArgumentParser(description="Pan MCP Server")
    parser.add_argument("--transport", default="stdio",
                        choices=["stdio", "sse", "streamable-http"])
    parser.add_argument("--port", type=int, default=9740,
                        help="Port for SSE/streamable-http transport (default: 9740)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host for SSE/streamable-http transport")
    parser.add_argument("--pan-url", default=_pan_api_url,
                        help=f"Pan API base URL (default: {_pan_api_url})")
    args = parser.parse_args()

    # Update module-level API URL so tools use the CLI override
    _pan_api_url = args.pan_url.rstrip("/")

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
