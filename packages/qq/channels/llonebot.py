"""LLOneBot（LLBot / 幸运莉莉娅）通道：OneBot 11 网关，正向 WS 默认 3002。

LLOneBot 是完整的 QQ 协议网关（GitHub: LLOneBot/LuckyLilliaBot），与 NapCat 同属
OneBot 11 实现，配置文件 ``data/config_<qq>.json`` 的 ``ob11.connect[]`` 段：
    - type "ws"（正向 WS，默认 port 3001）— NoneBot 作为客户端连接它；
    - type "ws-reverse"（反向 WS）— LLOneBot 连接 NoneBot 托管的 WS 服务端；
    - type "http"（默认 port 3000）— HTTP 服务端。
本通道默认使用 3002（而非 LLOneBot 默认 3001），以避免与同机运行的 NapCat（3001）
端口冲突；实际部署时按 LLOneBot 的 ``ob11.connect[type=ws].port`` 配置填写即可。

注意：LLOneBot 与 NapCat 都是协议网关，需要各自独立登录的 QQ 账号。同一账号无法
同时被两者登录，因此真实收发对接需为 LLOneBot 准备另一个 QQ 号（与运行中的 NapCat
账号 1234567890 区分）。本仓库仅完成通道抽象、配置化与初始化验证。
"""

from __future__ import annotations

from .base import ChannelConfig
from .onebot import OneBotChannel


class LLOneBotChannel(OneBotChannel):
    name = "llonebot"

    def __init__(
        self,
        config: ChannelConfig | None = None,
        *,
        bot_fallback=None,
    ) -> None:
        if config is None:
            config = ChannelConfig(name="llonebot", ws_urls=["ws://127.0.0.1:3002"])
        super().__init__(config, bot_fallback=bot_fallback)
