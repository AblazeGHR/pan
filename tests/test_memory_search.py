"""Tests for hybrid search module.

Tests the search logic without needing an API key by mocking the embedder.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.memory.search import (
    HybridSearcher,
    SearchResult,
    VECTOR_WEIGHT,
    TEXT_WEIGHT,
    DEFAULT_MIN_SCORE,
)

# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

def _make_mock_store(chunks=None, fts_results=None):
    store = MagicMock()
    store.get_chunks_for_search.return_value = chunks or []
    store.search_fts.return_value = fts_results or []
    return store


def _make_mock_embedder(return_vector=None):
    embedder = MagicMock()
    embedder.embed.return_value = return_vector or [0.1] * 1536
    return embedder


def _make_chunk(id_, text, embedding=None):
    return {
        "id": id_,
        "path": f"/mem/{id_}.md",
        "source": "memory",
        "start_line": 1,
        "end_line": 3,
        "text": text,
        "embedding": json.dumps(embedding or [0.1] * 1536),
    }


# ------------------------------------------------------------------ #
#  Tests
# ------------------------------------------------------------------ #

class TestHybridSearcher:
    def test_empty_query_returns_empty(self):
        searcher = HybridSearcher(
            _make_mock_store(), _make_mock_embedder()
        )
        assert searcher.search("") == []
        assert searcher.search("   ") == []

    def test_empty_store_returns_empty(self):
        store = _make_mock_store(chunks=[])
        embedder = _make_mock_embedder()
        searcher = HybridSearcher(store, embedder)
        assert searcher.search("test query") == []

    def test_single_chunk_matching(self):
        chunk = _make_chunk("abc", "coc 克苏鲁神话规则")
        store = _make_mock_store(chunks=[chunk])
        embedder = _make_mock_embedder([1.0] * 5)  # Same as chunk

        # Override dimensions for this simple test
        import packages.core.memory.search as mod
        # Make cosine similarity return 1.0 (perfect match)
        searcher = HybridSearcher(store, embedder)

        # Mock cosine to return 1.0
        orig_cos = mod.HybridSearcher._cosine_similarity
        mod.HybridSearcher._cosine_similarity = staticmethod(lambda a, b: 1.0)

        try:
            results = searcher.search("克苏鲁")
            assert len(results) == 1
            assert results[0].chunk_id == "abc"
            assert results[0].score > 0.5
        finally:
            mod.HybridSearcher._cosine_similarity = orig_cos

    def test_min_score_filter(self):
        chunk1 = _make_chunk("a1", "coc 基础规则")
        chunk2 = _make_chunk("a2", "dnd 相关")
        store = _make_mock_store(chunks=[chunk1, chunk2])
        embedder = _make_mock_embedder()

        import packages.core.memory.search as mod
        orig_cos = mod.HybridSearcher._cosine_similarity

        def mock_cos(a, b):
            return 0.1  # Very low score

        mod.HybridSearcher._cosine_similarity = staticmethod(mock_cos)

        try:
            searcher = HybridSearcher(store, embedder)
            results = searcher.search("test", min_score=0.35)
            assert results == []  # Below threshold
        finally:
            mod.HybridSearcher._cosine_similarity = orig_cos

    def test_max_results_limit(self):
        chunks = [_make_chunk(f"c{i}", f"文档内容 {i}") for i in range(10)]
        store = _make_mock_store(chunks=chunks)
        embedder = _make_mock_embedder()

        import packages.core.memory.search as mod
        orig_cos = mod.HybridSearcher._cosine_similarity
        mod.HybridSearcher._cosine_similarity = staticmethod(lambda a, b: 0.8)

        try:
            searcher = HybridSearcher(store, embedder)
            results = searcher.search("文档", max_results=3)
            assert len(results) == 3
        finally:
            mod.HybridSearcher._cosine_similarity = orig_cos

    def test_fts_only_result(self):
        """Chunk found by FTS but zero vector score."""
        chunk = _make_chunk("fts1", "FTS found this but vector missed")
        store = _make_mock_store(
            chunks=[chunk],
            fts_results=[{"id": "fts1", "rank": -5.0}],
        )
        embedder = _make_mock_embedder()

        import packages.core.memory.search as mod
        orig_cos = mod.HybridSearcher._cosine_similarity
        mod.HybridSearcher._cosine_similarity = staticmethod(lambda a, b: 0.1)

        try:
            searcher = HybridSearcher(store, embedder)
            results = searcher.search("missed by vector", max_results=5, min_score=0.01)
            assert len(results) == 1
            assert results[0].chunk_id == "fts1"
        finally:
            mod.HybridSearcher._cosine_similarity = orig_cos

    def test_invalid_embedding_skipped(self):
        """Chunk with malformed embedding JSON should be logged and skipped."""
        bad_chunk = {
            "id": "bad",
            "path": "/mem/bad.md",
            "source": "memory",
            "start_line": 1,
            "end_line": 1,
            "text": "corrupt data",
            "embedding": "NOT_VALID_JSON{{{",
        }
        good_chunk = _make_chunk("good", "good data")
        store = _make_mock_store(chunks=[bad_chunk, good_chunk])
        embedder = _make_mock_embedder()

        import packages.core.memory.search as mod
        orig_cos = mod.HybridSearcher._cosine_similarity
        mod.HybridSearcher._cosine_similarity = staticmethod(lambda a, b: 0.8)

        try:
            searcher = HybridSearcher(store, embedder)
            results = searcher.search("data")
            assert len(results) == 1
            assert results[0].chunk_id == "good"
        finally:
            mod.HybridSearcher._cosine_similarity = orig_cos

    def test_all_below_min_score(self):
        chunk = _make_chunk("low", "low relevance")
        store = _make_mock_store(chunks=[chunk])
        embedder = _make_mock_embedder()

        import packages.core.memory.search as mod
        orig_cos = mod.HybridSearcher._cosine_similarity
        mod.HybridSearcher._cosine_similarity = staticmethod(lambda a, b: 0.05)

        try:
            searcher = HybridSearcher(store, embedder)
            results = searcher.search("unrelated", min_score=0.35)
            assert results == []
        finally:
            mod.HybridSearcher._cosine_similarity = orig_cos


class TestSearchResult:
    def test_dataclass_fields(self):
        r = SearchResult(
            chunk_id="abc",
            path="/mem/test.md",
            text="hello",
            score=0.85,
            start_line=1,
            end_line=3,
            source="memory",
        )
        assert r.chunk_id == "abc"
        assert r.path == "/mem/test.md"
        assert r.score == 0.85


class TestCosineSimilarity:
    def test_perfect_match(self):
        from packages.core.memory.search import HybridSearcher

        v = [1.0, 2.0, 3.0]
        score = HybridSearcher._cosine_similarity(v, v)
        assert abs(score - 1.0) < 0.001

    def test_orthogonal(self):
        from packages.core.memory.search import HybridSearcher

        a = [1.0, 0.0]
        b = [0.0, 1.0]
        score = HybridSearcher._cosine_similarity(a, b)
        assert abs(score) < 0.001

    def test_zero_vector(self):
        from packages.core.memory.search import HybridSearcher

        score = HybridSearcher._cosine_similarity([0.0, 0.0], [1.0, 2.0])
        assert score == 0.0


class TestFtsNormalization:
    def test_positive_match(self):
        from packages.core.memory.search import HybridSearcher

        scores = HybridSearcher._normalize_fts_scores([
            {"id": "a", "rank": -5.0},
        ])
        # score = 1.0 / (1.0 + 5.0) = 1/6 ≈ 0.167
        assert 0.16 < scores["a"] < 0.17

    def test_bad_match(self):
        from packages.core.memory.search import HybridSearcher

        scores = HybridSearcher._normalize_fts_scores([
            {"id": "x", "rank": -0.01},
        ])
        # score = 1.0 / (1.0 + 0.01) ≈ 0.99
        assert 0.98 < scores["x"] < 1.0

    def test_missing_rank(self):
        from packages.core.memory.search import HybridSearcher

        scores = HybridSearcher._normalize_fts_scores([
            {"id": "norank"},
        ])
        assert scores["norank"] == 1.0


class TestQueryExpansion:
    def test_preserves_cjk(self):
        from packages.core.memory.search import HybridSearcher

        result = HybridSearcher._expand_query("克苏鲁 神话")
        assert "克苏鲁" in result
        assert "神话" in result

    def test_strips_ascii_punctuation(self):
        from packages.core.memory.search import HybridSearcher

        result = HybridSearcher._expand_query("hello, world! test?")
        assert "," not in result
        assert "!" not in result
        assert "?" not in result

    def test_preserves_hyphens(self):
        from packages.core.memory.search import HybridSearcher

        result = HybridSearcher._expand_query("non-breaking space")
        assert "non-breaking" in result
