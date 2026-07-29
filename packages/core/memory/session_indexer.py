"""Session transcript indexer — indexes conversation history into memory.

Off by default. Enable via config: memory.index_sessions = true.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import MemoryManager

log = logging.getLogger(__name__)

# Thresholds for incremental indexing
DEFAULT_DELTA_BYTES = 100_000    # Re-index after 100KB of new data
DEFAULT_DELTA_MESSAGES = 50      # Re-index after 50 new messages


class SessionIndexer:
    """Incrementally indexes session transcripts into the memory store.
    
    Usage::
    
        indexer = SessionIndexer(memory_manager, session_id="ses_xxx")
        indexer.index_history(session_history)  # Call after each turn
    """
    
    def __init__(
        self,
        memory_manager: MemoryManager,
        session_id: str,
        delta_bytes: int = DEFAULT_DELTA_BYTES,
        delta_messages: int = DEFAULT_DELTA_MESSAGES,
    ):
        self._mgr = memory_manager
        self._session_id = session_id
        self._delta_bytes = delta_bytes
        self._delta_messages = delta_messages
        self._last_indexed_size = 0
        self._last_indexed_count = 0
    
    def should_index(self, history: list[dict]) -> bool:
        """Check if enough new data exists to trigger re-indexing."""
        raw = json.dumps(history, ensure_ascii=False)
        size = len(raw.encode("utf-8"))
        count = len(history)
        
        size_delta = size - self._last_indexed_size
        count_delta = count - self._last_indexed_count
        
        return size_delta >= self._delta_bytes or count_delta >= self._delta_messages
    
    def index_history(self, history: list[dict]) -> int:
        """Index session history into the memory store.
        
        Returns number of new chunks indexed (0 if threshold not met).
        """
        if not self.should_index(history):
            return 0
        
        # Extract user + assistant messages as plain text
        lines: list[str] = []
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                lines.append(f"[{role}] {content}")
        
        if not lines:
            return 0
        
        full_text = "\n".join(lines)
        
        # Write to a temp .md file so the existing indexer can process it
        from tempfile import NamedTemporaryFile
        
        with NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(full_text)
            tmp_path = f.name
        
        try:
            report = self._mgr.index_file(
                tmp_path,
                source=f"sessions:{self._session_id}",
            )
            chunks = report.chunks
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        
        # Update tracking
        raw = json.dumps(history, ensure_ascii=False)
        self._last_indexed_size = len(raw.encode("utf-8"))
        self._last_indexed_count = len(history)
        
        log.info(
            "Indexed %d chunks from session %s (%d messages, %d bytes)",
            chunks, self._session_id, len(history), self._last_indexed_size,
        )
        return chunks


# Global registry (session_id -> SessionIndexer)
_indexers: dict[str, SessionIndexer] = {}


def get_or_create_indexer(
    memory_manager: MemoryManager,
    session_id: str,
) -> SessionIndexer:
    """Get or create a SessionIndexer for a session."""
    if session_id not in _indexers:
        _indexers[session_id] = SessionIndexer(memory_manager, session_id)
    return _indexers[session_id]


def remove_indexer(session_id: str):
    """Clean up indexer when session is deleted."""
    _indexers.pop(session_id, None)
