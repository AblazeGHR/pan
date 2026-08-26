"""QQ 通道抽象层（interface）。

把「QQ 接入方式」抽象为一个可切换的通道（channel）：NapCat / LLOneBot 等都是
OneBot 11 协议网关，NoneBot 的 OneBot v11 适配器以正向 WS 连接到它们。通道抽象
让业务层（packages/qq/plugin.py）只依赖 ``QQChannel`` 接口，切换通道不改业务逻辑。

接口方法：
    startup / shutdown      生命周期（连接/断开，按通道实现）
    on_message(handler)     注册入站消息回调（业务层传入 handle_qq_message）
    send(target_type,...)   发送私聊/群消息（wire 层，不含落盘）
    recent_contacts()       取联系人/群列表（合并去重）
    is_connected()          当前是否已连接

业务层约定的消息模型是 ``QQMessage``（已归一化，不依赖具体协议的 event 对象）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable


class ChannelError(RuntimeError):
    """通道层统一异常基类。"""


class ChannelNotConnected(ChannelError):
    """通道尚未连接（无可用 bot）时抛出。"""


@dataclass
class QQMessage:
    """归一化后的入站 QQ 消息，业务层只认这个，不依赖 OneBot event 对象。

    scope:     "user"（私聊）/ "group"（群聊）
    scope_id:  私聊为 QQ 号，群聊为群号（字符串）
    text:      消息纯文本（OneBot get_plaintext 结果，不含 CQ 码外的控制段）
    sender_nickname: 发送者昵称（best-effort，可为空）
    at_bot:    群消息是否为 @ 本 bot（私聊恒为 True）；未在通道 hook 内过滤
    raw:       原始 event dict 透传（高级用法，如 CQ 码/图片），默认 None
    """

    scope: str
    scope_id: str
    text: str
    sender_nickname: str = ""
    at_bot: bool = True
    raw: dict | None = None

    def target_type(self) -> str:
        """映射为 api_send 用的 target_type：user→private，group→group。"""
        return "private" if self.scope == "user" else "group"


# 业务层注册的入站消息回调签名
MessageHandler = Callable[[QQMessage], Awaitable[None]]


@dataclass
class ChannelConfig:
    """通道连接配置（由 config.json 的 qq.<channel> 段解析而来）。"""

    name: str
    ws_urls: list[str] = field(default_factory=list)
    token: str | None = None
    # 反向 WS（LLOneBot ws-reverse）：若配置，NoneBot 作为 WS 服务端供网关连接。
    reverse_ws_url: str | None = None


class QQChannel(ABC):
    """QQ 通道接口。所有 QQ 接入方式（NapCat / LLOneBot / 未来其它）都实现它。"""

    #: 通道标识，如 "napcat" / "llonebot"
    name: str = "base"

    def __init__(self, config: ChannelConfig) -> None:
        self.config = config
        self.name = config.name
        self._handler: MessageHandler | None = None
        self._bot_fallback: Callable[[], object] | None = None

    # ── 生命周期 ──

    @abstractmethod
    async def startup(self) -> None:
        """建立连接 / 启动接收。仅在不致命的初始化阶段被调用。"""

    @abstractmethod
    async def shutdown(self) -> None:
        """断开连接 / 清理。"""

    # ── 入站消息 ──

    def on_message(self, handler: MessageHandler) -> None:
        """注册业务层入站消息回调（handle_qq_message）。"""
        self._handler = handler

    async def _dispatch(self, msg: QQMessage) -> None:
        """通道内部把归一化消息交给业务层（空 handler 时静默丢弃）。"""
        if self._handler is not None:
            await self._handler(msg)

    # ── 出站 / 查询 ──

    @abstractmethod
    async def send(
        self, target_type: str, target_id: str | int, text: str
    ) -> dict:
        """发送私聊/群消息（wire 层）。返回 {ok, message_id} 或 {ok:false,...}。

        target_type: "private" / "group"
        """

    @abstractmethod
    async def recent_contacts(self) -> dict:
        """返回 {ok, contacts:[{peerUin,peerName,chatType}]}（合并去重）。"""

    # ── 状态 ──

    @abstractmethod
    async def is_connected(self) -> bool:
        """当前通道是否已连接（有可用 bot）。"""

    # ── 连接信息（供 bot.py 配置 OneBot 适配器 / 日志）──

    def ws_urls(self) -> list[str]:
        """正向 WS 目标地址列表（网关 WS 服务端），供 OneBot 适配器连接。"""
        return list(self.config.ws_urls)

    def token(self) -> str | None:
        return self.config.token

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} ws={self.config.ws_urls}>"
