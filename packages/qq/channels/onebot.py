"""OneBot 11 通道基类：NapCat 与 LLOneBot 共用的正向 WS 接入实现。

两者都是 OneBot 11 协议网关，NoneBot 的 OneBot v11 适配器以正向 WS 客户端连接
到它们的 WS 服务端（ws://127.0.0.1:<port>）。因此「收发 / 取联系人」的 wire 层
完全一致，差异仅在：通道标识（name）、连接地址（ws_urls）、token、以及启动预检
文案。NapCat / LLOneBot 各用薄子类提供这些差异，业务层零改动即可切换。

本基类负责：
    - 绑定 NoneBot driver：注册 on_bot_connect / on_bot_disconnect / on_message
    - 维护当前活动的 Bot（self._bot，由 on_bot_connect 注入；测试可经 bot_fallback）
    - send / recent_contacts：经 bot.call_api 调 OneBot API（合并好友/群列表）
    - 启动时 TCP 预检：网关不可达则打降级日志（适配器仍每 3s 重试，进程不退出）
"""

from __future__ import annotations

import socket
from typing import Callable
from urllib.parse import urlparse

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from .base import (
    ChannelConfig,
    ChannelNotConnected,
    QQChannel,
    QQMessage,
)

# chatType：1=私聊/好友，2=群聊；其它（临时会话/陌生人/系统/频道，peerUin 常为 "0"）
# 不可订阅，合并时忽略。
_CHAT_FRIEND = 1
_CHAT_GROUP = 2


class OneBotChannel(QQChannel):
    """OneBot 11 网关通道基类（NapCat / LLOneBot 共用）。"""

    def __init__(
        self,
        config: ChannelConfig,
        *,
        bot_fallback: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(config)
        self._bot: Bot | None = None
        # 测试/兼容：self._bot 为 None 时的 bot 获取回调（如返回 plugin._active_bot）
        self._bot_fallback = bot_fallback
        self._bound = False

    # ── 绑定 NoneBot driver ──

    def bind(self, driver=None) -> None:
        """绑定 NoneBot driver：注册连接/断开/消息 hook + 启动预检。幂等。"""
        if self._bound:
            return
        driver = driver or get_driver()
        driver.on_bot_connect(self._on_bot_connect)
        driver.on_bot_disconnect(self._on_bot_disconnect)
        driver.on_startup(self._startup_check)
        handler = on_message()
        handler.handle()(self._on_message)
        self._bound = True

    async def _on_bot_connect(self, bot: Bot) -> None:
        self._bot = bot
        print(f"[QQ][{self.name}] bot connected: {getattr(bot, 'self_id', '?')}")

    async def _on_bot_disconnect(self, bot: Bot) -> None:
        if self._bot is bot:
            self._bot = None
        print(f"[QQ][{self.name}] bot disconnected: {getattr(bot, 'self_id', '?')}")

    async def _startup_check(self) -> None:
        """启动预检：网关 WS 不可达则打降级日志（适配器持续每 3s 重试）。"""
        urls = self.ws_urls()
        if not urls:
            return
        if not any(self._tcp_reachable(u) for u in urls):
            print(
                f"[QQ][{self.name}] 网关未连接，QQ 模块降级运行"
                f"（进程保持存活，每 3s 自动重试连接）"
            )

    @staticmethod
    def _tcp_reachable(url: str, timeout: float = 1.0) -> bool:
        """对 ws(s):// URL 做廉价 TCP 连通性测试。"""
        try:
            p = urlparse(url)
            port = p.port or (443 if p.scheme == "wss" else 80)
            with socket.create_connection((p.hostname, port), timeout=timeout):
                return True
        except OSError:
            return False

    # ── 入站消息：OneBot event → QQMessage → 业务层 ──

    async def _on_message(self, bot: Bot, event: MessageEvent) -> None:
        # 缓存活动 bot（即使 on_bot_connect 未触发也能用）
        self._bot = bot
        # 群消息：OneBot v11 的 GroupMessageEvent.message_type == "group"
        # （用 message_type 而非 isinstance，便于测试与非标准 event 对象）
        if getattr(event, "message_type", None) == "group":
            # OneBot v11 协议 At.data["qq"] 是字符串（NapCat 实发亦为 str），
            # 两侧统一 str() 归一后比较，避免 int vs str 恒 False 漏掉所有 @
            bot_qq = str(bot.self_id)
            at_qqs = [
                str(seg.data.get("qq", ""))
                for seg in getattr(event, "message", [])
                if getattr(seg, "type", None) == "at"
            ]
            # 群消息仅处理 @ 本 bot 的（与重构前行为一致）；未 @ 直接丢弃
            if bot_qq not in at_qqs:
                return
            scope, scope_id = "group", str(event.group_id)
        else:
            scope, scope_id = "user", str(event.get_user_id())
        text = event.get_plaintext().strip()
        if not text:
            return
        sender = getattr(event, "sender", None)
        nickname = getattr(sender, "nickname", "") or ""
        msg = QQMessage(
            scope=scope,
            scope_id=scope_id,
            text=text,
            sender_nickname=nickname,
            at_bot=True,
            raw=getattr(event, "model_dump", lambda: None)(),
        )
        await self._dispatch(msg)

    # ── bot 解析 ──

    def _resolve_bot(self) -> Bot:
        if self._bot is not None:
            return self._bot
        if self._bot_fallback is not None:
            b = self._bot_fallback()
            if b is not None:
                return b
        raise ChannelNotConnected("QQ bot 未连接（网关尚未建立连接）")

    # ── 出站 / 查询 ──

    async def send(
        self, target_type: str, target_id: str | int, text: str
    ) -> dict:
        if target_type not in ("private", "group"):
            return {"ok": False, "error": {
                "code": "invalid_target_type",
                "message": "target_type 必须是 'private' 或 'group'"}}
        try:
            target_id_int = int(target_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": {
                "code": "invalid_target_id",
                "message": f"target_id 必须是 QQ 号/群号，got {target_id!r}"}}
        if not text:
            return {"ok": False, "error": {
                "code": "empty_text", "message": "text 不能为空"}}
        try:
            bot = self._resolve_bot()
            api = "send_private_msg" if target_type == "private" else "send_group_msg"
            params = (
                {"user_id": target_id_int}
                if target_type == "private"
                else {"group_id": target_id_int}
            )
            result = await bot.call_api(api, **params, message=text)
        except ChannelNotConnected as e:
            return {"ok": False, "error": {
                "code": "bot_not_connected", "message": str(e)}}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": {
                "code": "send_failed",
                "message": f"{type(e).__name__}: {e}"}}
        if isinstance(result, dict):
            return {"ok": True, "message_id": result.get("message_id")}
        return {"ok": True, "message_id": result}

    async def recent_contacts(self) -> dict:
        try:
            bot = self._resolve_bot()
        except ChannelNotConnected as e:
            return {"ok": False, "error": {
                "code": "bot_not_connected", "message": str(e)}}

        async def _call(api: str, params: dict | None = None):
            try:
                return await bot.call_api(api, **(params or {})), None
            except Exception as e:  # noqa: BLE001  # best-effort per API
                return None, f"{type(e).__name__}: {e}"

        # 1) 近期会话（best-effort；带 count 尽量多取，不支持则重试无参）
        recent_items: list = []
        recent_err: str | None = None
        result, err = await _call("get_recent_contact", {"count": 50})
        if err:
            result, err = await _call("get_recent_contact")
        if err:
            recent_err = err
        else:
            recent_items = self._api_result_list(result)

        # 2) 完整好友/群列表（补全缺失名称 + 补齐非近期会话）
        friends: dict[str, str] = {}
        result, _ = await _call("get_friend_list")
        for f in self._api_result_list(result):
            uin = self._clean_peer_uin(f.get("user_id"))
            if uin is None:
                continue
            name = (f.get("remark") or "").strip() or (f.get("nickname") or "").strip()
            friends[uin] = name

        groups: dict[str, str] = {}
        result, _ = await _call("get_group_list")
        for g in self._api_result_list(result):
            gid = self._clean_peer_uin(g.get("group_id"))
            if gid is None:
                continue
            groups[gid] = (g.get("group_name") or "").strip()

        # 3) 合并去重：近期优先 → 好友 → 群
        merged: list[dict] = []
        seen: set[tuple[int, str]] = set()

        def _add(chat_type: int, uin: str, name: str) -> None:
            key = (chat_type, uin)
            if key in seen:
                return
            seen.add(key)
            merged.append({
                "peerUin": uin,
                "peerName": name or uin,
                "chatType": chat_type,
            })

        for it in recent_items:
            chat_type = it.get("chatType")
            if chat_type not in (_CHAT_FRIEND, _CHAT_GROUP):
                continue
            uin = self._clean_peer_uin(it.get("peerUin"))
            if uin is None:
                continue
            name = (it.get("peerName") or "").strip() or (it.get("remark") or "").strip()
            if not name:
                name = friends.get(uin) if chat_type == _CHAT_FRIEND else groups.get(uin)
            _add(chat_type, uin, name or "")

        for uin, name in friends.items():
            _add(_CHAT_FRIEND, uin, name)
        for gid, name in groups.items():
            _add(_CHAT_GROUP, gid, name)

        if not merged and recent_err:
            return {"ok": False, "error": {
                "code": "unsupported",
                "message": f"{recent_err}（get_recent_contact 不可用且无好友/群数据）"}}
        return {"ok": True, "contacts": merged}

    # ── 状态 ──

    async def is_connected(self) -> bool:
        return self._bot is not None

    async def startup(self) -> None:
        # 正向 WS 由 NoneBot OneBot 适配器在运行时自动连接；此处仅做预检提示。
        await self._startup_check()

    async def shutdown(self) -> None:
        self._bot = None

    # ── OneBot 结果兼容工具 ──

    @staticmethod
    def _clean_peer_uin(value) -> str | None:
        """规范化 peer uin 为非零数字串；缺失 / "0" 占位返回 None。"""
        if value is None:
            return None
        s = str(value).strip()
        if not s or s == "0":
            return None
        return s

    @staticmethod
    def _api_result_list(result) -> list:
        """从 call_api 结果（已解包 list 或 dict）提取 list，兼容嵌套 data。"""
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                nested = data.get("data")
                if isinstance(nested, list):
                    return nested
        return []
