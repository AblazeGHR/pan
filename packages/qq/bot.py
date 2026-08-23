"""Pan QQ Channel entry point — start NoneBot2 and load QQ plugin.

NapCat 不可达时自动降级：NoneBot OneBot 适配器对正向 WS 连接失败会每 3 秒重试
（进程不会退出），此处在此基础上补充清晰的降级/恢复日志与状态标记。
"""

import socket
from urllib.parse import urlparse

from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter
from nonebot.adapters.onebot.v11.config import Config as OneBotConfig
import nonebot
from nonebot import get_plugin_config

# Mixed driver: fastapi serves the QQ HTTP API (server_app) while websockets
# provides the OneBot v11 WS *client* connection to NapCat. Without the WS
# driver, OneBot V11 cannot receive/send messages (仅 fastapi 不支持 WS client).
nonebot.init(driver="~fastapi+~websockets")
driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)

_DEGRADED_MSG = "[QQ] NapCat 未连接，QQ 模块降级运行（进程保持存活，每 3s 自动重试连接）"
_DEGRADED_DISCONNECT_MSG = "[QQ] NapCat 连接断开，QQ 模块降级运行（继续每 3s 自动重试）"
_RECOVERED_MSG = "[QQ] NapCat 连接恢复，QQ 模块正常运行"
_napcat_connected = False


def _onebot_ws_urls() -> list[str]:
    """OneBot 正向 WS 目标地址（ONEBOT_WS_URLS / .env 配置，如 ws://127.0.0.1:3001）。"""
    cfg = get_plugin_config(OneBotConfig)
    return [str(u) for u in cfg.onebot_ws_urls]


def _tcp_reachable(url: str, timeout: float = 1.0) -> bool:
    """Cheap TCP connect test for a ws(s):// URL — True if host:port accepts a connection."""
    try:
        p = urlparse(url)
        port = p.port or (443 if p.scheme == "wss" else 80)
        with socket.create_connection((p.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def _mark_connected() -> None:
    global _napcat_connected
    if not _napcat_connected:
        _napcat_connected = True
        print(_RECOVERED_MSG)


def _mark_disconnected() -> None:
    global _napcat_connected
    if _napcat_connected:
        _napcat_connected = False
        print(_DEGRADED_DISCONNECT_MSG)


@driver.on_startup
async def _napcat_startup_check() -> None:
    """启动时预检 NapCat 可达性；不可达则打降级日志（适配器会持续每 3s 重试）。

    在 on_startup 阶段（早于适配器 on_ready 的首次 WS 连接）执行，先于
    适配器的 ERROR 日志给出清晰的降级标记。未配置正向 WS 地址时无 NapCat
    依赖，跳过。
    """
    urls = _onebot_ws_urls()
    if not urls:
        return
    if not any(_tcp_reachable(u) for u in urls):
        print(_DEGRADED_MSG)


@driver.on_bot_connect
async def _on_bot_connect(bot) -> None:
    _mark_connected()


@driver.on_bot_disconnect
async def _on_bot_disconnect(bot) -> None:
    _mark_disconnected()


nonebot.load_plugin("plugin")
nonebot.run()
