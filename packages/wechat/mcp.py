"""Pan WeChat MCP Server — 独立 MCP server，经微信插件 HTTP API 精细驱动微信。

与 packages/qq/mcp.py 平行：本 server 只暴露微信能力（发送消息/读对话/
列联系人/读 inbox/订阅提醒/查状态），由 meta-agent 在需要微信时按需挂载
（manifest 的 mcp_servers 已含 ``pan-wechat``）。它不直连 iLink，而是调用
微信插件（packages/wechat/bot.py 拉起的 server.py，默认 127.0.0.1:8081），
因此与 bot 进程解耦；bot 未启动时各工具返回 connection_error，不影响其它工具。

Usage:
    python -m packages.wechat.mcp                  # stdio (default)
    python -m packages.wechat.mcp --transport sse --port 9742   # SSE transport

Tools exposed:
    - wechat_send_message: 向微信用户发送文本（无 context_token 时插件自动
      缓冲到 outbox，待对方下次发消息后补发 —— iLink 协议不能主动推送）
    - wechat_read_conversation: 读取某微信会话的落盘对话记录
    - wechat_read_inbox: 读取某微信会话的待处理消息队列（selective 模式专用）
    - wechat_list_contacts: 列出近期微信联系人（本地落盘推导，best-effort）
    - wechat_bind: 绑定当前 Pan session 到某微信会话，订阅 inbox 更新提醒
    - wechat_unbind: 解绑，停止 inbox 更新提醒
    - wechat_status: 查询桥状态（登录态 / 模式 / outbox 积压）

编排流程（selective 模式）：wechat_read_inbox（先看不删）→ wechat_read_conversation
读上下文 → 决策 → wechat_send_message 回复 → 再以 consume=True 读一次清空队列。

Environment variables:
    PAN_WECHAT_API_URL: 微信插件 HTTP API base URL（默认 http://127.0.0.1:8081）
    PAN_API_URL: Pan Core HTTP API base URL（默认 http://127.0.0.1:8768）
        wechat_bind / wechat_unbind 经它读写 Pan session 的 wechat_subscriptions。
"""

from __future__ import annotations

import argparse
import os

import httpx
from mcp.server.fastmcp import FastMCP

_wechat_api_url = os.environ.get(
    "PAN_WECHAT_API_URL", "http://127.0.0.1:8081"
).rstrip("/")
_pan_api_url = os.environ.get("PAN_API_URL", "http://127.0.0.1:8768").rstrip("/")

mcp = FastMCP("WeChat")


async def _api(method: str, path: str, body: dict | None = None,
               timeout: float = 30.0, base_url: str | None = None) -> dict:
    """Call the target HTTP API and return parsed JSON.

    base_url 默认用微信插件（_wechat_api_url）；绑定类工具传入 Pan Core
    （_pan_api_url）。错误约定与 packages/qq/mcp.py / packages/mcp/server.py
    一致：HTTP 非 2xx 尽力透传后端 JSON，连接失败归为 connection_error。
    """
    url = f"{(base_url or _wechat_api_url)}{path}"
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
async def wechat_send_message(target_id: str | int, text: str) -> dict:
    """向指定微信用户发送一条文本消息。

    【必填参数·参数名以此为准】
      - target_id: 微信用户 id（ilink user id，联系人列表里的 peerUin）
      - text: 消息内容。**参数名是 text，不是 message**；请勿用 camelCase（targetId）

    Args:
        target_id: 目标微信用户 id
        text: 消息内容（v1 仅支持纯文本）

    调用链：本工具 → POST {PAN_WECHAT_API_URL}/api/wechat/send → 微信插件
    按该用户最近一次上行消息的 context_token 回发（iLink 协议不能主动推送）。
    返回 {"ok": true} 或错误。注意 ``no_context_token`` 错误表示该用户从未
    发过消息（或 token 失效）——插件已把消息缓冲到 outbox，等对方下次发消息
    会自动补发，无需重试；发送前建议先 wechat_read_conversation 读上下文。
    """
    if not str(target_id or "").strip():
        return {"ok": False, "error": {
            "code": "invalid_target_id", "message": "target_id 不能为空"}}
    if not text or not str(text).strip():
        return {"ok": False, "error": {
            "code": "empty_text", "message": "text 不能为空"}}
    return await _api("POST", "/api/wechat/send", {
        "target_type": "user",
        "target_id": str(target_id),
        "text": text,
    })


@mcp.tool()
async def wechat_read_conversation(
    target_id: str | int, limit: int = 30
) -> dict:
    """读取某微信会话的对话记录（本地落盘，user/assistant 双侧）。

    【必填参数·参数名以此为准】
      - target_id: 微信用户 id，参数名是 target_id（不是 targetId）
      - limit: 可选，最多返回多少条（默认 30）

    调用链：本工具 → GET {PAN_WECHAT_API_URL}/api/wechat/history?target_id=&limit=
    → 微信插件读 data/wechat_history/<target_id>.json。记录由插件在收到消息时
    与发送成功后落盘。返回 {"target_id": ..., "messages": [{role, text, time}, ...]}
    （最新在后）。
    """
    if limit <= 0:
        limit = 30
    return await _api("GET", "/api/wechat/history", {
        "target_id": str(target_id), "limit": min(limit, 500),
    })


@mcp.tool()
async def wechat_read_inbox(
    target_id: str | int, limit: int = 30, consume: bool = False
) -> dict:
    """读取某微信会话的待处理消息（inbox），selective 模式下由编排者消费。

    【必填参数·参数名以此为准】
      - target_id: 微信用户 id，参数名是 target_id（不是 targetId）
      - limit: 可选，最多读取多少条（默认 30）
      - consume: 可选，True 时读取后删除（消费即删）

    selective 模式：微信收到的消息**不自动回复**，而是进入待处理队列（inbox）
    并触发订阅了该会话的 Pan session 收到 ``@@@@by wechat`` 提醒。典型流程：
      1. 用本工具读 inbox（consume=False 先看，不删除）；
      2. 结合 wechat_read_conversation 读历史，决策：忽略 / 回复 / 路由；
      3. 需要回复时 wechat_send_message(target_id, text)；
      4. 决策完成后再以 consume=True 读一次（消费即删），避免重复处理。

    调用链：本工具 → GET {PAN_WECHAT_API_URL}/api/wechat/inbox?target_id=&limit=&consume=
    → 微信插件读 data/wechat_inbox/<target_id>.json。
    返回 {"target_id": ..., "messages": [{id, target_id, scope, role, text, time}, ...]}。
    """
    if limit <= 0:
        limit = 30
    return await _api("GET", "/api/wechat/inbox", {
        "target_id": str(target_id),
        "limit": min(limit, 500),
        "consume": 1 if consume else 0,
    })


@mcp.tool()
async def wechat_list_contacts() -> dict:
    """列出可联系的微信会话（近期联系人，本地落盘推导）。

    调用链：本工具 → GET {PAN_WECHAT_API_URL}/api/wechat/recent_contacts →
    微信插件从 data/wechat_history、data/wechat_inbox 与 context_token 缓存
    推导（iLink 无联系人列表 API）。每项 {peerUin, peerName, chatType:1}；
    ``source: "local"`` 提示这不是权威列表 —— 只包含「跟 bot 有过来往」的用户，
    用于发现"该找谁发消息"。微信插件不可达时返回 ok:false，不影响其它工具。
    """
    return await _api("GET", "/api/wechat/recent_contacts")


@mcp.tool()
async def wechat_bind(target_id: str | int) -> dict:
    """绑定当前 Pan session 到某微信会话，订阅其 inbox 更新提醒。

    【必填参数·参数名以此为准】
      - target_id: 微信用户 id（ilink user id）

    Args:
        target_id: 目标微信用户 id（iLink v1 只有 1:1 会话，无需 target_type）

    绑定后，该微信会话在 selective 模式下每收到新消息，本 session 的
    queue_pending 都会收到一条 ``@@@@by wechat`` 提醒（含消息 summary 与
    canReply 标记）并唤醒本 session 的 worker。解绑用 wechat_unbind。

    仅 Pan 内 session 可用（需 PAN_AGENT_SESSION_ID 环境变量）。
    调用链：本工具 → POST {PAN_API_URL}/api/wechat/subscribe → Pan Core 在
    session 落盘 wechat_subscriptions。
    """
    manager_id = os.environ.get("PAN_AGENT_SESSION_ID")
    if not manager_id:
        return {"ok": False, "error": {
            "code": "missing_identity",
            "message": "PAN_AGENT_SESSION_ID not set — wechat_bind only works inside a Pan-managed session"}}
    if not str(target_id or "").strip():
        return {"ok": False, "error": {
            "code": "invalid_target_id", "message": "target_id 不能为空"}}
    return await _api("POST", "/api/wechat/subscribe", {
        "sessionId": manager_id,
        "target_type": "user",
        "target_id": str(target_id),
    }, base_url=_pan_api_url)


@mcp.tool()
async def wechat_unbind(target_id: str | int) -> dict:
    """解绑当前 Pan session 与某微信会话的绑定，停止 inbox 更新提醒。

    【必填参数·参数名以此为准】
      - target_id: 微信用户 id

    仅 Pan 内 session 可用（需 PAN_AGENT_SESSION_ID 环境变量）。
    调用链：本工具 → POST {PAN_API_URL}/api/wechat/unsubscribe → Pan Core
    移除 session 落盘的 wechat_subscriptions。
    """
    manager_id = os.environ.get("PAN_AGENT_SESSION_ID")
    if not manager_id:
        return {"ok": False, "error": {
            "code": "missing_identity",
            "message": "PAN_AGENT_SESSION_ID not set — wechat_unbind only works inside a Pan-managed session"}}
    if not str(target_id or "").strip():
        return {"ok": False, "error": {
            "code": "invalid_target_id", "message": "target_id 不能为空"}}
    return await _api("POST", "/api/wechat/unsubscribe", {
        "sessionId": manager_id,
        "target_type": "user",
        "target_id": str(target_id),
    }, base_url=_pan_api_url)


@mcp.tool()
async def wechat_status() -> dict:
    """查询微信桥状态：是否登录、是否在收消息、模式、outbox 积压。

    调用链：本工具 → GET {PAN_WECHAT_API_URL}/api/wechat/status → 微信插件。
    返回 {"ok": true, "channel": "ilink", "logged_in": bool, "connected": bool,
    "mode": "mirror"|"selective", "outbox": [{target_id, pending}, ...]}。
    ``no_context_token`` 缓冲（outbox）里的消息会在相应用户下次发消息时自动
    补发；积压只增不减且用户长期不回消息时考虑改用其它渠道触达。
    """
    return await _api("GET", "/api/wechat/status")


def main():
    global _wechat_api_url  # module-level override; __main__ attr would be a no-op when imported
    parser = argparse.ArgumentParser(description="Pan WeChat MCP Server")
    parser.add_argument("--transport", default="stdio",
                        choices=["stdio", "sse", "streamable-http"])
    parser.add_argument("--port", type=int, default=9742,
                        help="Port for SSE/streamable-http transport (default: 9742)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host for SSE/streamable-http transport")
    parser.add_argument("--wechat-api-url", default=_wechat_api_url,
                        help=f"WeChat plugin HTTP API base URL (default: {_wechat_api_url})")
    args = parser.parse_args()

    _wechat_api_url = args.wechat_api_url.rstrip("/")

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
