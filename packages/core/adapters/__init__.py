"""Pan CLI Adapters.

协议定义 + 注册表 + 内置 adapter。
"""

from __future__ import annotations

from .base import CliAdapter
from .registry import register, get_adapter, list_adapters
from .cbc import CbcAdapter
from .kimi import KimiAdapter
from .opencode import OpencodeAdapter

# 启动时注册内置 adapter
register("cbc", CbcAdapter())
register("kimi", KimiAdapter())
register("opencode", OpencodeAdapter())

__all__ = [
    "CliAdapter",
    "register",
    "get_adapter",
    "list_adapters",
    "CbcAdapter",
    "KimiAdapter",
    "OpencodeAdapter",
]
