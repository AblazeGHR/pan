"""Pan WeChat bot 入口 —— 被 main.py 的 _spawn_wechat_bot() 拉起的子进程。

启动链::

    python main.py (wechat.enabled=true)
      → subprocess: <python> packages/wechat/bot.py   cwd=packages/wechat
          → ILinkClient(load_spec()) + BotSession（二维码登录，打印到终端）
          → ILinkChannel + WeChatPlugin（本进程内互持引用，不走 HTTP 回环）
          → uvicorn 起 packages/wechat/server.py 的 app（默认 127.0.0.1:8081）
          → lifespan startup：登录 + 启动长轮询；shutdown：停轮询关连接

与 QQ bot.py 的差异：无 nonebot、无独立解释器（只依赖项目主环境已有的
httpx/fastapi/uvicorn/mcp），登录二维码直接打印到本进程终端（同时落盘
data/wechat/login_qrcode.txt，终端不可见时也能取用）。

环境变量（均可被 config.json 的 wechat 段覆盖，环境变量优先）：
    PAN_WECHAT_HOST / PAN_WECHAT_PORT   HTTP 监听地址（默认 127.0.0.1:8081）
    PAN_WECHAT_MODE                     mirror（默认）/ selective
    PAN_WECHAT_QRCODE_TIMEOUT           二维码登录总时长上限（默认 300s）
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# main.py 拉起时已注入 PYTHONPATH=项目根；直接 `python bot.py` 时兜底补上，
# 保证 `from packages.wechat import ...` 两种场景都能工作。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn  # noqa: E402

from packages.wechat import server as wechat_server  # noqa: E402
from packages.wechat.auth import BotSession  # noqa: E402
from packages.wechat.ilink import ILinkClient  # noqa: E402
from packages.wechat.ilink_channel import ILinkChannel  # noqa: E402
from packages.wechat.ilink_spec import load_spec  # noqa: E402
from packages.wechat.plugin import WeChatPlugin, load_config  # noqa: E402
from packages.wechat.store import login_qrcode_path  # noqa: E402

DEFAULT_PORT = 8081


def _show_qrcode(url: str, _key: str) -> None:
    """把登录二维码递给人：打印到终端（落盘由 BotSession 统一负责）。"""
    print("\n" + "=" * 62)
    print("[WeChat] 请用微信扫描二维码登录 iLink bot")
    print(f"[WeChat] 扫码 URL（已存 {login_qrcode_path()}）:")
    print(f"    {url}")
    print("=" * 62 + "\n", flush=True)


def _resolve_listen(wechat_cfg: dict) -> tuple[str, int]:
    """监听地址：环境变量 > config.json wechat 段 > 默认 127.0.0.1:8081。"""
    host = (
        os.environ.get("PAN_WECHAT_HOST")
        or str(wechat_cfg.get("host") or "127.0.0.1")
    )
    raw_port = (
        os.environ.get("PAN_WECHAT_PORT")
        or wechat_cfg.get("port")
        or DEFAULT_PORT
    )
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return host, port


def main() -> None:
    """组装 channel + plugin + server 并用 uvicorn 跑起来（阻塞至退出）。

    构造全部是同步操作，不需要事件循环；uvicorn.run 自建 loop 并在 lifespan
    里调 on_startup（登录 + 启动长轮询）/ on_shutdown（停轮询 + 关连接），
    信号处理（SIGINT/SIGTERM → 优雅关闭）由 uvicorn 默认接管。
    """
    wechat_cfg = load_config().get("wechat") or {}
    if not isinstance(wechat_cfg, dict):
        wechat_cfg = {}
    if not wechat_cfg.get("enabled", True):
        # main.py 已按 wechat.enabled 决定是否拉起；独立手动运行时提示即可，
        # 不拦截（enabled=false 但手动跑 bot.py 应当被允许，调试用）。
        print("[WeChat] 注意: config.json wechat.enabled=false（手动调试模式）")

    client = ILinkClient(load_spec(wechat_cfg))
    try:
        qrcode_timeout = float(
            os.environ.get("PAN_WECHAT_QRCODE_TIMEOUT")
            or wechat_cfg.get("qrcode_timeout")
            or 300.0
        )
    except (TypeError, ValueError):
        qrcode_timeout = 300.0
    session = BotSession(client, on_qrcode=_show_qrcode,
                         qrcode_timeout=qrcode_timeout)
    channel = ILinkChannel(client, session)
    plugin = WeChatPlugin(channel)
    channel.on_message(plugin.handle_wechat_message)

    async def _on_startup() -> None:
        """uvicorn 启动即返回（8081 立即监听），登录在后台进行。

        登录（二维码/复用凭证）是阻塞的人为步骤，不能卡住 lifespan——否则
        8081 在扫码前一直不监听，且登录偶发抖动会整进程退出。改为后台跑
        channel.startup()，失败自动重试，登录成功后才起长轮询收消息。
        """
        asyncio.create_task(_login_in_background(channel))

    async def _login_in_background(ch: "ILinkChannel") -> None:
        """后台登录：成功即启动长轮询；失败每 10s 重试，永不退出本任务。"""
        while True:
            try:
                await ch.startup()
                print("[WeChat] 登录成功，开始接收消息")
                return
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                print(f"[WeChat] 登录失败（10s 后重试）: {exc!r}")
                await asyncio.sleep(10)

    async def _on_shutdown() -> None:
        await channel.shutdown()
        await plugin.aclose()

    app = wechat_server.create_app(
        plugin, on_startup=_on_startup, on_shutdown=_on_shutdown
    )
    host, port = _resolve_listen(wechat_cfg)
    print(f"[WeChat] bridge starting: http://{host}:{port} "
          f"(mode={plugin.mode})")
    try:
        uvicorn.run(app, host=host, port=port, log_level="info",
                    access_log=False)
    except KeyboardInterrupt:
        print("[WeChat] bridge stopped")


if __name__ == "__main__":
    main()
