"""Adapter 注册表 — 按名注册和查找 CliAdapter 实例。"""

from __future__ import annotations
from .base import CliAdapter

_adapters: dict[str, CliAdapter] = {}


def register(name: str, adapter: CliAdapter) -> None:
    """注册一个 adapter 实例。重名覆盖。"""
    _adapters[name] = adapter


def get_adapter(name: str) -> CliAdapter:
    """按名取 adapter。不存在时抛 KeyError。"""
    if name not in _adapters:
        raise KeyError(
            f"Adapter '{name}' not registered. "
            f"Available: {list(_adapters.keys())}"
        )
    return _adapters[name]


def list_adapters() -> list[str]:
    """返回已注册的 adapter 名清单。"""
    return list(_adapters.keys())
