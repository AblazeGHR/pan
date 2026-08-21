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

Environment variables:
    PAN_QQ_API_URL: QQ bot HTTP API base URL (default: http://127.0.0.1:8080)
        8080 是 NoneBot fastapi driver 的默认端口；可在 packages/qq/bot.py
        运行环境里用 HOST/PORT 覆盖，届时这里也要同步改。
"""

from __future__ import annotations

import argparse
import os

import httpx
from mcp.server.fastmcp import FastMCP

_qq_api_url = os.environ.get("PAN_QQ_API_URL", "http://127.0.0.1:8080").rstrip("/")

mcp = FastMCP("QQ")


async def _api(method: str, path: str, body: dict | None = None, timeout: float = 30.0) -> dict:
    """Call the QQ bot HTTP API and return parsed JSON.

    HTTP 错误时尽力透传后端返回的 JSON（可能带 error 字段），连接失败返回
    {ok: false, error: {code: connection_error, ...}}，与 packages/mcp/server.py
    的 _api 错误约定保持一致。
    """
    url = f"{_qq_api_url}{path}"
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
