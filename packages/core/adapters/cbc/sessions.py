"""cbc session scanner and history parser.

Scans ~/.codebuddy/projects/ for resumable cbc sessions,
parses JSONL transcripts into CLIConductor history format.
"""

from __future__ import annotations

import functools
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path


def _project_dir(project_cwd: str | None) -> Path:
    """Return the cbc project directory for a given working directory.

    cbc sanitizes paths: D:\\project\\CLIConductor → d-project-CLIConductor.
    """
    base = Path(os.path.expanduser("~/.codebuddy/projects"))
    if project_cwd:
        sanitized = sanitize_project_dir_name(project_cwd)
        return base / sanitized
    return base


def sanitize_project_dir_name(cwd: str) -> str:
    """Mirror cbc's path-to-directory-name sanitization.

    Produces the same result as cbc's internal sanitizer: strip drive
    colon, lowercase, replace ``\\`` and ``/`` with ``-``, collapse
    consecutive ``-``, strip leading/trailing ``-``.

    This is the canoncial implementation shared by ``_project_dir``,
    ``server._sanitize_project_dir``, and any future callers.
    """
    p = cwd.replace(":", "").lower()
    p = p.replace("\\", "-").replace("/", "-")
    p = re.sub(r"-+", "-", p).strip("-")
    return p


@functools.lru_cache(maxsize=128)
def _read_project_cwd(dir_name: str) -> str | None:
    """Read the original cwd from the first JSONL event in a cbc project directory.

    cbc sanitizes directory names by replacing ``\\`` and ``/`` with ``-``,
    which is lossy when filenames already contain ``-``. To recover the real
    path, we read the ``cwd`` field from any session's first event — cbc
    stores it in every event regardless of type.

    Cached via LRU so repeated scans of the same project dir are fast.
    """
    base = Path(os.path.expanduser("~/.codebuddy/projects"))
    proj_dir = base / dir_name
    if not proj_dir.is_dir():
        return None
    for entry in sorted(proj_dir.iterdir()):
        if not entry.suffix == ".jsonl" or entry.stem == "agent":
            continue
        try:
            with open(entry, encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line:
                    event = json.loads(first_line)
                    cwd = event.get("cwd")
                    if cwd:
                        return cwd
        except (json.JSONDecodeError, OSError):
            continue
    return None


def list_cbc_sessions(project_cwd: str | None = None, *, project_dir: str | None = None) -> list[dict]:
    """List resumable cbc sessions from ~/.codebuddy/projects/.

    project_cwd: filesystem path → auto-sanitize to cbc project dir
    project_dir:  cbc project dir name directly (e.g. "d-project-CLIConductor")

    Returns a list of dicts with keys: session_id, title, message_count,
    first_timestamp, last_timestamp, model, forked_from.
    """
    sessions: list[dict] = []
    if project_dir:
        proj_dir = Path(os.path.expanduser("~/.codebuddy/projects")) / project_dir
    else:
        proj_dir = _project_dir(project_cwd)
    if not proj_dir.exists():
        return sessions

    for fpath in sorted(proj_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if fpath.suffix != ".jsonl" or fpath.stem == "agent":
            continue
        # skip subagent directories
        if fpath.is_dir():
            continue

        session_id = fpath.stem
        meta = _read_meta(proj_dir, session_id)

        try:
            title, msg_count, first_ts, last_ts, model = _parse_summary(fpath)
        except Exception:
            continue

        sessions.append({
            "session_id": session_id,
            "project_dir": proj_dir.name,
            "title": title,
            "message_count": msg_count,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "model": model,
            "forked_from": meta.get("forkedFrom"),
        })

    return sessions




def list_cbc_projects(recent_days: int = 0, min_resume_bytes: int = 0) -> list[dict]:
    """Scan ~/.codebuddy/projects/ and return available project directories.

    recent_days: only include sessions modified within this many days (0 = no filter).
    min_resume_bytes: file must be at least this size to count as resumable.
    Returns list of dicts with keys: project_dir, session_count, resumable_count,
    path_hint, drive, short_label. Projects with 0 resumable sessions are excluded.
    """
    base = Path(os.path.expanduser("~/.codebuddy/projects"))
    if not base.exists():
        return []

    cutoff = datetime.now() - timedelta(days=recent_days) if recent_days > 0 else None

    projects: list[dict] = []
    for child in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not child.is_dir():
            continue
        total_count = 0
        resumable_count = 0
        for f in child.iterdir():
            if f.suffix == ".jsonl" and f.stem != "agent" and f.is_file():
                total_count += 1
                if cutoff and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    continue
                if _can_resume(f, min_resume_bytes):
                    resumable_count += 1
        if resumable_count == 0:
            continue

        drive, short_label = _parse_project_label(child.name)

        projects.append({
            "project_dir": child.name,
            "session_count": total_count,
            "resumable_count": resumable_count,
            "path_hint": _project_dir_to_path(child.name),
            "drive": drive,
            "short_label": short_label,
        })

    return projects


def browse_cbc_tree(path: str = "", limit: int = 30, offset: int = 0, query: str = "") -> dict:
    """Browse cbc sessions in a file-explorer tree fashion.

    path: "" = root (show drives), "D:" = drive, "D:\\PROJECT" = deeper.
    limit/offset: pagination for sessions at the current level.
    query: optional title filter.

    Returns {breadcrumbs: [{label, path}], folders: [{name, path, session_count}],
             sessions: [...], total: N, has_more: bool}
    """
    all_projects = list_cbc_projects(recent_days=0, min_resume_bytes=0)
    if not all_projects:
        return {"breadcrumbs": [], "folders": [], "sessions": [], "total": 0, "has_more": False}

    # Normalize path and compute segments
    path = path.strip().rstrip("\\")
    path_parts = [p.upper() for p in path.split("\\") if p] if path else []

    # Group projects by path prefix and next segment
    # folder_key -> {name, path, session_count, project_dir (if exact match)}
    folder_map: dict[str, dict] = {}
    exact_sessions: list[dict] = []  # sessions from projects at exactly this depth

    for pj in all_projects:
        fp = (pj["path_hint"] or "").strip().rstrip("\\")
        if not fp:
            continue

        fp_upper = fp.upper()

        # Check if this project is under the current path
        if path:
            if not fp_upper.startswith(path.upper()):
                continue
            remaining = fp[len(path):].lstrip("\\")
        else:
            # Root level: group by drive (first segment)
            remaining = fp

        if not remaining:
            # Exact path match — load sessions directly
            session_list = list_cbc_sessions(project_dir=pj["project_dir"])
            if query:
                q = query.lower()
                session_list = [s for s in session_list if q in (s.get("title") or "").lower()]
            exact_sessions.extend(session_list)
            continue

        parts = remaining.split("\\")
        first = parts[0].upper()

        # Build folder key: current_path + first segment
        folder_key = (path + "\\" + first).upper() if path else first
        init_kwargs = {"name": parts[0], "path": folder_key, "session_count": 0}
        folder_map.setdefault(folder_key, init_kwargs)

        count = pj.get("resumable_count") or pj.get("session_count") or 0

        if len(parts) == 1:
            # Project at exactly this depth — load sessions
            session_list = list_cbc_sessions(project_dir=pj["project_dir"])
            # Apply query filter
            if query:
                q = query.lower()
                session_list = [s for s in session_list if q in (s.get("title") or "").lower()]
            exact_sessions.extend(session_list)
        else:
            # Deeper project — just count
            folder_map[folder_key]["session_count"] += count

    # Note: exact_sessions may contain sessions from different project dirs
    # Sort by last_timestamp desc
    exact_sessions.sort(key=lambda s: s.get("last_timestamp", "") or "", reverse=True)
    total = len(exact_sessions)
    sessions_page = exact_sessions[offset:offset + limit]

    # Build breadcrumbs
    breadcrumbs = []
    cumulative = ""
    for i, part in enumerate(path_parts):
        cumulative = (cumulative + "\\" + part) if cumulative else part
        breadcrumbs.append({"label": part, "path": cumulative})

    # Sort folders by name
    folders = sorted(folder_map.values(), key=lambda f: f["name"])

    return {
        "breadcrumbs": breadcrumbs,
        "folders": folders,
        "sessions": sessions_page,
        "total": total,
        "has_more": (offset + limit) < total,
    }


def _parse_project_label(dir_name: str) -> tuple[str, str]:
    """Extract drive letter and short label from a cbc project directory name.

    Uses the real ``cwd`` from JSONL when available; falls back to
    heuristic splitting.
    """
    cwd = _read_project_cwd(dir_name)
    if cwd:
        drive = cwd[:2].upper()  # e.g. "D:"
        # Short label: drive-relative path, or just last component
        rel = cwd[3:]  # strip "D:\"
        parts = rel.replace("\\", "/").rstrip("/").split("/")
        short_label = "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else rel)
        return drive, short_label

    # Fallback heuristic
    parts = dir_name.split("-")
    if not parts:
        return ("", dir_name)
    drive = parts[0].upper() + ":"
    short_label = "-".join(parts[1:]) if len(parts) >= 2 else dir_name
    if not short_label:
        short_label = dir_name
    return drive, short_label


def _project_dir_to_path(dir_name: str) -> str:
    """Reverse cbc's sanitization to produce a filesystem path.

    Uses the real ``cwd`` recorded in a session JSONL as the primary source.
    Falls back to heuristic parsing when no session files are available
    (e.g. a project dir that was just created but has no JSONL yet).
    """
    cwd = _read_project_cwd(dir_name)
    if cwd:
        return cwd

    # Fallback: heuristic reverse-engineering (lossy for paths containing '-')
    parts = dir_name.split("-")
    if not parts:
        return ""
    drive = parts[0] + ":"
    rest = "\\".join(parts[1:])
    return (drive + "\\" + rest).upper()


def project_dir_to_path(dir_name: str) -> str | None:
    """Public wrapper: reverse cbc project dir sanitization to a best-guess filesystem path.

    e.g. "d-project-CLIConductor" → "D:/project/CLIConductor"
    Returns None when reverse can't produce a meaningful path.
    """
    result = _project_dir_to_path(dir_name)
    return result if result else None


def parse_cbc_history(session_id: str, project_cwd: str | None = None, *, project_dir: str | None = None) -> list[dict]:
    """Parse cbc session JSONL into CLIConductor history format.

    project_cwd: filesystem path → auto-sanitize to cbc project dir
    project_dir:  cbc project dir name directly (e.g. "d-project-CLIConductor")

    Returns list of {"role": str, "content": str} blocks.
    """
    if project_dir:
        proj_dir = Path(os.path.expanduser("~/.codebuddy/projects")) / project_dir
    else:
        proj_dir = _project_dir(project_cwd)
    path = proj_dir / f"{session_id}.jsonl"
    if not path.exists():
        return []

    history: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            block = _event_to_block(event)
            if block:
                history.append(block)

    return history


def get_raw_usage(session_id: str, project_cwd: str | None = None, *, project_dir: str | None = None) -> list[dict]:
    """Extract rawUsage info from all assistant messages in a cbc session.

    project_cwd: filesystem path → auto-sanitize to cbc project dir
    project_dir:  cbc project dir name directly (e.g. "d-project-CLIConductor")

    Also scans subagent JSONL files (<session-dir>/subagents/agent-*.jsonl),
    since cbc spawns subagents that consume credits independently.

    Returns list of dicts, each containing rawUsage from one assistant message:
        {"model": str, "rawUsage": dict, "timestamp": str}
    """
    if project_dir:
        proj_dir = Path(os.path.expanduser("~/.codebuddy/projects")) / project_dir
    else:
        proj_dir = _project_dir(project_cwd)
    path = proj_dir / f"{session_id}.jsonl"
    if not path.exists():
        return []

    usage_entries: list[dict] = _extract_usage_entries(path)

    # 扫描子 agent 的 JSONL 文件
    subagents_dir = proj_dir / session_id / "subagents"
    if subagents_dir.is_dir():
        for agent_file in sorted(subagents_dir.glob("agent-*.jsonl")):
            usage_entries.extend(_extract_usage_entries(agent_file))

    return usage_entries


def _extract_usage_entries(filepath: Path) -> list[dict]:
    """从单个 JSONL 文件中提���所有带 rawUsage 的事件（assistant message + function_call）。"""
    entries: list[dict] = []
    try:
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # function_call events also carry rawUsage when the LLM
                # decides which tool to call — capture those credits too.
                pd = event.get("providerData", {})
                raw_usage = pd.get("rawUsage")
                if not raw_usage:
                    continue

                entries.append({
                    "model": pd.get("model", ""),
                    "rawUsage": raw_usage,
                    "timestamp": _ts_to_iso(event.get("timestamp", 0)),
                })
    except OSError:
        pass
    return entries


# ── internals ──

def _read_meta(proj_dir: Path, session_id: str) -> dict:
    meta_path = proj_dir / f"{session_id}.meta.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _can_resume(fpath: Path, min_bytes: int = 0) -> bool:
    """Quick check: is this JSONL large enough to be a valid session?"""
    try:
        return fpath.stat().st_size >= (min_bytes or 1)
    except OSError:
        return False


def _parse_summary(fpath: Path) -> tuple[str, int, str, str, str]:
    """Extract title, count, timestamps and model from a JSONL file."""
    custom_title = ""
    ai_title = ""
    user_title = ""
    msg_count = 0
    first_ts = ""
    last_ts = ""
    model = ""

    with open(fpath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_count += 1

            ts = event.get("timestamp")
            if ts and not first_ts:
                first_ts = _ts_to_iso(ts)
            if ts:
                last_ts = _ts_to_iso(ts)

            if not model and event.get("providerData", {}).get("model"):
                model = event["providerData"]["model"]

            # Title priority: custom-title > ai-title > user message
            # Track explicitly set titles separately from the user-message
            # fallback so they can override it even when appearing later.
            etype = event.get("type")
            if etype == "custom-title":
                custom_title = _strip_html(event.get("customTitle", ""))
            elif etype == "ai-title" and not custom_title:
                ai_title = _strip_html(event.get("aiTitle", ""))
            elif not user_title and etype == "message" and event.get("role") == "user":
                content = event.get("content") or []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") in ("input_text", "text"):
                            t = block.get("text", "").strip()
                            if t:
                                user_title = _strip_html(t)[:80]
                                break

    return custom_title or ai_title or user_title, msg_count, first_ts, last_ts, model


def get_session_title(session_id: str, cwd: str | None = None,
                      project_dir: str | None = None) -> str:
    """Extract the title that cbc assigned to a session (custom-title > ai-title)."""
    if project_dir:
        proj_dir = Path(os.path.expanduser("~/.codebuddy/projects")) / project_dir
    elif cwd:
        proj_dir = _project_dir(cwd)
    else:
        return ""
    path = proj_dir / f"{session_id}.jsonl"
    if not path.exists():
        return ""
    title, _, _, _, _ = _parse_summary(path)
    return title


def write_custom_title(session_id: str, title: str, cwd: str | None = None):
    """Write a custom-title event to a cbc session's JSONL file."""
    proj_dir = _project_dir(cwd)
    path = proj_dir / f"{session_id}.jsonl"
    if not path.exists():
        return
    import uuid
    event = {
        "id": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "type": "custom-title",
        "customTitle": title,
        "sessionId": session_id,
        "cwd": cwd or "",
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def fork_cbc_session(parent_id: str, name: str, cwd: str | None = None) -> str:
    """Fork a cbc session by copying JSONL + writing meta.json.

    Pure file operations — no cbc process spawned.
    Returns the new (pre-generated) cbc session ID.
    """
    import uuid as _uuid
    proj_dir = _project_dir(cwd)
    parent_path = proj_dir / f"{parent_id}.jsonl"

    if not parent_path.exists():
        raise FileNotFoundError(f"Parent session JSONL not found: {parent_path}")

    # Generate unique session ID
    new_id = str(_uuid.uuid4())
    new_path = proj_dir / f"{new_id}.jsonl"
    while new_path.exists():
        new_id = str(_uuid.uuid4())
        new_path = proj_dir / f"{new_id}.jsonl"

    # Copy JSONL
    import shutil
    shutil.copy2(parent_path, new_path)

    # Write meta.json
    meta_path = proj_dir / f"{new_id}.meta.json"
    meta = {
        "forkedFrom": parent_id,
        "forkedAt": int(time.time() * 1000),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")

    # Write custom-title
    write_custom_title(new_id, name, cwd)

    return new_id


def _strip_html(text: str) -> str:
    """Remove HTML tags and system-reminder markers from text."""
    text = re.sub(r"<[^>]*>", "", text)
    return text


def _ts_to_iso(ts: int) -> str:
    """Convert epoch ms to ISO string."""
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
    except (OSError, ValueError):
        return str(ts)


def _event_to_block(event: dict) -> dict | None:
    """Map a single cbc JSONL event to a CLIConductor history block."""
    etype = event.get("type")

    if etype == "message":
        role = event.get("role")
        # Content is at event["content"] directly (newer cbc format).
        # Fall back to event["message"]["content"] for older sessions.
        content_blocks = event.get("content") or []
        if not content_blocks:
            msg = event.get("message", {})
            content_blocks = msg.get("content", [])
        if not isinstance(content_blocks, list):
            content_blocks = []

        if role == "user":
            text = "".join(
                block.get("text", "")
                for block in content_blocks
                if isinstance(block, dict) and block.get("type") in ("input_text", "text", "user")
            )
            if text.strip():
                return {"role": "user", "content": text.strip()}
        elif role == "assistant":
            text = "".join(
                block.get("text", "")
                for block in content_blocks
                if isinstance(block, dict) and block.get("type") in ("text", "output_text", "assistant")
            )
            if text.strip():
                return {"role": "assistant", "content": text.strip()}

    elif etype == "reasoning":
        # Newer cbc stores reasoning in rawContent, fall back to content
        content_blocks = event.get("rawContent") or event.get("content") or []
        if isinstance(content_blocks, list):
            text = "".join(
                block.get("text", "")
                for block in content_blocks
                if isinstance(block, dict)
            )
            if text.strip():
                return {"role": "thinking", "content": text.strip()}

    elif etype == "function_call":
        # Same format as adapter.extract_assistant_blocks tool_use
        name = event.get("name", "?")
        args_raw = event.get("arguments") or event.get("args") or event.get("input") or {}
        if isinstance(args_raw, dict):
            args_str = json.dumps(args_raw, ensure_ascii=False)[:500]
        else:
            args_str = str(args_raw)[:500]
        return {"role": "tool", "content": f"{name}({args_str})"}

    # function_call_result is intentionally skipped — the live stdout path
    # does not store tool results in session history either.

    return None
