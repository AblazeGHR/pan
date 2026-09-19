"""Canonical Codex tool display projection shared by live and history paths."""

from __future__ import annotations

import json
from typing import Any


def canonical_tool_json(value: Any) -> str:
    """Match the frontend's compact Python-compatible JSON projection."""
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    )


def canonical_tool_content(name: str, value: Any) -> str:
    return f"{name}({canonical_tool_json(value)})"
