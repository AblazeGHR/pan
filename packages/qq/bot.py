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
    # 多通道解析：qq.channels 数组存在则逐项构建（多账号同收发），
    # 否则退回单通道 build_channel_spec（向后兼容）。
    specs = _channels.build_channel_specs(qq_cfg)

    # NoneBot OneBot v11 适配器按 ONEBOT_WS_URLS（JSON 数组）连接网关，原生支持
    # 多 bot：把全部通道的 ws_urls 合并注入，每个网关返回各自的 self_id，
    # 通道实例再按 config.bot_uin 认领属于自己的连接。
    all_urls: list[str] = []
    first_token: str | None = None
    for spec in specs:
        for u in spec["ws_urls"]:
            if u not in all_urls:
                all_urls.append(u)
        if first_token is None and spec["token"]:
            first_token = spec["token"]
    _inject_onebot_env(specs[0]["name"], all_urls, first_token)

    # Mixed driver: fastapi serves the QQ HTTP API (server_app) while websockets
    # provides the OneBot v11 WS *client* connection to the gateway. Without the
    # WS driver, OneBot V11 cannot receive/send messages (仅 fastapi 不支持 WS client).
    nonebot.init(driver="~fastapi+~websockets")
    driver = get_driver()
    driver.register_adapter(OneBotAdapter)

    # 构造全部通道并注册：第一个为默认通道（get_active_channel / plugin 复用），
    # 其余进注册表按 name / bot_uin 查找（plugin 的多账号回复路由）。
    for spec in specs:
        channel = _channels.create_channel(
            spec["name"], spec["ws_urls"], spec["token"], bot_uin=spec["bot_uin"]
        )
        _channels.set_active_channel(channel, name=channel.name)
        print(
            f"[QQ] 通道 '{channel.name}' 已注册"
            f"（bot_uin={spec['bot_uin'] or '未配置'}, ws={spec['ws_urls']}）"
        )
    if len(specs) > 1:
        # set_active_channel 每次都会把 _ACTIVE 指向新通道，这里拨回第一个作默认
        first = _channels.get_channel_by_name(specs[0]["name"])
        if first is not None:
            _channels.set_active_channel(first)

    nonebot.load_plugin("plugin")
    nonebot.run()


if __name__ == "__main__":
    main()
