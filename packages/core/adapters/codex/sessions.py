"""OpenAI Codex CLI session scanner and history parser.

Codex stores threads in two SQLite databases under ``~/.codex``:

- ``state_5.sqlite`` — ``threads`` 元数据表（id / rollout_path / cwd / title /
  model / model_provider / created_at / updated_at / archived ...）+ 
  ``thread_spawn_edges``（fork 父子关系）。
- ``thread_history_1.sqlite`` — ``thread_items``（thread_id / item_json /
  item_type），逐条 transcript；``thread_turns``（轮次状态）。
- ``sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl`` — 完整事件日志，其中
  ``event_msg`` payload.type=token_count 携带 token usage（``total_token_usage``）。

Why not ``codex session list`` / ``codex exec resume --last``？这些命令返回的是 CLI
视角的展示信息且受 cwd 过滤；直接读 DB 是权威且目录无关的（与 opencode 同思路）。
本模块只读访问，写操作（title / fork）仅在显式调用时进行。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid as _uuid
from datetime import datetime
from pathlib import Path


# Match the Codex CLI's own home selection.  This matters for isolated
# profiles, CI, and users who keep separate authenticated Codex environments.
_CODEX_DIR = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
_STATE_DB = _CODEX_DIR / "state_5.sqlite"
_HISTORY_DB = _CODEX_DIR / "thread_history_1.sqlite"
_SESSIONS_DIR = _CODEX_DIR / "sessions"

# 平台分支开关（模块级常量，便于测试 monkeypatch 模拟另一平台）
_IS_WINDOWS = os.name == "nt"


# ── 连接与工具 ──


def _connect_ro(db: Path) -> sqlite3.Connection | None:
    if not db.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _connect_rw(db: Path) -> sqlite3.Connection | None:
    if not db.is_file():
        return None
    try:
        return sqlite3.connect(str(db))
    except sqlite3.Error:
        return None


def _iso_ts(ts: int | None) -> str:
    """epoch 秒 → ISO 字符串（与 cbc/opencode 的 _iso_ts 同构）。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).isoformat()
    except (OSError, ValueError):
        return str(ts)


def _norm_path(p) -> str:
    """规范化路径用于 cwd 过滤：剥离 \\?\\ 长路径前缀、统一分隔符与大小写。

    Windows：casefold + 统一为反斜杠（NTFS 大小写不敏感、/ 与 \\ 等价）。
    POSIX：仅 normpath（大小写敏感，且反斜杠是合法文件名字符，不可当分隔符替换）。
    """
    s = str(p or "").replace("\\\\?\\", "").replace("\\?\\", "")
    try:
        s = os.path.normpath(s)
    except Exception:  # noqa: BLE001
        pass
    if _IS_WINDOWS:
        return s.casefold().replace("/", "\\").rstrip("\\")
    return s.rstrip("/")


def _cwd_matches(thread_cwd, project_cwd) -> bool:
    """Return whether a Codex thread belongs to a requested workdir.

    Codex may persist the git repository root even when Pan launched it from a
    nested workdir. Treat that stored root as an ancestor match, but keep a
    separator boundary so similarly-prefixed directories do not leak into the
    result (``repo`` must not match ``repo-other``).
    """
    thread = _norm_path(thread_cwd)
    project = _norm_path(project_cwd)
    if not thread or not project:
        return thread == project
    if thread == project:
        return True
    separator = "\\" if _IS_WINDOWS else "/"
    return project.startswith(thread + separator)


def _rollout_full_path(rollout_path: str | None) -> Path | None:
    if not rollout_path:
        return None
    p = Path(rollout_path)
    if not p.is_absolute():
        p = _SESSIONS_DIR / p
    return p if p.is_file() else None


def _thread_row(state_con: sqlite3.Connection, session_id: str) -> dict | None:
    cur = state_con.cursor()
    try:
        cur.execute("SELECT * FROM threads WHERE id=?", (session_id,))
    except sqlite3.Error:
        return None
    r = cur.fetchone()
    if not r:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, r))


def _parent_of(thread_spawn_edges_con, child_id: str) -> str:
    try:
        cur = thread_spawn_edges_con.cursor()
        cur.execute(
            "SELECT parent_thread_id FROM thread_spawn_edges WHERE child_thread_id=?",
            (child_id,),
        )
        r = cur.fetchone()
        return r[0] if r else ""
    except sqlite3.Error:
        return ""


# ── 会话列表 ──


def list_codex_sessions(project_cwd: str | None = None) -> list[dict]:
    """列 codex threads（来自 state_5.sqlite threads 表）。

    project_cwd：若提供，仅返回 cwd 匹配（大小写/分隔符不敏感）的会话。
    返回 dicts：session_id / title / workDir / createdAt / updatedAt /
    message_count / model / parent_id。
    """
    con = _connect_ro(_STATE_DB)
    if con is None:
        return []
    hcon = _connect_ro(_HISTORY_DB)
    try:
        cur = con.cursor()
        try:
            cur.execute(
                "SELECT id, title, cwd, model, model_provider, created_at, updated_at "
                "FROM threads WHERE archived=0 ORDER BY updated_at DESC"
            )
        except sqlite3.Error:
            return []
        rows: list[dict] = []
        for r in cur.fetchall():
            sid, title, cwd, model, provider, created, updated = r
            if project_cwd and not _cwd_matches(cwd, project_cwd):
                continue
            msg_count = 0
            if hcon is not None:
                try:
                    c2 = hcon.cursor()
                    c2.execute(
                        "SELECT COUNT(*) FROM thread_items WHERE thread_id=? "
                        "AND item_type IN ('userMessage','agentMessage')",
                        (sid,),
                    )
                    msg_count = c2.fetchone()[0]
                except sqlite3.Error:
                    msg_count = 0
            rows.append({
                "session_id": sid,
                "parent_id": _parent_of(con, sid),
                "title": title or "",
                "workDir": cwd or "",
                "createdAt": _iso_ts(created),
                "updatedAt": _iso_ts(updated),
                "message_count": msg_count,
                "model": model or provider or "",
            })
        return rows
    finally:
        con.close()
        if hcon is not None:
            hcon.close()


# ── 历史解析 ──


def _item_to_block(item: dict) -> dict | None:
    """单个 codex thread_item 的 item_json → Pan history block。

    持久化 thread_items 用 camelCase（agentMessage / commandExecution / userMessage），
    live rollout 可能是 snake_case；统一去掉下划线归一后匹配。
    """
    itype = (item.get("type") or "").replace("_", "").lower()
    if itype == "usermessage":
        parts = item.get("content") or []
        text = "".join(
            b.get("text", "") for b in parts
            if isinstance(b, dict) and b.get("type") == "text"
        )
        return {"role": "user", "content": text} if text else None
    if itype == "agentmessage":
        text = item.get("text", "")
        return {"role": "assistant", "content": text} if text else None
    if itype == "reasoning":
        text = item.get("text") or ""
        if not text:
            summary = item.get("summary") or []
            if summary:
                text = summary[0] if isinstance(summary[0], str) else str(summary[0])
        return {"role": "thinking", "content": text} if text else None
    if itype == "commandexecution":
        cmd = item.get("command", "")
        # app-server stores camelCase fields; legacy exec rollouts use the
        # snake_case spelling.  Accept both so switching protocols does not
        # lose tool output after a refresh.
        out = item.get("aggregated_output") or item.get("aggregatedOutput", "")
        content = cmd
        if out:
            content += "\n→ " + out
        return {"role": "tool", "content": content}
    if itype in ("functioncall", "mcptoolcall", "dynamictoolcall"):
        name = (item.get("name") or item.get("tool") or item.get("pluginId")
                or item.get("server") or "tool")
        args = item.get("arguments") or item.get("parameters") or item.get("input") or {}
        inp = json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args or "")
        out = item.get("output") or item.get("result") or item.get("error") or ""
        content = f"{name}({inp})"
        if out:
            content += "\n→ " + out
        return {"role": "tool", "content": content}
    return None


def parse_codex_history(session_id: str, workdir: str | None = None) -> list[dict]:
    """解析 codex thread 为 Pan history 格式（user/assistant/thinking/tool 块）。"""
    con = _connect_ro(_HISTORY_DB)
    if con is None:
        return []
    history: list[dict] = []
    try:
        cur = con.cursor()
        try:
            cur.execute(
                "SELECT item_json FROM thread_items WHERE thread_id=? "
                "ORDER BY created_at_ms ASC",
                (session_id,),
            )
        except sqlite3.Error:
            return []
        for (data,) in cur.fetchall():
            try:
                item = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                continue
            block = _item_to_block(item)
            if block:
                history.append(block)
        return history
    finally:
        con.close()


# ── usage ──


def get_codex_raw_usage(session_id: str, workdir: str | None = None) -> list[dict]:
    """提取 codex thread 的聚合 token usage（来自 rollout 的 token_count 事件）。

    返回单元素列表 {"model","rawUsage","timestamp"}（session 级聚合，与
    opencode 的 get_raw_usage 同构）；无 rollout/usage 时返回 []。
    rawUsage 字段对齐 codex live JSONL 的 turn.completed usage：
    input_tokens / cached_input_tokens / cache_write_input_tokens / output_tokens /
    reasoning_output_tokens / total_tokens。
    """
    con = _connect_ro(_STATE_DB)
    if con is None:
        return []
    try:
        row = _thread_row(con, session_id)
        if not row:
            return []
        rollout = _rollout_full_path(row.get("rollout_path"))
        model = row.get("model") or row.get("model_provider") or ""
        if not rollout:
            return []
        last_usage: dict | None = None
        last_ts = ""
        for line in _iter_jsonl(rollout):
            etype = line.get("type")
            if etype != "event_msg":
                continue
            payload = line.get("payload") or {}
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            usage = info.get("total_token_usage") or info.get("last_token_usage")
            if isinstance(usage, dict):
                last_usage = usage
                last_ts = line.get("timestamp") or ""
        if not last_usage:
            return []
        return [{
            "model": model,
            "rawUsage": {
                "input_tokens": last_usage.get("input_tokens", 0),
                "cached_input_tokens": last_usage.get("cached_input_tokens", 0),
                "cache_write_input_tokens": last_usage.get("cache_write_input_tokens", 0),
                "output_tokens": last_usage.get("output_tokens", 0),
                "reasoning_output_tokens": last_usage.get("reasoning_output_tokens", 0),
                "total_tokens": last_usage.get("total_tokens", 0),
            },
            "timestamp": last_ts,
        }]
    finally:
        con.close()


def _iter_jsonl(path: Path):
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


# ── 标题 ──


def get_codex_session_title(session_id: str, workdir: str | None = None) -> str:
    con = _connect_ro(_STATE_DB)
    if con is None:
        return ""
    try:
        cur = con.cursor()
        try:
            cur.execute("SELECT title FROM threads WHERE id=?", (session_id,))
        except sqlite3.Error:
            return ""
        r = cur.fetchone()
        return r[0] if r and r[0] else ""
    finally:
        con.close()


def write_codex_custom_title(session_id: str, title: str, workdir: str | None = None) -> None:
    """写入自定义标题（同时更新 title 与 name 两列，兼容 codex UI 展示）。"""
    con = _connect_rw(_STATE_DB)
    if con is None:
        return
    try:
        con.execute(
            "UPDATE threads SET title=?, name=? WHERE id=?",
            (title, title, session_id),
        )
        con.commit()
    except sqlite3.Error:
        pass
    finally:
        con.close()


# ── fork ──


def fork_codex_session(parent_id: str, name: str, workdir: str | None = None) -> str:
    """Fork 一个 codex thread：复制 state threads 行 + history items/turns。

    codex CLI 无 headless ``--fork``（``codex fork`` 是交互 picker），故直接复制
    DB 行：在 state_5.threads 建新线程（parent 指向保持 cwd/title=name），并把
    thread_items / thread_turns 复制到新 thread_id（resume 时 codex 从
    thread_history_1.sqlite 重建上下文）。rollout_path 指向按新 id 生成的新路径
    （首次 resume 时 codex 会新建）。

    Returns the new thread id.
    """
    scon = _connect_rw(_STATE_DB)
    if scon is None:
        raise FileNotFoundError(f"Codex state DB not found for session {parent_id}")
    hcon = _connect_rw(_HISTORY_DB)
    try:
        parent = _thread_row(scon, parent_id)
        if not parent:
            raise FileNotFoundError(f"Codex thread not found: {parent_id}")

        new_id = str(_uuid.uuid4())
        now = int(datetime.now().timestamp())
        now_ms = now * 1000
        # 新 rollout 路径（codex 命名：sessions/<Y>/<m>/<d>/rollout-<ts>-<id>.jsonl）
        dt = datetime.now()
        new_rollout = (
            _CODEX_DIR / "sessions" / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.day:02d}"
            / f"rollout-{dt.strftime('%Y-%m-%dT%H-%M-%S')}-{new_id}.jsonl"
        )

        cols = list(parent.keys())
        new_row = dict(parent)
        new_row["id"] = new_id
        new_row["title"] = name
        new_row["name"] = name
        new_row["rollout_path"] = str(new_rollout)
        new_row["created_at"] = now
        new_row["updated_at"] = now
        new_row["created_at_ms"] = now_ms
        new_row["updated_at_ms"] = now_ms
        new_row["recency_at"] = now
        new_row["recency_at_ms"] = now_ms
        new_row["archived"] = 0
        new_row["archived_at"] = None

        # 忽略数据库新增而当前 schema 快照不存在的列（防御）
        placeholders = ",".join("?" for _ in cols)
        scon.execute(
            f"INSERT INTO threads ({','.join(cols)}) VALUES ({placeholders})",
            [new_row[c] for c in cols],
        )

        # 复制 items / turns（history DB）
        if hcon is not None:
            hcon.execute(
                "INSERT INTO thread_items "
                "(thread_id, turn_id, item_id, rollout_ordinal, created_at_ms, "
                "item_json, item_type, updated_at_ordinal) "
                "SELECT ?, turn_id, item_id, rollout_ordinal, created_at_ms, "
                "item_json, item_type, updated_at_ordinal "
                "FROM thread_items WHERE thread_id=?",
                (new_id, parent_id),
            )
            hcon.execute(
                "INSERT INTO thread_turns "
                "(thread_id, turn_id, rollout_ordinal, status, error_json, started_at, "
                "completed_at, duration_ms, first_user_item_id, final_agent_item_id, "
                "rollout_byte_offset, rollout_end_ordinal, rollout_end_byte_offset) "
                "SELECT ?, turn_id, rollout_ordinal, status, error_json, started_at, "
                "completed_at, duration_ms, first_user_item_id, final_agent_item_id, "
                "rollout_byte_offset, rollout_end_ordinal, rollout_end_byte_offset "
                "FROM thread_turns WHERE thread_id=?",
                (new_id, parent_id),
            )
            hcon.commit()

        # 物化新 rollout：codex resume 依赖 rollout 文件（新路径必须存在，且其
        # session_meta 必须属于新线程）。复制父 rollout 并重写其中的父 thread id
        # （完整 UUID，实测仅出现在 session_meta.session_id/id 与 event_msg.thread_id
        # 字段），否则首次 resume 报 "no rollout found for thread id ..." 或
        # "session metadata ... belongs to thread ..."。best-effort：复制失败不阻塞
        # fork（后续 resume 时由 codex 报错）。
        src_rollout = _rollout_full_path(parent.get("rollout_path"))
        if src_rollout is not None:
            try:
                new_rollout.parent.mkdir(parents=True, exist_ok=True)
                payload = src_rollout.read_text(encoding="utf-8")
                if parent_id in payload:
                    payload = payload.replace(parent_id, new_id)
                new_rollout.write_text(payload, encoding="utf-8")
            except OSError:
                pass

        # 记录 fork 父子关系（best-effort；部分版本无此表/状态枚举差异，失败忽略）
        try:
            scon.execute(
                "INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) "
                "VALUES (?,?,?)",
                (parent_id, new_id, "forked"),
            )
        except sqlite3.Error:
            pass
        scon.commit()
        return new_id
    finally:
        scon.close()
        if hcon is not None:
            hcon.close()


# ── SessionsProvider 统一接口（adapter-architecture P0-2）──
# 与 cbc/kimi/opencode 的 sessions 模块对齐协议命名，供 server 按 adapter 名统一调用。
# 旧命名函数（list_codex_sessions / fork_codex_session 等）保留。


def list_sessions(cwd: str | None = None) -> list[dict]:
    """SessionsProvider：列 codex 会话（cwd 即 thread.cwd 过滤语义）。"""
    return list_codex_sessions(cwd)


def parse_history(session_id: str, cwd: str | None = None) -> list[dict]:
    """SessionsProvider：解析 codex thread 历史。"""
    return parse_codex_history(session_id, cwd)


def get_raw_usage(session_id: str, cwd: str | None = None) -> list[dict]:
    """SessionsProvider：提取 codex thread usage。"""
    return get_codex_raw_usage(session_id, cwd)


def get_session_title(session_id: str, cwd: str | None = None) -> str:
    """SessionsProvider：返回 codex thread 标题。"""
    return get_codex_session_title(session_id, cwd)


def write_custom_title(session_id: str, title: str, cwd: str | None = None) -> None:
    """SessionsProvider：写入自定义标题。"""
    write_codex_custom_title(session_id, title, cwd)


def session_exists(session_id: str, cwd: str | None = None) -> bool:
    """SessionsProvider 可选能力：thread 是否真实存在于 state DB（import guard）。"""
    con = _connect_ro(_STATE_DB)
    if con is None:
        return False
    try:
        cur = con.cursor()
        cur.execute("SELECT 1 FROM threads WHERE id=?", (session_id,))
        return cur.fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        con.close()


def fork_session(parent_id: str, name: str, cwd: str | None = None) -> str:
    """SessionsProvider：fork codex thread（DB 行复制 + parent 指向）。"""
    return fork_codex_session(parent_id, name, cwd)
