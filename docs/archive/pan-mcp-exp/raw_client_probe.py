"""Raw HTTP client that mimics an MCP streamable-http client to observe
whether the server actually emits notification events on the SSE stream.

This does NOT use the mcp SDK client session — it speaks HTTP directly so we
can see the raw SSE frames the server writes for a session, both during a tool
call and after (background push).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:9741/mcp"
HDRS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

events: list[dict] = []
stop = threading.Event()


def post(body: dict, session_id: str | None = None) -> tuple[int, str | None, str]:
    """POST a JSON-RPC frame. Returns (http_code, new_session_id, body_text)."""
    hdrs = dict(HDRS)
    if session_id:
        hdrs["mcp-session-id"] = session_id
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(), headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.status
            sid = resp.headers.get("mcp-session-id")
            return code, sid, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("mcp-session-id"), e.read().decode("utf-8", errors="replace")


def read_stream(session_id: str):
    """Blocking GET that accumulates SSE events until stop flag."""
    req = urllib.request.Request(BASE, headers={**HDRS, "mcp-session-id": session_id}, method="GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        buf = b""
        while not stop.is_set():
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk
            # split on double newline
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                text = frame.decode("utf-8", errors="replace")
                events.append({"raw": text})
                for line in text.splitlines():
                    if line.startswith("data: "):
                        try:
                            events.append(json.loads(line[6:]))
                        except json.JSONDecodeError:
                            pass


def main():
    # 1. initialize
    code, sid, body = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "raw-probe", "version": "0.1"}}})
    print(f"[init] code={code} sid={sid}")
    print(f"[init] body={body!r}")

    # 2. start GET stream reader
    t = threading.Thread(target=read_stream, args=(sid,), daemon=True)
    t.start()
    time.sleep(0.5)

    # 3. notifications/initialized
    code, _, _ = post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    print(f"[initialized] code={code}")
    time.sleep(0.5)

    # 4. call push_once
    code, _, body = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "push_once", "arguments": {}}}, sid)
    print(f"[call push_once] code={code}")
    print(f"[call push_once] body={body!r}")

    # 5. now call spawn_pusher (background notifications)
    code, _, body = post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                          "params": {"name": "spawn_pusher", "arguments": {"interval": 1.0, "count": 2, "tag": "rawprobe"}}}, sid)
    print(f"[call spawn_pusher] code={code}")
    print(f"[call spawn_pusher] body={body!r}")

    # 6. wait for background pushes to arrive
    print("[wait] sleeping 4s for background pushes...")
    time.sleep(4)
    stop.set()
    time.sleep(0.5)

    # 7. dump all events observed on the stream
    print(f"\n=== {len(events)} raw events captured ===")
    for e in events:
        print(json.dumps(e, ensure_ascii=False))


if __name__ == "__main__":
    main()
