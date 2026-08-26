"""Minimal FastMCP server to probe whether cbc consumes server->client notifications.

Usage:
    python test_notify_server.py
"""
from __future__ import annotations

import argparse
import asyncio
import time

from mcp.server.fastmcp import FastMCP
from mcp.types import Notification

mcp = FastMCP("NotifyProbe")

_ACTIVE_SESSIONS: list = []


@mcp.tool()
async def push_once() -> dict:
    """Send ONE server->client notification right now (from inside a tool call).

    Also sends a logging message notification. Returns what was sent so we can
    confirm the tool call itself succeeded.
    """
    ctx = mcp.get_context()
    session = ctx.request_context.session
    sent = []
    try:
        # STANDARD notifications/message (logging) — parseable by any MCP client
        await session.send_log_message(
            level="info",
            data={"msg": "hello-from-server", "ts": time.time()},
            logger="probe",
        )
        sent.append("notifications/message(log)")
    except Exception as e:
        sent.append(f"log-err:{e!r}")
    return {"ok": True, "sent": sent}


@mcp.tool()
async def spawn_pusher(interval: float = 2.0, count: int = 3, tag: str = "bg") -> dict:
    """Start a background task that pushes N notifications at interval.

    Keeps a reference to the calling session so pushes happen between tool
    calls (simulating a /ws/agent subscription delivering async events).
    """
    ctx = mcp.get_context()
    session = ctx.request_context.session

    async def _push():
        for i in range(count):
            await asyncio.sleep(interval)
            try:
                # Use STANDARD notifications/message (logging) so any compliant
                # MCP client can parse it (custom methods break client parsing).
                await session.send_log_message(
                    level="info",
                    data={"event": "worker.result", "seq": i, "tag": tag, "ts": time.time()},
                    logger="probe",
                )
                print(f"[pusher] sent seq={i}")
            except Exception as e:
                print(f"[pusher] send err: {e!r}")

    task = asyncio.create_task(_push())
    print(f"[pusher] background task started {task}")
    return {"ok": True, "started": True, "interval": interval, "count": count, "tag": tag}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9741)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--transport", default="streamable-http", choices=["streamable-http", "sse"])
    args = parser.parse_args()
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
