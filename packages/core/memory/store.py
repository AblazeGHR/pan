"""SQLite-based storage layer for Pan's memory system."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class MemoryStore:
    """Manages SQLite storage for file tracking, chunks, FTS, and embedding cache."""

    def __init__(self, db_path: str) -> None:
        self.db_path: str = str(Path(db_path))
        self._conn: sqlite3.Connection | None = None
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    # ------------------------------------------------------------------ #
    #  Schema initialization
    # ------------------------------------------------------------------ #

    def _ensure_schema(self) -> None:
        """Execute schema.sql if tables do not already exist."""
        assert self._conn is not None
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        )
        if cursor.fetchone() is not None:
            return

        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._conn:
            self._conn.executescript(schema_sql)

    # ------------------------------------------------------------------ #
    #  File operations
    # ------------------------------------------------------------------ #

    def insert_file(
        self, path: str, source: str, hash: str, mtime: float, size: int
    ) -> None:
        assert self._conn is not None
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO files (path, source, hash, mtime, size) "
                "VALUES (?, ?, ?, ?, ?)",
                (path, source, hash, mtime, size),
            )

    def update_file(
        self, path: str, hash: str, mtime: float, size: int
    ) -> None:
        assert self._conn is not None
        with self._conn:
            self._conn.execute(
                "UPDATE files SET hash = ?, mtime = ?, size = ? WHERE path = ?",
                (hash, mtime, size, path),
            )

    def get_file(self, path: str) -> dict[str, Any] | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT path, source, hash, mtime, size FROM files WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def file_exists(self, path: str) -> bool:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT 1 FROM files WHERE path = ?", (path,)
        ).fetchone()
        return row is not None

    def delete_file(self, path: str) -> None:
        assert self._conn is not None
        with self._conn:
            self._conn.execute("DELETE FROM files WHERE path = ?", (path,))

    # ------------------------------------------------------------------ #
    #  Chunk operations
    # ------------------------------------------------------------------ #

    def insert_chunk(self, chunk: dict[str, Any]) -> None:
        """Insert or replace a chunk.

        The *chunk* dict must contain keys: id, path, source, start_line,
        end_line, hash, model, text, embedding (already JSON-serialized).
        """
        assert self._conn is not None
        with self._conn:
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
        with self._conn:
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

    def get_chunks_for_search(self) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT id, path, source, start_line, end_line, hash, model, text, embedding, updated_at "
            "FROM chunks"
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ #
    #  Full-text search
    # ------------------------------------------------------------------ #

    def search_fts(self, query: str, max_results: int = 50) -> list[dict[str, Any]]:
        """Full-text search via FTS5 ordered by BM25 rank."""
        assert self._conn is not None
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
        with self._conn:
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
