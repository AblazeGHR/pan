"""Pan — FastAPI routes + WebSocket."""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import worker
from . import session as sess
from .adapters import get_adapter
from .adapters.cbc import sessions as cbc_sessions
from .config import load_config

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
    yield
    await worker.shutdown_all()
    _log("[Pan] All workers shut down")


app = FastAPI(title="Pan", lifespan=lifespan)

ws_clients: set[WebSocket] = set()
agent_clients: set[WebSocket] = set()

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
WORKDIRS_DIR = DATA_DIR / "workdirs"
DASHBOARD_FILE = Path(__file__).resolve().parent.parent.parent / "index.html"
MOBILE_DASHBOARD_FILE = Path(__file__).resolve().parent.parent.parent / "mobile.html"
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
    for ws in list(agent_clients):
        try:
            await ws.send_json(data)
        except Exception:
            dead_a.add(ws)
    agent_clients.difference_update(dead_a)


worker.set_broadcaster(broadcast)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every API request with method, path, and status code.

    Set CLICONDUCTOR_LOG_SKIP=comma,separated,path,prefixes to skip specific
    endpoints from being logged.
    """
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
    config = load_config().get("cbc", {})
    return {
        "id": s.id,
        "name": s.name,
        "cbcSessionId": s.cbc_session_id,
        "model": s.model or config.get("model") or worker.DEFAULT_MODEL,
        "permissionMode": s.permission_mode or config.get("permission_mode") or None,
        "alwaysThinkingEnabled": s.always_thinking_enabled,
        "effort": s.effort or config.get("effort", ""),
        "maxThinkingTokens": s.max_thinking_tokens,
        "workdir": s.workdir,
        "history": s.history,
        "lastResult": s.last_result,
        "rawUsage": s.raw_usage,
        "totalUsage": s.total_usage,
        "createdAt": s.created_at,
        "updatedAt": s.updated_at,
        "workerStatus": w.status if w else None,
        "workerId": w.worker_id if w else None,
    }


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

# Reserved for future path restriction — set in config.json to limit
# workdir to specific base directories (e.g. ["d:/project"]).
_ALLOWED_WORKDIR_ROOTS: list[Path] | None = None


def _resolve_workdir(workdir_name: str) -> Path:
    """Resolve a workdir name to a Path, creating it.

    - Absolute paths (e.g. D:\\project\\foo) are used directly.
    - Simple names (e.g. my-session) are placed under WORKDIRS_DIR.
    - Optional path restriction: set _ALLOWED_WORKDIR_ROOTS in the
      caller to reject paths outside allowed base directories.
    """
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

    # Slug name — resolve under WORKDIRS_DIR (original behaviour)
    if not _WORKDIR_NAME_RE.match(workdir_name):
        raise ValueError(
            f"Invalid workdir name: {workdir_name!r} "
            f"(only alphanumeric, underscore, hyphen allowed)"
        )
    workdir = WORKDIRS_DIR / workdir_name
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _build_session_params(data: dict) -> dict:
    """Extract session creation parameters from request data, with defaults."""
    config = load_config().get("cbc", {})
    name = data.get("name", "default")
    workdir_name = data.get("workdir") or name
    return {
        "name": name,
        "model": data.get("model") or config.get("model") or worker.DEFAULT_MODEL,
        "permission_mode": data.get("permissionMode") or config.get("permission_mode") or None,
        "always_thinking_enabled": data.get("alwaysThinkingEnabled", config.get("always_thinking_enabled", False)),
        "effort": data.get("effort") or config.get("effort", ""),
        "max_thinking_tokens": data.get("maxThinkingTokens") or None,
        "workdir": str(_resolve_workdir(workdir_name)),
    }


def _apply_session_updates(s: sess.Session, data: dict):
    """Apply model/mode/thinking/effort fields from data to a Session (in-place)."""
    if "model" in data:
        s.model = data["model"]
    if "permissionMode" in data:
        s.permission_mode = data["permissionMode"] or None
    if "alwaysThinkingEnabled" in data:
        s.always_thinking_enabled = data["alwaysThinkingEnabled"]
    if "effort" in data:
        s.effort = data["effort"]
    if "maxThinkingTokens" in data:
        s.max_thinking_tokens = data["maxThinkingTokens"]


# ── Dashboard & favicon ──

@app.get("/favicon.ico")
async def favicon():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#58a6ff"/><text x="16" y="22" font-size="18" text-anchor="middle" fill="#fff" font-family="monospace" font-weight="bold">C</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
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

            if msg_type == "task":
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
                        "status": "idle",
                        "model": s.model,
                    })

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


# ── Session API ──

@app.get("/api/sessions")
async def api_list_sessions():
    """List all sessions (includes worker status if active)."""
    sessions = sess.list_all()
    return {"sessions": [_session_to_api(s) for s in sessions]}


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


@app.patch("/api/sessions/{session_id}")
async def api_update_session(session_id: str, data: dict):
    """Update session-level settings (model/mode/thinking/effort) without spawning a worker."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    _apply_session_updates(s, data)
    sess.save(s)
    await broadcast({
        "type": "session.updated",
        "sessionId": s.id,
    })
    return _session_to_api(s)


@app.post("/api/sessions/{session_id}/rename")
async def api_rename_session(session_id: str, data: dict):
    """Rename a session by its internal ID (no worker required)."""
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return {"error": "name is required"}

    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}

    # Same name — nothing to do
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
    """Branch from a session — copy cbc JSONL, import new session, preserve settings."""
    s = sess.get(session_id)
    if not s:
        return {"error": "Session not found"}
    if not s.cbc_session_id:
        return {"error": "Session has no cbc session ID — cannot branch"}

    name = (data.get("name") or "").strip()
    if not name:
        name = f"{s.name}-branch"

    err = _check_session_name(name)
    if err:
        return {"error": err}

    # Fork via pure file operations — no cbc process spawned
    cwd = s.workdir or ""
    try:
        new_cbc_id_str = cbc_sessions.fork_cbc_session(
            s.cbc_session_id, name, cwd or None,
        )
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Fork failed: {e}"}

    # Import the forked session's JSONL
    try:
        history = cbc_sessions.parse_cbc_history(new_cbc_id_str, cwd)
        raw_usage_entries = cbc_sessions.get_raw_usage(new_cbc_id_str, cwd)
    except Exception as e:
        return {"error": f"Failed to parse forked session: {e}"}

    raw_usage = sess.accumulate_raw_usage(None, raw_usage_entries)
    total_usage = sess.compute_total_usage(raw_usage)

    # Create Pan session with user's name and parent's settings
    new_s = sess.create(
        name=name,
        cbc_session_id=new_cbc_id_str,
        model=s.model,
        permission_mode=s.permission_mode,
        always_thinking_enabled=s.always_thinking_enabled,
        effort=s.effort,
        max_thinking_tokens=s.max_thinking_tokens,
        raw_usage=raw_usage,
        total_usage=total_usage,
        workdir=s.workdir,
        history=history,
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


@app.get("/api/models")
async def api_models():
    return {"models": worker.SUPPORTED_MODELS, "default": worker.DEFAULT_MODEL}


@app.get("/api/adapter/config")
async def api_adapter_config():
    """Return default adapter configuration (models, effort values, permission modes).
    Frontend uses this to dynamically render selects.
    """
    a = get_adapter("cbc")
    return {
        "models": a.supported_models,
        "defaultModel": a.default_model,
        "effortValues": list(a.effort_values),
        "permissionModes": a.permission_modes,
        "defaultPermissionMode": a.default_permission_mode,
    }


# ── Spawn (create worker for a session) ──

@app.post("/api/spawn")
async def api_spawn(data: dict):
    """Spawn a Worker for a Session.

    If session_id is provided, use that Session.
    Otherwise, create a new Session first.
    """
    session_id = data.get("sessionId")
    if session_id:
        s = sess.get(session_id)
        if not s:
            return {"error": f"Session {session_id} not found"}
        # 杀掉已有的 worker（避免多个 worker 跑同一 session）
        existing = worker.find_worker_by_session(session_id)
        if existing:
            await worker.kill_worker(existing.worker_id)
        # apply settings from request if provided
        _apply_session_updates(s, data)
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
        "model": s.model or worker.DEFAULT_MODEL,
    }


@app.post("/api/task")
async def api_task(data: dict):
    """Send a task to a Worker by worker_id or session_id.

    Auto-spawns a worker (with --resume if session has cbc_session_id)
    if the session exists but has no live worker — consistent with the
    WS /ws and /ws/agent endpoints. Worker death is common (server restart,
    cbc crash), so we recover transparently instead of erroring.
    """
    worker_id = data.get("workerId")
    session_id = data.get("sessionId")

    # Resolve worker_id from session_id
    if not worker_id and session_id:
        w = worker.find_worker_by_session(session_id)
        if w:
            worker_id = w.worker_id

    # No worker found — try to auto-spawn for this session
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
            "model": s.model or worker.DEFAULT_MODEL,
            "reason": "auto-spawned by /api/task",
        })

    if not worker_id:
        return {"error": "workerId or sessionId required"}

    text = data.get("text")
    if not text:
        return {"error": "text is required"}

    err = await worker.send_task(worker_id, text, source="agent")
    if err:
        # Worker died between resolve and send (race). Kill the corpse,
        # auto-spawn+resume a fresh one, retry the task once.
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


@app.post("/api/kill/{worker_id}")
async def api_kill(worker_id: str):
    """Kill a Worker process. Does NOT delete the Session."""
    err = await worker.kill_worker(worker_id)
    if err:
        return {"error": err}
    return {"workerId": worker_id, "status": "killed"}


@app.get("/api/list")
async def api_list():
    """List running workers (not sessions)."""
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
    """List cbc project directories that have resumable sessions."""
    config = load_config()
    ci = config.get("cbc_import", {})
    recent_days = ci.get("import_recent_days", 30)
    min_resume_bytes = ci.get("min_resume_bytes", 200)
    projects = cbc_sessions.list_cbc_projects(
        recent_days=recent_days, min_resume_bytes=min_resume_bytes
    )
    return {"projects": projects}


def _sanitize_project_dir(cwd: str) -> str:
    """Mirror cbc's sanitize logic for exact dir matching."""
    p = cwd.replace(":", "")
    p = p.replace("\\", "-").replace("/", "-")
    p = re.sub(r"^[-]+", "", p)
    p = re.sub(r"[-]+", "-", p)
    return p.lower()


@app.get("/api/cbc/sessions")
async def api_cbc_sessions(project_dir: str = "", cwd: str = "", all: int = 0):
    """List external cbc sessions available for import.

    project_dir: cbc project dir name (e.g. "d-project-CLIConductor")
    cwd:         fallback filesystem path (auto-sanitized)
    """
    config = load_config()
    filter_cfg = config.get("cbc_import", {})

    if project_dir:
        all_sessions = cbc_sessions.list_cbc_sessions(project_dir=project_dir)
    else:
        cwd = cwd or str(Path.cwd())
        all_sessions = cbc_sessions.list_cbc_sessions(cwd)

    if all:
        return {"sessions": all_sessions, "total": len(all_sessions)}

    # Filter 1: removed — already-imported sessions are now visible
    #           and can be re-imported (old session is replaced).

    # Filter 2: skip non-main workdir sessions
    exclude_patterns = filter_cfg.get("exclude_workdir_patterns", [])
    target_dir = None
    if filter_cfg.get("project_dir_exact_match", False):
        target_dir = _sanitize_project_dir(cwd)

    filtered: list[dict] = []
    for s in all_sessions:
        if target_dir and s["project_dir"] != target_dir:
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
    """Browse cbc sessions as a file-tree (breadcrumb + folders + paginated sessions)."""
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

    # When frontend sends project_dir but not cwd, resolve the actual filesystem path
    if not data.get("cwd") and project_dir:
        resolved = cbc_sessions.project_dir_to_path(project_dir)
        if resolved:
            cwd = resolved

    # Parse cbc data first (needed regardless of new vs reimport)
    try:
        if project_dir:
            history = cbc_sessions.parse_cbc_history(session_id, project_dir=project_dir)
            raw_usage_entries = cbc_sessions.get_raw_usage(session_id, project_dir=project_dir)
        else:
            history = cbc_sessions.parse_cbc_history(session_id, cwd)
            raw_usage_entries = cbc_sessions.get_raw_usage(session_id, cwd)
    except Exception as e:
        return {"error": f"Failed to parse session history: {e}"}

    raw_usage = sess.accumulate_raw_usage(None, raw_usage_entries)
    total_usage = sess.compute_total_usage(raw_usage)

    # If already imported — update in-place (preserve name, model, settings)
    existing = None
    for s in sess.list_all():
        if s.cbc_session_id == session_id:
            existing = s
            break

    if existing:
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

    # New session
    name = (
        data.get("name", "")
        or cbc_sessions.get_session_title(session_id, project_dir=project_dir, cwd=cwd)
        or f"cbc-{session_id[:8]}"
    )

    s = sess.create(
        name=name,
        cbc_session_id=session_id,
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
    """Apply model/mode/thinking settings to a session and respawn the worker once.
    Accepted fields:

        model                  — model name (str or None)
        permissionMode         — permission mode (str or None)
        alwaysThinkingEnabled  — enable thinking (bool)
        effort                 — effort level (low/medium/high/xhigh)
    """
    w = worker.get_worker(worker_id)
    if not w:
        return {"error": "Worker not found"}
    s = sess.get(w.session_id)
    if not s:
        return {"error": "Session not found"}

    # update session fields first …
    _apply_session_updates(s, data)
    sess.save(s)

    # … then build extra args from the updated session and respawn once
    extra_args: list[str] = []
    if "model" in data:
        extra_args.extend(["--model", data["model"]])
    if "permissionMode" in data:
        extra_args.extend(["--permission-mode", data["permissionMode"] or ""])
    extra_args.extend(worker.effort_args(s))

    err = await worker.respawn_worker(worker_id, extra_args if extra_args else None)
    if err:
        return {"error": err}

    return {
        "workerId": worker_id,
        "sessionId": s.id,
        "model": s.model,
        "permissionMode": s.permission_mode,
        "alwaysThinkingEnabled": s.always_thinking_enabled,
        "effort": s.effort,
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

    # resolve session: prefer live worker, fallback to sessionId in body
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
    if not orig or not orig.cbc_session_id:
        return {"error": "Session not ready for branching"}

    name = data.get("name") or f"{orig.name}-branch"
    new_session = sess.create(name, model=orig.model,
                              permission_mode=orig.permission_mode,
                              always_thinking_enabled=orig.always_thinking_enabled,
                              effort=orig.effort,
                              max_thinking_tokens=orig.max_thinking_tokens,
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
    import subprocess

    w = worker.get_worker(worker_id)
    if not w:
        return {"error": "Worker not found"}
    s = sess.get(w.session_id)
    if not s:
        return {"error": "Session not found"}
    if not s.cbc_session_id:
        return {"error": "Worker has no cbc session yet"}
    if w.status == "held":
        return {"error": "Worker already in takeover mode"}

    # check adapter supports takeover
    adapter_cmd = w.adapter.takeover_command(s)
    if not adapter_cmd:
        return {"error": f"Adapter '{w.adapter.name}' does not support takeover"}

    # restart worker to free session, then mark held
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
        proc = subprocess.Popen(
            ["powershell.exe", "-NoExit", "-Command",
             " ".join(adapter_cmd)],
            cwd=s.workdir or str(Path.cwd()),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        w.takeover_pid = proc.pid
    except FileNotFoundError:
        return {"error": "powershell.exe not found"}
    except OSError as e:
        return {"error": str(e)}

    return {
        "workerId": worker_id,
        "sessionId": w.session_id,
        "cbcSessionId": s.cbc_session_id,
        "takeoverPid": w.takeover_pid,
        "status": "takeover started",
    }

# ── Static files (CSS, JS) ──
STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
