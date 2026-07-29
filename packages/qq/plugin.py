"""Pan QQ Channel — NoneBot2 plugin bridging QQ to Pan Core via HTTP/WS.

Architecture:
  QQ user → NapCat (OneBot WS) → NoneBot2 → this plugin → Pan Core HTTP API → Worker

Usage:
  1. Start NapCat (QQ protocol gateway)
  2. Start Pan Core: python main.py
  3. Start this bot: cd packages/qq && python bot.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, PrivateMessageEvent

# ── config ──

def _default_port():
    try:
        config_path = Path(__file__).resolve().parent.parent.parent / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        return config.get("port", 8767)
    except Exception:
        return 8767

PAN_URL = os.getenv("PAN_URL", f"http://127.0.0.1:{_default_port()}")
POLL_INTERVAL = 1.5
MAX_POLL_TIME = 120

# ── session mappings ──

_sessions: dict[str, "BridgeSession"] = {}
_pending: dict[str, asyncio.Event] = {}
_poll_tasks: dict[str, asyncio.Task] = {}


@dataclass
class BridgeSession:
    scope: str = "user"           # "user" or "group"
    scope_id: str = ""            # user_id or group_id
    session_id: str | None = None  # Pan internal session ID (ses_xxx), not the CLI session UUID
    worker_id: str | None = None
    last_result_ts: str = ""


# ── HTTP calls ──

_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=10)
    return _client


async def _get(path: str) -> dict:
    url = f"{PAN_URL}{path}"
    try:
        client = await _get_client()
        r = await client.get(url)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[QQ Bridge] GET failed: {type(e).__name__}: {e}")
        return {"error": str(e)}


async def _post(path: str, data: dict = None) -> dict:
    url = f"{PAN_URL}{path}"
    try:
        client = await _get_client()
        r = await client.post(url, json=data or {})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[QQ Bridge] POST failed: {type(e).__name__}: {e}")
        return {"error": str(e)}


# ── polling ──

async def _poll_result(session_id: str, session_key: str):
    session = _sessions.get(session_key)
    if not session:
        return

    start = time.time()
    last_ts = session.last_result_ts

    while time.time() - start < MAX_POLL_TIME:
        await asyncio.sleep(POLL_INTERVAL)

        try:
            data = await _get(f"/api/sessions/{session_id}")
            if "error" in data:
                continue

            # worker gone (server restart / worker crash) — stop early
            if not data.get("workerId"):
                print(f"[QQ Bridge] Session {session_id} worker gone, stop polling")
                evt = _pending.get(session_id)
                if evt:
                    evt.set()
                return

            # worker error state — stop early
            if data.get("workerStatus") == "error":
                print(f"[QQ Bridge] Session {session_id} worker error, stop polling")
                evt = _pending.get(session_id)
                if evt:
                    evt.set()
                return

            lr = data.get("lastResult") or {}

            new_ts = lr.get("timestamp", "") if lr else ""
            if new_ts and new_ts != last_ts:
                session.last_result_ts = new_ts
                evt = _pending.get(session_id)
                if evt:
                    evt.set()
                return

        except Exception:
            await asyncio.sleep(2)

    evt = _pending.get(session_id)
    if evt:
        evt.set()


async def _ensure_session(scope_id: str, scope: str = "user") -> str | None:
    session_key = f"{scope}:{scope_id}"
    session = _sessions.get(session_key)
    if session and session.session_id:
        data = await _get(f"/api/sessions/{session.session_id}")
        if "error" not in data:
            # always sync workerId from server — cache may be stale
            session.worker_id = data.get("workerId")
            # session exists on disk but worker may be dead
            if not session.worker_id:
                result = await _post("/api/spawn", {"sessionId": session.session_id})
                if "error" not in result:
                    session.worker_id = result.get("workerId")
                else:
                    print(f"[QQ Bridge] re-spawn worker failed: {result['error']}")
            return session.session_id

    # check for existing session (avoid duplicates)
    prefix = "qqg" if scope == "group" else "qq"
    name_prefix = f"{prefix}-{scope_id[-6:]}"
    existing = await _get("/api/sessions")
    if "sessions" in existing:
        for sess_data in existing["sessions"]:
            if sess_data.get("name", "").startswith(name_prefix):
                lr = sess_data.get("lastResult") or {}
                bridge = BridgeSession(
                    scope=scope,
                    scope_id=scope_id,
                    session_id=sess_data["id"],
                    worker_id=sess_data.get("workerId"),
                    last_result_ts=lr.get("timestamp", ""),
                )
                _sessions[session_key] = bridge
                if not bridge.worker_id:
                    result = await _post("/api/spawn", {"sessionId": bridge.session_id})
                    if "error" not in result:
                        bridge.worker_id = result.get("workerId")
                return bridge.session_id

    # new session
    name = name_prefix
    s = await _post("/api/sessions", {"name": name})
    if "error" in s:
        print(f"[QQ Bridge] create session failed: {s['error']}")
        return None
    session_id = s["id"]

    result = await _post("/api/spawn", {"sessionId": session_id})
    if "error" in result:
        print(f"[QQ Bridge] spawn worker failed: {result['error']}")
        return None

    bridge = BridgeSession(
        scope=scope,
        scope_id=scope_id,
        session_id=session_id,
        worker_id=result.get("workerId"),
    )
    _sessions[session_key] = bridge
    return session_id


async def _send_and_wait(text: str, scope_id: str, scope: str = "user") -> str:
    session_id = await _ensure_session(scope_id, scope)
    if not session_id:
        return "[Pan] cannot create session"

    session_key = f"{scope}:{scope_id}"
    evt = asyncio.Event()
    _pending[session_id] = evt

    if session_id not in _poll_tasks or _poll_tasks[session_id].done():
        _poll_tasks[session_id] = asyncio.create_task(
            _poll_result(session_id, session_key)
        )

    result = await _post("/api/task", {
        "sessionId": session_id,
        "text": text,
    })
    if "error" in result:
        del _pending[session_id]
        task = _poll_tasks.pop(session_id, None)
        if task:
            task.cancel()
        return f"[Pan] error: {result['error']}"

    try:
        await asyncio.wait_for(evt.wait(), timeout=MAX_POLL_TIME + 5)
    except asyncio.TimeoutError:
        del _pending[session_id]
        return "[Pan] response timeout"

    del _pending[session_id]

    data = await _get(f"/api/sessions/{session_id}")
    if "error" in data:
        return "[Pan] cannot get response"

    # prefer lastResult.result — cbc sometimes only gives final text in result event
    lr = data.get("lastResult") or {}
    result_text = lr.get("result", "") if lr else ""
    if isinstance(result_text, str) and result_text.strip():
        lines = [l for l in result_text.split("\n") if not l.startswith("🔧")]
        return "\n".join(lines).strip() or "(tool call only)"

    # fallback: last assistant message in history
    history = data.get("history", [])

    for msg in reversed(history):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            lines = [l for l in content.split("\n") if not l.startswith("🔧")]
            return "\n".join(lines).strip() or "(tool call only)"

    return "[Pan] no response"


# ── message handler ──

_msg_handler = on_message()


@_msg_handler.handle()
async def handle_message(bot: Bot, event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        bot_qq = int(bot.self_id)
        if bot_qq not in [seg.data.get("qq", 0) for seg in event.message if seg.type == "at"]:
            return
        scope = "group"
        scope_id = str(event.group_id)
    else:
        scope = "user"
        scope_id = str(event.get_user_id())

    text = event.get_plaintext().strip()

    if not text:
        return

    await bot.send(event, "processing, please wait...")

    response = await _send_and_wait(text, scope_id, scope=scope)

    MAX_LEN = 1500
    if len(response) <= MAX_LEN:
        await bot.send(event, response)
    else:
        for i in range(0, len(response), MAX_LEN):
            chunk = response[i : i + MAX_LEN]
            await bot.send(event, chunk)
            await asyncio.sleep(0.5)


# ── lifecycle hooks ──

driver = get_driver()


@driver.on_startup
async def _startup():
    try:
        data = await _get("/api/models")
        models = data.get("models", [])
        print(f"[QQ Bridge] Pan Core connected, {len(models)} models available")
        print(f"[QQ Bridge] default model: {data.get('default', 'unknown')}")
    except Exception as e:
        print(f"[QQ Bridge] cannot connect to Pan Core: {e}")
        print("[QQ Bridge] ensure Pan Core is running (python main.py)")


@driver.on_shutdown
async def _shutdown():
    for task in list(_poll_tasks.values()):
        task.cancel()
    if _poll_tasks:
        await asyncio.gather(*_poll_tasks.values(), return_exceptions=True)
    if _client:
        await _client.aclose()
