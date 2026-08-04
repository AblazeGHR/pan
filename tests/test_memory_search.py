"""Tests for hybrid search module.

Tests the search logic without needing an API key by mocking the embedder.
"""

import json
import os
import sys
import tempfile
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
        # Query vector must match chunk dims (1536) — see #14 dims guard
        embedder = _make_mock_embedder([1.0] * 1536)

        # Override dimensions for this simple test
        import packages.core.memory.search as mod
        # Make cosine similarity return 1.0 (perfect match)
        searcher = HybridSearcher(store, embedder)

        # Mock cosine to return 1.0
        orig_cos = mod.HybridSearcher.__dict__["_cosine_similarity"]
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
        orig_cos = mod.HybridSearcher.__dict__["_cosine_similarity"]

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
        orig_cos = mod.HybridSearcher.__dict__["_cosine_similarity"]
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
        orig_cos = mod.HybridSearcher.__dict__["_cosine_similarity"]
        mod.HybridSearcher._cosine_similarity = staticmethod(lambda a, b: 0.1)

        try:
            searcher = HybridSearcher(store, embedder)
            results = searcher.search("missed by vector", max_results=5, min_score=0.01)
            assert len(results) == 1
            assert results[0].chunk_id == "fts1"
        finally:
            mod.HybridSearcher._cosine_similarity = orig_cos

    def test_dims_mismatch_skipped(self):
        """Chunk embedding dims != query dims must be skipped, not compared."""
        # Chunk has 1536-dim embedding, query vector is 5-dim
        chunk = _make_chunk("mismatch", "dim mismatch")
        store = _make_mock_store(chunks=[chunk])
        embedder = _make_mock_embedder([1.0] * 5)

        import packages.core.memory.search as mod
        orig_cos = mod.HybridSearcher.__dict__["_cosine_similarity"]
        mod.HybridSearcher._cosine_similarity = staticmethod(lambda a, b: 1.0)

        try:
            searcher = HybridSearcher(store, embedder)
            results = searcher.search("test", min_score=0.01)
            assert results == []  # skipped due to dims mismatch
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
        orig_cos = mod.HybridSearcher.__dict__["_cosine_similarity"]
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
        orig_cos = mod.HybridSearcher.__dict__["_cosine_similarity"]
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
        # score = (-rank)/(1 + -rank) = 5/6 ≈ 0.833 (better match → higher)
        assert 0.82 < scores["a"] < 0.85

    def test_bad_match(self):
        from packages.core.memory.search import HybridSearcher

        scores = HybridSearcher._normalize_fts_scores([
            {"id": "x", "rank": -0.01},
        ])
        # score = 0.01/1.01 ≈ 0.0099 (worse match → near 0)
        assert 0.0 < scores["x"] < 0.02

    def test_missing_rank(self):
        from packages.core.memory.search import HybridSearcher

        scores = HybridSearcher._normalize_fts_scores([
            {"id": "norank"},
        ])
        assert scores["norank"] == 0.0


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


# ------------------------------------------------------------------ #
#  Real SQLite integration (not mocked) — #38
# ------------------------------------------------------------------ #

class TestRealSqliteIntegration:
    """Exercise HybridSearcher against a real MemoryStore + FTS5 schema.

    The unit tests above mock the store; this class verifies the actual SQL,
    tokenizer config, and hybrid merge behave correctly end-to-end.
    """

    def _search(self, query, embed_vector):
        from packages.core.memory.store import MemoryStore

        db = os.path.join(tempfile.mkdtemp(prefix="pan-search-"), "test.sqlite")
        store = MemoryStore(db)
        try:
            store.insert_file("/mem/coc.md", "memory", "h1", 0.0, 10)
            store.insert_chunk({
                "id": "coc00000000000001",
                "path": "/mem/coc.md", "source": "memory",
                "start_line": 1, "end_line": 2, "hash": "h1",
                "model": "test", "text": "克苏鲁神话 COC 规则书 技能检定",
                "embedding": json.dumps([1.0, 0.0, 0.0, 0.0]),
            })
            store.insert_file("/mem/dnd.md", "memory", "h2", 0.0, 10)
            store.insert_chunk({
                "id": "dnd00000000000001",
                "path": "/mem/dnd.md", "source": "memory",
                "start_line": 1, "end_line": 2, "hash": "h2",
                "model": "test", "text": "龙与地下城 冒险者 法术 地下城",
                "embedding": json.dumps([0.0, 0.0, 0.0, 1.0]),
            })

            class FakeEmbedder:
                dims = 4

                def embed(self, text):
                    return embed_vector

            searcher = HybridSearcher(store, FakeEmbedder())
            return searcher.search(query, min_score=0.05)
        finally:
            store.close()

    def test_real_sqlite_ranks_similar_chunk_first(self):
        # Query vector close to the COC chunk → it must rank first via real
        # cosine + FTS against a real SQLite DB.
        results = self._search("COC 技能检定", [0.9, 0.1, 0.0, 0.0])
        assert results, "real SQLite search returned no results"
        assert results[0].chunk_id == "coc00000000000001", (
            f"expected COC chunk first, got {results[0].chunk_id}"
        )

    def test_real_sqlite_fts_operator_query_does_not_raise(self):
        # Unquoted FTS5 operators in a user query must be neutralized (#27)
        # against the real tokenizer, not just in a mocked store.
        results = self._search("COC OR 法术", [0.9, 0.1, 0.0, 0.0])
        # Must not raise; vector side alone should still surface the COC chunk.
        assert any(r.chunk_id == "coc00000000000001" for r in results)

    def test_real_sqlite_stats(self):
        db = os.path.join(tempfile.mkdtemp(prefix="pan-search-"), "test.sqlite")
        from packages.core.memory.store import MemoryStore

        store = MemoryStore(db)
        try:
            store.insert_file("/mem/x.md", "memory", "h", 0.0, 5)
            store.insert_chunk({
                "id": "x" * 16,
                "path": "/mem/x.md", "source": "memory",
                "start_line": 1, "end_line": 1, "hash": "h",
                "model": "test", "text": "hello world",
                "embedding": json.dumps([0.5, 0.5]),
            })
            stats = store.get_stats()
            assert stats["files"] == 1
            assert stats["chunks"] == 1
            # FTS row mirrored by insert_chunk
            assert len(store.search_fts('"hello"')) == 1
        finally:
            store.close()
