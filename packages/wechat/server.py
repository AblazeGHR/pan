"""Pan WeChat HTTP 服务 —— 8081 端口上的 /api/wechat/* 数据后端。

在整体链路中的位置::

    Pan Core（packages/web/server.py）--代理 GET /api/wechat/{contacts,channels}--> **本服务**
    packages/wechat/mcp.py（pan-wechat MCP）--send/history/inbox/status--> **本服务**
    bot.py --uvicorn 启动本服务，lifespan 里拉起/关闭通道--> **本服务**

与 QQ 插件挂在 NoneBot driver server_app 上不同，微信走纯 HTTP，本文件独立
建 FastAPI app 并由 bot.py 用 uvicorn 拉起（端口默认 8081，PAN_WECHAT_API_URL
可让 Core/MCP 指向别处）。端点集与 QQ 平行（少 send_file —— iLink v1 只支持
纯文本），多一个 /api/wechat/status 供 wechat_status MCP 工具用。

端点一览：
    POST   /api/wechat/send            {target_type, target_id, text}
    GET    /api/wechat/history         ?target_id=&limit=
    GET    /api/wechat/recent_contacts
    GET    /api/wechat/channels
    GET    /api/wechat/inbox           ?target_id=&limit=&consume=
    DELETE /api/wechat/inbox           ?target_id=
    GET    /api/wechat/status
    POST   /api/wechat/typing          {target_id, status}

注意：subscribe / unsubscribe / notify 三个端点在 **Pan Core 侧**
（packages/web/server.py /api/wechat/subscribe|unsubscribe|notify，微信订阅
落盘在 Pan session 上，不在插件），本服务不重复实现，也无需代理。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Awaitable, Callable

from fastapi import FastAPI

from .plugin import WeChatPlugin

__all__ = ["create_app"]


def create_app(
    plugin: WeChatPlugin,
    *,
    on_startup: Callable[[], Awaitable[None]] | None = None,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    """构建 FastAPI app：所有 /api/wechat/* 路由转发到 plugin 的 api_* 方法。

    on_startup / on_shutdown 是 bot.py 传入的通道生命周期钩子（登录 +
    启动长轮询 / 停轮询 + 关连接），随 uvicorn lifespan 触发；单测可不传。
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if on_startup is not None:
            await on_startup()
        try:
            yield
        finally:
            if on_shutdown is not None:
                await on_shutdown()

    app = FastAPI(title="Pan WeChat Bridge", lifespan=lifespan)

    @app.post("/api/wechat/send")
    async def _route_send(body: dict):
        """发送一条微信消息。body: {target_type: "user", target_id, text}。"""
        return await plugin.api_send(
            body.get("target_type", ""),
            body.get("target_id"),
            body.get("text", ""),
        )

    @app.get("/api/wechat/history")
    async def _route_history(target_id: str, limit: int = 30):
        return await plugin.api_history(target_id, limit)

    @app.get("/api/wechat/recent_contacts")
    async def _route_recent_contacts():
        """近期联系人（Pan Core /api/wechat/contacts 代理到此）。"""
        return await plugin.api_recent_contacts()

    @app.get("/api/wechat/channels")
    async def _route_channels():
        """已注册通道列表（Pan Core /api/wechat/channels 代理到此）。"""
        return await plugin.api_channels()

    @app.get("/api/wechat/inbox")
    async def _route_inbox(target_id: str, limit: int = 30, consume: int = 0):
        return await plugin.api_inbox(target_id, limit, bool(consume))

    @app.delete("/api/wechat/inbox")
    async def _route_inbox_clear(target_id: str):
        return await plugin.api_inbox_clear(target_id)

    @app.get("/api/wechat/status")
    async def _route_status():
        """桥状态：登录态 / 模式 / outbox 积压（MCP wechat_status 后端）。"""
        return await plugin.api_status()

    @app.post("/api/wechat/typing")
    async def _route_typing(body: dict):
        """「正在输入」状态（best-effort）。body: {target_id, status: 1|2}。"""
        status = body.get("status", 1)
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = 1
        return await plugin.api_typing(body.get("target_id"), status)

    return app
