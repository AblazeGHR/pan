"""Pan Memory — hybrid memory retrieval with vector + full-text search.

Usage::

    from packages.core.memory import MemoryManager

    mgr = MemoryManager("data/memory/my_char.sqlite", api_key="sk-...")
    mgr.index_directory("characters/my_char/memory/")
    results = mgr.search("如何创建角色")
    for r in results:
        print(f"[{r.score:.2f}] {r.text[:80]}...")
    mgr.close()
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .chunker import chunk_markdown, ChunkInfo
from .embedder import OPENAI_DEFAULT_MODEL
from .embedder import PROVIDER_OPENAI, PROVIDER_OLLAMA, PROVIDER_SENTENCE_TRANSFORMERS, PROVIDER_LOCAL
from .embedder import Embedder

EMBEDDING_MODEL = OPENAI_DEFAULT_MODEL  # Backward compat
from .search import DEFAULT_MAX_RESULTS, DEFAULT_MIN_SCORE
from .search import VECTOR_WEIGHT, TEXT_WEIGHT
from .search import HybridSearcher, SearchResult
from .store import MemoryStore
from .watcher import MemoryWatcher

log = logging.getLogger(__name__)

__all__ = [
    "MemoryManager",
    "SearchResult",
    "IndexStats",
    "SyncReport",
]


# ------------------------------------------------------------------ #
#  Stats / report types
# ------------------------------------------------------------------ #

@dataclass
class IndexStats:
    files: int
    chunks: int


@dataclass
class FileReport:
    path: str
    status: str  # "new" | "updated" | "unchanged" | "error"
    chunks: int = 0


@dataclass
class SyncReport:
    files_scanned: int
    files_modified: int
    chunks_upserted: int
    details: list[FileReport]


# ------------------------------------------------------------------ #
#  MemoryManager
# ------------------------------------------------------------------ #

class MemoryManager:
    """Orchestrates memory indexing, retrieval, and file monitoring.

    Ties together MemoryStore, chunker, Embedder, HybridSearcher, and
    MemoryWatcher into a single high-level API.
    """

    def __init__(
        self,
        db_path: str,
        api_key: str | None = None,
        model: str | None = None,
        provider: str = PROVIDER_OPENAI,
        model_path: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._db_path = str(Path(db_path))
        self._store = MemoryStore(self._db_path)
        self._embedder = Embedder(
            store=self._store,
            provider=provider,
            model=model,
            model_path=model_path,
            api_key=api_key,
            base_url=base_url,
        )
        self._searcher = HybridSearcher(self._store, self._embedder)

        # Record provider + dims in meta table on first open; validate on
        # subsequent opens. Mixing providers in one DB silently corrupts
        # cosine scores (dims mismatch → zip truncation). See #1/#14.
        stored_provider = self._store.get_meta("embedding_provider")
        stored_dims = self._store.get_meta("embedding_dims")
        current_provider = provider or PROVIDER_OPENAI
        current_dims = str(self._embedder.dims)
        if stored_provider is None:
            self._store.set_meta("embedding_provider", current_provider)
            self._store.set_meta("embedding_dims", current_dims)
        elif stored_provider != current_provider or stored_dims != current_dims:
            self._store.close()
            raise ValueError(
                f"Memory DB '{self._db_path}' was indexed with provider "
                f"'{stored_provider}' ({stored_dims} dims), but is being opened "
                f"with '{current_provider}' ({current_dims} dims). Re-index the "
                "character memory with a single provider."
            )

        self._watcher: MemoryWatcher | None = None
        self._watch_dir: str | None = None

    # ------------------------------------------------------------------ #
    #  Indexing
    # ------------------------------------------------------------------ #

    def index_directory(
        self, dir_path: str, source: str = "memory"
    ) -> SyncReport:
        """Index all .md files under *dir_path* (non-recursive).

        Returns a SyncReport summarizing what was indexed.
        """
        dir_path = str(Path(dir_path))
        report_files: list[FileReport] = []
        total_upserted = 0

        p = Path(dir_path)
        if not p.exists() or not p.is_dir():
            log.warning("Index directory not found: %s", dir_path)
            return SyncReport(
                files_scanned=0, files_modified=0, chunks_upserted=0,
                details=[],
            )

        md_files = sorted(p.glob("*.md"))
        for md_path in md_files:
            try:
                file_report = self._index_single_file(
                    str(md_path), source=source
                )
                report_files.append(file_report)
                total_upserted += file_report.chunks
            except Exception:
                log.exception("Failed to index %s", md_path)
                report_files.append(
                    FileReport(
                        path=str(md_path), status="error", chunks=0
                    )
                )

        modified = sum(
            1 for r in report_files if r.status in ("new", "updated")
        )
        return SyncReport(
            files_scanned=len(md_files),
            files_modified=modified,
            chunks_upserted=total_upserted,
            details=report_files,
        )

    def index_file(
        self, file_path: str, source: str = "memory"
    ) -> FileReport:
        """Index a single Markdown file.

        Checks mtime + hash to decide whether re-indexing is needed.
        """
        return self._index_single_file(file_path, source=source)

    def index_text(
        self, text: str, source: str = "memory", path: str = "memory://inline"
    ) -> FileReport:
        """Index in-memory Markdown text under a stable virtual *path*.

        Unlike index_file, no filesystem read is performed — the text is
        chunked/embedded directly. Re-indexing the same *path* replaces the
        existing file's chunks atomically. Used by SessionIndexer (#18/#19).
        """
        file_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        existing = self._store.get_file(path)
        if existing and existing["hash"] == file_hash:
            return FileReport(path=path, status="unchanged", chunks=0)

        chunk_infos = chunk_markdown(text, source_path=path)

        # Embed FIRST — failure leaves the DB untouched.
        chunk_rows: list[dict] = []
        if chunk_infos:
            embeddings = self._embedder.embed_batch([c.text for c in chunk_infos])
            for info, emb in zip(chunk_infos, embeddings):
                chunk_rows.append({
                    "id": info.id,
                    "path": info.source_path,
                    "source": source,
                    "start_line": info.start_line,
                    "end_line": info.end_line,
                    "hash": info.hash,
                    "model": EMBEDDING_MODEL,
                    "text": info.text,
                    "embedding": json.dumps(emb),
                })

        self._store.replace_file_chunks(
            file_row={
                "path": path,
                "source": source,
                "hash": file_hash,
                "mtime": 0.0,
                "size": len(text.encode("utf-8")),
            },
            chunks=chunk_rows,
        )

        status = "updated" if existing else "new"
        return FileReport(path=path, status=status, chunks=len(chunk_infos))

    def reindex(self, dir_path: str, source: str = "memory") -> SyncReport:
        """Force re-index all .md files, regardless of mtime.

        Shortcut for clearing old data and re-indexing from scratch.
        Does NOT clear the index — just overwrites each file's chunks.
        """
        return self.index_directory(dir_path, source=source)

    def _index_single_file(
        self, file_path: str, source: str = "memory"
    ) -> FileReport:
        """Index one .md file: chunk → embed → store.

        Embedding happens BEFORE any DB write: if embedding fails, the DB is
        untouched so the file remains indexable on the next attempt.
        """
        p = Path(file_path)
        if not p.exists():
            return FileReport(path=file_path, status="error", chunks=0)

        mtime = p.stat().st_mtime
        raw = p.read_text(encoding="utf-8")
        file_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        # Check if file is unchanged
        existing = self._store.get_file(file_path)
        if existing and existing["hash"] == file_hash:
            return FileReport(
                path=file_path, status="unchanged",
                chunks=0,
            )

        # Chunk
        chunk_infos = chunk_markdown(raw, source_path=file_path)

        # Embed FIRST — if this fails, nothing was written and the file
        # remains indexable next time (see #7).
        if chunk_infos:
            texts = [c.text for c in chunk_infos]
            embeddings = self._embedder.embed_batch(texts)

        # Build chunk rows
        chunk_rows: list[dict] = []
        for info, emb in zip(chunk_infos, embeddings):
            chunk_rows.append({
                "id": info.id,
                "path": info.source_path,
                "source": source,
                "start_line": info.start_line,
                "end_line": info.end_line,
                "hash": info.hash,
                "model": EMBEDDING_MODEL,
                "text": info.text,
                "embedding": json.dumps(emb),
            })

        # Atomically replace old chunks + update file record
        self._store.replace_file_chunks(
            file_row={
                "path": file_path,
                "source": source,
                "hash": file_hash,
                "mtime": mtime,
                "size": len(raw.encode("utf-8")),
            },
            chunks=chunk_rows,
        )

        status = "updated" if existing else "new"
        return FileReport(
            path=file_path, status=status, chunks=len(chunk_infos),
        )

    # ------------------------------------------------------------------ #
    #  Search
    # ------------------------------------------------------------------ #

    def search(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        min_score: float = DEFAULT_MIN_SCORE,
        vector_weight: float = VECTOR_WEIGHT,
        text_weight: float = TEXT_WEIGHT,
    ) -> list[SearchResult]:
        """Run hybrid search against the indexed memory."""
        return self._searcher.search(
            query=query,
            max_results=max_results,
            min_score=min_score,
            vector_weight=vector_weight,
            text_weight=text_weight,
        )

    # ------------------------------------------------------------------ #
    #  Sync & watch
    # ------------------------------------------------------------------ #

    def sync(self, dir_path: str, source: str = "memory") -> SyncReport:
        """Sync index with the current state of files on disk.

        Re-indexes files that have changed mtime or are new.
        Removes entries for files that no longer exist.
        """
        return self.index_directory(dir_path, source=source)

    def start_watching(self, dir_path: str) -> None:
        """Start file-system monitoring for live indexing.

        When a .md file in *dir_path* changes, it is automatically
        re-indexed.
        """
        if self._watcher is not None:
            log.warning("Watcher already running, ignoring start_watching")
            return

        self._watch_dir = str(Path(dir_path))

        def _on_change(path: str):
            log.info("File changed, re-indexing: %s", path)
            try:
                self.index_file(path)
            except Exception:
                log.exception("Auto-index failed for %s", path)

        self._watcher = MemoryWatcher(self._watch_dir, on_change=_on_change)
        self._watcher.start()

    def stop_watching(self) -> None:
        """Stop file-system monitoring."""
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
            self._watch_dir = None

    # ------------------------------------------------------------------ #
    #  Stats & lifecycle
    # ------------------------------------------------------------------ #

    def stats(self) -> IndexStats:
        """Return file and chunk counts."""
        raw = self._store.get_stats()
        return IndexStats(files=raw["files"], chunks=raw["chunks"])

    def close(self) -> None:
        """Close the database and stop file watching."""
        self.stop_watching()
        self._store.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False
