"""iLink 协议客户端与登录编排的单元测试（完全离线）。

用 ``httpx.MockTransport`` 替换传输层：不发真实 HTTP 请求、不 bind 端口、
不 spawn 子进程。落盘经 ``store.set_data_root(tmp_path)`` 重定向到临时目录，
测试结束还原，不碰真实的 ``data/``。

运行::

    E:/python/python.exe -m pytest packages/wechat/test_ilink.py -q

（``pytest.ini`` 的 testpaths 暂未含 packages/wechat，需用显式路径；
由另一位同事统一加入，本文件不改动 pytest.ini。）
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
import pytest  # noqa: E402

from packages.wechat import auth, ilink, store  # noqa: E402
from packages.wechat.auth import BotCredential, BotSession  # noqa: E402
from packages.wechat.ilink import (  # noqa: E402
    ILinkClient,
    ILinkError,
    extract_context_token,
    extract_nickname,
    extract_text,
    extract_user_id,
    is_bot_message,
    is_ok,
    is_session_expired,
    make_uin,
)
from packages.wechat.ilink_spec import ILinkSpec  # noqa: E402

# 协议路径常量（与 ilink_spec 一致，测试里直接引 path key 的产物）
P_QRCODE = ILinkSpec.path(ILinkSpec(), "qrcode")
P_STATUS = ILinkSpec.path(ILinkSpec(), "qrcode_status")
P_UPDATES = ILinkSpec.path(ILinkSpec(), "getupdates")
P_SEND = ILinkSpec.path(ILinkSpec(), "sendmessage")
P_CONFIG = ILinkSpec.path(ILinkSpec(), "getconfig")
P_TYPING = ILinkSpec.path(ILinkSpec(), "sendtyping")

DEFAULT_HOST = "ilinkai.weixin.qq.com"


def _run(coro):
    return asyncio.run(coro)


# ── 落盘隔离 ──


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path):
    """把所有落盘重定向到 tmp_path，测完还原（绝不写真实 data/）。"""
    saved = store.data_root()
    store.set_data_root(tmp_path)
    try:
        yield tmp_path
    finally:
        store.set_data_root(saved)


# ── MockTransport 录制器 ──


class FakeServer:
    """按 URL path 回放预设响应，并录制所有请求。

    routes: ``path → [响应, ...]``。响应可以是 dict（包成 200 JSON）、
    ``httpx.Response``，或 Exception 实例（直接抛出，模拟传输层故障）。
    序列用完最后一个会被重复使用（长轮询会打很多次）。
    """

    def __init__(self, routes: dict[str, list]):
        self.routes = {key: list(value) for key, value in routes.items()}
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        seq = self.routes.get(request.url.path)
        if seq is None:
            return httpx.Response(404, json={"ret": -1})
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def calls_to(self, path: str) -> list[httpx.Request]:
        return [c for c in self.calls if c.url.path == path]

    def body_to(self, path: str, index: int = -1) -> dict:
        request = self.calls_to(path)[index]
        return json.loads(request.content.decode("utf-8"))


def _client(server: FakeServer, spec: ILinkSpec | None = None) -> ILinkClient:
    return ILinkClient(spec if spec is not None else ILinkSpec(), server.transport())


def _logged_in_client(server: FakeServer, token: str = "tok-1") -> ILinkClient:
    client = _client(server)
    client.set_credential(token)
    return client


# ── 1. X-WECHAT-UIN ──


def test_make_uin_unique_and_valid_base64():
    uins = {make_uin() for _ in range(200)}
    assert len(uins) == 200, "UIN 必须每次不同（防重放）"
    for uin in uins:
        # 合法 base64，且解码后是 uint32 的十进制串
        raw = base64.b64decode(uin, validate=True)
        value = int(raw.decode("ascii"))
        assert 0 <= value < 2**32
        assert raw.decode("ascii").isdigit()


# ── 2. 请求 header ──


def test_request_headers_complete_and_uin_rotates():
    server = FakeServer({P_SEND: [{}]})
    client = _logged_in_client(server)
    _run(client.send_text("u1", "a", "ct"))
    _run(client.send_text("u1", "b", "ct"))

    assert len(server.calls_to(P_SEND)) == 2
    uins = []
    for request in server.calls_to(P_SEND):
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["AuthorizationType"] == "ilink_bot_token"
        assert request.headers["Authorization"] == "Bearer tok-1"
        uins.append(request.headers["X-WECHAT-UIN"])
    assert uins[0] != uins[1], "每次请求的 UIN 必须重新生成"


# ── 3. 取二维码 ──


def test_get_qrcode_parses_fields():
    server = FakeServer(
        {P_QRCODE: [{"qrcode": "key-1", "qrcode_img_content": "https://qr/x.png"}]}
    )
    client = _client(server)
    data = _run(client.get_qrcode())
    assert data == {"qrcode": "key-1", "qrcode_img_content": "https://qr/x.png"}
    # bot_type 走 spec，不写字面量
    assert server.calls_to(P_QRCODE)[0].url.params["bot_type"] == "3"


def test_get_qrcode_missing_key_raises_protocol():
    server = FakeServer({P_QRCODE: [{"qrcode_img_content": "https://qr/x.png"}]})
    client = _client(server)
    with pytest.raises(ILinkError) as exc:
        _run(client.get_qrcode())
    assert exc.value.code == "protocol"


# ── 4. 登录状态机 + baseurl 覆盖 ──


def test_login_state_machine_and_baseurl_override():
    server = FakeServer(
        {
            P_QRCODE: [{"qrcode": "key-1", "qrcode_img_content": "https://qr/x.png"}],
            P_STATUS: [
                {"status": "wait"},
                {"status": "scaned"},
                {
                    "status": "confirmed",
                    "bot_token": "tok-2",
                    "baseurl": "https://ilink-2.example.com",
                },
            ],
            P_UPDATES: [{"ret": 0, "msgs": [], "get_updates_buf": "b1"}],
        }
    )
    client = _client(server)
    shown: list[tuple[str, str]] = []
    on_disk: list[str] = []

    def on_qrcode(url: str, key: str) -> None:
        shown.append((url, key))
        # 回调触发时二维码必须已落盘（终端不可见时靠这份文件取码）
        on_disk.append(store.login_qrcode_path().read_text(encoding="utf-8"))

    session = BotSession(client, on_qrcode=on_qrcode, poll_interval=0)
    credential = _run(session.ensure_login())

    # wait → scaned → confirmed 全程走通
    assert [c.url.params["qrcode"] for c in server.calls_to(P_STATUS)] == [
        "key-1", "key-1", "key-1",
    ]
    assert credential.bot_token == "tok-2"
    assert credential.baseurl == "https://ilink-2.example.com"
    # 二维码既给了回调也落了盘
    assert shown == [("https://qr/x.png", "key-1")]
    assert on_disk == ["https://qr/x.png"]
    # 凭证落盘 + 二维码文件已清理
    assert store.load_credential()["bot_token"] == "tok-2"
    assert not (store.data_root() / "wechat" / "login_qrcode.txt").exists()

    # baseurl 覆盖默认 base：登录后业务请求打到新 host
    assert server.calls_to(P_QRCODE)[0].url.host == DEFAULT_HOST
    _run(client.get_updates())
    assert server.calls_to(P_UPDATES)[0].url.host == "ilink-2.example.com"


def test_ensure_login_reuses_saved_credential():
    """已有落盘凭证 → 直接复用，不碰二维码接口，且 baseurl 一并恢复。"""
    store.save_credential(
        {"bot_token": "saved-tok", "baseurl": "https://saved.example.com"}
    )
    server = FakeServer({P_UPDATES: [{"ret": 0, "msgs": []}]})
    client = _client(server)
    credential = _run(BotSession(client).ensure_login())

    assert credential.bot_token == "saved-tok"
    assert server.calls_to(P_QRCODE) == []
    _run(client.get_updates())
    assert server.calls_to(P_UPDATES)[0].url.host == "saved.example.com"


def test_require_relogin_clears_and_rescans():
    """会话过期后强制重登：清旧凭证、重新走二维码、进程不退出。"""
    store.save_credential({"bot_token": "stale"})
    server = FakeServer(
        {
            P_QRCODE: [{"qrcode": "key-new", "qrcode_img_content": "https://qr/new"}],
            P_STATUS: [
                {"status": "confirmed", "bot_token": "fresh", "baseurl": ""},
            ],
            P_UPDATES: [{"ret": 0, "msgs": []}],
        }
    )
    client = _client(server)
    session = BotSession(client, poll_interval=0)
    credential = _run(session.require_relogin())

    assert credential.bot_token == "fresh"
    assert store.load_credential()["bot_token"] == "fresh"
    assert client.has_credential is True
    # baseurl 为空 → 不覆盖默认 base
    _run(client.get_updates())
    assert server.calls_to(P_UPDATES)[0].url.host == DEFAULT_HOST


# ── 5. 二维码过期自动重取 ──


def test_login_regets_qrcode_on_expired():
    server = FakeServer(
        {
            P_QRCODE: [{"qrcode": "key-1", "qrcode_img_content": "https://qr/1"},
                       {"qrcode": "key-2", "qrcode_img_content": "https://qr/2"}],
            P_STATUS: [
                {"status": "expired"},
                {"status": "confirmed", "bot_token": "tok-3", "baseurl": "https://b3.test"},
            ],
        }
    )
    client = _client(server)
    credential = _run(BotSession(client, poll_interval=0).ensure_login())

    assert credential.bot_token == "tok-3"
    assert len(server.calls_to(P_QRCODE)) == 2, "expired 后必须重新取码"
    # 第二次轮询用的是新码
    assert [c.url.params["qrcode"] for c in server.calls_to(P_STATUS)] == [
        "key-1", "key-2",
    ]


# ── 6. 登录超时 ──


def test_login_timeout_with_zero_budget():
    server = FakeServer(
        {P_QRCODE: [{"qrcode": "k", "qrcode_img_content": "https://qr/1"}]}
    )
    client = _client(server)
    session = BotSession(client, poll_interval=0, qrcode_timeout=0)
    with pytest.raises(ILinkError) as exc:
        _run(session.ensure_login())
    assert exc.value.code == "login_timeout"
    assert session.credential is None


def test_login_timeout_while_polling():
    server = FakeServer(
        {
            P_QRCODE: [{"qrcode": "k", "qrcode_img_content": "https://qr/1"}],
            P_STATUS: [{"status": "wait"}],
        }
    )
    client = _client(server)
    # 预算极短 + 轮询间隔极小 → 快速耗尽预算
    session = BotSession(client, poll_interval=0.01, qrcode_timeout=0.05)
    with pytest.raises(ILinkError) as exc:
        _run(session.ensure_login())
    assert exc.value.code == "login_timeout"
    assert len(server.calls_to(P_STATUS)) > 1, "超时前应在持续轮询"


# ── 7. get_updates 解析与游标 ──


def test_get_updates_parses_msgs_and_carries_cursor():
    server = FakeServer(
        {
            P_UPDATES: [
                {
                    "ret": 0,
                    "get_updates_buf": "cursor-2",
                    "msgs": [
                        {
                            "message_type": 2,
                            "from_user_id": "bot-self",
                            "context_token": "ct-1",
                            "item_list": [{"type": 1, "text_item": {"text": "我自己发的"}}],
                        },
                        {
                            "message_type": 1,
                            "from_user_id": "user-9",
                            "context_token": "ct-2",
                            "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
                        },
                    ],
                }
            ]
        }
    )
    client = _logged_in_client(server)
    resp = _run(client.get_updates("abc"))

    # 游标原样回传
    assert server.body_to(P_UPDATES)["get_updates_buf"] == "abc"
    assert resp["get_updates_buf"] == "cursor-2"

    bot_msg, user_msg = resp["msgs"]
    assert is_bot_message(bot_msg) is True, "message_type=2 必须被识别为 bot 自己发的"
    assert is_bot_message(user_msg) is False
    assert extract_text(user_msg) == "你好"
    assert extract_user_id(user_msg) == "user-9"
    assert extract_context_token(user_msg) == "ct-2"


# ── 8. 长轮询超时 = 无新消息 ──


def test_get_updates_timeout_returns_empty_without_raising():
    server = FakeServer(
        {P_UPDATES: [httpx.ReadTimeout("poll timed out", request=None)]}
    )
    client = _logged_in_client(server)
    resp = _run(client.get_updates("cursor-7"))

    assert resp == {"get_updates_buf": "cursor-7", "msgs": []}


# ── 9. send_text body 形状 ──


def test_send_text_body_shape():
    server = FakeServer({P_SEND: [{}]})
    client = _logged_in_client(server)
    result = _run(client.send_text("user-9", "收到", "ct-9"))

    assert result == {"ok": True}
    body = server.body_to(P_SEND)
    msg = body["msg"]
    assert msg["to_user_id"] == "user-9"
    assert msg["from_user_id"] == ""
    assert msg["message_type"] == 2
    assert msg["message_state"] == 2
    assert msg["context_token"] == "ct-9"
    assert msg["item_list"][0]["type"] == 1
    assert msg["item_list"][0]["text_item"]["text"] == "收到"
    assert msg["client_id"], "client_id 必须存在（缺省时自动生成）"
    assert body["base_info"] == {"channel_version": "1.0.2"}


def test_send_text_client_id_preserved():
    server = FakeServer({P_SEND: [{}]})
    client = _logged_in_client(server)
    _run(client.send_text("u", "x", "ct", client_id="fixed-id"))
    assert server.body_to(P_SEND)["msg"]["client_id"] == "fixed-id"


def test_send_text_session_expired_raises_distinct_code():
    server = FakeServer({P_SEND: [{"errcode": -14}]})
    client = _logged_in_client(server)
    with pytest.raises(ILinkError) as exc:
        _run(client.send_text("u", "x", "ct"))
    assert exc.value.code == "session_expired"


def test_send_text_server_error_raises():
    server = FakeServer({P_SEND: [{"ret": -1, "errcode": 5, "errmsg": "boom"}]})
    client = _logged_in_client(server)
    with pytest.raises(ILinkError) as exc:
        _run(client.send_text("u", "x", "ct"))
    assert exc.value.code == "server_error"


def test_http_error_maps_to_ilink_error():
    server = FakeServer({P_SEND: [httpx.Response(500, json={"ret": -1})]})
    client = _logged_in_client(server)
    with pytest.raises(ILinkError) as exc:
        _run(client.send_text("u", "x", "ct"))
    assert exc.value.code == "http_error"


# ── 10. 过期码判定 ──


def test_is_session_expired_codes():
    assert is_session_expired({"errcode": -14}) is True
    assert is_session_expired({"ret": -14}) is True
    assert is_session_expired({"ret": 0}) is False
    assert is_session_expired({}) is False, "字段缺失不算过期"
    assert is_session_expired({"errcode": -1}) is False


def test_is_ok_missing_fields_count_as_success():
    assert is_ok({}) is True
    assert is_ok({"ret": 0}) is True
    assert is_ok({"errcode": 0}) is True
    assert is_ok({"ret": 0, "errcode": 0}) is True
    assert is_ok({"ret": -1}) is False
    assert is_ok({"errcode": -14}) is False


# ── 11. 宽容解析 ──


def test_tolerant_extraction_falls_back_through_aliases():
    # 字段改名
    assert extract_user_id({"from_userid": "u1"}) == "u1"
    assert extract_user_id({"sender_id": "u2"}) == "u2"
    assert extract_user_id({"sender": "u3"}) == "u3"
    assert extract_user_id({"user_id": "u4"}) == "u4"
    # 数字 id 归一为字符串
    assert extract_user_id({"from_user_id": 12345}) == "12345"
    # 顶层兜底
    assert extract_text({"text": "hi"}) == "hi"
    assert extract_text({"content": "yo"}) == "yo"
    # 非 type==1 的文本段也要兜底拿到（防 type 取值漂移）
    assert extract_text({"item_list": [{"type": 3, "text_item": {"text": "兜底"}}]}) == "兜底"
    # item 里 text_item 缺失 → 退 text / content
    assert extract_text({"item_list": [{"type": 1, "text": "plain"}]}) == "plain"
    # 多段拼接
    assert extract_text(
        {"item_list": [
            {"type": 1, "text_item": {"text": "A"}},
            {"type": 1, "text_item": {"text": "B"}},
        ]}
    ) == "A\nB"
    # 大小写变体 + 昵称别名
    assert extract_context_token({"contextToken": "ct-x"}) == "ct-x"
    assert extract_nickname({"sender_nickname": "小明"}) == "小明"
    assert extract_nickname({"nick_name": "小红"}) == "小红"
    assert extract_nickname({"from_nickname": "小刚"}) == "小刚"


def test_tolerant_extraction_never_raises_on_garbage():
    # 全缺失 → 空串
    assert extract_text({}) == ""
    assert extract_user_id({}) == ""
    assert extract_context_token({}) == ""
    assert extract_nickname({}) == ""
    # 类型不对不抛
    assert extract_text({"item_list": "not-a-list", "text": 42}) == ""
    assert extract_text({"item_list": [None, 5, {"text_item": None}]}) == ""
    assert extract_user_id({"from_user_id": {"nested": 1}}) == ""
    assert extract_context_token({"context_token": None}) == ""
    # 非 dict 入参
    assert extract_text(None) == ""
    assert extract_user_id("nope") == ""
    # message_type 缺失/非数字 → 不是 bot 消息
    assert is_bot_message({}) is False
    assert is_bot_message({"message_type": "unknown"}) is False
    assert is_bot_message({"message_type": "2"}) is True


# ── 12. typing 非致命 ──


def test_send_typing_failure_does_not_raise():
    server = FakeServer({P_TYPING: [httpx.Response(500, json={"ret": -1})]})
    client = _logged_in_client(server)
    result = _run(client.send_typing("user-9", "ticket-1", 1))

    assert result["ok"] is False
    body = server.body_to(P_TYPING)
    assert body == {
        "ilink_user_id": "user-9",
        "typing_ticket": "ticket-1",
        "status": 1,
        "base_info": {"channel_version": "1.0.2"},
    }


def test_send_typing_network_failure_does_not_raise():
    server = FakeServer({P_TYPING: [httpx.ConnectError("unreachable")]})
    client = _logged_in_client(server)
    result = _run(client.send_typing("user-9", "ticket-1", 2))
    assert result["ok"] is False


def test_get_config_returns_raw_dict():
    server = FakeServer({P_CONFIG: [{"ret": 0, "typing_ticket": "tk-1"}]})
    client = _logged_in_client(server)
    resp = _run(client.get_config("user-9", "ct-9"))

    assert resp["typing_ticket"] == "tk-1"
    assert server.body_to(P_CONFIG) == {
        "ilink_user_id": "user-9",
        "context_token": "ct-9",
        "base_info": {"channel_version": "1.0.2"},
    }


# ── 凭证序列化 ──


def test_bot_credential_roundtrip_and_tolerance():
    cred = BotCredential(bot_token="t", baseurl="https://b", bot_id="id-1",
                         obtained_at=1.5)
    assert cred.is_valid() is True
    restored = BotCredential.from_dict(cred.to_dict())
    assert restored == cred
    # 空 dict / 脏数据不抛
    assert BotCredential.from_dict({}).is_valid() is False
    assert BotCredential.from_dict({"token": "alias"}).bot_token == "alias"
    assert BotCredential.from_dict({"base_url": "https://x"}).baseurl == "https://x"
    assert BotCredential.from_dict({"obtained_at": "bad"}).obtained_at == 0.0


def test_apply_injects_into_arbitrary_client():
    server = FakeServer({P_SEND: [{}]})
    target = _client(server)
    session = BotSession(_client(FakeServer({})))
    session._credential = BotCredential(bot_token="t2", baseurl="https://other.test")
    session.apply(target)

    assert target.has_credential is True
    _run(target.send_text("u", "x", "ct"))
    assert server.calls_to(P_SEND)[0].url.host == "other.test"


def test_bot_session_aclose_closes_client():
    server = FakeServer({})
    client = _client(server)
    _run(BotSession(client).aclose())
