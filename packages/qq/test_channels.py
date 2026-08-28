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
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import nonebot  # noqa: E402
import pytest  # noqa: E402

nonebot.init()

from packages.qq import channels as ch  # noqa: E402
from packages.qq import plugin as qq  # noqa: E402
from packages.qq.channels import (  # noqa: E402
    LLOneBotChannel,
    NapCatChannel,
    QQMessage,
)
from packages.qq.channels import onebot as ob  # noqa: E402

# autouse fixture 会把 ob._download 覆盖为失败 no-op；直测下载逻辑的用例
# 用 import 时保存的原始引用
_REAL_DOWNLOAD = ob._download


def _run(coro):
    return asyncio.run(coro)


# ── 全局通道状态隔离 ──
#
# 本模块会修改进程级的 QQ 通道单例（packages.qq.channels._ACTIVE 经
# set_active_channel()，以及 packages.qq.plugin 的 _channel / _active_bot）。其中
# test_plugin_channel_send_goes_through_active_channel 直接对 get_channel() 返回的
# 通道实例设置 _bot（FakeBot），该实例被 stash 进 _ACTIVE / _channel，测试后未还原。
# 若本文件先于 test_qq_api 运行，被污染的通道（_bot 已被占用）会泄漏给 test_qq_api：
# 其 recent_contacts / send 测试依赖 _resolve_bot() 在无 _bot 时回退到 _active_bot，
# 但污染通道的 _bot 已非空 → 拿到错误 bot → 断言失败。
#
# 用模块级 autouse fixture 在模块前后保存并恢复这些全局单例，保证本模块运行后
# 不留下污染（与 test_qq_api 共享 get_channel()）。导入 plugin 时 get_channel() 已
# 把 import-time 通道（_bot=None）缓存进 _ACTIVE / _channel，保存的即此干净状态。


@pytest.fixture(autouse=True, scope="module")
def _isolate_qq_channel_state():
    """Save and restore the process-wide QQ channel singletons so this module
    leaves no polluted channel behind for test_qq_api (which shares get_channel())."""
    saved_active = ch.get_active_channel()
    saved_channel = qq._channel
    saved_active_bot = qq._active_bot
    saved_bot = (
        getattr(saved_channel, "_bot", None) if saved_channel is not None else None
    )
    try:
        yield
    finally:
        ch.set_active_channel(saved_active)
        qq._channel = saved_channel
        qq._active_bot = saved_active_bot
        if saved_channel is not None:
            saved_channel._bot = saved_bot


@pytest.fixture(autouse=True)
def _noop_media_download(monkeypatch):
    """禁真实联网下载：_on_message 会对带 url 的 image/file 段尝试下载，单测
    一律把 ob._download 替换为失败 no-op（描述保留原 url，与旧行为一致）。
    需要验证下载成功路径的用例自行覆盖为写入 tmp 的假下载。"""

    async def _fail(url, dest_dir, filename, timeout=10.0):
        return None

    monkeypatch.setattr(ob, "_download", _fail)


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

    def __init__(self, user_id, text, nickname="alice", segments=None):
        self._user_id = user_id
        self._text = text
        self._nickname = nickname
        # 富媒体段（_FakeSeg 列表）；默认空 → 无富媒体，行为与旧版一致
        self.message = list(segments or [])

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
                # OneBot v11 协议 At.data["qq"] 为字符串，锁契约防 int 回归
                {"qq": str(FakeBot.self_id)} if at_bot else {"text": text},
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


# ── 多通道解析 build_channel_specs ──

def test_build_channel_specs_single_channel_compat():
    """无 channels 数组 → 退回单通道 build_channel_spec（向后兼容）。"""
    specs = ch.build_channel_specs({"channel": "llonebot"})
    assert specs == [{
        "name": "llonebot",
        "ws_urls": ["ws://127.0.0.1:3002"],
        "token": None,
        "bot_uin": None,
    }]


def test_build_channel_specs_multi_channel():
    """channels 数组逐项解析；name 别名按前缀映射通道类；bot_uin 保留。"""
    specs = ch.build_channel_specs({
        "channel": "llonebot",
        "channels": [
            {"name": "llonebot", "ws_urls": ["ws://127.0.0.1:3002"],
             "bot_uin": "3494144273"},
            {"name": "llonebot2", "ws_urls": ["ws://127.0.0.1:3003"],
             "bot_uin": 1470993983},
        ],
    })
    assert [s["name"] for s in specs] == ["llonebot", "llonebot2"]
    assert [s["ws_urls"] for s in specs] == [
        ["ws://127.0.0.1:3002"], ["ws://127.0.0.1:3003"]]
    assert [s["bot_uin"] for s in specs] == ["3494144273", "1470993983"]
    # bot_uin 数字也归一为字符串
    assert all(isinstance(s["bot_uin"], str) for s in specs)


def test_build_channel_specs_entry_fallbacks():
    """entry 缺 ws_urls → 回退 qq.<name>.ws_urls → 回退通道类缺省端口；
    空 entry / 非法 entry 跳过；数组整体无效 → 单通道回退。"""
    specs = ch.build_channel_specs({
        "channels": [
            {"name": "llonebot2", "bot_uin": "1470993983"},  # 无 ws_urls
            {"bot_uin": "x"},        # 无 name → 跳过
            "garbage",               # 非 dict → 跳过
            {"name": "napcat", "ws_urls": "ws://127.0.0.1:3901"},  # str 归一
        ],
        "llonebot2": {"ws_urls": ["ws://127.0.0.1:3903"]},
    })
    assert len(specs) == 2
    # llonebot2 无 entry ws_urls → 回退 qq.llonebot2.ws_urls
    assert specs[0]["ws_urls"] == ["ws://127.0.0.1:3903"]
    # napcat 的 str ws_urls 归一为 list
    assert specs[1]["ws_urls"] == ["ws://127.0.0.1:3901"]


def test_build_channel_specs_env_ignored_in_multi(monkeypatch):
    """channels 数组模式下不读 ONEBOT_WS_URLS（env 是单通道覆盖入口）。"""
    monkeypatch.setenv("ONEBOT_WS_URLS", '["ws://127.0.0.1:5555"]')
    specs = ch.build_channel_specs({
        "channels": [{"name": "llonebot", "ws_urls": ["ws://127.0.0.1:3002"]}],
    })
    assert specs[0]["ws_urls"] == ["ws://127.0.0.1:3002"]
    monkeypatch.delenv("ONEBOT_WS_URLS", raising=False)


def test_create_channel_alias_name():
    """别名通道："llonebot2" → LLOneBotChannel 实例，config.name 保留别名。"""
    c = ch.create_channel("llonebot2", ["ws://127.0.0.1:3003"], bot_uin="1470993983")
    assert isinstance(c, LLOneBotChannel)
    assert c.name == "llonebot2"
    assert c.config.bot_uin == "1470993983"
    # 完全未知名字仍回退 napcat（告警）
    c2 = ch.create_channel("wechat", ["ws://127.0.0.1:3999"])
    assert isinstance(c2, NapCatChannel)
    assert c2.name == "napcat"


# ── 多账号注册表 set/get/by_name/by_uin ──

def test_multi_channel_registry():
    # 进程级注册表在 import 时可能已含默认通道（plugin.get_channel() 惰性创建；
    # 无 config.json 时 resolve_channel_name 回退默认 napcat——CI 干净环境即此
    # 情形，本地有 config.json 时是 llonebot）。快照并清空注册表，使下方断言
    # 只针对本用例注册的通道，对环境前置状态免疫。
    saved_registry = dict(ch._ACTIVE_CHANNELS)
    ch._ACTIVE_CHANNELS.clear()
    c1 = ch.create_channel("llonebot", ["ws://127.0.0.1:3002"], bot_uin="3494144273")
    c2 = ch.create_channel("llonebot2", ["ws://127.0.0.1:3003"], bot_uin="1470993983")
    ch.set_active_channel(c1, name=c1.name)
    ch.set_active_channel(c2, name=c2.name)
    try:
        # set_active_channel 每次覆盖默认指针
        assert ch.get_active_channel() is c2
        # by_name
        assert ch.get_channel_by_name("llonebot") is c1
        assert ch.get_channel_by_name("llonebot2") is c2
        assert ch.get_channel_by_name(None) is None
        assert ch.get_channel_by_name("missing") is None
        # by_uin（int/str 皆可；未配置 bot_uin 的通道不参与匹配）
        assert ch.get_channel_by_uin("3494144273") is c1
        assert ch.get_channel_by_uin(1470993983) is c2
        assert ch.get_channel_by_uin("0") is None
        assert ch.get_channel_by_uin(None) is None
        # iter_channels 快照
        assert ch.iter_channels() == {"llonebot": c1, "llonebot2": c2}
    finally:
        # 还原注册表快照（本用例注册的 llonebot/llonebot2 一并移除）
        ch._ACTIVE_CHANNELS.clear()
        ch._ACTIVE_CHANNELS.update(saved_registry)
        # 恢复默认指针为剩余注册表首项（若空则 None）
        rest = list(ch.iter_channels().values())
        if rest:
            ch.set_active_channel(rest[0])
        else:
            import packages.qq.channels as _m
            _m._ACTIVE = None


def test_on_message_filters_by_bot_uin():
    """多账号：通道只处理自己 bot 的 event（其它 self_id 的 event 丢弃）。"""
    c = ch.create_channel("llonebot", ["ws://127.0.0.1:3002"], bot_uin="3494144273")
    received = []

    async def handler(msg):
        received.append(msg)

    c.on_message(handler)
    own = FakeBot()
    own.self_id = "3494144273"
    other = FakeBot()
    other.self_id = "1470993983"
    # 自己 bot 的私聊 → 处理，msg.bot_uin 带来源
    _run(c._on_message(own, FakePrivateEvent("10001", "你好")))
    assert len(received) == 1
    assert received[0].bot_uin == "3494144273"
    # 其它 bot 的私聊 → 丢弃
    _run(c._on_message(other, FakePrivateEvent("10001", "不是我的")))
    assert len(received) == 1
    # 未配置 bot_uin 的通道（单通道兼容）：任何 bot 都处理
    c2 = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    got = []

    async def h2(msg):
        got.append(msg)

    c2.on_message(h2)
    _run(c2._on_message(other, FakePrivateEvent("10001", "谁都收")))
    assert len(got) == 1
    assert got[0].bot_uin == "1470993983"


def test_on_bot_connect_filters_by_bot_uin():
    c = ch.create_channel("llonebot", ["ws://127.0.0.1:3002"], bot_uin="3494144273")
    own = FakeBot()
    own.self_id = "3494144273"
    other = FakeBot()
    other.self_id = "1470993983"
    _run(c._on_bot_connect(other))
    assert c._bot is None  # 非本通道 bot 不采纳
    _run(c._on_bot_connect(own))
    assert c._bot is own


# ── plugin 多账号路由 ──

def test_plugin_get_channel_by_uin_and_reply_routing(monkeypatch, tmp_path):
    """get_channel_by_uin 按注册表查找；handle_qq_message 按消息来源 bot 回复
    （谁收到谁回），落盘按 bot 隔离（方案A：<dir>/<bot_uin>/<target_id>.json）。"""
    ch_a = ch.create_channel("llonebot", ["ws://127.0.0.1:3002"], bot_uin="3494144273")
    ch_b = ch.create_channel("llonebot2", ["ws://127.0.0.1:3003"], bot_uin="1470993983")
    bot_a, bot_b = FakeBot(), FakeBot()
    bot_a.self_id, bot_b.self_id = "3494144273", "1470993983"
    ch_a._bot, ch_b._bot = bot_a, bot_b

    ch.set_active_channel(ch_a, name=ch_a.name)
    ch.set_active_channel(ch_b, name=ch_b.name)
    ch.set_active_channel(ch_a)  # 默认 = 第一个
    saved_plugin_channel = qq._channel
    qq._channel = ch_a
    qq._HISTORY_DIR = tmp_path / "history"
    qq._INBOX_DIR = tmp_path / "inbox"
    monkeypatch.setattr(qq, "_qq_mode", lambda: "selective")
    monkeypatch.setattr(qq, "_command_routes_loaded", True)
    monkeypatch.setattr(qq, "_match_command_route", lambda text: None)

    try:
        assert qq.get_channel_by_uin("3494144273") is ch_a
        assert qq.get_channel_by_uin("1470993983") is ch_b

        m1 = QQMessage(scope="user", scope_id="10001", text="hi",
                       bot_uin="3494144273")
        _run(qq.handle_qq_message(m1))
        m2 = QQMessage(scope="user", scope_id="10001", text="hello",
                       bot_uin="1470993983")
        _run(qq.handle_qq_message(m2))
        # selective 模式不回复；两个 bot 零调用
        assert bot_a.calls == [] and bot_b.calls == []
        # 同一 target 在两个 bot 下是独立会话文件（方案A 目录分层）
        assert (tmp_path / "history" / "3494144273" / "10001.json").exists()
        assert (tmp_path / "history" / "1470993983" / "10001.json").exists()
        assert (tmp_path / "inbox" / "3494144273" / "10001.json").exists()
        assert (tmp_path / "inbox" / "1470993983" / "10001.json").exists()
        # 按 bot_uin 读各自的 inbox，每条带 bot_uin 来源
        in_a = _run(qq.api_inbox("10001", bot_uin="3494144273"))
        assert [m.get("bot_uin") for m in in_a["messages"]] == ["3494144273"]
        assert in_a["messages"][0]["text"] == "hi"
        in_b = _run(qq.api_inbox("10001", bot_uin="1470993983"))
        assert [m.get("bot_uin") for m in in_b["messages"]] == ["1470993983"]
        assert in_b["messages"][0]["text"] == "hello"
        # history 同样按 bot 隔离可读
        h_a = _run(qq.api_history("10001", bot_uin="3494144273"))
        assert [m.get("bot_uin") for m in h_a["messages"]] == ["3494144273"]
        h_b = _run(qq.api_history("10001", bot_uin="1470993983"))
        assert [m.get("bot_uin") for m in h_b["messages"]] == ["1470993983"]
    finally:
        qq._channel = saved_plugin_channel
        for p in (
            tmp_path / "history" / "3494144273" / "10001.json",
            tmp_path / "history" / "1470993983" / "10001.json",
            tmp_path / "inbox" / "3494144273" / "10001.json",
            tmp_path / "inbox" / "1470993983" / "10001.json",
            tmp_path / "history" / "10001.json",
            tmp_path / "inbox" / "10001.json",
        ):
            p.unlink(missing_ok=True)
        ch._ACTIVE_CHANNELS.pop("llonebot", None)
        ch._ACTIVE_CHANNELS.pop("llonebot2", None)
        rest = list(ch.iter_channels().values())
        if rest:
            ch.set_active_channel(rest[0])
        else:
            import packages.qq.channels as _m
            _m._ACTIVE = None


def test_api_send_with_bot_uin_routes_and_persists(monkeypatch, tmp_path):
    """api_send(bot_uin=...) 用对应 bot 发送；未知 bot_uin 报错。"""
    ch_b = ch.create_channel("llonebot2", ["ws://127.0.0.1:3003"], bot_uin="1470993983")
    bot_b = FakeBot()
    bot_b.self_id = "1470993983"
    ch_b._bot = bot_b
    ch.set_active_channel(ch_b, name=ch_b.name)
    saved_plugin_channel = qq._channel
    qq._channel = None  # 默认通道置空，强制走 bot_uin 路由
    qq._HISTORY_DIR = tmp_path
    try:
        result = _run(qq.api_send("private", "10001", "来自2号", bot_uin="1470993983"))
        assert result["ok"] is True
        assert bot_b.calls[0][0] == "send_private_msg"
        # assistant 落盘到该 bot 隔离路径并带 bot_uin
        assert (tmp_path / "1470993983" / "10001.json").exists()
        hist = _run(qq.api_history("10001", bot_uin="1470993983"))
        assert hist["messages"][-1]["bot_uin"] == "1470993983"
        # 未知 bot_uin → 错误，不落盘
        bad = _run(qq.api_send("private", "10001", "x", bot_uin="9999999"))
        assert bad["ok"] is False and bad["error"]["code"] == "unknown_bot_uin"
        hist2 = _run(qq.api_history("10001", bot_uin="1470993983"))
        assert hist2["messages"][-1]["text"] == "来自2号"  # 失败未追加
    finally:
        qq._channel = saved_plugin_channel
        (tmp_path / "1470993983" / "10001.json").unlink(missing_ok=True)
        (tmp_path / "10001.json").unlink(missing_ok=True)
        ch._ACTIVE_CHANNELS.pop("llonebot2", None)
        rest = list(ch.iter_channels().values())
        if rest:
            ch.set_active_channel(rest[0])
        else:
            import packages.qq.channels as _m
            _m._ACTIVE = None


# ── 会话隔离：bot_uin 空走旧路径 + 旧数据迁移 + 默认 bot 回退 ──

def test_history_path_legacy_and_bot_isolation(tmp_path, monkeypatch):
    """bot_uin 空 → 旧路径 <dir>/<id>.json；非空 → <dir>/<bot>/<id>.json；
    bot 路径首次写入时把旧路径已有文件整体迁移过去（保上下文连续）。"""
    qq._HISTORY_DIR = tmp_path
    monkeypatch.setattr(qq, "_qq_channels", ch)
    # 1) 无 bot_uin → 旧路径
    _run(qq._append_history("10001", "user", "旧消息", bot_uin=None))
    legacy = tmp_path / "10001.json"
    assert legacy.exists()
    assert "bot_uin" not in _run(qq._load_history("10001"))[-1]
    # 2) 旧文件存在 + bot 路径写入 → 迁移
    _run(qq._append_history("10001", "user", "新消息", bot_uin="3494144273"))
    botfile = tmp_path / "3494144273" / "10001.json"
    assert botfile.exists()
    assert not legacy.exists()  # 已整体搬走
    msgs = _run(qq._load_history("10001", "3494144273"))
    assert [m["text"] for m in msgs] == ["旧消息", "新消息"]
    assert msgs[-1]["bot_uin"] == "3494144273"
    # 清理（_HISTORY_DIR 可能被后续测试改写，这里指回 tmp_path）
    qq._HISTORY_DIR = tmp_path
    legacy.unlink(missing_ok=True)
    botfile.unlink(missing_ok=True)


def test_api_read_default_bot_fallback(monkeypatch, tmp_path):
    """读接口 bot_uin 缺省 → 用默认通道的 bot_uin；无注册表 → 旧路径。"""
    qq._HISTORY_DIR = tmp_path / "h"
    qq._INBOX_DIR = tmp_path / "i"
    saved_plugin_channel = qq._channel
    qq._channel = None
    try:
        # 无默认通道 → 旧路径
        _run(qq._append_history("20002", "user", "legacy", bot_uin=None))
        got = _run(qq.api_history("20002"))
        assert got["messages"][0]["text"] == "legacy"
        # 注册默认通道（bot 3494）→ 缺省读 3494 的文件
        ch_a = ch.create_channel("llonebot", ["ws://127.0.0.1:3002"],
                                 bot_uin="3494144273")
        ch.set_active_channel(ch_a, name=ch_a.name)
        _run(qq._append_history("20002", "user", "bot-a", bot_uin="3494144273"))
        got = _run(qq.api_history("20002"))  # 未传 bot_uin
        assert got["messages"][-1]["text"] == "bot-a"
        assert got["messages"][-1]["bot_uin"] == "3494144273"
    finally:
        qq._channel = saved_plugin_channel
        ch._ACTIVE_CHANNELS.pop("llonebot", None)
        rest = list(ch.iter_channels().values())
        if rest:
            ch.set_active_channel(rest[0])
        else:
            import packages.qq.channels as _m
            _m._ACTIVE = None
        for p in (tmp_path / "h" / "20002.json", tmp_path / "h" / "3494144273" / "20002.json"):
            p.unlink(missing_ok=True)


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


# ── 接口契约：upload_file（私聊/群聊文件发送）──

def test_channel_upload_file_private():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    bot = FakeBot()
    c._bot = bot
    result = _run(c.upload_file("private", "10001", "D:/tmp/report.pdf", "报告.pdf"))
    assert result == {"ok": True}
    assert bot.calls == [(
        "upload_private_file",
        {"user_id": 10001, "file": "D:/tmp/report.pdf", "name": "报告.pdf"},
    )]


def test_channel_upload_file_group():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    bot = FakeBot()
    c._bot = bot
    result = _run(c.upload_file("group", "20002", "http://example.com/a.zip", "a.zip"))
    assert result == {"ok": True}
    assert bot.calls == [(
        "upload_group_file",
        {"group_id": 20002, "file": "http://example.com/a.zip", "name": "a.zip"},
    )]


def test_channel_upload_file_name_autoderive():
    """name 缺省时从 file_path 推导文件名（本地路径与 URL 均适用）。"""
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    bot = FakeBot()
    c._bot = bot
    _run(c.upload_file("private", "10001", "D:/tmp/notes.txt"))
    api, kwargs = bot.calls[0]
    assert api == "upload_private_file" and kwargs["name"] == "notes.txt"
    _run(c.upload_file("group", "20002", "http://example.com/dir/img.png"))
    api, kwargs = bot.calls[1]
    assert api == "upload_group_file" and kwargs["name"] == "img.png"


def test_channel_upload_file_validation_errors():
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    assert _run(c.upload_file("channel", "10001", "x"))["error"]["code"] == "invalid_target_type"
    assert _run(c.upload_file("private", "10001", ""))["error"]["code"] == "empty_file_path"
    assert _run(c.upload_file("private", "not-a-number", "x"))["error"]["code"] == "invalid_target_id"
    # 无 _bot，无 bot_fallback → ChannelNotConnected
    result = _run(c.upload_file("private", "10001", "D:/tmp/a.pdf"))
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


def test_on_message_rich_media_appended_to_text():
    """image/face 段渲染为占位描述拼进 text（媒体在前），纯文本部分不变。"""
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    received = []

    async def handler(msg: QQMessage):
        received.append(msg)

    c.on_message(handler)
    segs = [
        _FakeSeg("image", {"url": "https://example.com/a.jpg"}),
        _FakeSeg("text", {"text": "看这个"}),
        _FakeSeg("face", {"id": 5}),
    ]
    _run(c._on_message(FakeBot(), FakePrivateEvent("10001", "看这个", segments=segs)))
    assert len(received) == 1
    m = received[0]
    assert m.text == "[图片: https://example.com/a.jpg] [表情: 5] 看这个"


def test_on_message_pure_media_not_dropped():
    """纯富媒体消息（无文本）不再被丢弃：text 即媒体描述（url 缺失退 file，
    再退裸占位；未知段渲染为 [段类型]）。"""
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    received = []

    async def handler(msg: QQMessage):
        received.append(msg)

    c.on_message(handler)
    segs = [
        _FakeSeg("image", {"file": "abc.image"}),  # 无 url → 退用 file
        _FakeSeg("image", {}),  # url/file 皆无 → 裸 [图片]
        _FakeSeg("record", {"file": "voice.amr"}),  # 未知富媒体段 → [record]
    ]
    _run(c._on_message(FakeBot(), FakePrivateEvent("10001", "   ", segments=segs)))
    assert len(received) == 1
    assert received[0].text == "[图片: abc.image] [图片] [record]"


def test_on_message_file_segment_described():
    """file 段渲染为 [文件: 名 url]；url/path 缺失时逐级降级。"""
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    received = []

    async def handler(msg: QQMessage):
        received.append(msg)

    c.on_message(handler)
    segs = [
        _FakeSeg("file", {"file": "file://报告.pdf", "url": "https://example.com/r.pdf"}),
        _FakeSeg("file", {"file": "notes.txt"}),  # 无 url
        _FakeSeg("file", {"path": "C:/tmp/x.bin"}),  # 无名无 url → [文件]
    ]
    _run(c._on_message(FakeBot(), FakePrivateEvent("10001", "收到", segments=segs)))
    assert len(received) == 1
    assert received[0].text == (
        "[文件: 报告.pdf https://example.com/r.pdf] [文件: notes.txt] [文件] 收到"
    )


def test_on_message_mface_described():
    """mface（QQ 新版表情）段：优先 summary，缺失退 face_id/emoji_id 等 id
    字段（渲染为 mface<id>），全缺失回退 [mface]；qface 同规则。
    下载失败（autouse fixture）时描述保留原 url。"""
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    received = []

    async def handler(msg: QQMessage):
        received.append(msg)

    c.on_message(handler)
    segs = [
        _FakeSeg("mface", {"summary": "维什戴尔笑", "url": "https://example.com/f.gif"}),
        _FakeSeg("mface", {"face_id": "abc123"}),
        _FakeSeg("mface", {"emoji_id": 99}),
        _FakeSeg("mface", {}),
        _FakeSeg("qface", {"summary": "  进化  "}),
    ]
    _run(c._on_message(FakeBot(), FakePrivateEvent("10001", "看表情", segments=segs)))
    assert len(received) == 1
    assert received[0].text == (
        "[表情: 维什戴尔笑 https://example.com/f.gif] "
        "[表情: mfaceabc123] [表情: mface99] [mface] [表情: 进化] 看表情"
    )


def test_on_message_mface_downloaded_to_local_path(tmp_path, monkeypatch):
    """mface 有 url 且下载成功 → 描述带本地路径（文件名前缀 mface_）；
    无 url 的 mface 不触发下载；纯文本部分不受影响。"""
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    received = []

    async def handler(msg):
        received.append(msg)

    c.on_message(handler)
    calls: list[str] = []

    async def fake_download(url, dest_dir, filename, timeout=10.0):
        calls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        p = dest_dir / filename
        p.write_bytes(b"gif")
        return str(p)

    monkeypatch.setattr(ob, "_download", fake_download)
    monkeypatch.setattr(ob, "_media_root", lambda: tmp_path)
    segs = [
        _FakeSeg("mface", {"summary": "笑", "url": "https://example.com/a.gif"}),
        _FakeSeg("mface", {"summary": "无图"}),
        _FakeSeg("text", {"text": "哈哈"}),
    ]
    _run(c._on_message(FakeBot(), FakePrivateEvent("10001", "哈哈", segments=segs)))
    assert len(received) == 1
    m = received[0]
    # 只有带 url 的 mface 触发下载
    assert calls == ["https://example.com/a.gif"]
    match = re.match(r"\[表情: 笑 (.+?)\]", m.text)
    assert match
    path = Path(match.group(1))
    assert path.exists() and path.read_bytes() == b"gif"
    assert path.parent == tmp_path / "user" / "10001"
    assert re.match(r"mface_\d{8}_\d{6}_001\.gif$", path.name)
    assert m.text.endswith(" [表情: 无图] 哈哈")


# ── 入站富媒体自动下载 ──

def test_safe_filename_rules():
    """文件名生成：扩展名从 url 提取（大小写归一），url 无扩展名时退用
    fallback_name，再退 .bin；固定 img/file_时间戳_序号 格式。"""
    # 从 url 提取扩展名（query 不影响，大小写归一）
    assert re.fullmatch(
        r"img_\d{8}_\d{6}_001\.jpg",
        ob._safe_filename("https://example.com/a/Photo.JPG?appid=1", "img", 1),
    )
    # url 无扩展名 → fallback_name 的扩展名
    assert re.fullmatch(
        r"file_\d{8}_\d{6}_002\.pdf",
        ob._safe_filename("https://x/download", "file", 2, fallback_name="报告.pdf"),
    )
    # 都推断不出 → .bin（QQ 多媒体 url 常无扩展名）
    assert re.fullmatch(
        r"img_\d{8}_\d{6}_003\.bin",
        ob._safe_filename("https://multimedia.nt.qq.com.cn/download?appid=x", "img", 3),
    )
    # 假扩展名（后跟路径/查询残留）不误判
    assert ob._safe_filename("https://x/a.zip/evil?y=1", "img", 4).endswith(".bin")


def test_download_writes_file_and_ext_from_content_type(tmp_path, monkeypatch):
    """_download 真实写入文件；dest 扩展名为 .bin 时按 content-type 修正。"""

    class _Resp:
        def __init__(self):
            self.headers = {"content-type": "image/png; charset=utf-8"}

        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            yield b"hello"

    class _StreamCtx:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url):
            return _StreamCtx()

    monkeypatch.setattr(ob, "httpx", SimpleNamespace(AsyncClient=_Client))
    path = _run(_REAL_DOWNLOAD("https://x/download", tmp_path, "img_1.bin"))
    assert path is not None
    p = Path(path)
    assert p.suffix == ".png" and p.read_bytes() == b"hello"
    # 失败不留 .part 残留
    assert not list(tmp_path.glob("*.part"))


def test_download_failure_returns_none(tmp_path, monkeypatch):
    """下载异常（连接失败/HTTP 错误）静默返回 None，不抛不阻塞。"""

    class _BoomClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            raise RuntimeError("boom")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(ob, "httpx", SimpleNamespace(AsyncClient=_BoomClient))
    assert _run(_REAL_DOWNLOAD("https://x/a.jpg", tmp_path, "img_1.jpg")) is None


def test_on_message_media_downloaded_to_local_path(tmp_path, monkeypatch):
    """下载成功 → 描述带本地绝对路径，落盘 data/qq_media/<scope>/<target_id>/。"""
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    received = []

    async def handler(msg):
        received.append(msg)

    c.on_message(handler)

    async def fake_download(url, dest_dir, filename, timeout=10.0):
        dest_dir.mkdir(parents=True, exist_ok=True)
        p = dest_dir / filename
        p.write_bytes(b"data")
        return str(p)

    monkeypatch.setattr(ob, "_download", fake_download)
    monkeypatch.setattr(ob, "_media_root", lambda: tmp_path)
    segs = [
        _FakeSeg("image", {"url": "https://example.com/a.jpg"}),
        _FakeSeg("text", {"text": "看这个"}),
    ]
    _run(c._on_message(FakeBot(), FakePrivateEvent("10001", "看这个", segments=segs)))
    assert len(received) == 1
    m = received[0]
    match = re.match(r"\[图片: (.+?)\]", m.text)
    assert match
    path = Path(match.group(1))
    assert path.exists() and path.read_bytes() == b"data"
    # 落盘约定：<root>/<scope>/<scope_id>/img_时间戳_序号.扩展名
    assert path.parent == tmp_path / "user" / "10001"
    assert re.match(r"img_\d{8}_\d{6}_001\.jpg$", path.name)
    # 纯文本部分不受影响
    assert m.text.endswith(" 看这个")


def test_on_message_media_download_failure_keeps_url(tmp_path, monkeypatch):
    """下载失败 → 描述保留原 url，不阻塞消息处理。"""
    c = ch.create_channel("napcat", ["ws://127.0.0.1:3001"])
    received = []

    async def handler(msg):
        received.append(msg)

    c.on_message(handler)
    monkeypatch.setattr(ob, "_media_root", lambda: tmp_path)
    # ob._download 已被 autouse fixture 置为失败 no-op
    segs = [
        _FakeSeg("image", {"url": "https://example.com/a.jpg"}),
        _FakeSeg("file", {"file": "file://报告.pdf", "url": "https://example.com/r.pdf"}),
    ]
    _run(c._on_message(FakeBot(), FakePrivateEvent("10001", "收到", segments=segs)))
    assert len(received) == 1
    assert received[0].text == (
        "[图片: https://example.com/a.jpg] "
        "[文件: 报告.pdf https://example.com/r.pdf] 收到"
    )
    # 未落盘任何文件
    assert not list(tmp_path.rglob("*"))


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
