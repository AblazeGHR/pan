"""ILinkChannel 的单元测试（完全离线，httpx.MockTransport + 临时落盘目录）。

覆盖：
- send：token 命中回发 / 缺 token 降级 outbox / 各类校验错误 / 协议错误透出
- 软 TTL：过期 token 仍尝试发送（失败才算失效）
- _process_updates：归一化派发、token 存储、游标推进、bot 消息跳过、单条坏消息不
  中断整批
- recent_contacts：history/inbox/context_token 三来源合并 + inbox 昵称
- send_typing：getconfig → sendtyping 链路
- startup/shutdown 生命周期：登录（复用落盘凭证）→ 轮询收消息 → is_connected

运行：python -m pytest packages/wechat/test_channel.py -q
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
import pytest  # noqa: E402

from packages.wechat import store  # noqa: E402
from packages.wechat.auth import BotSession  # noqa: E402
from packages.wechat.channel import WeChatMessage  # noqa: E402
from packages.wechat.ilink import ILinkClient  # noqa: E402
from packages.wechat.ilink_channel import ILinkChannel  # noqa: E402
from packages.wechat.ilink_spec import ILinkSpec  # noqa: E402
from packages.wechat.test_ilink import (  # noqa: E402
    FakeServer,
    P_CONFIG,
    P_SEND,
    P_TYPING,
    P_UPDATES,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path):
    """把所有落盘重定向到 tmp_path，测完还原（绝不写真实 data/）。"""
    saved = store.data_root()
    store.set_data_root(tmp_path)
    try:
        yield tmp_path
    finally:
        store.set_data_root(saved)


def _channel(server: FakeServer, *, credential: str | None = "tok-1") -> ILinkChannel:
    client = ILinkClient(ILinkSpec(), server.transport())
    if credential:
        client.set_credential(credential)
    return ILinkChannel(client, BotSession(client), poll_interval=0, error_backoff=0)


# ── send：token 回发 ──


def test_send_uses_cached_context_token():
    server = FakeServer({P_SEND: [{}]})
    ch = _channel(server)
    ch.tokens.set("u1", "ct-1")

    result = _run(ch.send("user", "u1", "收到"))

    assert result == {"ok": True}
    body = server.body_to(P_SEND)["msg"]
    assert body["to_user_id"] == "u1"
    assert body["context_token"] == "ct-1"
    assert body["item_list"][0]["text_item"]["text"] == "收到"


def test_send_stale_token_still_attempts():
    """软 TTL：过期 token 仍会尝试（失败才算失效，与 store 约定一致）。"""
    server = FakeServer({P_SEND: [{}]})
    ch = _channel(server)
    store._atomic_write_json(
        store.context_tokens_path(), {"u1": {"token": "ct-old", "ts": 1.0}}
    )

    result = _run(ch.send("user", "u1", "hi"))

    assert result == {"ok": True}
    assert server.body_to(P_SEND)["msg"]["context_token"] == "ct-old"
    assert ch.has_fresh_token("u1") is False


def test_send_without_token_buffers_to_outbox():
    """缺 token：降级 outbox 缓冲 + no_context_token 错误（buffered=True）。"""
    server = FakeServer({})
    ch = _channel(server)

    result = _run(ch.send("user", "u1", "稍后送达"))

    assert result["ok"] is False
    assert result["error"]["code"] == "no_context_token"
    assert result["error"]["buffered"] is True
    pending = store.load_outbox("u1")
    assert [m["text"] for m in pending] == ["稍后送达"]
    assert server.calls == [], "缺 token 时绝不能打 iLink 发送接口"


def test_send_validations():
    ch = _channel(FakeServer({}))
    assert _run(ch.send("group", "u1", "x"))["error"]["code"] == "invalid_target_type"
    assert _run(ch.send("user", "", "x"))["error"]["code"] == "invalid_target_id"
    assert _run(ch.send("user", "u1", "  "))["error"]["code"] == "empty_text"


def test_send_without_credential_reports_not_connected():
    ch = _channel(FakeServer({}), credential=None)
    ch.tokens.set("u1", "ct-1")  # 有 token 也没用，没登录
    result = _run(ch.send("user", "u1", "x"))
    assert result["error"]["code"] == "not_connected"


def test_send_propagates_ilink_error_code():
    server = FakeServer({P_SEND: [{"ret": -1, "errcode": 5}]})
    ch = _channel(server)
    ch.tokens.set("u1", "ct-1")
    result = _run(ch.send("user", "u1", "x"))
    assert result["ok"] is False
    assert result["error"]["code"] == "server_error"


# ── 收消息：归一化 / token / 游标 ──


def test_process_updates_dispatches_and_stores_token():
    server = FakeServer({})
    ch = _channel(server)
    received: list[WeChatMessage] = []
    ch.on_message(received.append)

    _run(ch._process_updates({
        "get_updates_buf": "cursor-2",
        "msgs": [
            {  # bot 自己发的 → 跳过
                "message_type": 2, "from_user_id": "bot",
                "context_token": "ct-bot",
                "item_list": [{"type": 1, "text_item": {"text": "回声"}}],
            },
            {  # 用户上行 → 归一化派发
                "message_type": 1, "from_user_id": "u9",
                "context_token": "ct-9", "sender_nickname": "小明",
                "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
            },
        ],
    }))

    assert len(received) == 1, "bot 消息必须被跳过"
    msg = received[0]
    assert msg.scope == "user"
    assert msg.scope_id == "u9"
    assert msg.text == "你好"
    assert msg.sender_nickname == "小明"
    assert msg.context_token == "ct-9"
    assert msg.target_type() == "user"
    assert ch.tokens.get("u9") == "ct-9"
    assert ch.cursor() == "cursor-2"


def test_process_updates_bad_message_does_not_break_batch():
    server = FakeServer({})
    ch = _channel(server)
    received: list[WeChatMessage] = []

    async def flaky_handler(msg: WeChatMessage) -> None:
        received.append(msg)
        if msg.scope_id == "bad":
            raise RuntimeError("boom")

    ch.on_message(flaky_handler)
    _run(ch._process_updates({
        "get_updates_buf": "c",
        "msgs": [
            {"from_user_id": "bad", "context_token": "t1",
             "item_list": [{"type": 1, "text_item": {"text": "会炸"}}]},
            {"from_user_id": "good", "context_token": "t2",
             "item_list": [{"type": 1, "text_item": {"text": "正常"}}]},
        ],
    }))
    assert [m.scope_id for m in received] == ["bad", "good"]


def test_process_updates_without_context_token_still_dispatches():
    server = FakeServer({})
    ch = _channel(server)
    received: list[WeChatMessage] = []
    ch.on_message(received.append)
    _run(ch._process_updates({
        "get_updates_buf": "c",
        "msgs": [{"from_user_id": "u1", "item_list": [{"type": 1, "text": "无token"}]}],
    }))
    assert len(received) == 1
    assert received[0].context_token == ""
    assert ch.tokens.get("u1") == ""
    assert store.load_outbox("u1") == [], "收消息侧不做 outbox，缓冲只发生在 send"


# ── recent_contacts ──


def test_recent_contacts_merges_local_sources():
    ch = _channel(FakeServer({}))
    store.append_history("u-hist", "user", "hi")
    store.append_inbox("u-inbox", "在吗", nickname="小红")
    ch.tokens.set("u-token", "t")

    result = _run(ch.recent_contacts())

    assert result["ok"] is True
    assert result["source"] == "local"
    by_id = {c["peerUin"]: c for c in result["contacts"]}
    assert set(by_id) == {"u-hist", "u-inbox", "u-token"}
    assert by_id["u-inbox"]["peerName"] == "小红", "昵称取自最后一条 inbox 记录"
    assert by_id["u-hist"]["peerName"] == "u-hist", "无昵称回退 user id"
    assert all(c["chatType"] == 1 for c in result["contacts"])


# ── send_typing ──


def test_send_typing_happy_path():
    server = FakeServer({P_CONFIG: [{"typing_ticket": "tk-1"}], P_TYPING: [{}]})
    ch = _channel(server)
    ch.tokens.set("u1", "ct-1")

    result = _run(ch.send_typing("u1", 1))

    assert result == {"ok": True}
    assert server.body_to(P_CONFIG) == {
        "ilink_user_id": "u1", "context_token": "ct-1",
        "base_info": {"channel_version": "1.0.2"},
    }
    assert server.body_to(P_TYPING)["typing_ticket"] == "tk-1"


def test_send_typing_without_token():
    ch = _channel(FakeServer({}))
    result = _run(ch.send_typing("u1", 1))
    assert result["error"]["code"] == "no_context_token"


# ── 生命周期 ──


def test_startup_polls_and_shutdown():
    """startup（复用落盘凭证，免扫码）→ 轮询收到消息 → shutdown 干净收尾。"""
    store.save_credential({"bot_token": "saved-tok", "baseurl": ""})
    server = FakeServer({
        P_UPDATES: [{
            "ret": 0, "get_updates_buf": "c1",
            "msgs": [{"from_user_id": "u1", "context_token": "ct-1",
                      "item_list": [{"type": 1, "text_item": {"text": "早"}}]}],
        }],
    })
    ch = _channel(server)
    received: list[WeChatMessage] = []
    ch.on_message(received.append)

    async def scenario():
        await ch.startup()
        assert await ch.is_connected() is True
        await asyncio.sleep(0.1)  # 让长轮询至少跑一圈（MockTransport 立即返回）
        await ch.shutdown()

    _run(scenario())

    assert ch.client.has_credential is True, "启动时应复用落盘凭证（免扫码）"
    assert _run(ch.is_connected()) is False, "shutdown 后通道应报告未连接"
    assert any(m.scope_id == "u1" for m in received), "轮询收到的消息必须派发给业务层"
    assert ch.tokens.get("u1") == "ct-1"


def test_is_connected_false_before_startup():
    ch = _channel(FakeServer({}))
    assert _run(ch.is_connected()) is False
