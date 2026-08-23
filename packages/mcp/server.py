"""Pan MCP Server — wraps Pan's HTTP API as MCP tools for agent consumption.

Usage:
    python -m packages.mcp.server                 # stdio (default)
    python -m packages.mcp.server --transport sse --port 9740   # SSE transport

Tools exposed:
    - session_create: Create a new session (optional workdir)
    - session_import: Import an external cbc/kimi session or list what's importable
    - session_list: List all sessions (optional lean summary mode)
    - session_managed: List the caller's managed sessions (summary)
    - session_get: Get session details (optional history limit)
    - session_update: Update session settings (model/effort/mcp etc.)
    - session_delete: Delete a session
    - session_batch_delete: Delete multiple sessions at once
    - session_claim: Claim a session for the calling agent (managed relationship)
    - session_claim_many: Batch-claim multiple sessions
    - session_unclaim: Unclaim a session from the calling agent
    - session_unclaim_many: Batch-unclaim multiple sessions
    - worker_spawn: Spawn a worker for a session
    - worker_task: Send a task to a worker
    - worker_kill: Kill a worker
    - worker_send_force: Force-push a message to a worker (restart + send, fallback when worker_send can't deliver)
    - session_history: Get paginated conversation history
    - model_list: List available AI models
    - report_subscribe: Subscribe to completion reports of a managed session
    - report_unsubscribe: Unsubscribe from completion reports of a managed session
    - session_qq_subscribe: Subscribe the calling session to a QQ chat's inbox reminders
    - session_qq_unsubscribe: Unsubscribe the calling session from a QQ chat's inbox reminders
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
from urllib.parse import urlencode

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

# Capability flags: canonical nested ``panAccess`` (camelCase keys) on the
# session API; legacy top-level keys are the fallback for older servers.
_PAN_ACCESS_API_FIELDS = (
    ("restrictToManaged", "restrictToManaged"),
    ("canClaimUnmanaged", "canClaimUnmanaged"),
    ("autoClaimCreated", "autoClaimCreated"),
)


def _caller_pan_access(caller: dict) -> dict:
    """Capability flags of a caller dict (nested ``panAccess``, default {}).

    ``_caller_identity`` normalizes the nested key in place, so this is just a
    defensive accessor for callers that may carry a raw / legacy-shaped dict.
    """
    pa = caller.get("panAccess")
    return pa if isinstance(pa, dict) else {}


def _caller_identity() -> dict | None:
    """Return the calling agent's session info (id/capabilities/managed) or None.

    Identity comes from PAN_AGENT_SESSION_ID (4.8 injection). Returns None when
    the env var is absent or the session can't be resolved — callers then run
    unrestricted (external coordinators, sessions without restriction).

    Capability flags live under ``panAccess`` (nested, F-schema). For servers
    that still emit the legacy top-level flags they are normalized here, so
    callers can always read them from ``panAccess``.
    """
    sid = os.environ.get("PAN_AGENT_SESSION_ID")
    if not sid:
        return None
    result = _api("GET", f"/api/sessions/{sid}")
    if not isinstance(result, dict) or result.get("error") or "id" not in result:
        return None
    result = dict(result)
    pa = result.get("panAccess")
    if not isinstance(pa, dict):
        # legacy server response: capability flags at top level
        pa = {}
        for pa_key, legacy_key in _PAN_ACCESS_API_FIELDS:
            if legacy_key in result:
                pa[pa_key] = result[legacy_key]
    result["panAccess"] = pa
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
    pa = _caller_pan_access(caller)
    if not pa.get("restrictToManaged"):
        return None
    if session_id in (caller.get("managed") or []):
        return None
    if claim and pa.get("canClaimUnmanaged"):
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
    if caller and _caller_pan_access(caller).get("autoClaimCreated") and session_id:
        _api("POST", "/api/claim", {"managerId": caller["id"], "sessionId": session_id})


def _reimport_precheck(cli_session_id: str) -> dict | None:
    """Deny reimport when a restricted caller would overwrite an unmanaged session.

    A reimport (same cli_session_id already present as a Pan session) overwrites
    that session in place. For callers restricted to managed sessions the
    target must be in the caller's managed list — otherwise refuse (§8.2).
    The target is located cheaply via GET /api/sessions?summary=1, which now
    carries cliSessionId. No match → pure new import → allowed.

    Returns None when allowed, or an error dict when denied.
    """
    caller = _caller_identity()
    if not caller:
        return None
    if not _caller_pan_access(caller).get("restrictToManaged"):
        return None
    result = _api("GET", "/api/sessions?summary=1")
    sessions = result.get("sessions") if isinstance(result, dict) else None
    if not isinstance(sessions, list):
        return None  # 无法枚举 → 交给后端 import-guard 兜底
    managed_ids = set(caller.get("managed") or [])
    for s in sessions:
        if (isinstance(s, dict)
                and s.get("cliSessionId") == cli_session_id
                and s.get("id") not in managed_ids):
            return {"ok": False, "error": {
                "code": "permission_denied",
                "message": f"session {caller['id']} is restricted to its managed sessions; "
                           f"reimport of cli_session_id {cli_session_id} would overwrite "
                           f"session {s.get('id')} which it does not manage"}}
    return None


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
    session_template: str | None = None,
    character_id: str | None = None,
    system_prompt: str | None = None,
    game_id: str | None = None,
    pan_access: dict | None = None,
) -> dict:
    """Create a new session (persistent conversation container).

    Args:
        name: Session name (unique, no spaces)
        adapter: CLI adapter to use ("cbc" or "kimi")
        model: AI model name (e.g. "hy3", "deepseek-v4-flash")
        permission_mode: Permission mode ("bypassPermissions", "acceptEdits", "default", "plan")
        workdir: Workdir name, resolved under data/workdirs/. Defaults to session name.
        session_template: Session template name from the manifest (e.g.
            "meta-agent"). The template supplies defaults for model /
            permission_mode / system_prompt / MCP / capability flags.
        character_id: Bind a character (memory/assets) to the session.
        system_prompt: Override the session system prompt.
        game_id: RuleWhisper game binding.
        pan_access: Capability flags dict, keys: restrictToManaged /
            canClaimUnmanaged / autoClaimCreated (all default False).

    Priority (explicit field > sessionTemplate template value > default):
    arguments explicitly passed here override the session_template's values,
    which in turn override built-in defaults (model / permission_mode also
    fall back to the adapter's config.json settings).

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
    if session_template:
        body["sessionTemplate"] = session_template
    if character_id:
        body["characterId"] = character_id
    if system_prompt:
        body["systemPrompt"] = system_prompt
    if game_id:
        body["gameId"] = game_id
    if pan_access:
        body["panAccess"] = pan_access
    result = _strip_usage(_api("POST", "/api/sessions", body))
    # meta-agent 创建的 session 自动归其管理（立项 4.2）
    if isinstance(result, dict) and result.get("id"):
        _auto_claim(result["id"])
    return result


@mcp.tool()
def session_import(
    action: str,
    adapter: str = "cbc",
    project_dir: str | None = None,
    cwd: str | None = None,
    query: str | None = None,
    limit: int = 30,
    session_id: str | None = None,
    name: str | None = None,
    session_template: str | None = None,
    pan_access: dict | None = None,
) -> dict:
    """Import an external cbc/kimi session into Pan, or list what's importable.

    Args:
        action: "list_projects" (cbc) / "list_workspaces" (kimi) /
            "list_sessions" / "import"
        adapter: Source adapter ("cbc" or "kimi")
        project_dir: cbc project dir name (from list_projects)
        cwd: Absolute path — kimi requires the workspace root; cbc accepts it
            in place of project_dir
        query: Title filter for cbc list_sessions
        limit: Pagination hint (backend caps at max_sessions_shown)
        session_id: External session id to import (required for action="import")
        name: Override imported session name
        session_template: Session template to apply (model / permission_mode /
            MCP / pan_access defaults; explicit fields still override template)
        pan_access: Capability flags {restrictToManaged, canClaimUnmanaged,
            autoClaimCreated}

    调用链（导入历史会话）：
    1. session_import(action="list_projects") 或 (action="list_workspaces") 发现可导入来源；
    2. session_import(action="list_sessions", project_dir=...) 按项目/工作区列出候选会话；
    3. session_import(action="import", session_id=..., project_dir=.../cwd=...,
       name?/session_template?/pan_access?) 导入成 Pan session —— 仅建 session 不
       spawn worker；workdir 为外部项目路径。同一 cli_session_id 重复导入 = reimport，
       覆盖原 Pan session 历史（受限 caller 只能覆盖自己管理的）。
    4. 接编排主链：report_subscribe（订阅完成报告）→ worker_assign（派发任务）→
       session_get（查结果）→ session_delete（收尾）。完整编排流程见 /pan skill。
    """
    if action not in ("list_projects", "list_workspaces", "list_sessions", "import"):
        return {"ok": False, "error": {
            "code": "invalid_action",
            "message": "action must be one of list_projects / list_workspaces / "
                       f"list_sessions / import, got {action!r}"}}

    if action == "list_projects":
        return _strip_usage(_api("GET", "/api/cbc/projects"))

    if action == "list_workspaces":
        return _strip_usage(_api("GET", "/api/kimi/workspaces"))

    if action == "list_sessions":
        qs: list[tuple[str, str]] = []
        if adapter == "cbc":
            if not project_dir and not cwd:
                return {"ok": False, "error": {
                    "code": "missing_params",
                    "message": "project_dir (or cwd) is required for cbc list_sessions"}}
            if project_dir:
                qs.append(("project_dir", project_dir))
            if cwd:
                qs.append(("cwd", cwd))
            if query:
                qs.append(("q", query))
            path = "/api/cbc/sessions"
        else:
            if not cwd:
                return {"ok": False, "error": {
                    "code": "missing_params",
                    "message": "cwd (workspace root) is required for kimi list_sessions"}}
            qs.append(("cwd", cwd))
            path = "/api/kimi/sessions"
        suffix = "?" + urlencode(qs) if qs else ""
        return _strip_usage(_api("GET", path + suffix))

    # action == "import"
    if not session_id:
        return {"ok": False, "error": {
            "code": "missing_params",
            "message": "session_id is required for import"}}
    if adapter == "kimi" and not cwd:
        return {"ok": False, "error": {
            "code": "missing_params",
            "message": "cwd (workspace root) is required for kimi import"}}

    # reimport 预检（§8.2）：受限 caller 只能覆盖自己管理的 session
    denied = _reimport_precheck(session_id)
    if denied:
        return denied

    body: dict = {"session_id": session_id}
    if adapter == "cbc":
        path = "/api/cbc/sessions/import"
        if project_dir:
            body["project_dir"] = project_dir
        if cwd:
            body["cwd"] = cwd
    else:
        path = "/api/kimi/sessions/import"
        if cwd:
            body["cwd"] = cwd
    if name:
        body["name"] = name
    if session_template:
        body["sessionTemplate"] = session_template
    if pan_access:
        body["panAccess"] = pan_access

    # 导入解析可能较慢（大 history），放宽 _api 超时
    result = _strip_usage(_api("POST", path, body, timeout=120.0))
    if isinstance(result, dict) and result.get("id"):
        history = result.pop("history", None)
        result["historyCount"] = len(history) if isinstance(history, list) else 0
        result["reimportedExisting"] = bool(result.pop("reimported", False))
        result["imported"] = True
        _auto_claim(result["id"])
    return result


@mcp.tool()
def session_list(summary: bool = False) -> list[dict] | dict:
    """List all sessions with their worker status.

    Args:
        summary: When True, return only lean fields
            [{id, name, adapter, cliSessionId, workerStatus, updatedAt,
            managedBy}] — no history/usage. Backed by the backend ?summary=1
            endpoint so the full payload is never transferred (context 预算友好).

    默认返回全量（含 history，最多 50 条截断）。summary=True 适合巡检/编排前的
    状态扫查；单 session 明细用 session_get。
    完整编排流程见 /pan skill。
    """
    if summary:
        result = _api("GET", "/api/sessions?summary=1")
        if isinstance(result, dict) and isinstance(result.get("sessions"), list):
            return result["sessions"]
        return result
    return _strip_usage(_api("GET", "/api/sessions"))


@mcp.tool()
def session_managed() -> list[dict] | dict:
    """List the calling session's managed sessions as a summary.

    Returns [{id, name, workerStatus, updatedAt}] for each session in the
    caller's managed list, resolved via _caller_identity and fetched through
    the backend ?summary=1 endpoint (then filtered by the caller's managed ids).

    Semantics:
    - No caller identity (PAN_AGENT_SESSION_ID unset or unresolvable) → error
      {"ok": false, "error": {code: "missing_identity", ...}} — can't determine
      who "the caller" is, so no managed list can be resolved.
    - Caller not restricted to managed (restrictToManaged false) → [] — there
      is no managed boundary to report; use session_list for the full view.
    - Otherwise → the managed sessions summary, or [] when the list is empty.

    完整编排流程见 /pan skill。
    """
    caller = _caller_identity()
    if not caller:
        return {"ok": False, "error": {
            "code": "missing_identity",
            "message": "PAN_AGENT_SESSION_ID not set or unresolvable — "
                       "session_managed requires a caller identity"}}
    if not caller.get("restrictToManaged"):
        return []
    managed_ids = caller.get("managed") or []
    if not managed_ids:
        return []
    result = _api("GET", "/api/sessions?summary=1")
    sessions = result.get("sessions") if isinstance(result, dict) else None
    if not isinstance(sessions, list):
        return result  # backend error passthrough
    by_id = {s.get("id"): s for s in sessions if isinstance(s, dict) and s.get("id")}
    return [by_id[sid] for sid in managed_ids if sid in by_id]


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
def session_batch_delete(session_ids: list[str]) -> dict:
    """Delete multiple sessions at once (kill workers, purge references).

    Maps to the existing backend POST /api/sessions/batch-delete. Each target
    session is access-checked (managed isolation) before any deletion; the
    backend also purges each deleted id from other sessions' report_subscriptions
    and managers' managed lists (B1).

    Args:
        session_ids: List of session IDs to delete (e.g. ["ses_a", "ses_b"])

    Returns:
        {"deleted": n} on success, or an error dict.

    批量收尾用：多个一次性会话一次删完（比逐个 session_delete 省轮次）。
    完整编排流程见 /pan skill。
    """
    if not session_ids:
        return {"ok": False, "error": {
            "code": "missing_params",
            "message": "session_ids is required"}}
    for sid in session_ids:
        denied = _check_access(sid)
        if denied:
            return denied
    return _api("POST", "/api/sessions/batch-delete", {"sessionIds": session_ids})


@mcp.tool()
def session_claim(session_id: str) -> dict:
    """Claim a session for the calling agent (establish managed relationship).

    Establishes manager.managed += [session_id] / session.managed_by =
    manager_id (立项 4.2). Claim 自动 report_subscribe：本 agent 会收到该
    session 的完成报告（后端已实现）。目标若已被其他 manager 认领则拒绝。

    Args:
        session_id: Session ID to claim

    调用链：走 POST /api/claim（带 _check_access(claim=True) 隔离检查：
    受限 caller 可自动 claim 无主/自己可认领的 session；不受限 caller 放行后
    工具显式调 POST /api/claim 返回统一结果，幂等）。claim 后即建立 managed
    关系并订阅完成报告，可直接 worker_assign 派活。完整编排流程见 /pan skill。
    """
    manager_id = os.environ.get("PAN_AGENT_SESSION_ID")
    if not manager_id:
        return {"ok": False, "error": {
            "code": "missing_identity",
            "message": "PAN_AGENT_SESSION_ID not set — claim tools only work inside a Pan-managed meta-agent session"}}
    denied = _check_access(session_id, claim=True)
    if denied:
        return denied
    return _api("POST", "/api/claim",
                {"managerId": manager_id, "sessionId": session_id})


@mcp.tool()
def session_claim_many(session_ids: list[str]) -> dict:
    """Batch-claim multiple sessions (per-item isolation, partial success).

    Each session is processed independently through session_claim — a single
    failure (permission denied / already managed by someone else / not found)
    doesn't block the rest.

    Args:
        session_ids: List of session IDs to claim (e.g. ["ses_a", "ses_b"])

    Returns:
        {"ok": True, "claimed": [...], "failed": [{"sessionId": ..., "error": ...}]}

    批量接管用：多个一次性会话一次性建立 managed 关系（比逐个 session_claim
    省轮次）。完整编排流程见 /pan skill。
    """
    if not session_ids:
        return {"ok": False, "error": {
            "code": "missing_params",
            "message": "session_ids is required"}}
    claimed: list[str] = []
    failed: list[dict] = []
    for sid in session_ids:
        result = session_claim(sid)
        if isinstance(result, dict) and result.get("ok"):
            claimed.append(sid)
        else:
            err = result.get("error") if isinstance(result, dict) else result
            failed.append({"sessionId": sid, "error": err})
    return {"ok": True, "claimed": claimed, "failed": failed}


@mcp.tool()
def session_unclaim(session_id: str) -> dict:
    """Release the calling agent's managed relationship with a session.

    Removes session from the caller's managed list and clears the session's
    managed_by (auto-unsubscribes completion reports, backend已实现). Only the
    current manager may unclaim — the backend validates that.

    Args:
        session_id: Session ID to unclaim

    调用链：走 POST /api/unclaim（带 _check_access 隔离检查：受限 caller 只能
    解除自己 managed 列表内的 session）。unclaim 不删除 session，仅解除管理。
    完整编排流程见 /pan skill。
    """
    manager_id = os.environ.get("PAN_AGENT_SESSION_ID")
    if not manager_id:
        return {"ok": False, "error": {
            "code": "missing_identity",
            "message": "PAN_AGENT_SESSION_ID not set — unclaim tools only work inside a Pan-managed meta-agent session"}}
    denied = _check_access(session_id)
    if denied:
        return denied
    return _api("POST", "/api/unclaim",
                {"managerId": manager_id, "sessionId": session_id})


@mcp.tool()
def session_unclaim_many(session_ids: list[str]) -> dict:
    """Batch-unclaim multiple sessions (per-item isolation, partial success).

    Each session is processed independently through session_unclaim — a single
    failure doesn't block the rest.

    Args:
        session_ids: List of session IDs to unclaim (e.g. ["ses_a", "ses_b"])

    Returns:
        {"ok": True, "unclaimed": [...], "failed": [{"sessionId": ..., "error": ...}]}

    批量收尾用：多个一次性会话一次解除 managed 关系（比逐个 session_unclaim
    省轮次）。完整编排流程见 /pan skill。
    """
    if not session_ids:
        return {"ok": False, "error": {
            "code": "missing_params",
            "message": "session_ids is required"}}
    unclaimed: list[str] = []
    failed: list[dict] = []
    for sid in session_ids:
        result = session_unclaim(sid)
        if isinstance(result, dict) and result.get("ok"):
            unclaimed.append(sid)
        else:
            err = result.get("error") if isinstance(result, dict) else result
            failed.append({"sessionId": sid, "error": err})
    return {"ok": True, "unclaimed": unclaimed, "failed": failed}


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
# QQ inbox subscription tools (inbox 更新提醒，镜像 report-subscribe 链路)
# ---------------------------------------------------------------------------

@mcp.tool()
def session_qq_subscribe(target_type: str, target_id: str) -> dict:
    """Subscribe the calling session to a QQ chat's inbox update reminders.

    After subscribing, every new message from that QQ chat (selective mode,
    delivered to inbox) pushes a `@@@@by qq` reminder to the calling session's
    queue_pending and wakes its worker.

    Args:
        target_type: "user" (私聊) or "group" (群聊) — only these two are
            accepted (backend validates)
        target_id: QQ 号 / 群号 (passed as str; the backend needs a string
            for its .strip() handling)

    调用链：走 POST /api/qq/subscribe（操作 caller 自己的 session，body
    sessionId = PAN_AGENT_SESSION_ID，无需 _check_access，但需环境变量）。
    完整编排流程见 /pan skill。
    """
    session_id = os.environ.get("PAN_AGENT_SESSION_ID")
    if not session_id:
        return {"ok": False, "error": {
            "code": "missing_identity",
            "message": "PAN_AGENT_SESSION_ID not set — qq subscribe tools only work inside a Pan-managed meta-agent session"}}
    return _api("POST", "/api/qq/subscribe",
                {"sessionId": session_id, "target_type": target_type,
                 "target_id": str(target_id)})


@mcp.tool()
def session_qq_unsubscribe(target_type: str, target_id: str) -> dict:
    """Unsubscribe the calling session from a QQ chat's inbox update reminders.

    Args:
        target_type: "user" (私聊) or "group" (群聊) — only these two are
            accepted (backend validates)
        target_id: QQ 号 / 群号 (passed as str; the backend needs a string
            for its .strip() handling)

    调用链：走 POST /api/qq/unsubscribe（操作 caller 自己的 session，body
    sessionId = PAN_AGENT_SESSION_ID，无需 _check_access，但需环境变量）。
    完整编排流程见 /pan skill。
    """
    session_id = os.environ.get("PAN_AGENT_SESSION_ID")
    if not session_id:
        return {"ok": False, "error": {
            "code": "missing_identity",
            "message": "PAN_AGENT_SESSION_ID not set — qq subscribe tools only work inside a Pan-managed meta-agent session"}}
    return _api("POST", "/api/qq/unsubscribe",
                {"sessionId": session_id, "target_type": target_type,
                 "target_id": str(target_id)})


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


@mcp.tool()
def worker_send_force(worker_id: str, text: str) -> dict:
    """Force-push a message to a worker: restart the worker, then send.

    强制推送 = restart + send：目标 worker 卡死 / 忙 / 连接异常导致普通
    worker_send 消息无法送达时的兜底。先调用 restart 端点终止并重新 spawn
    worker 进程，再发送消息，保证消息能送达。

    When this MCP server runs inside a Pan-managed session (env injected by
    adapter.mcp_args() for the "pan" server), the text is prefixed with the
    sending agent's identity — identical to ``worker_send``:

        ////by agent : {PAN_AGENT_SESSION_ID} | {PAN_AGENT_SESSION_TITLE}
        {text}

    Args:
        worker_id: Worker ID (e.g. "worker-1")
        text: Task text / prompt

    调用链：隔离检查（与 worker_send 一致）→ POST /api/worker/{worker_id}/restart
    → POST /api/task（自动拼接 ////by agent 前缀）。restart 或 send 任一失败
    均返回含后端 error 信息的错误 dict，不吞错。
    完整编排流程见 /pan skill。
    """
    sid = os.environ.get("PAN_AGENT_SESSION_ID")
    title = os.environ.get("PAN_AGENT_SESSION_TITLE")
    if sid or title:
        text = f"////by agent : {sid} | {title}\n{text}"
    # 向被管 session 的 worker 强制推送即接管（与 worker_send 一致）
    target_sid = _worker_session_id(worker_id)
    if target_sid:
        denied = _check_access(target_sid, claim=True)
        if denied:
            return denied
    # 1) 重启 worker 进程（失败直接返回后端错误）
    result = _api("POST", f"/api/worker/{worker_id}/restart")
    if not isinstance(result, dict) or result.get("error"):
        return result
    # 2) 发送消息（与 worker_send 相同）
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
