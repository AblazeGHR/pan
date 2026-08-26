"""Pan QQ Channel entry point — 按配置选择并启动 QQ 通道（NapCat / LLOneBot）。

通道化之前这里硬编码 NapCat 正向 WS 连接 + 3 秒重试降级。现在：
    - 读 config.json 的 qq.channel（缺省 "napcat"，向后兼容）
    - 读各通道连接参数（ws_urls / token），环境变量 ONEBOT_WS_URLS /
      ONEBOT_ACCESS_TOKEN 优先，其次 config 的 qq.<channel>.ws_urls / token
    - 把 ws_urls 注入 ONEBOT_WS_URLS 环境变量，让 NoneBot OneBot v11 适配器连到
      对应网关（NapCat / LLOneBot 都是 OneBot 11 网关，wire 层一致）
    - 构造并 stash 通道实例，再 load_plugin("plugin")，plugin 复用同一实例
    - 网关不可达时由通道层打降级日志，适配器每 3s 重试，进程保持存活

通道选择/连接的具体差异（端口、token、降级文案）全部封装在
packages/qq/channels/* 中，本文件只做「读配置 → 注入 env → 建通道 → 启动」。
"""

import json
import os
from pathlib import Path

from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter
import nonebot
from nonebot import get_driver

from packages.qq import channels as _channels


def _load_qq_config() -> dict:
    """Read config.json at the project root; empty dict on failure."""
    try:
        path = Path(__file__).resolve().parent.parent.parent / "config.json"
        return json.loads(path.read_text(encoding="utf-8")).get("qq") or {}
    except Exception:
        return {}


def _inject_onebot_env(name: str, ws_urls: list[str], token: str | None) -> None:
    """把通道的连接参数注入 NoneBot OneBot 适配器读取的环境变量。

    仅当用户未显式设置时才覆盖，尊重手动 .env 配置。
    """
    if os.getenv("ONEBOT_WS_URLS") is None:
        os.environ["ONEBOT_WS_URLS"] = json.dumps(ws_urls)
    if token and os.getenv("ONEBOT_ACCESS_TOKEN") is None:
        os.environ["ONEBOT_ACCESS_TOKEN"] = token
    print(f"[QQ] 启动通道 '{name}'，OneBot WS: {ws_urls}")


def main() -> None:
    qq_cfg = _load_qq_config()
    name, ws_urls, token = _channels.build_channel_spec(qq_cfg)
    _inject_onebot_env(name, ws_urls, token)

    # Mixed driver: fastapi serves the QQ HTTP API (server_app) while websockets
    # provides the OneBot v11 WS *client* connection to the gateway. Without the
    # WS driver, OneBot V11 cannot receive/send messages (仅 fastapi 不支持 WS client).
    nonebot.init(driver="~fastapi+~websockets")
    driver = get_driver()
    driver.register_adapter(OneBotAdapter)

    # 构造通道并 stash：plugin 复用同一实例（避免重复创建 / 配置不一致）
    channel = _channels.create_channel(name, ws_urls, token)
    _channels.set_active_channel(channel)

    nonebot.load_plugin("plugin")
    nonebot.run()


if __name__ == "__main__":
    main()
