"""Experiment 4: subscribe to Pan /ws/agent worker.result and verify delivery.

Usage:
    python ws_subscribe_probe.py <session_id> <text>

Connects to /ws/agent, subscribes to worker.result, then triggers a task
via the HTTP API (async /api/assign so we return immediately), and waits
for the worker.result event on the websocket.
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request

import websockets

PAN_WS = "ws://127.0.0.1:8768/ws/agent"
PAN_API = "http://127.0.0.1:8768"


async def main():
    session_id, text = sys.argv[1], sys.argv[2]
    print(f"[probe] session={session_id} text={text!r}")

    # Trigger task asynchronously via /api/assign
    req = urllib.request.Request(
        PAN_API + "/api/assign",
        data=json.dumps({"sessionId": session_id, "text": text}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"[probe] assign -> {resp.read().decode()}")

    async with websockets.connect(PAN_WS) as ws:
        # Subscribe
        await ws.send(json.dumps({"type": "subscribe", "eventTypes": ["worker.result"]}))
        sub_resp = await asyncio.wait_for(ws.recv(), timeout=5)
        print(f"[probe] subscribed: {sub_resp}")

        print("[probe] waiting for worker.result (60s)...")
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                msg = json.loads(raw)
                print(f"[probe] WS event: {msg.get('type')}")
                if msg.get("type") == "worker.result":
                    print(f"[probe] WORKER.RESULT RECEIVED: status={msg.get('status')} "
                          f"sessionId={msg.get('sessionId')}")
                    print(f"[probe] result={json.dumps(msg.get('result'), ensure_ascii=False)[:400]}")
                    return
        except asyncio.TimeoutError:
            print("[probe] timeout: no worker.result within 60s")


if __name__ == "__main__":
    asyncio.run(main())
