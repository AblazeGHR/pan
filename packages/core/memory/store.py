"""SQLite-based storage layer for Pan's memory system."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Bump when schema.sql changes in a way that needs migrating old DBs.
SCHEMA_VERSION = 2

# Ordered migrations: version N applies to DBs at version N-1 (or unknown).
# Each migration is a list of SQL statements run in a single transaction —
# a failure rolls back, leaving the DB at its old version for retry (#25).
_MIGRATIONS: dict[int, list[str]] = {
    2: [
        # v2: fts.id must be UNINDEXED so 16-hex chunk ids are not tokenized.
        "CREATE VIRTUAL TABLE fts_v2 USING fts5("
        "text, id UNINDEXED, path UNINDEXED, source UNINDEXED, "
        "model UNINDEXED, start_line UNINDEXED, end_line UNINDEXED, "
        "tokenize='unicode61')",
        "INSERT INTO fts_v2(rowid, text, id, path, source, model, start_line, end_line) "
        "SELECT rowid, text, id, path, source, model, start_line, end_line FROM fts",
        "DROP TABLE fts",
        "ALTER TABLE fts_v2 RENAME TO fts",
    ],
}


class MemoryStore:
    """Manages SQLite storage for file tracking, chunks, FTS, and embedding cache."""

    def __init__(self, db_path: str) -> None:
        self.db_path: str = str(Path(db_path))
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()  # serializes access across threads (watcher + API threadpool)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    # ------------------------------------------------------------------ #
    #  Schema initialization
    # ------------------------------------------------------------------ #

    def _ensure_schema(self) -> None:
        """Create schema for fresh DBs; migrate existing ones to the latest version."""
        assert self._conn is not None
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
            )
            if cursor.fetchone() is None:
                schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
                self._conn.executescript(schema_sql)
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                return

            # Existing DB — apply any pending migrations (#25).
            self._apply_migrations()

    def _apply_migrations(self) -> None:
        """Run pending migrations in order, then bump schema_version.

        Caller must hold ``self._lock`` and an open transaction.
        """
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        current = int(row["value"]) if row and row["value"].isdigit() else 1

        for version in range(current + 1, SCHEMA_VERSION + 1):
            for stmt in _MIGRATIONS.get(version, []):
                self._conn.execute(stmt)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(version),),
            )
            log.info("Memory schema migrated to v%d", version)

    # ------------------------------------------------------------------ #
    #  Meta (key-value)
    # ------------------------------------------------------------------ #

    def set_meta(self, key: str, value: str) -> None:
        """Store a key-value metadata entry (upsert)."""
        assert self._conn is not None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        """Read a metadata entry, or None if missing."""
        assert self._conn is not None
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return row["value"]

    # ------------------------------------------------------------------ #
    #  File operations
    # ------------------------------------------------------------------ #

    def insert_file(
        self, path: str, source: str, hash: str, mtime: float, size: int
    ) -> None:
        assert self._conn is not None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO files (path, source, hash, mtime, size) "
                "VALUES (?, ?, ?, ?, ?)",
                (path, source, hash, mtime, size),
            )

    def update_file(
        self, path: str, hash: str, mtime: float, size: int
    ) -> None:
        assert self._conn is not None
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE files SET hash = ?, mtime = ?, size = ? WHERE path = ?",
                (hash, mtime, size, path),
            )

    def get_file(self, path: str) -> dict[str, Any] | None:
        assert self._conn is not None
        with self._lock:
            row = self._conn.execute(
                "SELECT path, source, hash, mtime, size FROM files WHERE path = ?",
                (path,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def file_exists(self, path: str) -> bool:
        assert self._conn is not None
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM files WHERE path = ?", (path,)
            ).fetchone()
        return row is not None

    def delete_file(self, path: str) -> None:
        assert self._conn is not None
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM files WHERE path = ?", (path,))

    def get_files(self, source: str | None = None) -> list[dict[str, Any]]:
        """List tracked files, optionally filtered by source."""
        assert self._conn is not None
        with self._lock:
            if source:
                rows = self._conn.execute(
                    "SELECT path, source, hash, mtime, size FROM files "
                    "WHERE source = ?",
                    (source,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT path, source, hash, mtime, size FROM files"
                ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ #
    #  Chunk operations
    # ------------------------------------------------------------------ #

    def insert_chunk(self, chunk: dict[str, Any]) -> None:
        """Insert or replace a chunk.

        The *chunk* dict must contain keys: id, path, source, start_line,
        end_line, hash, model, text, embedding (already JSON-serialized).

        FTS5 has no UNIQUE constraint on ``id``, so ``INSERT OR REPLACE``
        behaves like plain ``INSERT`` and duplicates rows on re-index. Delete
        the existing row first to guarantee one row per chunk id (#24).
        """
        assert self._conn is not None
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM fts WHERE id = ?", (chunk["id"],))
            self._conn.execute(
                "INSERT OR REPLACE INTO chunks "
                "(id, path, source, start_line, end_line, hash, model, text, embedding, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk["id"],
                    chunk["path"],
                    chunk["source"],
                    chunk["start_line"],
                    chunk["end_line"],
                    chunk["hash"],
                    chunk["model"],
                    chunk["text"],
                    chunk["embedding"],
                    time.time(),
                ),
            )
            # Mirror into FTS index
            self._conn.execute(
                "INSERT OR REPLACE INTO fts (text, id, path, source, model, start_line, end_line) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk["text"],
                    chunk["id"],
                    chunk["path"],
                    chunk["source"],
                    chunk["model"],
                    chunk["start_line"],
                    chunk["end_line"],
                ),
            )

    def delete_chunks_for_file(self, path: str) -> None:
        assert self._conn is not None
        with self._lock, self._conn:
            # Gather ids so we can clean FTS too
            ids = [
                row["id"]
                for row in self._conn.execute(
                    "SELECT id FROM chunks WHERE path = ?", (path,)
                ).fetchall()
            ]
            self._conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
            for chunk_id in ids:
                self._conn.execute(
                    "DELETE FROM fts WHERE id = ?", (chunk_id,)
                )

    def replace_file_chunks(
        self,
        file_row: dict[str, Any],
        chunks: list[dict[str, Any]],
    ) -> None:
        """Atomically replace a file's chunks: delete old, write new file row
        and all new chunks + FTS rows in a single transaction.

        The caller must have computed embeddings *before* calling this — an
        embedding failure leaves the DB untouched (file remains indexable).
        """
        assert self._conn is not None
        with self._lock, self._conn:
            # Gather old chunk ids to clean FTS
            old_ids = [
                row["id"]
                for row in self._conn.execute(
                    "SELECT id FROM chunks WHERE path = ?", (file_row["path"],)
                ).fetchall()
            ]
            self._conn.execute(
                "DELETE FROM chunks WHERE path = ?", (file_row["path"],)
            )
            for chunk_id in old_ids:
                self._conn.execute(
                    "DELETE FROM fts WHERE id = ?", (chunk_id,)
                )

            self._conn.execute(
                "INSERT OR REPLACE INTO files (path, source, hash, mtime, size) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    file_row["path"],
                    file_row["source"],
                    file_row["hash"],
                    file_row["mtime"],
                    file_row["size"],
                ),
            )

            for chunk in chunks:
                self._conn.execute(
                    "INSERT OR REPLACE INTO chunks "
                    "(id, path, source, start_line, end_line, hash, model, text, embedding, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk["id"],
                        chunk["path"],
                        chunk["source"],
                        chunk["start_line"],
                        chunk["end_line"],
                        chunk["hash"],
                        chunk["model"],
                        chunk["text"],
                        chunk["embedding"],
                        time.time(),
                    ),
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO fts (text, id, path, source, model, start_line, end_line) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk["text"],
                        chunk["id"],
                        chunk["path"],
                        chunk["source"],
                        chunk["model"],
                        chunk["start_line"],
                        chunk["end_line"],
                    ),
                )

    def get_chunks_for_search(self) -> list[dict[str, Any]]:
        assert self._conn is not None
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, path, source, start_line, end_line, hash, model, text, embedding, updated_at "
                "FROM chunks"
            ).fetchall()
        return [dict(row) for row in rows]

    def probe_embedding_dims(self) -> int | None:
        """Return the dims of the first stored chunk embedding, or None if empty.

        Used to fail fast when opening a pre-provider-tracking DB whose
        existing chunks were embedded with a different vector size (#1/#14).
        """
        assert self._conn is not None
        with self._lock:
            row = self._conn.execute(
                "SELECT embedding FROM chunks LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        try:
            return len(json.loads(row["embedding"]))
        except (json.JSONDecodeError, TypeError):
            return None

    # ------------------------------------------------------------------ #
    #  Full-text search
    # ------------------------------------------------------------------ #

    def search_fts(self, query: str, max_results: int = 50) -> list[dict[str, Any]]:
        """Full-text search via FTS5 ordered by BM25 rank."""
        assert self._conn is not None
        with self._lock:
            rows = self._conn.execute(
                "SELECT text, id, path, source, model, start_line, end_line, rank "
                "FROM fts WHERE fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (query, max_results),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ #
    #  Embedding cache
    # ------------------------------------------------------------------ #

    def insert_embedding_cache(
        self,
        provider: str,
        model: str,
        provider_key: str,
        hash: str,
        embedding: list[float],
        dims: int,
    ) -> None:
        assert self._conn is not None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO embedding_cache "
                "(provider, model, provider_key, hash, embedding, dims, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    provider,
                    model,
                    provider_key,
                    hash,
                    json.dumps(embedding),
                    dims,
                    time.time(),
                ),
            )

    def get_embedding_cache(
        self, provider: str, model: str, provider_key: str, hash: str
    ) -> list[float] | None:
        assert self._conn is not None
        with self._lock:
            row = self._conn.execute(
                "SELECT embedding FROM embedding_cache "
                "WHERE provider = ? AND model = ? AND provider_key = ? AND hash = ?",
                (provider, model, provider_key, hash),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["embedding"])

    # ------------------------------------------------------------------ #
    #  Stats
    # ------------------------------------------------------------------ #

    def get_stats(self) -> dict[str, Any]:
        """Return row counts for files and chunks."""
        assert self._conn is not None
        with self._lock:
            file_count = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM files"
            ).fetchone()["cnt"]
            chunk_count = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM chunks"
            ).fetchone()["cnt"]
        return {"files": file_count, "chunks": chunk_count}

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
