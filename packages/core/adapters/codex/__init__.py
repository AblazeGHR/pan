"""OpenAI Codex CLI adapter for Pan.

- adapter.py：实现 CliAdapter 协议（app-server 长驻 + stream 模式）
- wrapper.py：稳定入口；默认转发到 `app_server_wrapper.py`，保留 `codex exec` 兼容路径
- sessions.py：SessionsProvider（读 ~/.codex 的 SQLite + rollout）
"""

from __future__ import annotations

from .adapter import CodexAdapter

__all__ = ["CodexAdapter"]
