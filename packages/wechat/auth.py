"""微信 iLink 登录编排 + 凭证持久化。

在整体链路中的位置::

    plugin.py → WeChatChannel(ilink_channel.py) → **本模块 BotSession** → ilink.ILinkClient
                                                ↘ store（落盘 bot_token.json）

职责边界：:mod:`packages.wechat.ilink` 只管「一次 HTTP 怎么发」，本模块管
「怎么把二维码递给人、怎么轮询到登录成功、凭证怎么存/取」。
拿到 :class:`BotCredential` 后由 :meth:`BotSession.apply` 注入 client，
此后的收发与登录无关。

会话过期（iLink 返回 errcode -14）是常态而非异常 —— 微信个人号 token 会
周期性失效。:meth:`BotSession.require_relogin` 因此被设计成「清旧凭证 →
重新走一遍二维码流程 → 进程不退出」，业务层捕获后重试即可。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import store
from .ilink import ILinkClient, ILinkError

__all__ = ["BotCredential", "BotSession"]


@dataclass
class BotCredential:
    """登录凭证。落盘为 ``data/wechat/bot_token.json``。

    baseurl 由登录响应给出，可能与默认 base 不同（腾讯会分区域调度），
    因此必须随 token 一起持久化，否则重启后请求会打到错误域名。
    """

    bot_token: str
    baseurl: str = ""
    bot_id: str = ""
    obtained_at: float = 0.0

    def is_valid(self) -> bool:
        return bool(self.bot_token)

    def to_dict(self) -> dict:
        return {
            "bot_token": self.bot_token,
            "baseurl": self.baseurl,
            "bot_id": self.bot_id,
            "obtained_at": self.obtained_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BotCredential":
        """从落盘 dict 还原；字段缺失/改名的容错（协议字段尚未稳定）。"""
        data = data if isinstance(data, dict) else {}

        def _str(*keys: str) -> str:
            for key in keys:
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
            return ""

        raw_ts = data.get("obtained_at")
        try:
            obtained_at = float(raw_ts)
        except (TypeError, ValueError):
            obtained_at = 0.0
        return cls(
            bot_token=_str("bot_token", "token"),
            baseurl=_str("baseurl", "base_url"),
            bot_id=_str("bot_id"),
            obtained_at=obtained_at,
        )


class BotSession:
    """二维码登录编排：取码 → 展示 → 轮询状态机 → 落盘 → 注入 client。"""

    def __init__(
        self,
        client: ILinkClient,
        *,
        on_qrcode: Callable[[str, str], Awaitable[None] | None] | None = None,
        qrcode_timeout: float = 300.0,
        poll_interval: float = 2.0,
    ) -> None:
        """
        on_qrcode: ``(扫码URL, 轮询key)`` 回调，可同步也可协程，用于把二维码
                   展示给人类（打印到终端 / 转成图片）。二维码同时会落盘
                   ``data/wechat/login_qrcode.txt``，终端不可见时也能取用。
        qrcode_timeout: 整轮登录（含多次重新取码）的总时长上限（秒）。
        poll_interval: 扫码状态轮询间隔（秒）。
        """
        self._client = client
        self._on_qrcode = on_qrcode
        self.qrcode_timeout = qrcode_timeout
        self.poll_interval = poll_interval
        self._credential: BotCredential | None = None

    @property
    def credential(self) -> BotCredential | None:
        return self._credential

    # ── 对外主入口 ──

    async def ensure_login(self) -> BotCredential:
        """保证有可用凭证：优先用内存/落盘的，否则走完整二维码登录。

        落盘凭证即使「字段齐全」也不一定有效——用户可能在微信里解绑了
        ClawBot，旧 token 已被服务端作废。所以复用前先 ``verify_credential``
        探测一次：失效就清掉重走扫码，避免「假登录」（显示登录成功却不弹码、
        实际收不到消息）。
        """
        if self._credential is not None and self._credential.is_valid():
            if await self._client.verify_credential():
                return self._credential
            self._credential = None
            store.clear_credential()
        saved = BotCredential.from_dict(store.load_credential())
        if saved.is_valid():
            self._credential = saved
            self.apply(self._client)
            if await self._client.verify_credential():
                return saved
            # 落盘 token 已失效（解绑/过期）：清掉，重新扫码
            self._credential = None
            store.clear_credential()
        return await self._login()

    async def require_relogin(self) -> BotCredential:
        """丢弃旧凭证强制重新扫码（会话过期时调用），进程不退出。"""
        self._credential = None
        store.clear_credential()
        return await self._login()

    def apply(self, client: ILinkClient | None = None) -> None:
        """把当前凭证注入 client（默认构造时传入的那个）。"""
        target = client if client is not None else self._client
        if target is None or self._credential is None:
            return
        target.set_credential(
            self._credential.bot_token, self._credential.baseurl or None
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── 登录流程 ──

    async def _fetch_qrcode(self) -> str:
        """取码并展示（落盘 + 回调），返回轮询 key。"""
        data = await self._client.get_qrcode()
        url = str(data.get("qrcode_img_content") or "")
        # 先落盘再回调：回调可能阻塞（等人看/等渲染），二维码必须已经可取
        if url:
            store.save_login_qrcode(url)
        if self._on_qrcode is not None:
            result = self._on_qrcode(url, str(data.get("qrcode") or ""))
            if inspect.isawaitable(result):
                await result
        return str(data.get("qrcode") or "")

    async def _login(self) -> BotCredential:
        """二维码状态机：wait → scaned → confirmed；expired 则重新取码。

        整轮受 qrcode_timeout 约束；单次的取码/轮询失败只记下来继续重试
        （一次网络抖动不该终止登录），耗尽预算才抛 login_timeout。
        """
        deadline = time.monotonic() + self.qrcode_timeout
        qrcode_key = ""
        last_error = ""

        while True:
            if time.monotonic() >= deadline:
                raise ILinkError(
                    "login_timeout",
                    f"二维码登录超时（{self.qrcode_timeout}s）"
                    + (f"，最后一次错误: {last_error}" if last_error else ""),
                )

            if not qrcode_key:
                try:
                    qrcode_key = await self._fetch_qrcode()
                except ILinkError as exc:
                    # 取码失败多半是网络抖动，等一个间隔再来
                    qrcode_key = ""
                    last_error = str(exc)
                    await asyncio.sleep(self.poll_interval)
                    continue

            try:
                status = await self._client.get_qrcode_status(qrcode_key)
            except ILinkError as exc:
                last_error = str(exc)
                await asyncio.sleep(self.poll_interval)
                continue
            except Exception as exc:  # noqa: BLE001 —— 任何非预期异常都当轮询失败重试
                # 长轮询超时已在 get_qrcode_status 内转成 wait；这里拦住其它漏网
                # 异常，避免一次抖动就整轮登录崩溃、二维码被迫刷新。
                last_error = str(exc)
                await asyncio.sleep(self.poll_interval)
                continue

            state = str(status.get("status") or "unknown")
            if state == "confirmed":
                token = str(status.get("bot_token") or "")
                if not token:  # confirmed 却没 token：当坏响应处理，重新取码
                    qrcode_key = ""
                    last_error = "confirmed 但未返回 bot_token"
                    await asyncio.sleep(self.poll_interval)
                    continue
                credential = BotCredential(
                    bot_token=token,
                    baseurl=str(status.get("baseurl") or ""),
                    obtained_at=time.time(),
                )
                store.save_credential(credential.to_dict())
                store.clear_login_qrcode()
                self._credential = credential
                self.apply(self._client)
                return credential

            if state == "expired":
                qrcode_key = ""  # 下一轮重新取码
            # wait / scaned / unknown：继续轮询

            await asyncio.sleep(self.poll_interval)

    def __repr__(self) -> str:
        state = "logged-in" if (self._credential and self._credential.is_valid()) else "anonymous"
        return f"<BotSession {state}>"
