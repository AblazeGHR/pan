"""iLink 协议常量 —— 单一事实源。

iLink 是腾讯 2026 年随「微信 ClawBot」开放的个人号 Bot API，纯 HTTP/JSON。
本协议较新，服务端字段/路径可能演进，因此**所有协议常量集中在本文件**，
且每一项都能被 config.json 的 ``wechat`` 段或 ``PAN_ILINK_*`` 环境变量覆盖
——协议漂移时改配置即可，不必改业务代码。

覆盖优先级（高 → 低）:
    PAN_ILINK_* 环境变量 > config.json 的 wechat 段 > 本文件默认值

可调项:
    PAN_ILINK_BASE_URL          协议 base url
    PAN_ILINK_BOT_TYPE          二维码 bot_type
    PAN_ILINK_CHANNEL_VERSION   base_info.channel_version
    PAN_ILINK_LONG_POLL_TIMEOUT getupdates 客户端超时（秒，须 > 服务端 35s 挂起）
    PAN_ILINK_PATHS             JSON dict，按 path key 覆盖单条路径
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# ── 默认值 ──

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"

#: path key → 路径。业务代码只用 key（spec.path("sendmessage")），不写字面量。
DEFAULT_PATHS: dict[str, str] = {
    "qrcode": "/ilink/bot/get_bot_qrcode",
    "qrcode_status": "/ilink/bot/get_qrcode_status",
    "getupdates": "/ilink/bot/getupdates",
    "sendmessage": "/ilink/bot/sendmessage",
    "getconfig": "/ilink/bot/getconfig",
    "sendtyping": "/ilink/bot/sendtyping",
}

DEFAULT_BOT_TYPE = 3
DEFAULT_CHANNEL_VERSION = "1.0.2"

#: getupdates 长轮询服务端挂起约 35s，客户端超时须留余量。
DEFAULT_LONG_POLL_TIMEOUT = 45.0

#: 单条请求超时（非轮询接口）。
DEFAULT_REQUEST_TIMEOUT = 15.0

#: 会话过期的错误码（服务端在 errcode 上返回）。
SESSION_EXPIRED_ERRCODE = -14

#: 消息类型：2 = bot 自己发出的消息（收消息时跳过，防自激循环）。
MESSAGE_TYPE_BOT = 2
#: 发送时的 message_type / message_state（2 = BOT / FINISH）。
OUT_MESSAGE_TYPE = 2
OUT_MESSAGE_STATE = 2
#: item_list 元素类型：1 = 文本。
ITEM_TYPE_TEXT = 1
#: typing 状态：1 = 开始，2 = 取消。
TYPING_START = 1
TYPING_CANCEL = 2


@dataclass
class ILinkSpec:
    """一份可运行的协议配置。用 ``load_spec()`` 构造。"""

    base_url: str = DEFAULT_BASE_URL
    paths: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PATHS))
    bot_type: int = DEFAULT_BOT_TYPE
    channel_version: str = DEFAULT_CHANNEL_VERSION
    long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT

    def path(self, key: str) -> str:
        """按 key 取路径；未知 key 抛 KeyError（早失败，别静默拼出坏 URL）。"""
        try:
            return self.paths[key]
        except KeyError:
            raise KeyError(f"unknown iLink path key: {key!r}") from None

    def url(self, key: str) -> str:
        """base_url + path。"""
        return f"{self.base_url.rstrip('/')}{self.path(key)}"

    def base_info(self) -> dict:
        """每个请求体都要带的 base_info 段。"""
        return {"channel_version": self.channel_version}


def _config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config.json"


def _load_wechat_config() -> dict:
    """读 config.json 的 wechat 段；读不到返回空 dict（不抛）。"""
    try:
        cfg = json.loads(_config_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return cfg.get("wechat") or {}


def load_spec(wechat_cfg: dict | None = None) -> ILinkSpec:
    """构造 ILinkSpec：默认值 < config.json 的 wechat 段 < PAN_ILINK_* 环境变量。

    wechat_cfg 显式传入时用它（测试/调用方已读过配置）；否则自行读 config.json。
    环境变量只做临时覆盖，优先级最高。
    """
    cfg = _load_wechat_config() if wechat_cfg is None else (wechat_cfg or {})

    base_url = (
        os.environ.get("PAN_ILINK_BASE_URL")
        or cfg.get("base_url")
        or DEFAULT_BASE_URL
    )

    paths = dict(DEFAULT_PATHS)
    cfg_paths = cfg.get("paths")
    if isinstance(cfg_paths, dict):
        paths.update({str(k): str(v) for k, v in cfg_paths.items()})
    env_paths = os.environ.get("PAN_ILINK_PATHS")
    if env_paths:
        try:
            paths.update({str(k): str(v) for k, v in json.loads(env_paths).items()})
        except (ValueError, json.JSONDecodeError):
            pass  # 坏 JSON：忽略环境变量覆盖，不阻断启动

    def _num(env_key: str, cfg_key: str, default: float) -> float:
        for raw in (os.environ.get(env_key), cfg.get(cfg_key)):
            if raw is None or raw == "":
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return default

    def _int(env_key: str, cfg_key: str, default: int) -> int:
        for raw in (os.environ.get(env_key), cfg.get(cfg_key)):
            if raw is None or raw == "":
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                continue
        return default

    return ILinkSpec(
        base_url=str(base_url),
        paths=paths,
        bot_type=_int("PAN_ILINK_BOT_TYPE", "bot_type", DEFAULT_BOT_TYPE),
        channel_version=str(
            os.environ.get("PAN_ILINK_CHANNEL_VERSION")
            or cfg.get("channel_version")
            or DEFAULT_CHANNEL_VERSION
        ),
        long_poll_timeout=_num(
            "PAN_ILINK_LONG_POLL_TIMEOUT", "long_poll_timeout", DEFAULT_LONG_POLL_TIMEOUT
        ),
        request_timeout=_num(
            "PAN_ILINK_REQUEST_TIMEOUT", "request_timeout", DEFAULT_REQUEST_TIMEOUT
        ),
    )
