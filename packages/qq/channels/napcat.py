"""NapCat 通道：OneBot 11 网关，正向 WS 默认 ws://127.0.0.1:3001。

NapCat 是当前生产默认通道（运行中的 8080 QQ bot 即连此）。本类仅提供标识与缺省
连接地址；wire 层全部复用 OneBotChannel。配置来自 config.json 的 qq.napcat 段或
环境变量 ONEBOT_WS_URLS / ONEBOT_ACCESS_TOKEN（由 bot.py 注入）。
"""

from __future__ import annotations

from .base import ChannelConfig
from .onebot import OneBotChannel


class NapCatChannel(OneBotChannel):
    name = "napcat"

    def __init__(
        self,
        config: ChannelConfig | None = None,
        *,
        bot_fallback=None,
    ) -> None:
        if config is None:
            config = ChannelConfig(name="napcat", ws_urls=["ws://127.0.0.1:3001"])
        super().__init__(config, bot_fallback=bot_fallback)
