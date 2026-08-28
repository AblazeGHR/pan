"""Pan Web Channel — FastAPI routes + WebSocket + Dashboard."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, Response, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import httpx

from packages.core import worker
from packages.core import session as sess
from packages.core.adapters import get_adapter, list_adapters, get_sessions_provider
from packages.core.adapters.cbc import sessions as cbc_sessions
from packages.core.adapters.cbc.sessions import sanitize_project_dir_name
from packages.core.adapters.kimi import sessions as kimi_sessions
from packages.core.adapters.opencode import sessions as opencode_sessions
from packages.core.config import load_config, read_config_file, save_config
from packages.core.character import CharacterManager
from packages.core.manifest_loader import SessionTemplate

# ── logging ──

def _log(msg: str):
    """Print with HH:MM:SS prefix."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# Comma-separated path prefixes to skip in request logging.
# e.g. PAN_LOG_SKIP=/api/sessions,/ws
_LOG_SKIP = [
    p.strip()
    for p in os.environ.get("PAN_LOG_SKIP", "").split(",")
    if p.strip()
]


# ── lifespan ──


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load all saved sessions (don't auto-spawn Workers).
    Shutdown: kill all child processes."""
    sessions = sess.list_all()
    if sessions:
        _log(f"[Pan] Loaded {len(sessions)} sessions from disk")

    # 服务级 watchdog（立项 4.4）：生命周期=Pan 服务，周期扫描落盘队列
    # queue_pending 非空但没有活 worker 的 session，自动 spawn 恢复。
    worker.start_global_watchdog()
    
    # Init CharacterManager with manifest
    global _character_manager
    config = load_config()
    plugin_paths = config.get("plugin_manifests", ["manifest.json"])
    _character_manager = CharacterManager(str(DATA_DIR))
    try:
        _character_manager.load_manifest(plugin_paths)
        templates = _character_manager.list_session_templates()
        _log(f"[Pan] Loaded {len(templates)} session templates from manifest")
    except Exception as e:
        _log(f"[Pan] Character manifest not loaded: {e}")
    
    yield
    worker.stop_global_watchdog()
    await worker.shutdown_all()
    # Release cached MemoryManagers + loaded embedding models (#20).
    try:
        from packages.core.memory_context import clear_memory_manager_cache
        clear_memory_manager_cache()
    except Exception:
        pass
    _log("[Pan] All workers shut down")


_character_manager: CharacterManager | None = None


app = FastAPI(title="Pan", lifespan=lifespan)

ws_clients: set[WebSocket] = set()
agent_clients: set[WebSocket] = set()

# agent 视角的默认订阅：只推结果摘要，不推原始 stream（防 context 爆炸）
_AGENT_DEFAULT_SUBSCRIPTION = frozenset({"worker.result"})


@dataclass
class AgentSubscription:
    """单个 /ws/agent 连接的订阅状态。

    - event_types：订阅的事件类型（默认只 worker.result）
    - session_ids：关心的 session 集合；非空时 worker.result 只推给匹配
      workerId→sessionId 的连接的 session；空集 = 订阅所有 session
    - consumed_seq：每 session 已消费的 result 序号（taskSeq），重连补发用
    """
    event_types: set[str] = field(default_factory=lambda: set(_AGENT_DEFAULT_SUBSCRIPTION))
    session_ids: set[str] = field(default_factory=set)
    consumed_seq: dict[str, int] = field(default_factory=dict)


# 每个 /ws/agent 连接的订阅状态；未订阅默认只推 worker.result
agent_subscriptions: dict[WebSocket, AgentSubscription] = {}

# ── file paths (relative to packages/web/) ──
_WEB_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _WEB_DIR.parent.parent  # packages/web/ → packages/ → project root
DATA_DIR = _PROJECT_DIR / "data"
WORKDIRS_DIR = DATA_DIR / "workdirs"
DASHBOARD_FILE = _WEB_DIR / "index.html"
MOBILE_DASHBOARD_FILE = _WEB_DIR / "mobile.html"
REACT_DIST_DIR = _WEB_DIR / "dist"
REACT_DIST_EXISTS = REACT_DIST_DIR.is_dir()

# Production switch: config.json frontend 字段
# "coexist"（默认）→ React SPA / + Vanilla /vanilla/（+ React /react/ 兼容保留）
# "react" → React SPA / + Vanilla /vanilla/
# "legacy" → 仅旧前端 /（无 /vanilla、/react）
FRONTEND_MODE = load_config().get("frontend", "coexist")

_MOBILE_UA_RE = re.compile(
    r"Mobile|Android|iPhone|iPad|iPod|BlackBerry|Windows Phone|webOS",
    re.IGNORECASE,
)


async def _send_ws(ws: WebSocket, data: dict):
    """单个客户端发送（带 2s 超时）；超时/失败由 broadcast 统一剔除。

    慢客户端（TCP 缓冲满）2s 内不消费即断开，防止阻塞 broadcast → 卡死
    所有 _read_stdout / worker（实测 Edge 后台标签页）。
    """
    await asyncio.wait_for(ws.send_json(data), timeout=2)


async def broadcast(data: dict):
    """向 dashboard（ws_clients）+ agent（agent_clients）广播。

    A4 并行化：asyncio.gather 并发发送，慢客户端只拖自己的 2s 超时，不再串行
    拖累全部客户端（此前一个 TCP 缓冲满的客户端让整个 broadcast 卡 2s×N）。
    死连接在 gather 后统一剔除。
    """
    dead = set()
    clients = list(ws_clients)
    if clients:
        results = await asyncio.gather(
            *[_send_ws(ws, data) for ws in clients],
            return_exceptions=True,
        )
        for ws, exc in zip(clients, results):
            if exc is not None:
                dead.add(ws)
    ws_clients.difference_update(dead)

    etype = data.get("type", "")
    data_session_id = data.get("sessionId")
    targets: list[WebSocket] = []
    for ws in list(agent_clients):
        sub = agent_subscriptions.setdefault(ws, AgentSubscription())
        # 事件类型过滤
        if etype not in sub.event_types and "*" not in sub.event_types:
            continue
        # worker.result 按 sessionId 过滤（若订阅了特定 session 列表）
        if etype == "worker.result" and sub.session_ids and data_session_id not in sub.session_ids:
            continue
        targets.append(ws)
    dead_a = set()
    if targets:
        results = await asyncio.gather(
            *[_send_ws(ws, data) for ws in targets],
            return_exceptions=True,
        )
        for ws, exc in zip(targets, results):
            if exc is not None:
                dead_a.add(ws)
                continue
            # 记录已消费的 result 序号（重连补发用）——发送成功后才推进
            if etype == "worker.result" and data_session_id:
                seq = data.get("taskSeq")
                if isinstance(seq, int):
                    sub = agent_subscriptions.get(ws)
                    if sub is not None:
                        sub.consumed_seq[data_session_id] = max(sub.consumed_seq.get(data_session_id, 0), seq)
    agent_clients.difference_update(dead_a)


worker.set_broadcaster(broadcast)
worker.load_worker_config()
worker.load_memory_config()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every API request with method, path, and status code."""
    path = request.url.path
    response = await call_next(request)

    # Prevent browsers/CDNs from serving stale static assets
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"

    if not any(path.startswith(p) for p in _LOG_SKIP):
        status = response.status_code
        _log(f"{request.method}  {path}  → {status}")
    return response


@app.middleware("http")
async def no_cache_api(request: Request, call_next):
    """Prevent browser/CDN from caching API responses."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# ── helpers ──

def _session_to_api(s: sess.Session):
    """Convert Session to API response dict."""
    w = worker.find_worker_by_session(s.id)
    a = get_adapter(s.adapter)
    config = load_config().get(s.adapter, {})
    ac = s.adapter_config
    return {
        "id": s.id,
        "name": s.name,
        "adapter": s.adapter,
        "cliSessionId": s.cli_session_id,
        "model": s.model or config.get("model") or a.default_model,
        "permissionMode": s.permission_mode or config.get("permission_mode") or None,
        # Canonical nested capability object; the flat keys below are kept as
        # deprecated aliases so old HTTP consumers keep working.
        "panAccess": {
            "restrictToManaged": s.restrict_to_managed,
            "canClaimUnmanaged": s.can_claim_unmanaged,
            "autoClaimCreated": s.auto_claim_created,
        },
        "restrictToManaged": s.restrict_to_managed,
        "canClaimUnmanaged": s.can_claim_unmanaged,
        "autoClaimCreated": s.auto_claim_created,
        "sessionTemplate": s.session_template,
        "alwaysThinkingEnabled": ac.get("always_thinking_enabled", False),
        "effort": ac.get("effort") or config.get("effort", ""),
        "maxThinkingTokens": ac.get("max_thinking_tokens"),
        "workdir": s.workdir,
        "history": s.history,
        "lastResult": s.last_result,
        "rawUsage": s.raw_usage,
        "totalUsage": s.total_usage,
        "createdAt": s.created_at,
        "updatedAt": s.updated_at,
        "managed": s.managed,
        "managedBy": s.managed_by,
        "agentLevel": sess.agent_level(s.id),
        "reportSubscriptions": sorted(s.report_subscriptions),
        "qqSubscriptions": sorted(s.qq_subscriptions),
        "workerStatus": w.status if w else None,
        "workerId": w.worker_id if w else None,
        "mcpEnabled": bool(ac.get("mcp_servers")),
        "mcpLocked": _get_mcp_locked_state(s),
        # Currently-enabled MCP server names (extracted from the adapter_config
        # mcp_servers list of config dicts, each carrying a "name" key).
        "mcpServers": [
            c.get("name")
            for c in (ac.get("mcp_servers") or [])
            if isinstance(c, dict) and c.get("name")
        ],
        "outputMode": ac.get("output_mode"),
        "executionModes": list(a.execution_modes),
        "gameId": s.game_id,
    }


def _session_summary(s: sess.Session) -> dict:
    """Lean session dict for list summaries (A1: no history / usage).

    Fields: id/name/adapter/cliSessionId/workerStatus/updatedAt/managedBy/
    agentLevel —
    used by GET /api/sessions?summary=1 for agent context budgeting.
    cliSessionId lets MCP session_import locate the session that a reimport
    would overwrite (§8.2).

    Since 2026-08-23: also exposes lastMessage / historyTotal / totalUsage so
    the React sidebar can be driven entirely by summary=1 (no per-session
    history download for hidden sessions). lastMessage is the last history
    item's text truncated to 200 chars (no full message bodies).
    """
    w = worker.find_worker_by_session(s.id)
    a = get_adapter(s.adapter)
    config = load_config().get(s.adapter, {})
    ac = s.adapter_config
    last_text = ""
    if s.history:
        last = s.history[-1]
        if isinstance(last, dict):
            last_text = str(last.get("content") or "")[:200]
    return {
        "id": s.id,
        "name": s.name,
        "adapter": s.adapter,
        "cliSessionId": s.cli_session_id,
        "workerStatus": w.status if w else None,
        "updatedAt": s.updated_at,
        "managedBy": s.managed_by,
        "agentLevel": sess.agent_level(s.id),
        "lastMessage": last_text,
        "historyTotal": len(s.history),
        "totalUsage": s.total_usage,
        # 设置字段（供前端列表/InputRow 显示真实值，避免未打开设置弹窗时回退默认）
        "model": s.model or config.get("model") or a.default_model,
        "permissionMode": s.permission_mode or config.get("permission_mode") or None,
        "alwaysThinkingEnabled": ac.get("always_thinking_enabled", False),
        "effort": ac.get("effort") or config.get("effort", ""),
        "workdir": s.workdir,
    }


def _get_mcp_locked_state(s) -> bool | None:
    """Check if MCP toggle is locked for this session's session_template."""
    if _character_manager is None or not s.session_template:
        return None
    try:
        template = _character_manager.get_session_template(s.session_template)
        if template and template.mcp_mode in ("always", "never"):
            return True
    except Exception:
        pass
    return None


_NAME_RE = re.compile(r"^\S+$")  # session name: any non-whitespace chars
_MAX_NAME_LEN = 64
_MAX_TEXT_LEN = 10000


def _check_session_name(name: str) -> str | None:
    """Return error if name is empty, has invalid chars, too long, or taken."""
    if not name or not name.strip():
        return "Session name is required"
    if len(name) > _MAX_NAME_LEN:
        return f"Session name too long (max {_MAX_NAME_LEN})"
    if not _NAME_RE.match(name):
        return "Session name cannot contain spaces"
    for s in sess.list_all():
        if s.name == name:
            return f"Session name '{name}' already exists"
    return None


_WORKDIR_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

# ── file-system api constants ──
_HIDDEN_ENTRIES = {".git", ".venv", "node_modules", "__pycache__", ".codebuddy"}
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB

# Reserved for future path restriction
_ALLOWED_WORKDIR_ROOTS: list[Path] | None = None


def _resolve_workdir(workdir_name: str) -> Path:
    """Resolve a workdir name to a Path, creating it."""
    p = Path(workdir_name)
    if p.is_absolute():
        if _ALLOWED_WORKDIR_ROOTS is not None:
            for root in _ALLOWED_WORKDIR_ROOTS:
                try:
                    p.resolve().relative_to(root.resolve())
                    break
                except ValueError:
                    pass
            else:
                raise ValueError(
                    f"Workdir {workdir_name!r} is outside allowed roots: "
                    f"{[str(r) for r in _ALLOWED_WORKDIR_ROOTS]}"
                )
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Slug name — resolve under WORKDIRS_DIR
    # 非法字符不抛错：清理成安全 slug（替换为 -），避免合法 session 名
    # （如 "fanout.config"、"v1.2"）触发 500。
    if not _WORKDIR_NAME_RE.match(workdir_name):
        cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "-", workdir_name).strip("-")
        if not cleaned:
            cleaned = "session"
        workdir_name = cleaned
    workdir = WORKDIRS_DIR / workdir_name
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _resolve_fs_path(session_id: str, rel_path: str) -> Path:
    """Resolve a relative path within a session's workdir, rejecting escapes."""
    s = sess.get(session_id)
    if not s or not s.workdir:
        raise ValueError("session has no workdir")
    root = Path(s.workdir).resolve()
    target = (root / rel_path).resolve()
    # raises ValueError if rel_path (after resolving .. etc.) escapes root
    target.relative_to(root)
    return target


def _build_session_params(data: dict, *, resolve_workdir: bool = True) -> dict:
    """Extract session creation parameters from request data, with defaults.

    Session config (system_prompt / adapter / model / permission_mode /
    mcp_mode / mcp_servers / pan_access capability flags) comes from a
    session_template —
    either the explicit ``sessionTemplate`` name or the built-in ``default``
    template (config.json session config). ``characterId`` only binds memory/assets.

    ``resolve_workdir=False`` skips creating a workdir under data/workdirs/ —
    used by the import endpoints whose sessions keep the external project /
    workspace path as workdir instead.
    """
    adapter_name = data.get("adapter") or "cbc"
    a = get_adapter(adapter_name)
    config = load_config().get(adapter_name, {})
    name = data.get("name", "default")
    workdir_name = data.get("workdir") or name

    # Resolve session_template: explicit name → manifest template; else default.
    template_name = data.get("sessionTemplate") or data.get("session_template") or None
    template = None
    if template_name and _character_manager is not None:
        template = _character_manager.get_session_template(template_name)

    if template is None:
        # Built-in default session_template = config.json session config.
        # 默认 stream + MCP：注入 `pan` MCP server（输出模式自动走 stream+MCP）。
        template = SessionTemplate(
            name="default",
            adapter=adapter_name,
            # Same stale-model guard as the params below: only adopt the config
            # model when it's in the adapter's selectable list.
            model=(config.get("model")
                   if config.get("model") in a.supported_models else None)
            or a.default_model,
            permission_mode=config.get("permission_mode"),
            system_prompt="",
            mcp_mode="always",
            mcp_servers=["pan"],
        )
        template_name = None  # don't record an explicit name for the default

    # Capability flags: template values are the baseline; an explicit request
    # ``panAccess`` (or legacy flat body fields) overrides them.
    # Priority: explicit field > sessionTemplate template value > default.
    pan_access = {
        "restrict_to_managed": template.restrict_to_managed,
        "can_claim_unmanaged": template.can_claim_unmanaged,
        "auto_claim_created": template.auto_claim_created,
    }
    req_pa = data.get("panAccess")
    if isinstance(req_pa, dict):
        for req_key, pa_key in [
            ("restrictToManaged", "restrict_to_managed"),
            ("canClaimUnmanaged", "can_claim_unmanaged"),
            ("autoClaimCreated", "auto_claim_created"),
        ]:
            if req_key in req_pa:
                pan_access[pa_key] = bool(req_pa[req_key])
    for req_key, pa_key in [
        ("restrictToManaged", "restrict_to_managed"),
        ("canClaimUnmanaged", "can_claim_unmanaged"),
        ("autoClaimCreated", "auto_claim_created"),
    ]:
        if req_key in data:  # legacy flat body fields (backward compat)
            pan_access[pa_key] = bool(data[req_key])

    params = {
        "name": name,
        # User's explicit adapter wins, then the template's (a no-adapter
        # template parses to ""), else default "cbc". Fix: previously the user
        # selection was ignored — `template.adapter or "cbc"` overwrote a
        # chosen adapter (e.g. kimi) with "cbc" when the template had none.
        "adapter": data.get("adapter") or template.adapter or "cbc",
        # config.json may still hold a stale/invalid model (e.g. the old
        # kimi-code/kimi-for-coding); only adopt it when it's actually in the
        # adapter's selectable model list, else fall back to the adapter's
        # (already validated) default.
        "model": data.get("model") or template.model
        or (config.get("model") if config.get("model") in a.supported_models else None)
        or a.default_model,
        "permission_mode": data.get("permissionMode") or template.permission_mode or config.get("permission_mode") or None,
        "workdir": str(_resolve_workdir(workdir_name)) if resolve_workdir else "",
        "adapter_config": {
            "always_thinking_enabled": data.get("alwaysThinkingEnabled", config.get("always_thinking_enabled", False)),
            "effort": data.get("effort") or config.get("effort", ""),
            "max_thinking_tokens": data.get("maxThinkingTokens") or None,
        },
        "session_template": template_name,
        "pan_access": pan_access,
        "system_prompt": data.get("systemPrompt") or template.system_prompt,
        "game_id": data.get("gameId") or None,
    }
    # Optional worker execution mode ("stream" | "oneshot"); validated against
    # the adapter's execution_modes. Unset/"auto" = automatic (existing behaviour).
    raw_mode = data.get("outputMode")
    if raw_mode not in (None, "", "auto"):
        allowed = list(a.execution_modes)
        if raw_mode not in allowed:
            raise ValueError(
                f"outputMode must be one of {allowed} for adapter '{a.name}', got {raw_mode!r}"
            )
        params["adapter_config"]["output_mode"] = raw_mode

    # MCP servers come from the template (names → full configs).
    # mcp_mode decides injection: "always" injects; "optional"/"never" start
    # without servers (optional templates toggle via PATCH mcpServers).
    if template.mcp_mode == "always" and template.mcp_servers:
        try:
            params["adapter_config"]["mcp_servers"] = _resolve_mcp_server_configs(template.mcp_servers)
        except ValueError:
            # 默认 MCP server 未注册（manifest 未加载/缺失）→ 降级为无 MCP，
            # 不能因默认 MCP 缺失阻塞建 session。
            _log(f"默认 MCP server {template.mcp_servers} 未解析，降级为无 MCP")
            params["adapter_config"]["mcp_servers"] = []

    # Character binding: memory/assets only (no session config from character).
    character_id = data.get("characterId")
    if character_id and _character_manager is not None:
        char = _character_manager.get_character(character_id)
        if char is None:
            # characterId may be a character_template name (e.g. "coc-keeper")
            # rather than a persisted character id — instantiate on first use.
            try:
                char = _character_manager.create_character(character_id)
            except ValueError:
                char = None
        if char:
            params["character_id"] = char.id

    return params


def _resolve_mcp_server_configs(server_names) -> list[dict]:
    """Resolve MCP server names to full configs from the manifest table."""
    _ensure_manifest_fresh()
    if _character_manager is None or _character_manager._manifest_config is None:
        raise ValueError("MCP manifest not loaded")
    configs: list[dict] = []
    for name in server_names:
        for srv in _character_manager._manifest_config.mcp_servers:
            if srv.name == name:
                cfg: dict = {"name": srv.name}
                if srv.command:
                    cfg["command"] = srv.command
                if srv.args:
                    cfg["args"] = srv.args
                if srv.env:
                    cfg["env"] = srv.env
                if srv.cwd:
                    cfg["cwd"] = srv.cwd
                configs.append(cfg)
                break
        else:
            raise ValueError(f"Unknown MCP server: {name!r}")
    return configs


def _safe_adapter(adapter_name: str):
    """Return adapter by name, falling back to cbc on unknown names."""
    try:
        return get_adapter(adapter_name)
    except KeyError:
        return get_adapter("cbc")


# 进程相关字段：修改后影响 worker 进程（cbc 启动参数/执行模式），需要 respawn 才生效。
# 不含 name 等纯元数据字段。
_PROCESS_AFFECTING_FIELDS = {
    "model", "permissionMode", "alwaysThinkingEnabled", "effort",
    "maxThinkingTokens", "mcpServers", "outputMode",
}


def _apply_session_updates(s: sess.Session, data: dict):
    """Apply model/mode/thinking/effort fields from data to a Session (in-place)."""
    if "model" in data:
        s.model = data["model"]
    if "permissionMode" in data:
        s.permission_mode = data["permissionMode"] or None
    if "alwaysThinkingEnabled" in data:
        s.set_adapter_field("always_thinking_enabled", data["alwaysThinkingEnabled"])
    if "effort" in data:
        s.set_adapter_field("effort", data["effort"])
    if "maxThinkingTokens" in data:
        s.set_adapter_field("max_thinking_tokens", data["maxThinkingTokens"])
    if "mcpServers" in data:
        _apply_mcp_servers(s, data["mcpServers"])
    if "panAccess" in data:
        _apply_pan_access(s, data["panAccess"])
    if "outputMode" in data:
        _apply_output_mode(s, data["outputMode"])
    if "gameId" in data:
        # Allow None / empty to clear; store string otherwise. Used by QQ
        # plugin to bind a RuleWhisper game_id to a group-scoped session so
        # LLM-driven MCP tool calls can pass it through.
        s.game_id = data["gameId"] or None


_PAN_ACCESS_FIELDS = (
    ("restrictToManaged", "restrict_to_managed"),
    ("canClaimUnmanaged", "can_claim_unmanaged"),
    ("autoClaimCreated", "auto_claim_created"),
)


def _apply_pan_access(s: sess.Session, body) -> None:
    """Patch the session's capability flags from a camelCase ``panAccess`` dict.

    Only keys present in ``body`` are touched — a partial dict leaves the other
    flags alone (no whole-object overwrite, no back-filling of missing keys).
    """
    if not isinstance(body, dict):
        raise ValueError("panAccess must be an object of capability flags")
    for req_key, pa_key in _PAN_ACCESS_FIELDS:
        if req_key in body:
            s.pan_access[pa_key] = bool(body[req_key])


def _apply_mcp_servers(s: sess.Session, server_names) -> None:
    """Set session mcp_servers by manifest server names (e.g. ["pan"]).

    Resolves names to full configs via the character manager's manifest table.
    Accepts a list of names, or None/[] to clear. mcp_servers 非空即启用
    （单一事实源），mcp_mode 的 always/never 锁在此处强制执行。
    """
    enabling = server_names not in (None, [], "")
    if s.session_template and _character_manager is not None:
        template = _character_manager.get_session_template(s.session_template)
        if template:
            if template.mcp_mode == "always" and not enabling:
                raise ValueError(f"MCP is locked to 'always' for session template '{template.name}'. Cannot disable.")
            if template.mcp_mode == "never" and enabling:
                raise ValueError(f"MCP is locked to 'never' for session template '{template.name}'. Cannot enable.")

    if not enabling:
        s.set_adapter_field("mcp_servers", [])
        return
    if not isinstance(server_names, list):
        raise ValueError("mcpServers must be a list of server names")
    s.set_adapter_field("mcp_servers", _resolve_mcp_server_configs(server_names))


_VALID_OUTPUT_MODES = ("stream", "oneshot")


def _apply_output_mode(s: sess.Session, mode):
    """Apply outputMode: worker execution channel ("stream" | "oneshot").

    - "stream": long-running stream-json process; if MCP is also enabled,
      the process is spawned with --mcp-config (stream + MCP, cbc >= 2.137.0).
    - "oneshot": per-task one-shot cbc process (legacy MCP path).
    - None/""/"auto" clears the field -> automatic (按 adapter 默认解析，
      见 packages/core/adapters/resolution.py:resolve_execution_mode)。

    校验：mode 必须 ∈ 该 adapter 的 execution_modes，否则拒绝（避免"不可能"的
    配置，如给 one-shot-only adapter 设 stream）。adapter-architecture P1 建议 4。
    """
    if mode in (None, "", "auto"):
        s.adapter_config.pop("output_mode", None)
        return
    a = get_adapter(s.adapter)
    allowed = list(getattr(a, "execution_modes", _VALID_OUTPUT_MODES))
    if mode not in allowed:
        raise ValueError(
            f"outputMode must be one of {allowed} for adapter '{a.name}', got {mode!r}"
        )
    s.set_adapter_field("output_mode", mode)


def _open_terminal(cmd: str, cwd: str | Path) -> int:
    """Open a new terminal window running `cmd` in `cwd` (cross-platform)."""
    cwd = str(cwd) if cwd else str(Path.cwd())
    if sys.platform == "win32":
        # Strip terminal-capability vars inherited from a POSIX parent (e.g.
        # Git Bash sets TERM=xterm-256color). Left in place, cbc's ink TUI
        # mis-detects the console as a Unix terminal and renders black & white.
        env = dict(os.environ)
        env.pop("TERM", None)
        env.pop("NO_COLOR", None)
        env.pop("COLORTERM", None)
        proc = subprocess.Popen(
            ["powershell.exe", "-NoExit", "-Command", cmd],
            cwd=cwd,
            env=env,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        return proc.pid
    elif sys.platform == "darwin":
        script = f'tell app "Terminal" to do script "{cmd}"'
        proc = subprocess.Popen(["osascript", "-e", script], cwd=cwd)
        return proc.pid
    else:
        try:
            proc = subprocess.Popen(
                ["gnome-terminal", "--", "bash", "-c", cmd],
                cwd=cwd,
            )
            return proc.pid
        except FileNotFoundError:
            proc = subprocess.Popen(["xterm", "-e", cmd], cwd=cwd)
            return proc.pid


# ── Dashboard & favicon ──

from packages.core import __version__

@app.get("/api/health")
async def health():
    """Health check: process liveness + Pan version."""
    return {"status": "ok", "version": __version__}


@app.get("/favicon.ico")
async def favicon():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#58a6ff"/><text x="16" y="22" font-size="18" text-anchor="middle" fill="#fff" font-family="monospace" font-weight="bold">P</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # react / coexist → redirect root to /react/ so the React SPA basename
    # ("/react") matches the URL path and actually renders. Serving index.html
    # directly at "/" left the router with a non-matching basename → blank.
    if FRONTEND_MODE in ("react", "coexist") and REACT_DIST_EXISTS:
        return RedirectResponse("/react/", status_code=307)

    # legacy 模式（或 dist 缺失）→ Vanilla，保留移动端分流
    ua = request.headers.get("user-agent", "")
    if _MOBILE_UA_RE.search(ua):
        return HTMLResponse(
            content=MOBILE_DASHBOARD_FILE.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache"},
        )
    return HTMLResponse(
        content=DASHBOARD_FILE.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache"},
    )


# Vanilla 前端入口：coexist / react 模式下旧前端移至 /vanilla。
# legacy 模式根路径即旧前端，无需 /vanilla。
if FRONTEND_MODE != "legacy":

    @app.get("/vanilla", response_class=HTMLResponse)
    async def vanilla_dashboard(request: Request):
        """Serve legacy Vanilla frontend (with mobile UA split) at /vanilla."""
        ua = request.headers.get("user-agent", "")
        if _MOBILE_UA_RE.search(ua):
            return HTMLResponse(
                content=MOBILE_DASHBOARD_FILE.read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-cache"},
            )
        return HTMLResponse(
            content=DASHBOARD_FILE.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache"},
        )


# ── WebSocket: Dashboard ──

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")
            if msg_type == "user_inject":
                session_id = msg.get("sessionId")
                text = msg.get("text")
                if session_id and text:
                    w = worker.find_worker_by_session(session_id)
                    if w:
                        err = await worker.send_task(w.worker_id, text, source="user")
                        if err:
                            await broadcast({"type": "error", "message": err})
                    else:
                        # auto-spawn worker for this session
                        result = await worker.create_worker(session_id)
                        if isinstance(result, str):
                            await broadcast({"type": "error", "message": result})
                        else:
                            err = await worker.send_task(result.worker_id, text, source="user")
                            if err:
                                await broadcast({"type": "error", "message": err})
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)


# ── WebSocket: Main Agent ──

@app.websocket("/ws/agent")
async def ws_agent_endpoint(ws: WebSocket):
    await ws.accept()
    agent_clients.add(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "subscribe":
                # 订阅：{"type":"subscribe","eventTypes":[...],"sessionIds":[...]}
                # - eventTypes: "*" 订阅全部；[] 或省略 → 默认（仅 worker.result）
                # - sessionIds: 关心的 session 列表；省略 → 订阅所有 session 的 result
                sub = agent_subscriptions.get(ws)
                if sub is None:
                    sub = AgentSubscription()
                    agent_subscriptions[ws] = sub
                raw_types = msg.get("eventTypes")
                if raw_types is not None:
                    if not isinstance(raw_types, list):
                        await ws.send_json({"type": "error", "message": "eventTypes must be a list"})
                        continue
                    types = set(str(t) for t in raw_types)
                    sub.event_types = types if types else set(_AGENT_DEFAULT_SUBSCRIPTION)
                raw_sids = msg.get("sessionIds")
                if raw_sids is not None:
                    if not isinstance(raw_sids, list):
                        await ws.send_json({"type": "error", "message": "sessionIds must be a list"})
                        continue
                    sub.session_ids = set(str(s) for s in raw_sids)
                await ws.send_json({
                    "type": "subscribed",
                    "eventTypes": sorted(sub.event_types),
                    "sessionIds": sorted(sub.session_ids),
                })

            elif msg_type == "reconnect":
                # 断线重连补发：{"type":"reconnect","sessionIds":[...]}
                # 补发各 session 未消费的 worker.result（seq 大于已消费游标）
                sub = agent_subscriptions.get(ws)
                if sub is None:
                    sub = AgentSubscription()
                    agent_subscriptions[ws] = sub
                for sid in (msg.get("sessionIds") or []):
                    s = sess.get(sid)
                    if not s or not s.last_result or s.last_result.get("status") != "done":
                        continue
                    # 补发条件：consumed_seq < latest_seq（中途断线、部分消费也补发）
                    latest_seq = s.last_result.get("taskSeq")
                    if latest_seq is None:
                        # 旧数据未存 taskSeq：仅当完全未消费时补发（保持原有行为）
                        if sub.consumed_seq.get(sid, 0) > 0:
                            continue
                        latest_seq = 0
                    elif sub.consumed_seq.get(sid, 0) >= latest_seq:
                        continue
                    await ws.send_json({
                        "type": "worker.result",
                        "workerId": "",
                        "sessionId": sid,
                        "status": s.last_result.get("status"),
                        "result": s.last_result.get("result"),
                        "taskSeq": latest_seq,
                        "replayed": True,
                    })
                    # 补发成功后再推进游标，避免下次 reconnect 重复补发
                    sub.consumed_seq[sid] = max(sub.consumed_seq.get(sid, 0), latest_seq)

            elif msg_type == "task":
                session_id = msg.get("sessionId")
                text = msg.get("text")
                if session_id and text:
                    w = worker.find_worker_by_session(session_id)
                    if not w:
                        result = await worker.create_worker(session_id)
                        if isinstance(result, str):
                            await ws.send_json({"type": "error", "message": result})
                        else:
                            w = result
                    err = await worker.send_task(w.worker_id, text, source="agent")
                    if err:
                        await ws.send_json({"type": "error", "message": err})

            elif msg_type == "spawn":
                params = _build_session_params(msg)
                s = sess.create(**params)
                result = await worker.create_worker(s.id)
                if isinstance(result, str):
                    await ws.send_json({"type": "error", "message": result})
                else:
                    await ws.send_json({
                        "type": "worker.spawned",
                        "sessionId": s.id,
                        "workerId": result.worker_id,
                        "name": s.name,
                        "status": result.status,
                        "model": s.model,
                    })

            elif msg_type == "assign":
                session_id = msg.get("sessionId")
                text = msg.get("text")
                if not session_id or not text:
                    await ws.send_json({"type": "error", "message": "sessionId and text required"})
                    continue
                result = await worker.assign(session_id, text, source="agent")
                await ws.send_json({"type": "assign.result", **result})

            elif msg_type == "send":
                worker_id = msg.get("workerId")
                text = msg.get("text")
                if not worker_id or not text:
                    await ws.send_json({"type": "error", "message": "workerId and text required"})
                    continue
                result = await worker.send(worker_id, text, source="agent")
                await ws.send_json({"type": "send.result", **result})

            elif msg_type == "kill":
                session_id = msg.get("sessionId") or msg.get("workerId")
                w = worker.find_worker_by_session(session_id)
                if w:
                    err = await worker.kill_worker(w.worker_id)
                    if err:
                        await ws.send_json({"type": "error", "message": err})

            elif msg_type == "list":
                sessions = sess.list_all()
                await ws.send_json({
                    "type": "session.list",
                    "sessions": [_session_to_api(s) for s in sessions],
                })

    except WebSocketDisconnect:
        pass
    finally:
        agent_clients.discard(ws)
        agent_subscriptions.pop(ws, None)


# ── Session API ──

@app.get("/api/sessions")
async def api_list_sessions(summary: int = 0):
    """List all sessions (includes worker status if active).

    summary=1 → lean payload [{id, name, adapter, workerStatus, updatedAt,
    managedBy}] without history/usage (供 MCP session_list(summary=true) 等
    轻量巡检，避免全量传输再过滤). Default stays the full payload for
    backward compatibility.
    """
    sessions = sess.list_all()
    if summary:
        return {"sessions": [_session_summary(s) for s in sessions]}
    result = []
    for s in sessions:
        api = _session_to_api(s)
        full_history = api.get("history") or []
        api["history"] = full_history[-50:] if len(full_history) > 50 else full_history
        api["historyTruncated"] = len(full_history) > 50
        api["historyTotal"] = len(full_history)
        result.append(api)
    return {"sessions": result}


@app.post("/api/sessions")
async def api_create_session(data: dict):
    """Create a new Session (no worker spawned)."""
    try:
        params = _build_session_params(data)
    except ValueError as e:
        return {"error": str(e)}
    name = params["name"]
    err = _check_session_name(name)
    if err:
        return {"error": err}

    s = sess.create(**params)
    await broadcast({
        "type": "session.created",
        "sessionId": s.id,
        "name": s.name,
    })
    return _session_to_api(s)


@app.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str):
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    return _session_to_api(s)


@app.get("/api/sessions/{session_id}/managers")
async def api_session_managers(session_id: str):
    """Manager chain of a session, topmost first (level 1 = top).

    Walks the managedBy chain upward from the session. Each entry carries
    {level, id, name, workerStatus, lastResultStatus}:
    - workerStatus: live worker status (None = no worker spawned)
    - lastResultStatus: status of the session's last completed task
      ("done"/"error"/...; None = never ran a task)

    Edge cases:
    - Dangling managedBy (manager deleted): the chain stops there — the
      dangling id is not included (no info available about it).
    - Cycles: a visited-set stops the walk on a repeated id.
    - Unknown session_id → {"error": "Session not found"}.
    - Session with no manager → {"managers": []}.
    """
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    chain: list[sess.Session] = []
    seen = {session_id}
    cur = s
    while True:
        mb = cur.managed_by
        if not mb or mb in seen:
            break
        manager = sess.get(mb)
        if manager is None:
            break  # dangling reference → chain ends here
        seen.add(mb)
        chain.append(manager)
        cur = manager
    managers = []
    for i, m in enumerate(reversed(chain)):  # topmost first
        w = worker.find_worker_by_session(m.id)
        last_status = (m.last_result or {}).get("status") \
            if isinstance(m.last_result, dict) else None
        managers.append({
            "level": i + 1,  # level 1 = topmost manager
            "id": m.id,
            "name": m.name,
            "workerStatus": w.status if w else None,
            "lastResultStatus": last_status,
        })
    return {"ok": True, "sessionId": session_id, "managers": managers}


@app.get("/api/sessions/{session_id}/history")
async def api_session_history(session_id: str, before: int = 0, limit: int = 50):
    """Paginated session history for lazy-loading older messages."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    total = len(s.history)
    if before <= 0:
        before = total
    start = max(0, before - limit)
    page = s.history[start:before]
    return {
        "history": page,
        "total": total,
        "hasMore": start > 0,
        "start": start,
    }


@app.patch("/api/sessions/{session_id}")
async def api_update_session(session_id: str, data: dict):
    """Update session-level settings without spawning a worker."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    require_restart = False
    old_mcp_servers = s.adapter_config.get("mcp_servers")
    old_output_mode = s.adapter_config.get("output_mode")
    try:
        _apply_session_updates(s, data)
    except ValueError as e:
        return {"error": str(e)}
    new_mcp_servers = s.adapter_config.get("mcp_servers")
    new_output_mode = s.adapter_config.get("output_mode")
    # MCP servers 增删或执行模式切换都需要重启 worker 才生效
    require_restart = (old_mcp_servers != new_mcp_servers
                       or old_output_mode != new_output_mode)
    sess.save(s)
    await broadcast({
        "type": "session.updated",
        "sessionId": s.id,
    })
    result = _session_to_api(s)
    # 进程相关字段变更（model/effort/thinking/MCP 等）：idle worker 立即
    # respawn 让新配置生效；running worker 标记 pending_restart，回 idle 时
    # 自动 respawn（worker._maybe_restart_pending）。
    if any(k in data for k in _PROCESS_AFFECTING_FIELDS):
        w = worker.find_worker_by_session(session_id)
        if w:
            if w.status == "idle" and w.process is not None:
                asyncio.create_task(worker._respawn_worker(w))
            else:
                w.pending_restart = True
            require_restart = True
    if require_restart:
        result["requireRestart"] = True
    return result


@app.post("/api/sessions/{session_id}/rename")
async def api_rename_session(session_id: str, data: dict):
    """Rename a session by its internal ID (no worker required)."""
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return {"error": "name is required"}

    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}

    if s.name == new_name:
        return {"sessionId": s.id, "name": new_name, "status": "unchanged"}

    err = _check_session_name(new_name)
    if err:
        return {"error": err}

    old_name = s.name
    s.name = new_name
    sess.save(s)

    # G7: 把重命名持久化进 adapter 原生存储（按 provider 统一调用，P0-2）。
    # kimi/opencode 显式回写 state.json / SQLite；cbc 追加 custom-title 事件
    # （与 fork 时的写标题路径一致），三者均幂等安全。
    if s.cli_session_id:
        provider = _sessions_provider(s.adapter)
        if provider is not None:
            try:
                provider.write_custom_title(
                    s.cli_session_id, new_name, s.workdir or None,
                    kimi_home=s.adapter_config.get("kimi_home_dir"),
                )
            except Exception as e:
                print(f"[rename] {s.adapter} write_custom_title failed: {e}")

    await broadcast({
        "type": "session.renamed",
        "sessionId": s.id,
        "oldName": old_name,
        "newName": new_name,
    })
    return {"sessionId": s.id, "name": new_name, "status": "renamed"}


@app.post("/api/sessions/{session_id}/branch")
async def api_branch_session(session_id: str, data: dict):
    """Branch from a session — copy adapter-specific transcript, import new session, preserve settings.

    Dispatches by adapter because each backend stores sessions differently
    (cbc: JSONL under a project dir; kimi: wire.jsonl under ~/.kimi-code/sessions).
    """
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    if not s.cli_session_id:
        return {"error": "Session has no CLI session ID — cannot branch"}

    name = (data.get("name") or "").strip()
    if not name:
        name = f"{s.name}-branch"

    err = _check_session_name(name)
    if err:
        return {"error": err}

    # Fork via pure file operations — no CLI process spawned.
    # 按 provider 统一调用（P0-2）：cbc=复制 JSONL、kimi=目录复制、
    # opencode=SQLite 行复制，各自 fork_session 内部实现。
    cwd = s.workdir or ""
    provider = _sessions_provider(s.adapter)
    if provider is None:
        return {"error": f"Unknown adapter: {s.adapter}"}
    try:
        new_cli_id = provider.fork_session(s.cli_session_id, name, cwd or None)
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Fork failed: {e}"}

    try:
        history = provider.parse_history(new_cli_id, cwd or None)
        raw_usage_entries = provider.get_raw_usage(new_cli_id, cwd or None)
    except Exception as e:
        return {"error": f"Failed to parse forked session: {e}"}

    raw_usage = sess.accumulate_raw_usage(None, raw_usage_entries)
    total_usage = sess.compute_total_usage(raw_usage)

    # Preserve MCP binding from parent so branched session inherits
    # character identity + MCP servers (needed for --resume to work with tools).
    new_adapter_config = {
        "always_thinking_enabled": s.adapter_config.get("always_thinking_enabled", False),
        "effort": s.adapter_config.get("effort", ""),
        "max_thinking_tokens": s.adapter_config.get("max_thinking_tokens"),
    }
    if s.adapter_config.get("mcp_servers"):
        new_adapter_config["mcp_servers"] = s.adapter_config["mcp_servers"]

    new_s = sess.create(
        name=name,
        adapter=s.adapter,
        cli_session_id=new_cli_id,
        model=s.model,
        permission_mode=s.permission_mode,
        always_thinking_enabled=s.adapter_config.get("always_thinking_enabled", False),
        effort=s.adapter_config.get("effort", ""),
        max_thinking_tokens=s.adapter_config.get("max_thinking_tokens"),
        raw_usage=raw_usage,
        total_usage=total_usage,
        workdir=s.workdir,
        history=history,
        character_id=s.character_id,
        system_prompt=s.system_prompt,
        adapter_config=new_adapter_config,
        pan_access=dict(s.pan_access),
    )

    await broadcast({
        "type": "session.created",
        "sessionId": new_s.id,
        "name": new_s.name,
    })

    return _session_to_api(new_s)


@app.post("/api/sessions/{session_id}/handoff")
async def api_session_handoff(session_id: str, data: dict):
    """替身交接（session_handoff v1）：创建孪生 session B 接替 A。

    Body: {"handoffPrompt": <必填，A 的 agent 编写的交接简报>,
           "copySettings": bool (默认 true), "adapter"?, "model"?,
           "permissionMode"?}

    行为见 ``sess.handoff_session``：关系网接替 + B 自动 manage A + 可选设置
    复制（不含 system_prompt）+ B.system_prompt = handoffPrompt 与 A 原
    system_prompt 拼接 + 重命名（A → "(archive) <原名>"，B → "<原名>"）。
    """
    handoff_prompt = (data.get("handoffPrompt") or "").strip()
    if not handoff_prompt:
        return {"error": "handoffPrompt is required — 由 session A 的 agent 编写交接简报"}
    copy_settings = data.get("copySettings", True)
    if not isinstance(copy_settings, bool):
        copy_settings = True
    adapter = data.get("adapter")
    model = data.get("model")
    permission_mode = data.get("permissionMode")
    if not copy_settings and not adapter:
        return {"error": "adapter is required when copySettings is false"}
    result = sess.handoff_session(
        session_id, handoff_prompt,
        copy_settings=copy_settings, adapter=adapter, model=model,
        permission_mode=permission_mode,
    )
    if isinstance(result, str):
        return {"error": result}
    a, b = result
    await broadcast({
        "type": "session.renamed",
        "sessionId": a.id,
        "oldName": b.name,  # A 原名 == B 现名
        "newName": a.name,
    })
    await broadcast({
        "type": "session.created",
        "sessionId": b.id,
        "name": b.name,
    })
    return {
        "ok": True,
        "archivedSession": _session_to_api(a),
        "session": _session_to_api(b),
    }


def _cleanup_mcp_config(session_id: str) -> None:
    """S3：session 删除后清理 data/mcp-configs/<session_id>.mcp.json。

    cbc 运行期间持续读取该文件（mcp-configs 生命周期立项），但 session
    删除后不再需要，避免残留。文件不存在时静默跳过。
    """
    p = DATA_DIR / "mcp-configs" / f"{session_id}.mcp.json"
    try:
        p.unlink(missing_ok=True)
    except OSError as e:
        _log(f"[mcp-config] 清理失败 {p}: {e}")


def _cleanup_kimi_home(session_id: str) -> None:
    """S3：session 删除后清理 data/kimi-homes/<session_id>/ 隔离 HOME（方案 C）。

    kimi MCP 会话的整个用户目录（config.toml + mcp.json + kimi 自身写入的
    sessions/、session_index.jsonl 等）都在该隔离目录内，删除 session 时一并
    清理，避免 data/ 残留。目录不存在时静默跳过。
    """
    p = DATA_DIR / "kimi-homes" / session_id
    try:
        if p.exists():
            shutil.rmtree(p)
    except OSError as e:
        _log(f"[kimi-home] 清理失败 {p}: {e}")


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    """Delete a session and its worker if running."""
    sess.release(session_id)  # 清理 managed 关系 + 各 manager 的 report 订阅残留（B1）
    w = worker.find_worker_by_session(session_id)
    if w:
        asyncio.create_task(
            worker.cleanup_worker_background(w.worker_id, w.session_id)
        )
    sess.delete(session_id)
    _cleanup_mcp_config(session_id)
    _cleanup_kimi_home(session_id)
    await broadcast({
        "type": "session.deleted",
        "sessionId": session_id,
    })
    return {"sessionId": session_id, "status": "deleted"}


@app.post("/api/sessions/batch-delete")
async def api_batch_delete_sessions(data: dict):
    """Delete multiple sessions and their workers at once."""
    session_ids = data.get("sessionIds", [])
    if not session_ids:
        return {"error": "sessionIds is required"}

    deleted = 0
    for sid in session_ids:
        sess.release(sid)  # 清理 managed 关系 + 各 manager 的 report 订阅残留（B1）
        w = worker.find_worker_by_session(sid)
        if w:
            asyncio.create_task(
                worker.cleanup_worker_background(w.worker_id, w.session_id)
            )
        sess.delete(sid)
        _cleanup_mcp_config(sid)
        _cleanup_kimi_home(sid)
        deleted += 1

    await broadcast({
        "type": "sessions.deleted",
        "sessionIds": session_ids,
    })

    return {"deleted": deleted}


@app.get("/api/models")
async def api_models(adapter: str = "cbc"):
    """Return model list and default for a given adapter."""
    a = _safe_adapter(adapter)
    return {"models": a.supported_models, "default": a.default_model}


@app.get("/api/adapter/config")
async def api_adapter_config(adapter: str = "cbc"):
    """Return adapter configuration for frontend selects (per-adapter dynamic)."""
    a = _safe_adapter(adapter)
    return {
        "adapter": a.name,
        "models": a.supported_models,
        "defaultModel": a.default_model,
        "effortValues": list(a.effort_values),
        "permissionModes": a.permission_modes,
        "defaultPermissionMode": a.default_permission_mode,
        "supportedSettings": getattr(a, "supported_settings", ["model", "permissionMode", "thinking", "effort"]),
        "executionModes": list(a.execution_modes),
    }


@app.get("/api/adapters")
async def api_list_adapters():
    """Return all registered adapter names and basic info."""
    adapters = list_adapters()
    return {
        "adapters": [
            {
                "name": a.name,
                "defaultModel": a.default_model,
                "supportsResume": a.supports_resume,
                "supportsFork": a.supports_fork,
            }
            for a in adapters
        ],
        "default": "cbc",
    }


# ── App settings (config.json ui) ──

@app.get("/api/settings/ui")
async def api_get_settings_ui():
    """Return the app-settings ``ui`` object (merged with defaults).

    Backs the React ``appSettingsStore``. config.json's ``ui`` object is the
    single source of truth for these display-level settings, shared across
    browsers/sessions (previously they lived in browser localStorage).
    """
    return load_config().get("ui") or {}


@app.put("/api/settings/ui")
async def api_put_settings_ui(data: dict):
    """Merge partial/full ui settings into config.json's ``ui`` object.

    Body may carry any subset of the ui fields; provided keys are merged over
    the current values (defaults fill the rest) and persisted atomically.
    Unknown keys are stored as-is for forward compatibility. Returns the full
    merged ui object.
    """
    ui = dict(load_config().get("ui") or {})
    ui.update(data)
    raw = read_config_file()
    raw["ui"] = ui
    save_config(raw)
    return ui


# ── Config hot-reload ──

@app.post("/api/config/reload")
async def api_config_reload(data: dict | None = None):
    """Force a config.json hot-reload for adapter model caches + worker config.

    config.json is re-read from disk on every load_config() call, but two
    things are read once and then cached: the adapters' class-level model-list
    caches (codex/opencode/cbc with TTL, kimi/claude permanent) and worker.py's
    module-level lifecycle timeouts. This endpoint invalidates both so edits
    to config.json take effect without a server restart — same style as
    POST /api/manifest/reload.

    Body (optional): ``{"scope": "adapters" | "worker" | "all"}`` — default
    "all". Idempotent: repeated calls just re-read the same config. Per-item
    failures are collected into ``errors`` and reported with
    ``reloaded: false`` instead of a 500.
    """
    scope = (data or {}).get("scope") or "all"
    if scope not in ("adapters", "worker", "all"):
        return {"reloaded": False, "error": f"Unknown scope: {scope}"}

    result: dict = {"reloaded": True}
    errors: list[str] = []

    if scope in ("adapters", "all"):
        adapters_out = []
        for a in list_adapters():
            entry: dict = {"name": a.name}
            try:
                entry["modelsBefore"] = len(a.supported_models)
            except Exception as e:
                entry["modelsBefore"] = None
                errors.append(f"{a.name}: read before: {e}")
            invalidate = getattr(a, "invalidate_models_cache", None)
            if callable(invalidate):
                try:
                    invalidate()
                except Exception as e:
                    errors.append(f"{a.name}: invalidate: {e}")
            try:
                entry["modelsAfter"] = len(a.supported_models)
            except Exception as e:
                entry["modelsAfter"] = None
                errors.append(f"{a.name}: read after: {e}")
            adapters_out.append(entry)
        result["adapters"] = adapters_out

    if scope in ("worker", "all"):
        try:
            result["worker"] = worker.reload_worker_config()
        except Exception as e:
            errors.append(f"worker: {e}")

    if errors:
        result["reloaded"] = False
        result["errors"] = errors
    return result


# ── Spawn ──

@app.post("/api/spawn")
async def api_spawn(data: dict):
    """Spawn a Worker for a Session."""
    session_id = data.get("sessionId")
    if session_id:
        s = sess.get(session_id)
        if not s:
            return {"error": f"Session {session_id} not found"}
        # kill existing worker (avoid multiple workers on same session)
        existing = worker.find_worker_by_session(session_id)
        if existing:
            await worker.kill_worker(existing.worker_id)
        try:
            _apply_session_updates(s, data)
        except ValueError as e:
            return {"error": str(e)}
        sess.save(s)
    else:
        params = _build_session_params(data)
        name = params["name"]
        err_name = _check_session_name(name)
        if err_name:
            return {"error": err_name}
        s = sess.create(**params)
        session_id = s.id
        await broadcast({
            "type": "session.created",
            "sessionId": s.id,
            "name": s.name,
        })

    result = await worker.create_worker(session_id)
    if isinstance(result, str):
        return {"error": result}

    w = result
    return {
        "workerId": w.worker_id,
        "sessionId": session_id,
        "name": s.name,
        "status": w.status,
        "model": s.model or get_adapter(s.adapter).default_model,
    }


@app.post("/api/task")
async def api_task(data: dict):
    """Send a task to a Worker by worker_id or session_id."""
    worker_id = data.get("workerId")
    session_id = data.get("sessionId")

    if not worker_id and session_id:
        w = worker.find_worker_by_session(session_id)
        if w:
            worker_id = w.worker_id

    if not worker_id and session_id:
        s = sess.get(session_id)
        if not s:
            return {"error": f"Session {session_id} not found"}
        result = await worker.create_worker(session_id)
        if isinstance(result, str):
            return {"error": f"Worker auto-spawn failed: {result}"}
        worker_id = result.worker_id
        await broadcast({
            "type": "worker.spawned",
            "workerId": worker_id,
            "sessionId": session_id,
            "name": s.name,
            "status": "idle",
            "model": s.model or get_adapter(s.adapter).default_model,
            "reason": "auto-spawned by /api/task",
        })

    if not worker_id:
        return {"error": "workerId or sessionId required"}

    text = data.get("text")
    if not text:
        return {"error": "text is required"}

    err = await worker.send_task(worker_id, text, source="agent")
    if err:
        if session_id and err in ("Worker not found", "Worker process dead"):
            old = worker.find_worker_by_session(session_id)
            if old:
                await worker.kill_worker(old.worker_id)
            s = sess.get(session_id)
            if s:
                result = await worker.create_worker(session_id)
                if not isinstance(result, str):
                    worker_id = result.worker_id
                    err = await worker.send_task(worker_id, text, source="agent")
        if err:
            return {"error": err}

    w = worker.get_worker(worker_id)
    return {
        "workerId": worker_id,
        "sessionId": w.session_id if w else session_id,
        "status": "queued",
    }


@app.post("/api/send")
async def api_send(data: dict):
    """向 session（agent）发消息（阶段 6 寻址兼容层）。

    workerId / sessionId 皆可寻址（编排对象是 agent，进程是顺带的）。
    sessionId 无活 worker 时**不报错**：消息入 Session.queue_pending
    （type=task），由全局 watchdog spawn 后经 _recover_pending_signals
    分发。force=true 时对活 worker 先 restart 再投递（worker_send_force 语义）。
    隔离由 MCP 层实施（与 /api/claim 同约定），本端点不检查 pan_access。
    """
    worker_id = data.get("workerId")
    session_id = data.get("sessionId")
    text = data.get("text")
    if not text:
        return {"error": "text is required"}
    if not worker_id and not session_id:
        return {"error": "workerId or sessionId required"}
    if worker_id and not session_id:
        w = worker.get_worker(worker_id)
        if not w:
            return {"error": "Worker not found"}
        session_id = w.session_id
    result = await worker.send_session(session_id, text,
                                       source=data.get("source", "agent"),
                                       force=bool(data.get("force")))
    if isinstance(result, dict) and result.get("status") == "error":
        return {"error": result.get("result") or "send failed"}
    return result


@app.post("/api/assign")
async def api_assign(data: dict):
    """异步分派：发任务后立即返回 queued，完成时通过 worker.result 事件回调。

    taskId 可选：带 taskId 时走幂等语义（同 taskId 重发不双跑），见 worker.assign。
    """
    session_id = data.get("sessionId")
    text = data.get("text")
    if not session_id or not text:
        return {"ok": False, "error": {"code": "missing_params",
                                       "message": "sessionId and text are required"}}
    task_id = data.get("taskId")
    return await worker.assign(session_id, text, source="agent", task_id=task_id)


@app.post("/api/report-subscribe")
async def api_report_subscribe(data: dict):
    """meta-agent 订阅某个 managed session 的完成报告（立项 4.3 订阅制）。

    Body: {"managerId": <meta-agent session id>, "sessionId": <managed session id>}

    订阅后，该 managed session 每次完成（done/error）都会把报告 append 到
    meta-agent 的落盘队列 queue_pending（报告形状 {status,result,sessionId,taskId,workerId}）。
    未订阅则只保留现有 worker.result 广播。
    """
    manager_id = (data.get("managerId") or "").strip()
    session_id = (data.get("sessionId") or "").strip()
    if not manager_id or not session_id:
        return {"error": "managerId and sessionId are required"}
    # 禁止自订阅：meta-agent 不能订阅自己（自管理/自订阅无意义且会自我唤醒）
    if manager_id == session_id:
        return {"error": f"Cannot subscribe to itself ({session_id})"}
    manager = sess.get(manager_id)
    if not manager:
        return {"error": f"Manager session {manager_id} not found"}
    target = sess.get(session_id)
    if not target:
        return {"error": f"Session {session_id} not found"}
    # 软约束：已有归属且不属于该 manager → 拒绝（防止越权订阅）
    if target.managed_by and target.managed_by != manager_id:
        return {"error": f"Session {session_id} is managed by {target.managed_by}, not {manager_id}"}
    # 订阅即接管：建立 managed 关系（双向落盘，立项 4.2）。
    # claim 内部已自动 report_subscribe；此处 add 幂等，保留作防御性兜底。
    sess.claim(manager_id, session_id)
    manager.report_subscriptions.add(session_id)
    sess.save(manager)
    return {
        "managerId": manager_id,
        "sessionId": session_id,
        "subscribed": True,
        "reportSubscriptions": sorted(manager.report_subscriptions),
    }


@app.post("/api/report-unsubscribe")
async def api_report_unsubscribe(data: dict):
    """取消订阅 managed session 的完成报告（立项 4.3）。"""
    manager_id = (data.get("managerId") or "").strip()
    session_id = (data.get("sessionId") or "").strip()
    if not manager_id or not session_id:
        return {"error": "managerId and sessionId are required"}
    manager = sess.get(manager_id)
    if not manager:
        return {"error": f"Manager session {manager_id} not found"}
    manager.report_subscriptions.discard(session_id)
    sess.save(manager)
    return {
        "managerId": manager_id,
        "sessionId": session_id,
        "subscribed": session_id in manager.report_subscriptions,
        "reportSubscriptions": sorted(manager.report_subscriptions),
    }


# ── QQ session 绑定（订阅 inbox 更新提醒，镜像 report-subscribe 链路）──


@app.post("/api/qq/subscribe")
async def api_qq_subscribe(data: dict):
    """Pan session 订阅某 QQ 会话的 inbox 更新提醒（镜像 report-subscribe）。

    Body: {"sessionId": <pan session id>, "target_type": "user"|"group",
           "target_id": <QQ 号 / 群号>}

    订阅后，该 QQ 会话每次收到新消息（selective 模式入 inbox）都会推送一条
    `@@@@by qq` 提醒到本 session 的落盘队列 queue_pending 并唤醒其 worker。
    """
    session_id = (data.get("sessionId") or "").strip()
    target_type = (data.get("target_type") or "").strip().lower()
    target_id = (data.get("target_id") or "").strip()
    if not session_id or not target_id or target_type not in ("user", "group"):
        return {"error": "sessionId, target_type(user|group) and target_id are required"}
    s = sess.get(session_id)
    if not s:
        return {"error": f"Session {session_id} not found"}
    target_key = f"{target_type}:{target_id}"
    s.qq_subscriptions.add(target_key)
    sess.save(s)
    return {
        "sessionId": session_id,
        "qqTarget": target_key,
        "subscribed": True,
        "qqSubscriptions": sorted(s.qq_subscriptions),
    }


@app.post("/api/qq/unsubscribe")
async def api_qq_unsubscribe(data: dict):
    """取消订阅某 QQ 会话的 inbox 更新提醒。"""
    session_id = (data.get("sessionId") or "").strip()
    target_type = (data.get("target_type") or "").strip().lower()
    target_id = (data.get("target_id") or "").strip()
    if not session_id or not target_id or target_type not in ("user", "group"):
        return {"error": "sessionId, target_type(user|group) and target_id are required"}
    s = sess.get(session_id)
    if not s:
        return {"error": f"Session {session_id} not found"}
    target_key = f"{target_type}:{target_id}"
    s.qq_subscriptions.discard(target_key)
    sess.save(s)
    return {
        "sessionId": session_id,
        "qqTarget": target_key,
        "subscribed": target_key in s.qq_subscriptions,
        "qqSubscriptions": sorted(s.qq_subscriptions),
    }


@app.post("/api/qq/notify")
async def api_qq_notify(data: dict):
    """QQ 插件上报 inbox 更新（由 packages/qq/plugin.py 调用）。

    Body: {"target_type": "user"|"group", "target_id": ..., "nickname": ...,
           "text": ..., "time": ...}

    找到所有订阅该 QQ 会话的 session，各推送一条 `@@@@by qq` 提醒到其
    queue_pending 并唤醒 worker。返回投递数量。
    """
    target_type = (data.get("target_type") or "").strip().lower()
    target_id = (data.get("target_id") or "").strip()
    if not target_id or target_type not in ("user", "group"):
        return {"error": "target_type(user|group) and target_id are required"}
    delivered = await worker.enqueue_qq_reminder(
        target_type,
        target_id,
        nickname=(data.get("nickname") or ""),
        text=(data.get("text") or ""),
        time_str=(data.get("time") or ""),
    )
    return {"ok": True, "delivered": delivered}


@app.get("/api/qq/contacts")
async def api_qq_contacts():
    """列出最近的 QQ 联系人/群（代理到 QQ 插件 recent_contacts）。

    postbox 弹窗需要可选 QQ 会话列表。QQ 插件（packages/qq/plugin.py）在独立
    端口（默认 8080，PAN_QQ_API_URL 可覆盖）暴露 GET /api/qq/recent_contacts，
    此处代理转发，让前端统一走 Pan Core 同源 /api，避免跨域。
    """
    plugin_url = os.environ.get("PAN_QQ_API_URL", "http://127.0.0.1:8080").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{plugin_url}/api/qq/recent_contacts")
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": {
            "code": e.response.status_code,
            "message": e.response.text[:300]}}
    except httpx.HTTPError as e:
        return {"ok": False, "error": {
            "code": "connection_error",
            "message": f"{type(e).__name__}: {e}"}}


@app.post("/api/claim")
async def api_claim(data: dict):
    """建立 managed 关系（双向落盘，立项 4.2）。

    Body: {"managerId": <manager session id>, "sessionId": <managed session id>}

    效果：manager.managed += [sessionId]，session.managed_by = managerId。
    约束：session 若已有其他 manager 则拒绝。can_claim_unmanaged 能力限制仅由
    MCP 层（packages/mcp/server.py _check_access）实施；本端点（web/前端途径）
    不检查 pan_access——前端 manage 调整拥有最高权限。
    """
    manager_id = (data.get("managerId") or "").strip()
    session_id = (data.get("sessionId") or "").strip()
    if not manager_id or not session_id:
        return {"ok": False, "error": {
            "code": "missing_params",
            "message": "managerId and sessionId are required"}}
    manager = sess.get(manager_id)
    if not manager:
        return {"ok": False, "error": {
            "code": "manager_not_found",
            "message": f"Manager session {manager_id} not found"}}
    # 不检查 manager.can_claim_unmanaged：pan_access 限制只由 MCP 层实施，
    # web/前端途径拥有最高权限（前端 manage 调整不受限）。
    # claim 已自动 report_subscribe（manager.report_subscriptions 加入 session_id）
    err = sess.claim(manager_id, session_id)
    if err:
        return {"ok": False, "error": {
            "code": "claim_failed",
            "message": err}}
    return {
        "ok": True,
        "managerId": manager_id,
        "sessionId": session_id,
        "managed": list(manager.managed),
        "reportSubscriptions": sorted(manager.report_subscriptions),
    }


@app.post("/api/unclaim")
async def api_unclaim(data: dict):
    """解除 managed 关系（管理方主动解绑，不删除 session）。

    Body: {"managerId": <manager session id>, "sessionId": <managed session id>}

    效果：manager.managed 移除 sessionId，session.managed_by 置空，同时清理
    manager.report_subscriptions 对该 session 的订阅（解除管理即退订报告）。
    """
    manager_id = (data.get("managerId") or "").strip()
    session_id = (data.get("sessionId") or "").strip()
    if not manager_id or not session_id:
        return {"ok": False, "error": {
            "code": "missing_params",
            "message": "managerId and sessionId are required"}}
    err = sess.unclaim(manager_id, session_id)
    if err:
        return {"ok": False, "error": {
            "code": "unclaim_failed",
            "message": err}}
    manager = sess.get(manager_id)
    return {
        "ok": True,
        "managerId": manager_id,
        "sessionId": session_id,
        "managed": list(manager.managed) if manager else [],
    }


@app.post("/api/kill/{worker_id}")
async def api_kill(worker_id: str):
    """Kill a Worker process. Does NOT delete the Session."""
    err = await worker.kill_worker(worker_id)
    if err:
        return {"error": err}
    return {"workerId": worker_id, "status": "killed"}


@app.get("/api/list")
async def api_list():
    """List running workers."""
    return {
        "workers": [
            {
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "status": w.status,
            }
            for w in worker.list_workers()
        ]
    }


# ── cbc Session Import ──

@app.get("/api/cbc/projects")
async def api_cbc_projects():
    config = load_config()
    ci = config.get("cbc_import", {})
    recent_days = ci.get("import_recent_days", 30)
    min_resume_bytes = ci.get("min_resume_bytes", 200)
    projects = cbc_sessions.list_cbc_projects(
        recent_days=recent_days, min_resume_bytes=min_resume_bytes
    )
    return {"projects": projects}




@app.get("/api/cbc/sessions")
async def api_cbc_sessions(project_dir: str = "", cwd: str = "", all: int = 0):
    """List external cbc sessions available for import."""
    config = load_config()
    filter_cfg = config.get("cbc_import", {})

    if project_dir:
        all_sessions = cbc_sessions.list_cbc_sessions(project_dir=project_dir)
    else:
        cwd = cwd or str(Path.cwd())
        all_sessions = cbc_sessions.list_cbc_sessions(cwd)

    if all:
        return {"sessions": all_sessions, "total": len(all_sessions)}

    exclude_patterns = filter_cfg.get("exclude_workdir_patterns", [])
    target_dir = None
    if filter_cfg.get("project_dir_exact_match", False):
        target_dir = sanitize_project_dir_name(cwd)

    filtered: list[dict] = []
    for s in all_sessions:
        if target_dir and s["project_dir"].lower() != target_dir:
            continue
        if not target_dir and any(p in s["project_dir"] for p in exclude_patterns):
            continue
        if s["message_count"] < filter_cfg.get("min_message_count", 5):
            continue
        filtered.append(s)

    filtered.sort(key=lambda x: x.get("last_timestamp", ""), reverse=True)

    max_shown = filter_cfg.get("max_sessions_shown", 30)
    total = len(filtered)
    filtered = filtered[:max_shown]

    return {
        "sessions": filtered,
        "total": total,
        "shown": len(filtered),
    }


@app.get("/api/cbc/browse")
async def api_cbc_browse(path: str = "", limit: int = 30, offset: int = 0, q: str = ""):
    """Browse cbc sessions as a file-tree."""
    result = cbc_sessions.browse_cbc_tree(
        path=path, limit=limit, offset=offset, query=q,
    )
    return result


@app.post("/api/cbc/sessions/import")
async def api_cbc_sessions_import(data: dict):
    """Import a cbc session into Pan (Session only, no worker spawned)."""
    return await _import_session(cbc_sessions, "cbc", data)


# ── Kimi import ──

@app.get("/api/kimi/workspaces")
async def api_kimi_workspaces():
    """List Kimi workspaces that have sessions."""
    from packages.core.adapters.kimi import sessions as kimi_sessions
    return {"workspaces": kimi_sessions.list_kimi_workspaces()}


@app.get("/api/kimi/sessions")
async def api_kimi_sessions(cwd: str = ""):
    """List Kimi sessions for a workspace."""
    from packages.core.adapters.kimi import sessions as kimi_sessions
    return {"sessions": kimi_sessions.list_kimi_sessions_for_cwd(cwd)}


@app.post("/api/kimi/sessions/import")
async def api_kimi_sessions_import(data: dict):
    """Import a Kimi session into Pan (Session only, no worker spawned)."""
    return await _import_session(kimi_sessions, "kimi", data)


# ── OpenCode import ──

@app.get("/api/opencode/sessions")
async def api_opencode_sessions(cwd: str = ""):
    """List OpenCode sessions available for import (read from SQLite DB)."""
    sessions = opencode_sessions.list_opencode_sessions(cwd or None)
    return {"sessions": sessions, "total": len(sessions)}


@app.post("/api/opencode/sessions/import")
async def api_opencode_sessions_import(data: dict):
    """Import an OpenCode session into Pan (Session only, no worker spawned)."""
    return await _import_session(opencode_sessions, "opencode", data)


# ── Generic adapter sessions (P0-2: SessionsProvider 统一分派) ──
# 每新增一个 adapter，只要其在 adapters/__init__.py 注册了 sessions provider
# （模块，提供 SessionsProvider 协议函数），以下通用路由即自动覆盖，server.py
# 无需再写 import/branch/rename 分派。旧 /api/cbc|kimi|opencode/* 端点保留为
# 薄包装（前端仍在用，见 app.ts / api.ts）。


def _sessions_provider(adapter: str):
    """按 adapter 名取 sessions provider；未注册返回 None。"""
    try:
        return get_sessions_provider(adapter)
    except KeyError:
        return None


async def _import_session(provider, adapter: str, data: dict) -> dict:
    """Import an adapter-native session into Pan（Session only，不 spawn worker）。

    三个旧 import 端点（cbc/kimi/opencode）与通用 /api/adapters/{adapter}/sessions/import
    共用此实现。行为差异由 provider 能力位承载：
    - ``session_exists(session_id, cwd)``：可选；存在则启用 import-guard
      （cbc/opencode 提供，kimi 不提供 → 保持旧的无 guard 行为）。
    - ``project_dir_to_path(project_dir)``：可选；cbc 独有，把 cbc 项目目录名
      解析回真实路径（旧 /api/cbc/sessions/import 契约）。
    """
    session_id = data.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    cwd = data.get("cwd") or data.get("workdir") or str(Path.cwd())

    # cbc 兼容：project_dir 直接给出且未显式传 cwd 时，解析为真实路径。
    project_dir = data.get("project_dir")
    if project_dir and not data.get("cwd") and not data.get("workdir"):
        resolver = getattr(provider, "project_dir_to_path", None)
        if resolver:
            resolved = resolver(project_dir)
            if resolved:
                cwd = resolved

    try:
        history = provider.parse_history(session_id, cwd)
        raw_usage_entries = provider.get_raw_usage(session_id, cwd)
    except Exception as e:
        return {"error": f"Failed to parse session history: {e}"}

    # 防御（#import-guard）：验证 adapter 侧 session 真实存在，防止孤儿/坏 id
    # 污染导入（例如某 Pan session 的 cli_session_id 被错误指向不存在的 session，
    # 直接 import 会匹配到 existing 并清空其 history）。
    exists = getattr(provider, "session_exists", None)
    if exists and not exists(session_id, cwd):
        return {"error": f"{adapter} session {session_id} not found on disk; refusing to import"}

    raw_usage = sess.accumulate_raw_usage(None, raw_usage_entries)
    total_usage = sess.compute_total_usage(raw_usage)

    # 信用验证：比对 raw_usage_entries 总和与 total_usage（调试用途，不阻断导入）
    credit_sum = sum(
        e.get("rawUsage", {}).get("credit", 0) for e in raw_usage_entries
    )
    cli_credit = total_usage.get("credit", 0) if total_usage else 0
    if abs(credit_sum - cli_credit) > 0.01:
        _log(f"[WARN] import credit mismatch ({adapter}): sum={credit_sum:.2f} cli={cli_credit:.2f}")

    # Dedup by cli_session_id（限定同 adapter）
    existing = None
    for s in sess.list_all():
        if s.cli_session_id == session_id and s.adapter == adapter:
            existing = s
            break

    # 防御（#import-guard）：匹配到已有 session 但解析不出任何历史时，拒绝用空
    # 历史覆盖，避免把已有会话数据清空。
    if existing and not history:
        _log(f"[WARN] import {session_id}: history empty for existing session {existing.id}; refusing to overwrite")
        return {"error": f"{adapter} session {session_id} has no parseable history; refusing to overwrite existing session {existing.id}"}

    if existing:
        w = worker.find_alive_worker_by_session(existing.id)
        if w:
            # Worker process is alive — overwrite history atomically.
            # _replaying=True prevents _read_stdout from appending during
            # the race window (CLI writes to external store before stdout),
            # which would otherwise duplicate agent-side messages.
            w._replaying = True
            try:
                existing.history = history
                existing.raw_usage = raw_usage
                existing.total_usage = total_usage
                # history 整体替换 → 全量重写 jsonl（增量 append 会把新历史
                # 头部误判为已落盘而跳过）
                sess.save_full(existing)
                await broadcast({
                    "type": "session.updated",
                    "sessionId": existing.id,
                })
            finally:
                w._replaying = False
            return {**_session_to_api(existing), "reimported": True}
        w = worker.find_worker_by_session(existing.id)
        if w:
            await worker.kill_worker(w.worker_id)
        existing.history = history
        existing.raw_usage = raw_usage
        existing.total_usage = total_usage
        existing.last_result = None
        sess.save_full(existing)
        await broadcast({
            "type": "session.updated",
            "sessionId": existing.id,
        })
        return {**_session_to_api(existing), "reimported": True}

    name = (
        data.get("name", "")
        or provider.get_session_title(session_id, cwd)
        or f"{adapter}-{session_id[:8]}"
    )

    # 模板/能力字段：复用 _build_session_params 模板解析（显式 > 模板 > 默认），
    # 使导入的会话能带 model / permission_mode / MCP / pan_access。
    # workdir 刻意保留外部项目路径（cwd），不落 data/workdirs/<name>。
    try:
        params = _build_session_params(
            {
                "adapter": adapter,
                "name": name,
                "sessionTemplate": data.get("sessionTemplate"),
                **({"panAccess": data["panAccess"]} if "panAccess" in data else {}),
            },
            resolve_workdir=False,
        )
    except ValueError as e:
        return {"error": f"Failed to apply session template: {e}"}

    # model 兜底：模板未显式指定时回填原生存储里实际用过的模型
    # （opencode 旧端点行为；cbc/kimi 顺带受益，无害）。
    model = params.get("model") or (
        raw_usage_entries[0].get("model") if raw_usage_entries else None
    )

    s = sess.create(
        name=name,
        adapter=adapter,
        cli_session_id=session_id,
        history=history,
        raw_usage=raw_usage,
        total_usage=total_usage,
        workdir=cwd,
        model=model,
        permission_mode=params.get("permission_mode"),
        session_template=params.get("session_template"),
        pan_access=params.get("pan_access"),
        adapter_config=params.get("adapter_config"),
    )

    await broadcast({
        "type": "session.created",
        "sessionId": s.id,
        "name": s.name,
    })

    return _session_to_api(s)


@app.get("/api/adapters/{adapter}/sessions")
async def api_adapter_sessions(adapter: str, cwd: str = ""):
    """List adapter-native sessions available for import (generic)."""
    provider = _sessions_provider(adapter)
    if provider is None:
        return {"error": f"Unknown adapter: {adapter}"}
    cwd = cwd or str(Path.cwd())
    sessions = provider.list_sessions(cwd)
    return {"sessions": sessions, "total": len(sessions)}


@app.post("/api/adapters/{adapter}/sessions/import")
async def api_adapter_sessions_import(adapter: str, data: dict):
    """Import an adapter-native session into Pan (generic)."""
    provider = _sessions_provider(adapter)
    if provider is None:
        return {"error": f"Unknown adapter: {adapter}"}
    return await _import_session(provider, adapter, data)


# ── Worker actions ──

@app.post("/api/worker/{worker_id}/restart")
async def api_restart(worker_id: str):
    err = await worker.restart_worker(worker_id)
    if err:
        return {"error": err}
    return {"workerId": worker_id, "status": "restarted"}


@app.post("/api/worker/{worker_id}/settings")
async def api_worker_settings(worker_id: str, data: dict):
    """Apply model/mode/thinking settings to a session and respawn the worker."""
    w = worker.get_worker(worker_id)
    if not w:
        return {"error": "Worker not found"}
    s = sess.get(w.session_id)
    if not s:
        return {"error": "Session not found"}

    try:
        _apply_session_updates(s, data)
    except ValueError as e:
        return {"error": str(e)}
    sess.save(s)

    extra_args: list[str] = []
    if "model" in data:
        extra_args.extend(["--model", data["model"]])
    if "permissionMode" in data:
        extra_args.extend(["--permission-mode", data["permissionMode"] or ""])
    extra_args.extend(get_adapter(s.adapter).effort_args(s))

    err = await worker.respawn_worker(worker_id, extra_args if extra_args else None)
    if err:
        return {"error": err}

    return {
        "workerId": worker_id,
        "sessionId": s.id,
        "model": s.model,
        "permissionMode": s.permission_mode,
        "alwaysThinkingEnabled": s.adapter_config.get("always_thinking_enabled", False),
        "effort": s.adapter_config.get("effort", ""),
        "status": "settings applied",
    }


@app.post("/api/worker/{worker_id}/rename")
async def api_rename(worker_id: str, data: dict):
    new_name = data.get("name")
    if not new_name:
        return {"error": "name is required"}

    err = _check_session_name(new_name)
    if err:
        return {"error": err}

    session_id = None
    w = worker.get_worker(worker_id)
    if w:
        session_id = w.session_id
    else:
        session_id = data.get("sessionId") or worker_id

    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}

    old_name = s.name
    s.name = new_name
    sess.save(s)
    await broadcast({
        "type": "session.renamed",
        "sessionId": s.id,
        "oldName": old_name,
        "newName": new_name,
    })
    return {"sessionId": s.id, "name": new_name, "status": "renamed"}


@app.post("/api/worker/{worker_id}/branch")
async def api_branch(worker_id: str, data: dict):
    w = worker.get_worker(worker_id)
    if not w:
        return {"error": "Worker not found"}

    orig = sess.get(w.session_id)
    if not orig or not orig.cli_session_id:
        return {"error": "Session not ready for branching"}

    name = data.get("name") or f"{orig.name}-branch"
    new_session = sess.create(name, adapter=orig.adapter, model=orig.model,
                              permission_mode=orig.permission_mode,
                              always_thinking_enabled=orig.adapter_config.get("always_thinking_enabled", False),
                              effort=orig.adapter_config.get("effort", ""),
                              max_thinking_tokens=orig.adapter_config.get("max_thinking_tokens"),
                              workdir=orig.workdir)

    result = await worker.branch_worker(worker_id, new_session.id)
    if isinstance(result, str):
        sess.delete(new_session.id)
        return {"error": result}

    return {
        "workerId": result.worker_id,
        "sessionId": new_session.id,
        "name": new_session.name,
        "status": "idle",
        "parentSessionId": w.session_id,
    }


@app.post("/api/worker/{worker_id}/interrupt")
async def api_interrupt(worker_id: str):
    err = await worker.interrupt_worker(worker_id)
    if err:
        return {"error": err}
    return {"workerId": worker_id, "status": "interrupted"}


@app.post("/api/worker/{worker_id}/takeover")
async def api_takeover(worker_id: str):
    w = worker.get_worker(worker_id)
    if not w:
        return {"error": "Worker not found"}
    s = sess.get(w.session_id)
    if not s:
        return {"error": "Session not found"}
    if not s.cli_session_id:
        return {"error": "Worker has no CLI session yet"}
    if w.status == "held":
        return {"error": "Worker already in takeover mode"}

    adapter_cmd = w.adapter.takeover_command(s)
    if not adapter_cmd:
        return {"error": f"Adapter '{w.adapter.name}' does not support takeover"}

    err = await worker.restart_worker(worker_id)
    if err:
        return {"error": err}

    w.status = "held"
    await broadcast({
        "type": "worker.status",
        "sessionId": w.session_id,
        "workerId": w.worker_id,
        "status": "held",
    })

    try:
        w.takeover_pid = _open_terminal(
            # 逐参数引号转义：takeover 命令含 --resume <cli_session_id>，裸 join
            # 会把其特殊字符拆成额外参数，导致 takeover 终端 cbc 启动失败。
            subprocess.list2cmdline(adapter_cmd),
            s.workdir or Path.cwd(),
        )
    except FileNotFoundError:
        return {"error": "terminal opener not found"}
    except OSError as e:
        return {"error": str(e)}

    return {
        "workerId": worker_id,
        "sessionId": w.session_id,
        "cliSessionId": s.cli_session_id,
        # list2cmdline 逐参数引号转义（同 /takeover）：system_prompt 含空格时
        # 裸 join 的命令无法直接粘贴执行（takeover 修复）。
        # list2cmdline 逐参数引号转义（同 /takeover）：takeover 命令含 --resume
        # <cli_session_id>，裸 join 的命令无法直接粘贴执行。
        "takeoverCommand": subprocess.list2cmdline(adapter_cmd),
        "takeoverPid": w.takeover_pid,
        "status": "takeover started",
    }


@app.get("/api/worker/{worker_id}/takeover-command")
async def api_takeover_command(worker_id: str):
    """Return the takeover command without executing it (mobile use).

    Unlike POST /takeover, this does NOT restart the worker or open a
    terminal.  The caller (mobile dashboard) copies the command to
    clipboard so the user can paste it manually.
    """
    w = worker.get_worker(worker_id)
    if not w:
        return {"error": "Worker not found"}
    s = sess.get(w.session_id)
    if not s:
        return {"error": "Session not found"}
    if not s.cli_session_id:
        return {"error": "Worker has no CLI session yet"}

    adapter_cmd = w.adapter.takeover_command(s)
    if not adapter_cmd:
        return {"error": f"Adapter '{w.adapter.name}' does not support takeover"}

    return {
        "workerId": worker_id,
        "sessionId": w.session_id,
        "cliSessionId": s.cli_session_id,
        # list2cmdline 逐参数引号转义（同 /takeover）：takeover 命令含 --resume
        # <cli_session_id>，裸 join 的命令无法直接粘贴执行。
        "takeoverCommand": subprocess.list2cmdline(adapter_cmd),
    }


# ── Memory API ──

_memory_managers: dict[str, object] = {}  # character_id → MemoryManager

# character_id must be exactly "char_" + 16 lowercase hex (or "default")
_CHARACTER_ID_RE = re.compile(r"^(?:char_[0-9a-f]{16}|default)$")


def _validate_character_id(character_id: str) -> bool:
    """Return True if character_id is safe to embed in a filesystem path."""
    return bool(_CHARACTER_ID_RE.match(character_id))


def _get_memory_manager(character_id: str):
    """Get or create a MemoryManager for a character.

    Rejects unsafe character_ids (path traversal). Memory is indexed with the
    same sentence-transformers provider used by the worker injection path
    (packages/core/memory_context.py) to avoid embedding dims mismatch.
    """
    from packages.core.memory import MemoryManager, PROVIDER_SENTENCE_TRANSFORMERS

    if not _validate_character_id(character_id):
        raise ValueError(
            f"Invalid character_id: {character_id!r}. "
            "Expected 'char_<16 hex>' or 'default'."
        )

    if character_id not in _memory_managers:
        api_key = os.environ.get("OPENAI_API_KEY")
        db_path = str(DATA_DIR / "memory" / f"{character_id}.sqlite")
        db_path_parent = Path(db_path).parent
        db_path_parent.mkdir(parents=True, exist_ok=True)
        _memory_managers[character_id] = MemoryManager(
            db_path=db_path,
            api_key=api_key,
            provider=PROVIDER_SENTENCE_TRANSFORMERS,
        )
    return _memory_managers[character_id]


# ── Character API ──


def _ensure_manifest_fresh() -> None:
    """Hot-reload the manifest if any manifest file changed on disk.

    Cheap on the hot path: only ``stat``s files (no read / parse). A full
    re-read + re-parse + atomic config swap happens *only* when the newest
    mtime advanced or the set of manifest files changed. Any failure is
    swallowed (and already logged inside the manager) so a request never 500s
    on a broken manifest.
    """
    if _character_manager is None:
        return
    try:
        if _character_manager.manifest_changed():
            _character_manager.reload_manifest()
    except Exception:
        _log("[Pan] Manifest hot-reload check failed (non-fatal)")


@app.get("/api/session-templates")
async def api_session_templates():
    """List available session templates from manifest.

    Semantically this endpoint lists session_templates (the former "profiles"),
    which now describe session config rather than characters.
    """
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    _ensure_manifest_fresh()
    templates = _character_manager.list_session_templates()
    return {
        "sessionTemplates": [
            {
                "name": t.name,
                "adapter": t.adapter,
                "model": t.model,
                "mcpServers": list(t.mcp_servers or []),
                "sourceManifest": t.source_manifest,
                "sourceManifestLabel": t.source_manifest_label,
                "system_prompt_preview": t.system_prompt[:100] + "..." if len(t.system_prompt) > 100 else t.system_prompt,
                "panAccess": {
                    "restrictToManaged": t.restrict_to_managed,
                    "canClaimUnmanaged": t.can_claim_unmanaged,
                    "autoClaimCreated": t.auto_claim_created,
                },
            }
            for t in templates
        ],
        "total": len(templates),
    }


@app.get("/api/mcp/servers")
async def api_mcp_servers():
    """List all available MCP servers from the manifest.

    Returns the full catalog of MCP servers declared across plugin manifests
    (deduplicated/merged). Used by the Manage modal's "MCP Server" section to
    let the user multi-select which servers a session enables.

    Does NOT expose `env` (may contain secrets). If the manifest is not loaded
    yet, returns an empty list with `loaded: false` instead of a 500.
    """
    if _character_manager is None or _character_manager._manifest_config is None:
        return {"servers": [], "loaded": False}
    _ensure_manifest_fresh()
    servers = [
        {
            "name": srv.name,
            "command": srv.command,
            "cwd": srv.cwd,
        }
        for srv in _character_manager._manifest_config.mcp_servers
    ]
    return {"servers": servers, "loaded": True}


@app.get("/api/characters/profiles")
async def api_characters_profiles():
    """Deprecated alias for GET /api/session-templates (backward compat).

    Old callers hitting the historical ``/api/characters/profiles`` path get
    the same response; new code should use ``/api/session-templates``.
    """
    return await api_session_templates()


@app.post("/api/manifest/reload")
async def api_manifest_reload():
    """Force a manifest hot-reload.

    Idempotent: calling it repeatedly just reloads the same files and returns
    the same counts. Returns the number of loaded templates/servers/routes so
    the caller can confirm the new state. On failure it returns the last good
    counts and ``reloaded: false`` (the previous config is kept).
    """
    if _character_manager is None:
        return {"error": "Character manager not initialized", "reloaded": False}
    config = _character_manager.reload_manifest()
    if config is None:
        return {
            "reloaded": False,
            "sessionTemplates": 0,
            "mcpServers": 0,
            "characters": 0,
            "commandRoutes": 0,
        }
    return {
        "reloaded": True,
        "sessionTemplates": len(config.session_templates),
        "mcpServers": len(config.mcp_servers),
        "characters": len(config.character_templates),
        "commandRoutes": len(config.command_routes),
    }


@app.get("/api/manifest/command-routes")
async def api_manifest_command_routes():
    """List QQ Bot command routes from loaded manifests.

    Used by the QQ Bot plugin (separate NoneBot2 process) to do prefix
    matching: a message starting with one of ``prefixes`` is forwarded
    directly to ``target`` (HTTP POST ``{"text": "..."}``) without going
    through the LLM path. The plugin should sort by prefix length descending
    so ``.rca`` wins over ``.rc``.
    """
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    _ensure_manifest_fresh()
    routes = _character_manager.list_command_routes()
    return {
        "routes": [
            {"prefixes": list(r.prefixes), "target": r.target}
            for r in routes
        ],
        "total": len(routes),
    }


@app.post("/api/characters")
async def api_characters_create(data: dict):
    """Create a new character from a manifest character_template."""
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    _ensure_manifest_fresh()
    template_name = data.get("template_name", "")
    if not template_name:
        return {"error": "template_name is required"}
    try:
        char = _character_manager.create_character(
            template_name=template_name,
            name=data.get("name"),
            auto_index=True,
        )
    except ValueError as e:
        return {"error": str(e)}
    return {
        "id": char.id,
        "name": char.name,
        "memory_db_path": char.memory_db_path,
        "memory_dir": char.memory_dir,
        "created_at": char.created_at,
    }


@app.get("/api/characters")
async def api_characters_list():
    """List all created characters."""
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    chars = _character_manager.list_characters()
    return {
        "characters": [
            {"id": c.id, "name": c.name}
            for c in chars
        ],
        "total": len(chars),
    }


@app.get("/api/characters/{character_id}")
async def api_characters_get(character_id: str):
    """Get character details including memory stats."""
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    if not _validate_character_id(character_id):
        return {"error": f"Invalid character_id: {character_id!r}"}
    char = _character_manager.get_character(character_id)
    if char is None:
        return {"error": f"Character {character_id} not found"}
    mem_stats = None
    try:
        mgr = _character_manager.get_memory_manager(character_id)
        if mgr:
            mem_stats = mgr.stats()
    except Exception:
        pass
    return {
        "id": char.id,
        "name": char.name,
        "memory_db_path": char.memory_db_path,
        "memory_dir": char.memory_dir,
        "memory_stats": {"files": mem_stats.files, "chunks": mem_stats.chunks} if mem_stats else None,
        "created_at": char.created_at,
    }


@app.delete("/api/characters/{character_id}")
async def api_characters_delete(character_id: str):
    """Delete a character and its memory store."""
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    if not _validate_character_id(character_id):
        return {"error": f"Invalid character_id: {character_id!r}"}
    if _character_manager.delete_character(character_id):
        # Drop any cached MemoryManager in this module too — the underlying
        # .sqlite has been unlinked and the fd is stale (#33).
        mgr = _memory_managers.pop(character_id, None)
        if mgr is not None:
            try:
                mgr.close()
            except Exception:
                pass
        return {"status": "deleted", "character_id": character_id}
    return {"error": f"Character {character_id} not found"}


@app.post("/api/memory/index")
async def api_memory_index(data: dict):
    """Index a directory of .md files into the memory store.
    
    Request body:
        character_id: str — which character's memory store
        dir_path: str     — directory containing .md files to index
    """
    character_id = data.get("character_id", "default")
    dir_path = data.get("dir_path")
    if not dir_path:
        return {"error": "dir_path is required"}

    try:
        mgr = _get_memory_manager(character_id)
    except ValueError as e:
        return {"error": str(e)}

    # Restrict dir_path to the project root to prevent arbitrary-directory
    # indexing (security). Mirrors _resolve_fs_path's containment check.
    try:
        target = Path(dir_path).resolve()
        target.relative_to(_PROJECT_DIR)
    except ValueError:
        return {
            "error": f"dir_path {dir_path!r} is outside allowed roots "
            f"({_PROJECT_DIR})"
        }

    report = mgr.index_directory(str(target))
    return {
        "character_id": character_id,
        "files_scanned": report.files_scanned,
        "files_modified": report.files_modified,
        "chunks_upserted": report.chunks_upserted,
        "details": [
            {"path": d.path, "status": d.status, "chunks": d.chunks}
            for d in report.details
        ],
    }


@app.get("/api/memory/search")
async def api_memory_search(
    q: str = "",
    character_id: str = "default",
    max_results: int = 6,
    min_score: float = 0.35,
):
    """Search the memory store for a character.
    
    Query params:
        q            — search query text
        character_id — which character's memory store
        max_results  — max number of results (default 6)
        min_score    — minimum score threshold (default 0.35)
    """
    if not q.strip():
        return {"results": [], "total": 0}

    try:
        mgr = _get_memory_manager(character_id)
    except ValueError as e:
        return {"error": str(e)}
    results = mgr.search(q, max_results=max_results, min_score=min_score)
    return {
        "results": [
            {
                "chunk_id": r.chunk_id,
                "path": r.path,
                "text": r.text,
                "score": round(r.score, 4),
                "start_line": r.start_line,
                "end_line": r.end_line,
                "source": r.source,
            }
            for r in results
        ],
        "total": len(results),
    }


@app.get("/api/memory/stats")
async def api_memory_stats(character_id: str = "default"):
    """Get memory store statistics for a character."""
    try:
        mgr = _get_memory_manager(character_id)
    except ValueError as e:
        return {"error": str(e)}
    stats = mgr.stats()
    return {
        "character_id": character_id,
        "files": stats.files,
        "chunks": stats.chunks,
    }


@app.post("/api/memory/inject")
async def api_memory_inject(data: dict):
    """Search memory and return formatted context for Worker injection.

    Request body:
        text         — user's query to search memory AND the task text
        character_id — which character's memory to search (default: "default")
        max_results  — max memory snippets (default: 3)
        min_score    — minimum score threshold (default: 0.35)

    Returns:
        injected_text — the task text with memory context prepended
        context       — raw memory context (Markdown)
        snippet_count — number of memory snippets found
    """
    from packages.core.memory_context import search_and_format, inject_context

    text = data.get("text", "")
    if not text.strip():
        return {"error": "text is required"}

    character_id = data.get("character_id", "default")
    max_results = data.get("max_results", 3)
    min_score = float(data.get("min_score", 0.35))

    if not _validate_character_id(character_id):
        return {"error": f"Invalid character_id: {character_id!r}"}

    # Search memory using the task text as query
    context = search_and_format(
        query=text,
        character_id=character_id,
        max_results=max_results,
        min_score=min_score,
        db_dir=str(DATA_DIR / "memory"),
    )

    if context.snippet_count > 0:
        injected = inject_context(text, context)
    else:
        injected = text

    return {
        "injected_text": injected,
        "context": context.results_md,
        "snippet_count": context.snippet_count,
        "character_id": character_id,
    }


# ── File-system operations ──

@app.get("/api/fs/list")
async def api_fs_list(session_id: str, path: str = "", include_hidden: bool = False):
    """List files/dirs under a path within the session's workdir."""
    try:
        target = _resolve_fs_path(session_id, path)
    except ValueError as e:
        return {"error": str(e)}
    if not target.is_dir():
        return {"error": f"Not a directory: {path!r}"}
    try:
        entries = []
        for entry in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if entry.name.startswith("."):
                if not include_hidden:
                    continue
                if entry.name in _HIDDEN_ENTRIES:
                    continue
            stat = entry.stat()
            entries.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": stat.st_size if entry.is_file() else 0,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return {"entries": entries}
    except PermissionError:
        return {"error": f"Permission denied: {path!r}"}


@app.get("/api/fs/read")
async def api_fs_read(session_id: str, path: str = ""):
    """Read file contents within the session's workdir."""
    try:
        target = _resolve_fs_path(session_id, path)
    except ValueError as e:
        return {"error": str(e)}
    if not target.is_file():
        return {"error": f"Not a file: {path!r}"}
    if target.stat().st_size > _MAX_FILE_BYTES:
        return {"error": f"File too large (max {_MAX_FILE_BYTES // (1024*1024)} MiB)"}
    try:
        content = target.read_text(encoding="utf-8")
        return {"content": content, "size": len(content)}
    except UnicodeDecodeError:
        return {"error": "Cannot read binary file"}
    except PermissionError:
        return {"error": f"Permission denied: {path!r}"}


@app.post("/api/fs/write")
async def api_fs_write(data: dict):
    """Write content to a file within the session's workdir (atomic write)."""
    session_id = data.get("session_id")
    path = data.get("path", "")
    content = data.get("content", "")
    if not session_id:
        return {"error": "session_id required"}
    if len(content) > _MAX_FILE_BYTES:
        return {"error": f"File too large (max {_MAX_FILE_BYTES // (1024*1024)} MiB)"}
    try:
        target = _resolve_fs_path(session_id, path)
    except ValueError as e:
        return {"error": str(e)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # atomic: write to temp then replace
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(str(tmp), str(target))
        return {"path": path, "size": len(content)}
    except PermissionError:
        return {"error": f"Permission denied: {path!r}"}


@app.post("/api/fs/rename")
async def api_fs_rename(data: dict):
    """Rename a file or directory within the session's workdir."""
    session_id = data.get("session_id")
    frm = data.get("from", "")
    to = data.get("to", "")
    if not session_id:
        return {"error": "session_id required"}
    if not frm:
        return {"error": "from path required"}
    if not to:
        return {"error": "to path required"}
    try:
        src = _resolve_fs_path(session_id, frm)
        dst = _resolve_fs_path(session_id, to)
    except ValueError as e:
        return {"error": str(e)}
    try:
        os.replace(str(src), str(dst))
        return {"from": frm, "to": to}
    except PermissionError:
        return {"error": f"Permission denied"}
    except OSError as e:
        return {"error": str(e)}


@app.post("/api/fs/delete")
async def api_fs_delete(data: dict):
    """Delete a file or empty directory within the session's workdir."""
    session_id = data.get("session_id")
    path = data.get("path", "")
    if not session_id:
        return {"error": "session_id required"}
    try:
        target = _resolve_fs_path(session_id, path)
    except ValueError as e:
        return {"error": str(e)}
    if not target.exists():
        return {"error": f"Not found: {path!r}"}
    try:
        if target.is_dir():
            target.rmdir()  # only deletes empty dirs — safety
        else:
            target.unlink()
        return {"path": path, "deleted": True}
    except OSError as e:
        return {"error": str(e)}


# ── React SPA (coexist: / + /react/* 均为 React) ──
# Mount React at /react/ unless FRONTEND_MODE=legacy（/react 保留作兼容入口）
if REACT_DIST_EXISTS and FRONTEND_MODE != "legacy":
    react_name = (
        "react"
        if not app.routes or not any(
            getattr(r, "name", "") == "react" for r in app.routes
        )
        else "react_v2"
    )

    @app.get(f"/{react_name}/", response_class=HTMLResponse)
    async def react_index_html():
        """Serve React index.html with no-cache so new builds are picked up on refresh."""
        return HTMLResponse(
            content=(REACT_DIST_DIR / "index.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache"},
        )

    app.mount(
        f"/{react_name}",
        StaticFiles(directory=str(REACT_DIST_DIR), html=True),
        name=react_name,
    )

    @app.get(f"/{react_name}/{{full_path:path}}")
    async def react_spa_fallback(full_path: str):
        """SPA fallback: return index.html for any /react/* path not matching a file."""
        file_path = REACT_DIST_DIR / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(
            REACT_DIST_DIR / "index.html",
            headers={"Cache-Control": "no-cache"},
        )


# ── Static files ──
STATIC_DIR = _WEB_DIR / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
