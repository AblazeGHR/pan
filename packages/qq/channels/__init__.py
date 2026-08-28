"""QQ 通道注册表 / 工厂 / 配置解析。

职责：
    - resolve_channel_name(raw): 校验通道名，未知值回退默认 "napcat"
    - build_channel_spec(raw_qq_cfg): 从 config.json 的 qq 段解析出
      (name, ws_urls, token)，环境变量 ONEBOT_WS_URLS / ONEBOT_ACCESS_TOKEN 优先
      （单通道入口，向后兼容）
    - build_channel_specs(raw_qq_cfg): 多通道解析。qq.channels 数组存在则逐项
      解析为 [{"name", "ws_urls", "token", "bot_uin"}, ...]，否则退回
      build_channel_spec 单通道结果。name 支持别名（如 "llonebot2"），通道类按
      最长已知前缀匹配
    - create_channel(name, ws_urls, token, bot_uin, bot_fallback): 构造通道实例
    - get/set_active_channel(): 默认通道（bot.py 与 plugin.py 共享的第一个实例）
    - get_channel_by_name / get_channel_by_uin / iter_channels: 多账号注册表
      _ACTIVE_CHANNELS（name → channel），按通道名或 bot QQ 号查找

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
    "build_channel_specs",
    "create_channel",
    "get_active_channel",
    "set_active_channel",
    "get_channel_by_name",
    "get_channel_by_uin",
    "iter_channels",
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

# bot.py 构造并 stash 的默认通道实例（第一个通道）；plugin.py 优先复用
_ACTIVE: QQChannel | None = None

# 多账号注册表：通道名 → 通道实例（bot.py 启动时把全部通道 set 进来）
_ACTIVE_CHANNELS: dict[str, QQChannel] = {}


def resolve_channel_name(raw: str | None, default: str = _DEFAULT_CHANNEL) -> str:
    """校验通道名；空值或未知值回退默认 napcat（打印告警）。"""
    name = (raw or default).strip().lower()
    if name not in _CHANNELS:
        print(f"[QQ] 未知通道 '{raw}'，回退为 '{default}'")
        return default
    return name


def _resolve_channel_type(name: str | None) -> str:
    """通道名 → 通道类 key：精确匹配，否则最长已知前缀（"llonebot2"→"llonebot"），
    否则默认 napcat。别名通道与基类共用实现，仅 name/ws_urls/bot_uin 不同。"""
    n = (name or "").strip().lower()
    if n in _CHANNELS:
        return n
    for key in sorted(_CHANNELS, key=len, reverse=True):
        if n.startswith(key):
            return key
    return _DEFAULT_CHANNEL


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


def build_channel_specs(raw_qq_cfg: dict | None) -> list[dict]:
    """从 config.json 的 qq 段解析全部通道 spec（多通道入口）。

    qq.channels 为非空数组时逐项解析，每项形如：
        {"name": "llonebot2", "ws_urls": ["ws://127.0.0.1:3003"],
         "token": null, "bot_uin": "1470993983"}
    项内缺 ws_urls/token 时回退 qq.<name>.ws_urls/token，再回退通道类缺省端口。
    name 为空或整段无有效项时退回单通道 build_channel_spec（向后兼容）。

    注意：channels 数组模式下不读 ONEBOT_WS_URLS / ONEBOT_ACCESS_TOKEN 环境变量
    （env 是单通道覆盖入口，对所有通道生效会产生歧义）。

    返回 list[dict]：{"name", "ws_urls", "token", "bot_uin"}；bot_uin 可为 None
    （未配置时通道不按 self_id 过滤，采纳任何连入的 bot，与单通道行为一致）。
    """
    raw = raw_qq_cfg or {}
    specs: list[dict] = []
    arr = raw.get("channels")
    if isinstance(arr, list):
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip().lower()
            if not name:
                continue
            cls_key = _resolve_channel_type(name)
            sub = raw.get(name, {}) or {}
            ws = entry.get("ws_urls") or sub.get("ws_urls") or [_DEFAULT_WS[cls_key]]
            if isinstance(ws, str):
                ws = [ws]
            token = entry.get("token") or sub.get("token") or None
            specs.append({
                "name": name,
                "ws_urls": [str(u) for u in ws],
                "token": token,
                "bot_uin": str(entry["bot_uin"]) if entry.get("bot_uin") else None,
            })
    if not specs:
        name, ws, token = build_channel_spec(raw)
        sub = raw.get(name, {}) or {}
        specs = [{
            "name": name,
            "ws_urls": ws,
            "token": token,
            "bot_uin": str(sub["bot_uin"]) if sub.get("bot_uin") else None,
        }]
    return specs


def create_channel(
    name: str | None,
    ws_urls: list[str] | None = None,
    token: str | None = None,
    *,
    bot_uin: str | None = None,
    bot_fallback: Callable[[], object] | None = None,
) -> QQChannel:
    """构造通道实例。

    name 支持别名（如 "llonebot2"）：通道类按 _resolve_channel_type 的最长已知
    前缀匹配（llonebot2 → LLOneBotChannel），注册表以别名为主键；完全未知的
    名字回退 napcat 并告警（与单通道行为一致）。ws_urls 缺省用通道类缺省端口。
    """
    raw = str(name or "").strip().lower()
    if raw and raw not in _CHANNELS and not any(
        raw.startswith(k) for k in _CHANNELS
    ):
        raw = resolve_channel_name(raw)  # 完全未知 → 告警回退 napcat
    cls_key = _resolve_channel_type(raw)
    config = ChannelConfig(
        name=raw or cls_key,
        ws_urls=list(ws_urls or [_DEFAULT_WS[cls_key]]),
        token=token,
        bot_uin=bot_uin,
    )
    cls = _CHANNELS[cls_key]
    return cls(config, bot_fallback=bot_fallback)


def set_active_channel(ch: QQChannel | None, name: str | None = None) -> None:
    """注册通道实例。name 给定时写入注册表 _ACTIVE_CHANNELS[name] 并设为默认；
    否则仅当注册表尚无同名项时写入（保持首个通道为默认）。ch=None 仅清默认指针
    （旧测试/复位用法）。"""
    global _ACTIVE
    _ACTIVE = ch
    if ch is None:
        return
    if name is not None:
        _ACTIVE_CHANNELS[str(name)] = ch
    else:
        _ACTIVE_CHANNELS.setdefault(ch.name, ch)


def get_active_channel() -> QQChannel | None:
    """返回默认通道（bot.py stash 的第一个实例；未注册时 None）。"""
    return _ACTIVE


def get_channel_by_name(name: str | None) -> QQChannel | None:
    """按通道名查注册表；无名或未注册返回 None。"""
    if not name:
        return None
    return _ACTIVE_CHANNELS.get(str(name))


def get_channel_by_uin(uin: str | int | None) -> QQChannel | None:
    """按 bot QQ 号（self_id）查注册表；未配置 bot_uin 的通道不参与匹配。"""
    if not uin:
        return None
    uin = str(uin)
    for ch in _ACTIVE_CHANNELS.values():
        if ch.config.bot_uin and str(ch.config.bot_uin) == uin:
            return ch
    return None


def iter_channels() -> dict[str, QQChannel]:
    """返回注册表快照（name → channel），供 plugin 绑定全部通道。"""
    return dict(_ACTIVE_CHANNELS)
