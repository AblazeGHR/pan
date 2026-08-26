"""QQ 通道注册表 / 工厂 / 配置解析。

职责：
    - resolve_channel_name(raw): 校验通道名，未知值回退默认 "napcat"
    - build_channel_spec(raw_qq_cfg): 从 config.json 的 qq 段解析出
      (name, ws_urls, token)，环境变量 ONEBOT_WS_URLS / ONEBOT_ACCESS_TOKEN 优先
    - create_channel(name, ws_urls, token, bot_fallback): 构造通道实例
    - get/set_active_channel(): 在 bot.py 与 plugin.py 间共享同一通道实例
      （bot.py 先构造并 stash，plugin.py 复用；单测直接 import plugin 时plugin自建）

默认 WS 地址：napcat→3001（与运行中的生产通道一致），llonebot→3002（避免与 NapCat
端口冲突）。
"""

from __future__ import annotations

import json
import os
from typing import Callable

from .base import ChannelConfig, QQChannel, QQMessage
from .llonebot import LLOneBotChannel
from .napcat import NapCatChannel
from .onebot import OneBotChannel

__all__ = [
    "QQChannel",
    "QQMessage",
    "OneBotChannel",
    "NapCatChannel",
    "LLOneBotChannel",
    "ChannelConfig",
    "ChannelError",
    "ChannelNotConnected",
    "resolve_channel_name",
    "build_channel_spec",
    "create_channel",
    "get_active_channel",
    "set_active_channel",
]

from .base import ChannelError, ChannelNotConnected  # noqa: E402

# 已注册的通道类
_CHANNELS: dict[str, type[QQChannel]] = {
    "napcat": NapCatChannel,
    "llonebot": LLOneBotChannel,
}

# 各通道缺省正向 WS 地址（LLOneBot 用 3002 避开 NapCat 的 3001）
_DEFAULT_WS: dict[str, str] = {
    "napcat": "ws://127.0.0.1:3001",
    "llonebot": "ws://127.0.0.1:3002",
}

_DEFAULT_CHANNEL = "napcat"

# bot.py 构造并 stash 的当前进程通道实例；plugin.py 优先复用
_ACTIVE: QQChannel | None = None


def resolve_channel_name(raw: str | None, default: str = _DEFAULT_CHANNEL) -> str:
    """校验通道名；空值或未知值回退默认 napcat（打印告警）。"""
    name = (raw or default).strip().lower()
    if name not in _CHANNELS:
        print(f"[QQ] 未知通道 '{raw}'，回退为 '{default}'")
        return default
    return name


def build_channel_spec(raw_qq_cfg: dict | None) -> tuple[str, list[str], str | None]:
    """从 config.json 的 qq 段解析 (name, ws_urls, token)。

    优先级：环境变量 ONEBOT_WS_URLS/ONEBOT_ACCESS_TOKEN（最显式，可经 .env 覆盖）
    > qq.<channel>.ws_urls/token > qq.ws_url（旧字段兼容）> 通道缺省地址。
    """
    raw_qq_cfg = raw_qq_cfg or {}
    name = resolve_channel_name(raw_qq_cfg.get("channel"))
    sub = raw_qq_cfg.get(name, {}) or {}

    # ws_urls：环境变量优先，其次通道专属配置，其次旧 qq.ws_url，最后缺省
    ws = None
    env = os.getenv("ONEBOT_WS_URLS")
    if env:
        try:
            ws = json.loads(env)
        except (ValueError, json.JSONDecodeError):
            ws = None
    if not ws:
        ws = sub.get("ws_urls")
    if not ws and raw_qq_cfg.get("ws_url"):
        ws = [raw_qq_cfg["ws_url"]]
    if not ws:
        ws = [_DEFAULT_WS[name]]
    if isinstance(ws, str):
        ws = [ws]

    # token：环境变量优先，其次通道专属配置
    token = os.getenv("ONEBOT_ACCESS_TOKEN") or sub.get("token") or None
    return name, list(ws), token


def create_channel(
    name: str | None,
    ws_urls: list[str] | None = None,
    token: str | None = None,
    *,
    bot_fallback: Callable[[], object] | None = None,
) -> QQChannel:
    """构造通道实例。name 经 resolve_channel_name 校验（未知回退 napcat）。"""
    name = resolve_channel_name(name)
    ws_urls = ws_urls or [_DEFAULT_WS[name]]
    config = ChannelConfig(name=name, ws_urls=list(ws_urls), token=token)
    cls = _CHANNELS[name]
    return cls(config, bot_fallback=bot_fallback)


def set_active_channel(ch: QQChannel) -> None:
    global _ACTIVE
    _ACTIVE = ch


def get_active_channel() -> QQChannel | None:
    return _ACTIVE
