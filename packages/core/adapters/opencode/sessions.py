"""OpenCode session scanner and history parser.

OpenCode stores sessions in a SQLite database at
``~/.local/share/opencode/opencode.db`` (event-sourced). This module reads it
read-only for listing / parsing / usage, and performs controlled writes only for
fork (row duplication) and title rename.

Why not ``opencode session list`` / ``opencode export``? Those subcommands are
scoped to the current working directory's project and return "Session not found"
for sessions rooted elsewhere (verified 2026-08-26). Reading the DB directly is
authoritative and directory-independent.

Schema (relevant columns):
  session(id, parent_id, slug, directory, title, model JSON{modelID,providerID,variant},
          agent, permission, cost, tokens_input, tokens_output, tokens_reasoning,
          tokens_cache_read, tokens_cache_write, time_created, time_updated)
  message(id, session_id, time_created, time_updated, data JSON{role, model, ...})
  part(id, message_id, session_id, time_created, time_updated, data JSON)
        part.data.type: "text" | "reasoning" | "step-start" | "step-finish" | "tool"
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path


def _db_path() -> Path:
    return Path(os.path.expanduser("~/.local/share/opencode/opencode.db"))


def _connect_ro() -> sqlite3.Connection:
    p = _db_path()
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def _connect_rw() -> sqlite3.Connection:
    p = _db_path()
    return sqlite3.connect(str(p))


def _iso_ts(ts_ms: int | None) -> str:
    if not ts_ms:
        return ""
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
    except (OSError, ValueError):
        return str(ts_ms)


def _model_str(model_json: str | None) -> str:
    """Extract 'provider/model' from the session.model JSON column."""
    if not model_json:
        return ""
    try:
        m = json.loads(model_json)
    except (json.JSONDecodeError, TypeError):
        return str(model_json)
    pid = m.get("providerID") or ""
    mid = m.get("id") or ""
    if pid and mid:
        return f"{pid}/{mid}"
    return mid or pid or ""


def list_opencode_sessions(project_cwd: str | None = None) -> list[dict]:
    """List OpenCode sessions from the SQLite DB.

    project_cwd: if provided, only return sessions whose ``directory`` matches
    (case/separator-insensitive).

    Returns a list of dicts with keys: session_id, title, workDir, createdAt,
    updatedAt, message_count, model, parent_id.
    """
    if not _db_path().exists():
        return []
    try:
        con = _connect_ro()
    except sqlite3.Error:
        return []
    rows: list[dict] = []
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id, parent_id, title, directory, model, "
            "tokens_input, tokens_output, time_created, time_updated "
            "FROM session ORDER BY time_updated DESC"
        )
        for r in cur.fetchall():
            (
                sid, parent_id, title, directory, model,
                t_in, t_out, t_created, t_updated,
            ) = r
            if project_cwd:
                try:
                    if str(Path(directory).resolve()).lower() != str(Path(project_cwd).resolve()).lower():
                        continue
                except OSError:
                    continue
            cur2 = con.cursor()
            cur2.execute(
                "SELECT COUNT(*) FROM message WHERE session_id=? AND data LIKE ?",
                (sid, '%"role":"user"%'),
            )
            user_n = cur2.fetchone()[0]
            cur2.execute(
                "SELECT COUNT(*) FROM message WHERE session_id=? AND data LIKE ?",
                (sid, '%"role":"assistant"%'),
            )
            asst_n = cur2.fetchone()[0]
            rows.append({
                "session_id": sid,
                "parent_id": parent_id,
                "title": title or "",
                "workDir": directory or "",
                "createdAt": _iso_ts(t_created),
                "updatedAt": _iso_ts(t_updated),
                "message_count": user_n + asst_n,
                "model": _model_str(model),
            })
    finally:
        con.close()
    return rows


def parse_opencode_history(session_id: str, workdir: str | None = None) -> list[dict]:
    """Parse an OpenCode session into Pan history format.

    Returns a list of {"role": str, "content": str} blocks, ordered by creation.
    Maps DB ``part`` rows to Pan blocks:
      type "text"   -> user (if message.role==user) or assistant
      type "reasoning" -> thinking
      type "tool"   -> tool (name + input + output)
    """
    if not _db_path().exists():
        return []
    try:
        con = _connect_ro()
    except sqlite3.Error:
        return []
    history: list[dict] = []
    try:
        cur = con.cursor()
        # message role lookup
        cur.execute(
            "SELECT id, data FROM message WHERE session_id=?", (session_id,)
        )
        msg_role: dict[str, str] = {}
        for mid, data in cur.fetchall():
            try:
                d = json.loads(data)
                msg_role[mid] = d.get("role", "")
            except (json.JSONDecodeError, TypeError):
                msg_role[mid] = ""

        cur.execute(
            "SELECT data FROM part WHERE session_id=? ORDER BY time_created ASC",
            (session_id,),
        )
        for (data,) in cur.fetchall():
            try:
                p = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                continue
            ptype = p.get("type", "")
            role = msg_role.get(p.get("messageID", ""), "")
            if ptype == "text":
                text = p.get("text", "")
                if not text:
                    continue
                blk_role = "assistant" if role == "assistant" else "user"
                history.append({"role": blk_role, "content": text})
            elif ptype == "reasoning":
                text = p.get("text", "")
                if text:
                    history.append({"role": "thinking", "content": text})
            elif ptype == "tool":
                tool = p.get("tool") or p.get("name") or "tool"
                inp = p.get("input")
                out = p.get("output")
                inp_str = json.dumps(inp, ensure_ascii=False) if isinstance(inp, (dict, list)) else str(inp or "")
                content = f"{tool}({inp_str})"
                if out:
                    content += f"\n→ {out}"
                history.append({"role": "tool", "content": content})
            # step-start / step-finish carry no direct transcript text
    finally:
        con.close()
    return history


def get_raw_usage(session_id: str, workdir: str | None = None) -> list[dict]:
    """Extract usage for an OpenCode session from the SQLite DB.

    Returns a list with a single dict: {"model", "rawUsage", "timestamp"}.
    """
    if not _db_path().exists():
        return []
    try:
        con = _connect_ro()
    except sqlite3.Error:
        return []
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT model, tokens_input, tokens_output, tokens_reasoning, "
            "tokens_cache_read, tokens_cache_write, cost, time_updated "
            "FROM session WHERE id=?",
            (session_id,),
        )
        r = cur.fetchone()
        if not r:
            return []
        (
            model, t_in, t_out, t_reason, t_cr, t_cw, cost, t_updated,
        ) = r
        return [{
            "model": _model_str(model),
            "rawUsage": {
                "prompt_tokens": t_in or 0,
                "completion_tokens": t_out or 0,
                "reasoning_tokens": t_reason or 0,
                "cache_read_tokens": t_cr or 0,
                "cache_write_tokens": t_cw or 0,
                "cost": cost or 0.0,
            },
            "timestamp": _iso_ts(t_updated),
        }]
    finally:
        con.close()


def get_session_title(session_id: str, workdir: str | None = None) -> str:
    """Return the title stored for an OpenCode session."""
    if not _db_path().exists():
        return ""
    try:
        con = _connect_ro()
    except sqlite3.Error:
        return ""
    try:
        cur = con.cursor()
        cur.execute("SELECT title FROM session WHERE id=?", (session_id,))
        r = cur.fetchone()
        return r[0] if r and r[0] else ""
    finally:
        con.close()


def write_custom_title(session_id: str, title: str, workdir: str | None = None) -> None:
    """Persist a custom title into the OpenCode session row (G7)."""
    if not _db_path().exists():
        return
    try:
        con = _connect_rw()
    except sqlite3.Error:
        return
    try:
        con.execute("UPDATE session SET title=? WHERE id=?", (title, session_id))
        con.commit()
    except sqlite3.Error:
        pass
    finally:
        con.close()


def fork_opencode_session(parent_id: str, name: str, workdir: str | None = None) -> str:
    """Fork an OpenCode session by duplicating its DB rows.

    OpenCode has no headless ``--fork`` (it only forks when a real run commits,
    which needs a working API key). So we duplicate the session/message/part
    rows under a new session id with ``parent_id`` set, giving a resumable
    fork that shares history. Mirrors kimi's file-copy approach.

    Returns the new OpenCode session id.
    """
    if not _db_path().exists():
        raise FileNotFoundError(f"OpenCode DB not found for session {parent_id}")

    con = _connect_rw()
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id, project_id, workspace_id, parent_id, slug, directory, "
            "path, title, version, share_url, summary_additions, summary_deletions, "
            "summary_files, summary_diffs, metadata, cost, tokens_input, "
            "tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, "
            "revert, permission, agent, model, time_created, time_updated, "
            "time_compacting, time_archived FROM session WHERE id=?",
            (parent_id,),
        )
        row = cur.fetchone()
        if not row:
            raise FileNotFoundError(f"OpenCode session not found: {parent_id}")

        new_id = f"ses_{_uuid.uuid4().hex}"
        # Build insert with same columns but new id / parent_id / title / timestamps
        cols = [
            "id", "project_id", "workspace_id", "parent_id", "slug", "directory",
            "path", "title", "version", "share_url", "summary_additions",
            "summary_deletions", "summary_files", "summary_diffs", "metadata",
            "cost", "tokens_input", "tokens_output", "tokens_reasoning",
            "tokens_cache_read", "tokens_cache_write", "revert", "permission",
            "agent", "model", "time_created", "time_updated", "time_compacting",
            "time_archived",
        ]
        now = int(__import__("time").time() * 1000)
        new_row = list(row)
        # id (idx0), parent_id (idx3), title (idx7), time_created/updated
        new_row[0] = new_id
        new_row[3] = parent_id
        new_row[7] = name
        new_row[25] = now
        new_row[26] = now

        placeholders = ",".join("?" for _ in cols)
        col_list = ",".join(cols)
        cur.execute(
            f"INSERT INTO session ({col_list}) VALUES ({placeholders})",
            tuple(new_row),
        )

        # Duplicate messages
        cur.execute(
            "SELECT id, time_created, time_updated, data FROM message WHERE session_id=?",
            (parent_id,),
        )
        msg_map: dict[str, str] = {}
        for mid, tc, tu, data in cur.fetchall():
            new_mid = f"msg_{_uuid.uuid4().hex}"
            msg_map[mid] = new_mid
            cur.execute(
                "INSERT INTO message (id, session_id, time_created, time_updated, data) "
                "VALUES (?,?,?,?,?)",
                (new_mid, new_id, tc, tu, data),
            )

        # Duplicate parts (remap message_id)
        cur.execute(
            "SELECT id, message_id, time_created, time_updated, data FROM part WHERE session_id=?",
            (parent_id,),
        )
        for pid, message_id, tc, tu, data in cur.fetchall():
            new_pid = f"prt_{_uuid.uuid4().hex}"
            new_mid = msg_map.get(message_id, message_id)
            cur.execute(
                "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) "
                "VALUES (?,?,?,?,?,?)",
                (new_pid, new_mid, new_id, tc, tu, data),
            )

        con.commit()
        return new_id
    finally:
        con.close()
