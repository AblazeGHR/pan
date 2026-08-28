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
    - qq_send_file: 向 QQ 私聊/群聊发送文件（本地路径或 URL）
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
async def qq_send_message(
    target_type: str, target_id: str | int, text: str, bot_uin: str | None = None
) -> dict:
    """向指定 QQ 会话发送一条消息（私聊或群聊）。

    【必填参数·参数名以此为准】
      - target_type: "private"(私聊) 或 "group"(群聊)
      - target_id: QQ 号（私聊）或群号（群聊）
      - text: 消息内容。**参数名是 text，不是 message**；请勿用 camelCase（targetType/targetId）

    Args:
        target_type: 目标类型，"private"=私聊 / "group"=群聊
        target_id: 目标 QQ 号（私聊）或群号（群聊）
        text: 消息内容，支持 OneBot CQ 码（如 "[CQ:face,id=1]"、
            "[CQ:image,file=url]"）与普通文本
        bot_uin: 可选，指定用哪个 bot（QQ 号）发送。多账号部署时与入站消息的
            bot_uin 对齐（inbox/history 里每条消息的 bot_uin 标明它由哪个 bot
            收到，回复时传同一个号即「谁收到谁回」）；缺省用默认通道

    调用链：本工具 → POST {PAN_QQ_API_URL}/api/qq/send → NoneBot（packages/qq/
    plugin.py）按 bot_uin 选通道经 bot.call_api("send_private_msg"/
    "send_group_msg") → NapCat/LLOneBot 发送。
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
    body = {
        "target_type": target_type,
        "target_id": str(target_id),
        "text": text,
    }
    if bot_uin:
        body["bot_uin"] = str(bot_uin)
    return await _api("POST", "/api/qq/send", body)


@mcp.tool()
async def qq_send_file(
    target_type: str, target_id: str | int, file_path: str, name: str = "",
    bot_uin: str | None = None,
) -> dict:
    """向指定 QQ 会话发送一个文件（私聊或群聊）。

    【必填参数·参数名以此为准】
      - target_type: "private"(私聊) 或 "group"(群聊)
      - target_id: QQ 号（私聊）或群号（群聊）
      - file_path: 文件路径或 URL。**参数名是 file_path，不是 file/path**；
        请勿用 camelCase（targetType/targetId/filePath）

    Args:
        target_type: 目标类型，"private"=私聊 / "group"=群聊
        target_id: 目标 QQ 号（私聊）或群号（群聊）
        file_path: 文件的本地绝对路径（须 bot 网关侧可读）或可下载 URL，
            由网关（NapCat/LLOneBot）负责取用上传
        name: 展示文件名，缺省时从 file_path 自动推导（OneBot 要求显式 name）
        bot_uin: 可选，指定用哪个 bot（QQ 号）发送，语义同 qq_send_message；
            缺省用默认通道

    调用链：本工具 → POST {PAN_QQ_API_URL}/api/qq/send_file → NoneBot
    （packages/qq/plugin.py）按 bot_uin 选通道经 bot.call_api("upload_private_file"/
    "upload_group_file") → NapCat/LLOneBot 上传发送。返回 {"ok": true} 或错误；
    发送成功后对话记录中会出现一条 "[文件: 名字]" 的 assistant 记录。
    """
    if target_type not in ("private", "group"):
        return {"ok": False, "error": {
            "code": "invalid_target_type",
            "message": "target_type 必须是 'private' 或 'group'"}}
    if not file_path:
        return {"ok": False, "error": {
            "code": "empty_file_path", "message": "file_path 不能为空"}}
    body = {
        "target_type": target_type,
        "target_id": str(target_id),
        "file_path": file_path,
        "name": name,
    }
    if bot_uin:
        body["bot_uin"] = str(bot_uin)
    return await _api("POST", "/api/qq/send_file", body)


@mcp.tool()
async def qq_read_conversation(
    target_id: str | int, limit: int = 30, bot_uin: str | None = None
) -> dict:
    """读取某 QQ 会话的对话记录（本地落盘，非 NapCat 缓存）。

    【必填参数·参数名以此为准】
      - target_id: QQ 号（私聊）或群号（群聊），参数名是 target_id（不是 targetId）
      - limit: 可选，最多返回多少条（默认 30）

    Args:
        target_id: 会话标识 —— 私聊用 QQ 号，群聊用群号（与发送时的 target_id 一致）
        limit: 最多返回多少条（最新在前，默认 30，最大 500）
        bot_uin: 可选，指定读哪个 bot（QQ 号）收到的会话。多账号部署下同一
            用户在两个 bot 各是独立会话（按 bot 隔离落盘）；缺省读默认 bot；
            每条消息带 bot_uin 字段标明来源

    调用链：本工具 → GET {PAN_QQ_API_URL}/api/qq/history?target_id=&limit=&bot_uin=
    → packages/qq/plugin.py 读 data/qq_history/<bot_uin>/<target_id>.json。
    记录由 plugin 在收到消息时与 bot 回复时落盘，包含 user 与 assistant 两侧。
    返回 {"target_id": ..., "messages": [{role, text, time, bot_uin?}, ...]}。
    """
    if limit <= 0:
        limit = 30
    params = {"target_id": str(target_id), "limit": min(limit, 500)}
    if bot_uin:
        params["bot_uin"] = str(bot_uin)
    return await _api("GET", "/api/qq/history", params)


@mcp.tool()
async def qq_list_contacts() -> dict:
    """列出可联系的 QQ 会话（近期会话 + 完整好友/群，合并去重）。

    调用链：本工具 → GET {PAN_QQ_API_URL}/api/qq/recent_contacts → NoneBot 经
    bot.call_api 合并 get_recent_contact / get_friend_list / get_group_list。
    返回全部好友与群（peerUin/peerName/chatType，chatType 1=私聊 2=群），用于
    发现"该找谁发消息"；异常条目（peerUin 空/0、系统/临时会话）已剔除。
    NapCat 完全不可用时返回 ok:false，不影响其它工具。
    """
    return await _api("GET", "/api/qq/recent_contacts")


@mcp.tool()
async def qq_read_inbox(
    target_id: str | int, limit: int = 30, consume: bool = False,
    bot_uin: str | None = None,
) -> dict:
    """读取某 QQ 会话的待处理消息（inbox），selective 模式（PAN_QQ_MODE=selective）
    下由编排者（meta-agent/worker）消费。

    【必填参数·参数名以此为准】
      - target_id: QQ 号（私聊）或群号（群聊），参数名是 target_id（不是 targetId）
      - limit: 可选，最多读取多少条（默认 30）
      - consume: 可选，True 时读取后删除（消费即删）

    selective 模式：QQ 收到的消息**不自动回复**，而是进入待处理队列（inbox），
    等待编排者决定。典型编排流程：
      1. 用本工具读某 target 的 inbox（consume=False 先看，不删除）；
      2. 结合 qq_read_conversation 读该会话历史，决策：忽略 / 回复 / 路由到别的
         流程；
      3. 需要回复时用 qq_send_message(target_type, target_id, text, bot_uin)
         发送 —— bot_uin 用消息里标注的来源 bot（谁收到谁回）。
    决策完成后应带 consume=True 再读一次（消费即删），避免同一批消息被重复处理。

    Args:
        target_id: 会话标识 —— 私聊用 QQ 号，群聊用群号（与 qq_send_message 一致）
        limit: 最多读取多少条（默认 30，最大 500；旧消息在前，FIFO）
        consume: True 时读取后从 inbox 队列删除（消费即删）
        bot_uin: 可选，指定读哪个 bot（QQ 号）的 inbox。多账号部署下同一用户
            在两个 bot 各是独立会话（按 bot 隔离落盘）；缺省读默认 bot

    调用链：本工具 → GET {PAN_QQ_API_URL}/api/qq/inbox?target_id=&limit=&consume=&bot_uin=
    → packages/qq/plugin.py 读 data/qq_inbox/<bot_uin>/<target_id>.json。
    返回 {"target_id": ..., "messages": [{id, text, time, bot_uin?}, ...]}。
    """
    if limit <= 0:
        limit = 30
    params = {
        "target_id": str(target_id),
        "limit": min(limit, 500),
        "consume": 1 if consume else 0,
    }
    if bot_uin:
        params["bot_uin"] = str(bot_uin)
    return await _api("GET", "/api/qq/inbox", params)


@mcp.tool()
async def qq_bind(
    target_type: str, target_id: str | int, bot_uin: str | None = None
) -> dict:
    """绑定当前 Pan session 到某 QQ 会话，订阅其 inbox 更新提醒。

    【必填参数·参数名以此为准】
      - target_type: "user"(私聊) 或 "group"(群聊)
      - target_id: QQ 号（私聊）或群号（群聊）

    Args:
        target_type: "user"=私聊（对应 qq_send_message 的 private）/"group"=群聊
        target_id: 目标 QQ 号（私聊）或群号（群聊）
        bot_uin: 可选，订阅哪个 bot（QQ 号）的会话。多账号部署下同一用户在两
            个 bot 各是独立会话，订阅粒度是「某 bot 的某用户/群」；缺省订阅
            不区分 bot 的会话（任何 bot 收到都提醒，兼容旧订阅）

    绑定后，该 QQ 会话在 selective 模式下每收到新消息，本 session 的 queue_pending
    都会收到一条 `@@@@by qq : <会话标识> | <昵称> | bot <uin>` 提醒（含消息
    summary），并唤醒本 session 的 worker。镜像 report_subscribe 的订阅制，
    解绑用 qq_unbind。

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
    body = {
        "sessionId": manager_id,
        "target_type": target_type,
        "target_id": str(target_id),
    }
    if bot_uin:
        body["bot_uin"] = str(bot_uin)
    return await _api("POST", "/api/qq/subscribe", body, base_url=_pan_api_url)


@mcp.tool()
async def qq_unbind(
    target_type: str, target_id: str | int, bot_uin: str | None = None
) -> dict:
    """解绑当前 Pan session 与某 QQ 会话的绑定，停止 inbox 更新提醒。

    【必填参数·参数名以此为准】
      - target_type: "user"(私聊) 或 "group"(群聊)
      - target_id: QQ 号（私聊）或群号（群聊）

    Args:
        target_type: "user"=私聊 / "group"=群聊
        target_id: 目标 QQ 号（私聊）或群号（群聊）
        bot_uin: 可选，解绑哪个 bot（QQ 号）的订阅，与 qq_bind 对应；缺省解绑
            不区分 bot 的旧订阅

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
    body = {
        "sessionId": manager_id,
        "target_type": target_type,
        "target_id": str(target_id),
    }
    if bot_uin:
        body["bot_uin"] = str(bot_uin)
    return await _api("POST", "/api/qq/unsubscribe", body, base_url=_pan_api_url)


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
