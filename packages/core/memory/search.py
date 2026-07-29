"""Hybrid search combining vector cosine similarity with FTS5 text match.

Strategy from OpenClaw: vector_weight * cosine_sim + text_weight * fts_score.
Default weights: 0.7 vector, 0.3 text.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import MemoryStore
    from .embedder import Embedder

logger = logging.getLogger(__name__)

# ── Defaults (from OpenClaw) ────────────────────────────────────────── #

VECTOR_WEIGHT = 0.7
TEXT_WEIGHT = 0.3
DEFAULT_MAX_RESULTS = 6
DEFAULT_MIN_SCORE = 0.20
CANDIDATE_MULTIPLIER = 4


# ── Search result ───────────────────────────────────────────────────── #

@dataclass
class SearchResult:
    """A single search result with hybrid score and metadata."""

    chunk_id: str
    path: str
    text: str
    score: float  # 0.0 .. 1.0, higher is better
    start_line: int
    end_line: int
    source: str


# ── Hybrid searcher ─────────────────────────────────────────────────── #

class HybridSearcher:
    """Combines vector cosine similarity and FTS5 BM25 for ranked retrieval."""

    def __init__(self, store: MemoryStore, embedder: Embedder) -> None:
        self._store = store
        self._embedder = embedder

    # ---------------------------------------------------------------- #
    #  Public API
    # ---------------------------------------------------------------- #

    def search(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        min_score: float = DEFAULT_MIN_SCORE,
        vector_weight: float = VECTOR_WEIGHT,
        text_weight: float = TEXT_WEIGHT,
    ) -> list[SearchResult]:
        """Run hybrid search and return ranked results.

        Steps:
          1. Embed the query.
          2. Compute cosine similarity for every chunk.
          3. Run FTS5 with expanded query terms.
          4. Merge: hybrid = vector_weight * cosine + text_weight * fts.
          5. Filter by *min_score*, sort descending, take top *max_results*.
        """
        if not query.strip():
            return []

        # 1. Embed the query
        query_embedding = self._embedder.embed(query)

        # 2. Vector scoring
        chunks = self._store.get_chunks_for_search()
        if not chunks:
            return []

        vector_scores: dict[str, float] = {}
        chunk_map: dict[str, dict] = {}
        for chunk in chunks:
            chunk_id = chunk["id"]
            chunk_map[chunk_id] = chunk
            try:
                emb = json.loads(chunk["embedding"])
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning(
                    "Skipping chunk %s — invalid embedding JSON: %s",
                    chunk_id,
                    exc,
                )
                continue
            vector_scores[chunk_id] = self._cosine_similarity(
                query_embedding, emb
            )

        # 3. FTS scoring
        expanded_query = self._expand_query(query)
        fts_results = self._store.search_fts(
            expanded_query, max_results * CANDIDATE_MULTIPLIER
        )
        fts_scores = self._normalize_fts_scores(fts_results)

        # 4. Hybrid merge — every chunk_id from EITHER result set
        all_ids = set(vector_scores.keys()) | set(fts_scores.keys())
        results: list[SearchResult] = []
        for chunk_id in all_ids:
            vs = vector_scores.get(chunk_id, 0.0)
            ts = fts_scores.get(chunk_id, 0.0)
            hybrid_score = vector_weight * vs + text_weight * ts

            if hybrid_score < min_score:
                continue

            chunk = chunk_map.get(chunk_id)
            if chunk is None:
                # Can happen if chunk was in FTS but dropped during vector
                # scoring (invalid embedding). Skip.
                continue

            results.append(
                SearchResult(
                    chunk_id=chunk_id,
                    path=chunk["path"],
                    text=chunk["text"],
                    score=hybrid_score,
                    start_line=chunk["start_line"],
                    end_line=chunk["end_line"],
                    source=chunk["source"],
                )
            )

        # 5. Sort by score descending, return top N
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:max_results]

    # ---------------------------------------------------------------- #
    #  Helpers
    # ---------------------------------------------------------------- #

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two equal-length vectors.

        Returns a value in [0.0, 1.0] (clamped from [-1, 1]).
        """
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return max(0.0, dot / (norm_a * norm_b))

    @staticmethod
    def _normalize_fts_scores(fts_results: list[dict]) -> dict[str, float]:
        """Convert FTS5 rank values to 0..1 scores.

        FTS5 rank is negative (more negative = better match).
        Normalized: score = 1.0 / (1.0 + (-rank))
        """
        scores: dict[str, float] = {}
        for row in fts_results:
            rank = row.get("rank", 0)
            scores[row["id"]] = 1.0 / (1.0 + (-rank))
        return scores

    @staticmethod
    def _expand_query(query: str) -> str:
        """Extract keywords for FTS5 matching.

        Strips common punctuation while preserving CJK characters.
        Returns space-separated terms suitable for FTS5 unicode61 tokenizer.
        """
        # Remove ASCII punctuation except hyphens (keep for compound terms)
        cleaned = re.sub(r"[^\w\s\u4e00-\u9fff\-]", " ", query)
        # Collapse whitespace
        terms = cleaned.split()
        return " ".join(terms)
