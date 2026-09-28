"""Disposable SQLite FTS5 index for canonical Session history.

Session history JSON/JSONL remains authoritative. The index opens only during a
search request, repairs changed rows from a caller-provided loader, and closes
before returning. Session persistence never imports or calls this module.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .session import is_pan_message_id

DEFAULT_HISTORY_SEARCH_LIMIT = 50
MAX_HISTORY_SEARCH_LIMIT = 100
MAX_HISTORY_SEARCH_QUERY_LENGTH = 512
_SNIPPET_RADIUS = 42
_BUSY_TIMEOUT_MS = 10_000


class HistorySearchError(RuntimeError):
    """The disposable search index could not be read or updated."""


class HistorySearchCursorError(RuntimeError):
    """A signed pagination cursor failed authentication."""


@dataclass(frozen=True)
class _SearchMessage:
    message_id: str
    message_index: int
    role: str
    content: str


@dataclass(frozen=True)
class _HistorySnapshot:
    session_id: str
    history_epoch: str
    history_revision: int
    history_total: int
    messages: tuple[_SearchMessage, ...]


def bounded_limit(value: int | None) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_HISTORY_SEARCH_LIMIT
    except (TypeError, ValueError):
        parsed = DEFAULT_HISTORY_SEARCH_LIMIT
    return max(1, min(parsed, MAX_HISTORY_SEARCH_LIMIT))


def session_version(session) -> tuple[str, int, bool, int | None]:
    """Read the current Session history version and a known total hint."""
    lock = getattr(session, "_summary_lock", None)
    with lock if lock is not None else nullcontext():
        epoch = getattr(session, "history_epoch", "")
        if not isinstance(epoch, str) or not epoch:
            epoch = f"legacy:{getattr(session, 'id', 'unknown')}"
        try:
            revision = max(0, int(getattr(session, "history_revision", 0) or 0))
        except (TypeError, ValueError):
            revision = 0
        history_loaded = bool(getattr(session, "_history_loaded", True))
        if history_loaded:
            history_total = len(getattr(session, "history", []) or [])
        else:
            projection = getattr(session, "summary_projection", {})
            raw_total = projection.get("history_total") if isinstance(projection, dict) else None
            history_total = (
                raw_total
                if isinstance(raw_total, int) and not isinstance(raw_total, bool)
                and raw_total >= 0
                else None
            )
        return epoch, revision, history_loaded, history_total


def _snapshot(session) -> _HistorySnapshot | None:
    """Capture searchable body text and its version under the Session lock."""
    lock = getattr(session, "_summary_lock", None)
    with lock if lock is not None else nullcontext():
        if not getattr(session, "_history_loaded", True):
            return None
        history = getattr(session, "history", []) or []
        epoch = getattr(session, "history_epoch", "")
        if not isinstance(epoch, str) or not epoch:
            epoch = f"legacy:{getattr(session, 'id', 'unknown')}"
        try:
            revision = max(0, int(getattr(session, "history_revision", 0) or 0))
        except (TypeError, ValueError):
            revision = 0

        # source=system_prompt is injected context, not a user body. Defensive
        # duplicate IDs collapse to the last occurrence as in the current finder.
        by_id: dict[str, _SearchMessage] = {}
        for index, row in enumerate(history):
            if not isinstance(row, dict):
                continue
            role = row.get("role")
            content = row.get("content")
            message_id = row.get("messageId")
            if (
                role not in ("user", "assistant")
                or row.get("source") == "system_prompt"
                or not isinstance(content, str)
                or not content.strip()
                or not is_pan_message_id(message_id)
            ):
                continue
            by_id[message_id] = _SearchMessage(
                message_id=message_id,
                message_index=index,
                role=role,
                content=content,
            )

        return _HistorySnapshot(
            session_id=str(getattr(session, "id", "")),
            history_epoch=epoch,
            history_revision=revision,
            history_total=len(history),
            messages=tuple(sorted(by_id.values(), key=lambda item: item.message_index)),
        )


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_MS / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS history_search_sessions (
               session_id TEXT PRIMARY KEY,
               history_epoch TEXT NOT NULL,
               history_revision INTEGER NOT NULL,
               history_total INTEGER NOT NULL
           )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS history_search_messages (
               rowid INTEGER PRIMARY KEY,
               session_id TEXT NOT NULL,
               message_id TEXT NOT NULL,
               message_index INTEGER NOT NULL,
               role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
               content TEXT NOT NULL,
               folded_content TEXT NOT NULL,
               UNIQUE(session_id, message_id)
           )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS history_search_messages_session "
        "ON history_search_messages(session_id, message_index)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS history_search_meta (
               key TEXT PRIMARY KEY,
               value BLOB NOT NULL
           )"""
    )
    # Short and punctuated queries use instr() below. Check FTS5 on demand so
    # importing the app creates no file, connection, thread, or background task.
    connection.execute(
        """CREATE VIRTUAL TABLE IF NOT EXISTS history_search_fts
           USING fts5(folded_content, tokenize='trigram')"""
    )
    connection.commit()


def _cursor_key(connection: sqlite3.Connection) -> bytes:
    """Read or atomically create the disposable index's cursor-signing key."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT value FROM history_search_meta WHERE key='cursor_hmac_key'"
        ).fetchone()
        if row is None:
            key = secrets.token_bytes(32)
            connection.execute(
                "INSERT INTO history_search_meta(key, value) VALUES ('cursor_hmac_key', ?)",
                (key,),
            )
        else:
            try:
                key = bytes(row["value"])
            except (TypeError, ValueError) as exc:
                raise HistorySearchError("history search cursor key is invalid") from exc
            if len(key) != 32:
                raise HistorySearchError("history search cursor key is invalid")
        connection.commit()
        return key
    except BaseException:
        connection.rollback()
        raise


def _delete_session(connection: sqlite3.Connection, session_id: str) -> None:
    rowids = connection.execute(
        "SELECT rowid FROM history_search_messages WHERE session_id=?",
        (session_id,),
    ).fetchall()
    connection.executemany(
        "DELETE FROM history_search_fts WHERE rowid=?",
        ((row["rowid"],) for row in rowids),
    )
    connection.execute(
        "DELETE FROM history_search_messages WHERE session_id=?", (session_id,)
    )
    connection.execute(
        "DELETE FROM history_search_sessions WHERE session_id=?", (session_id,)
    )


def _replace_session(connection: sqlite3.Connection, snapshot: _HistorySnapshot) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = connection.execute(
            """SELECT history_epoch, history_revision FROM history_search_sessions
               WHERE session_id=?""",
            (snapshot.session_id,),
        ).fetchone()
        # A concurrent request may have indexed a newer append while this
        # request loaded history; do not overwrite the newer snapshot.
        if (
            current
            and current["history_epoch"] == snapshot.history_epoch
            and int(current["history_revision"]) > snapshot.history_revision
        ):
            connection.commit()
            return
        _delete_session(connection, snapshot.session_id)
        connection.execute(
            """INSERT INTO history_search_sessions
               (session_id, history_epoch, history_revision, history_total)
               VALUES (?, ?, ?, ?)""",
            (
                snapshot.session_id, snapshot.history_epoch,
                snapshot.history_revision, snapshot.history_total,
            ),
        )
        for message in snapshot.messages:
            cursor = connection.execute(
                """INSERT INTO history_search_messages
                   (session_id, message_id, message_index, role, content, folded_content)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.session_id, message.message_id, message.message_index,
                    message.role, message.content, message.content.lower(),
                ),
            )
            connection.execute(
                "INSERT INTO history_search_fts(rowid, folded_content) VALUES (?, ?)",
                (cursor.lastrowid, message.content.lower()),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _snippet(content: str, needle: str, offset: int) -> str:
    start = max(0, offset - _SNIPPET_RADIUS)
    end = min(len(content), offset + len(needle) + _SNIPPET_RADIUS)
    excerpt = re.sub(r"\s+", " ", content[start:end]).strip()
    return f"{'…' if start else ''}{excerpt}{'…' if end < len(content) else ''}"


def _rows_for_query(
    connection: sqlite3.Connection,
    needle: str,
    limit: int,
    after: tuple[int, int] | None = None,
) -> list[sqlite3.Row]:
    # Bound parameters keep queries literal. Plain needles of 3+ characters
    # use trigram FTS; short and punctuated inputs use exact instr() matching.
    use_fts = len(needle) >= 3 and all(character.isalnum() for character in needle)
    after_clause = ""
    after_params: tuple[int, ...] = ()
    if after is not None:
        after_clause = (
            "AND (scope.position > ? "
            "OR (scope.position = ? AND message.message_index > ?))"
        )
        after_params = (after[0], after[0], after[1])
    if not use_fts:
        return connection.execute(
            """SELECT message.session_id, message.message_id, message.message_index,
                      message.role, message.content, scope.position AS scope_position
               FROM history_search_messages AS message
               JOIN temp.history_search_scope AS scope
                 ON scope.session_id=message.session_id
               WHERE instr(message.folded_content, ?) > 0
               """ + after_clause + """
               ORDER BY scope.position, message.message_index
               LIMIT ?""",
            (needle, *after_params, limit + 1),
        ).fetchall()

    # A single quoted FTS phrase makes OR/NOT/NEAR ordinary text.
    expression = '"' + needle.replace('"', '""') + '"'
    try:
        return connection.execute(
            """SELECT message.session_id, message.message_id, message.message_index,
                      message.role, message.content, scope.position AS scope_position
               FROM history_search_fts
               JOIN history_search_messages AS message
                 ON message.rowid=history_search_fts.rowid
               JOIN temp.history_search_scope AS scope
                 ON scope.session_id=message.session_id
               WHERE history_search_fts MATCH ?
                 AND instr(message.folded_content, ?) > 0
               """ + after_clause + """
               ORDER BY scope.position, message.message_index
               LIMIT ?""",
            (expression, needle, *after_params, limit + 1),
        ).fetchall()
    except sqlite3.Error:
        # Preserve literal semantics if a tokenizer revision rejects an unusual
        # Unicode phrase. Schema/indexing failures still surface as search errors.
        return connection.execute(
            """SELECT message.session_id, message.message_id, message.message_index,
                      message.role, message.content, scope.position AS scope_position
               FROM history_search_messages AS message
               JOIN temp.history_search_scope AS scope
                 ON scope.session_id=message.session_id
               WHERE instr(message.folded_content, ?) > 0
               """ + after_clause + """
               ORDER BY scope.position, message.message_index
               LIMIT ?""",
            (needle, *after_params, limit + 1),
        ).fetchall()


def search_history(
    db_path: str | Path,
    sessions: Sequence[object],
    live_session_ids: Iterable[str],
    query: str,
    *,
    limit: int = DEFAULT_HISTORY_SEARCH_LIMIT,
    after: tuple[int, int] | None = None,
    cursor_auth: tuple[str, bytes] | None = None,
    load_session: Callable[[str], object | None],
) -> dict:
    """Search the requested live Session scope and repair changed index rows.

    sessions controls search scope and ordering. live_session_ids is the full
    current registry and removes cached rows for deleted Sessions.
    """
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    needle_text = query.strip()
    result_limit = bounded_limit(limit)
    if not needle_text:
        return {"hits": [], "versions": [], "limit": result_limit, "hasMore": False}
    if len(needle_text) > MAX_HISTORY_SEARCH_QUERY_LENGTH:
        raise ValueError(
            f"query must be at most {MAX_HISTORY_SEARCH_QUERY_LENGTH} characters"
        )
    if after is not None and (
        not isinstance(after, tuple)
        or len(after) != 2
        or any(type(value) is not int or value < 0 or value > 2_147_483_647
               for value in after)
    ):
        raise ValueError("after must contain bounded non-negative integers")

    needle = needle_text.lower()
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(Path(db_path))
        _ensure_schema(connection)
        cursor_key = _cursor_key(connection)
        if cursor_auth is not None:
            body, signature = cursor_auth
            expected = hmac.new(
                cursor_key, body.encode("ascii"), hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(signature, expected):
                raise HistorySearchCursorError("history search cursor signature is invalid")

        live_ids = {str(session_id) for session_id in live_session_ids if session_id}
        current_rows = {str(session.id): session for session in sessions}

        # Remove stale database rows before reading matches.
        stored_ids = {
            row["session_id"]
            for row in connection.execute(
                "SELECT session_id FROM history_search_sessions"
            ).fetchall()
        }
        stale_ids = stored_ids - live_ids
        if stale_ids:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for session_id in stale_ids:
                    _delete_session(connection, session_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        for session_id, session in current_rows.items():
            if session_id not in live_ids:
                continue
            epoch, revision, history_loaded, history_total = session_version(session)
            indexed = connection.execute(
                """SELECT history_epoch, history_revision, history_total
                   FROM history_search_sessions WHERE session_id=?""",
                (session_id,),
            ).fetchone()
            if (
                indexed
                and indexed["history_epoch"] == epoch
                and int(indexed["history_revision"]) == revision
                and history_total is not None
                and int(indexed["history_total"]) == history_total
            ):
                continue

            full_session = session if history_loaded else load_session(session_id)
            if full_session is None:
                # The registry snapshot still contained this Session. Treat a
                # missing/unreadable canonical history as a search error rather
                # than silently presenting an incomplete result set.
                raise HistorySearchError(
                    f"Session {session_id} history could not be loaded for indexing"
                )
            snapshot = _snapshot(full_session)
            if snapshot is None:
                raise HistorySearchError(
                    f"Session {session_id} history could not be loaded for indexing"
                )
            _replace_session(connection, snapshot)
            del snapshot, full_session

        scope_ids = [
            str(session.id)
            for session in sessions
            if str(session.id) in live_ids
            and connection.execute(
                "SELECT 1 FROM history_search_sessions WHERE session_id=?",
                (str(session.id),),
            ).fetchone()
        ]
        connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS history_search_scope "
            "(session_id TEXT PRIMARY KEY, position INTEGER NOT NULL)"
        )
        connection.execute("DELETE FROM temp.history_search_scope")
        connection.executemany(
            "INSERT INTO temp.history_search_scope(session_id, position) VALUES (?, ?)",
            ((session_id, position) for position, session_id in enumerate(scope_ids)),
        )

        version_rows = connection.execute(
            """SELECT session.session_id, session.history_epoch,
                      session.history_revision, session.history_total
               FROM history_search_sessions AS session
               JOIN temp.history_search_scope AS scope
                 ON scope.session_id=session.session_id
               ORDER BY scope.position"""
        ).fetchall()
        versions = [
            {
                "sessionId": row["session_id"],
                "historyEpoch": row["history_epoch"],
                "historyRevision": int(row["history_revision"]),
                "historyTotal": int(row["history_total"]),
            }
            for row in version_rows
        ]
        version_by_session = {row["sessionId"]: row for row in versions}

        rows = _rows_for_query(connection, needle, result_limit, after)
        hits = []
        for row in rows[:result_limit]:
            content = row["content"]
            offset = content.lower().find(needle)
            if offset < 0:
                continue
            version = version_by_session.get(row["session_id"])
            if version is None:
                continue
            hits.append({
                "sessionId": row["session_id"],
                "messageId": row["message_id"],
                "messageIndex": int(row["message_index"]),
                "role": row["role"],
                "snippet": _snippet(content, needle, offset),
                "historyEpoch": version["historyEpoch"],
                "historyRevision": version["historyRevision"],
                "historyTotal": version["historyTotal"],
            })
        result = {
            "hits": hits,
            "versions": versions,
            "limit": result_limit,
            "hasMore": len(rows) > result_limit,
            "_cursorKey": cursor_key,
        }
        if len(rows) > result_limit and hits:
            last = rows[result_limit - 1]
            result["nextAfter"] = (
                int(last["scope_position"]), int(last["message_index"]),
            )
        return result
    except (HistorySearchError, HistorySearchCursorError):
        raise
    except (sqlite3.Error, OSError) as exc:
        raise HistorySearchError(
            f"SQLite FTS5 history search is unavailable: {exc}"
        ) from exc
    finally:
        if connection is not None:
            connection.close()
