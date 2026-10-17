"""Generated stdio projections for native Pan, fixed gateway and API shims."""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from . import ipc
from .runtime import error

GATEWAY_TOOLS = [
    types.Tool(name="api_discover", description="Discover enabled plugin capabilities and their current schemas. Exact name returns the full schema.",
        inputSchema={"type": "object", "properties": {
            "query": {"type": "string"}, "plugin": {"type": "string"},
            "name": {"type": "string"}, "include_schema": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "cursor": {"type": "string"}}, "additionalProperties": False}),
    types.Tool(name="api_call", description="Call a discovered canonical capability with arguments matching its schema. Never automatically replay an unconfirmed side effect.",
        inputSchema={"type": "object", "properties": {
            "name": {"type": "string"}, "arguments": {"type": "object"},
            "expected_contract_digest": {"type": "string"}},
            "required": ["name", "arguments"], "additionalProperties": False}),
]


def create_server(view, plugin=None, core=False, rpc=None):
    if view not in {"native", "gateway", "standalone"}:
        raise ValueError("Unknown plugin projection")
    server = Server("Pan" if view == "native" else "Pan Tools" if view == "gateway" else plugin)
    core_mcp = None
    if core:
        from packages.mcp.server import mcp
        core_mcp = mcp
    core_names = set()

    async def send(operation, **payload):
        message = {"session_id": os.environ.get("PAN_AGENT_SESSION_ID", ""),
                   "view": view, "operation": operation, **({"plugin": plugin} if plugin else {}), **payload}
        if rpc:
            return await rpc(message)
        return await asyncio.to_thread(ipc.request, message)

    @server.list_tools()
    async def list_tools():
        if view == "gateway":
            return GATEWAY_TOOLS
        response = await send("list")
        if "tools" not in response:
            raise RuntimeError("Plugin directory unavailable: " + json.dumps(response))
        result = []
        if core_mcp and (await send("core_enabled")).get("enabled"):
            result = await core_mcp.list_tools()
            core_names.clear()
            core_names.update(t.name for t in result)
        for descriptor in response["tools"]:
            tool = dict(descriptor["tool"])
            tool["name"] = descriptor["native_name"] if view == "native" else descriptor["source_name"]
            if tool["name"] in core_names:
                raise ValueError("Plugin projection collides with a Pan core tool")
            result.append(types.Tool.model_validate(tool))
        return result

    @server.call_tool(validate_input=True)
    async def call_tool(name, arguments):
        try:
            if core_mcp:
                # Check the current logical core selection, not only the stale
                # tool list cached by an already running CLI connection.
                response = await send("core_enabled")
                if response.get("enabled"):
                    available = {t.name for t in await core_mcp.list_tools()}
                    if name in available:
                        return await core_mcp.call_tool(name, arguments)
            if view == "gateway":
                if name == "api_discover":
                    response = await send("discover", **arguments)
                    if response.get("isError"):
                        return types.CallToolResult.model_validate(response)
                    return response
                if name != "api_call":
                    return types.CallToolResult.model_validate(error("tool_unavailable", "Unknown gateway tool"))
                result = await send("call", **arguments)
            else:
                result = await send("call", name=name, arguments=arguments)
            return types.CallToolResult.model_validate(result)
        except Exception as exc:
            return types.CallToolResult.model_validate(error("execution_unconfirmed", str(exc)))

    return server


async def run(view, plugin=None, core=False):
    server = create_server(view, plugin, core)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view", required=True, choices=["native", "gateway", "standalone"])
    parser.add_argument("--plugin")
    parser.add_argument("--core", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.view, args.plugin, args.core))


if __name__ == "__main__":
    main()
