"""微信通道抽象层（interface）。

把「微信接入方式」抽象为可切换的通道。v1 只有 iLink 一个实现
（:mod:`packages.wechat.ilink_channel`），抽象层的意义在于业务层
（``plugin.py``）只依赖本文件的 ``WeChatChannel`` / ``WeChatMessage``，
将来换接入方式不改业务逻辑。

与 QQ 的 ``packages/qq/channels/base.py`` 保持同构，差异只有两点：
    1. :class:`WeChatMessage` 多一个 ``context_token`` —— iLink 回复必须回传
    2. ``send`` 自己查 ContextTokenStore 取 token，**没有 token 即返回
       ``no_context_token`` 错误**（iLink 不能主动推送，这是协议级限制，
       不是通道实现的缺陷）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable


class ChannelError(RuntimeError):
    """通道层统一异常基类。"""


class ChannelNotConnected(ChannelError):
    """通道尚未连接（未登录 / 无凭证）时抛出。"""


@dataclass
class WeChatMessage:
    """归一化后的入站微信消息，业务层只认这个，不依赖 iLink 的原始 dict。

    scope:         会话范围。iLink 目前只有 1:1，恒为 "user"；
                   保留字段以便将来支持群（届时会有 "group"）
    scope_id:      对端用户 id（微信号 / ilink user id）
    text:          消息文本。v1 只处理纯文本；非文本内容取不到时为空串
    sender_nickname: 发送者昵称（best-effort，可为空）
    context_token: iLink 回复必须回传的会话令牌；空串表示不可用
                   （此时无法回复，只能缓冲到 outbox）
    raw:           原始消息 dict 透传（排障 / 未来扩展），默认 None
    """

    scope: str
    scope_id: str
    text: str
    sender_nickname: str = ""
    context_token: str = ""
    raw: dict | None = None

    def target_type(self) -> str:
        """映射为 api_send 用的 target_type（v1 恒为 "user"）。"""
        return "user"


#: 业务层注册的入站消息回调签名
MessageHandler = Callable[[WeChatMessage], Awaitable[None]]


class WeChatChannel(ABC):
    """微信通道接口。所有微信接入方式都实现它。"""

    #: 通道标识，如 "ilink"
    name: str = "base"

    def __init__(self) -> None:
        self._handler: MessageHandler | None = None

    # ── 生命周期 ──

    @abstractmethod
    async def startup(self) -> None:
        """建立连接 / 启动接收循环。"""

    @abstractmethod
    async def shutdown(self) -> None:
        """断开连接 / 清理。"""

    # ── 入站消息 ──

    def on_message(self, handler: MessageHandler) -> None:
        """注册业务层入站消息回调（handle_wechat_message）。"""
        self._handler = handler

    async def _dispatch(self, msg: WeChatMessage) -> None:
        """通道内部把归一化消息交给业务层（未注册 handler 时静默丢弃）。"""
        if self._handler is not None:
            await self._handler(msg)

    # ── 出站 / 查询 ──

    @abstractmethod
    async def send(self, target_type: str, target_id: str | int, text: str) -> dict:
        """发送一条消息（wire 层，不含落盘）。

        返回 ``{"ok": True, "message_id": ...}`` 或
        ``{"ok": False, "error": {"code": ..., "message": ...}}``。

        典型错误码：
            ``no_context_token``  该用户从未发过消息（或 token 已失效），
                                  无法主动推送 —— 调用方应转 outbox 缓冲
            ``not_connected``     通道未登录
        """

    @abstractmethod
    async def recent_contacts(self) -> dict:
        """返回 ``{ok, contacts:[{peerUin, peerName, chatType}], source}``。

        iLink 无联系人列表 API，v1 从本地 history/inbox 的 user id 推导，
        因此 ``source`` 恒为 ``"local"``（调用方需知晓这不是权威列表）。
        """

    async def send_typing(self, target_id: str | int, status: int) -> dict:
        """发送「正在输入」状态（best-effort）。默认不支持，不抛异常。"""
        return {"ok": False, "error": {
            "code": "unsupported", "message": "当前通道不支持 typing 状态"}}

    # ── 状态 ──

    @abstractmethod
    async def is_connected(self) -> bool:
        """当前通道是否已连接（已登录且轮询正常）。"""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
