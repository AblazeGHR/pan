"""Test whether the official mcp Python SDK client receives notifications
from the NotifyProbe server (streamable-http), using ClientSession's
logging_callback + message_handler.
"""
from __future__ import annotations

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    received: list[str] = []

    async def logging_cb(params):
        received.append(f"LOGGING: {params.model_dump_json()}")
        return None

    async def message_handler(message):
        try:
            root = message.root
            received.append(f"HANDLER: {root.method}: {root.params.model_dump_json() if root.params else ''}")
        except Exception as e:
            received.append(f"HANDLER-ERR: {e!r}")
        return None

    async with streamablehttp_client("http://127.0.0.1:9741/mcp") as (read, write, _):
        async with ClientSession(
            read, write, logging_callback=logging_cb, message_handler=message_handler
        ) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"tools: {[t.name for t in tools.tools]}")

            res = await session.call_tool("push_once", {})
            print(f"push_once result: {res.content[0].text}")

            res = await session.call_tool("spawn_pusher", {"interval": 1.0, "count": 2, "tag": "sdkclient"})
            print(f"spawn_pusher result: {res.content[0].text}")

            print("[wait] 3.5s for background pushes...")
            await asyncio.sleep(3.5)

    print(f"\n=== received {len(received)} callback messages ===")
    for r in received:
        print(r)


if __name__ == "__main__":
    asyncio.run(main())
