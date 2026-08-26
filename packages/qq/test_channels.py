"""Unit tests for the QQ channel abstraction (packages/qq/channels/*).

Covers:
    - 通道选择：config 缺省 / 显式 napcat / llonebot / 未知回退 napcat
    - 配置解析：ws_urls 缺省值、config 覆盖、环境变量 ONEBOT_WS_URLS 优先
    - 接口契约：send 路由到 call_api（private/group）、recent_contacts 合并去重、
      on_message 把 OneBot event 归一化为 QQMessage、群消息 @-bot 过滤、is_connected
    - NapCat / LLOneBot 实例隔离（ws_urls / 标识互不影响）
    - plugin.get_channel() 按 config.qq.channel 返回正确通道类型

全部本地、无网络：用 FakeBot 替换活动 bot，不连接真实网关。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import nonebot  # noqa: E402

nonebot.init()

from packages.qq import channels as ch  # noqa: E402
from packages.qq import plugin as qq  # noqa: E402
from packages.qq.channels import (  # noqa: E402
    LLOneBotChannel,
    NapCatChannel,
    QQMessage,
)


def _run(coro):
    return asyncio.run(coro)


# ── FakeBot / FakeEvent ──

class FakeBot:
    """Minimal OneBot Bot stand-in that records call_api invocations."""

    self_id = "10000"

    def __init__(self):
        self.calls = []
        self.api_result = {"message_id": 12345}
        self.api_results = {}

    async def call_api(self, api, **kwargs):
        self.calls.append((api, kwargs))
        result = self.api_results.get(api, self.api_result)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeSeg:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class FakePrivateEvent:
    """OneBot private message event stand-in (not a GroupMessageEvent)."""

    def __init__(self, user_id, text, nickname="alice"):
        self._user_id = user_id
        self._text = text
        self._nickname = nickname

    def get_user_id(self):
        return self._user_id

    def get_plaintext(self):
        return self._text

    @property
    def sender(self):
        return type("S", (), {"nickname": self._nickname})()


class FakeGroupEvent:
    """OneBot 群消息 event 替身（plain object，message_type='group'）。

    channel._on_message 现按 message_type 判断群/私聊（不再 isinstance），
    故无需真实 pydantic 模型，避免 model_construct + __dict__ 替换的脆弱性。
    """

    message_type = "group"

    def __init__(self, group_id, text, at_bot=True):
        self.group_id = group_id
        self._text = text
        self.message = [
            _FakeSeg(
                "at" if at_bot else "text",
                {"qq": int(FakeBot.self_id)} if at_bot else {"text": text},
            )
        ]
        self.sender = type("S", (), {"nickname": "grp"})()

    def get_user_id(self):
        return "12345"

    def get_plaintext(self):
        return self._text


# ── 通道选择 ──

def test_resolve_channel_name_defaults_and_fallback():
    assert ch.resolve_channel_name(None) == "napcat"
    assert ch.resolve_channel_name("") == "napcat"
    assert ch.resolve_channel_name("napcat") == "napcat"
    assert ch.resolve_channel_name("llonebot") == "llonebot"
    # 未知通道回退 napcat（打印告警但不抛）
    assert ch.resolve_channel_name("wechat") == "napcat"


def test_create_channel_returns_correct_type():
    nap = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    assert isinstance(nap, NapCatChannel)
    assert nap.name == "napcat"
    llo = ch.create_channel("llonebot", ["ws://127.0.0.1:3002"])
    assert isinstance(llo, LLOneBotChannel)
    assert llo.name == "llonebot"


# ── 配置解析 ──

def test_build_channel_spec_defaults():
    assert ch.build_channel_spec({}) == ("napcat", ["ws://127.0.0.1:3001"], None)
    assert ch.build_channel_spec({"channel": "napcat"}) == (
        "napcat", ["ws://127.0.0.1:3001"], None)
    assert ch.build_channel_spec({"channel": "llonebot"}) == (
        "llonebot", ["ws://127.0.0.1:3002"], None)


def test_build_channel_spec_config_override():
    spec = ch.build_channel_spec({
        "channel": "llonebot",
        "llonebot": {"ws_urls": ["ws://127.0.0.1:3999"], "token": "secret"},
    })
    assert spec == ("llonebot", ["ws://127.0.0.1:3999"], "secret")


def test_build_channel_spec_env_precedence(monkeypatch):
    monkeypatch.setenv("ONEBOT_WS_URLS", '["ws://127.0.0.1:5555"]')
    monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "envtok")
    # config 中的 ws_urls 被环境变量覆盖
    spec = ch.build_channel_spec({
        "channel": "llonebot",
        "llonebot": {"ws_urls": ["ws://127.0.0.1:3999"], "token": "cfg"},
    })
    assert spec == ("llonebot", ["ws://127.0.0.1:5555"], "envtok")
    monkeypatch.delenv("ONEBOT_WS_URLS", raising=False)
    monkeypatch.delenv("ONEBOT_ACCESS_TOKEN", raising=False)


# ── 接口契约：send ──

def test_channel_send_private_routes_to_send_private_msg():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    bot = FakeBot()
    c._bot = bot
    result = _run(c.send("private", "10001", "hello [CQ:face,id=1]"))
    assert result["ok"] is True
    assert result["message_id"] == 12345
    api, kwargs = bot.calls[0]
    assert api == "send_private_msg"
    assert kwargs["user_id"] == 10001
    assert kwargs["message"] == "hello [CQ:face,id=1]"


def test_channel_send_group_routes_to_send_group_msg():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    bot = FakeBot()
    c._bot = bot
    result = _run(c.send("group", "20002", "群公告"))
    assert result["ok"] is True
    api, kwargs = bot.calls[0]
    assert api == "send_group_msg"
    assert kwargs["group_id"] == 20002


def test_channel_send_validation_errors():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    c._bot = FakeBot()
    assert _run(c.send("guild", "1", "x"))["error"]["code"] == "invalid_target_type"
    assert _run(c.send("private", "not-a-number", "x"))["error"]["code"] == "invalid_target_id"
    assert _run(c.send("private", "10001", ""))["error"]["code"] == "empty_text"


def test_channel_send_bot_not_connected():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    # 无 _bot，无 bot_fallback → ChannelNotConnected → {ok:false, bot_not_connected}
    result = _run(c.send("private", "10001", "x"))
    assert result["ok"] is False
    assert result["error"]["code"] == "bot_not_connected"


# ── 接口契约：recent_contacts（合并去重）──

def test_channel_recent_contacts_merges():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    bot = FakeBot()
    bot.api_results = {
        "get_recent_contact": [
            {"chatType": 1, "peerUin": "10001", "peerName": "recent-name"},
            {"chatType": 2, "peerUin": "20001", "peerName": "recent-group"},
            {"chatType": 8, "peerUin": "0", "peerName": ""},  # 异常剔除
        ],
        "get_friend_list": [
            {"user_id": 10001, "nickname": "alice", "remark": ""},
            {"user_id": 10002, "nickname": "bob", "remark": "同学B"},
        ],
        "get_group_list": [
            {"group_id": 20001, "group_name": "group-a"},
            {"group_id": 20002, "group_name": "group-b"},
        ],
    }
    c._bot = bot
    result = _run(c.recent_contacts())
    assert result["ok"] is True
    by_uin = {x["peerUin"]: x for x in result["contacts"]}
    assert [x["peerUin"] for x in result["contacts"]] == ["10001", "20001", "10002", "20002"]
    assert by_uin["10001"]["peerName"] == "recent-name"
    assert by_uin["10002"]["peerName"] == "同学B"
    assert set(result["contacts"][0]) == {"peerUin", "peerName", "chatType"}
    # 调用顺序：get_recent_contact → get_friend_list → get_group_list
    assert [a for a, _ in bot.calls] == [
        "get_recent_contact", "get_friend_list", "get_group_list"]


def test_channel_recent_contacts_bot_not_connected():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    result = _run(c.recent_contacts())
    assert result["ok"] is False
    assert result["error"]["code"] == "bot_not_connected"


# ── 接口契约：on_message 归一化 + @-bot 过滤 ──

def test_on_message_private_builds_qqmessage():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    received = []

    async def handler(msg: QQMessage):
        received.append(msg)

    c.on_message(handler)
    bot = FakeBot()
    _run(c._on_message(bot, FakePrivateEvent("10001", "你好")))
    assert len(received) == 1
    m = received[0]
    assert isinstance(m, QQMessage)
    assert m.scope == "user" and m.scope_id == "10001"
    assert m.text == "你好" and m.sender_nickname == "alice"
    assert m.target_type() == "private"


def test_on_message_group_requires_at_bot():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    received = []

    async def handler(msg: QQMessage):
        received.append(msg)

    c.on_message(handler)
    bot = FakeBot()
    # @ 本 bot → 应收到，scope=group
    _run(c._on_message(bot, FakeGroupEvent("20002", "群聊测试", at_bot=True)))
    assert len(received) == 1
    assert received[0].scope == "group" and received[0].scope_id == "20002"
    # 未 @ bot → 丢弃，不调用 handler
    _run(c._on_message(bot, FakeGroupEvent("20002", "不 @ 不处理", at_bot=False)))
    assert len(received) == 1  # 仍只有上一条


def test_on_message_empty_text_dropped():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    received = []

    async def handler(msg: QQMessage):
        received.append(msg)

    c.on_message(handler)
    _run(c._on_message(FakeBot(), FakePrivateEvent("10001", "   ")))
    assert received == []


# ── 状态 ──

def test_is_connected_toggles():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    assert _run(c.is_connected()) is False
    bot = FakeBot()

    async def _connect(b):
        await c._on_bot_connect(b)

    _run(_connect(bot))
    assert _run(c.is_connected()) is True

    async def _disconnect(b):
        await c._on_bot_disconnect(b)

    _run(_disconnect(bot))
    assert _run(c.is_connected()) is False


# ── NapCat / LLOneBot 隔离 ──

def test_napcat_llonebot_isolation():
    nap = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    llo = ch.create_channel("llonebot", ["ws://127.0.0.1:3002"])
    assert nap.name == "napcat" and llo.name == "llonebot"
    assert nap.ws_urls() == ["ws://127.0.0.1:3001"]
    assert llo.ws_urls() == ["ws://127.0.0.1:3002"]
    # 各自独立维护活动 bot
    b1, b2 = FakeBot(), FakeBot()
    nap._bot = b1
    llo._bot = b2
    _run(nap.send("private", "1", "a"))
    _run(llo.send("private", "1", "b"))
    assert [a for a, _ in b1.calls] == ["send_private_msg"]
    assert [a for a, _ in b2.calls] == ["send_private_msg"]


# ── plugin.get_channel 按 config 选择通道 ──

def test_plugin_get_channel_uses_configured_channel(monkeypatch):
    # 隔离运行环境：重置缓存 + stash，强制按 config 重建
    qq._channel = None
    ch.set_active_channel(None)
    monkeypatch.setattr(qq, "_load_config", lambda: {"qq": {"channel": "llonebot"}})
    selected = qq.get_channel()
    assert isinstance(selected, LLOneBotChannel)
    assert selected.ws_urls() == ["ws://127.0.0.1:3002"]
    # 复原为缺省 napcat，避免影响后续用例
    qq._channel = None
    ch.set_active_channel(None)
    monkeypatch.setattr(qq, "_load_config", lambda: {})
    assert isinstance(qq.get_channel(), NapCatChannel)


def test_plugin_channel_send_goes_through_active_channel(tmp_path, monkeypatch):
    # api_send 经当前通道发出（用 FakeBot 注入通道._bot）
    qq._HISTORY_DIR = tmp_path
    bot = FakeBot()
    qq.get_channel()._bot = bot
    result = _run(qq.api_send("private", "10001", "经通道发送"))
    assert result["ok"] is True
    assert bot.calls[0][0] == "send_private_msg"
    # 发送成功落盘 assistant 历史
    hist = _run(qq.api_history("10001"))
    assert hist["messages"][-1]["role"] == "assistant"
