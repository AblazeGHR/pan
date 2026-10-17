"""Owned subprocess fixture. Reads its directory afresh on every list request."""
import json
import os
import sys
from pathlib import Path

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server("plugin-fixture")
state_path = Path(sys.argv[1])
sid = os.environ.get("PAN_AGENT_SESSION_ID", "")


@server.list_tools()
async def list_tools():
    state = json.loads(state_path.read_text(encoding="utf-8"))
    field = "count" if sid.endswith("b") else "text"
    return [types.Tool(name=name, description="session-specific fixture", inputSchema={
        "type": "object", "properties": {field: {"type": "integer" if field == "count" else "string"}},
        "required": [field], "additionalProperties": False}) for name in state["tools"]]


@server.call_tool()
async def call_tool(name, arguments):
    if name == "broken":
        return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text="business rejected")])
    return types.CallToolResult(structuredContent={"session": sid, "name": name, "arguments": arguments},
        content=[types.TextContent(type="text", text="fixture result"),
                 types.ImageContent(type="image", mimeType="image/png", data="AA==")])


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
