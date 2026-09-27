"""SnowLuma OneBot v11 forward WebSocket channel."""

from __future__ import annotations

from .base import ChannelConfig
from .onebot import OneBotChannel


class SnowLumaChannel(OneBotChannel):
    name = "snowluma"

    def __init__(self, config: ChannelConfig | None = None, *, bot_fallback=None) -> None:
        if config is None:
            config = ChannelConfig(name="snowluma", ws_urls=["ws://127.0.0.1:3003"])
        super().__init__(config, bot_fallback=bot_fallback)
