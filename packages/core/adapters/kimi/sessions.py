"""Kimi Code CLI session scanner and history parser.

Scans ~/.kimi-code/sessions/ and workspaces.json for resumable Kimi sessions,
parses wire.jsonl transcripts into Pan history format.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path


_KIMI_DIR = Path.home() / ".kimi-code"
_WORKSPACES_FILE = _KIMI_DIR / "workspaces.json"
_SESSION_INDEX_FILE = _KIMI_DIR / "session_index.jsonl"


def _load_workspaces() -> dict[str, dict]:
    """Return workspace_id -> workspace info from workspaces.json."""
    if not _WORKSPACES_FILE.exists():
        return {}
    try:
        data = json.loads(_WORKSPACES_FILE.read_text(encoding="utf-8"))
        return data.get("workspaces", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _load_session_index() -> list[dict]:
    """Return list of session index entries from session_index.jsonl."""
    if not _SESSION_INDEX_FILE.exists():
        return []
    entries: list[dict] = []
    try:
        with open(_SESSION_INDEX_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def _state_file(session_dir: Path) -> Path | None:
    """Return state.json path for a Kimi session directory."""
    path = session_dir / "state.json"
    return path if path.exists() else None


def _wire_file(session_dir: Path) -> Path | None:
    """Return agents/main/wire.jsonl path for a Kimi session directory."""
    path = session_dir / "agents" / "main" / "wire.jsonl"
    return path if path.exists() else None


def _iso_ts(ts_ms: int | None) -> str:
    """Convert epoch ms to ISO string."""
    if not ts_ms:
        return ""
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
    except (OSError, ValueError):
        return str(ts_ms)


def _read_state(session_dir: Path) -> dict:
    """Read and parse state.json."""
    path = _state_file(session_dir)
    if not path:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _same_path(a: str, b: str) -> bool:
    """Compare two filesystem paths ignoring slash direction and case."""
    try:
        return str(Path(a).resolve()).lower() == str(Path(b).resolve()).lower()
    except OSError:
        return a.lower().replace("\\", "/") == b.lower().replace("\\", "/")


def list_kimi_sessions(project_cwd: str | None = None) -> list[dict]:
    """List resumable Kimi sessions.

    project_cwd: if provided, only return sessions whose workDir matches.

    Returns a list of dicts with keys: session_id, workspace_id, title,
    workDir, createdAt, updatedAt, message_count, model.
    """
    workspaces = _load_workspaces()
    index = _load_session_index()
    sessions: list[dict] = []

    for entry in index:
        sid = entry.get("sessionId")
        sdir = entry.get("sessionDir")
        workdir = entry.get("workDir")
        if not sid or not sdir:
            continue
        if project_cwd and not _same_path(workdir, project_cwd):
            continue

        session_dir = Path(sdir)
        state = _read_state(session_dir)
        if not state:
            continue

        # Find workspace id from workspaces.json by root
        workspace_id = ""
        for wid, ws in workspaces.items():
            if ws.get("root") == workdir:
                workspace_id = wid
                break

        history = parse_kimi_history(sid, workdir)
        model = ""
        for ev in _iter_wire(session_dir):
            if ev.get("type") == "config.update" and ev.get("modelAlias"):
                model = ev["modelAlias"]
                break
            if ev.get("type") == "usage.record" and ev.get("model"):
                model = ev["model"]
                break

        sessions.append({
            "session_id": sid,
            "workspace_id": workspace_id,
            "title": state.get("title", ""),
            "workDir": workdir,
            "createdAt": state.get("createdAt", ""),
            "updatedAt": state.get("updatedAt", ""),
            "message_count": len([h for h in history if h.get("role") in ("user", "assistant")]),
            "model": model,
        })

    sessions.sort(key=lambda s: str(s.get("updatedAt") or s.get("createdAt") or ""), reverse=True)
    return sessions


def list_kimi_workspaces() -> list[dict]:
    """List Kimi workspaces that have sessions."""
    workspaces = _load_workspaces()
    index = _load_session_index()
    counts: dict[str, int] = {}
    for entry in index:
        workdir = entry.get("workDir")
        if not workdir:
            continue
        for wid, ws in workspaces.items():
            if ws.get("root") == workdir:
                counts[wid] = counts.get(wid, 0) + 1
                break

    result: list[dict] = []
    for wid, ws in workspaces.items():
        if counts.get(wid, 0) == 0:
            continue
        result.append({
            "workspace_id": wid,
            "name": ws.get("name", ""),
            "root": ws.get("root", ""),
            "created_at": ws.get("created_at", ""),
            "last_opened_at": ws.get("last_opened_at", ""),
            "session_count": counts[wid],
        })
    result.sort(key=lambda x: x.get("last_opened_at") or "", reverse=True)
    return result


def _iter_wire(session_dir: Path):
    """Yield parsed wire.jsonl events for a session directory."""
    path = _wire_file(session_dir)
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _event_to_block(event: dict) -> dict | None:
    """Map a single Kimi wire.jsonl event to a Pan history block."""
    etype = event.get("type")

    if etype == "context.append_message":
        msg = event.get("message", {})
        role = msg.get("role")
        origin = msg.get("origin", {})
        # Skip system injections and non-user assistant messages
        if role == "user" and origin.get("kind") == "user":
            content_blocks = msg.get("content") or []
            text = ""
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") in ("text", "input_text"):
                    text += block.get("text", "")
            text = text.strip()
            if text:
                return {"role": "user", "content": text}

    elif etype == "context.append_loop_event":
        ev = event.get("event", {})
        sub_type = ev.get("type")

        if sub_type == "content.part":
            part = ev.get("part", {})
            ptype = part.get("type")
            if ptype == "text":
                text = part.get("text", "").strip()
                if text:
                    return {"role": "assistant", "content": text}
            elif ptype == "think":
                text = part.get("think", "").strip()
                if text:
                    return {"role": "thinking", "content": text}

        elif sub_type == "tool.call":
            name = ev.get("name", "?")
            args = ev.get("args") or {}
            if isinstance(args, dict):
                args_str = json.dumps(args, ensure_ascii=False)[:500]
            else:
                args_str = str(args)[:500]
            return {"role": "tool", "content": f"{name}({args_str})"}

    return None


def parse_kimi_history(session_id: str, workdir: str | None = None) -> list[dict]:
    """Parse Kimi session wire.jsonl into Pan history format.

    session_id: full Kimi session id, e.g. session_xxxxxxxx-xxxx-...
    workdir: optional workDir to locate the session

    Returns list of {"role": str, "content": str} blocks.
    """
    # Locate session dir from index
    index = _load_session_index()
    session_dir: Path | None = None
    for entry in index:
        if entry.get("sessionId") == session_id:
            if workdir and not _same_path(entry.get("workDir", ""), workdir):
                continue
            session_dir = Path(entry.get("sessionDir"))
            break

    if not session_dir or not session_dir.exists():
        return []

    history: list[dict] = []
    for event in _iter_wire(session_dir):
        block = _event_to_block(event)
        if block:
            history.append(block)
    return history


def get_raw_usage(session_id: str, workdir: str | None = None) -> list[dict]:
    """Extract usage records from a Kimi session.

    Returns list of dicts: {"model": str, "rawUsage": dict, "timestamp": str}
    """
    index = _load_session_index()
    session_dir: Path | None = None
    for entry in index:
        if entry.get("sessionId") == session_id:
            if workdir and not _same_path(entry.get("workDir", ""), workdir):
                continue
            session_dir = Path(entry.get("sessionDir"))
            break

    if not session_dir or not session_dir.exists():
        return []

    usage_entries: list[dict] = []
    for event in _iter_wire(session_dir):
        if event.get("type") != "usage.record":
            continue
        usage = event.get("usage")
        if not usage:
            continue
        usage_entries.append({
            "model": event.get("model", "unknown"),
            "rawUsage": {
                "prompt_tokens": usage.get("inputOther", 0) + usage.get("inputCacheRead", 0),
                "prompt_cache_hit_tokens": usage.get("inputCacheRead", 0),
                "prompt_cache_miss_tokens": usage.get("inputCacheCreation", 0),
                "completion_tokens": usage.get("output", 0),
                "credit": 0.0,
            },
            "timestamp": _iso_ts(event.get("time")),
        })
    return usage_entries


def get_session_title(session_id: str, workdir: str | None = None) -> str:
    """Return the title stored in state.json for a Kimi session."""
    index = _load_session_index()
    session_dir: Path | None = None
    for entry in index:
        if entry.get("sessionId") == session_id:
            if workdir and not _same_path(entry.get("workDir", ""), workdir):
                continue
            session_dir = Path(entry.get("sessionDir"))
            break
    if not session_dir:
        return ""
    state = _read_state(session_dir)
    return state.get("title", "")


def write_custom_title(session_id: str, title: str, workdir: str | None = None) -> None:
    """Write a custom title into a Kimi session's state.json.

    Sets state.json ``title`` and ``isCustomTitle=true`` so the rename persists
    in Kimi's own storage — mirrors cbc's write_custom_title (which appends a
    custom-title event to the JSONL). Used by server.api_rename_session for
    kimi sessions (G7).
    """
    session_dir = _find_session_dir(session_id, workdir)
    if not session_dir:
        return
    state_path = session_dir / "state.json"
    if not state_path.exists():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    state["title"] = title
    state["isCustomTitle"] = True
    try:
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _resolve_workspace_id_for_cwd(cwd: str) -> str | None:
    """Find Kimi workspace id for a given filesystem path."""
    workspaces = _load_workspaces()
    for wid, ws in workspaces.items():
        root = ws.get("root")
        if not root:
            continue
        if _same_path(root, cwd):
            return wid
    return None


def list_kimi_sessions_for_cwd(cwd: str) -> list[dict]:
    """List Kimi sessions whose workDir matches the given cwd."""
    return list_kimi_sessions(project_cwd=str(Path(cwd).resolve()))


def _find_session_dir(session_id: str, workdir: str | None = None) -> Path | None:
    """Locate Kimi session directory by session id."""
    index = _load_session_index()
    for entry in index:
        if entry.get("sessionId") == session_id:
            if workdir and not _same_path(entry.get("workDir", ""), workdir):
                continue
            sdir = Path(entry.get("sessionDir", ""))
            if sdir.exists():
                return sdir
    return None


def fork_kimi_session(parent_id: str, name: str, workdir: str | None = None) -> str:
    """Fork a Kimi session by copying its directory and registering the new session.

    This is a best-effort implementation: Kimi CLI does not expose a stable
    `--fork` flag, so we copy the session files directly. The forked session can
    then be resumed with `kimi -S <new_session_id>`.

    Returns the new Kimi session id.
    """
    parent_dir = _find_session_dir(parent_id, workdir)
    if not parent_dir:
        raise FileNotFoundError(f"Parent Kimi session not found: {parent_id}")

    workspace_dir = parent_dir.parent
    new_id = f"session_{str(_uuid.uuid4())}"
    new_dir = workspace_dir / new_id
    while new_dir.exists():
        new_id = f"session_{str(_uuid.uuid4())}"
        new_dir = workspace_dir / new_id

    # Copy entire session directory
    shutil.copytree(parent_dir, new_dir)

    # Update state.json for the new session
    state_path = new_dir / "state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
    else:
        state = {}

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state["createdAt"] = now
    state["updatedAt"] = now
    state["title"] = name
    state["isCustomTitle"] = True
    state["lastPrompt"] = ""

    # Update agent homedir to point to new session dir
    agents = state.get("agents", {})
    if isinstance(agents, dict):
        for agent in agents.values():
            if isinstance(agent, dict) and "homedir" in agent:
                agent["homedir"] = str(new_dir / "agents" / agent.get("type", "main"))

    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # Register in session_index.jsonl
    parent_entry = None
    for entry in _load_session_index():
        if entry.get("sessionId") == parent_id:
            parent_entry = entry
            break

    new_entry = {
        "sessionId": new_id,
        "sessionDir": str(new_dir),
        "workDir": parent_entry.get("workDir", "") if parent_entry else "",
    }
    try:
        with open(_SESSION_INDEX_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(new_entry, ensure_ascii=False) + "\n")
    except OSError:
        pass

    return new_id
