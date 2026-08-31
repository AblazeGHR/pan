"""MCP server 描述符构造与落盘的共享 helper（adapter-architecture P0-1）。

cbc 与 kimi 的 mcp_args 曾各自实现「构造 mcpServers 描述符 + 对 pan/pan-qq
server 注入 PAN_AGENT_SESSION_ID/TITLE + type=stdio」的近逐行重复逻辑，此处
收敛为共享函数，供各 adapter 复用。

- ``build_mcp_servers(s)``：由 session 的 mcp_servers 配置构造 mcpServers dict
  （含 pan/pan-qq 身份注入），不产生副作用。
- ``write_mcp_json(path, s)``：幂等写 {"mcpServers": ...} 到 path，返回构造好的
  mcpServers dict（写失败/未配置时返回 None，调用方据此决定是否返回 --mcp-config flag）。

opencode 本轮未接 MCP（无 mcp_args，见 opencode-adaptation.md §4.5），故不在此列。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from ..session import Session

_log = logging.getLogger(__name__)

# 允许从 server 描述符透传的字段（stdio 与 HTTP/SSE MCP 配置的并集）。
_TRANSPORT_KEYS = (
    "command", "args", "url", "transport", "type", "cwd", "env", "headers",
)

# 需要注入 MA session 身份的 pan 系 server（worker_send 打标 / qq 订阅，立项 4.8）
_PAN_IDENTITY_SERVERS = ("pan", "pan-qq")
_PAN_SESSION_ID_ENV = "PAN_AGENT_SESSION_ID"
_PAN_SESSION_TITLE_ENV = "PAN_AGENT_SESSION_TITLE"


def build_mcp_servers(s: Session) -> dict[str, dict]:
    """从 session 的 mcp_servers 配置构造 mcpServers 描述符 dict。

    - 透传 command/args/url/transport/cwd/env/headers；
    - 对 pan / pan-qq server 注入 PAN_AGENT_SESSION_ID / PAN_AGENT_SESSION_TITLE
      （MA session 身份，供 MCP 工具代表本会话动作）；
    - 缺失 type 时默认 "stdio"（保证 CLI 可靠发现）。

    无 mcp_servers 配置时返回空 dict（调用方据此决定不写文件/不返回 flag）。
    """
    servers = s.adapter_config.get("mcp_servers")
    if not servers:
        return {}
    mcp_servers: dict[str, dict] = {}
    for srv in servers:
        if not isinstance(srv, dict):
            raise ValueError("MCP server descriptor must be an object")
        name = srv.get("name", "unnamed")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("MCP server descriptor requires a non-empty name")
        entry: dict = {k: srv[k] for k in _TRANSPORT_KEYS if k in srv}
        if not entry.get("command") and not entry.get("url"):
            raise ValueError(
                f"MCP server {name!r} has no command or URL configured"
            )
        if name in _PAN_IDENTITY_SERVERS:
            env = dict(entry.get("env") or {})
            env[_PAN_SESSION_ID_ENV] = s.id
            env[_PAN_SESSION_TITLE_ENV] = s.name
            entry["env"] = env
        entry.setdefault("type", "http" if entry.get("url") else "stdio")
        mcp_servers[name] = entry
    return mcp_servers


def write_mcp_json(path: str | Path, s: Session) -> dict[str, dict] | None:
    """幂等写 {"mcpServers": ...} 到 *path*，返回构造好的 mcpServers dict。

    session 未配置 mcp_servers 时不写文件并返回 None（与旧行为一致：cbc.mcp_args
    此时返回 []，kimi.write_kimi_mcp_json 直接返回）。写失败（OSError）时记录
    warning 并返回 None——调用方回退为「无 MCP」而非抛出，保证 worker 能继续跑。
    """
    mcp_servers = build_mcp_servers(s)
    if not mcp_servers:
        return None
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": mcp_servers}, f, ensure_ascii=False, indent=2)
    except OSError as e:
        _log.warning("failed to write mcp.json at %s: %s", path, e)
        return None
    return mcp_servers
