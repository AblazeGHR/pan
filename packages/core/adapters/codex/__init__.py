"""OpenAI Codex CLI adapter for Pan.

- adapter.py：实现 CliAdapter 协议（wrapper 长驻 + stream 模式）
- wrapper.py：长驻包装器，内部逐条驱动 `codex exec --json`
- sessions.py：SessionsProvider（读 ~/.codex 的 SQLite + rollout）
"""

from __future__ import annotations

from .adapter import CodexAdapter

__all__ = ["CodexAdapter"]
