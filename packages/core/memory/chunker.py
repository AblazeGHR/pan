"""CJK-aware Markdown chunker for Pan's memory system.

Splits Markdown content into overlapping chunks using token-aware sizing
that adapts to CJK vs ASCII content density.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_TOKENS = 400
DEFAULT_CHUNK_OVERLAP = 80
CHARS_PER_TOKEN_ESTIMATE = 4  # English: ~4 chars per token on average
CJK_CHARS_PER_TOKEN = 1       # CJK: each character is roughly one token
CJK_RATIO_THRESHOLD = 0.2     # Use CJK mode when >20% of characters are CJK


# ---------------------------------------------------------------------------
# CJK detection
# ---------------------------------------------------------------------------

def is_cjk(c: str) -> bool:
    """Check whether a single character falls in the CJK Unicode range."""
    cp = ord(c)
    return (
        (0x4E00 <= cp <= 0x9FFF)   # CJK Unified Ideographs
        or (0x3400 <= cp <= 0x4DBF)  # CJK Unified Ideographs Extension A
        or (0x3000 <= cp <= 0x303F)  # CJK Symbols and Punctuation
        or (0xFF00 <= cp <= 0xFFEF)  # Halfwidth and Fullwidth Forms
        or (0x2E80 <= cp <= 0x2FFF)  # CJK Radicals Supplement
        or (0x31C0 <= cp <= 0x31EF)  # CJK Strokes
        or (0xF900 <= cp <= 0xFAFF)  # CJK Compatibility Ideographs
        or (0xAC00 <= cp <= 0xD7AF)  # Hangul Syllables (Korean)
    )


def _detect_cjk_ratio(text: str) -> float:
    """Return the fraction of CJK characters in *text*."""
    if not text:
        return 0.0
    cjk_count = sum(1 for c in text if is_cjk(c))
    return cjk_count / len(text)


def _chars_per_token(text: str) -> int:
    """Determine the character-per-token estimate for *text*."""
    if _detect_cjk_ratio(text) > CJK_RATIO_THRESHOLD:
        return CJK_CHARS_PER_TOKEN
    return CHARS_PER_TOKEN_ESTIMATE


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ChunkInfo:
    """Metadata for a single chunk produced by chunk_markdown()."""
    id: str
    text: str
    start_line: int
    end_line: int
    hash: str
    source_path: str


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_markdown(
    content: str,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    source_path: str = "unknown",
) -> list[ChunkInfo]:
    """Split Markdown *content* into overlapping, hash-identified chunks.

    Parameters
    ----------
    content : str
        The raw Markdown text to chunk.
    chunk_tokens : int
        Target chunk size in tokens (default 400).
    chunk_overlap : int
        Overlap size in tokens carried between consecutive chunks (default 80).
    source_path : str
        Identifier attached to every chunk for provenance tracking.

    Returns
    -------
    list[ChunkInfo]
        Ordered list of chunks.  Empty list when *content* is empty.
    """
    if not content:
        return []

    lines = content.splitlines()
    if not lines:
        return []

    cpt = _chars_per_token(content)
    max_chars = chunk_tokens * cpt
    overlap_chars = chunk_overlap * cpt
    current_lines: list[str] = []
    current_char_count = 0
    chunks: list[ChunkInfo] = []
    line_idx = 0  # 0-indexed cursor into *lines*

    while line_idx < len(lines):
        line = lines[line_idx]
        line_len = len(line)

        # --- Intra-line split when a single line exceeds max_chars ---
        if line_len > max_chars:
            # First flush any accumulated shorter lines
            if current_lines:
                chunks.append(_make_chunk(
                    current_lines, line_idx,
                    source_path=source_path,
                ))
                current_lines = []
                current_char_count = 0

            # Split the long line into overlapping pieces
            pos = 0
            chunk_line_num = line_idx + 1  # 1-indexed
            while pos < line_len:
                piece = line[pos:pos + max_chars]
                chunks.append(_make_chunk(
                    [piece], chunk_line_num,
                    source_path=source_path,
                ))
                pos += max_chars - overlap_chars
                if pos <= 0:  # safety: overlap could equal or exceed max_chars
                    pos = max_chars

            line_idx += 1
            continue

        # --- Flush when adding this line would exceed max_chars ---
        if current_lines and current_char_count + line_len > max_chars:
            chunks.append(_make_chunk(
                current_lines, line_idx,
                source_path=source_path,
            ))

            # --- Overlap: keep last N chars worth of lines ---
            carry_lines: list[str] = []
            carry_chars = 0
            for ol_line in reversed(current_lines):
                if carry_chars + len(ol_line) > overlap_chars:
                    break
                carry_lines.insert(0, ol_line)
                carry_chars += len(ol_line)

            current_lines = carry_lines
            current_char_count = carry_chars
            # Do NOT advance line_idx — reprocess the line that triggered flush
            continue

        # --- Accumulate ---
        current_lines.append(line)
        current_char_count += line_len
        line_idx += 1

    # --- Flush remaining lines ---
    if current_lines:
        chunks.append(_make_chunk(
            current_lines, line_idx,
            source_path=source_path,
        ))

    return chunks


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_chunk(
    lines: list[str],
    cursor_line: int,  # 0-indexed, points to the line AFTER the last included
    source_path: str = "unknown",
) -> ChunkInfo:
    """Build a ChunkInfo from a list of lines."""
    chunk_text = "\n".join(lines)
    hash_digest = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
    start_line = (cursor_line - len(lines)) + 1  # 1-indexed
    end_line = cursor_line
    return ChunkInfo(
        id=hash_digest[:16],
        text=chunk_text,
        start_line=start_line,
        end_line=end_line,
        hash=hash_digest,
        source_path=source_path,
    )
