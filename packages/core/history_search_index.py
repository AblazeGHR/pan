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
import json
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .session import is_pan_message_id
from .history_search_query import cached_query_page, clear_query_cache, install_query_schema

DEFAULT_HISTORY_SEARCH_LIMIT = 50
MAX_HISTORY_SEARCH_LIMIT = 100
MAX_HISTORY_SEARCH_QUERY_LENGTH = 512
_SNIPPET_RADIUS = 42
_BUSY_TIMEOUT_MS = 10_000
SEARCH_ROLES = ('user', 'assistant', 'tool', 'thinking')
BODY_ROLES = ('user', 'assistant')
_SCHEMA_VERSION = 3
_FTS_TABLES = {'user': 'history_search_fts', 'assistant': 'history_search_fts',
               'tool': 'history_search_tool_fts', 'thinking': 'history_search_thinking_fts'}


def normalize_roles(roles: Iterable[str] | None) -> tuple[str, ...]:
    if roles is None:
        return BODY_ROLES
    selected = set(roles)
    if selected - set(SEARCH_ROLES):
        raise ValueError('roles must contain only user, assistant, tool, thinking')
    return tuple(role for role in SEARCH_ROLES if role in selected)


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
    digest: bytes | None
    prefix_digest: bytes | None
    roles: tuple[str, ...] = BODY_ROLES


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


def _snapshot(session, previous_total: int | None = None,
              roles: tuple[str, ...] = BODY_ROLES, *,
              only_suffix: bool = False) -> _HistorySnapshot | None:
    """Capture text blocks and their version under the Session lock.

    Capture eligible roles (only the suffix for a possible append), but index
    only requested partitions. This lets
    a transaction preserve concurrently enabled partitions without reopening
    canonical history while holding a SQLite writer lock.
    """
    lock = getattr(session, "_summary_lock", None)
    with lock if lock is not None else nullcontext():
        if not getattr(session, "_history_loaded", True):
            return None
        history = getattr(session, "history", []) or []
        # Hash every canonical row, including excluded roles and legacy IDs.
        # An unchanged revision or a growing total alone cannot prove append.
        prefix_digest = None
        try:
            def encode_rows(rows):
                # Encode a canonical sequence once, rather than creating a
                # JSON encoder and crossing Python/C once per historical row.
                encoded = json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode('utf-8')
                # Outer brackets are omitted so an encoded suffix can extend
                # the hash without encoding the prefix a second time. Commas
                # preserve canonical sequence boundaries, including all roles.
                return encoded[1:-1]
            if previous_total is not None and 0 <= previous_total <= len(history):
                digest = hashlib.sha256(encode_rows(history[:previous_total]))
                prefix_digest = digest.digest()
                if previous_total < len(history):
                    if previous_total:
                        digest.update(b',')
                    digest.update(encode_rows(history[previous_total:]))
                full_digest = digest.digest()
            else:
                full_digest = hashlib.sha256(encode_rows(history)).digest()
        except (TypeError, ValueError, OverflowError):
            # Unusual in-memory rows still get a complete rebuild; they never
            # qualify for the prefix proof.
            full_digest = prefix_digest = None
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
        start = previous_total if only_suffix and previous_total is not None else 0
        for index in range(start, len(history)):
            row = history[index]
            if not isinstance(row, dict):
                continue
            role = row.get("role")
            content = row.get("content")
            message_id = row.get("messageId")
            if (
                role not in SEARCH_ROLES
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
            digest=full_digest,
            prefix_digest=prefix_digest,
            roles=roles,
        )


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_MS / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    if connection.execute('PRAGMA user_version').fetchone()[0] == _SCHEMA_VERSION:
        return
    # Only the disposable index is migrated, never canonical history. Recheck
    # after acquiring the lock so concurrent first requests cannot drop v3.
    connection.execute('BEGIN IMMEDIATE')
    if connection.execute('PRAGMA user_version').fetchone()[0] == _SCHEMA_VERSION:
        connection.commit()
        return
    for table in (*(table+'_vocab' for table in dict.fromkeys(_FTS_TABLES.values())),
                  *dict.fromkeys(_FTS_TABLES.values()), 'history_search_query_hits', 'history_search_query_chunks',
                  'history_search_queries', 'history_search_messages',
                  'history_search_sessions', 'history_search_meta'):
        connection.execute(f'DROP TABLE IF EXISTS {table}')
    for name in ('body', 'tool', 'thinking'):
        connection.execute(f'DROP VIEW IF EXISTS history_search_{name}_content')
    connection.execute(
        """CREATE TABLE IF NOT EXISTS history_search_sessions (
               session_id TEXT PRIMARY KEY,
               history_epoch TEXT NOT NULL,
               history_revision INTEGER NOT NULL,
               history_total INTEGER NOT NULL,
               indexed_roles TEXT NOT NULL
           )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS history_search_messages (
               rowid INTEGER PRIMARY KEY,
               session_id TEXT NOT NULL,
               message_id TEXT NOT NULL,
               message_index INTEGER NOT NULL,
               role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool', 'thinking')),
               content TEXT NOT NULL,
               folded_content TEXT NOT NULL,
               UNIQUE(session_id, message_id)
           )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS history_search_messages_session "
        "ON history_search_messages(session_id, role, message_index)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS history_search_meta (
               key TEXT PRIMARY KEY,
               value BLOB NOT NULL
           )"""
    )
    # Short queries use instr() below. Check FTS5 on demand so
    # importing the app creates no file, connection, thread, or background task.
    partitions = (('body', 'history_search_fts', "'user','assistant'"),
                  ('tool', 'history_search_tool_fts', "'tool'"),
                  ('thinking', 'history_search_thinking_fts', "'thinking'"))
    for name, table, roles_sql in partitions:
        # The external content view has exactly this partition's documents.
        # SQLite integrity-check/rebuild must not silently add excluded roles.
        view = f'history_search_{name}_content'
        connection.execute(f'CREATE VIEW {view} AS SELECT rowid, folded_content '
                           f'FROM history_search_messages WHERE role IN ({roles_sql})')
        connection.execute(f"CREATE VIRTUAL TABLE {table} USING fts5("
                           "folded_content, tokenize='trigram', detail=none, columnsize=0, "
                           f"content='{view}', content_rowid='rowid')")
        connection.execute(f"CREATE VIRTUAL TABLE {table}_vocab USING fts5vocab({table}, 'row')")
    install_query_schema(connection)
    connection.execute(f'PRAGMA user_version={_SCHEMA_VERSION}')
    connection.commit()


def _cursor_key(connection: sqlite3.Connection) -> bytes:
    """Read or atomically create the disposable index's cursor-signing key."""
    existing = connection.execute("SELECT value FROM history_search_meta WHERE key='cursor_hmac_key'").fetchone()
    if existing is not None:
        key = bytes(existing['value'])
        if len(key) != 32:
            raise HistorySearchError('history search cursor key is invalid')
        return key
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
    clear_query_cache(connection)
    rowids = connection.execute(
        "SELECT rowid, role, folded_content FROM history_search_messages WHERE session_id=?",
        (session_id,),
    ).fetchall()
    for row in rowids:
        table = _FTS_TABLES[row['role']]
        connection.execute(f"INSERT INTO {table}({table}, rowid, folded_content) VALUES ('delete', ?, ?)",
                           (row['rowid'], row['folded_content']))
    connection.execute(
        "DELETE FROM history_search_messages WHERE session_id=?", (session_id,)
    )
    connection.execute(
        "DELETE FROM history_search_sessions WHERE session_id=?", (session_id,)
    )
    connection.execute(
        "DELETE FROM history_search_meta WHERE key=?", (f"digest:{session_id}",)
    )


def _replace_session(connection: sqlite3.Connection, snapshot: _HistorySnapshot) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = connection.execute(
            """SELECT history_epoch, history_revision, history_total, indexed_roles
               FROM history_search_sessions WHERE session_id=?""",
            (snapshot.session_id,),
        ).fetchone()
        if (
            current is not None
            and snapshot.digest is not None
            and current["history_epoch"] == snapshot.history_epoch
            and int(current["history_revision"]) == snapshot.history_revision
            and int(current["history_total"]) == snapshot.history_total
            and set(current['indexed_roles'].split(',')) >= set(snapshot.roles)
        ):
            saved_digest = connection.execute(
                "SELECT value FROM history_search_meta WHERE key=?",
                (f"digest:{snapshot.session_id}",),
            ).fetchone()
            if saved_digest is not None and saved_digest["value"] == snapshot.digest:
                connection.commit()
                return
        # A concurrent request may have indexed a newer append while this
        # request loaded history; do not overwrite the newer snapshot.
        if (
            current
            and current["history_epoch"] == snapshot.history_epoch
            and (
                int(current["history_revision"]) > snapshot.history_revision
                or (
                    int(current["history_revision"]) == snapshot.history_revision
                    and int(current["history_total"]) > snapshot.history_total
                )
            )
        ):
            connection.commit()
            return
        # A concurrent query may have enabled another partition since this
        # snapshot was captured. Preserve its coverage while replacing rows.
        effective_roles = normalize_roles((*snapshot.roles,
            *(current['indexed_roles'].split(',') if current else ())))
        _delete_session(connection, snapshot.session_id)
        connection.execute(
            """INSERT INTO history_search_sessions
               (session_id, history_epoch, history_revision, history_total, indexed_roles)
               VALUES (?, ?, ?, ?, ?)""",
            (
                snapshot.session_id, snapshot.history_epoch,
                snapshot.history_revision, snapshot.history_total,
                ','.join(effective_roles),
            ),
        )
        _insert_messages(connection, snapshot.session_id,
                         (message for message in snapshot.messages if message.role in effective_roles))
        _index_inserted_rows(connection, snapshot.session_id, 0)
        if snapshot.digest is not None:
            connection.execute(
                "INSERT INTO history_search_meta(key, value) VALUES (?, ?)",
                (f"digest:{snapshot.session_id}", snapshot.digest),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _insert_messages(connection, session_id, messages):
    """Batch canonical rows, then feed each selected FTS partition in SQL."""
    connection.executemany(
        'INSERT INTO history_search_messages '
        '(session_id,message_id,message_index,role,content,folded_content) VALUES (?,?,?,?,?,?)',
        ((session_id, message.message_id, message.message_index, message.role,
          message.content, message.content.casefold()) for message in messages))
    # Only rows added in this transaction qualify. SQLite assigns increasing
    # rowids for this batch; a caller records the pre-batch maximum below.


def _index_inserted_rows(connection, session_id, after_rowid):
    for table in dict.fromkeys(_FTS_TABLES.values()):
        roles = tuple(role for role, target in _FTS_TABLES.items() if target == table)
        placeholders = ','.join('?' for _ in roles)
        connection.execute(f'INSERT INTO {table}(rowid,folded_content) '
                           'SELECT rowid,folded_content FROM history_search_messages '
                           f'WHERE session_id=? AND rowid>? AND role IN ({placeholders})',
                           (session_id, after_rowid, *roles))


def _append_session(
    connection: sqlite3.Connection, snapshot: _HistorySnapshot,
    previous_epoch: str, previous_revision: int, previous_total: int,
) -> bool:
    """Append a verified suffix, or return False for a complete rebuild."""
    if snapshot.prefix_digest is None or snapshot.digest is None:
        return False
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = connection.execute(
            """SELECT history_epoch, history_revision, history_total, indexed_roles
               FROM history_search_sessions WHERE session_id=?""",
            (snapshot.session_id,),
        ).fetchone()
        saved_digest = connection.execute(
            "SELECT value FROM history_search_meta WHERE key=?",
            (f"digest:{snapshot.session_id}",),
        ).fetchone()
        if (
            current is None
            or current["history_epoch"] != previous_epoch
            or int(current["history_revision"]) != previous_revision
            or int(current["history_total"]) != previous_total
            or snapshot.history_revision < previous_revision
            or saved_digest is None
            or saved_digest["value"] != snapshot.prefix_digest
        ):
            connection.rollback()
            return False
        all_suffix = [message for message in snapshot.messages
                      if message.message_index >= previous_total]
        suffix = [message for message in all_suffix
                  if message.role in current['indexed_roles'].split(',')]
        # A duplicate Pan ID in the suffix changes which occurrence wins.
        # Rebuild so the old row is removed and the last occurrence survives.
        if any(connection.execute(
            """SELECT 1 FROM history_search_messages
               WHERE session_id=? AND message_id=?""",
            (snapshot.session_id, message.message_id),
        ).fetchone() for message in all_suffix):
            connection.rollback()
            return False
        clear_query_cache(connection)
        last_rowid = connection.execute('SELECT COALESCE(MAX(rowid),0) FROM history_search_messages').fetchone()[0]
        _insert_messages(connection, snapshot.session_id, suffix)
        _index_inserted_rows(connection, snapshot.session_id, last_rowid)
        connection.execute(
            """UPDATE history_search_sessions
               SET history_revision=?, history_total=? WHERE session_id=?""",
            (snapshot.history_revision, snapshot.history_total, snapshot.session_id),
        )
        connection.execute(
            "UPDATE history_search_meta SET value=? WHERE key=?",
            (snapshot.digest, f"digest:{snapshot.session_id}"),
        )
        connection.commit()
        return True
    except BaseException:
        connection.rollback()
        raise


def _snippet(content: str, needle: str, offset: int) -> str:
    start = max(0, offset - _SNIPPET_RADIUS)
    end = min(len(content), offset + len(needle) + _SNIPPET_RADIUS)
    excerpt = re.sub(r"\s+", " ", content[start:end]).strip()
    return f"{'…' if start else ''}{excerpt}{'…' if end < len(content) else ''}"
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
    roles: Iterable[str] | None = None,
    content_counts: bool = False,
    match_index: int | None = None,
) -> dict:
    """Search the requested live Session scope and repair changed index rows.

    sessions controls search scope and ordering. live_session_ids is the full
    current registry and removes cached rows for deleted Sessions.
    """
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    needle_text = query.strip()
    selected_roles = normalize_roles(roles)
    result_limit = bounded_limit(limit)
    if not needle_text or not selected_roles:
        return {"hits": [], "versions": [], "limit": result_limit, "hasMore": False,
                **({'totalMatches': 0, 'totalMessages': 0, 'roles': list(selected_roles)} if content_counts else {})}
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

    needle = needle_text.casefold()
    if match_index is not None and (type(match_index) is not int or not 0 <= match_index <= 2_147_483_647):
        raise ValueError('match_index must be a bounded non-negative integer')
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
                """SELECT history_epoch, history_revision, history_total, indexed_roles
                   FROM history_search_sessions WHERE session_id=?""",
                (session_id,),
            ).fetchone()
            if (
                indexed
                and indexed["history_epoch"] == epoch
                and int(indexed["history_revision"]) == revision
                and history_total is not None
                and int(indexed["history_total"]) == history_total
                and set(indexed['indexed_roles'].split(',')) >= set(selected_roles)
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
            previous_total = int(indexed["history_total"]) if indexed else None
            indexed_roles = tuple(indexed['indexed_roles'].split(',')) if indexed else ()
            wanted_roles = normalize_roles((*indexed_roles, *selected_roles))
            only_suffix = bool(indexed and set(indexed_roles) >= set(selected_roles)
                               and indexed['history_epoch'] == epoch
                               and history_total is not None and history_total > previous_total)
            snapshot = _snapshot(full_session, previous_total, wanted_roles, only_suffix=only_suffix)
            if snapshot is None:
                raise HistorySearchError(
                    f"Session {session_id} history could not be loaded for indexing"
                )
            appended = (
                indexed is not None
                and set(indexed_roles) >= set(selected_roles)
                and indexed["history_epoch"] == snapshot.history_epoch
                and snapshot.history_total > previous_total
                and _append_session(
                    connection, snapshot, indexed["history_epoch"],
                    int(indexed["history_revision"]), previous_total,
                )
            )
            if not appended:
                if only_suffix:
                    # A failed proof, ID conflict or concurrent replacement
                    # needs a complete snapshot; never rebuild from just suffix.
                    snapshot = _snapshot(full_session, roles=wanted_roles)
                    if snapshot is None:
                        raise HistorySearchError(f'Session {session_id} history could not be loaded for rebuilding')
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
        # Finish temporary-scope writes before acquiring a cache-build lock.
        connection.commit()
        rows, counts = cached_query_page(connection, needle, selected_roles,
                                         _FTS_TABLES, versions, result_limit, after, match_index)
        hits = []
        for row in rows[:result_limit]:
            content = row["content"]
            folded = content.casefold()
            offset = folded.find(needle)
            if offset < 0:
                continue
            if len(folded) != len(content):
                # Case folding may expand characters (ß -> ss). Snippets and
                # firstMatch still refer to the original displayed content.
                folded_offset = offset
                consumed = 0
                for original_offset, character in enumerate(content):
                    consumed += len(character.casefold())
                    if consumed > folded_offset:
                        offset = original_offset
                        break
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
                **({'matchCount': int(row['match_count']), 'matchStart': int(row['match_start']),
                    'firstMatch': offset} if content_counts else {}),
            })
        result = {
            "hits": hits,
            "versions": versions,
            "limit": result_limit,
            "hasMore": len(rows) > result_limit,
            "_cursorKey": cursor_key,
            **({'totalMatches': counts['totalMatches'], 'totalMessages': counts['totalMessages'],
                'roles': list(selected_roles)} if content_counts else {}),
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
