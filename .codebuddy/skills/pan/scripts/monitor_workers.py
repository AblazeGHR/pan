"""Subscribe to Pan /ws/agent and print worker events (one line each).

Runs under Monitor's `command` mode: each printed line wakes the coordinator.

Subscribes to worker.result (normal completion) AND worker.zombie
(unexpected death / watchdog kill / process exit) so unexpected worker
loss is visible to the coordinator.

Env:
  PAN_WS_URL       WS endpoint (default ws://127.0.0.1:8768/ws/agent)
  PAN_SESSION_IDS  comma-separated session ids to restrict subscription to
                   (omitted = all sessions)
"""
import asyncio
import json
import os

import websockets


def _subscribe_message() -> dict:
    raw = os.environ.get("PAN_SESSION_IDS", "")
    sids = [s.strip() for s in raw.split(",") if s.strip()]
    msg: dict = {"type": "subscribe", "eventTypes": ["worker.result", "worker.zombie"]}
    if sids:
        msg["sessionIds"] = sids
    return msg


async def main() -> None:
    uri = os.environ.get("PAN_WS_URL", "ws://127.0.0.1:8768/ws/agent")
    while True:
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps(_subscribe_message()))
                print("MONITOR_CONNECTED", flush=True)
                async for msg in ws:
                    try:
                        ev = json.loads(msg)
                    except json.JSONDecodeError:
                        print("MONITOR_RAW:", msg[:300], flush=True)
                        continue
                    if ev.get("type") == "worker.result":
                        print(
                            f"DONE session={ev.get('sessionId')} "
                            f"status={ev.get('status')} worker={ev.get('workerId')}",
                            flush=True,
                        )
                    elif ev.get("type") == "worker.zombie":
                        print(
                            f"DIE session={ev.get('sessionId')} "
                            f"worker={ev.get('workerId')} returncode={ev.get('returncode')}",
                            flush=True,
                        )
                    elif ev.get("type") == "subscribed":
                        print("MONITOR_SUBSCRIBED", flush=True)
                    else:
                        print("MONITOR_OTHER:", msg[:200], flush=True)
        except Exception as e:  # reconnect on drop
            print(f"MONITOR_DISCONNECTED: {e}", flush=True)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
