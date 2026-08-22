"""Pan QQ MCP Server — 独立 MCP server，经 QQ bot HTTP API 精细驱动 QQ。

与 packages/mcp/server.py 平行：本 server 只暴露 QQ 能力（发送消息/读对话/
列联系人），由 meta-agent 在需要 QQ 时按需挂载（manifest 的 mcp_servers 加
``pan-qq``）。它不直接连 NapCat，而是调用 NoneBot（packages/qq/plugin.py）
在 driver server_app 上挂的 HTTP API，因此与 bot 进程解耦。

Usage:
    python -m packages.qq.mcp                 # stdio (default)
    python -m packages.qq.mcp --transport sse --port 9741   # SSE transport

Tools exposed:
    - qq_send_message: 向 QQ 私聊/群聊发送消息（text 支持 OneBot CQ 码）
    - qq_read_conversation: 读取某 QQ 会话的落盘对话记录（本地持久化）
    - qq_list_contacts: 列出最近的 QQ 联系人/群（NapCat get_recent_contact，best-effort）
    - qq_read_inbox: 读取某 QQ 会话的待处理消息队列（selective 模式专用）
    - qq_bind: 绑定当前 Pan session 到 QQ 会话，订阅其 inbox 更新提醒
    - qq_unbind: 解绑，停止 inbox 更新提醒

Environment variables:
    PAN_QQ_API_URL: QQ bot HTTP API base URL (default: http://127.0.0.1:8080)
        8080 是 NoneBot fastapi driver 的默认端口；可在 packages/qq/bot.py
        运行环境里用 HOST/PORT 覆盖，届时这里也要同步改。
    PAN_API_URL: Pan Core HTTP API base URL (default: http://127.0.0.1:8768)
        qq_bind/qq_unbind 经它读写 Pan session 的 qq_subscriptions。
"""

from __future__ import annotations

import argparse
import os

import httpx
from mcp.server.fastmcp import FastMCP

_qq_api_url = os.environ.get("PAN_QQ_API_URL", "http://127.0.0.1:8080").rstrip("/")
_pan_api_url = os.environ.get("PAN_API_URL", "http://127.0.0.1:8768").rstrip("/")

mcp = FastMCP("QQ")


async def _api(method: str, path: str, body: dict | None = None,
               timeout: float = 30.0, base_url: str | None = None) -> dict:
    """Call the target HTTP API and return parsed JSON.

    base_url 默认用 QQ bot（_qq_api_url）；绑定类工具传入 Pan Core
    （_pan_api_url）。HTTP 错误时尽力透传后端返回的 JSON（可能带 error 字段），
    连接失败返回 {ok: false, error: {code: connection_error, ...}}，与
    packages/mcp/server.py 的 _api 错误约定保持一致。
    """
    url = f"{(base_url or _qq_api_url)}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                r = await client.get(url, params=body)
            else:
                r = await client.post(url, json=body)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        try:
            return e.response.json()
        except Exception:
            return {"ok": False, "error": {
                "code": e.response.status_code,
                "message": e.response.text[:500]}}
    except httpx.HTTPError as e:
        return {"ok": False, "error": {
            "code": "connection_error",
            "message": f"{type(e).__name__}: {e}"}}
    except Exception as e:
        return {"ok": False, "error": {
            "code": "unknown",
            "message": f"{type(e).__name__}: {e}"}}


@mcp.tool()
async def qq_send_message(target_type: str, target_id: str | int, text: str) -> dict:
    """向指定 QQ 会话发送一条消息（私聊或群聊）。

    Args:
        target_type: 目标类型，"private"=私聊 / "group"=群聊
        target_id: 目标 QQ 号（私聊）或群号（群聊）
        text: 消息内容，支持 OneBot CQ 码（如 "[CQ:face,id=1]"、
            "[CQ:image,file=url]"）与普通文本

    调用链：本工具 → POST {PAN_QQ_API_URL}/api/qq/send → NoneBot（packages/qq/
    plugin.py）经 bot.call_api("send_private_msg"/"send_group_msg") → NapCat 发送。
    返回 {"ok": true, "message_id": ...} 或错误。发送前建议先 qq_read_conversation
    读上下文（避免重复打扰），发送后可在对话记录中看到本条 assistant 消息。
    """
    if target_type not in ("private", "group"):
        return {"ok": False, "error": {
            "code": "invalid_target_type",
            "message": "target_type 必须是 'private' 或 'group'"}}
    if not text:
        return {"ok": False, "error": {
            "code": "empty_text", "message": "text 不能为空"}}
    return await _api("POST", "/api/qq/send", {
        "target_type": target_type,
        "target_id": str(target_id),
        "text": text,
    })


@mcp.tool()
async def qq_read_conversation(target_id: str | int, limit: int = 30) -> dict:
    """读取某 QQ 会话的对话记录（本地落盘，非 NapCat 缓存）。

    Args:
        target_id: 会话标识 —— 私聊用 QQ 号，群聊用群号（与发送时的 target_id 一致）
        limit: 最多返回多少条（最新在前，默认 30，最大 500）

    调用链：本工具 → GET {PAN_QQ_API_URL}/api/qq/history?target_id=&limit= →
    packages/qq/plugin.py 读 data/qq_history/<target_id>.json。记录由 plugin 在
    handle_message 收到消息时与 bot 回复时按 target_id 落盘，因此包含 user 与
    assistant 两侧（NapCat 历史 API 只能取缓存近期消息，可靠历史以此为准）。
    返回 {"target_id": ..., "messages": [{role, text, time}, ...]}。
    """
    if limit <= 0:
        limit = 30
    return await _api("GET", "/api/qq/history", {"target_id": str(target_id), "limit": min(limit, 500)})


@mcp.tool()
async def qq_list_contacts() -> dict:
    """列出最近的 QQ 联系人/群（NapCat get_recent_contact，best-effort）。

    调用链：本工具 → GET {PAN_QQ_API_URL}/api/qq/recent_contacts → NoneBot 经
    bot.call_api("get_recent_contact") → NapCat。仅返回缓存中的近期会话，用于
    发现"该找谁发消息"；NapCat 版本不支持该扩展 API 时返回 ok:false，不影响
    其它工具。完整历史请用 qq_read_conversation 读落盘记录。
    """
    return await _api("GET", "/api/qq/recent_contacts")


@mcp.tool()
async def qq_read_inbox(target_id: str | int, limit: int = 30, consume: bool = False) -> dict:
    """读取某 QQ 会话的待处理消息（inbox），selective 模式（PAN_QQ_MODE=selective）
    下由编排者（meta-agent/worker）消费。

    selective 模式：QQ 收到的消息**不自动回复**，而是进入待处理队列（inbox），
    等待编排者决定。典型编排流程：
      1. 用本工具读某 target 的 inbox（consume=False 先看，不删除）；
      2. 结合 qq_read_conversation 读该会话历史，决策：忽略 / 回复 / 路由到别的
         流程；
      3. 需要回复时用 qq_send_message(target_type, target_id, text) 发送。
    决策完成后应带 consume=True 再读一次（消费即删），避免同一批消息被重复处理。

    Args:
        target_id: 会话标识 —— 私聊用 QQ 号，群聊用群号（与 qq_send_message 一致）
        limit: 最多读取多少条（默认 30，最大 500；旧消息在前，FIFO）
        consume: True 时读取后从 inbox 队列删除（消费即删）

    调用链：本工具 → GET {PAN_QQ_API_URL}/api/qq/inbox?target_id=&limit=&consume= →
    packages/qq/plugin.py 读 data/qq_inbox/<target_id>.json。
    返回 {"target_id": ..., "messages": [{id, text, time}, ...]}。
    """
    if limit <= 0:
        limit = 30
    return await _api("GET", "/api/qq/inbox", {
        "target_id": str(target_id),
        "limit": min(limit, 500),
        "consume": 1 if consume else 0,
    })


@mcp.tool()
async def qq_bind(target_type: str, target_id: str | int) -> dict:
    """绑定当前 Pan session 到某 QQ 会话，订阅其 inbox 更新提醒。

    绑定后，该 QQ 会话在 selective 模式下每收到新消息，本 session 的 queue_pending
    都会收到一条 `@@@@by qq : <会话标识> | <昵称>` 提醒（含消息 summary），并唤醒
    本 session 的 worker。镜像 report_subscribe 的订阅制，解绑用 qq_unbind。

    Args:
        target_type: "user"=私聊（对应 qq_send_message 的 private）/"group"=群聊
        target_id: 目标 QQ 号（私聊）或群号（群聊）

    仅 Pan 内 session 可用（需 PAN_AGENT_SESSION_ID 环境变量）。
    调用链：本工具 → POST {PAN_API_URL}/api/qq/subscribe → Pan Core 在 session
    落盘 qq_subscriptions。
    """
    manager_id = os.environ.get("PAN_AGENT_SESSION_ID")
    if not manager_id:
        return {"ok": False, "error": {
            "code": "missing_identity",
            "message": "PAN_AGENT_SESSION_ID not set — qq_bind only works inside a Pan-managed session"}}
    if target_type not in ("user", "group"):
        return {"ok": False, "error": {
            "code": "invalid_target_type",
            "message": "target_type 必须是 'user'（私聊）或 'group'（群聊）"}}
    return await _api("POST", "/api/qq/subscribe", {
        "sessionId": manager_id,
        "target_type": target_type,
        "target_id": str(target_id),
    }, base_url=_pan_api_url)


@mcp.tool()
async def qq_unbind(target_type: str, target_id: str | int) -> dict:
    """解绑当前 Pan session 与某 QQ 会话的绑定，停止 inbox 更新提醒。

    Args:
        target_type: "user"=私聊 / "group"=群聊
        target_id: 目标 QQ 号（私聊）或群号（群聊）

    仅 Pan 内 session 可用（需 PAN_AGENT_SESSION_ID 环境变量）。
    调用链：本工具 → POST {PAN_API_URL}/api/qq/unsubscribe → Pan Core 移除
    session 落盘的 qq_subscriptions。
    """
    manager_id = os.environ.get("PAN_AGENT_SESSION_ID")
    if not manager_id:
        return {"ok": False, "error": {
            "code": "missing_identity",
            "message": "PAN_AGENT_SESSION_ID not set — qq_unbind only works inside a Pan-managed session"}}
    if target_type not in ("user", "group"):
        return {"ok": False, "error": {
            "code": "invalid_target_type",
            "message": "target_type 必须是 'user'（私聊）或 'group'（群聊）"}}
    return await _api("POST", "/api/qq/unsubscribe", {
        "sessionId": manager_id,
        "target_type": target_type,
        "target_id": str(target_id),
    }, base_url=_pan_api_url)


def main():
    global _qq_api_url  # module-level override; __main__ attr would be a no-op when imported
    parser = argparse.ArgumentParser(description="Pan QQ MCP Server")
    parser.add_argument("--transport", default="stdio",
                        choices=["stdio", "sse", "streamable-http"])
    parser.add_argument("--port", type=int, default=9741,
                        help="Port for SSE/streamable-http transport (default: 9741)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host for SSE/streamable-http transport")
    parser.add_argument("--qq-api-url", default=_qq_api_url,
                        help=f"QQ bot HTTP API base URL (default: {_qq_api_url})")
    args = parser.parse_args()

    _qq_api_url = args.qq_api_url.rstrip("/")

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
