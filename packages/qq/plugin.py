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
import websockets
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
_WS_URL = os.getenv("PAN_WS_URL", PAN_URL.replace("http://", "ws://", 1) + "/ws/agent")
POLL_INTERVAL = 1.5
MAX_POLL_TIME = 120

# ── command routes (QQ prefix → external HTTP API, bypasses LLM) ──

# Cached as a list of (prefixes, target) tuples, sorted by longest prefix
# first so ".rca" wins over ".rc". Empty list means "no manifest routes" —
# every message falls through to the LLM path.
_command_routes: list[tuple[list[str], str]] = []
_command_routes_loaded: bool = False


async def _refresh_command_routes() -> None:
    """Pull manifest command_routes from Pan Core. Failure leaves cache empty."""
    global _command_routes, _command_routes_loaded
    data = await _get("/api/manifest/command-routes")
    if "error" in data or "routes" not in data:
        print(f"[QQ Bridge] command-routes fetch failed: {data.get('error', 'no routes field')}")
        _command_routes = []
        _command_routes_loaded = True
        return
    routes = []
    for r in data["routes"]:
        prefixes = list(r.get("prefixes", []))
        target = r.get("target", "")
        if prefixes and target:
            routes.append((prefixes, target))
    # Longest-prefix-first: ".rca" must be matched before ".rc".
    routes.sort(key=lambda rt: max(len(p) for p in rt[0]), reverse=True)
    _command_routes = routes
    _command_routes_loaded = True
    print(f"[QQ Bridge] loaded {len(routes)} command route group(s) from manifest")


def _match_command_route(text: str) -> tuple[str, str] | None:
    """Return (target_url, text_after_prefix) if text matches a route, else None."""
    for prefixes, target in _command_routes:
        for p in prefixes:
            if text.startswith(p):
                return target, text[len(p):].lstrip()
    return None


# ── game_id resolution (config-driven, optional) ──

# RuleWhisper (or any plugin exposing a game lookup endpoint) is queried to
# bind a group_id → game_id. Configured via env so Pan code stays
# domain-agnostic (no RuleWhisper literal here). Template example:
#   PAN_GAME_LOOKUP_URL="http://127.0.0.1:9731/api/games/by_group?group_id={group_id}"
# Response JSON must contain a "game_id" field; otherwise None is returned.
_GAME_LOOKUP_URL = os.getenv("PAN_GAME_LOOKUP_URL", "")


async def _resolve_game_id(scope_id: str, scope: str) -> str | None:
    """Resolve a RuleWhisper game_id for the given group_id.

    Returns None if lookup is unconfigured, the scope is not group-level,
    the upstream call fails, or the response carries no game_id. Failures
    are logged at debug level only — game_id is best-effort metadata.
    """
    if not _GAME_LOOKUP_URL or scope != "group":
        return None
    url = _GAME_LOOKUP_URL.replace("{group_id}", scope_id)
    try:
        client = await _get_client()
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
        # Accept {"game_id": "..."} or {"id": "..."}; bail on anything else.
        return data.get("game_id") or data.get("id")
    except Exception as e:
        print(f"[QQ Bridge] game_id lookup failed (non-fatal): {type(e).__name__}: {e}")
        return None


async def _sync_session_game_id(session_id: str, scope_id: str, scope: str) -> None:
    """Ensure session carries a game_id for group-scoped sessions.

    Called after _ensure_session resolves a session_id. Reads the current
    session, short-circuits if it already has a game_id, otherwise resolves
    and PATCHes. No-op for non-group scopes or unconfigured lookups.
    """
    if scope != "group":
        return
    data = await _get(f"/api/sessions/{session_id}")
    if "error" in data:
        return
    if data.get("gameId"):
        return  # already bound
    game_id = await _resolve_game_id(scope_id, scope)
    if not game_id:
        return
    await _patch(f"/api/sessions/{session_id}", {"gameId": game_id})


# ── session mappings ──

_sessions: dict[str, "BridgeSession"] = {}
_pending: dict[str, asyncio.Event] = {}
_poll_tasks: dict[str, asyncio.Task] = {}

# ── WebSocket push state ──
# session_id → last consumed result taskSeq. Shared by the WS handler and the
# poll fallback so neither re-delivers a result already resolved (replay/dup).
_consumed_seq: dict[str, int] = {}
_ws_task: asyncio.Task | None = None
_ws_connected = asyncio.Event()  # set while subscribed to /ws/agent


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


async def _patch(path: str, data: dict = None) -> dict:
    url = f"{PAN_URL}{path}"
    try:
        client = await _get_client()
        r = await client.patch(url, json=data or {})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[QQ Bridge] PATCH failed: {type(e).__name__}: {e}")
        return {"error": str(e)}


# ── result delivery: WS push (primary) + poll (fallback) ──
#
# Primary path subscribes to /ws/agent worker.result events → real-time delivery
# (replaces the old 1.5s HTTP polling). `_poll_result` is kept as a fallback
# that only hits HTTP while the WS is disconnected, so a WS outage degrades to
# the previous polling behavior instead of dropping replies.
#
# Subscription strategy: we subscribe server-side to *all* worker.result events
# and filter locally via `_pending` (sessions with an active waiter). Sessions
# are created lazily per QQ chat, so a server-side sessionIds filter would need
# a resubscribe on every session creation; local filtering is equivalent and
# simpler. `_consumed_seq` dedupes replays / duplicates across WS and poll.

def _set_pending(session_id: str) -> None:
    """Resolve the pending waiter for session_id, if any."""
    evt = _pending.get(session_id)
    if evt and not evt.is_set():
        evt.set()


async def _poll_result(session_id: str, session_key: str):
    """Poll fallback — only does HTTP while the WS push is unavailable."""
    session = _sessions.get(session_key)
    if not session:
        return

    start = time.time()
    start_ts = session.last_result_ts

    while True:
        await asyncio.sleep(POLL_INTERVAL)

        evt = _pending.get(session_id)
        if evt is None or evt.is_set():
            return  # resolved by WS push, or waiter already gone

        # WS is live — it owns delivery; never force-resolve early here.
        if _ws_connected.is_set():
            continue

        # WS down: enforce the polling ceiling, then degrade gracefully.
        if time.time() - start >= MAX_POLL_TIME:
            _set_pending(session_id)
            return

        try:
            data = await _get(f"/api/sessions/{session_id}")
            if "error" in data:
                continue

            # worker gone (server restart / worker crash) — stop early
            if not data.get("workerId"):
                print(f"[QQ Bridge] Session {session_id} worker gone, stop polling")
                _set_pending(session_id)
                return

            # worker error state — stop early
            if data.get("workerStatus") == "error":
                print(f"[QQ Bridge] Session {session_id} worker error, stop polling")
                _set_pending(session_id)
                return

            lr = data.get("lastResult") or {}
            new_ts = lr.get("timestamp", "") if lr else ""
            new_seq = lr.get("taskSeq") if lr else None

            if isinstance(new_seq, int):
                # seq-based: only newer results resolve the waiter
                if new_seq > _consumed_seq.get(session_id, 0):
                    _consumed_seq[session_id] = new_seq
                    if new_ts:
                        session.last_result_ts = new_ts
                    _set_pending(session_id)
                    return
            else:
                # legacy data without taskSeq: fall back to timestamp change
                if new_ts and new_ts != start_ts:
                    session.last_result_ts = new_ts
                    _set_pending(session_id)
                    return

        except Exception:
            await asyncio.sleep(2)


# ── WebSocket push ──

def _handle_ws_result(ev: dict) -> None:
    """Handle a worker.result event (sync: only dict ops + Event.set)."""
    session_id = ev.get("sessionId")
    if not session_id:
        return
    seq = ev.get("taskSeq")
    if isinstance(seq, int):
        if seq <= _consumed_seq.get(session_id, 0):
            return  # replay / duplicate — already consumed
        _consumed_seq[session_id] = seq
    _set_pending(session_id)


async def _agent_ws_loop() -> None:
    """Persistent /ws/agent subscription with 5s reconnect backoff.

    Core restart rebuilds per-connection subscription state, so we re-subscribe
    on every connect, then send a `reconnect` catch-up for sessions with active
    waiters — the server replays the latest done result for each, covering
    results that landed while the WS was down.
    """
    while True:
        try:
            async with websockets.connect(_WS_URL, close_timeout=2) as ws:
                await ws.send(json.dumps({
                    "type": "subscribe",
                    "eventTypes": ["worker.result"],
                }))
                pending_ids = list(_pending.keys())
                if pending_ids:
                    await ws.send(json.dumps({
                        "type": "reconnect",
                        "sessionIds": pending_ids,
                    }))
                _ws_connected.set()
                print(f"[QQ Bridge] WS connected: {_WS_URL}")
                async for raw in ws:
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "worker.result":
                        _handle_ws_result(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[QQ Bridge] WS disconnected ({type(e).__name__}: {e}), reconnect in 5s")
        finally:
            _ws_connected.clear()
        await asyncio.sleep(5)


def _ensure_ws_task() -> None:
    """Lazily start the WS subscription loop (idempotent)."""
    global _ws_task
    if _ws_task is None or _ws_task.done():
        _ws_task = asyncio.create_task(_agent_ws_loop())


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
                # Seed the consumed cursor so WS/poll won't re-deliver the
                # session's existing last result as if it were new.
                seq = lr.get("taskSeq") if lr else None
                if isinstance(seq, int):
                    _consumed_seq[sess_data["id"]] = max(_consumed_seq.get(sess_data["id"], 0), seq)
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
    _ensure_ws_task()
    session_id = await _ensure_session(scope_id, scope)
    if not session_id:
        return "[Pan] cannot create session"

    # Best-effort game_id sync for group-scoped sessions so MCP tool calls
    # (e.g. RuleWhisper get_weapon) can carry the right game_id. No-op for
    # user-scoped sessions or unconfigured lookups.
    await _sync_session_game_id(session_id, scope_id, scope)

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

    # keep the cached ts fresh so the poll fallback doesn't re-detect an old
    # result for the next message
    session = _sessions.get(session_key)
    if session and lr and lr.get("timestamp"):
        session.last_result_ts = lr.get("timestamp", "")

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

    # Lazy-load command routes on first use (lets the bot start before Core
    # if needed). Hits are forwarded straight to the manifest-declared HTTP
    # target — 0 LLM tokens, millisecond latency.
    global _command_routes_loaded
    if not _command_routes_loaded:
        await _refresh_command_routes()

    match = _match_command_route(text)
    if match:
        target, body = match
        await bot.send(event, "processing, please wait...")
        try:
            client = await _get_client()
            r = await client.post(target, json={"text": body})
            r.raise_for_status()
            payload = r.json()
            # RuleWhisper HTTP API returns {"result": "..."} or plain text
            response = (
                payload.get("result")
                or payload.get("text")
                or payload.get("message")
                or (payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False))
            )
        except Exception as e:
            response = f"[Pan] command route error: {type(e).__name__}: {e}"

        MAX_LEN = 1500
        if len(response) <= MAX_LEN:
            await bot.send(event, response)
        else:
            for i in range(0, len(response), MAX_LEN):
                chunk = response[i : i + MAX_LEN]
                await bot.send(event, chunk)
                await asyncio.sleep(0.5)
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
    _ensure_ws_task()
    try:
        data = await _get("/api/models")
        models = data.get("models", [])
        print(f"[QQ Bridge] Pan Core connected, {len(models)} models available")
        print(f"[QQ Bridge] default model: {data.get('default', 'unknown')}")
    except Exception as e:
        print(f"[QQ Bridge] cannot connect to Pan Core: {e}")
        print("[QQ Bridge] ensure Pan Core is running (python main.py)")
    # Best-effort: pre-fetch command routes. Failure is non-fatal — the
    # handler will lazy-retry on first message. Logged separately so a
    # missing manifest is visible without masking Core connectivity.
    try:
        await _refresh_command_routes()
    except Exception as e:
        print(f"[QQ Bridge] command-routes prefetch failed (will retry on first msg): {e}")


@driver.on_shutdown
async def _shutdown():
    global _ws_task
    for task in list(_poll_tasks.values()):
        task.cancel()
    if _poll_tasks:
        await asyncio.gather(*_poll_tasks.values(), return_exceptions=True)
    if _ws_task:
        _ws_task.cancel()
        await asyncio.gather(_ws_task, return_exceptions=True)
        _ws_task = None
    if _client:
        await _client.aclose()
