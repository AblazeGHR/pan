"""Adapter 注册表 — 按名注册和查找 CliAdapter 实例 / SessionsProvider。"""

from __future__ import annotations
import inspect
from .base import CliAdapter, SessionsProvider

_adapters: dict[str, CliAdapter] = {}
_sessions_providers: dict[str, SessionsProvider] = {}


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


def list_adapters() -> list[CliAdapter]:
    """返回已注册的 adapter 实例清单。"""
    return list(_adapters.values())


def register_sessions_provider(name: str, provider: SessionsProvider) -> None:
    """注册 adapter 的 sessions provider（模块，提供 SessionsProvider 协议函数）。

    每新增一个 adapter，只要其 sessions 模块实现协议命名函数，在此注册一行，
    server.py 的 import/branch/rename 分派即自动覆盖（adapter-architecture P0-2）。
    """
    _sessions_providers[name] = provider


def get_sessions_provider(name: str) -> SessionsProvider:
    """按 adapter 名取 sessions provider。不存在时抛 KeyError。"""
    if name not in _sessions_providers:
        raise KeyError(
            f"Sessions provider for adapter '{name}' not registered. "
            f"Available: {list(_sessions_providers.keys())}"
        )
    return _sessions_providers[name]


def list_sessions_providers() -> list[SessionsProvider]:
    """返回已注册的 sessions provider 清单。"""
    return list(_sessions_providers.values())


def provider_kwargs(func, **candidates) -> dict:
    """Filter *candidates* to the keyword arguments *func* actually accepts.

    SessionsProvider 协议外，个别 provider 支持额外 kwargs（目前只有 kimi 的
    ``kimi_home``：方案 C 隔离 HOME，MCP 会话的原生状态只存在于该 HOME）。
    调用方（server 的 rename/branch、worker 的 branch）按 session 的
    adapter_config 构造候选值，由本函数按签名过滤——不支持该参数的 provider
    （cbc/codex/claude/opencode）不会收到多余 kwarg（否则 TypeError）。
    """
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD
           for p in params.values()):
        return dict(candidates)
    return {k: v for k, v in candidates.items() if k in params}


def adapter_supports_native_control(name: str) -> bool:
    """True when the registered adapter declares a Terminal TUI transport.

    Gate by capability (``supports_native_control``), never by a hardcoded
    adapter-name set: cbc/codex/kimi declare it, others do not.
    """
    try:
        adapter = get_adapter(name)
    except KeyError:
        return False
    return bool(getattr(adapter, "supports_native_control", False))
