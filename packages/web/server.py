"""Pan Web Channel — FastAPI routes + WebSocket + Dashboard."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles

from packages.core import worker
from packages.core import session as sess
from packages.core.adapters import get_adapter, list_adapters
from packages.core.adapters.cbc import sessions as cbc_sessions
from packages.core.adapters.cbc.sessions import sanitize_project_dir_name
from packages.core.adapters.kimi import sessions as kimi_sessions
from packages.core.config import load_config
from packages.core.character import CharacterManager

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
    
    # Init CharacterManager with manifest
    global _character_manager
    config = load_config()
    plugin_paths = config.get("plugin_manifests", ["manifest.json"])
    _character_manager = CharacterManager(str(DATA_DIR))
    try:
        _character_manager.load_manifest(plugin_paths)
        profiles = _character_manager.list_profiles()
        _log(f"[Pan] Loaded {len(profiles)} character profiles from manifest")
    except Exception as e:
        _log(f"[Pan] Character manifest not loaded: {e}")
    
    yield
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
# "coexist"（默认）→ 旧前端 / + React /react/
# "react" → React SPA /
# "legacy" → 仅旧前端
FRONTEND_MODE = load_config().get("frontend", "coexist")

_MOBILE_UA_RE = re.compile(
    r"Mobile|Android|iPhone|iPad|iPod|BlackBerry|Windows Phone|webOS",
    re.IGNORECASE,
)


async def broadcast(data: dict):
    dead = set()
    for ws in list(ws_clients):
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)
    dead_a = set()
    etype = data.get("type", "")
    data_session_id = data.get("sessionId")
    for ws in list(agent_clients):
        sub = agent_subscriptions.get(ws)
        if sub is None:
            sub = AgentSubscription()
        # 事件类型过滤
        if etype not in sub.event_types and "*" not in sub.event_types:
            continue
        # worker.result 按 sessionId 过滤（若订阅了特定 session 列表）
        if etype == "worker.result" and sub.session_ids and data_session_id not in sub.session_ids:
            continue
        # 记录已消费的 result 序号（重连补发用）
        if etype == "worker.result" and data_session_id:
            seq = data.get("taskSeq")
            if isinstance(seq, int):
                sub.consumed_seq[data_session_id] = max(sub.consumed_seq.get(data_session_id, 0), seq)
        try:
            await ws.send_json(data)
        except Exception:
            dead_a.add(ws)
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
        "workerStatus": w.status if w else None,
        "workerId": w.worker_id if w else None,
        "mcpEnabled": ac.get("mcp_enabled", False),
        "mcpLocked": _get_mcp_locked_state(s),
        "outputMode": ac.get("output_mode"),
        "gameId": s.game_id,
    }


def _get_mcp_locked_state(s) -> bool | None:
    """Check if MCP toggle is locked for this session's character profile."""
    if not s.character_id or _character_manager is None:
        return None
    try:
        char = _character_manager.get_character(s.character_id)
        if char and char.mcp_mode:
            return char.mcp_mode in ("always", "never")
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


def _build_session_params(data: dict) -> dict:
    """Extract session creation parameters from request data, with defaults."""
    adapter_name = data.get("adapter") or "cbc"
    a = get_adapter(adapter_name)
    config = load_config().get(adapter_name, {})
    name = data.get("name", "default")
    workdir_name = data.get("workdir") or name
    
    params = {
        "name": name,
        "adapter": adapter_name,
        "model": data.get("model") or config.get("model") or a.default_model,
        "permission_mode": data.get("permissionMode") or config.get("permission_mode") or None,
        "workdir": str(_resolve_workdir(workdir_name)),
        "adapter_config": {
            "always_thinking_enabled": data.get("alwaysThinkingEnabled", config.get("always_thinking_enabled", False)),
            "effort": data.get("effort") or config.get("effort", ""),
            "max_thinking_tokens": data.get("maxThinkingTokens") or None,
        },
    }
    # Optional worker execution mode ("stream" | "oneshot"); validated later
    # by _apply_output_mode. Unset = automatic (existing behaviour).
    if "outputMode" in data and data.get("outputMode") not in (None, ""):
        params["adapter_config"]["output_mode"] = data["outputMode"]
    
    # If characterId is set, override adapter/model/permission_mode/system_prompt from character
    character_id = data.get("characterId")
    if character_id and _character_manager is not None:
        char = _character_manager.get_character(character_id)
        if char is None:
            # characterId may be a profile name (e.g. "meta-agent") rather than a
            # persisted character id — instantiate the profile on first use.
            try:
                char = _character_manager.create_character(character_id)
            except ValueError:
                char = None
        if char:
            if not data.get("adapter"):
                params["adapter"] = char.adapter
            if not data.get("model"):
                params["model"] = char.model
            if not data.get("permissionMode"):
                params["permission_mode"] = char.permission_mode
            params["character_id"] = char.id
            params["system_prompt"] = char.system_prompt
            # Always pass mcp_servers so config is available if user toggles MCP on
            if char.mcp_servers is not None and len(char.mcp_servers) > 0:
                params["adapter_config"]["mcp_servers"] = char.mcp_servers
            # Set mcp_enabled based on profile's mcp_mode
            if char.mcp_mode == "always":
                params["adapter_config"]["mcp_enabled"] = True
            elif char.mcp_mode == "never":
                params["adapter_config"]["mcp_enabled"] = False
            elif char.mcp_mode == "optional":
                # Start in stream mode, user can toggle later
                if "mcp_enabled" not in params["adapter_config"]:
                    params["adapter_config"]["mcp_enabled"] = False
    
    return params


def _safe_adapter(adapter_name: str):
    """Return adapter by name, falling back to cbc on unknown names."""
    try:
        return get_adapter(adapter_name)
    except KeyError:
        return get_adapter("cbc")


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
    if "mcpEnabled" in data:
        _apply_mcp_enabled(s, data["mcpEnabled"])
    if "mcpServers" in data:
        _apply_mcp_servers(s, data["mcpServers"])
    if "outputMode" in data:
        _apply_output_mode(s, data["outputMode"])
    if "gameId" in data:
        # Allow None / empty to clear; store string otherwise. Used by QQ
        # plugin to bind a RuleWhisper game_id to a group-scoped session so
        # LLM-driven MCP tool calls can pass it through.
        s.game_id = data["gameId"] or None


def _apply_mcp_servers(s: sess.Session, server_names) -> None:
    """Set session mcp_servers by manifest server names (e.g. ["pan"]).

    Resolves names to full configs via the character manager's manifest table
    (same as create_character). Accepts a list of names, or None/[] to clear.
    """
    if server_names in (None, [], ""):
        s.set_adapter_field("mcp_servers", [])
        return
    if not isinstance(server_names, list):
        raise ValueError("mcpServers must be a list of server names")
    if _character_manager is None or _character_manager._manifest_config is None:
        raise ValueError("MCP manifest not loaded")
    configs: list[dict] = []
    for name in server_names:
        found = False
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
                found = True
                break
        if not found:
            raise ValueError(f"Unknown MCP server: {name!r}")
    s.set_adapter_field("mcp_servers", configs)


def _apply_mcp_enabled(s: sess.Session, enable: bool):
    """Apply mcpEnabled toggle, respecting profile mcp_mode lock.

    No broad `except Exception: pass` here — swallowing non-ValueError
    exceptions bypassed the always/never lock (#12). If the character lookup
    raises, that's a real error and should surface.
    """
    if s.character_id and _character_manager is not None:
        char = _character_manager.get_character(s.character_id)
        if char:
            if char.mcp_mode == "always" and not enable:
                raise ValueError(f"MCP is locked to 'always' for profile '{char.profile_name}'. Cannot disable.")
            if char.mcp_mode == "never" and enable:
                raise ValueError(f"MCP is locked to 'never' for profile '{char.profile_name}'. Cannot enable.")
            if char.mcp_mode == "always":
                # Already enabled, no-op
                s.set_adapter_field("mcp_enabled", True)
                return

    s.set_adapter_field("mcp_enabled", enable)


_VALID_OUTPUT_MODES = ("stream", "oneshot")


def _apply_output_mode(s: sess.Session, mode):
    """Apply outputMode: worker execution channel ("stream" | "oneshot").

    - "stream": long-running stream-json process; if MCP is also enabled,
      the process is spawned with --mcp-config (stream + MCP, cbc >= 2.137.0).
    - "oneshot": per-task one-shot cbc process (legacy MCP path).
    - None/"" clears the field -> automatic (existing behaviour: MCP -> oneshot).
    """
    if mode in (None, "", "auto"):
        s.adapter_config.pop("output_mode", None)
        return
    if mode not in _VALID_OUTPUT_MODES:
        raise ValueError(f"outputMode must be one of {list(_VALID_OUTPUT_MODES)}, got {mode!r}")
    s.set_adapter_field("output_mode", mode)


def _open_terminal(cmd: str, cwd: str | Path) -> int:
    """Open a new terminal window running `cmd` in `cwd` (cross-platform)."""
    cwd = str(cwd) if cwd else str(Path.cwd())
    if sys.platform == "win32":
        proc = subprocess.Popen(
            ["powershell.exe", "-NoExit", "-Command", cmd],
            cwd=cwd,
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

@app.get("/favicon.ico")
async def favicon():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#58a6ff"/><text x="16" y="22" font-size="18" text-anchor="middle" fill="#fff" font-family="monospace" font-weight="bold">P</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # PAN_FRONTEND=react → serve React SPA at root
    if FRONTEND_MODE == "react" and REACT_DIST_EXISTS:
        return HTMLResponse(
            content=(REACT_DIST_DIR / "index.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache"},
        )

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
                    # 该 session 最新 result 已消费过则跳过
                    if sub.consumed_seq.get(sid, 0) > 0:
                        continue
                    await ws.send_json({
                        "type": "worker.result",
                        "workerId": "",
                        "sessionId": sid,
                        "status": s.last_result.get("status"),
                        "result": s.last_result.get("result"),
                        "taskSeq": sub.consumed_seq.get(sid, 0),
                        "replayed": True,
                    })

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

            elif msg_type == "handoff":
                session_id = msg.get("sessionId")
                text = msg.get("text")
                if not session_id or not text:
                    await ws.send_json({"type": "error", "message": "sessionId and text required"})
                    continue
                result = await worker.handoff(session_id, text, source="agent")
                await ws.send_json({"type": "handoff.result", **result})

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
async def api_list_sessions():
    """List all sessions (includes worker status if active)."""
    sessions = sess.list_all()
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
    params = _build_session_params(data)
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
    old_mcp_enabled = s.adapter_config.get("mcp_enabled")
    old_output_mode = s.adapter_config.get("output_mode")
    try:
        _apply_session_updates(s, data)
    except ValueError as e:
        return {"error": str(e)}
    new_mcp_enabled = s.adapter_config.get("mcp_enabled")
    new_output_mode = s.adapter_config.get("output_mode")
    # MCP enable/disable 或执行模式切换都需要重启 worker 才生效
    require_restart = (old_mcp_enabled != new_mcp_enabled
                       or old_output_mode != new_output_mode)
    sess.save(s)
    await broadcast({
        "type": "session.updated",
        "sessionId": s.id,
    })
    result = _session_to_api(s)
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
    cwd = s.workdir or ""
    try:
        if s.adapter == "kimi":
            # Kimi has no --fork flag; copy the session directory instead.
            new_cli_id = kimi_sessions.fork_kimi_session(
                s.cli_session_id, name, cwd or None,
            )
        else:
            new_cli_id = cbc_sessions.fork_cbc_session(
                s.cli_session_id, name, cwd or None,
            )
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Fork failed: {e}"}

    try:
        if s.adapter == "kimi":
            history = kimi_sessions.parse_kimi_history(new_cli_id, cwd or None)
            raw_usage_entries = kimi_sessions.get_raw_usage(new_cli_id, cwd or None)
        else:
            history = cbc_sessions.parse_cbc_history(new_cli_id, cwd)
            raw_usage_entries = cbc_sessions.get_raw_usage(new_cli_id, cwd)
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
    if "mcp_enabled" in s.adapter_config:
        new_adapter_config["mcp_enabled"] = s.adapter_config["mcp_enabled"]

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
    )

    await broadcast({
        "type": "session.created",
        "sessionId": new_s.id,
        "name": new_s.name,
    })

    return _session_to_api(new_s)


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    """Delete a session and its worker if running."""
    w = worker.find_worker_by_session(session_id)
    if w:
        asyncio.create_task(
            worker.cleanup_worker_background(w.worker_id, w.session_id)
        )
    sess.delete(session_id)
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
        w = worker.find_worker_by_session(sid)
        if w:
            asyncio.create_task(
                worker.cleanup_worker_background(w.worker_id, w.session_id)
            )
        sess.delete(sid)
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


@app.post("/api/handoff")
async def api_handoff(data: dict):
    """同步等待：发任务并阻塞直到 worker 返回结果（默认 10min 超时）。

    支持 task_id 幂等：重发同一 taskId 不重复入队（超时后安全重试）。
    """
    session_id = data.get("sessionId")
    text = data.get("text")
    if not session_id or not text:
        return {"error": "sessionId and text are required"}
    timeout = data.get("timeout", 600)
    task_id = data.get("taskId")
    return await worker.handoff(session_id, text, source="agent",
                                timeout=float(timeout), task_id=task_id)


@app.post("/api/assign")
async def api_assign(data: dict):
    """异步分派：发任务后立即返回 queued，完成时通过 worker.result 事件回调。"""
    session_id = data.get("sessionId")
    text = data.get("text")
    if not session_id or not text:
        return {"error": "sessionId and text are required"}
    return await worker.assign(session_id, text, source="agent")


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
    session_id = data.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    project_dir = data.get("project_dir")
    cwd = data.get("cwd") or str(Path.cwd())

    if not data.get("cwd") and project_dir:
        resolved = cbc_sessions.project_dir_to_path(project_dir)
        if resolved:
            cwd = resolved

    try:
        if project_dir:
            history = cbc_sessions.parse_cbc_history(session_id, project_dir=project_dir)
            raw_usage_entries = cbc_sessions.get_raw_usage(session_id, project_dir=project_dir)
        else:
            history = cbc_sessions.parse_cbc_history(session_id, cwd)
            raw_usage_entries = cbc_sessions.get_raw_usage(session_id, cwd)
    except Exception as e:
        return {"error": f"Failed to parse session history: {e}"}

    # 防御（#import-guard）：验证 cbc 侧 session 真实存在，防止孤儿/坏 id
    # 污染导入。例如某 Pan session 的 cli_session_id 被错误指向一个不存在的
    # cbc session，直接 import 会匹配到 existing 并清空其 history。
    if project_dir:
        session_path = cbc_sessions._resolve_session_file(session_id, project_dir=project_dir)
    else:
        session_path = cbc_sessions._resolve_session_file(session_id, cwd)
    if session_path is None:
        return {"error": f"CBC session {session_id} not found on disk; refusing to import"}

    raw_usage = sess.accumulate_raw_usage(None, raw_usage_entries)
    total_usage = sess.compute_total_usage(raw_usage)

    # 信用验证：比对 raw_usage_entries 总和与 total_usage（调试用途，不阻断导入）
    cbc_credit_sum = sum(
        e.get("rawUsage", {}).get("credit", 0) for e in raw_usage_entries
    )
    cli_credit = total_usage.get("credit", 0) if total_usage else 0
    if abs(cbc_credit_sum - cli_credit) > 0.01:
        _log(f"[WARN] import credit mismatch: cbc_sum={cbc_credit_sum:.2f} cli={cli_credit:.2f}")

    existing = None
    for s in sess.list_all():
        if s.cli_session_id == session_id:
            existing = s
            break

    # 防御（#import-guard）：匹配到已有 session 但 cbc 侧解析不出任何历史时，
    # 拒绝用空历史覆盖，避免把已有会话数据清空。
    if existing and not history:
        _log(f"[WARN] import {session_id}: history empty for existing session {existing.id}; refusing to overwrite")
        return {"error": f"CBC session {session_id} has no parseable history; refusing to overwrite existing session {existing.id}"}

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
                sess.save(existing)
                await broadcast({
                    "type": "session.updated",
                    "sessionId": existing.id,
                })
            finally:
                w._replaying = False
            return _session_to_api(existing)
        w = worker.find_worker_by_session(existing.id)
        if w:
            await worker.kill_worker(w.worker_id)
        existing.history = history
        existing.raw_usage = raw_usage
        existing.total_usage = total_usage
        existing.last_result = None
        sess.save(existing)
        await broadcast({
            "type": "session.updated",
            "sessionId": existing.id,
        })
        return _session_to_api(existing)

    name = (
        data.get("name", "")
        or cbc_sessions.get_session_title(session_id, project_dir=project_dir, cwd=cwd)
        or f"cbc-{session_id[:8]}"
    )

    s = sess.create(
        name=name,
        cli_session_id=session_id,
        history=history,
        raw_usage=raw_usage,
        total_usage=total_usage,
        workdir=cwd,
    )

    await broadcast({
        "type": "session.created",
        "sessionId": s.id,
        "name": s.name,
    })

    return _session_to_api(s)


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
    from packages.core.adapters.kimi import sessions as kimi_sessions

    session_id = data.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    cwd = data.get("cwd") or str(Path.cwd())

    try:
        history = kimi_sessions.parse_kimi_history(session_id, cwd)
        raw_usage_entries = kimi_sessions.get_raw_usage(session_id, cwd)
    except Exception as e:
        return {"error": f"Failed to parse Kimi session history: {e}"}

    raw_usage = sess.accumulate_raw_usage(None, raw_usage_entries)
    total_usage = sess.compute_total_usage(raw_usage)

    # Dedup by cli_session_id
    existing = None
    for s in sess.list_all():
        if s.cli_session_id == session_id and s.adapter == "kimi":
            existing = s
            break

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
                sess.save(existing)
                await broadcast({
                    "type": "session.updated",
                    "sessionId": existing.id,
                })
            finally:
                w._replaying = False
            return _session_to_api(existing)
        w = worker.find_worker_by_session(existing.id)
        if w:
            await worker.kill_worker(w.worker_id)
        existing.history = history
        existing.raw_usage = raw_usage
        existing.total_usage = total_usage
        existing.last_result = None
        sess.save(existing)
        await broadcast({
            "type": "session.updated",
            "sessionId": existing.id,
        })
        return _session_to_api(existing)

    name = (
        data.get("name", "")
        or kimi_sessions.get_session_title(session_id, cwd)
        or f"kimi-{session_id[:8]}"
    )

    s = sess.create(
        name=name,
        adapter="kimi",
        cli_session_id=session_id,
        history=history,
        raw_usage=raw_usage,
        total_usage=total_usage,
        workdir=cwd,
    )

    await broadcast({
        "type": "session.created",
        "sessionId": s.id,
        "name": s.name,
    })

    return _session_to_api(s)


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
            " ".join(adapter_cmd),
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
        "takeoverCommand": " ".join(adapter_cmd),
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
        "takeoverCommand": " ".join(adapter_cmd),
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


@app.get("/api/characters/profiles")
async def api_characters_profiles():
    """List available character profiles from manifest."""
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    profiles = _character_manager.list_profiles()
    return {
        "profiles": [
            {
                "name": p.name,
                "adapter": p.adapter,
                "model": p.model,
                "mcpServers": list(p.mcp_servers or []),
                "system_prompt_preview": p.system_prompt[:100] + "..." if len(p.system_prompt) > 100 else p.system_prompt,
            }
            for p in profiles
        ],
        "total": len(profiles),
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
    """Create a new character from a manifest profile."""
    if _character_manager is None:
        return {"error": "Character manager not initialized"}
    profile_name = data.get("profile_name", "")
    if not profile_name:
        return {"error": "profile_name is required"}
    try:
        char = _character_manager.create_character(
            profile_name=profile_name,
            name=data.get("name"),
            auto_index=True,
        )
    except ValueError as e:
        return {"error": str(e)}
    return {
        "id": char.id,
        "profile_name": char.profile_name,
        "name": char.name,
        "adapter": char.adapter,
        "model": char.model,
        "system_prompt_preview": char.system_prompt[:100] + "..." if len(char.system_prompt) > 100 else char.system_prompt,
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
            {
                "id": c.id,
                "profile_name": c.profile_name,
                "name": c.name,
                "adapter": c.adapter,
                "model": c.model,
            }
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
        "profile_name": char.profile_name,
        "name": char.name,
        "adapter": char.adapter,
        "model": char.model,
        "system_prompt": char.system_prompt,
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


# ── React SPA (coexistence mode: /react/*) ──
# Mount React at /react/ unless FRONTEND_MODE=legacy
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
