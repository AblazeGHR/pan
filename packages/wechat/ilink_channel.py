"""iLink 通道实现 —— WeChatChannel 的具体化（唯一 v1 实现）。

在整体链路中的位置::

    plugin.py → **本模块 ILinkChannel** → ilink.ILinkClient → 腾讯 iLink HTTP API
                  ↘ auth.BotSession（登录编排/会话过期重扫码）
                  ↘ store.ContextTokenStore（每用户最近 context_token）
                  ↘ store.outbox（无 token 时的发送缓冲）

与 QQ 通道（NapCat/LLOneBot 长连接）的本质差异：iLink 是**纯 HTTP + 长轮询**，
且协议级禁止主动推送 —— bot 只能在用户发来消息后，用该消息携带的
``context_token`` 回复。因此本通道：

1. ``startup()`` 登录后起一个长轮询任务，把 get_updates 的消息归一化为
   :class:`WeChatMessage` 派发给业务层，并把每个用户的最新 token 存进
   ContextTokenStore（这就是 selective 模式下异步回复的凭据来源）；
2. ``send()`` 从 ContextTokenStore 查 token 回发；**查不到 token 时降级**：
   写入 outbox 缓冲（待下次该用户上行消息刷新 token 后由业务层补发），
   同时仍按 ABC 约定返回 ``no_context_token`` 错误（附 ``buffered=True``，
   调用方据此前缀不再重复缓冲）；
3. 会话过期（errcode -14）在轮询与发送两侧都可能发生：轮询侧直接触发
   ``BotSession.require_relogin()``（进程不退出）；发送侧把它作为错误码
   透出，由业务层决定是否重试。
"""

from __future__ import annotations

import asyncio

from . import store
from .auth import BotSession
from .channel import WeChatChannel, WeChatMessage
from .ilink import (
    ILinkClient,
    ILinkError,
    extract_context_token,
    extract_nickname,
    extract_text,
    extract_user_id,
    is_bot_message,
)
from .ilink_spec import TYPING_CANCEL, TYPING_START

__all__ = ["ILinkChannel"]


class ILinkChannel(WeChatChannel):
    """iLink 接入的微信通道：长轮询收 + context_token 回发 + outbox 降级。"""

    name = "ilink"

    def __init__(
        self,
        client: ILinkClient,
        session: BotSession,
        *,
        token_store: store.ContextTokenStore | None = None,
        poll_interval: float = 1.0,
        error_backoff: float = 3.0,
    ) -> None:
        """
        client/session: 协议客户端与登录编排（bot.py 构造后注入）。
        token_store:    每用户 context_token 缓存；缺省用默认落盘路径。
        poll_interval:  一次成功 get_updates 之后的额外间隔（服务端本身会
                        挂起约 35s，正常节奏由长轮询决定，这只是兜底节流）。
        error_backoff:  轮询出错后的重试间隔。
        """
        super().__init__()
        self.client = client
        self.session = session
        self.tokens = (
            token_store if token_store is not None else store.ContextTokenStore()
        )
        self.poll_interval = poll_interval
        self.error_backoff = error_backoff
        self._cursor = ""  # get_updates_buf 游标（进程内存态，重启从头拉取）
        self._poll_task: asyncio.Task | None = None

    # ── 生命周期 ──

    async def startup(self) -> None:
        """登录（优先复用落盘凭证，否则二维码）后启动长轮询接收循环。

        注意：本方法会**阻塞**直到登录成功（二维码流程要等人扫）。因此 bot.py
        不应在 uvicorn lifespan 的 on_startup 里直接 await 它（那会卡住 8081
        监听），而应在后台任务里跑；见 bot.py 的 _on_startup。
        """
        await self.session.ensure_login()
        self.session.apply(self.client)
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(
                self._poll_loop(), name="ilink-poll"
            )

    async def shutdown(self) -> None:
        """停掉轮询任务并关闭 HTTP 客户端。"""
        task = self._poll_task
        self._poll_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.client.aclose()

    async def _poll_loop(self) -> None:
        """长轮询主循环：永不退出（错误退避重试），取消是唯一出口。

        会话过期是常态而非异常：收到 -14 就地 ``require_relogin()``（会阻塞
        循环直到重新扫码成功），进程保持存活 —— 与 auth.py 的设计约定一致。
        """
        while True:
            try:
                resp = await self.client.get_updates(self._cursor)
                await self._process_updates(resp)
            except ILinkError as exc:
                if exc.code == "session_expired":
                    print("[WeChat] 会话过期，重新扫码登录 …")
                    try:
                        await self.session.require_relogin()
                    except ILinkError as login_exc:
                        print(f"[WeChat] 重新登录失败: {login_exc}")
                else:
                    print(f"[WeChat] 轮询出错（{exc}），{self.error_backoff}s 后重试")
                await asyncio.sleep(self.error_backoff)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 循环必须活着
                print(f"[WeChat] 轮询意外异常（{exc!r}），{self.error_backoff}s 后重试")
                await asyncio.sleep(self.error_backoff)
            await asyncio.sleep(self.poll_interval)

    async def _process_updates(self, resp: dict) -> None:
        """处理一次 get_updates 响应：推进游标、归一化并派发每条消息。

        单条消息处理失败不影响其它消息（一条坏数据不该中断整批）。
        """
        buf = resp.get("get_updates_buf")
        if isinstance(buf, str) and buf:
            self._cursor = buf
        msgs = resp.get("msgs")
        if not isinstance(msgs, list):
            return
        for raw in msgs:
            if not isinstance(raw, dict) or is_bot_message(raw):
                continue  # bot 自己发的消息也回推，必须跳过防自激
            user_id = extract_user_id(raw)
            token = extract_context_token(raw)
            if user_id and token:
                # 收到上行即刷新该用户的回复凭据（outbox 补发靠它解锁）
                self.tokens.set(user_id, token)
            msg = WeChatMessage(
                scope="user",
                scope_id=user_id,
                text=extract_text(raw),
                sender_nickname=extract_nickname(raw),
                context_token=token,
                raw=raw,
            )
            try:
                await self._dispatch(msg)
            except Exception as exc:  # noqa: BLE001
                print(f"[WeChat] 消息处理回调异常（{exc!r}），继续下一条")

    # ── 出站 / 查询 ──

    async def send(self, target_type: str, target_id: str | int, text: str) -> dict:
        """回发一条文本：查 ContextTokenStore 取 token，缺 token 降级 outbox。

        返回 ``{"ok": True}`` 或 ``{"ok": False, "error": {...}}``。错误码：
            ``invalid_target_type`` v1 仅支持 "user"（iLink 只有 1:1）
            ``invalid_target_id``   目标为空
            ``empty_text``          文本为空
            ``not_connected``       未登录（无凭证）
            ``no_context_token``    该用户没有可用 token —— **已代为写入
                                    outbox 缓冲**（error.buffered=True），
                                    待下次上行补发；调用方无需重复缓冲
            其余                    iLink 协议错误码原样透出
                                   （session_expired / server_error / …）
        """
        if target_type != "user":
            return {"ok": False, "error": {
                "code": "invalid_target_type",
                "message": "微信 v1 仅支持 'user'（iLink 只有 1:1 会话）"}}
        target = str(target_id or "").strip()
        if not target:
            return {"ok": False, "error": {
                "code": "invalid_target_id", "message": "target_id 不能为空"}}
        body = str(text or "")
        if not body.strip():
            return {"ok": False, "error": {
                "code": "empty_text", "message": "text 不能为空"}}
        if not self.client.has_credential:
            return {"ok": False, "error": {
                "code": "not_connected", "message": "微信未登录（无凭证）"}}

        token = self.tokens.get(target)
        if not token:
            pending = store.append_outbox(target, body)
            return {"ok": False, "error": {
                "code": "no_context_token",
                "message": "该用户没有可用 context_token（iLink 不能主动推送），"
                           "已缓冲到 outbox 待其下次发消息后补发",
                "buffered": True,
            }, "outbox_pending": pending}

        try:
            await self.client.send_text(target, body, token)
        except ILinkError as exc:
            return {"ok": False, "error": {
                "code": exc.code, "message": exc.detail or exc.code}}
        return {"ok": True}

    async def recent_contacts(self) -> dict:
        """从本地落盘推导近期联系人（iLink 无联系人列表 API）。

        合并三个来源：wechat_history/、wechat_inbox/ 的文件名 + context_token
        缓存里的 user id。昵称 best-effort 取该用户最后一条带昵称的 inbox 记录，
        取不到回退 user id。``source`` 恒为 "local"（调用方需知晓这不是权威列表）。
        """
        ids: dict[str, str] = {}
        for dirname in ("wechat_inbox", "wechat_history"):
            base = store.data_root() / dirname
            if base.is_dir():
                for path in base.glob("*.json"):
                    ids.setdefault(path.stem, "")
        for user_id in self.tokens.all_ids():
            ids.setdefault(str(user_id), "")

        contacts = []
        for user_id in sorted(ids):
            nickname = ""
            for entry in reversed(store.load_inbox(user_id)):
                nick = entry.get("nickname") if isinstance(entry, dict) else ""
                if nick:
                    nickname = str(nick)
                    break
            contacts.append({
                "peerUin": user_id,
                "peerName": nickname or user_id,
                "chatType": 1,  # iLink v1 只有 1:1
            })
        return {"ok": True, "contacts": contacts, "source": "local"}

    async def send_typing(self, target_id: str | int, status: int) -> dict:
        """「正在输入」状态（best-effort）：get_config 取 ticket 后 sendtyping。

        status 传 1（开始）/ 2（取消）；其它值按 TYPING_START 处理。
        任何一步失败都返回 ``{"ok": False}`` 而不抛 —— 装饰性调用不打断主流程。
        """
        target = str(target_id or "").strip()
        token = self.tokens.get(target) if target else ""
        if not token:
            return {"ok": False, "error": {
                "code": "no_context_token",
                "message": "该用户没有可用 context_token，无法取 typing ticket"}}
        try:
            config = await self.client.get_config(target, token)
            ticket = str(config.get("typing_ticket") or "")
            if not ticket:
                return {"ok": False, "error": {
                    "code": "no_typing_ticket",
                    "message": f"getconfig 未返回 typing_ticket: {config}"}}
            return await self.client.send_typing(
                target, ticket, status if status in (TYPING_START, TYPING_CANCEL)
                else TYPING_START
            )
        except ILinkError as exc:
            return {"ok": False, "error": {
                "code": exc.code, "message": exc.detail or exc.code}}

    # ── 状态 / 辅助（业务层 plugin.py 使用）──

    async def is_connected(self) -> bool:
        """已登录且接收循环仍在跑。"""
        return bool(
            self.client.has_credential
            and self._poll_task is not None
            and not self._poll_task.done()
        )

    def get_token(self, user_id: str | int) -> str:
        """读取某用户当前缓存的 context_token（空串表示不可用）。"""
        return self.tokens.get(user_id)

    def has_fresh_token(self, user_id: str | int) -> bool:
        """该用户是否有「未过软 TTL」的 token —— outbox 补发前的预判依据。"""
        return bool(self.tokens.get(user_id)) and not self.tokens.is_stale(user_id)

    def cursor(self) -> str:
        """当前长轮询游标（排障 / 状态展示用）。"""
        return self._cursor

    def __repr__(self) -> str:
        return (
            f"<ILinkChannel authed={self.client.has_credential} "
            f"polling={self._poll_task is not None and not self._poll_task.done()} "
            f"cursor={self._cursor[:8]!r}>"
        )
