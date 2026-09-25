"""iLink 协议客户端 —— 微信通道的最底层。

在整体链路中的位置::

    业务层 plugin.py → WeChatChannel(ilink_channel.py) → **本模块 ILinkClient** → 腾讯 iLink HTTP API
                                                       ↘ auth.BotSession（登录编排）

本模块**只做协议**：拼 URL、拼 header、发 HTTP、解析 JSON、做容错字段提取。
它不知道 Agent、不知道落盘（store 由 auth.py 负责）、不认识
:class:`packages.wechat.channel.WeChatMessage`（归一化是业务层的事）。

两条设计硬约束：

1. **不写字面量路径** —— 所有 URL 经 :meth:`ILinkSpec.path`，协议漂移只改
   ``ilink_spec.py``（或 config.json / 环境变量），本文件不动。
2. **transport 可注入** —— ``ILinkClient(transport=httpx.MockTransport(...))``
   即可零网络单测，不必 bind 端口或起 mock server。

协议较新，服务端字段可能与文档有偏差，因此所有解析函数（``extract_*`` /
``is_ok`` / ``is_session_expired``）一律「缺失即空、不抛异常」，
把坏数据挡在这一层，而不是让业务层到处 try/except。
"""

from __future__ import annotations

import base64
import secrets
import uuid
from dataclasses import dataclass

import httpx

from .ilink_spec import (
    ITEM_TYPE_TEXT,
    MESSAGE_TYPE_BOT,
    OUT_MESSAGE_STATE,
    OUT_MESSAGE_TYPE,
    SESSION_EXPIRED_ERRCODE,
    ILinkSpec,
    load_spec,
)

__all__ = [
    "ILinkError",
    "ILinkClient",
    "make_uin",
    "is_session_expired",
    "is_ok",
    "extract_text",
    "extract_user_id",
    "extract_context_token",
    "extract_nickname",
    "is_bot_message",
]


@dataclass
class ILinkError(RuntimeError):
    """协议层异常。

    code 取值（业务层按 code 分支，不要解析 detail 文本）:
        ``session_expired`` 会话过期（errcode -14），调用方应
                            ``auth.BotSession.require_relogin()`` 后重试
        ``http_error``      HTTP 状态码非 2xx
        ``server_error``    业务码失败（ret / errcode 非 0）
        ``network``         传输层失败（连接/读/协议错误）
        ``protocol``        响应结构不符合预期（JSON 坏、字段缺失）
        ``login_timeout``   二维码登录超时（由 auth.BotSession 抛出）
    """

    code: str
    detail: str = ""

    def __str__(self) -> str:  # 异常默认 str 会打成 args 元组，日志里很难读
        return f"[{self.code}] {self.detail}" if self.detail else self.code


# ── X-WECHAT-UIN ──


def make_uin() -> str:
    """生成 ``X-WECHAT-UIN``：随机 uint32 的十进制串再 base64。

    每次请求都重新生成 —— 服务端用它防重放，复用会被拒。用 secrets 而非
    random：可预测的 UIN 等于没有防重放。
    """
    value = int.from_bytes(secrets.token_bytes(4), "big")
    return base64.b64encode(str(value).encode("ascii")).decode("ascii")


def _new_client_id() -> str:
    """发送幂等用的 client_id（本地生成，协议不校验格式）。"""
    return uuid.uuid4().hex


# ── 宽容解析（防协议漂移）──


def _as_int(value: object) -> int | None:
    """把协议里的数字字段转成 int；缺失/空/不可解析一律 None（视为未提供）。

    用「不可解析即视为缺失」而不是「即视为错误」，是因为 iLink 较新，
    宁可放过未知取值也不能把正常响应当成失败。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def is_session_expired(resp: dict) -> bool:
    """是否会话过期（errcode/ret == -14）。

    只认**显式**的过期码：字段缺失不算过期，避免把空响应当成掉线
    （那会导致业务层无谓地重新扫码）。
    """
    if not isinstance(resp, dict):
        return False
    for key in ("errcode", "ret"):
        code = _as_int(resp.get(key))
        if code == SESSION_EXPIRED_ERRCODE:
            return True
    return False


def is_ok(resp: dict) -> bool:
    """业务码是否成功：``ret`` 与 ``errcode`` 都为 0 或缺失。

    两者缺失都算成功 —— iLink 多个接口成功时直接返回 ``{}``。
    """
    if not isinstance(resp, dict):
        return False
    for key in ("ret", "errcode"):
        code = _as_int(resp.get(key))
        if code is not None and code != 0:
            return False
    return True


def _item_text(item: dict) -> str:
    """从 item_list 的一个元素里掏文本：text_item.text → text → content。"""
    text_item = item.get("text_item")
    if isinstance(text_item, dict):
        text = text_item.get("text")
        if isinstance(text, str) and text:
            return text
    for key in ("text", "content"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def extract_text(msg: dict) -> str:
    """从一条入站 msg 提取文本，取不到返回空串。

    依次尝试：``item_list[].text_item.text``（优先 type==1 的文本段，
    取不到再兜底扫所有段）→ ``item_list[].text`` → ``item_list[].content``
    → ``msg["text"]`` → ``msg["content"]``。多段文本用换行拼接。
    """
    if not isinstance(msg, dict):
        return ""
    items = msg.get("item_list")
    if isinstance(items, list):
        # 两轮：先只认 type==1，全无文本时才兜底扫全部 type（防 type 取值漂移）
        for wanted in (ITEM_TYPE_TEXT, None):
            parts: list[str] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if wanted is not None and _as_int(item.get("type")) != wanted:
                    continue
                text = _item_text(item)
                if text:
                    parts.append(text)
            if parts:
                return "\n".join(parts)
    for key in ("text", "content"):
        value = msg.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _first_str(msg: dict, keys: tuple[str, ...]) -> str:
    """按候选 key 顺序取第一个非空标量（str/int/float），全无返回空串。"""
    if not isinstance(msg, dict):
        return ""
    for key in keys:
        value = msg.get(key)
        if value is None or isinstance(value, (dict, list, tuple, bool)):
            continue
        if isinstance(value, (str, int, float)):
            text = str(value)
            if text:
                return text
    return ""


def extract_user_id(msg: dict) -> str:
    """发送者 id：from_user_id → from_userid → sender_id → sender → user_id。"""
    return _first_str(
        msg, ("from_user_id", "from_userid", "sender_id", "sender", "user_id")
    )


def extract_context_token(msg: dict) -> str:
    """会话令牌：context_token → contextToken。回复必须原样回传它。"""
    return _first_str(msg, ("context_token", "contextToken"))


def extract_nickname(msg: dict) -> str:
    """best-effort 昵称：nickname → nick_name → sender_nickname → from_nickname。"""
    return _first_str(
        msg, ("nickname", "nick_name", "sender_nickname", "from_nickname")
    )


def is_bot_message(msg: dict) -> bool:
    """是否 bot 自己发出的消息（message_type == 2）。

    长轮询会把 bot 自己发的消息也推回来，**必须跳过**，否则回复触发
    新一轮回复，形成自激循环。
    """
    return _as_int(msg.get("message_type")) == MESSAGE_TYPE_BOT


# ── 客户端 ──


class ILinkClient:
    """iLink 纯协议客户端，只依赖 httpx。

    transport 可注入（``httpx.MockTransport`` 或任何 ``AsyncBaseTransport``），
    这是本层做到零网络单测的关键。
    """

    def __init__(
        self,
        spec: ILinkSpec | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.spec = spec if spec is not None else load_spec()
        self._client = httpx.AsyncClient(transport=transport)
        self._bot_token = ""
        # 登录返回的 baseurl 可能与默认 base 不同，覆盖只作用于本 client，
        # 不回写 spec（spec 可能被多个 client 共享）
        self._base_url = ""

    # ── 凭证（由 auth.BotSession 注入）──

    def set_credential(self, token: str, base_url: str | None = None) -> None:
        """注入 bot_token；base_url 非空时覆盖本 client 的 base。"""
        self._bot_token = str(token or "")
        if base_url:
            self._base_url = str(base_url).rstrip("/")

    @property
    def has_credential(self) -> bool:
        return bool(self._bot_token)

    # ── 底层 ──

    def _url(self, key: str) -> str:
        base = self._base_url or self.spec.base_url.rstrip("/")
        return f"{base}{self.spec.path(key)}"

    def _headers(self) -> dict[str, str]:
        """业务请求四件套。UIN 每次重生成（防重放）。

        未登录（_bot_token 为空，如取二维码阶段）时不带 Authorization /
        AuthorizationType —— 空 Bearer 会被 httpcore 判为非法 header 值
        （LocalProtocolError）导致请求直接失败。登录后才有 token，再带上。
        """
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-WECHAT-UIN": make_uin(),
        }
        if self._bot_token:
            headers["AuthorizationType"] = "ilink_bot_token"
            headers["Authorization"] = f"Bearer {self._bot_token}"
        return headers

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        """发一次请求并返回已解析的 dict；传输/HTTP/结构问题统一转 ILinkError。

        不做业务码判断（ret/errcode）—— 那由各方法按语义决定：
        「返回原始 dict」的方法原样透出，「返回 ok」的方法才判定成败。
        """
        try:
            resp = await self._client.request(
                method,
                url,
                params=params,
                json=body,
                headers=self._headers(),
                timeout=timeout if timeout is not None else self.spec.request_timeout,
            )
        except httpx.TimeoutException:
            raise  # 长轮询的超时是正常情况，交给 get_updates 语义化处理
        except httpx.HTTPError as exc:
            raise ILinkError("network", f"{method} {url} 失败: {exc!r}") from exc
        except Exception as exc:  # noqa: BLE001 —— 兜底：任何传输层异常都归为 network
            # httpcore 的 LocalProtocolError（如非法 header 值）不属 httpx.HTTPError
            # 子类，会被原样冒泡；这里统一转 ILinkError，避免登录循环被击穿。
            raise ILinkError("network", f"{method} {url} 失败: {exc!r}") from exc

        if resp.status_code >= 400:
            raise ILinkError(
                "http_error", f"{method} {url} → HTTP {resp.status_code}"
            )
        try:
            payload = resp.json()
        except ValueError:
            raise ILinkError(
                "protocol", f"{method} {url} 响应不是合法 JSON"
            ) from None
        if not isinstance(payload, dict):
            raise ILinkError(
                "protocol", f"{method} {url} 响应不是 JSON 对象: {type(payload).__name__}"
            )
        return payload

    # ── 登录 ──

    async def get_qrcode(self) -> dict:
        """取登录二维码。返回 ``{"qrcode": 轮询key, "qrcode_img_content": 扫码URL}``。

        拿不到轮询 key 抛 ILinkError("protocol") —— 没有 key 就无法进入状态机，
        早失败比让上层空转好。
        """
        data = await self._request(
            "GET", self._url("qrcode"), params={"bot_type": self.spec.bot_type}
        )
        key = _first_str(data, ("qrcode", "qrcode_key"))
        if not key:
            raise ILinkError("protocol", "get_bot_qrcode 未返回 qrcode")
        return {
            "qrcode": key,
            "qrcode_img_content": _first_str(
                data, ("qrcode_img_content", "qrcode_img_url", "qrcode_url")
            ),
        }

    async def get_qrcode_status(self, qrcode: str) -> dict:
        """轮询扫码状态（长轮询语义）。

        iLink 的 qrcode_status 是**服务端挂起**接口：在用户扫码/确认前会一直
        挂起，直到状态变化或达到服务端超时才返回。因此客户端超时须留足余量
        （复用 long_poll_timeout），且超时视为「仍等待」而非错误——返回
        ``{"status": "wait"}``，让登录状态机继续用同一 key 轮询，不会因此
        重新取码（否则二维码会不停刷新、用户来不及扫）。

        返回 ``{"status": wait|scaned|confirmed|expired|unknown, "bot_token": str,
        "baseurl": str}``；缺失字段填空串，由 auth.BotSession 决定如何流转。
        """
        try:
            data = await self._request(
                "GET",
                self._url("qrcode_status"),
                params={"qrcode": qrcode},
                timeout=self.spec.long_poll_timeout,
            )
        except httpx.TimeoutException:
            # 长轮询挂起期间客户端先超时：当作「还没扫」，沿用同一 key 继续轮询
            return {"status": "wait", "bot_token": "", "baseurl": ""}
        status = _first_str(data, ("status", "qrcode_status")).strip().lower()
        return {
            "status": status or "unknown",
            "bot_token": _first_str(data, ("bot_token", "token")),
            "baseurl": _first_str(data, ("baseurl", "base_url")),
        }

    # ── 收发 ──

    async def get_updates(self, buf: str = "") -> dict:
        """长轮询收消息，返回**原始**响应（含 get_updates_buf / msgs / ret / errcode）。

        超时（服务端挂起约 35s 后无新消息）视为「本次无消息」而非错误，
        返回 ``{"get_updates_buf": buf, "msgs": []}``，让业务层的下一次
        轮询立即发起；真正的错误（网络/HTTP/结构）才抛 ILinkError。
        """
        body = {"get_updates_buf": buf, "base_info": self.spec.base_info()}
        try:
            return await self._request(
                "POST",
                self._url("getupdates"),
                body=body,
                timeout=self.spec.long_poll_timeout,
            )
        except httpx.TimeoutException:
            # 游标原样回传，避免业务层误以为游标被推进/清空
            return {"get_updates_buf": buf, "msgs": []}

    async def send_text(
        self,
        to_user_id: str,
        text: str,
        context_token: str,
        client_id: str | None = None,
    ) -> dict:
        """发一条文本消息。成功返回 ``{"ok": True}``，失败抛 ILinkError。

        会话过期（errcode -14）单独成 code，业务层据此触发重新扫码。
        """
        body = {
            "msg": {
                "from_user_id": "",  # 协议要求占位：由服务端按 bot_token 补
                "to_user_id": str(to_user_id),
                "client_id": client_id or _new_client_id(),
                "message_type": OUT_MESSAGE_TYPE,
                "message_state": OUT_MESSAGE_STATE,
                "context_token": context_token,
                "item_list": [
                    {"type": ITEM_TYPE_TEXT, "text_item": {"text": text}}
                ],
            },
            "base_info": self.spec.base_info(),
        }
        resp = await self._request("POST", self._url("sendmessage"), body=body)
        if is_session_expired(resp):
            raise ILinkError("session_expired", "sendmessage 报告会话过期")
        if not is_ok(resp):
            raise ILinkError("server_error", f"sendmessage 失败: {resp}")
        return {"ok": True}

    async def verify_credential(self) -> bool:
        """探测当前 token 是否仍有效（解绑/过期后应判失效）。

        发一次轻量 getupdates（空游标）；服务端返回会话过期（errcode -14）
        或非 2xx 即视为无效。任何异常也判失效——宁可重新扫码也不要带着
        死 token 假登录。返回 True 表示 token 可用。
        """
        if not self._bot_token:
            return False
        try:
            resp = await self._request(
                "POST", self._url("getupdates"),
                body={"get_updates_buf": "", "base_info": self.spec.base_info()},
            )
        except ILinkError:
            return False
        except Exception:  # noqa: BLE001
            return False
        if is_session_expired(resp):
            return False
        return True

    async def get_config(self, user_id: str, context_token: str) -> dict:
        """取 typing_ticket，返回原始 dict。"""
        body = {
            "ilink_user_id": str(user_id),
            "context_token": context_token,
            "base_info": self.spec.base_info(),
        }
        return await self._request("POST", self._url("getconfig"), body=body)

    async def send_typing(self, user_id: str, ticket: str, status: int) -> dict:
        """发「正在输入」状态（1=开始，2=取消）。

        typing 是非致命装饰：任何失败都转成 ``{"ok": False, ...}`` 而不抛，
        免得装饰性调用把主流程（收消息/回复）打断。
        """
        body = {
            "ilink_user_id": str(user_id),
            "typing_ticket": ticket,
            "status": status,
            "base_info": self.spec.base_info(),
        }
        try:
            resp = await self._request("POST", self._url("sendtyping"), body=body)
        except ILinkError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.detail}}
        if not is_ok(resp):
            return {
                "ok": False,
                "error": {"code": "server_error", "message": f"sendtyping 失败: {resp}"},
            }
        return {"ok": True}

    # ── 生命周期 ──

    async def aclose(self) -> None:
        await self._client.aclose()

    def __repr__(self) -> str:
        return f"<ILinkClient base={self._url('getupdates')!r} authed={self.has_credential}>"
