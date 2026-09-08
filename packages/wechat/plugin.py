"""Pan WeChat 业务编排 —— 把 iLink 通道桥接到 Pan Core。

在整体链路中的位置::

    ilink_channel.ILinkChannel → **本模块 WeChatPlugin** → Pan Core HTTP API（main.py 起，
                                ↘ store（history/inbox/outbox 落盘）     端口默认 8768）
                                ↘ server.py（本插件 8081 HTTP API，供 Pan Core 代理与
                                            packages/wechat/mcp.py 调用）

与 packages/qq/plugin.py 平行，但有两点结构性不同：

1. 不用 nonebot —— iLink 是纯 HTTP 长轮询，通道生命周期由本包 bot.py 直接驱动，
   本模块是普通类（``WeChatPlugin``），无 import 副作用，单测友好；
2. 双模式语义与 QQ 相同（PAN_WECHAT_MODE / config.json wechat.mode）：
     mirror    默认。收到消息 → 绑定 Pan session → 派发 worker → 自动回复；
     selective 消息只写 history + inbox（待处理队列）并通知 Pan Core（
               /api/wechat/notify，订阅了该会话的 Pan session 收到
               ``@@@@by wechat`` 提醒），由 meta-agent 经 wechat MCP 工具
               （wechat_read_inbox → 决策 → wechat_send_message）决定回不回。
   command-routes（绕过 LLM 的确定路由）在两种模式下都立即执行。

镜像 session 编排（mirror 模式）：session 命名 ``wx-<scope_id 尾 6 位>``，经
/api/sessions 创建、/api/spawn 派发、/api/task 投递、轮询 /api/sessions/<id>
的 lastResult 取回复（QQ 版另有 WS 推送主路径，微信 v1 只保留轮询回退，
iLink 回复时效要求宽松，1.5s 轮询足够）。

outbox 补发：send 缺 context_token 时通道已把消息缓冲进 outbox；本模块在每次
收到该用户新上行（token 已被通道刷新）后调用 :meth:`flush_outbox` 按序补发。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from . import store
from .channel import WeChatChannel, WeChatMessage

__all__ = ["WeChatPlugin", "load_config", "wechat_mode", "pan_core_url"]

POLL_INTERVAL = 1.5   # mirror 模式取回复的轮询间隔（秒）
MAX_POLL_TIME = 120.0  # 单条消息等待 worker 回复的上限（秒）


# ── 配置 ──

def load_config() -> dict:
    """读项目根 config.json；读不到返回空 dict（不抛，缺失字段走默认值）。"""
    try:
        path = Path(__file__).resolve().parent.parent.parent / "config.json"
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def wechat_mode(wechat_cfg: dict | None = None) -> str:
    """微信桥接模式："mirror"（默认）或 "selective"。

    优先级：环境变量 PAN_WECHAT_MODE > config.json 的 wechat.mode > "mirror"
    （与 QQ 的 PAN_QQ_MODE 语义一致）；非法值回退 mirror。
    """
    cfg = wechat_cfg if wechat_cfg is not None else (load_config().get("wechat") or {})
    cfg_mode = cfg.get("mode", "") if isinstance(cfg, dict) else ""
    mode = os.getenv("PAN_WECHAT_MODE", str(cfg_mode)).strip().lower()
    return mode if mode in ("mirror", "selective") else "mirror"


def pan_core_url() -> str:
    """Pan Core base URL：PAN_CORE_API_URL 环境变量优先，否则 config.json 的 port。"""
    env_url = os.getenv("PAN_CORE_API_URL")
    if env_url:
        return env_url.rstrip("/")
    port = load_config().get("port") or 8768
    return f"http://127.0.0.1:{port}"


def _clean_reply(text: str) -> str:
    """剥掉 worker 结果里的工具调用装饰行（与 QQ 版同一约定）。"""
    lines = [ln for ln in str(text).split("\n") if not ln.startswith("🔧")]
    return "\n".join(lines).strip()


class WeChatPlugin:
    """微信业务编排：入站消息 → 落盘/提醒/回复；出站经通道 + 补发 outbox。

    一个实例对应一个通道（当前单通道 ilink；多账号将来在此扩展）。所有
    ``api_*`` 方法同时是 8081 HTTP API（server.py）的处理器，MCP 工具经
    HTTP 调到它们，因此这里不做鉴权以外的前置假设。
    """

    def __init__(
        self,
        channel: WeChatChannel,
        *,
        core_url: str | None = None,
        mode: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        poll_interval: float = POLL_INTERVAL,
        max_poll_time: float = MAX_POLL_TIME,
    ) -> None:
        self.channel = channel
        self.core_url = (core_url or pan_core_url()).rstrip("/")
        self.mode = (mode or wechat_mode()).strip().lower()
        if self.mode not in ("mirror", "selective"):
            self.mode = "mirror"
        # 与 Pan Core / command-route 目标的全部 HTTP 都走这一个 client；
        # transport 可注入（httpx.MockTransport）即零网络单测。
        self._http = httpx.AsyncClient(timeout=10, transport=transport)
        self.poll_interval = poll_interval
        self.max_poll_time = max_poll_time

        # scope_key ("user:<id>") → {"session_id", "worker_id", "seq", "ts"}
        self._sessions: dict[str, dict] = {}
        # command-routes：[(prefixes, target)]，最长前缀优先；空 = 无路由
        self._command_routes: list[tuple[list[str], str]] = []
        self._routes_loaded = False

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── 入站主入口（通道回调）──

    async def handle_wechat_message(self, msg: WeChatMessage) -> None:
        """业务层入站处理：落盘 → outbox 补发 → selective 入队/提醒 → 回复。

        由 ILinkChannel 的接收循环在归一化消息后回调；此时尚未做任何落盘，
        但该用户的 context_token 已被通道刷新（补发的解锁条件）。
        """
        text = (msg.text or "").strip()
        if not text or not msg.scope_id:
            return
        scope, scope_id = msg.scope, msg.scope_id
        target_type = msg.target_type()

        # 1. outbox 补发：这条上行刚刷新了 token，之前发不出去的积压按序补出
        await self.flush_outbox(scope_id)

        # 2. 落盘用户消息（user/assistant 双侧，供 /api/wechat/history 读取）
        store.append_history(scope_id, "user", text)

        # 3. selective 模式：入 inbox（待处理队列）+ 通知 Pan Core（订阅提醒）；
        #    不建 session / 不 spawn / 不自动回复。
        if self.mode == "selective":
            store.append_inbox(scope_id, text, scope=scope,
                               nickname=msg.sender_nickname)
            await self._notify_core(msg)

        # 4. command-routes（两种模式都执行）：命中即直连外部 HTTP 目标并结束
        #    （镜像 QQ 版：命中 route 后不再走 mirror 自动回复）
        if await self._maybe_command_route(msg, text, scope_id):
            return

        # 5. mirror 模式：绑定 session → 派发 worker → 等回复 → 回发
        if self.mode != "selective":
            # 不发「processing, please wait...」等待占位：好友只应收到最终回复
            response = await self._send_and_wait(text, scope_id, scope)
            store.append_history(scope_id, "assistant", response)
            await self.channel.send(target_type, scope_id, response)

    # ── outbox 补发 ──

    async def flush_outbox(self, scope_id: str) -> int:
        """把该用户 outbox 积压按序补发（成功才出队，保序；失败即停）。

        前置条件是 token 新鲜（has_fresh_token）：不新鲜时补发大概率失败，
        还会把积压重排（通道 send 失败不回写 outbox），索性整批留到下次。
        返回成功补发的条数。
        """
        pending = store.load_outbox(scope_id)
        if not pending or not self.channel.has_fresh_token(scope_id):
            return 0
        sent = 0
        for item in pending:
            body = str(item.get("text") or "")
            result = await self.channel.send("user", scope_id, body)
            if not result.get("ok"):
                break  # 保序：一条发不出去，后面的都留着
            sent += 1
            store.append_history(scope_id, "assistant", body)
        if sent:
            fresh = store.load_outbox(scope_id)
            # load_outbox 可能已顺带丢弃过期项，所以重新读再裁掉前 sent 条
            store._atomic_write_json(store.outbox_path(scope_id), fresh[sent:])
        return sent

    # ── Pan Core 交互 ──

    async def _notify_core(self, msg: WeChatMessage) -> None:
        """Best-effort 通知 Pan Core：inbox 有新消息（/api/wechat/notify）。

        订阅了该微信会话的 Pan session 会收到 ``@@@@by wechat`` 提醒。
        can_reply 取决于该用户当前是否有未过软 TTL 的 context_token：
        False 时提醒文本多一行 ``canReply: false``，提示编排者此刻回不出去。
        通知失败只打警告 —— inbox 文件才是真源，Core 不可达不阻断落盘。
        """
        payload = {
            "target_type": msg.scope,
            "target_id": msg.scope_id,
            "nickname": msg.sender_nickname,
            "text": msg.text,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "can_reply": self.channel.has_fresh_token(msg.scope_id),
        }
        try:
            resp = await self._http.post(f"{self.core_url}/api/wechat/notify",
                                         json=payload)
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            print(f"[WeChat] notify Pan Core 失败（非致命）: {type(e).__name__}: {e}")

    async def _get(self, path: str) -> dict:
        try:
            r = await self._http.get(f"{self.core_url}{path}")
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            print(f"[WeChat] GET {path} 失败: {type(e).__name__}: {e}")
            return {"error": str(e)}

    async def _post(self, path: str, data: dict | None = None) -> dict:
        try:
            r = await self._http.post(f"{self.core_url}{path}", json=data or {})
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            print(f"[WeChat] POST {path} 失败: {type(e).__name__}: {e}")
            return {"error": str(e)}

    # ── command-routes（绕过 LLM 的确定路由，与 QQ 版同构）──

    async def _refresh_command_routes(self) -> None:
        """从 Pan Core manifest 拉取 command_routes；失败缓存为空（不致命）。"""
        data = await self._get("/api/manifest/command-routes")
        routes: list[tuple[list[str], str]] = []
        if isinstance(data, dict) and isinstance(data.get("routes"), list):
            for item in data["routes"]:
                prefixes = list(item.get("prefixes", []))
                target = item.get("target", "")
                if prefixes and target:
                    routes.append((prefixes, target))
        routes.sort(key=lambda rt: max(len(p) for p in rt[0]), reverse=True)
        self._command_routes = routes
        self._routes_loaded = True
        print(f"[WeChat] 已加载 {len(routes)} 组 command route")

    def _match_command_route(self, text: str) -> tuple[str, str] | None:
        """返回 (target_url, 剥掉前缀后的文本)；未命中返回 None。"""
        for prefixes, target in self._command_routes:
            for prefix in prefixes:
                if text.startswith(prefix):
                    return target, text[len(prefix):].lstrip()
        return None

    async def _maybe_command_route(
        self, msg: WeChatMessage, text: str, scope_id: str
    ) -> bool:
        """命中 command-route 时立即执行并回发；返回是否命中。"""
        if not self._routes_loaded:
            await self._refresh_command_routes()
        match = self._match_command_route(text)
        if not match:
            return False
        target, body = match
        # 不发等待占位提示：好友只应收到最终回复
        try:
            r = await self._http.post(target, json={"text": body})
            r.raise_for_status()
            payload = r.json()
            response = (
                payload.get("result")
                or payload.get("text")
                or payload.get("message")
                or (payload if isinstance(payload, str)
                    else json.dumps(payload, ensure_ascii=False))
            )
        except Exception as e:  # noqa: BLE001
            response = f"[Pan] command route error: {type(e).__name__}: {e}"
        store.append_history(scope_id, "assistant", response)
        await self.channel.send(msg.target_type(), scope_id, response)
        return True

    # ── mirror 模式：Pan session 编排 ──

    def _session_key(self, scope: str, scope_id: str) -> str:
        return f"{scope}:{scope_id}"

    @staticmethod
    def _session_name(scope_id: str) -> str:
        """每好友一个稳定且唯一的 session 名。

        早期用 ``wx-<id 尾6位>``，不同好友尾 6 位碰撞会串号（A 的回复发到 B 的
        会话）。改为对完整 id 取短哈希，碰撞概率可忽略，且能从名字反查归属。
        """
        import hashlib
        h = hashlib.sha1(str(scope_id).encode("utf-8")).hexdigest()[:12]
        return f"wx-{h}"

    async def _ensure_session(self, scope_id: str, scope: str = "user") -> str | None:
        """绑定（或创建）该微信会话对应的 Pan session，返回 session_id。

        与 QQ 版同构：缓存命中且 worker 活着直接用；死了 re-spawn；没有就按
        名字 ``wx-<完整id哈希>`` 认领旧 session，最后才新建。每个好友独立
        session，互不串号，支持多好友并发对话。
        """
        key = self._session_key(scope, scope_id)
        cached = self._sessions.get(key)
        if cached and cached.get("session_id"):
            session_id = cached["session_id"]
            data = await self._get(f"/api/sessions/{session_id}")
            if "error" not in data:
                worker_id = data.get("workerId")
                if not worker_id:
                    spawned = await self._post("/api/spawn",
                                               {"sessionId": session_id})
                    if "error" not in spawned:
                        worker_id = spawned.get("workerId")
                    else:
                        print(f"[WeChat] re-spawn worker 失败: {spawned['error']}")
                cached["worker_id"] = worker_id
                return session_id
            self._sessions.pop(key, None)  # 缓存失效（session 被删）

        name = self._session_name(scope_id)
        existing = await self._get("/api/sessions")
        if isinstance(existing, dict) and isinstance(existing.get("sessions"), list):
            for item in existing["sessions"]:
                if str(item.get("name", "")) == name:
                    lr = item.get("lastResult") or {}
                    state = {
                        "session_id": item["id"],
                        "worker_id": item.get("workerId"),
                        # 认领时播种 lastResult 游标，避免把旧结果当新回复
                        "seq": lr.get("taskSeq") if isinstance(lr.get("taskSeq"), int) else 0,
                        "ts": lr.get("timestamp", "") or "",
                    }
                    self._sessions[key] = state
                    if not state["worker_id"]:
                        spawned = await self._post("/api/spawn",
                                                   {"sessionId": state["session_id"]})
                        if "error" not in spawned:
                            state["worker_id"] = spawned.get("workerId")
                    return state["session_id"]

        created = await self._post("/api/sessions", {"name": name})
        if "error" in created or not created.get("id"):
            print(f"[WeChat] 创建 session 失败: {created.get('error')}")
            return None
        session_id = created["id"]
        spawned = await self._post("/api/spawn", {"sessionId": session_id})
        worker_id = None if "error" in spawned else spawned.get("workerId")
        if "error" in spawned:
            print(f"[WeChat] spawn worker 失败: {spawned['error']}")
        self._sessions[key] = {
            "session_id": session_id, "worker_id": worker_id, "seq": 0, "ts": "",
        }
        return session_id

    async def _send_and_wait(self, text: str, scope_id: str,
                             scope: str = "user") -> str:
        """投递任务给 worker 并轮询等回复（纯 HTTP，无 WS 推送）。

        不拼接历史：每个好友对应一个**持久 Pan session**，worker 自身保有
        完整对话历史（模型自带上下文），重复发送历史既浪费 token 又会稀释
        当前消息。落盘历史（store）仅用于 /api/wechat/history 读取与排障。
        """
        session_id = await self._ensure_session(scope_id, scope)
        if not session_id:
            return "[Pan] cannot create session"

        result = await self._post("/api/task",
                                  {"sessionId": session_id, "text": text})
        if "error" in result:
            return f"[Pan] error: {result['error']}"

        state = self._sessions.get(self._session_key(scope, scope_id)) or {}
        deadline = time.monotonic() + self.max_poll_time
        while time.monotonic() < deadline:
            await asyncio.sleep(self.poll_interval)
            data = await self._get(f"/api/sessions/{session_id}")
            if "error" in data:
                continue
            if not data.get("workerId") or data.get("workerStatus") == "error":
                return "[Pan] worker stopped"
            lr = data.get("lastResult") or {}
            seq = lr.get("taskSeq")
            if isinstance(seq, int) and seq > int(state.get("seq") or 0):
                state["seq"] = seq
                break
            ts = lr.get("timestamp", "") or ""
            if not isinstance(seq, int) and ts and ts != state.get("ts", ""):
                state["ts"] = ts
                break
        else:
            return "[Pan] response timeout"

        data = await self._get(f"/api/sessions/{session_id}")
        if "error" in data:
            return "[Pan] cannot get response"
        lr = data.get("lastResult") or {}
        reply = _clean_reply(lr.get("result", "") or "")
        if reply:
            return reply
        for entry in reversed(data.get("history", []) or []):
            if entry.get("role") == "assistant":
                reply = _clean_reply(entry.get("content", ""))
                if reply:
                    return reply
        return "[Pan] no response"

    # ── HTTP API 处理器（server.py 挂载；MCP 工具的最终落点）──

    async def api_send(self, target_type: str, target_id: str | int,
                       text: str) -> dict:
        """发送一条微信消息（经通道；成功后以 assistant 落盘供读回）。

        no_context_token 时通道已缓冲到 outbox（error.buffered=True），
        这里照实透出 —— 编排者可据此告知用户「消息已排队，等他下次联系时送达」。
        """
        result = await self.channel.send(target_type, target_id, text)
        if result.get("ok"):
            store.append_history(str(target_id), "assistant", text)
        return result

    async def api_history(self, target_id: str, limit: int = 30) -> dict:
        """读某会话的落盘对话记录（最新在后，上限 500）。"""
        limit = store.HISTORY_MAX_ENTRIES if not limit or limit <= 0 else min(limit, store.HISTORY_MAX_ENTRIES)
        messages = store.load_history(str(target_id))[-limit:]
        return {"target_id": str(target_id), "messages": messages}

    async def api_inbox(self, target_id: str, limit: int = 30,
                        consume: bool = False) -> dict:
        """读某会话的待处理消息（selective 模式）；consume=True 消费即删。"""
        limit = store.INBOX_MAX_ENTRIES if not limit or limit <= 0 else min(limit, store.INBOX_MAX_ENTRIES)
        target_id = str(target_id)
        if consume:
            take = store.consume_inbox(target_id, limit)
        else:
            take = store.take_inbox(target_id, limit)
        return {"target_id": target_id, "messages": take}

    async def api_inbox_clear(self, target_id: str) -> dict:
        """清空某会话的 inbox。"""
        target_id = str(target_id)
        existed = store.clear_inbox(target_id)
        return {"ok": True, "target_id": target_id, "cleared": existed}

    async def api_recent_contacts(self) -> dict:
        """近期联系人（经通道推导，iLink 无权威联系人列表）。"""
        return await self.channel.recent_contacts()

    async def api_channels(self) -> dict:
        """列出微信通道（与 QQ /api/qq/channels 平行；单通道，bot_uin 恒空）。"""
        try:
            connected = bool(await self.channel.is_connected())
        except Exception:  # noqa: BLE001
            connected = False
        return {"ok": True, "channels": [
            {"name": self.channel.name, "bot_uin": "", "connected": connected}
        ]}

    async def api_status(self) -> dict:
        """微信桥状态（MCP wechat_status 的后端）：登录态/模式/outbox 积压。"""
        logged_in = bool(getattr(self.channel, "client", None)
                         and self.channel.client.has_credential)
        try:
            connected = bool(await self.channel.is_connected())
        except Exception:  # noqa: BLE001
            connected = False
        outbox_dir = store.data_root() / "wechat_outbox"
        outbox = []
        if outbox_dir.is_dir():
            for path in sorted(outbox_dir.glob("*.json")):
                pending = store.load_outbox(path.stem)
                if pending:
                    outbox.append({"target_id": path.stem,
                                   "pending": len(pending)})
        return {
            "ok": True,
            "channel": getattr(self.channel, "name", "ilink"),
            "logged_in": logged_in,
            "connected": connected,
            "mode": self.mode,
            "outbox": outbox,
        }

    async def api_typing(self, target_id: str | int, status: int = 1) -> dict:
        """「正在输入」状态（best-effort，经通道）。"""
        return await self.channel.send_typing(target_id, status)
