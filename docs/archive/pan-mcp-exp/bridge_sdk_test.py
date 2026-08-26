"""E2E experiment 4: connect bridge MCP server, start bridge, trigger a Pan
task, and observe whether worker.result arrives as a notification.
"""
from __future__ import annotations

import asyncio
import json
import urllib.request

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PAN_API = "http://127.0.0.1:8768"
PAN_SESSION = "ses_0f0d2fd889ad32ae"  # worker-5 (idle)


async def main():
    received: list[str] = []

    async def logging_cb(params):
        received.append(f"LOGGING: {params.model_dump_json()}")
        return None

    async with streamablehttp_client("http://127.0.0.1:9742/mcp") as (read, write, _):
        async with ClientSession(read, write, logging_callback=logging_cb) as session:
            await session.initialize()
            res = await session.call_tool("start_bridge", {"session_ids": [PAN_SESSION], "timeout": 90})
            print(f"start_bridge: {res.content[0].text}")

            # Trigger a task on the idle worker via async /api/assign
            req = urllib.request.Request(
                PAN_API + "/api/assign",
                data=json.dumps({"sessionId": PAN_SESSION, "text": "请只回复两个字：完成。"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                print(f"assign -> {resp.read().decode()}")

            print("[wait] 45s for worker.result notification...")
            for i in range(45):
                await asyncio.sleep(1)
                if received:
                    break

    print(f"\n=== received {len(received)} notifications ===")
    for r in received:
        print(r)


if __name__ == "__main__":
    asyncio.run(main())
