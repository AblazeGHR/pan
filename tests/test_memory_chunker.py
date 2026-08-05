"""Tests for CJK-aware Markdown chunker."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.memory.chunker import (
    chunk_markdown,
    is_cjk,
    _detect_cjk_ratio,
    _chars_per_token,
    DEFAULT_CHUNK_TOKENS,
    DEFAULT_CHUNK_OVERLAP,
    CHARS_PER_TOKEN_ESTIMATE,
    CJK_CHARS_PER_TOKEN,
)


class TestIsCjk:
    def test_cjk_ideograph(self):
        assert is_cjk("中") is True
        assert is_cjk("文") is True
        assert is_cjk("日") is True
        assert is_cjk("本") is True

    def test_cjk_punctuation(self):
        assert is_cjk("。") is True
        assert is_cjk("、") is True
        assert is_cjk("「") is True

    def test_fullwidth(self):
        assert is_cjk("Ａ") is True  # Fullwidth A
        assert is_cjk("０") is True  # Fullwidth 0

    def test_ascii(self):
        assert is_cjk("A") is False
        assert is_cjk("1") is False
        assert is_cjk(" ") is False
        assert is_cjk("\n") is False

    def test_hangul(self):
        assert is_cjk("한") is True
        assert is_cjk("글") is True


class TestDetectCjkRatio:
    def test_pure_ascii(self):
        assert _detect_cjk_ratio("Hello World") == 0.0

    def test_pure_cjk(self):
        assert _detect_cjk_ratio("你好世界") == 1.0

    def test_mixed(self):
        ratio = _detect_cjk_ratio("Hello 世界")
        assert ratio == 2 / 8  # 2 CJK out of 8 chars

    def test_empty(self):
        assert _detect_cjk_ratio("") == 0.0


class TestCharsPerToken:
    def test_ascii_content(self):
        assert _chars_per_token("Hello World") == CHARS_PER_TOKEN_ESTIMATE

    def test_cjk_content(self):
        assert _chars_per_token("你好世界") == CJK_CHARS_PER_TOKEN

    def test_threshold_boundary(self):
        # 1/2 CJK = 50% > 20% threshold → uses CJK estimate
        assert _chars_per_token("a中") == CJK_CHARS_PER_TOKEN
        # 2/3 CJK = 66% > 20% threshold → uses CJK estimate
        assert _chars_per_token("中中a") == CJK_CHARS_PER_TOKEN


class TestChunkMarkdown:
    def test_empty_content(self):
        assert chunk_markdown("") == []

    def test_single_short_line(self):
        chunks = chunk_markdown("hello world")
        assert len(chunks) == 1
        assert chunks[0].text == "hello world"
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 1
        assert len(chunks[0].hash) == 64

    def test_multiple_short_lines(self):
        content = "\n".join(["line one", "line two", "line three"])
        chunks = chunk_markdown(content)
        assert len(chunks) == 1
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 3

    def test_id_is_first_16_chars_of_hash(self):
        chunks = chunk_markdown("test content")
        h = chunks[0].hash
        assert chunks[0].id == h[:16]

    def test_source_path(self):
        chunks = chunk_markdown("test", source_path="foo/bar.md")
        assert chunks[0].source_path == "foo/bar.md"

    def test_cjk_content_smaller_chunks(self):
        """CJK content should produce more chunks than ASCII for same char count."""
        ascii_content = "x" * 800  # 800 chars → ~200 tokens → 1 chunk
        cjk_content = "中" * 800   # 800 chars → ~800 tokens → 3 chunks (400+400+remainder)

        ascii_chunks = chunk_markdown(ascii_content, chunk_tokens=400)
        cjk_chunks = chunk_markdown(cjk_content, chunk_tokens=400)

        assert len(ascii_chunks) == 1
        assert len(cjk_chunks) >= 2

    def test_chunk_line_numbers(self):
        content = "\n".join(str(i) for i in range(1, 51))  # 50 lines of "1" to "50"
        chunks = chunk_markdown(content, chunk_tokens=100, chunk_overlap=10)

        # Check line numbers are contiguous and increasing
        prev_end = 0
        for chunk in chunks:
            assert chunk.start_line > prev_end or (prev_end == 0 and chunk.start_line == 1)
            assert chunk.end_line >= chunk.start_line
            prev_end = chunk.end_line

    def test_overlap_exists(self):
        """Verify that consecutive chunks have overlapping content."""
        content = "\n".join(f"line {i:03d}" for i in range(1, 200))
        chunks = chunk_markdown(content, chunk_tokens=50, chunk_overlap=20)

        assert len(chunks) >= 2, f"expected multiple chunks, got {len(chunks)}"
        for i in range(1, len(chunks)):
            prev_lines = set(chunks[i - 1].text.splitlines())
            cur_lines = set(chunks[i].text.splitlines())
            overlap = prev_lines & cur_lines
            assert overlap, (
                f"chunk {i} shares no lines with chunk {i - 1} "
                f"(overlap broken): {chunks[i - 1].text!r} vs {chunks[i].text!r}"
            )

    def test_hash_reproducibility(self):
        """Same content should produce same hash."""
        content = "this is a test of consistent hashing"
        chunks1 = chunk_markdown(content)
        chunks2 = chunk_markdown(content)
        assert chunks1[0].hash == chunks2[0].hash
        assert chunks1[0].id == chunks2[0].id

    def test_very_long_single_line(self):
        """Very long line should still be chunked."""
        long_line = "A" * 5000  # > 400*4 chars
        chunks = chunk_markdown(long_line, chunk_tokens=400)
        assert len(chunks) >= 2
