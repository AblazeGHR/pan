"""Claude Code session scanner and history parser.

Claude Code stores each conversation as a JSONL transcript under
``~/.claude/projects/<encoded-cwd>/<session_id>.jsonl``. This module reads it
read-only for listing / parsing / usage, and performs best-effort writes for
title rename and fork (file copy).

Relevant event types in the JSONL (subset we care about):
  - ``user``        : user message (message.content = string or block array)
  - ``assistant``   : assistant message (message.content = text/thinking/tool_use)
  - ``tool_result`` : result of a tool call (tooluseId, content, isError)
  - ``ai-title``    : AI-generated (or custom) title (aiTitle)
  - others (queue-operation, attachment, last-prompt, atis-latch, summary,
    system) : skipped for transcript / not relevant to Pan history.

Note: the JSONL does NOT contain a ``result`` event — the result (with
total_cost_usd) only appears on stdout during ``claude -p``. Hence get_raw_usage
reads per-turn usage from each ``assistant`` event's ``message.usage`` (token
counts accurate; cost not present in JSONL → 0). Cost is captured separately by
the adapter's enrich_after_result via the stdout result event.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path


def _projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def _encode_cwd(cwd: str) -> str:
    """Encode a filesystem cwd the way Claude Code names its project dir.

    Observed rule (``C:\\Users\\x\\AppData\\Local\\Temp\\claude-probe`` →
    ``C--Users-14709-AppData-Local-Temp-claude-probe``): replace ``:`` and both
    path separators (``\\`` and ``/``) with ``-``. Literal ``-`` in path
    components is preserved (it is not a separator).
    """
    cwd = os.path.normpath(cwd)
    for sep in (":", "\\", "/"):
        cwd = cwd.replace(sep, "-")
    return cwd


def _decode_cwd(encoded: str) -> str:
    """Best-effort reverse of _encode_cwd for display (ambiguous, informational)."""
    # Drive letter: "C--Users" → "C:\\Users"
    if "--" in encoded:
        head, tail = encoded.split("--", 1)
        return f"{head}:\\{tail.replace('-', '\\')}"
    return encoded.replace("-", "\\")


def _find_jsonl(session_id: str, cwd: str | None = None) -> Path | None:
    """Locate ``<session_id>.jsonl``.

    If *cwd* given, look under the encoded project dir first (where Pan's own
    spawns write). Fall back to a global rglob so sessions created elsewhere are
    still found.
    """
    base = _projects_dir()
    if not base.exists():
        return None
    if cwd:
        target = base / _encode_cwd(cwd) / f"{session_id}.jsonl"
        if target.is_file():
            return target
    # global search
    hits = list(base.rglob(f"{session_id}.jsonl"))
    return hits[0] if hits else None


def _iter_jsonl(path: Path):
    """Yield parsed events from a claude JSONL (skips blank/corrupt lines)."""
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


def _iso_ts(ts: str | None) -> str:
    if not ts:
        return ""
    # already ISO (e.g. "2026-08-26T15:14:41.550Z"); pass through
    return str(ts)


# ── list ──

def list_sessions(cwd: str | None = None) -> list[dict]:
    """List Claude Code sessions.

    cwd: if provided, only sessions whose project dir encodes that cwd.

    Returns a list of dicts with keys: session_id, title, workDir, createdAt,
    updatedAt, message_count, model, parent_id.
    """
    base = _projects_dir()
    if not base.exists():
        return []

    # determine candidate project dirs
    if cwd:
        encoded = _encode_cwd(cwd)
        proj_dir = base / encoded
        dirs = [proj_dir] if proj_dir.is_dir() else []
    else:
        dirs = [d for d in base.iterdir() if d.is_dir()]

    sessions: list[dict] = []
    for proj in dirs:
        for jsonl in proj.glob("*.jsonl"):
            sid = jsonl.stem
            info = _scan_session(jsonl)
            if info is None:
                continue
            sessions.append({
                "session_id": sid,
                "title": info.get("title", ""),
                "workDir": info.get("cwd", "") or _decode_cwd(proj.name),
                "createdAt": info.get("createdAt", ""),
                "updatedAt": info.get("updatedAt", ""),
                "message_count": info.get("message_count", 0),
                "model": info.get("model", ""),
                "parent_id": "",
            })

    sessions.sort(key=lambda s: s.get("updatedAt") or "", reverse=True)
    return sessions


def _scan_session(jsonl: Path) -> dict | None:
    """Extract lightweight metadata from a session JSONL (no full history)."""
    cwd = ""
    title = ""
    model = ""
    created = ""
    updated = ""
    user_n = 0
    asst_n = 0
    try:
        for ev in _iter_jsonl(jsonl):
            t = ev.get("type")
            ts = ev.get("timestamp", "")
            if not updated and ts:
                updated = ts
            if not created and ts:
                created = ts
            if t == "user":
                user_n += 1
                if not cwd:
                    cwd = ev.get("cwd", "") or ""
            elif t == "assistant":
                asst_n += 1
                if not model:
                    model = (ev.get("message", {}) or {}).get("model", "") or ""
            elif t == "ai-title" and not title:
                title = ev.get("aiTitle", "") or ""
    except Exception:
        return None
    if user_n == 0 and asst_n == 0:
        return None
    return {
        "cwd": cwd,
        "title": title,
        "model": model,
        "createdAt": created,
        "updatedAt": updated,
        "message_count": user_n + asst_n,
    }


# ── parse history ──

def parse_history(session_id: str, cwd: str | None = None) -> list[dict]:
    """Parse a Claude Code session JSONL into Pan history format.

    Returns a list of {"role": str, "content": str} blocks.
    """
    path = _find_jsonl(session_id, cwd)
    if path is None:
        return []

    history: list[dict] = []
    for ev in _iter_jsonl(path):
        t = ev.get("type")
        if t == "user":
            content = _user_content(ev)
            if content:
                history.append({"role": "user", "content": content})
        elif t == "assistant":
            history.extend(_assistant_blocks(ev))
        elif t == "tool_result":
            content = _tool_result_content(ev)
            if content:
                history.append({"role": "tool", "content": content})
    return history


def _user_content(ev: dict) -> str:
    msg = ev.get("message", {}) or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        text = "\n".join(p for p in parts if p).strip()
        return text
    # some user events store the prompt at top level
    prompt = ev.get("prompt") or ev.get("content")
    if isinstance(prompt, str):
        return prompt.strip()
    return ""


def _assistant_blocks(ev: dict) -> list[dict]:
    blocks: list[dict] = []
    msg = ev.get("message", {}) or {}
    for b in msg.get("content", []) or []:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text":
            text = b.get("text", "")
            if text:
                blocks.append({"role": "assistant", "content": text})
        elif bt == "thinking":
            thinking = b.get("thinking", "")
            if thinking:
                blocks.append({"role": "thinking", "content": thinking})
        elif bt == "tool_use":
            name = b.get("name", "?")
            inp = b.get("input", {})
            inp_str = (json.dumps(inp, ensure_ascii=False)
                       if isinstance(inp, (dict, list)) else str(inp or ""))
            blocks.append({"role": "tool", "content": f"{name}({inp_str})"})
    return blocks


def _tool_result_content(ev: dict) -> str:
    content = ev.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        text = "\n".join(p for p in parts if p)
    else:
        text = str(content or "")
    text = text.strip()
    if not text:
        return ""
    if ev.get("isError"):
        return f"[tool error] {text}"
    return f"→ {text}"


# ── usage ──

def get_raw_usage(session_id: str, cwd: str | None = None) -> list[dict]:
    """Extract per-turn usage from a Claude Code session JSONL.

    Returns a list with one dict per assistant turn:
    {"model", "rawUsage": {prompt/completion/cache tokens}, "timestamp"}.

    Cost is not present in JSONL (see module docstring) → 0.
    """
    path = _find_jsonl(session_id, cwd)
    if path is None:
        return []
    entries: list[dict] = []
    for ev in _iter_jsonl(path):
        if ev.get("type") != "assistant":
            continue
        usage = (ev.get("message", {}) or {}).get("usage") or {}
        if not usage:
            continue
        model = (ev.get("message", {}) or {}).get("model", "") or ""
        entries.append({
            "model": model,
            "rawUsage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
                "cost": 0.0,
            },
            "timestamp": _iso_ts(ev.get("timestamp")),
        })
    return entries


# ── title ──

def get_session_title(session_id: str, cwd: str | None = None) -> str:
    """Return the title stored for a Claude Code session (ai-title event)."""
    path = _find_jsonl(session_id, cwd)
    if path is None:
        return ""
    title = ""
    first_user = ""
    for ev in _iter_jsonl(path):
        t = ev.get("type")
        if t == "ai-title" and not title:
            title = ev.get("aiTitle", "") or ""
        elif t == "user" and not first_user:
            first_user = _user_content(ev)
        if title and first_user:
            break
    if title:
        return title
    # fallback: first user message (truncated)
    if first_user:
        return first_user[:60]
    return ""


def write_custom_title(session_id: str, title: str, cwd: str | None = None) -> None:
    """Persist a custom title into the session JSONL (best-effort).

    Updates the existing ``ai-title`` event's ``aiTitle`` in place (or appends a
    new one). This makes get_session_title return the custom title within Pan.
    Claude may regenerate the title on its next native run, so this is
    best-effort (mirrors cbc's append-custom-title approach).
    """
    path = _find_jsonl(session_id, cwd)
    if path is None:
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    new_event = {"type": "ai-title", "aiTitle": title, "sessionId": session_id}
    updated = False
    out: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if ev.get("type") == "ai-title" and not updated:
            ev["aiTitle"] = title
            updated = True
        out.append(json.dumps(ev, ensure_ascii=False))

    if not updated:
        out.append(json.dumps(new_event, ensure_ascii=False))

    try:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError:
        pass


# ── existence guard ──

def session_exists(session_id: str, cwd: str | None = None) -> bool:
    """SessionsProvider 可选能力：session 是否真实存在于 JSONL（import guard）。"""
    return _find_jsonl(session_id, cwd) is not None


# ── fork ──

def fork_session(parent_id: str, name: str, cwd: str | None = None) -> str:
    """Fork a Claude Code session by copying its JSONL to a new session id.

    Claude Code has no native ``--fork`` (it only resumes when a real run
    commits). We copy the transcript under a new UUID filename in the same
    project dir, so ``claude -p --resume <new_id>`` loads the copied history
    (mirrors kimi's file-copy approach). The new id is written into
    cli_session_id by the caller (server branch endpoint / adapter.fork_args).

    Returns the new Claude Code session id.
    """
    parent = _find_jsonl(parent_id, cwd)
    if parent is None:
        raise FileNotFoundError(f"Claude session not found: {parent_id}")

    proj = parent.parent
    new_id = str(_uuid.uuid4())
    # avoid (vanishingly unlikely) collision
    while (proj / f"{new_id}.jsonl").exists():
        new_id = str(_uuid.uuid4())
    new_path = proj / f"{new_id}.jsonl"

    try:
        shutil.copyfile(parent, new_path)
    except OSError as e:
        raise FileNotFoundError(f"Failed to copy Claude session: {e}") from e

    # record the custom fork title as an ai-title event (best-effort)
    try:
        write_custom_title(new_id, name, cwd)
    except Exception:
        pass

    return new_id
