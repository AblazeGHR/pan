"""Pan Web Channel — FastAPI routes + WebSocket + Dashboard."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse, Response, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import httpx

from packages.core import worker
from packages.core import session as sess
from packages.core.adapters import get_adapter, list_adapters, get_sessions_provider
from packages.core.adapters.validation import (
    AdapterCapabilityError,
    is_valid_model,
    resolve_adapter,
    validate_effort,
    validate_model,
    validate_permission_mode,
    validate_session_settings,
    sanitize_adapter_config,
    supported_settings as _adapter_supported_settings,
    VALID_MCP_TRANSPORTS,
)
from packages.core.adapters.cbc import sessions as cbc_sessions
from packages.core.adapters.cbc.sessions import sanitize_project_dir_name
from packages.core.adapters.kimi import sessions as kimi_sessions
from packages.core.adapters.opencode import sessions as opencode_sessions
from packages.core.config import (
    DEFAULT_PLUGIN_MANIFESTS,
    load_config,
    read_config_file,
    save_config,
)
from packages.core.cli_diagnostics import get_cli_diagnostics
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
    plugin_paths = config.get("plugin_manifests", DEFAULT_PLUGIN_MANIFESTS)
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
_AGENT_TERMINAL_RESULT_STATUSES = frozenset({"done", "error", "cancelled"})


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

# Main-service restart is intentionally a tiny state machine in the current
# process.  The state only prevents double-clicks/racing HTTP requests before
# this process exits; the detached supervisor does the actual stop/start work.
_main_restart_lock = threading.Lock()
_main_restart_pending = False
_main_restart_request_id: str | None = None


def _main_restart_paths() -> dict[str, Path]:
    scripts = _PROJECT_DIR / "scripts"
    return {
        "supervisor": scripts / "restart_pan.ps1",
        "stop": scripts / "stop_pan.bat",
        "start": scripts / "start_pan.bat",
    }


def _main_restart_status() -> dict:
    paths = _main_restart_paths()
    available = os.name == "nt" and all(path.is_file() for path in paths.values())
    missing = [str(path) for path in paths.values() if not path.is_file()]
    with _main_restart_lock:
        pending = _main_restart_pending
        request_id = _main_restart_request_id
    result = {
        "available": available,
        "pending": pending,
        "platform": os.name,
    }
    if request_id:
        result["requestId"] = request_id
    if not available:
        result["reason"] = (
            "main service restart is available only on Windows"
            if os.name != "nt"
            else "restart scripts are missing: " + ", ".join(missing)
        )
    return result


def _clear_main_restart_state(request_id: str) -> None:
    global _main_restart_pending, _main_restart_request_id
    with _main_restart_lock:
        if _main_restart_request_id == request_id:
            _main_restart_pending = False
            _main_restart_request_id = None


def _watch_main_restart(process: subprocess.Popen, request_id: str) -> None:
    """Release the in-process duplicate guard if the supervisor exits early."""
    try:
        process.wait()
    except Exception as exc:  # noqa: BLE001 - the service must stay healthy
        _log(f"[main-restart] supervisor monitor failed: {exc}")
    finally:
        # restart_pan.ps1 has a short launcher hop which starts the actual
        # supervisor and then exits.  Keep the guard through the normal
        # stop/start window so a second HTTP request cannot race that child.
        time.sleep(30)
        _clear_main_restart_state(request_id)


def _launch_main_restart_supervisor(request_id: str) -> subprocess.Popen:
    """Launch a hidden, detached PowerShell supervisor.

    The supervisor is deliberately not awaited here.  It starts a second
    PowerShell process before running stop_pan.bat, so taskkill /T against the
    current Pan process cannot take the restart orchestration with it.
    """
    script = _main_restart_paths()["supervisor"]
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Root",
        str(_PROJECT_DIR),
        "-RequestId",
        request_id,
    ]
    flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    )
    startupinfo = None
    if os.name == "nt" and hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return subprocess.Popen(
        command,
        cwd=str(_PROJECT_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
        startupinfo=startupinfo,
    )

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


async def _replay_agent_results(ws: WebSocket, session_ids: list[str]) -> None:
    """补发 agent 尚未消费的终态结果（成功、失败、取消均不能静默丢失）。"""
    sub = agent_subscriptions.get(ws)
    if sub is None:
        sub = AgentSubscription()
        agent_subscriptions[ws] = sub
    for sid in session_ids:
        s = sess.get(sid)
        if not s or not s.last_result:
            continue
        status = s.last_result.get("status")
        if status not in _AGENT_TERMINAL_RESULT_STATUSES:
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
            "status": status,
            "result": s.last_result.get("result"),
            "taskSeq": latest_seq,
            "replayed": True,
        })
        # 补发成功后再推进游标，避免下次 reconnect 重复补发
        sub.consumed_seq[sid] = max(sub.consumed_seq.get(sid, 0), latest_seq)


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
    w = worker.find_alive_worker_by_session(s.id)
    a = get_adapter(s.adapter)
    config = load_config().get(s.adapter, {})
    ac = s.adapter_config
    mcp_lock_reason = _get_mcp_locked_state(s)
    return {
        "id": s.id,
        "name": s.name,
        "adapter": s.adapter,
        "cliSessionId": s.cli_session_id,
        "model": s.model or a.default_model,
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
        "readonlySession": s.readonly_session,
        "agentLevel": sess.agent_level(s.id),
        "reportSubscriptions": sorted(s.report_subscriptions),
        "qqSubscriptions": sorted(s.qq_subscriptions),
        "workerStatus": w.status if w else None,
        "workerId": w.worker_id if w else None,
        "mcpEnabled": bool(ac.get("mcp_servers")),
        "mcpLocked": mcp_lock_reason is not None,
        "mcpLockReason": mcp_lock_reason,
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

    Since 2026-09-01: also exposes managed / mcpServers / mcpLockReason so the
    sidebar can run the "has subagent" and "is MetaAgent" special filters
    without per-session detail calls (mirrors _session_to_api).
    """
    w = worker.find_alive_worker_by_session(s.id)
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
        "readonlySession": s.readonly_session,
        "agentLevel": sess.agent_level(s.id),
        "lastMessage": last_text,
        "historyTotal": len(s.history),
        "totalUsage": s.total_usage,
        "managed": s.managed,
        "mcpServers": [
            c.get("name")
            for c in (ac.get("mcp_servers") or [])
            if isinstance(c, dict) and c.get("name")
        ],
        "mcpLockReason": _get_mcp_locked_state(s),
        # 设置字段（供前端列表/InputRow 显示真实值，避免未打开设置弹窗时回退默认）
        "model": s.model or a.default_model,
        "permissionMode": s.permission_mode or config.get("permission_mode") or None,
        "alwaysThinkingEnabled": ac.get("always_thinking_enabled", False),
        "effort": ac.get("effort") or config.get("effort", ""),
        "workdir": s.workdir,
    }


def _get_mcp_locked_state(s) -> str | None:
    """Return the MCP lock reason for this session's session_template.

    Returns "always" / "never" when the template locks the MCP toggle,
    None when unlocked (no template, optional mode, or lookup failure).
    """
    if _character_manager is None or not s.session_template:
        return None
    try:
        template = _character_manager.get_session_template(s.session_template)
        if template and template.mcp_mode in ("always", "never"):
            return template.mcp_mode
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


def _guarded_model(a, value) -> str | None:
    """模板 / config.json 回退 model 的宽容守卫。

    与显式请求的硬校验（validate_model → 拒绝）不同：回退值不在 adapter
    可选列表内时丢弃（交还 adapter 默认），记日志但不阻止 session 创建。
    这是既有 stale-model guard 语义（原 kimi-code/kimi-for-coding 场景）。
    """
    if not value:
        return None
    if is_valid_model(a, value):
        return value
    _log(f"[session-config] dropping invalid model {value!r} "
         f"for adapter '{a.name}' (not in supported_models)")
    return None


def _guarded_permission_mode(a, value) -> str | None:
    """模板 / config.json 回退 permissionMode 的宽容守卫（同 _guarded_model）。"""
    if not value:
        return None
    try:
        validate_permission_mode(a, value)
        return value
    except AdapterCapabilityError as exc:
        _log(f"[session-config] dropping invalid permission_mode for adapter "
             f"'{a.name}': {exc}")
        return None


def _build_session_params(
    data: dict,
    *,
    resolve_workdir: bool = True,
    strict_mcp: bool = True,
) -> dict:
    """Extract session creation parameters from request data, with defaults.

    Session config (system_prompt / adapter / model / permission_mode /
    mcp_mode / mcp_servers / pan_access capability flags) comes from a
    session_template —
    either the explicit ``sessionTemplate`` name or the built-in ``default``
    template (config.json session config). ``characterId`` only binds memory/assets.

    ``resolve_workdir=False`` skips creating a workdir under data/workdirs/ —
    used by the import endpoints whose sessions keep the external project /
    workspace path as workdir instead.

    ``strict_mcp=False`` is reserved for importing an existing external CLI
    session. A stale import template may omit MCP rather than preventing the
    external session from being recorded; normal session creation remains
    strict so a configured MCP server can never disappear silently.
    """
    name = data.get("name", "default")
    workdir_name = data.get("workdir") or name

    # Resolve session_template first: the template's adapter participates in
    # the final adapter resolution (explicit request > template > "cbc"), so
    # capability validation must run against the adapter the session will
    # actually use (previously a no-adapter-request + kimi-template request
    # validated against cbc but created a kimi session).
    template_name = data.get("sessionTemplate") or data.get("session_template") or None
    template = None
    if template_name:
        if _character_manager is None:
            raise AdapterCapabilityError(
                f"Unknown session template {template_name!r}: "
                "manifest catalog not loaded"
            )
        template = _character_manager.get_session_template(template_name)
        if template is None:
            available: list[str] = []
            if _character_manager._manifest_config is not None:
                available = [
                    t.name for t in _character_manager._manifest_config.session_templates
                ]
            raise AdapterCapabilityError(
                f"Unknown session template {template_name!r}. "
                f"Available templates: {', '.join(available) or '(none)'}"
            )

    adapter_name = data.get("adapter") or (template.adapter if template else "") or "cbc"
    a = resolve_adapter(adapter_name)
    config = load_config().get(adapter_name, {})

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

    # 显式请求的能力校验：非法值直接拒绝（结构化错误，不静默回退）。
    # 校验在所有回退值解析完成后进行，per-model effort 收窄以最终 model 为准。
    # A named manifest template is an explicit, contractual configuration: if
    # it carries a model, an adapter override that cannot run that model must
    # fail rather than silently replacing the template's requested model.
    # The synthesized default template / config.json fallback keeps the legacy
    # stale-model guard semantics (宽容守卫，不误杀 —— 例如 claude 的 config
    # model 可能不在其 best-effort builtin 列表内)。
    if data.get("model"):
        _final_model = data["model"]
    elif template_name and template is not None and template.model:
        validate_model(a, str(template.model))
        _final_model = template.model
    elif template is not None and template.model:
        # 内置 default 模板：model 已在构造时按 supported_models 守卫过
        # （config guard + adapter 自带 default 守卫），此处信任旧语义。
        _final_model = template.model
    else:
        _final_model = _guarded_model(a, config.get("model")) or a.default_model
    explicit_settings: dict = {}
    if data.get("model"):
        explicit_settings["model"] = data["model"]
    if data.get("permissionMode"):
        explicit_settings["permissionMode"] = data["permissionMode"]
    if data.get("effort"):
        explicit_settings["effort"] = data["effort"]
    if data.get("maxThinkingTokens"):
        explicit_settings["maxThinkingTokens"] = data["maxThinkingTokens"]
    if data.get("alwaysThinkingEnabled"):
        explicit_settings["alwaysThinkingEnabled"] = data["alwaysThinkingEnabled"]
    if explicit_settings:
        validate_session_settings(adapter_name, explicit_settings, current_model=_final_model)

    # thinking / effort 的 config 回退值做宽容守卫：adapter 不支持或值非法时
    # 丢弃回退默认（与 stale-model guard 同语义），只有显式请求才硬拒绝。
    _declared = _adapter_supported_settings(a)
    _explicit_thinking = data.get("alwaysThinkingEnabled")
    if _explicit_thinking is not None:
        _thinking = bool(_explicit_thinking)
    else:
        _config_thinking = bool(config.get("always_thinking_enabled", False))
        _thinking = _config_thinking if (_declared is None or "thinking" in _declared) else False

    _effort = data.get("effort") or ""
    if not _effort:
        _effort = config.get("effort", "") or ""
        if _effort:
            try:
                validate_effort(a, _effort, model=_final_model)
            except AdapterCapabilityError as _exc:
                _log(f"[session-config] dropping config effort for adapter "
                     f"'{a.name}': {_exc}")
                _effort = ""

    params = {
        "name": name,
        # User's explicit adapter wins, then the template's (a no-adapter
        # template parses to ""), else default "cbc". Fix: previously the user
        # selection was ignored — `template.adapter or "cbc"` overwrote a
        # chosen adapter (e.g. kimi) with "cbc" when the template had none.
        "adapter": adapter_name,
        # Priority: explicit request > template > config.json > adapter
        # default. Explicit values are hard-validated above; template/config
        # fallbacks are guard-dropped (stale/invalid → adapter default).
        "model": _final_model,
        "permission_mode": data.get("permissionMode")
        or _guarded_permission_mode(a, template.permission_mode)
        or _guarded_permission_mode(a, config.get("permission_mode"))
        or None,
        "workdir": str(_resolve_workdir(workdir_name)) if resolve_workdir else "",
        "adapter_config": {
            "always_thinking_enabled": _thinking,
            "effort": _effort,
            # maxThinkingTokens 不再从请求写入：没有任何 adapter 消费该字段，
            # 显式传入会在上面的能力校验中被拒绝；字段保留为 None 以兼容
            # 既有落盘结构读取。
            "max_thinking_tokens": None,
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
        except ValueError as exc:
            if not strict_mcp:
                _log(
                    f"Imported MCP server(s) {template.mcp_servers!r} unavailable; "
                    f"continuing without MCP: {exc}"
                )
                params["adapter_config"]["mcp_servers"] = []
                return params
            # Do not create a session that claims to have the default Pan MCP
            # while silently dropping its descriptor.  The API caller needs a
            # concrete configuration error so it can fix the catalog first.
            raise ValueError(
                f"Unable to configure default MCP server(s) "
                f"{template.mcp_servers!r}: {exc}"
            ) from exc

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
    """Resolve MCP server names to full configs from the manifest table.

    Session 的 MCP 配置只接受 manifest 中声明的 server 名称（单一事实源）：
    名称必须是字符串、不允许重复，transport/type 必须在白名单内
    （stdio/http/sse），不接受任意 command/url/env 内联描述符。
    """
    _ensure_manifest_fresh()
    if _character_manager is None or _character_manager._manifest_config is None:
        raise ValueError("MCP manifest not loaded")
    if not isinstance(server_names, list):
        raise ValueError("MCP server names must be a list")
    seen: set[str] = set()
    configs: list[dict] = []
    for name in server_names:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid MCP server name: {name!r}")
        if name in seen:
            raise ValueError(f"Duplicate MCP server: {name!r}")
        seen.add(name)
        for srv in _character_manager._manifest_config.mcp_servers:
            if srv.name == name:
                for _key in ("transport", "type"):
                    _val = getattr(srv, _key, None)
                    if _val and str(_val) not in VALID_MCP_TRANSPORTS:
                        raise ValueError(
                            f"MCP server {name!r} has invalid {_key}: "
                            f"{_val!r}. Allowed: {', '.join(VALID_MCP_TRANSPORTS)}"
                        )
                cfg: dict = {"name": srv.name}
                if srv.command:
                    command = str(srv.command)
                    if not _mcp_command_available(command):
                        raise ValueError(
                            f"MCP server {srv.name!r} command is unavailable: {command}"
                        )
                    cfg["command"] = srv.command
                if srv.args:
                    cfg["args"] = srv.args
                if srv.env:
                    cfg["env"] = srv.env
                if srv.cwd:
                    if not Path(srv.cwd).is_dir():
                        raise ValueError(
                            f"MCP server {srv.name!r} cwd is unavailable: {srv.cwd}"
                        )
                    cfg["cwd"] = srv.cwd
                if srv.url:
                    cfg["url"] = srv.url
                if srv.transport:
                    cfg["transport"] = srv.transport
                if srv.headers:
                    cfg["headers"] = srv.headers
                if srv.type:
                    cfg["type"] = srv.type
                if not srv.command and not srv.url:
                    raise ValueError(
                        f"MCP server {srv.name!r} has no command or URL configured"
                    )
                configs.append(cfg)
                break
        else:
            raise ValueError(f"Unknown MCP server: {name!r}")
    return configs


def _mcp_command_available(command: str) -> bool:
    """Return whether a manifest stdio command can be launched.

    Absolute and path-like commands are checked directly. Bare executable
    names are resolved through PATH, allowing portable declarations such as
    ``python`` while still reporting a useful error before a Claude worker is
    spawned. This is a preflight check, not a probe that starts the MCP server.
    """
    candidate = Path(command)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate.is_file()
    return shutil.which(command) is not None


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
    """Apply model/mode/thinking/effort fields from data to a Session (in-place).

    Validate-first：所有显式设置先整体通过 adapter 能力校验，任一非法即抛
    AdapterCapabilityError 且 **不修改** session（避免半套写入的脏配置）。
    """
    _explicit = {
        key: data[key]
        for key in ("model", "permissionMode", "alwaysThinkingEnabled",
                    "effort", "maxThinkingTokens", "outputMode")
        if key in data
    }
    if _explicit:
        validate_session_settings(s.adapter, _explicit, current_model=s.model)
    if "model" in data:
        s.model = data["model"]
        # Some adapters (currently Codex) expose model-specific effort levels.
        # Switching models must not leave an incompatible effort on the session;
        # an empty value delegates to the native model default.
        if "effort" not in data:
            model_efforts = getattr(get_adapter(s.adapter), "model_efforts", {}).get(str(s.model))
            current_effort = s.adapter_config.get("effort", "")
            if model_efforts and current_effort and current_effort not in model_efforts:
                s.set_adapter_field("effort", "")
    if "permissionMode" in data:
        s.permission_mode = data["permissionMode"] or None
    if "alwaysThinkingEnabled" in data:
        s.set_adapter_field("always_thinking_enabled", data["alwaysThinkingEnabled"])
    if "effort" in data:
        s.set_adapter_field("effort", data["effort"])
    if "maxThinkingTokens" in data:
        s.set_adapter_field("max_thinking_tokens", data["maxThinkingTokens"])
    if "mcpServers" in data:
        # forceMcp:true（UI 强制解除模板锁确认后携带）跳过 always/never 校验。
        _apply_mcp_servers(s, data["mcpServers"], force=bool(data.get("forceMcp")))
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


def _apply_mcp_servers(s: sess.Session, server_names, force: bool = False) -> None:
    """Set session mcp_servers by manifest server names (e.g. ["pan"]).

    Resolves names to full configs via the character manager's manifest table.
    Accepts a list of names, or None/[] to clear. mcp_servers 非空即启用
    （单一事实源），mcp_mode 的 always/never 锁在此处强制执行。
    force=True 跳过该锁（PATCH body 的 forceMcp:true 传入，UI 在用户
    明确确认后用于强制解除 never 锁）。
    """
    enabling = server_names not in (None, [], "")
    if not force and s.session_template and _character_manager is not None:
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


@app.get("/api/main/restart/status")
async def api_main_restart_status():
    """Report whether the safe Windows main-service restart is available."""
    return _main_restart_status()


@app.post("/api/main/restart")
async def api_main_restart():
    """Schedule a detached restart of this Pan instance.

    Returning before the supervisor stops this process is essential: waiting
    for stop/start inside this request would turn the expected disconnect into
    an HTTP failure in the browser.  ``restart_pan.ps1`` owns the subsequent
    stop/start chain and uses this checkout's scripts only.
    """
    global _main_restart_pending, _main_restart_request_id

    status = _main_restart_status()
    if not status["available"]:
        return {
            "ok": False,
            "status": "disabled",
            "error": status.get("reason", "main restart is unavailable"),
        }

    with _main_restart_lock:
        if _main_restart_pending:
            return {
                "ok": False,
                "status": "busy",
                "pending": True,
                "error": "Pan main-service restart is already scheduled",
                "requestId": _main_restart_request_id,
            }
        request_id = uuid.uuid4().hex
        _main_restart_pending = True
        _main_restart_request_id = request_id

    try:
        process = _launch_main_restart_supervisor(request_id)
    except (OSError, ValueError) as exc:
        _clear_main_restart_state(request_id)
        return {
            "ok": False,
            "status": "error",
            "error": f"failed to schedule Pan restart: {exc}",
        }

    threading.Thread(
        target=_watch_main_restart,
        args=(process, request_id),
        name="pan-main-restart-supervisor",
        daemon=True,
    ).start()
    return {
        "ok": True,
        "status": "scheduled",
        "message": "Pan main-service restart scheduled",
        "requestId": request_id,
    }


def _directory_roots() -> list[Path]:
    """Return navigable filesystem roots on the machine running Pan."""
    if sys.platform == "win32":
        # GetLogicalDrives avoids presenting disconnected or nonexistent drive
        # letters.  Keep this behind a helper so the API remains testable on
        # non-Windows hosts.
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [Path(f"{chr(65 + i)}:\\") for i in range(26) if mask & (1 << i)]
    return [Path("/")]


def _resolve_directory(path: str | None) -> Path | None:
    if not path or not path.strip():
        return None
    # strict=True ensures the response never claims a path exists when it does
    # not. Path.resolve also normalizes separators and removes dot segments.
    return Path(path.strip()).resolve(strict=True)


@app.get("/api/directories")
async def list_directories(path: str | None = None):
    """List one directory level on the Pan server (never recursive).

    ``path`` omitted/empty returns the server's filesystem roots.  The API is
    intentionally ready for an allowed-roots check to be inserted after path
    resolution when sandboxing is introduced.
    """
    try:
        current = _resolve_directory(path)
        if current is None:
            roots = _directory_roots()
            entries = [
                {"name": root.name or str(root), "path": str(root), "isDirectory": True}
                for root in roots
            ]
            return {"current": "", "parent": None, "entries": entries}

        if not current.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")
        entries = []
        with os.scandir(current) as scan:
            for item in scan:
                try:
                    if item.is_dir(follow_symlinks=False):
                        entries.append({
                            "name": item.name,
                            "path": str(Path(item.path)),
                            "isDirectory": True,
                        })
                except OSError:
                    # A directory can disappear or become inaccessible during
                    # enumeration; omit only that entry and keep the layer.
                    continue
        entries.sort(key=lambda entry: entry["name"].casefold())
        parent = current.parent if current.parent != current else None
        return {
            "current": str(current),
            "parent": str(parent) if parent else None,
            "entries": entries,
        }
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Directory does not exist") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid directory path") from exc


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

async def _replay_pending_interactions(
    ws: WebSocket, session_ids: list[str] | None = None,
) -> None:
    """Restore live native prompts to a dashboard that just reconnected.

    Only live workers are considered: the snapshot is a UI replay cache, while
    the open request still belongs to the native process/future.  A
    dead/restarted worker cannot safely receive the old response.
    """
    selected = {str(sid) for sid in session_ids or []}
    for w in worker.list_workers():
        if selected and w.session_id not in selected:
            continue
        if not worker._process_alive(w):
            continue
        status_event = worker.native_status_event(w)
        if status_event is not None:
            await ws.send_json({
                "type": "worker.stream",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "generation": w.generation,
                "event": status_event,
                "replayed": True,
            })
        usage_event = worker.native_usage_event(w)
        if usage_event is not None:
            await ws.send_json({
                "type": "worker.stream",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "generation": w.generation,
                "event": usage_event,
                "replayed": True,
            })
        rate_limits_event = worker.native_rate_limits_event(w)
        if rate_limits_event is not None:
            await ws.send_json({
                "type": "worker.stream",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "generation": w.generation,
                "event": rate_limits_event,
                "replayed": True,
            })
        for native_event in (
            worker.native_plan_event(w),
            worker.native_diff_event(w),
        ):
            if native_event is not None:
                await ws.send_json({
                    "type": "worker.stream",
                    "workerId": w.worker_id,
                    "sessionId": w.session_id,
                    "generation": w.generation,
                    "event": native_event,
                    "replayed": True,
                })
        for event in worker.pending_interaction_events(w):
            await ws.send_json({
                "type": "worker.stream",
                "workerId": w.worker_id,
                "sessionId": w.session_id,
                "generation": w.generation,
                "event": event,
                "replayed": True,
            })

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
                    client_message_id = msg.get("clientMessageId")
                    if client_message_id is not None and not isinstance(client_message_id, str):
                        await ws.send_json({"type": "user_inject.rejected",
                                            "sessionId": session_id,
                                            "message": "clientMessageId must be a string"})
                        continue
                    if isinstance(client_message_id, str) and len(client_message_id) > 512:
                        await ws.send_json({"type": "user_inject.rejected",
                                            "sessionId": session_id,
                                            "clientMessageId": client_message_id,
                                            "message": "clientMessageId is too long"})
                        continue
                    # Session is the durable address.  A dead/no worker must
                    # not silently drop a dashboard message; send_session
                    # persists it and lets the global watchdog recover.
                    result = await worker.send_session(
                        session_id, text, source="user",
                        client_message_id=client_message_id,
                    )
                    if result.get("status") == "error":
                        await ws.send_json({"type": "user_inject.rejected",
                                            "sessionId": session_id,
                                            "clientMessageId": client_message_id,
                                            "message": result.get("result", "send failed")})
                    else:
                        await ws.send_json({"type": "user_inject.accepted",
                                            "sessionId": session_id,
                                            "workerId": result.get("workerId"),
                                            "clientMessageId": client_message_id})
            elif msg_type == "worker_control":
                session_id = msg.get("sessionId")
                worker_id = msg.get("workerId")
                control = msg.get("control")
                if session_id and isinstance(control, dict):
                    result = await worker.send_session_control(session_id, control)
                    if isinstance(result, str):
                        await ws.send_json({"type": "error", "message": result})
                    elif result is None:
                        await ws.send_json({"type": "error", "message": "Worker not found"})
                elif worker_id and isinstance(control, dict):
                    err = await worker.send_control_message(worker_id, control)
                    if err:
                        await ws.send_json({"type": "error", "message": err})
            elif msg_type == "sync_interactive":
                # Optional sessionIds narrows the replay; omitted means all
                # live workers visible to this dashboard, matching /ws's
                # existing broadcast scope.
                raw_session_ids = msg.get("sessionIds")
                if raw_session_ids is not None and not isinstance(raw_session_ids, list):
                    await ws.send_json({
                        "type": "error",
                        "message": "sessionIds must be a list",
                    })
                    continue
                await _replay_pending_interactions(ws, raw_session_ids)
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
                # 补发各 session 未消费的终态 worker.result（成功/失败/取消）
                await _replay_agent_results(ws, msg.get("sessionIds") or [])

            elif msg_type == "task":
                session_id = msg.get("sessionId")
                text = msg.get("text")
                if session_id and text:
                    w = worker.find_alive_worker_by_session(session_id)
                    if not w:
                        result = await worker.create_worker(session_id)
                        if isinstance(result, str):
                            await ws.send_json({"type": "error", "message": result})
                            continue
                        else:
                            w = result
                    err = await worker.send_task(w.worker_id, text, source="agent")
                    if err:
                        await ws.send_json({"type": "error", "message": err})

            elif msg_type == "spawn":
                try:
                    params = _build_session_params(msg)
                except ValueError as exc:
                    await ws.send_json({"type": "error", "message": str(exc)})
                    continue
                # 名称校验与 HTTP spawn 对齐（缺名/重名此前会静默建出重复名 session）
                err_name = _check_session_name(params.get("name", "default"))
                if err_name:
                    await ws.send_json({"type": "error", "message": err_name})
                    continue
                s = sess.create(**params)
                result = await worker.create_worker(s.id)
                if isinstance(result, str):
                    await ws.send_json({"type": "error", "message": result})
                else:
                    await ws.send_json({
                        "type": "worker.spawned",
                        "sessionId": s.id,
                        "workerId": result.worker_id,
                        "generation": result.generation,
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
                result = await worker.kill_session_worker(session_id)
                if isinstance(result, str):
                    await ws.send_json({"type": "error", "message": result})

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
        w = worker.find_alive_worker_by_session(m.id)
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


# ── Agent queue (session.queue_pending, normalized view) ──
#
# queue_pending 是异构落盘队列：task 项（type=task，自带 uuid id）、report 项
# （普通报告有 type=report，旧行可能无 type，zombie 报告保留 type=zombie）、
# qq 提醒项（type=qq）。
# 这里序列化为统一的 AgentQueueItem 供前端 SendQueuePanel 渲染，不改存储结构。
# 新格式的所有 queue item 都有自己的 id。旧 report/QQ 行没有 id 时仍回退到
# canonical hash，以便升级期间 GET/DELETE 不失联。

_REPORT_TEXT_MAX = 200


def _queue_item_id(item: dict) -> str:
    """Stable id for a queue_pending item (new rows own uuid; old rows hash)."""
    if item.get("id"):
        return str(item["id"])
    try:
        # deliveryState changes during hand-off; it is bookkeeping, not item
        # identity.  Keep report/QQ ids stable so a stale panel action still
        # addresses the same durable item.
        identity = {k: v for k, v in item.items() if k not in {
            "deliveryState", "reservedBy", "reservedGeneration", "reservedAt",
            "deliveryAttempts", "nextAttemptAt", "lastDeliveryError",
        }}
        canonical = json.dumps(identity, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        canonical = repr(item)
    return "sha1:" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _queue_dispatch_state(s, item: dict) -> str:
    """Expose the durable state without deriving it from Worker memory."""
    return worker._delivery_state(item)


def _serialize_queue_item(item, session=None) -> dict | None:
    """queue_pending item → AgentQueueItem；无法识别的形状返回 None（跳过）。"""
    if not isinstance(item, dict):
        return None
    t = item.get("type")
    # Before durable task envelopes were introduced, user/agent messages were
    # persisted as {text, source} without a type.  They are tasks, never
    # reports: treating a no-type text item as a report is what made a message
    # show up under the Agent queue (and eventually acquire an @@@@by agent
    # rendering after a worker restart).  Mirror worker._migrate_legacy_task_items
    # here so the queue API is safe even before the next worker recovery tick.
    if t == "task" or (t is None and isinstance(item.get("text"), str)):
        source = worker._task_source(item) or "user"
        meta = {
            "seq": item.get("seq"),
            "taskId": item.get("taskId"),
            "dispatchState": _queue_dispatch_state(session, item) if session else worker._delivery_state(item),
        }
        if item.get("sourceSessionId") is not None:
            meta["sourceSessionId"] = item.get("sourceSessionId")
        return {
            "id": _queue_item_id(item),
            "kind": "task",
            "text": item.get("text") if isinstance(item.get("text"), str) else "",
            "createdAt": 0,
            "source": source,
            "meta": meta,
        }
    if t == "qq":
        return {
            "id": _queue_item_id(item),
            "kind": "qq",
            "text": item.get("text") if isinstance(item.get("text"), str) else "",
            "source": item.get("source") or "qq",
            "createdAt": 0,
            "meta": {
                "qqTarget": item.get("qqTarget"),
                "time": item.get("time"),
                "dispatchState": _queue_dispatch_state(session, item) if session else worker._delivery_state(item),
            },
        }
    # 非 task/qq 一律按 report 处理（普通报告无 type 字段、zombie 为 type=zombie），
    # 与 worker 消费端分类（type != "task" 即报告）保持一致；完全无 result 的
    # 畸形项跳过。
    if "result" not in item and t is not None:
        return None
    result = item.get("result")
    if isinstance(result, str):
        text = result
    elif result is None:
        text = ""
    else:
        try:
            text = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            text = repr(result)
    if len(text) > _REPORT_TEXT_MAX:
        text = text[:_REPORT_TEXT_MAX] + "…"
    meta = {
        "status": item.get("status"),
        "taskId": item.get("taskId"),
        "workerId": item.get("workerId"),
        "dispatchState": _queue_dispatch_state(session, item) if session else worker._delivery_state(item),
    }
    if item.get("sourceSessionId") is not None:
        meta["sourceSessionId"] = item.get("sourceSessionId")
    return {
        "id": _queue_item_id(item),
        "kind": "report",
        "text": text,
        "source": item.get("source") or "report",
        "createdAt": 0,
        "meta": meta,
    }


def _session_queue_items(s) -> list[dict]:
    items = []
    for it in s.queue_pending or []:
        # reserved/writing/sent rows are internal hand-off states.  They stay
        # durable for recovery but are intentionally hidden from the pending
        # queue view; newly appended queued rows therefore remain visible while
        # another item is running.
        if isinstance(it, dict) and worker._delivery_state(it) != worker._DELIVERY_QUEUED:
            continue
        si = _serialize_queue_item(it, s)
        if si is not None:
            items.append(si)
    return items


@app.get("/api/sessions/{session_id}/queue")
async def api_session_queue(session_id: str):
    """Normalized agent queue (session.queue_pending) for the frontend panel."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    return {"items": _session_queue_items(s)}


@app.delete("/api/sessions/{session_id}/queue/{item_id}")
async def api_session_queue_delete(session_id: str, item_id: str):
    """Remove one item from the persisted queue by its normalized id."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    pending = s.queue_pending or []
    target = None
    # Pass 1: direct id match (task items carry their own uuid id).
    for it in pending:
        if isinstance(it, dict) and it.get("id") == item_id:
            target = it
            break
    # Pass 2: content-hash fallback for old rows that predate queue item ids.
    if target is None:
        for it in pending:
            if isinstance(it, dict) and _queue_item_id(it) == item_id:
                target = it
                break
    if target is None:
        return {"ok": False, "error": "not_found"}
    if worker._delivery_state(target) != worker._DELIVERY_QUEUED:
        return {"ok": False, "error": "queue_item_not_pending"}
    s.queue_pending = [it for it in pending if it is not target]
    await sess.save_async(s)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/queue/{item_id}/retry")
async def api_session_queue_retry(session_id: str, item_id: str):
    """Retry the original durable item after a failed/paused hand-off."""
    result = await worker.retry_pending_item(session_id, item_id)
    if isinstance(result, str):
        return {"ok": False, "error": result}
    if worker._delivery_state(result) != worker._DELIVERY_QUEUED:
        return {
            "ok": False,
            "error": "queue_item_not_pending",
            "status": worker._delivery_state(result),
            "queueItemId": result.get("queueItemId"),
        }
    return {"ok": True, "item": _serialize_queue_item(result, sess.get(session_id))}


@app.patch("/api/sessions/{session_id}/queue/order")
async def api_session_queue_order(session_id: str, data: dict):
    """Reorder task items within the persisted queue (report/qq keep their slots).

    Body: {"order": ["id1", ...]} — the relative order of task ids in `order`
    becomes the new task sequence; tasks not mentioned keep their relative
    order at the end of the task run. Returns the reordered normalized items.
    """
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    order = data.get("order")
    if not isinstance(order, list):
        return {"error": "order (array of item ids) required"}
    pending = list(s.queue_pending or [])
    by_id: dict[str, dict] = {}
    for it in pending:
        if (isinstance(it, dict) and it.get("type") == "task"
                and worker._delivery_state(it) == worker._DELIVERY_QUEUED):
            by_id.setdefault(_queue_item_id(it), it)
    ordered: list[dict] = []
    seen: set[int] = set()
    for iid in order:
        it = by_id.get(str(iid))
        if it is not None and id(it) not in seen:
            ordered.append(it)
            seen.add(id(it))
    for it in pending:  # tasks absent from `order` keep relative order at the end
        if (isinstance(it, dict) and it.get("type") == "task"
                and worker._delivery_state(it) == worker._DELIVERY_QUEUED
                and id(it) not in seen):
            ordered.append(it)
            seen.add(id(it))
    task_iter = iter(ordered)
    s.queue_pending = [
        next(task_iter, it)
        if (isinstance(it, dict) and it.get("type") == "task"
            and worker._delivery_state(it) == worker._DELIVERY_QUEUED) else it
        for it in pending
    ]
    await sess.save_async(s)
    return {"items": _session_queue_items(s)}


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
        w = worker.find_alive_worker_by_session(session_id)
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

    # ── 能力预校验：新 adapter 与 model / settings 组合必须成立 ──
    # 切 adapter 时连继承自 A 的设置也一并核验，避免交接出一个必然启动失败
    # 的 B（不静默换模型/清设置）。
    session_a = sess.get(session_id)
    if session_a is None:
        return {"error": f"Session {session_id} not found"}
    new_adapter_name = adapter or (session_a.adapter if copy_settings else "cbc")
    switched = new_adapter_name != session_a.adapter
    try:
        new_adapter = resolve_adapter(new_adapter_name)
        if model:
            validate_model(new_adapter, model)
        elif copy_settings and session_a.model and switched:
            try:
                validate_model(new_adapter, session_a.model)
            except AdapterCapabilityError:
                return {"error": (
                    f"handoff to adapter '{new_adapter_name}' would inherit "
                    f"model '{session_a.model}' from session A (adapter "
                    f"'{session_a.adapter}'), which does not support it. Pass "
                    f"an explicit supported 'model' or use copySettings=false."
                )}
        if permission_mode:
            validate_permission_mode(new_adapter, permission_mode)
        elif copy_settings and session_a.permission_mode and switched:
            try:
                validate_permission_mode(new_adapter, session_a.permission_mode)
            except AdapterCapabilityError:
                return {"error": (
                    f"handoff to adapter '{new_adapter_name}' would inherit "
                    f"permissionMode '{session_a.permission_mode}' from session "
                    f"A (adapter '{session_a.adapter}'), which does not support "
                    f"it. Pass an explicit 'permissionMode' or use "
                    f"copySettings=false."
                )}
    except AdapterCapabilityError as exc:
        return {"error": str(exc)}

    result = sess.handoff_session(
        session_id, handoff_prompt,
        copy_settings=copy_settings, adapter=adapter, model=model,
        permission_mode=permission_mode,
    )
    if isinstance(result, str):
        return {"error": result}
    a, b = result
    # 跨 adapter 复制设置时，清洗 adapter_config：源 adapter 的 effort /
    # thinking / output_mode / maxThinkingTokens 对新 adapter 不成立的降级为
    # 默认（复制残值的既有降级语义；显式传参已在上面硬校验）。
    if switched and copy_settings:
        b.adapter_config = sanitize_adapter_config(
            new_adapter_name, b.adapter_config, model=b.model)
        sess.save(b)
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
        "modelEfforts": getattr(a, "model_efforts", {}),
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


# ── Worker settings (config.json worker, hot-applied) ──

_WORKER_SETTING_KEYS = ("timeout_sec", "task_timeout_sec", "idle_sec")


@app.put("/api/settings/worker")
async def api_put_settings_worker(data: dict):
    """Merge worker lifecycle timeouts into config.json's ``worker`` object.

    Body may carry any subset of {timeout_sec, task_timeout_sec, idle_sec};
    each provided value must be a positive number (seconds). Provided keys
    are merged over the current worker section (unprovided keys and other
    fields in the section are kept) and persisted atomically, then
    worker.reload_worker_config() re-reads the file so the running server
    applies the new values without a restart. Returns the {before, after}
    diff in the same shape as the ``worker`` entry of /api/config/reload.
    """
    patch: dict = {}
    for k in _WORKER_SETTING_KEYS:
        if k not in data:
            continue
        try:
            v = float(data[k])
        except (TypeError, ValueError):
            return {"error": f"worker.{k} must be a number (seconds)"}
        if not (math.isfinite(v) and v > 0):  # rejects 0 / negative / inf / nan
            return {"error": f"worker.{k} must be a positive number (seconds)"}
        patch[k] = v
    if not patch:
        return {
            "error": "no worker keys provided (expected any of: "
            + ", ".join(_WORKER_SETTING_KEYS)
            + ")"
        }
    raw = read_config_file()
    section = dict(raw.get("worker") or {})
    section.update(patch)
    raw["worker"] = section
    save_config(raw)
    return worker.reload_worker_config()


# ── Remote tunnel (cloudflared, scripts/start_cf.ps1) ──
#
# Pan's own tunnel cloudflared is started by scripts/start_cf.ps1 with a
# generated temp yml (%TEMP%/pan_cf_config_<port>.yml). PidFiles written at
# start are deleted by start_pan.bat, so running processes are identified by
# command line instead — the temp-yml marker is unique to Pan's tunnel and
# never matches service installs (e.g. cloudflared-ssh).

_PAN_TUNNEL_MARKER = "pan_cf_config_"


def _matches_pan_tunnel(name: str, cmdline: str) -> bool:
    """Pure matcher for Pan's own tunnel cloudflared process.

    True only when the process is a cloudflared binary AND its command line
    references the temp tunnel config marker (pan_cf_config_<port>.yml).
    Service processes (cloudflared-ssh etc.) never reference that file and
    are never matched — mirrors scripts/stop_pan.bat's precise 5c fallback.
    """
    if not cmdline:
        return False
    base = name.lower()
    if not base.startswith("cloudflared"):
        return False
    return _PAN_TUNNEL_MARKER in cmdline


def _find_pan_tunnel_processes() -> list[dict]:
    """Return [{pid, name, cmdline}] for Pan's cloudflared tunnel processes."""
    procs: list[dict] = []
    try:
        import psutil

        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = p.info
                cmdline = " ".join(info.get("cmdline") or [])
                if _matches_pan_tunnel(info.get("name") or "", cmdline):
                    procs.append({
                        "pid": info["pid"],
                        "name": info.get("name") or "",
                        "cmdline": cmdline,
                    })
            except Exception:
                continue
    except ImportError:
        # PowerShell fallback (same matcher as scripts/stop_pan.bat 5c).
        try:
            out = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\""
                    " | Select-Object ProcessId,CommandLine | ConvertTo-Json",
                ],
                capture_output=True, text=True, timeout=15,
            )
            data = json.loads(out.stdout or "null")
            items = data if isinstance(data, list) else ([data] if data else [])
            for it in items:
                cmdline = it.get("CommandLine") or ""
                if _matches_pan_tunnel("cloudflared.exe", cmdline):
                    procs.append({
                        "pid": int(it["ProcessId"]),
                        "name": "cloudflared.exe",
                        "cmdline": cmdline,
                    })
        except Exception as e:
            _log(f"[remote] cloudflared process scan failed: {e}")
    return procs


def _kill_pan_tunnel_processes(procs: list[dict]) -> list[int]:
    """Kill the given processes (tree-kill); returns pids actually killed."""
    killed: list[int] = []
    for pr in procs:
        try:
            r = subprocess.run(
                ["taskkill", "/PID", str(pr["pid"]), "/T", "/F"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                killed.append(pr["pid"])
            else:
                _log(f"[remote] kill pid {pr['pid']} failed: {r.stderr.strip()}")
        except Exception as e:
            _log(f"[remote] kill pid {pr['pid']} error: {e}")
    return killed


@app.get("/api/remote/status")
async def api_remote_status():
    """Remote tunnel status for the App Settings modal.

    ``available`` reflects the raw on-disk config (remote section present);
    ``enabled`` comes from the merged config. ``running`` = a Pan tunnel
    cloudflared process was found by command-line match.
    """
    config = load_config()
    raw = read_config_file()
    remote = config.get("remote") or {}
    return {
        "available": "remote" in raw,
        "enabled": bool(remote.get("enabled")),
        "provider": remote.get("provider"),
        "quickTunnel": bool(remote.get("quick_tunnel")),
        "protocol": remote.get("protocol") or "",
        "port": config.get("port"),
        "running": bool(_find_pan_tunnel_processes()),
    }


@app.post("/api/remote/restart")
async def api_remote_restart():
    """Restart Pan's cloudflared tunnel via scripts/start_cf.ps1.

    Only processes whose command line carries the temp-yml marker are killed
    (never the cloudflared-ssh service). Restarting re-runs start_cf.ps1 —
    the same entry point start_pan.bat uses — so the freshly generated temp
    yml picks up current config.json values (port + remote.protocol).
    """
    config = load_config()
    remote = config.get("remote") or {}
    if not remote.get("enabled"):
        return {"ok": False, "error": "remote is not enabled in config.json"}

    killed = _kill_pan_tunnel_processes(_find_pan_tunnel_processes())

    script = _PROJECT_DIR / "scripts" / "start_cf.ps1"
    if not script.exists():
        return {"ok": False, "error": f"start script not found: {script}",
                "killed": killed}
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script)],
            capture_output=True, text=True, timeout=30,
            cwd=str(_PROJECT_DIR),
        )
    except Exception as e:
        return {"ok": False, "error": str(e), "killed": killed}
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[-500:]
        return {"ok": False, "error": f"start_cf.ps1 failed: {err}",
                "killed": killed}

    # Give cloudflared a moment to appear, then confirm via process scan.
    await asyncio.sleep(2)
    restarted = bool(_find_pan_tunnel_processes())
    return {"ok": True, "killed": killed, "restarted": restarted}


# ── Config hot-reload ──

def _reload_adapter_models() -> tuple[list[dict], list[str]]:
    """Invalidate adapter model caches and return count diffs and errors."""
    adapters_out = []
    errors: list[str] = []
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
    return adapters_out, errors


@app.post("/api/codex/refresh-official-models")
async def api_codex_refresh_official_models():
    """Replace the Codex whitelist with the visible official model catalog."""
    try:
        completed = subprocess.run(
            ["codex", "debug", "models"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(_PROJECT_DIR),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="codex debug models timed out")
    except OSError as e:
        raise HTTPException(status_code=502, detail=f"failed to run codex: {e}")

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "command failed").strip()
        raise HTTPException(status_code=502, detail=f"codex debug models failed: {message[-500:]}")
    try:
        catalog = json.loads(completed.stdout)
        if isinstance(catalog, dict):
            catalog = catalog.get("models")  # actual `codex debug models` shape
        if not isinstance(catalog, list):
            raise ValueError("expected a JSON object with models[] or a JSON array")
        models = []
        for item in catalog:
            if not isinstance(item, dict):
                raise ValueError("catalog entries must be objects")
            if item.get("visibility") in (None, "list"):
                slug = item.get("slug")
                if not isinstance(slug, str) or not slug:
                    raise ValueError("visible catalog entry has no valid slug")
                models.append(slug)
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"invalid codex model catalog: {e}")

    before = list(get_adapter("codex").supported_models)
    raw = read_config_file()
    codex = dict(raw.get("codex") or {})
    codex["models"] = models
    raw["codex"] = codex
    save_config(raw)

    _, reload_errors = _reload_adapter_models()
    if reload_errors:
        raise HTTPException(status_code=500, detail="; ".join(reload_errors))
    after = list(get_adapter("codex").supported_models)
    return {"ok": True, "before": before, "after": after}

@app.post("/api/config/reload")
async def api_config_reload(data: dict | None = None):
    """Force a config.json hot-reload (adapters / worker / plugin / memory).

    config.json is re-read from disk on every load_config() call, but a few
    things are read once and then cached: the adapters' class-level model-list
    caches (codex/opencode/cbc with TTL, kimi/claude permanent), worker.py's
    module-level lifecycle timeouts, worker.py's memory-injection switch
    (``memory.enabled``), and the registered plugin manifest list
    (``plugin_manifests``, fixed at lifespan). This endpoint invalidates all
    of them so edits to config.json take effect without a server restart —
    same style as POST /api/manifest/reload.

    Body (optional): ``{"scope": "adapters" | "worker" | "plugin" | "memory"
    | "all"}`` — default "all". Idempotent: repeated calls just re-read the
    same config. Per-item failures are collected into ``errors`` and reported
    with ``reloaded: false`` instead of a 500. The response always carries
    ``requiresRestart`` — fields that are startup-frozen by nature and can
    never hot-apply (frontend route mounting, bound port, logging handlers,
    the external remote tunnel process).
    """
    scope = (data or {}).get("scope") or "all"
    if scope not in ("adapters", "worker", "plugin", "memory", "all"):
        return {"reloaded": False, "error": f"Unknown scope: {scope}"}

    result: dict = {"reloaded": True}
    errors: list[str] = []

    if scope in ("adapters", "all"):
        adapters_out, adapter_errors = _reload_adapter_models()
        errors.extend(adapter_errors)
        result["adapters"] = adapters_out

    if scope in ("worker", "all"):
        try:
            result["worker"] = worker.reload_worker_config()
        except Exception as e:
            errors.append(f"worker: {e}")

    if scope in ("memory", "all"):
        try:
            result["memory"] = worker.reload_memory_config()
        except Exception as e:
            errors.append(f"memory: {e}")

    if scope in ("plugin", "all"):
        if _character_manager is None:
            errors.append("plugin: character manager not initialized")
        else:
            before_paths = list(_character_manager._plugin_paths)
            # Same default as lifespan: a missing key loads the project and
            # first-party MCP manifests; an explicit [] clears all plugins.
            new_paths = list(
                load_config().get("plugin_manifests", DEFAULT_PLUGIN_MANIFESTS)
            )
            cfg, plug_errors = _character_manager.reload_plugin_paths(new_paths)
            plugin_entry: dict = {"before": before_paths, "after": new_paths}
            if cfg is None:
                # Aborted: previous paths + config kept by the manager.
                plugin_entry["applied"] = False
                plugin_entry["errors"] = plug_errors
                errors.extend(f"plugin: {e}" for e in plug_errors)
            else:
                plugin_entry["applied"] = True
                plugin_entry["sessionTemplates"] = len(cfg.session_templates)
                plugin_entry["mcpServers"] = len(cfg.mcp_servers)
                plugin_entry["characters"] = len(cfg.character_templates)
                plugin_entry["commandRoutes"] = len(cfg.command_routes)
            result["plugin"] = plugin_entry

    # Startup-frozen fields a config.json edit can never hot-apply.
    result["requiresRestart"] = ["frontend", "port", "logging", "remote"]

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
        # 已有 Session 的 spawn 不接受切换 adapter（此前被静默忽略）。
        # 换 adapter 是结构性切换（进程 argv/协议都不同），必须走 handoff。
        req_adapter = data.get("adapter")
        if req_adapter and req_adapter != s.adapter:
            return {"error": (
                f"Session '{s.name}' uses adapter '{s.adapter}' and cannot "
                f"be spawned with adapter '{req_adapter}'. Adapter switching "
                f"requires POST /api/sessions/{session_id}/handoff."
            )}
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
        try:
            params = _build_session_params(data)
        except ValueError as e:
            return {"error": str(e)}
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


def _request_source_metadata(data: dict, default: str = "agent"):
    """Parse the two independent source fields at an HTTP boundary."""
    source_type, error = worker._normalize_source_type(data.get("source"), default)
    if error:
        return None, None, {"error": error}
    source_session_id, error = worker._normalize_source_session_id(
        data.get("sourceSessionId"))
    if error:
        return None, None, {"error": error}
    return source_type, source_session_id, None


def _source_access_error(target, source_session_id):
    if (target is not None and source_session_id
            and source_session_id != target.id and target.readonly_session
            and target.managed_by == source_session_id):
        return {"ok": False, "error": {"code": "readonly_session",
                "message": worker.READONLY_SESSION_ERROR}}
    return None


@app.post("/api/task")
async def api_task(data: dict):
    """Send a task to a Worker by worker_id or session_id."""
    worker_id = data.get("workerId")
    session_id = data.get("sessionId")
    source_type, source_session_id, source_error = _request_source_metadata(data)
    if source_error:
        return source_error
    if session_id:
        target = sess.get(session_id)
        if not target:
            return {"error": f"Session {session_id} not found"}
        denied = _source_access_error(target, source_session_id)
        if denied:
            return denied

    if not worker_id and session_id:
        w = worker.find_alive_worker_by_session(session_id)
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
            "generation": result.generation,
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

    if not session_id:
        resolved = worker.get_worker(worker_id)
        session_id = resolved.session_id if resolved else None
    if session_id:
        target = sess.get(session_id)
        if target:
            denied = _source_access_error(target, source_session_id)
            if denied:
                return denied
    send_kwargs = {"source": source_type}
    if source_session_id is not None:
        send_kwargs["source_session_id"] = source_session_id
    err = await worker.send_task(worker_id, text, **send_kwargs)
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
                    err = await worker.send_task(worker_id, text, **send_kwargs)
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
    target = sess.get(session_id)
    source_type, source_session_id, source_error = _request_source_metadata(data)
    if source_error:
        return source_error
    if source_session_id and not target:
        return {"error": f"Session {session_id} not found"}
    denied = _source_access_error(target, source_session_id)
    if denied:
        return denied
    client_message_id = data.get("clientMessageId")
    if client_message_id is not None and not isinstance(client_message_id, str):
        return {"error": "clientMessageId must be a string"}
    if isinstance(client_message_id, str) and len(client_message_id) > 512:
        return {"error": "clientMessageId is too long"}
    send_kwargs = {
        "source": source_type,
        "force": bool(data.get("force")),
        "client_message_id": client_message_id,
    }
    if source_session_id is not None:
        send_kwargs["source_session_id"] = source_session_id
    result = await worker.send_session(session_id, text, **send_kwargs)
    if isinstance(result, dict) and result.get("status") == "error":
        return {"error": result.get("result") or "send failed"}
    return result


@app.post("/api/notify")
async def api_notify(data: dict):
    """[internal/MCP-only] 向 session 投递一条提醒（MCP agent_notify 专用）。

    不作为通用对外 API（README/API 文档不宣传）；隔离由 MCP 层
    _check_access 实施（仅允许调用方自己或其 managed 的 session），本端点
    与 /api/send 同约定不检查 pan_access。

    Body: {"targetSessionId": <目标 session id>, "text": <提醒正文>,
           "source"?: <来源类型，如 agent>,
           "sourceSessionId"?: <来源 session id>}

    内部走 worker.enqueue_notice：与 report 相同的持久化投递链路
    （queue_pending 落盘 + 唤醒 + 无活 worker 立即 auto-spawn，不等
    watchdog tick）。区别于 /api/send：投递的是「通知」而非任务消息。
    """
    target = (data.get("targetSessionId") or data.get("sessionId") or "").strip()
    text = data.get("text")
    if not target or not text:
        return {"error": "targetSessionId and text are required"}
    target_session = sess.get(target)
    if not target_session:
        return {"error": f"Session {target} not found"}
    source_type, source_session_id, source_error = _request_source_metadata(data)
    if source_error:
        return source_error
    denied = _source_access_error(target_session, source_session_id)
    if denied:
        return denied
    notice_kwargs = {"source": source_type}
    if source_session_id is not None:
        notice_kwargs["source_session_id"] = source_session_id
    result = await worker.enqueue_notice(target, str(text),
                                         **notice_kwargs)
    if isinstance(result, dict) and not result.get("ok", True):
        return {"error": (result.get("error") or {}).get("message", "notify failed")}
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
    source_type, source_session_id, source_error = _request_source_metadata(data)
    if source_error:
        return {"status": "error", "result": source_error["error"]}
    target = sess.get(session_id)
    if not target:
        return {"status": "error", "result": f"Session {session_id} not found"}
    denied = _source_access_error(target, source_session_id)
    if denied:
        return {"status": "error", "result": denied["error"]["message"]}
    return await worker.assign(session_id, text,
                               source=source_type,
                               source_session_id=source_session_id,
                               task_id=task_id)


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
    # A deleted manager may remain in historical data as a dangling reference;
    # treat that target as unmanaged so it can be recovered.
    if (target.managed_by and target.managed_by != manager_id
            and sess.get(target.managed_by) is not None):
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
           "target_id": <QQ 号 / 群号>, "bot_uin"?: <bot QQ 号>}

    订阅后，该 QQ 会话每次收到新消息（selective 模式入 inbox）都会推送一条
    `@@@@by qq` 提醒到本 session 的落盘队列 queue_pending 并唤醒其 worker。

    bot_uin 可选（多账号）：订阅粒度为「某 bot 的某用户/群」，订阅键
    `<type>:<id>@<bot_uin>`；缺省订阅键 `<type>:<id>`（不区分 bot，任何 bot
    收到都提醒，兼容旧订阅）。
    """
    session_id = (data.get("sessionId") or "").strip()
    target_type = (data.get("target_type") or "").strip().lower()
    target_id = (data.get("target_id") or "").strip()
    bot_uin = (data.get("bot_uin") or "").strip()
    if not session_id or not target_id or target_type not in ("user", "group"):
        return {"error": "sessionId, target_type(user|group) and target_id are required"}
    s = sess.get(session_id)
    if not s:
        return {"error": f"Session {session_id} not found"}
    target_key = f"{target_type}:{target_id}@{bot_uin}" if bot_uin else f"{target_type}:{target_id}"
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
    """取消订阅某 QQ 会话的 inbox 更新提醒。

    bot_uin 可选（多账号）：解绑 `<type>:<id>@<bot>` 订阅键；缺省解绑不区分
    bot 的旧键 `<type>:<id>`。
    """
    session_id = (data.get("sessionId") or "").strip()
    target_type = (data.get("target_type") or "").strip().lower()
    target_id = (data.get("target_id") or "").strip()
    bot_uin = (data.get("bot_uin") or "").strip()
    if not session_id or not target_id or target_type not in ("user", "group"):
        return {"error": "sessionId, target_type(user|group) and target_id are required"}
    s = sess.get(session_id)
    if not s:
        return {"error": f"Session {session_id} not found"}
    target_key = f"{target_type}:{target_id}@{bot_uin}" if bot_uin else f"{target_type}:{target_id}"
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
           "text": ..., "time": ..., "bot_uin"?: <bot QQ 号>}

    找到所有订阅该 QQ 会话的 session，各推送一条 `@@@@by qq` 提醒到其
    queue_pending 并唤醒 worker。bot_uin 可选（多账号来源标注）：同时命中
    不区分 bot 的旧订阅键与 `<type>:<id>@<bot>` 精确订阅键。返回投递数量。
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
        bot_uin=(data.get("bot_uin") or ""),
    )
    return {"ok": True, "delivered": delivered}


# 复用 AsyncClient：避免每个代理请求新建连接（Windows 下 async httpx 首次
# 初始化/连接池创建可达数秒，复用后仅首次有开销）。
_qq_plugin_client: httpx.AsyncClient | None = None


def _qq_plugin_client_get() -> httpx.AsyncClient:
    global _qq_plugin_client
    if _qq_plugin_client is None:
        _qq_plugin_client = httpx.AsyncClient(timeout=5)
    return _qq_plugin_client


async def _qq_plugin_get(path: str, params: dict | None = None) -> dict:
    """GET 代理到 QQ 插件（packages/qq/plugin.py，PAN_QQ_API_URL，默认 8080）。

    统一错误形态 {ok:false, error:{code,message}}；连接失败归为 connection_error，
    HTTP 非 2xx 透传插件返回体（插件已按同一形态返回）。
    """
    plugin_url = os.environ.get("PAN_QQ_API_URL", "http://127.0.0.1:8080").rstrip("/")
    try:
        r = await _qq_plugin_client_get().get(f"{plugin_url}{path}", params=params)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            if isinstance(body, dict) and body.get("error"):
                return body
        except ValueError:
            pass
        return {"ok": False, "error": {
            "code": e.response.status_code,
            "message": e.response.text[:300]}}
    except httpx.HTTPError as e:
        return {"ok": False, "error": {
            "code": "connection_error",
            "message": f"{type(e).__name__}: {e}"}}


@app.get("/api/qq/contacts")
async def api_qq_contacts(bot_uin: str = ""):
    """列出最近的 QQ 联系人/群（代理到 QQ 插件 recent_contacts）。

    postbox 弹窗需要可选 QQ 会话列表。QQ 插件（packages/qq/plugin.py）在独立
    端口（默认 8080，PAN_QQ_API_URL 可覆盖）暴露 GET /api/qq/recent_contacts，
    此处代理转发，让前端统一走 Pan Core 同源 /api，避免跨域。bot_uin 可选
    （多账号）：透传给插件，只拉指定 bot 通道的联系人；缺省插件走默认通道。
    """
    params = {"bot_uin": bot_uin} if bot_uin else None
    return await _qq_plugin_get("/api/qq/recent_contacts", params)


@app.get("/api/qq/channels")
async def api_qq_channels():
    """列出已注册的 QQ bot 通道（代理到插件 /api/qq/channels）。

    Postbox 合并模式据此对每个 bot 分别拉联系人；每项 {name, bot_uin, connected}。
    """
    return await _qq_plugin_get("/api/qq/channels")


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


@app.post("/api/readonly")
async def api_readonly(data: dict):
    """Set or clear a managed session's persistent readonly state.

    Only the target's current manager may change it; unlike claim this endpoint
    never establishes a managed relationship or accepts a claim capability.
    """
    manager_id = (data.get("managerId") or "").strip()
    session_id = (data.get("sessionId") or "").strip()
    enabled = data.get("readonlySession")
    if not manager_id or not session_id or not isinstance(enabled, bool):
        return {"ok": False, "error": {"code": "missing_params",
                "message": "managerId, sessionId and readonlySession(boolean) are required"}}
    if manager_id == session_id:
        return {"ok": False, "error": {"code": "permission_denied",
                "message": "A session cannot set readonly on itself"}}
    manager = sess.get(manager_id)
    target = sess.get(session_id)
    if not manager:
        return {"ok": False, "error": {"code": "manager_not_found",
                "message": f"Manager session {manager_id} not found"}}
    if not target:
        return {"ok": False, "error": {"code": "session_not_found",
                "message": f"Session {session_id} not found"}}
    if target.managed_by != manager_id or session_id not in manager.managed:
        return {"ok": False, "error": {"code": "permission_denied",
                "message": f"Session {manager_id} does not manage {session_id}"}}
    target.readonly_session = enabled
    sess.save(target)
    await broadcast({"type": "session.updated", "sessionId": session_id})
    return {"ok": True, "managerId": manager_id, "sessionId": session_id,
            "readonlySession": target.readonly_session}


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
                "generation": w.generation,
            }
            for w in worker.list_live_workers()
        ]
    }


@app.get("/api/cli/status")
async def api_cli_status():
    """Return user-facing availability diagnostics for supported Agent CLIs."""
    adapters = get_cli_diagnostics()
    return {
        "adapters": adapters,
        "available": [entry["name"] for entry in adapters if entry["available"]],
        "hasAvailable": any(entry["available"] for entry in adapters),
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
            strict_mcp=False,
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
    # An empty cwd means all native sessions.  Providers can still apply
    # their own safe default/filtering when a caller supplies a directory.
    sessions = provider.list_sessions(cwd or None)
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


@app.post("/api/sessions/{session_id}/worker/restart")
async def api_restart_or_start(session_id: str):
    """Restart the session's live worker, or start it when it is gone."""
    s = sess.get(session_id)
    if not s:
        return {"error": f"Session {session_id} not found"}
    result = await worker.restart_or_start_worker(session_id)
    if isinstance(result, str):
        return {"error": result}
    return {
        "workerId": result.worker_id,
        "sessionId": session_id,
        "status": result.status,
    }


@app.post("/api/sessions/{session_id}/worker/kill")
async def api_session_kill(session_id: str):
    """Kill the session's live worker; workerId is response detail only."""
    result = await worker.kill_session_worker(session_id)
    if isinstance(result, str):
        return {"error": result}
    if result is None:
        return {"workerId": None, "sessionId": session_id, "status": "offline"}
    return {"workerId": result.worker_id, "sessionId": session_id, "status": "offline"}


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

    adapter = get_adapter(s.adapter)
    extra_args: list[str] = []
    # Most native CLIs consume these compatibility overrides directly. Codex
    # uses a persistent wrapper and receives all settings from its Session via
    # --codex-extra-args; passing --model/--permission-mode to the wrapper would
    # make argparse reject the process before it can handle the next message.
    if not getattr(adapter, "settings_via_session", False):
        if "model" in data:
            extra_args.extend(["--model", data["model"]])
        if "permissionMode" in data:
            extra_args.extend(["--permission-mode", data["permissionMode"] or ""])
        extra_args.extend(adapter.effort_args(s))

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


@app.post("/api/sessions/{session_id}/worker/interrupt")
async def api_session_interrupt(session_id: str):
    """Interrupt the session's live worker; workerId is response detail only."""
    result = await worker.interrupt_session_worker(session_id)
    if isinstance(result, str):
        return {"error": result}
    if result is None:
        return {"workerId": None, "sessionId": session_id, "status": "offline"}
    return {"workerId": result.worker_id, "sessionId": session_id, "status": "interrupted"}


@app.post("/api/worker/{worker_id}/control")
async def api_worker_control(worker_id: str, data: dict):
    """Send an adapter-native out-of-band control to a live worker."""
    control = data.get("control") if isinstance(data.get("control"), dict) else data
    err = await worker.send_control_message(worker_id, control)
    if err:
        return {"error": err}
    return {"workerId": worker_id, "status": "control sent"}


@app.post("/api/sessions/{session_id}/worker/control")
async def api_session_worker_control(session_id: str, data: dict):
    """Send a native control using the session as the stable address."""
    control = data.get("control") if isinstance(data, dict) and isinstance(data.get("control"), dict) else data
    result = await worker.send_session_control(session_id, control)
    if isinstance(result, str):
        return {"error": result}
    if result is None:
        return {"error": "Worker not found"}
    return {"workerId": result.worker_id, "sessionId": session_id, "status": "control sent"}


@app.post("/api/worker/{worker_id}/steer")
async def api_worker_steer(worker_id: str, data: dict):
    """Inject text into a running native turn (Claude stream / Codex)."""
    err = await worker.steer_worker(
        worker_id, data.get("text") if isinstance(data, dict) else ""
    )
    if err:
        return {"error": err}
    return {"workerId": worker_id, "status": "steer sent"}


@app.post("/api/sessions/{session_id}/worker/steer")
async def api_session_worker_steer(session_id: str, data: dict):
    result = await worker.steer_session_worker(
        session_id, data.get("text") if isinstance(data, dict) else "",
    )
    if isinstance(result, str):
        return {"error": result}
    if result is None:
        return {"error": "Worker not found"}
    return {"workerId": result.worker_id, "sessionId": session_id, "status": "steer sent"}


@app.post("/api/worker/{worker_id}/claude-permission")
async def api_claude_permission(worker_id: str, data: dict):
    """Bridge Claude Code's MCP permission-prompt callback to the dashboard."""
    if not isinstance(data, dict):
        return {"behavior": "deny", "message": "Invalid permission request"}
    tool_name = data.get("toolName") or data.get("tool_name") or "unknown"
    tool_input = data.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    return await worker.request_claude_permission(
        worker_id, str(tool_name), tool_input,
    )


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

    # Takeover must leave no Pan app-server alive.  Restarting here would spawn
    # a replacement writer immediately before the native TUI resumes the same
    # Codex thread, which fails with "already has an active writer".
    err = await worker.takeover_worker(worker_id)
    if err:
        return {"error": err}

    await broadcast({
        "type": "worker.status",
        "sessionId": w.session_id,
        "workerId": w.worker_id,
        "generation": w.generation,
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


@app.post("/api/sessions/{session_id}/worker/takeover")
async def api_session_takeover(session_id: str):
    """Take over the session's live worker using sessionId as the control key."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    w = worker.find_alive_worker_by_session(session_id)
    if not w:
        return {"workerId": None, "sessionId": session_id, "status": "offline"}
    if not s.cli_session_id:
        return {"error": "Worker has no CLI session yet"}
    adapter_cmd = w.adapter.takeover_command(s)
    if not adapter_cmd:
        return {"error": f"Adapter '{w.adapter.name}' does not support takeover"}

    result = await worker.takeover_session_worker(session_id)
    if isinstance(result, str):
        return {"error": result}
    if result is None:
        return {"workerId": None, "sessionId": session_id, "status": "offline"}
    w = result
    await broadcast({
        "type": "worker.status",
        "sessionId": session_id,
        "workerId": w.worker_id,
        "generation": w.generation,
        "status": "held",
    })
    try:
        w.takeover_pid = _open_terminal(
            subprocess.list2cmdline(adapter_cmd), s.workdir or Path.cwd(),
        )
    except FileNotFoundError:
        return {"error": "terminal opener not found"}
    except OSError as e:
        return {"error": str(e)}
    return {
        "workerId": w.worker_id,
        "sessionId": session_id,
        "cliSessionId": s.cli_session_id,
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


@app.get("/api/sessions/{session_id}/worker/takeover-command")
async def api_session_takeover_command(session_id: str):
    """Return a takeover command by session, without changing runtime state."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    w = worker.find_alive_worker_by_session(session_id)
    if not w:
        return {"workerId": None, "sessionId": session_id, "status": "offline"}
    if not s.cli_session_id:
        return {"error": "Worker has no CLI session yet"}
    adapter_cmd = w.adapter.takeover_command(s)
    if not adapter_cmd:
        return {"error": f"Adapter '{w.adapter.name}' does not support takeover"}
    return {
        "workerId": w.worker_id,
        "sessionId": session_id,
        "cliSessionId": s.cli_session_id,
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
            "url": srv.url,
            "transport": srv.transport,
            "type": srv.type,
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
async def api_fs_read(session_id: str, path: str = "", download: bool = False):
    """Read file contents within the session's workdir.

    download=False (default): JSON {content, size}, UTF-8 text, 5 MiB cap
    (editor file-open path). download=True: binary attachment download via
    FileResponse (streamed, no size cap, works for binary files too).
    """
    try:
        target = _resolve_fs_path(session_id, path)
    except ValueError as e:
        return {"error": str(e)}
    if not target.is_file():
        return {"error": f"Not a file: {path!r}"}
    if download:
        # Sanitize: strip header-injection chars (CR/LF/quotes/control) —
        # starlette itself quotes non-ASCII via RFC 5987.
        safe_name = "".join(
            c for c in target.name if ord(c) >= 32 and c not in '"\\'
        ).strip() or "download"
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, filename=safe_name, media_type=media_type)
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
