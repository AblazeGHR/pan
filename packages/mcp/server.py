"""Pan MCP Server — wraps Pan's HTTP API as MCP tools for agent consumption.

Usage:
    python -m packages.mcp.server                 # stdio (default)
    python -m packages.mcp.server --transport sse --port 9740   # SSE transport

Tools exposed:
    - session_create: Create a new session (optional workdir)
    - session_list: List all sessions
    - session_get: Get session details (optional history limit)
    - session_update: Update session settings (model/effort/mcp etc.)
    - session_delete: Delete a session
    - worker_spawn: Spawn a worker for a session
    - worker_task: Send a task to a worker
    - worker_kill: Kill a worker
    - session_history: Get paginated conversation history
    - model_list: List available AI models
    - report_subscribe: Subscribe to completion reports of a managed session
    - report_unsubscribe: Unsubscribe from completion reports of a managed session
    - pan_handbook: Return the full Pan orchestration handbook (reads docs/skills/pan/SKILL.md)

Environment variables:
    PAN_API_URL: Pan API base URL (default: http://127.0.0.1:8768)
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
import urllib.error
from pathlib import Path

from mcp.server.fastmcp import FastMCP

_pan_api_url = os.environ.get("PAN_API_URL", "http://127.0.0.1:8768")

mcp = FastMCP("Pan")


def _api(method: str, path: str, body: dict | None = None, timeout: float = 30.0) -> dict:
    """Call Pan's HTTP API and return parsed JSON response."""
    url = f"{_pan_api_url}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(error_body)
        except json.JSONDecodeError:
            return {"ok": False, "error": {"code": e.code, "message": error_body}}
    except urllib.error.URLError as e:
        return {"ok": False, "error": {"code": "connection_error", "message": str(e.reason)}}
    except TimeoutError:
        return {"ok": False, "error": {"code": "timeout", "message": "request timed out"}}


def _strip_usage(result: dict) -> dict:
    """Remove token usage fields from session API responses.

    Usage counters (rawUsage/totalUsage) are meaningless to agents and just
    burn context tokens. Human consumers should use the HTTP API directly.
    """
    if not isinstance(result, dict):
        return result
    result = dict(result)
    result.pop("rawUsage", None)
    result.pop("totalUsage", None)
    if "sessions" in result and isinstance(result["sessions"], list):
        result["sessions"] = [_strip_usage(s) for s in result["sessions"]]
    return result


# ---------------------------------------------------------------------------
# MCP isolation (立项 4.1 能力字段 + 4.2 managed): 受 restrict_to_managed 约束的
# session 只能操作它管理的 session
# ---------------------------------------------------------------------------

def _caller_identity() -> dict | None:
    """Return the calling agent's session info (id/capabilities/managed) or None.

    Identity comes from PAN_AGENT_SESSION_ID (4.8 injection). Returns None when
    the env var is absent or the session can't be resolved — callers then run
    unrestricted (external coordinators, sessions without restriction).
    """
    sid = os.environ.get("PAN_AGENT_SESSION_ID")
    if not sid:
        return None
    result = _api("GET", f"/api/sessions/{sid}")
    if not isinstance(result, dict) or result.get("error") or "id" not in result:
        return None
    return result


def _check_access(session_id: str, claim: bool = False) -> dict | None:
    """Enforce managed-session isolation on a target session.

    Rules:
    - No caller identity → allowed.
    - Caller operating on its own session → allowed.
    - Caller not restricted (restrictToManaged false) → allowed.
    - Target in caller's managed list → allowed.
    - Otherwise, if `claim` is True and caller canClaimUnmanaged, attempt to
      claim via POST /api/claim (succeeds only if the session is unclaimed or
      already this manager's).
    - Otherwise → permission denied (ok:false + error).

    Returns None when allowed, or an error dict when denied.
    """
    caller = _caller_identity()
    if not caller:
        return None
    if session_id == caller.get("id"):
        return None
    if not caller.get("restrictToManaged"):
        return None
    if session_id in (caller.get("managed") or []):
        return None
    if claim and caller.get("canClaimUnmanaged"):
        # 先看目标 session：不存在 → 放行（让下游工具报 not found）
        target = _api("GET", f"/api/sessions/{session_id}")
        if not isinstance(target, dict) or target.get("error"):
            return None
        tmb = target.get("managedBy")
        if tmb and tmb != caller["id"]:
            return {"ok": False, "error": {
                "code": "permission_denied",
                "message": f"session {caller['id']} is restricted to its managed sessions; "
                           f"{session_id} is managed by {tmb}"}}
        # 未归属或已归属本 manager → claim（幂等）
        res = _api("POST", "/api/claim", {"managerId": caller["id"], "sessionId": session_id})
        if isinstance(res, dict) and res.get("ok"):
            return None
        msg = "claim refused"
        if isinstance(res, dict) and isinstance(res.get("error"), dict):
            msg = res["error"].get("message", msg)
        return {"ok": False, "error": {
            "code": "permission_denied",
            "message": f"session {caller['id']} is restricted to its managed sessions; "
                       f"{session_id}: {msg}"}}
    return {"ok": False, "error": {
        "code": "permission_denied",
        "message": f"session {caller['id']} is restricted to its managed sessions; "
                   f"{session_id} is not in its managed list"}}


def _auto_claim(session_id: str) -> None:
    """Auto-claim a newly created session for the caller when autoClaimCreated (best-effort)."""
    caller = _caller_identity()
    if caller and caller.get("autoClaimCreated") and session_id:
        _api("POST", "/api/claim", {"managerId": caller["id"], "sessionId": session_id})


def _worker_session_id(worker_id: str) -> str | None:
    """Resolve a worker_id to its session_id via /api/list (or None)."""
    result = _api("GET", "/api/list")
    if not isinstance(result, dict):
        return None
    for w in result.get("workers", []) if isinstance(result.get("workers"), list) else []:
        if w.get("workerId") == worker_id:
            return w.get("sessionId")
    return None


# ---------------------------------------------------------------------------
# Session management tools
# ---------------------------------------------------------------------------

@mcp.tool()
def session_create(
    name: str,
    adapter: str = "cbc",
    model: str | None = None,
    permission_mode: str | None = None,
    workdir: str | None = None,
) -> dict:
    """Create a new session (persistent conversation container).

    Args:
        name: Session name (unique, no spaces)
        adapter: CLI adapter to use ("cbc" or "kimi")
        model: AI model name (e.g. "hy3", "deepseek-v4-flash")
        permission_mode: Permission mode ("bypassPermissions", "acceptEdits", "default", "plan")
        workdir: Workdir name, resolved under data/workdirs/. Defaults to session name.

    调用链（编排主链第 1 步·创建）：返回的 `id` 即后续所有请求的 `session_id` 入参，
    记下它再 `worker_assign` 派发任务。workdir 默认 data/workdirs/<name>（Pan 外目录用绝对路径）。
    完整编排流程见 /pan skill。
    """
    body: dict = {"name": name, "adapter": adapter}
    if model:
        body["model"] = model
    if permission_mode:
        body["permissionMode"] = permission_mode
    if workdir:
        body["workdir"] = workdir
    result = _strip_usage(_api("POST", "/api/sessions", body))
    # meta-agent 创建的 session 自动归其管理（立项 4.2）
    if isinstance(result, dict) and result.get("id"):
        _auto_claim(result["id"])
    return result


@mcp.tool()
def session_list() -> dict:
    """List all sessions with their worker status.

    完整编排流程见 /pan skill。
    """
    return _strip_usage(_api("GET", "/api/sessions"))


@mcp.tool()
def session_get(session_id: str, limit: int = 0) -> dict:
    """Get full session details including history and last result.

    Args:
        session_id: Session ID (e.g. "ses_abc123def4567890")
        limit: Max history entries to return (0 = full history, default). When
            set, history is truncated to the latest `limit` entries and
            historyTruncated/historyTotal markers are added.

    调用链（编排主链第 3 步·查结果）：读 lastResult.status——"done" 取 result，
    "error" 读错误排查；完成后 `session_delete` 收尾。巡检用 limit 截断
    （如 limit=15），避免全量 history 撑爆工具输出。
    完整编排流程见 /pan skill。
    """
    denied = _check_access(session_id)
    if denied:
        return denied
    result = _api("GET", f"/api/sessions/{session_id}")
    if limit and "history" in result and isinstance(result["history"], list):
        history = result["history"]
        result = dict(result)
        result["history"] = history[-limit:]
        result["historyTruncated"] = len(history) > limit
        result["historyTotal"] = len(history)
    return _strip_usage(result)


@mcp.tool()
def session_delete(session_id: str) -> dict:
    """Delete a session and kill its worker if running.

    Args:
        session_id: Session ID to delete

    调用链（编排主链第 4 步·收尾）：删除 session 并 kill worker；
    workdir 磁盘目录残留，批量删除用 HTTP POST /api/sessions/batch-delete。
    完整编排流程见 /pan skill。
    """
    denied = _check_access(session_id)
    if denied:
        return denied
    return _api("DELETE", f"/api/sessions/{session_id}")


@mcp.tool()
def session_update(
    session_id: str,
    model: str | None = None,
    permission_mode: str | None = None,
    always_thinking_enabled: bool | None = None,
    effort: str | None = None,
    max_thinking_tokens: int | None = None,
    mcp_servers: list[str] | None = None,
    game_id: str | None = None,
) -> dict:
    """Update session-level settings without spawning a worker.

    Note: changing mcpServers makes the response include requireRestart: true —
    the worker must be respawned (worker_kill + worker_spawn) for the change
    to take effect.

    Args:
        session_id: Session ID
        model: AI model name (e.g. "hy3", "deepseek-v4-flash")
        permission_mode: Permission mode ("bypassPermissions", "acceptEdits", "default", "plan")
        always_thinking_enabled: Toggle extended thinking
        effort: Thinking effort (e.g. "low"/"medium"/"high")
        max_thinking_tokens: Max thinking tokens
        mcp_servers: MCP server names from the manifest (e.g. ["pan"]); 非空即
            启用 MCP，空列表/省略 = 无 MCP（单一事实源）
        game_id: RuleWhisper game binding; pass "" to clear

    完整编排流程见 /pan skill。
    """
    denied = _check_access(session_id)
    if denied:
        return denied
    body: dict = {}
    if model is not None:
        body["model"] = model
    if permission_mode is not None:
        body["permissionMode"] = permission_mode
    if always_thinking_enabled is not None:
        body["alwaysThinkingEnabled"] = always_thinking_enabled
    if effort is not None:
        body["effort"] = effort
    if max_thinking_tokens is not None:
        body["maxThinkingTokens"] = max_thinking_tokens
    if mcp_servers is not None:
        body["mcpServers"] = mcp_servers
    if game_id is not None:
        body["gameId"] = game_id or None
    return _strip_usage(_api("PATCH", f"/api/sessions/{session_id}", body))


@mcp.tool()
def session_history(session_id: str, limit: int = 50, before: int | None = None) -> dict:
    """Get paginated conversation history for a session.

    Args:
        session_id: Session ID
        limit: Max history entries to return (default 50)
        before: Only return entries before this index (for pagination)

    完整编排流程见 /pan skill。
    """
    denied = _check_access(session_id)
    if denied:
        return denied
    path = f"/api/sessions/{session_id}/history?limit={limit}"
    if before is not None:
        path += f"&before={before}"
    return _api("GET", path)


# ---------------------------------------------------------------------------
# Report subscription tools (立项 4.3 订阅制)
# ---------------------------------------------------------------------------

@mcp.tool()
def report_subscribe(session_id: str) -> dict:
    """Subscribe to completion reports for a managed session.

    Opt-in report delivery (立项 4.3): after subscribing, every time the
    managed session finishes a task (done/error) its report dict — aligned
    with handoff format: status/result/sessionId/taskId/workerId — is appended
    to this meta-agent's persisted queue_pending and delivered as one batched
    message to this session's worker (concatenated with a visible separator
    when multiple reports accumulate). Unsubscribed sessions only keep the
    existing worker.result broadcast for external coordinators.

    Args:
        session_id: Managed session ID to subscribe to reports for

    完整编排流程见 /pan skill。
    """
    manager_id = os.environ.get("PAN_AGENT_SESSION_ID")
    if not manager_id:
        return {"ok": False, "error": {
            "code": "missing_identity",
            "message": "PAN_AGENT_SESSION_ID not set — report tools only work inside a Pan-managed meta-agent session"}}
    # 订阅即接管：claim=True（目标未归属或已归属本 manager 才可订阅）
    denied = _check_access(session_id, claim=True)
    if denied:
        return denied
    return _api("POST", "/api/report-subscribe",
                {"managerId": manager_id, "sessionId": session_id})


@mcp.tool()
def report_unsubscribe(session_id: str) -> dict:
    """Unsubscribe from completion reports for a managed session.

    Stops report delivery for the session. Existing worker.result broadcasts
    are unaffected (they were never gated by subscription).

    Args:
        session_id: Managed session ID to unsubscribe from

    完整编排流程见 /pan skill。
    """
    manager_id = os.environ.get("PAN_AGENT_SESSION_ID")
    if not manager_id:
        return {"ok": False, "error": {
            "code": "missing_identity",
            "message": "PAN_AGENT_SESSION_ID not set — report tools only work inside a Pan-managed meta-agent session"}}
    # 退订只允许针对本 manager 已管理的 session
    denied = _check_access(session_id)
    if denied:
        return denied
    return _api("POST", "/api/report-unsubscribe",
                {"managerId": manager_id, "sessionId": session_id})


# ---------------------------------------------------------------------------
# Worker management tools
# ---------------------------------------------------------------------------

@mcp.tool()
def worker_spawn(session_id: str | None = None, name: str | None = None,
                 adapter: str = "cbc", model: str | None = None,
                 workdir: str | None = None) -> dict:
    """Spawn a worker (CLI process) for a session. Creates session if name given.

    Args:
        session_id: Existing session ID to spawn worker for
        name: Or create a new session with this name
        adapter: CLI adapter (default "cbc")
        model: Model override
        workdir: Workdir for the new session (only used when name is given)

    完整编排流程见 /pan skill。
    """
    body: dict = {"adapter": adapter}
    if session_id:
        # spawn 即接管：meta-agent 首次 spawn 现有 session 时自动建立 managed 关系
        denied = _check_access(session_id, claim=True)
        if denied:
            return denied
        body["sessionId"] = session_id
    if name:
        body["name"] = name
    if model:
        body["model"] = model
    if workdir:
        body["workdir"] = workdir
    result = _api("POST", "/api/spawn", body)
    # name 路径会新建 session → 自动归调用 meta-agent 管理
    if name and not session_id and isinstance(result, dict) and result.get("sessionId"):
        _auto_claim(result["sessionId"])
    return result


@mcp.tool()
def worker_task(session_id: str | None = None, worker_id: str | None = None,
                text: str = "", source: str = "agent") -> dict:
    """Send a task to a worker. Auto-spawns if session has no worker.

    Args:
        session_id: Session ID (finds worker by session if worker_id not given)
        worker_id: Worker ID (e.g. "worker-1")
        text: Task text / prompt to send
        source: Source label (default "agent")

    完整编排流程见 /pan skill。
    """
    if session_id:
        # 派任务即接管（与 worker_assign 一致）
        denied = _check_access(session_id, claim=True)
        if denied:
            return denied
    elif worker_id:
        sid = _worker_session_id(worker_id)
        if sid:
            denied = _check_access(sid, claim=True)
            if denied:
                return denied
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

    完整编排流程见 /pan skill。
    """
    sid = _worker_session_id(worker_id)
    if sid:
        denied = _check_access(sid)
        if denied:
            return denied
    return _api("POST", f"/api/kill/{worker_id}")


@mcp.tool()
def worker_list() -> dict:
    """List all running workers.

    完整编排流程见 /pan skill。
    """
    return _api("GET", "/api/list")


@mcp.tool()
def worker_handoff(session_id: str, text: str, timeout: float = 600.0,
                   task_id: str | None = None) -> dict:
    """[DEPRECATED] Send a task and BLOCK until the worker returns a result.

    DEPRECATED (立项 4.7): prefer ``worker_assign`` + report subscription
    (``report_subscribe``) instead. If you truly need to wait, "waiting" should
    be your session's default idle state — dispatch asynchronously with
    worker_assign, subscribe to the completion report, and consume it when it
    arrives — not a blocking call that leaves your session busy or subject to
    interruption. This tool is retained only for cases that require a strictly
    blocking synchronous return value; it may be removed in the future. No
    runtime warning is emitted (deprecation is documentation-level only).

    Synchronous orchestration primitive: ensures a worker exists for the
    session, sends the task, then waits for the worker.result event.
    Returns the final result dict. Use for serial dependent steps.

    Idempotency: pass the same `task_id` when retrying a timed-out handoff —
    the server won't re-enqueue if that taskId is already known (returns its
    status / existing result). Prevents double-execution of the same task.

    Args:
        session_id: Session ID to run the task on
        text: Task text / prompt
        timeout: Max seconds to wait for completion (default 600 / 10min)
        task_id: Optional caller-supplied idempotency key (uuid-like string)

    完整编排流程见 /pan skill。
    """
    denied = _check_access(session_id, claim=True)
    if denied:
        return denied
    body = {"sessionId": session_id, "text": text, "timeout": timeout}
    if task_id:
        body["taskId"] = task_id
    return _api("POST", "/api/handoff", body, timeout=timeout + 60)


@mcp.tool()
def worker_assign(session_id: str, text: str, task_id: str | None = None) -> dict:
    """Dispatch a task asynchronously and return immediately.

    Async orchestration primitive: returns {"status": "queued", ...}
    right away. Completion is delivered via the worker.result event —
    subscribe to /ws/agent with eventTypes=["worker.result"] to catch it.
    Use for parallel fan-out.

    Idempotency: pass the same `task_id` when retrying a task (e.g. after a
    timeout or network error) — the server won't re-enqueue if that taskId is
    already known. It returns the cached result if already completed, or
    {"status": "pending", "taskId": ...} if still in flight. Prevents
    double-execution of the same task.

    Args:
        session_id: Session ID to run the task on
        text: Task text / prompt
        task_id: Optional caller-supplied idempotency key (uuid-like string)

    调用链（编排主链第 2 步·派发）：立即返回 queued（worker 自动 spawn）；
    完成信号经 /ws/agent 的 worker.result 事件推送，或轮询 session_get 到
    lastResult.status=="done"。完成后 `session_delete` 收尾。
    完整编排流程见 /pan skill。
    """
    # 派任务即接管：meta-agent 首次 assign 目标 session 时自动建立 managed 关系
    denied = _check_access(session_id, claim=True)
    if denied:
        return denied
    body = {"sessionId": session_id, "text": text}
    if task_id:
        body["taskId"] = task_id
    return _api("POST", "/api/assign", body)


@mcp.tool()
def worker_send(worker_id: str, text: str) -> dict:
    """Send a message to an existing live worker (multi-turn collaboration).

    Completion is delivered via the worker.result event. If the worker
    is dead, returns an error (spawn it again first).

    When this MCP server runs inside a Pan-managed session (env injected by
    adapter.mcp_args() for the "pan" server), the text is prefixed with the
    sending agent's identity so the target worker can distinguish agent
    orchestration from real user messages (立项 4.8):

        ////by agent : {PAN_AGENT_SESSION_ID} | {PAN_AGENT_SESSION_TITLE}
        {text}

    Args:
        worker_id: Worker ID (e.g. "worker-1")
        text: Task text / prompt

    调用链：发送时自动拼接 ////by agent 前缀（来源标记，区分 MA 编排消息）。
    完整编排流程见 /pan skill。
    """
    sid = os.environ.get("PAN_AGENT_SESSION_ID")
    title = os.environ.get("PAN_AGENT_SESSION_TITLE")
    if sid or title:
        text = f"////by agent : {sid} | {title}\n{text}"
    # 向被管 session 的 worker 发消息即接管
    target_sid = _worker_session_id(worker_id)
    if target_sid:
        denied = _check_access(target_sid, claim=True)
        if denied:
            return denied
    return _api("POST", "/api/task", {"workerId": worker_id, "text": text})


# ---------------------------------------------------------------------------
# Model / adapter info
# ---------------------------------------------------------------------------

@mcp.tool()
def model_list(adapter: str = "cbc") -> dict:
    """List available AI models for an adapter.

    Args:
        adapter: Adapter name ("cbc" or "kimi")

    完整编排流程见 /pan skill。
    """
    return _api("GET", f"/api/models?adapter={adapter}")


# ---------------------------------------------------------------------------
# Pan handbook (single source of truth: docs/skills/pan/SKILL.md)
# ---------------------------------------------------------------------------

def _pan_skill_path() -> str:
    """Locate the pan skill handbook file (single source of truth).

    Main source: docs/skills/pan/SKILL.md at the project root (git versioned),
    resolved from the module location (packages/mcp/server.py → project root).
    Falls back to the .codebuddy copy (CodeBuddy editor loads skill from there),
    then to the current working directory. An explicit PAN_SKILL_PATH env
    override wins over all candidates.
    """
    if env := os.environ.get("PAN_SKILL_PATH"):
        return env
    project_root = Path(__file__).resolve().parent.parent.parent
    candidates = [
        project_root / "docs" / "skills" / "pan" / "SKILL.md",
        project_root / ".codebuddy" / "skills" / "pan" / "SKILL.md",
        Path(".codebuddy") / "skills" / "pan" / "SKILL.md",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return str(candidates[0])


@mcp.tool()
def pan_handbook() -> dict:
    """Return the full Pan orchestration handbook.

    Reads docs/skills/pan/SKILL.md (the single source of truth) live and
    returns its raw content — nothing is duplicated here. Covers the
    orchestration workflow, HTTP API cheat sheet, gotchas and conventions
    (workdir, watchdog, handoff idempotency, ////by agent prefix, ...).

    调用链：接线完成后若不清楚编排流程，先调本工具拿手册，再按主链
    session_create → worker_assign → session_get → session_delete 执行。
    完整编排流程见 /pan skill。
    """
    path = _pan_skill_path()
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return {"ok": False, "error": {
            "code": "skill_not_found",
            "message": f"pan SKILL.md not readable at {path}: {e}"}}
    return {"ok": True, "name": "pan", "path": path, "content": content}


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
