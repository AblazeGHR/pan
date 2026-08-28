"""Pan QQ Channel — NoneBot2 plugin bridging QQ to Pan Core via HTTP/WS.

Architecture:
  QQ user → NapCat (OneBot WS) → NoneBot2 → this plugin → Pan Core HTTP API → Worker

Usage:
  1. Start NapCat (QQ protocol gateway)
  2. Start Pan Core: python main.py
  3. Start this bot: cd packages/qq && python bot.py

QQ bot HTTP API（挂载在 NoneBot fastapi driver server_app，默认 127.0.0.1:8080，
供独立 MCP server packages/qq/mcp.py 经 PAN_QQ_API_URL 调用）:
  POST   /api/qq/send       body {target_type: private|group, target_id, text}
  GET    /api/qq/history    ?target_id=&limit= → {target_id, messages:[{role,text,time}]}
  GET    /api/qq/recent_contacts               → 近期会话 + 完整好友/群 合并列表
  GET    /api/qq/inbox      ?target_id=&limit=&consume= → {target_id, messages:[{id,text,time}]}
  DELETE /api/qq/inbox      ?target_id= → 清空该 target 的 inbox
消息记录按 target_id 落盘到 data/qq_history/<target_id>.json（user/assistant 双侧）。

双模式（PAN_QQ_MODE）:
  mirror    默认。收到 QQ 消息 → 绑定 session → 派发 worker → 自动回复（现状兼容）。
  selective 监听/选择性发送。QQ 消息只写 history + data/qq_inbox/<target_id>.json
            （待处理队列），不建 session、不 spawn、不自动回复；由 meta-agent 经
            MCP 工具 qq_read_inbox（读 inbox）→ 决策 → qq_send_message（回复）。
            command-routes（绕过 LLM 的确定路由）在两种模式下都立即执行。
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
from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot

# recent_contacts 结果缓存（按 bot_uin）：联系人/会话列表相对稳定，短 TTL 缓存
# 避免每次打开 Postbox 都全量调 3 个 OneBot API（get_recent_contact / get_friend_list /
# get_group_list），双 bot 时尤甚。失败结果不缓存（下次重试）。
_CONTACTS_CACHE_TTL = 60.0
_contacts_cache: dict[str, tuple[float, dict]] = {}


def _contacts_cache_key(bot_uin: str | int | None) -> str:
    return str(bot_uin or "")

from packages.qq.channels import QQChannel, QQMessage
from packages.qq import channels as _qq_channels

# ── config ──

def _default_port():
    try:
        config_path = Path(__file__).resolve().parent.parent.parent / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        return config.get("port", 8768)
    except Exception:
        return 8768

PAN_URL = os.getenv("PAN_URL", f"http://127.0.0.1:{_default_port()}")
_WS_URL = os.getenv("PAN_WS_URL", PAN_URL.replace("http://", "ws://", 1) + "/ws/agent")
POLL_INTERVAL = 1.5
MAX_POLL_TIME = 120


# ── mode switch ──

def _load_config() -> dict:
    """Read config.json at the project root; empty dict on failure."""
    try:
        path = Path(__file__).resolve().parent.parent.parent / "config.json"
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _qq_mode() -> str:
    """Return the QQ bridge mode: "mirror" (default) or "selective".

    配置来源优先级：config.json 的 ``qq.mode``（推荐，如 ``{"qq": {"mode": "selective"}}``）
    > 环境变量 ``PAN_QQ_MODE`` > 默认 "mirror"。

    selective 时开启监听/选择性发送：QQ 消息只进 inbox（待处理队列）与 history，
    不建 session / 不 spawn / 不自动回复；由 meta-agent 经 MCP 工具
    qq_read_inbox → 决策 → qq_send_message 决定回不回、回什么。
    非法值回退 mirror，保证现状兼容。
    """
    cfg_mode = (_load_config().get("qq") or {}).get("mode", "")
    mode = os.getenv("PAN_QQ_MODE", cfg_mode).strip().lower()
    return mode if mode in ("mirror", "selective") else "mirror"

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
    lr = data.get("lastResult") or {}
    if session and lr and lr.get("timestamp"):
        session.last_result_ts = lr.get("timestamp", "")

    # prefer lastResult.result — cbc sometimes only gives final text in result event
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


# ── message handler（通道抽象：业务层只认 QQMessage）──

async def _send_chunks(
    target_type: str, target_id: str, text: str, ch: QQChannel | None = None
) -> None:
    """经指定通道把文本按 1500 字切片发送（command-route 与 mirror 回复共用）。

    ch 缺省用默认通道；多账号部署时由调用方传入消息来源 bot 的通道（谁收到谁回）。
    """
    ch = ch or get_channel()
    MAX_LEN = 1500
    if len(text) <= MAX_LEN:
        await ch.send(target_type, target_id, text)
        return
    for i in range(0, len(text), MAX_LEN):
        chunk = text[i : i + MAX_LEN]
        await ch.send(target_type, target_id, chunk)
        await asyncio.sleep(0.5)


async def handle_qq_message(msg: QQMessage) -> None:
    """业务层入站消息处理（通道无关）。

    由通道的 on_message hook 把 OneBot event 归一化为 QQMessage 后调用。群消息的
    @-bot 过滤已在通道 hook 内完成，这里收到的已是应当处理的消息。发送一律经
    ``get_channel().send(...)``，切换 NapCat / LLOneBot 不改本函数。
    """
    text = msg.text
    if not text:
        return
    scope, scope_id = msg.scope, msg.scope_id
    target_type = msg.target_type()

    # 多账号路由：谁收到谁回 —— 按消息来源 bot（self_id）找通道，未配置 / 未
    # 注册（单通道兼容）回退默认通道。
    reply_channel = get_channel_by_uin(msg.bot_uin) or get_channel()

    # 落盘用户消息（按 target_id：私聊 user_id / 群 group_id），供 HTTP API
    # GET /api/qq/history 与 MCP 工具 qq_read_conversation 读取。bot_uin 标注
    # 消息由哪个 bot 收到（多账号；空串时不写字段，旧记录兼容）。
    await _append_history(scope_id, "user", text, bot_uin=msg.bot_uin)

    # selective 模式：消息进入 inbox（待处理队列），不自动回复；由 meta-agent
    # 经 MCP 工具 qq_read_inbox → 决策 → qq_send_message 决定回不回、回什么。
    # 注意：command-route 消息同样会入 inbox（消息已在别处自动回复，编排者可
    # 结合 history 判断），见下方 command route 分支。
    if _qq_mode() == "selective":
        await _append_inbox(
            scope_id, scope, text, msg.sender_nickname, bot_uin=msg.bot_uin
        )

    # Lazy-load command routes on first use (lets the bot start before Core
    # if needed). Hits are forwarded straight to the manifest-declared HTTP
    # target — 0 LLM tokens, millisecond latency.
    global _command_routes_loaded
    if not _command_routes_loaded:
        await _refresh_command_routes()

    match = _match_command_route(text)
    if match:
        target, body = match
        await reply_channel.send(target_type, scope_id, "processing, please wait...")
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

        await _append_history(
            scope_id, "assistant", response, bot_uin=reply_channel.config.bot_uin
        )
        await _send_chunks(target_type, scope_id, response, ch=reply_channel)
        return

    # mirror 模式（默认）：绑定 session → 派发 worker → 自动回复（现状兼容）。
    # selective 模式：消息已入 inbox + history，此处不 _ensure_session / 不
    # spawn / 不 _send_and_wait / 不自动回复，直接结束。
    if _qq_mode() != "selective":
        await reply_channel.send(target_type, scope_id, "processing, please wait...")

        response = await _send_and_wait(text, scope_id, scope=scope)

        await _append_history(
            scope_id, "assistant", response, bot_uin=reply_channel.config.bot_uin
        )
        await _send_chunks(target_type, scope_id, response, ch=reply_channel)


# ── lifecycle hooks ──

driver = get_driver()

# ── QQ 通道（插件化/配置化）：按 config.json qq.channel 选择 NapCat / LLOneBot ──
#
# bot.py 会先构造并 stash 通道（含正确的 ws_urls）；若 plugin 被单独 import（单测），
# 这里自建一个默认 napcat 通道。业务层经 get_channel() 取得当前通道实例，发送 / 取
# 联系人全部走通道抽象，切换通道不改业务逻辑。

_channel: "QQChannel | None" = None


def get_channel() -> QQChannel:
    """返回当前 QQ 通道实例（惰性创建；优先复用 bot.py stash 的通道）。"""
    global _channel
    if _channel is None:
        stashed = _qq_channels.get_active_channel()
        if stashed is not None:
            _channel = stashed
        else:
            name, ws, token = _qq_channels.build_channel_spec(_load_config().get("qq"))
            _channel = _qq_channels.create_channel(
                name, ws, token, bot_fallback=lambda: _active_bot
            )
            _qq_channels.set_active_channel(_channel)
    return _channel


def get_channel_by_uin(bot_uin: str | int | None) -> QQChannel | None:
    """按 bot QQ 号（消息来源 self_id）查通道；未注册 / 未配置返回 None。

    多账号部署时业务层用它把回复路由到收到消息的那个 bot（谁收到谁回）。
    """
    return _qq_channels.get_channel_by_uin(bot_uin)


# 绑定 NoneBot driver（注册 on_bot_connect / on_message）+ 注册业务消息回调
_channel = get_channel()
_channel.bind(driver)
_channel.on_message(handle_qq_message)

# 多账号部署：bot.py 启动时已把全部通道注册进 _ACTIVE_CHANNELS。这里把默认之外
# 的通道也绑定到 driver 并注册业务回调（bind 幂等；通道 _on_message 内部按
# config.bot_uin 过滤，各通道只处理自己 bot 的 event）。
for _name_, _ch_ in _qq_channels.iter_channels().items():
    if _ch_ is not _channel:
        _ch_.bind(driver)
        _ch_.on_message(handle_qq_message)


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


# ── QQ bot HTTP API（方案 A：独立 MCP server 精细驱动 QQ）──
#
# 在 NoneBot 的 FastAPI server_app 上挂载内部 HTTP API，供独立 MCP server
# （packages/qq/mcp.py）经 PAN_QQ_API_URL 调用。消息记录按 target_id
# （私聊 user_id / 群 group_id）落盘到 data/qq_history/<target_id>.json，
# 供 history API 与 qq_read_conversation 读取。NapCat 的 get_recent_contact
# 只能取缓存近期联系人，可靠历史以本文件为准。

_HISTORY_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "qq_history"
_HISTORY_MAX_ENTRIES = 500
_history_lock = asyncio.Lock()

# 最新可用的 bot 实例（NoneBot on_bot_connect 维护），API 发送用。
_active_bot: Bot | None = None


@driver.on_bot_connect
async def _on_bot_connect(bot: Bot) -> None:
    global _active_bot
    _active_bot = bot
    print(f"[QQ Bridge] bot connected: {bot.self_id}")


@driver.on_bot_disconnect
async def _on_bot_disconnect(bot: Bot) -> None:
    global _active_bot
    if _active_bot is bot:
        _active_bot = None
    print(f"[QQ Bridge] bot disconnected: {bot.self_id}")


def _history_path(target_id: str, bot_uin: str | int | None = None) -> Path:
    """Sanitized history file path for a target_id (user_id / group_id).

    多账号会话隔离（方案A 目录分层）：bot_uin 非空 → ``<dir>/<bot_uin>/<id>.json``；
    为空（单通道旧数据）→ 旧路径 ``<dir>/<id>.json``。
    Only alphanumerics / ``-_`` survive, so a hostile target_id can't escape
    the history dir via ``..``.
    """
    safe = "".join(c for c in str(target_id) if c.isalnum() or c in "-_")
    if bot_uin:
        bot = "".join(c for c in str(bot_uin) if c.isalnum() or c in "-_")
        return _HISTORY_DIR / bot / f"{safe}.json"
    return _HISTORY_DIR / f"{safe}.json"


def _migrate_legacy_file(legacy: Path, new: Path) -> None:
    """旧单通道数据迁移：bot 路径尚无文件而旧路径存在时整体搬过去（best-effort）。

    保证升级到多账号隔离后旧会话记录不丢（延续对话上下文）。失败静默忽略。
    """
    try:
        if not new.exists() and legacy.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            legacy.rename(new)
    except OSError:
        pass


async def _load_history(target_id: str, bot_uin: str | int | None = None) -> list[dict]:
    """Load a target's on-disk conversation log (list of {role,text,time[,bot_uin]})."""
    try:
        data = json.loads(_history_path(target_id, bot_uin).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


async def _append_history(
    target_id: str, role: str, text: str, bot_uin: str | int | None = None
) -> None:
    """Append one message to a target's conversation log (role: user/assistant).

    bot_uin 非空时按 bot 隔离落盘（方案A：<dir>/<bot_uin>/<id>.json）并标注该
    消息经哪个 bot（QQ 号）收发；为空时写旧路径（单通道兼容，不写字段）。
    """
    if not text:
        return
    async with _history_lock:
        _migrate_legacy_file(_history_path(target_id), _history_path(target_id, bot_uin))
        messages = await _load_history(target_id, bot_uin)
        entry = {
            "role": role,
            "text": text,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if bot_uin:
            entry["bot_uin"] = str(bot_uin)
        messages.append(entry)
        if len(messages) > _HISTORY_MAX_ENTRIES:
            messages = messages[-_HISTORY_MAX_ENTRIES:]
        _history_path(target_id, bot_uin).parent.mkdir(parents=True, exist_ok=True)
        _history_path(target_id, bot_uin).write_text(
            json.dumps(messages, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )


# ── inbox（selective 模式待处理队列）──
#
# selective 模式（PAN_QQ_MODE=selective）下，QQ 收到的消息不自动回复，而是按
# target_id（私聊 user_id / 群 group_id）追加到 data/qq_inbox/<target_id>.json
# 待处理队列，由 meta-agent 经 MCP 工具 qq_read_inbox / GET /api/qq/inbox 读取
# 并决定是否回复（消费即删）。路径消毒 / 锁 / 上限沿用 qq_history 的实现。

_INBOX_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "qq_inbox"
_INBOX_MAX_ENTRIES = 500
_inbox_lock = asyncio.Lock()


def _inbox_path(target_id: str, bot_uin: str | int | None = None) -> Path:
    """Sanitized inbox file path — same rule as _history_path（方案A bot 目录分层）."""
    safe = "".join(c for c in str(target_id) if c.isalnum() or c in "-_")
    if bot_uin:
        bot = "".join(c for c in str(bot_uin) if c.isalnum() or c in "-_")
        return _INBOX_DIR / bot / f"{safe}.json"
    return _INBOX_DIR / f"{safe}.json"


async def _load_inbox(
    target_id: str, bot_uin: str | int | None = None
) -> list[dict]:
    """Load a target's pending inbox queue (list of {id,target_id,scope,role,text,time[,bot_uin]})."""
    try:
        data = json.loads(_inbox_path(target_id, bot_uin).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


async def _append_inbox(
    target_id: str, scope: str, text: str, nickname: str = "",
    bot_uin: str | int | None = None,
) -> None:
    """Append one pending QQ message to a target's inbox (selective mode).

    Scope is "user" (private) or "group"; role is always "user" — inbox 只收
    上行 QQ 消息，编排者的回复经 api_send 走 history，不回流 inbox。

    bot_uin 可选：多账号部署时标注该消息由哪个 bot 收到，编排者读 inbox 时
    据此决定用哪个 bot 回复（谁收到谁回）；None 不写字段（旧记录兼容）。

    落盘后 best-effort 通知 Pan Core（/api/qq/notify）：若该 QQ 会话被某个
    Pan session 订阅（qq_subscriptions），订阅者会收到一条 `@@@@by qq` 提醒。
    """
    if not text:
        return
    async with _inbox_lock:
        _migrate_legacy_file(_inbox_path(target_id), _inbox_path(target_id, bot_uin))
        messages = await _load_inbox(target_id, bot_uin)
        entry = {
            "id": f"{int(time.time() * 1000)}-{len(messages)}",
            "target_id": target_id,
            "scope": scope,
            "role": "user",
            "text": text,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if bot_uin:
            entry["bot_uin"] = str(bot_uin)
        messages.append(entry)
        if len(messages) > _INBOX_MAX_ENTRIES:
            messages = messages[-_INBOX_MAX_ENTRIES:]
        _inbox_path(target_id, bot_uin).parent.mkdir(parents=True, exist_ok=True)
        _inbox_path(target_id, bot_uin).write_text(
            json.dumps(messages, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    await _notify_pan_inbox(scope, target_id, nickname, text, bot_uin)


def _pan_core_url() -> str:
    """Pan Core base URL：PAN_CORE_API_URL 环境变量优先，否则 config.json 的 port。"""
    env_url = os.getenv("PAN_CORE_API_URL")
    if env_url:
        return env_url.rstrip("/")
    port = (_load_config().get("port")) or 8768
    return f"http://127.0.0.1:{port}"


async def _notify_pan_inbox(
    scope: str, target_id: str, nickname: str, text: str,
    bot_uin: str | int | None = None,
) -> None:
    """Best-effort 通知 Pan Core：QQ inbox 有新消息。

    供 qq_subscriptions 订阅者（绑定该 QQ 会话的 Pan session）消费。bot_uin
    可选（多账号来源标注）。仅当 Pan Core 可达时推送；失败只打印警告，
    不影响 inbox 落盘（通知是异步增强，inbox 文件才是真源）。
    """
    try:
        url = f"{_pan_core_url()}/api/qq/notify"
        client = await _get_client()
        payload = {
            "target_type": scope,
            "target_id": str(target_id),
            "nickname": nickname,
            "text": text,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if bot_uin:
            payload["bot_uin"] = str(bot_uin)
        await client.post(url, json=payload)
    except Exception as e:
        print(f"[QQ Bridge] notify Pan inbox failed: {type(e).__name__}: {e}")


def _resolve_read_bot_uin(bot_uin: str | int | None) -> str | None:
    """读接口（history/inbox）的 bot 归属解析。

    显式 bot_uin 优先；缺省用默认通道的 bot_uin（多账号部署下读默认 bot 的
    会话）；都无（单通道 / 未配置）→ None 走旧的无 bot 路径，兼容旧数据。
    """
    if bot_uin:
        return str(bot_uin)
    ch = _qq_channels.get_active_channel()
    if ch is not None and getattr(ch.config, "bot_uin", None):
        return str(ch.config.bot_uin)
    return None


async def api_inbox(
    target_id: str, limit: int = 30, consume: bool = False,
    bot_uin: str | int | None = None,
) -> dict:
    """Read a target's pending inbox messages, oldest first.

    bot_uin 定位 bot 隔离的 inbox 文件（方案A：<dir>/<bot_uin>/<target_id>.json）；
    缺省用默认通道的 bot_uin，单通道旧数据走旧路径。consume=True → 读取后即从
    队列删除（消费即删，落盘回写），避免编排者重复处理。
    """
    target_id = str(target_id)
    bot = _resolve_read_bot_uin(bot_uin)
    if limit is None or limit <= 0:
        limit = _INBOX_MAX_ENTRIES
    async with _inbox_lock:
        messages = await _load_inbox(target_id, bot)
        take = messages[:limit]
        if consume:
            messages = messages[limit:]
            _inbox_path(target_id, bot).parent.mkdir(parents=True, exist_ok=True)
            _inbox_path(target_id, bot).write_text(
                json.dumps(messages, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
    out = []
    for m in take:
        item = {"id": m.get("id"), "text": m.get("text"), "time": m.get("time")}
        # bot_uin：多账号来源标注；旧记录无此字段则不返回（向后兼容）
        if m.get("bot_uin"):
            item["bot_uin"] = str(m["bot_uin"])
        out.append(item)
    return {"target_id": target_id, "messages": out}


async def api_inbox_clear(
    target_id: str, bot_uin: str | int | None = None
) -> dict:
    """Delete a target's inbox file (clear all pending messages).

    bot_uin 语义同 api_inbox（缺省默认通道 bot；None 走旧路径）。
    """
    target_id = str(target_id)
    bot = _resolve_read_bot_uin(bot_uin)
    async with _inbox_lock:
        try:
            p = _inbox_path(target_id, bot)
            if p.exists():
                p.unlink()
            return {"ok": True, "target_id": target_id, "cleared": True}
        except OSError as e:
            return {"ok": False, "error": {
                "code": "clear_failed",
                "message": f"{type(e).__name__}: {e}"}}


async def api_send(
    target_type: str, target_id: str | int, text: str, bot_uin: str | int | None = None
) -> dict:
    """发送一条 QQ 消息（走通道抽象）。返回 {ok, message_id} 或错误。

    调用链（HTTP POST /api/qq/send）：body {target_type, target_id, text,
    bot_uin?} → QQChannel.send(...) → 网关（NapCat / LLOneBot）发送。bot_uin
    可选：指定用哪个 bot（QQ 号）发送，缺省用默认通道；多账号部署时与入站
    消息的 bot_uin 对齐（谁收到谁回）。text 支持 OneBot CQ 码（如
    "[CQ:face,id=1]"、图片 URL）。发送成功后以 assistant 角色落盘，
    供 qq_read_conversation 读回。校验（target_type / target_id / 空文本）与
    wire 发送都在通道内完成。
    """
    ch = get_channel()
    if bot_uin:
        ch = get_channel_by_uin(bot_uin)
        if ch is None:
            return {"ok": False, "error": {
                "code": "unknown_bot_uin",
                "message": f"未注册的 bot_uin: {bot_uin}（通道未配置或未连接）"}}
    result = await ch.send(target_type, target_id, text)
    # 发送成功才落盘本次主动发送（assistant 角色），保持对话上下文完整；
    # bot_uin 标注本条由哪个 bot 发出（多账号；未配置时为 None 不写字段）
    if result.get("ok"):
        await _append_history(
            str(target_id), "assistant", text, bot_uin=ch.config.bot_uin
        )
    return result


async def api_send_file(
    target_type: str, target_id: str | int, file_path: str, name: str = "",
    bot_uin: str | int | None = None,
) -> dict:
    """发送一个文件到 QQ 会话（走当前通道抽象）。返回 {ok} 或错误。

    调用链（HTTP POST /api/qq/send_file）：body {target_type, target_id,
    file_path, name, bot_uin?} → QQChannel.upload_file(...) → 网关
    upload_private_file / upload_group_file。bot_uin 语义同 api_send（指定用
    哪个 bot 发送，缺省默认通道）。file_path 为本地绝对路径（须网关侧可读）或
    URL，name 缺省时由通道从路径推导。发送成功后以 assistant 角色落盘一条
    "[文件: 名字]"，供 qq_read_conversation 读回，与 api_send 的落盘做法一致。
    """
    ch = get_channel()
    if bot_uin:
        ch = get_channel_by_uin(bot_uin)
        if ch is None:
            return {"ok": False, "error": {
                "code": "unknown_bot_uin",
                "message": f"未注册的 bot_uin: {bot_uin}（通道未配置或未连接）"}}
    result = await ch.upload_file(target_type, target_id, file_path, name)
    if result.get("ok"):
        fname = (name or "").strip() or file_path
        await _append_history(
            str(target_id), "assistant", f"[文件: {fname}]", bot_uin=ch.config.bot_uin
        )
    return result


async def api_history(
    target_id: str, limit: int = 30, bot_uin: str | int | None = None
) -> dict:
    """Read a target's on-disk conversation log, newest-last, capped at limit.

    bot_uin 定位 bot 隔离的 history 文件（方案A）；缺省用默认通道 bot_uin；
    单通道旧数据走旧路径。
    """
    if limit is None or limit <= 0:
        limit = _HISTORY_MAX_ENTRIES
    bot = _resolve_read_bot_uin(bot_uin)
    messages = await _load_history(str(target_id), bot)
    messages = messages[-min(limit, _HISTORY_MAX_ENTRIES):]
    return {"target_id": str(target_id), "messages": messages}


async def api_recent_contacts(bot_uin: str | int | None = None) -> dict:
    """列出 QQ 联系人：近期会话合并完整好友/群（经通道抽象）。

    调用链：GET /api/qq/recent_contacts → QQChannel.recent_contacts() →
    网关 call_api 合并 get_recent_contact / get_friend_list / get_group_list。
    字段统一映射为 peerUin/peerName/chatType；peerUin 空/"0"、chatType 非 1/2 的
    异常条目剔除；名称缺失时兜底显示 QQ 号。单列表失败不致命；bot 未连接或全空
    才 ok:false。合并逻辑在通道内（OneBot 通用），切换通道不改行为。

    bot_uin 可选（多账号）：指定从哪个 bot（QQ 号）的通道拉取联系人；未注册 /
    未连接返回 ok:false（unknown_bot_uin）；缺省走默认通道（向后兼容）。
    结果按 bot_uin 做短 TTL 缓存（_CONTACTS_CACHE_TTL），避免 Postbox 每次打开
    都全量调网关 API；失败结果不缓存。
    """
    key = _contacts_cache_key(bot_uin)
    now = time.monotonic()
    cached = _contacts_cache.get(key)
    if cached and now - cached[0] < _CONTACTS_CACHE_TTL:
        return cached[1]
    if bot_uin:
        ch = get_channel_by_uin(bot_uin)
        if ch is None:
            return {"ok": False, "error": {
                "code": "unknown_bot_uin",
                "message": f"未注册的 bot_uin: {bot_uin}（通道未配置或未连接）"}}
        result = await ch.recent_contacts()
    else:
        result = await get_channel().recent_contacts()
    if result.get("ok"):
        _contacts_cache[key] = (now, result)
    return result


async def api_channels() -> dict:
    """列出当前注册的 QQ 通道（bot 账号维度），供前端 Postbox 合并展示来源账号。

    每项 {name, bot_uin, connected}：bot_uin 为空表示通道未配置 bot QQ 号
    （单通道兼容，不参与按号路由）；connected 为通道当前连接状态。
    """
    chans: list[QQChannel] = list(_qq_channels.iter_channels().values())
    active = _qq_channels.get_active_channel()
    if active is not None and not any(ch is active for ch in chans):
        chans.append(active)
    channels = []
    for ch in chans:
        bot_uin = getattr(ch.config, "bot_uin", None)
        try:
            connected = bool(await ch.is_connected())
        except Exception:
            connected = False
        channels.append({
            "name": ch.name,
            "bot_uin": str(bot_uin) if bot_uin else "",
            "connected": connected,
        })
    return {"ok": True, "channels": channels}


def _register_qq_api(app) -> None:
    """Mount QQ bot HTTP API routes on the given FastAPI app (NoneBot server_app)."""
    @app.post("/api/qq/send")
    async def _route_qq_send(body: dict):
        return await api_send(
            body.get("target_type", ""),
            body.get("target_id"),
            body.get("text", ""),
            body.get("bot_uin"),
        )

    @app.post("/api/qq/send_file")
    async def _route_qq_send_file(body: dict):
        return await api_send_file(
            body.get("target_type", ""),
            body.get("target_id"),
            body.get("file_path", ""),
            body.get("name", ""),
            body.get("bot_uin"),
        )

    @app.get("/api/qq/history")
    async def _route_qq_history(target_id: str, limit: int = 30, bot_uin: str = ""):
        return await api_history(target_id, limit, bot_uin or None)

    @app.get("/api/qq/recent_contacts")
    async def _route_qq_recent_contacts(bot_uin: str = ""):
        return await api_recent_contacts(bot_uin or None)

    @app.get("/api/qq/channels")
    async def _route_qq_channels():
        return await api_channels()

    @app.get("/api/qq/inbox")
    async def _route_qq_inbox(
        target_id: str, limit: int = 30, consume: int = 0, bot_uin: str = ""
    ):
        return await api_inbox(target_id, limit, bool(consume), bot_uin or None)

    @app.delete("/api/qq/inbox")
    async def _route_qq_inbox_clear(target_id: str, bot_uin: str = ""):
        return await api_inbox_clear(target_id, bot_uin or None)


# 仅在 fastapi driver 下挂载（nonebot2[fastapi] 默认 driver 即 fastapi）。
# getattr 兜底让本模块在非 fastapi driver / 单元测试环境下也能导入。
server_app = getattr(driver, "server_app", None)
if server_app is not None:
    _register_qq_api(server_app)
    print("[QQ Bridge] QQ HTTP API mounted on driver server_app")

