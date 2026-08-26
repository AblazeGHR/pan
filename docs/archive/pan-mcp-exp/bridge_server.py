"""Experiment 4 bridge server: forward Pan /ws/agent worker.result events
to connected MCP clients as standard notifications/message (logging).

Usage:
    python bridge_server.py --port 9742

The tool `start_bridge` subscribes a per-call background task to /ws/agent
for the current MCP session and pushes each worker.result as a standard
logging notification. A second tool `stop_bridge` (in new call) is not
needed — background task dies with the session.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request

import websockets
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("PanBridge")

PAN_WS = "ws://127.0.0.1:8768/ws/agent"


@mcp.tool()
async def start_bridge(session_ids: list[str] | None = None, timeout: float = 120.0) -> dict:
    """Subscribe this MCP connection to Pan /ws/agent worker.result events.

    Each worker.result is pushed back to the MCP client as a standard
    notifications/message (logging) with data={"event":"worker.result",...}.

    Args:
        session_ids: Restrict to these Pan session IDs (None = all)
        timeout: Max seconds to keep the WS subscription alive (0 = until session ends)
    """
    ctx = mcp.get_context()
    session = ctx.request_context.session

    async def _bridge():
        try:
            async with websockets.connect(PAN_WS) as ws:
                await ws.send(json.dumps({"type": "subscribe", "eventTypes": ["worker.result"]}))
                # consume the "subscribed" ack
                await ws.recv()
                print(f"[bridge] subscribed (sids={session_ids})")
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout if timeout else None)
                    except asyncio.TimeoutError:
                        print("[bridge] timeout, closing")
                        break
                    msg = json.loads(raw)
                    if msg.get("type") != "worker.result":
                        continue
                    if session_ids and msg.get("sessionId") not in session_ids:
                        continue
                    data = {
                        "event": "worker.result",
                        "sessionId": msg.get("sessionId"),
                        "workerId": msg.get("workerId"),
                        "status": msg.get("status"),
                        "taskSeq": msg.get("taskSeq"),
                        "result": msg.get("result"),
                    }
                    try:
                        await session.send_log_message(
                            level="info", data=data, logger="panbridge"
                        )
                        print(f"[bridge] forwarded worker.result sessionId={data['sessionId']} status={data['status']}")
                    except Exception as e:
                        print(f"[bridge] send err: {e!r}")
        except Exception as e:
            print(f"[bridge] ws err: {e!r}")

    task = asyncio.create_task(_bridge())
    return {"ok": True, "bridgeStarted": True, "sessionIds": session_ids, "timeout": timeout}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9742)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
