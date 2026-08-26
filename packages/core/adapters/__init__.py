"""Pan CLI Adapters.

协议定义 + 注册表 + 内置 adapter。
"""

from __future__ import annotations

from .base import CliAdapter, SessionsProvider
from .resolution import resolve_execution_mode
from .registry import (
    register,
    get_adapter,
    list_adapters,
    register_sessions_provider,
    get_sessions_provider,
    list_sessions_providers,
)
from .cbc import CbcAdapter
from .cbc import sessions as cbc_sessions
from .kimi import KimiAdapter
from .kimi import sessions as kimi_sessions
from .opencode import OpencodeAdapter
from .opencode import sessions as opencode_sessions
from .claude import ClaudeAdapter
from .claude import sessions as claude_sessions

# 启动时注册内置 adapter
register("cbc", CbcAdapter())
register("kimi", KimiAdapter())
register("opencode", OpencodeAdapter())
register("claude", ClaudeAdapter())

# 启动时注册 sessions provider（P0-2：server 按 adapter 名统一调用，
# 无需为每个 adapter 写 import/branch/rename 分派）。
register_sessions_provider("cbc", cbc_sessions)
register_sessions_provider("kimi", kimi_sessions)
register_sessions_provider("opencode", opencode_sessions)
register_sessions_provider("claude", claude_sessions)

__all__ = [
    "CliAdapter",
    "SessionsProvider",
    "register",
    "get_adapter",
    "list_adapters",
    "register_sessions_provider",
    "get_sessions_provider",
    "list_sessions_providers",
    "resolve_execution_mode",
    "CbcAdapter",
    "KimiAdapter",
    "OpencodeAdapter",
    "ClaudeAdapter",
]
