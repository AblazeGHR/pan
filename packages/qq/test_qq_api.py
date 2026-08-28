"""Unit tests for packages/qq/plugin.py QQ HTTP API logic (mock bot, no network).

Covers:
    - api_send: private/group routing through bot.call_api, message_id return,
      invalid target_type / target_id / empty text / bot-not-connected errors
    - api_history: empty / roundtrip after _append_history, limit capping
    - outgoing sends are persisted as assistant messages for qq_read_conversation
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import nonebot  # noqa: E402

# Must init the NoneBot driver before importing the plugin module (plugin.py
# calls get_driver() at import time and mounts routes on server_app).
nonebot.init()

from packages.qq import plugin as qq  # noqa: E402
from packages.qq.channels import QQMessage  # noqa: E402


class FakeBot:
    """Minimal OneBot Bot stand-in that records call_api invocations."""

    self_id = "10000"

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.api_result = {"message_id": 12345}
        # Per-API result overrides (merged recent_contacts tests use this).
        self.api_results: dict[str, object] = {}

    async def call_api(self, api: str, **kwargs):
        self.calls.append((api, kwargs))
        result = self.api_results.get(api, self.api_result)
        if isinstance(result, Exception):
            raise result
        return result


def _run(coro):
    return asyncio.run(coro)


# ── api_send ──

def test_send_private_routes_to_send_private_msg(tmp_path):
    qq._HISTORY_DIR = tmp_path
    bot = FakeBot()
    qq._active_bot = bot
    try:
        result = _run(qq.api_send("private", "10001", "hello [CQ:face,id=1]"))
        assert result["ok"] is True
        assert result["message_id"] == 12345
        api, kwargs = bot.calls[0]
        assert api == "send_private_msg"
        assert kwargs["user_id"] == 10001
        assert kwargs["message"] == "hello [CQ:face,id=1]"
    finally:
        qq._active_bot = None


def test_send_group_routes_to_send_group_msg(tmp_path):
    qq._HISTORY_DIR = tmp_path
    bot = FakeBot()
    qq._active_bot = bot
    try:
        result = _run(qq.api_send("group", "20002", "群公告"))
        assert result["ok"] is True
        api, kwargs = bot.calls[0]
        assert api == "send_group_msg"
        assert kwargs["group_id"] == 20002
        assert kwargs["message"] == "群公告"
    finally:
        qq._active_bot = None


def test_send_string_numeric_target_id(tmp_path):
    qq._HISTORY_DIR = tmp_path
    bot = FakeBot()
    qq._active_bot = bot
    try:
        result = _run(qq.api_send("private", "10001", "x"))
        assert result["ok"] is True
        assert bot.calls[0][1]["user_id"] == 10001  # int coercion
    finally:
        qq._active_bot = None


def test_send_invalid_target_type():
    result = _run(qq.api_send("guild", "1", "x"))
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_target_type"


def test_send_invalid_target_id():
    result = _run(qq.api_send("private", "not-a-number", "x"))
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_target_id"


def test_send_empty_text():
    result = _run(qq.api_send("private", "10001", ""))
    assert result["ok"] is False
    assert result["error"]["code"] == "empty_text"


def test_send_bot_not_connected():
    qq._active_bot = None
    result = _run(qq.api_send("private", "10001", "x"))
    assert result["ok"] is False
    assert result["error"]["code"] == "bot_not_connected"


def test_send_api_failure(tmp_path):
    qq._HISTORY_DIR = tmp_path
    bot = FakeBot()
    bot.api_result = RuntimeError("send blocked")
    qq._active_bot = bot
    try:
        result = _run(qq.api_send("private", "10001", "x"))
        assert result["ok"] is False
        assert result["error"]["code"] == "send_failed"
        assert "send blocked" in result["error"]["message"]
    finally:
        qq._active_bot = None


# ── history persistence ──

def test_history_empty(tmp_path):
    qq._HISTORY_DIR = tmp_path
    result = _run(qq.api_history("10001"))
    assert result["target_id"] == "10001"
    assert result["messages"] == []


def test_append_and_read_roundtrip(tmp_path):
    qq._HISTORY_DIR = tmp_path
    _run(qq._append_history("10001", "user", "你好"))
    _run(qq._append_history("10001", "assistant", "你好，有什么可以帮你？"))
    result = _run(qq.api_history("10001", limit=10))
    assert len(result["messages"]) == 2
    assert result["messages"][0] == {"role": "user", "text": "你好", "time": result["messages"][0]["time"]}
    assert result["messages"][1]["role"] == "assistant"
    assert result["messages"][1]["text"] == "你好，有什么可以帮你？"


def test_history_limit_takes_newest(tmp_path):
    qq._HISTORY_DIR = tmp_path
    for i in range(5):
        _run(qq._append_history("10001", "user", f"msg-{i}"))
    result = _run(qq.api_history("10001", limit=2))
    assert [m["text"] for m in result["messages"]] == ["msg-3", "msg-4"]


def test_history_sanitizes_target_id(tmp_path):
    qq._HISTORY_DIR = tmp_path
    _run(qq._append_history("../../evil", "user", "x"))
    # traversal attempts stay inside the history dir
    assert not (tmp_path.parent / "evil.json").exists()
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1


def test_send_records_outgoing_assistant_message(tmp_path):
    qq._HISTORY_DIR = tmp_path
    bot = FakeBot()
    qq._active_bot = bot
    try:
        result = _run(qq.api_send("private", "10001", "主动推送"))
        assert result["ok"] is True
        history = _run(qq.api_history("10001"))
        assert len(history["messages"]) == 1
        assert history["messages"][0]["role"] == "assistant"
        assert history["messages"][0]["text"] == "主动推送"
    finally:
        qq._active_bot = None


# ── recent contacts (best-effort merged list) ──

def test_recent_contacts_merges_full_lists():
    """近期会话 + 完整好友/群 合并去重：近期优先、异常条目剔除、备注/昵称兜底。"""
    bot = FakeBot()
    bot.api_results = {
        "get_recent_contact": [
            {"chatType": 1, "peerUin": "10001", "peerName": "recent-name", "remark": "备注A"},
            {"chatType": 2, "peerUin": "20001", "peerName": "recent-group"},
            # 异常条目：chatType 非 1/2、peerUin "0" → 剔除
            {"chatType": 8, "peerUin": "0", "peerName": ""},
            {"chatType": 7, "peerUin": "0", "peerName": "某频道"},
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
    qq._active_bot = bot
    try:
        result = _run(qq.api_recent_contacts())
        assert result["ok"] is True
        contacts = result["contacts"]
        by_uin = {c["peerUin"]: c for c in contacts}
        # 近期优先 + 完整列表补齐 + 去重（10001/20001 仅出现一次）
        assert [c["peerUin"] for c in contacts] == ["10001", "20001", "10002", "20002"]
        # 近期会话保留其 peerName；私聊名称兜底用 remark 或 nickname
        assert by_uin["10001"]["peerName"] == "recent-name"
        assert by_uin["10001"]["chatType"] == 1
        assert by_uin["20001"]["peerName"] == "recent-group"
        assert by_uin["20001"]["chatType"] == 2
        # 非近期好友：remark 优先于 nickname
        assert by_uin["10002"]["peerName"] == "同学B"
        assert by_uin["20002"]["peerName"] == "group-b"
        # 字段契约：peerUin/peerName/chatType
        assert set(contacts[0]) == {"peerUin", "peerName", "chatType"}
        # 调用了三个 API
        assert [a for a, _ in bot.calls] == [
            "get_recent_contact", "get_friend_list", "get_group_list"]
    finally:
        qq._active_bot = None


def test_recent_contacts_missing_name_falls_back_to_uin():
    bot = FakeBot()
    bot.api_results = {
        "get_recent_contact": [],
        "get_friend_list": [{"user_id": 10001, "nickname": "", "remark": ""}],
        "get_group_list": [{"group_id": 20001, "group_name": ""}],
    }
    qq._active_bot = bot
    try:
        result = _run(qq.api_recent_contacts())
        assert result["ok"] is True
        by_uin = {c["peerUin"]: c for c in result["contacts"]}
        assert by_uin["10001"]["peerName"] == "10001"  # 兜底显示 QQ 号
        assert by_uin["20001"]["peerName"] == "20001"
    finally:
        qq._active_bot = None


def test_recent_contacts_recent_fails_still_lists():
    """get_recent_contact 不受支持时回退为完整好友/群列表，仍返回 ok:true。"""
    bot = FakeBot()
    bot.api_results = {
        "get_recent_contact": RuntimeError("API not found"),
        "get_friend_list": [{"user_id": 10001, "nickname": "alice", "remark": ""}],
        "get_group_list": [],
    }
    qq._active_bot = bot
    try:
        result = _run(qq.api_recent_contacts())
        assert result["ok"] is True
        assert result["contacts"][0]["peerUin"] == "10001"
    finally:
        qq._active_bot = None


def test_recent_contacts_unsupported():
    """全部列表都失败且无数据 → ok:false（unsupported）。"""
    bot = FakeBot()
    bot.api_result = RuntimeError("API not found")
    qq._active_bot = bot
    try:
        result = _run(qq.api_recent_contacts())
        assert result["ok"] is False
        assert result["error"]["code"] == "unsupported"
    finally:
        qq._active_bot = None


# ── selective mode / inbox（PAN_QQ_MODE=selective）──

# 业务层只认 QQMessage（通道已把 OneBot event 归一化）；群消息 @-bot 过滤在通道
# hook 内完成，见 packages/qq/test_channels.py。这里用 QQMessage 直接驱动
# handle_qq_message，收发经当前通道抽象（FakeBot 经 channel._bot 注入）。


class FakeRouteResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeRouteClient:
    def __init__(self, payload):
        self.payload = payload
        self.posted = []

    async def post(self, url, json=None):
        self.posted.append((url, json))
        return FakeRouteResponse(self.payload)


def _prepare_handle_test(tmp_path, monkeypatch, mode):
    """Common setup for handle_qq_message tests: isolated dirs, clean state, env.

    把 FakeBot 注入当前通道（channel._bot），使 handle_qq_message 经通道抽象收发。
    """
    qq._HISTORY_DIR = tmp_path / "hist"
    qq._INBOX_DIR = tmp_path / "inbox"
    qq._sessions.clear()
    qq._command_routes_loaded = True
    qq._command_routes = []
    monkeypatch.setenv("PAN_QQ_MODE", mode)
    bot = FakeBot()
    qq.get_channel()._bot = bot
    return bot


def test_qq_mode_default_mirror(monkeypatch):
    monkeypatch.delenv("PAN_QQ_MODE", raising=False)
    # 隔离用户 config.json（可能配了 qq.mode=selective），验证无配置时默认 mirror
    monkeypatch.setattr(qq, "_load_config", lambda: {})
    assert qq._qq_mode() == "mirror"


def test_qq_mode_selective_and_invalid(monkeypatch):
    monkeypatch.setenv("PAN_QQ_MODE", "selective")
    assert qq._qq_mode() == "selective"
    monkeypatch.setenv("PAN_QQ_MODE", " bogus ")
    assert qq._qq_mode() == "mirror"  # 非法值回退 mirror


def test_selective_writes_inbox_and_history_no_reply(tmp_path, monkeypatch):
    bot = _prepare_handle_test(tmp_path, monkeypatch, "selective")
    _run(qq.handle_qq_message(QQMessage(scope="user", scope_id="10001", text="你好")))
    # selective 模式不自动回复（收发经通道，这里不应调用 send）
    assert bot.calls == []
    # 不建 session / 不 spawn（_sessions 保持空）
    assert qq._sessions == {}
    # 消息进 inbox（含 id/text/time）
    inbox = _run(qq.api_inbox("10001", limit=10))
    assert len(inbox["messages"]) == 1
    m = inbox["messages"][0]
    assert m["text"] == "你好"
    assert m["id"] and m["time"]
    # 消息进 history（user 侧）
    hist = _run(qq.api_history("10001"))
    assert [h["role"] for h in hist["messages"]] == ["user"]
    assert hist["messages"][0]["text"] == "你好"


def test_selective_group_at_message_goes_inbox(tmp_path, monkeypatch):
    bot = _prepare_handle_test(tmp_path, monkeypatch, "selective")
    _run(qq.handle_qq_message(QQMessage(scope="group", scope_id="20002", text="群聊测试")))
    assert bot.calls == []
    inbox = _run(qq.api_inbox("20002"))
    assert [m["text"] for m in inbox["messages"]] == ["群聊测试"]
    hist = _run(qq.api_history("20002"))
    assert [h["role"] for h in hist["messages"]] == ["user"]


# 群消息 @-bot 过滤已在通道 hook（OneBotChannel._on_message）内完成，见
# packages/qq/test_channels.py 的 test_on_message_group_requires_at_bot。


def test_inbox_consume_deletes_all(tmp_path):
    qq._HISTORY_DIR = tmp_path / "hist"
    qq._INBOX_DIR = tmp_path / "inbox"
    _run(qq._append_inbox("10001", "user", "a"))
    _run(qq._append_inbox("10001", "user", "b"))
    result = _run(qq.api_inbox("10001", limit=10, consume=True))
    assert [m["text"] for m in result["messages"]] == ["a", "b"]
    # 消费即删
    assert _run(qq.api_inbox("10001", limit=10))["messages"] == []


def test_inbox_consume_partial_keeps_rest(tmp_path):
    qq._HISTORY_DIR = tmp_path / "hist"
    qq._INBOX_DIR = tmp_path / "inbox"
    _run(qq._append_inbox("10001", "user", "a"))
    _run(qq._append_inbox("10001", "user", "b"))
    result = _run(qq.api_inbox("10001", limit=1, consume=True))
    assert [m["text"] for m in result["messages"]] == ["a"]
    rest = _run(qq.api_inbox("10001", limit=10))
    assert [m["text"] for m in rest["messages"]] == ["b"]


def test_inbox_clear(tmp_path):
    qq._HISTORY_DIR = tmp_path / "hist"
    qq._INBOX_DIR = tmp_path / "inbox"
    _run(qq._append_inbox("10001", "user", "x"))
    result = _run(qq.api_inbox_clear("10001"))
    assert result["ok"] is True
    assert _run(qq.api_inbox("10001"))["messages"] == []


def test_selective_command_route_still_executes(tmp_path, monkeypatch):
    bot = _prepare_handle_test(tmp_path, monkeypatch, "selective")
    qq._command_routes = [([".test"], "http://fake.local/route")]
    client = FakeRouteClient({"result": "route-ok"})

    async def _fake_client():
        return client

    monkeypatch.setattr(qq, "_get_client", _fake_client)
    _run(qq.handle_qq_message(QQMessage(scope="user", scope_id="10001", text=".test hello")))
    # command route 直连外部 HTTP、绕过 LLM → selective 下仍执行并回复
    # processing 提示 + route-ok 均经通道 send（call_api）发出
    assert [c[0] for c in bot.calls] == ["send_private_msg", "send_private_msg"]
    assert bot.calls[0][1]["message"] == "processing, please wait..."
    assert bot.calls[1][1]["message"] == "route-ok"
    # 过滤 _append_inbox 触发的 Pan Core notify（best-effort），只断言路由调用
    route_posts = [(u, b) for (u, b) in client.posted if not u.endswith("/api/qq/notify")]
    assert route_posts == [("http://fake.local/route", {"text": "hello"})]
    # 记录 user + assistant 双侧 history
    hist = _run(qq.api_history("10001"))
    assert [h["role"] for h in hist["messages"]] == ["user", "assistant"]
    assert hist["messages"][1]["text"] == "route-ok"


def test_mirror_mode_behavior_unchanged(tmp_path, monkeypatch):
    bot = _prepare_handle_test(tmp_path, monkeypatch, "mirror")
    called = []

    async def _fake_send_and_wait(text, scope_id, scope="user"):
        called.append((text, scope_id, scope))
        return "mirror reply"

    monkeypatch.setattr(qq, "_send_and_wait", _fake_send_and_wait)
    _run(qq.handle_qq_message(QQMessage(scope="user", scope_id="10001", text="你好")))
    # 自动回复（现状兼容），经通道 send（call_api）
    assert [c[0] for c in bot.calls] == ["send_private_msg", "send_private_msg"]
    assert bot.calls[0][1]["message"] == "processing, please wait..."
    assert bot.calls[1][1]["message"] == "mirror reply"
    assert called == [("你好", "10001", "user")]
    # mirror 模式不写 inbox
    assert _run(qq.api_inbox("10001"))["messages"] == []
    # history 完整（user + assistant）
    hist = _run(qq.api_history("10001"))
    assert [h["role"] for h in hist["messages"]] == ["user", "assistant"]


# ── MCP qq_read_inbox ──

def test_mcp_qq_read_inbox_consume(monkeypatch):
    from packages.qq import mcp as qq_mcp
    captured = {}

    async def _fake_api(method, path, body=None, timeout=30.0):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"target_id": "10001", "messages": [{"id": "1", "text": "hi", "time": "t"}]}

    monkeypatch.setattr(qq_mcp, "_api", _fake_api)
    result = _run(qq_mcp.qq_read_inbox("10001", limit=10, consume=True))
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/qq/inbox"
    assert captured["body"] == {"target_id": "10001", "limit": 10, "consume": 1}
    assert result["messages"][0]["text"] == "hi"


def test_mcp_qq_read_inbox_defaults(monkeypatch):
    from packages.qq import mcp as qq_mcp
    captured = {}

    async def _fake_api(method, path, body=None, timeout=30.0):
        captured["body"] = body
        return {"target_id": "10001", "messages": []}

    monkeypatch.setattr(qq_mcp, "_api", _fake_api)
    _run(qq_mcp.qq_read_inbox("10001"))
    assert captured["body"] == {"target_id": "10001", "limit": 30, "consume": 0}


# ── QQ session 绑定（qq_bind/qq_unbind、提醒格式、session 字段）──


def test_format_report_batch_qq_branch():
    from packages.core import worker
    item = {"type": "qq", "qqTarget": "user:1234567890", "targetType": "user",
            "targetId": "1234567890", "nickname": "TestUser", "text": "你好",
            "time": "2026-08-22 01:00:00"}
    formatted = worker._format_report_batch([item])
    assert "@@@@by qq : user:1234567890 | TestUser" in formatted
    assert "message:" in formatted and "你好" in formatted
    assert "time: 2026-08-22 01:00:00" in formatted


def test_format_report_batch_agent_branch_unchanged():
    from packages.core import worker
    item = {"status": "done", "result": "ok", "sessionId": "ses_x",
            "taskId": "t1", "workerId": "w1"}
    formatted = worker._format_report_batch([item])
    assert formatted.startswith("@@@@by agent : ses_x")
    assert "status: done" in formatted


def test_qq_bind_missing_identity(monkeypatch):
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    from packages.qq import mcp as qq_mcp
    r = _run(qq_mcp.qq_bind("user", "1234567890"))
    assert r["ok"] is False
    assert r["error"]["code"] == "missing_identity"


def test_qq_unbind_missing_identity(monkeypatch):
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    from packages.qq import mcp as qq_mcp
    r = _run(qq_mcp.qq_unbind("user", "1234567890"))
    assert r["ok"] is False
    assert r["error"]["code"] == "missing_identity"


def test_qq_bind_invalid_target_type(monkeypatch):
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_test")
    from packages.qq import mcp as qq_mcp
    r = _run(qq_mcp.qq_bind("guild", "1"))
    assert r["ok"] is False
    assert r["error"]["code"] == "invalid_target_type"


def test_qq_bind_passes_identity_to_pan_core(monkeypatch):
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_abc")
    from packages.qq import mcp as qq_mcp
    captured = {}

    async def _fake_api(method, path, body=None, timeout=30.0, base_url=None):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        captured["base_url"] = base_url
        return {"sessionId": "ses_abc", "qqTarget": "user:1234567890",
                "subscribed": True, "qqSubscriptions": ["user:1234567890"]}

    monkeypatch.setattr(qq_mcp, "_api", _fake_api)
    result = _run(qq_mcp.qq_bind("user", "1234567890"))
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/qq/subscribe"
    assert captured["body"] == {"sessionId": "ses_abc", "target_type": "user",
                                "target_id": "1234567890"}
    assert captured["base_url"] == qq_mcp._pan_api_url
    assert result["subscribed"] is True


def test_session_qq_subscriptions_roundtrip(monkeypatch, tmp_path):
    from packages.core import session as sess
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    s = sess.create(name="test-qq-subs", model="deepseek-v4-flash")
    s.qq_subscriptions.add("user:1234567890")
    sess.save(s)
    loaded = sess.get(s.id)
    assert loaded.qq_subscriptions == {"user:1234567890"}
    sess.delete(s.id)


def test_enqueue_qq_reminder_delivers_to_subscribers(monkeypatch, tmp_path):
    import types
    from packages.core import session as sess
    from packages.core import worker
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    sub = sess.create(name="sub", model="m")
    other = sess.create(name="other", model="m")
    sub.qq_subscriptions.add("user:1234567890")
    # 隔离 worker 视角的 session store，只含测试 session（避免读真实落盘）
    monkeypatch.setattr(worker, "_sess", types.SimpleNamespace(
        list_all=lambda: [sub, other],
        save_async=lambda s: asyncio.sleep(0),
        get=lambda sid: next((x for x in [sub, other] if x.id == sid), None),
    ))
    monkeypatch.setattr(worker, "create_worker", AsyncMock())  # auto_spawn 不真建进程
    delivered = _run(worker.enqueue_qq_reminder(
        "user", "1234567890", nickname="TestUser", text="你好", time_str="t"))
    assert delivered == 1
    assert len(sub.queue_pending) == 1
    item = sub.queue_pending[0]
    assert item["type"] == "qq"
    assert item["qqTarget"] == "user:1234567890"
    assert item["nickname"] == "TestUser" and item["text"] == "你好"
    assert other.queue_pending == []


def test_enqueue_qq_reminder_bot_scoped_subscription(monkeypatch, tmp_path):
    """多账号订阅区分：bot_uin 非空时同时命中旧键（不区分 bot）与
    <type>:<id>@<bot> 精确键；提醒项带 botUin 字段。"""
    import types
    from packages.core import session as sess
    from packages.core import worker
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    legacy_sub = sess.create(name="legacy-sub", model="m")   # 旧键：任何 bot
    bot_sub = sess.create(name="bot-sub", model="m")         # 精确键：仅 1470993983
    outsider = sess.create(name="outsider", model="m")       # 订阅别的 bot
    legacy_sub.qq_subscriptions.add("user:1234567890")
    bot_sub.qq_subscriptions.add("user:1234567890@1470993983")
    outsider.qq_subscriptions.add("user:1234567890@3494144273")
    monkeypatch.setattr(worker, "_sess", types.SimpleNamespace(
        list_all=lambda: [legacy_sub, bot_sub, outsider],
        save_async=lambda s: asyncio.sleep(0),
        get=lambda sid: next(
            (x for x in [legacy_sub, bot_sub, outsider] if x.id == sid), None),
    ))
    monkeypatch.setattr(worker, "create_worker", AsyncMock())  # auto_spawn 不真建进程
    delivered = _run(worker.enqueue_qq_reminder(
        "user", "1234567890", nickname="TestUser", text="hi", time_str="t",
        bot_uin="1470993983"))
    assert delivered == 2  # legacy_sub + bot_sub
    assert len(legacy_sub.queue_pending) == 1
    item = legacy_sub.queue_pending[0]
    assert item["botUin"] == "1470993983"
    assert item["qqTarget"] == "user:1234567890"
    assert len(bot_sub.queue_pending) == 1
    assert outsider.queue_pending == []
    # bot_uin 为空（旧来源）→ 仅旧键命中
    delivered2 = _run(worker.enqueue_qq_reminder(
        "user", "1234567890", nickname="U", text="x", time_str="t"))
    assert delivered2 == 1
    assert legacy_sub.queue_pending[1].get("botUin") is None
    assert len(bot_sub.queue_pending) == 1  # 精确键不被空 bot 命中
    for s in (legacy_sub, bot_sub, outsider):
        sess.delete(s.id)


def test_format_report_batch_qq_header_with_bot():
    """@@@@by qq 抬头带 bot 来源标识：botUin 非空 → `| bot <uin>`；空 → 旧格式。"""
    from packages.core import worker
    rendered = worker._format_report_batch([{
        "type": "qq", "qqTarget": "user:1234567890", "targetType": "user",
        "targetId": "1234567890", "nickname": "Alice", "botUin": "3494144273",
        "text": "hello", "time": "2026-08-28 12:00:00",
    }])
    lines = rendered.split("\n")
    assert lines[0] == "@@@@by qq : user:1234567890 | Alice | bot 3494144273"
    assert "botUin: 3494144273" in lines
    # 旧来源（无 botUin）保持旧抬头
    rendered2 = worker._format_report_batch([{
        "type": "qq", "qqTarget": "user:1234567890", "targetType": "user",
        "targetId": "1234567890", "nickname": "Alice",
        "text": "hello", "time": "t",
    }])
    assert rendered2.split("\n")[0] == "@@@@by qq : user:1234567890 | Alice"
    assert "botUin" not in rendered2


# ── api_recent_contacts(bot_uin) / api_channels（多账号 bot 维度）──


def test_recent_contacts_bot_uin_routes_to_channel(monkeypatch):
    """bot_uin 提供时按号取对应通道拉联系人。"""
    calls = []

    class FakeCh:
        async def recent_contacts(self):
            calls.append(self)
            return {"ok": True, "contacts": [
                {"peerUin": "555", "peerName": "B2", "chatType": 1}]}

    ch = FakeCh()
    monkeypatch.setattr(
        qq, "get_channel_by_uin",
        lambda u: ch if str(u) == "1470993983" else None)
    result = _run(qq.api_recent_contacts("1470993983"))
    assert result["ok"] is True
    assert result["contacts"][0]["peerUin"] == "555"
    assert calls == [ch]


def test_recent_contacts_unknown_bot_uin(monkeypatch):
    """未注册的 bot_uin → ok:false（unknown_bot_uin），不落到默认通道。"""
    monkeypatch.setattr(qq, "get_channel_by_uin", lambda u: None)

    def boom():
        raise AssertionError("should not hit default channel")
    monkeypatch.setattr(qq, "get_channel", boom)
    result = _run(qq.api_recent_contacts("999"))
    assert result["ok"] is False
    assert result["error"]["code"] == "unknown_bot_uin"


def test_recent_contacts_default_channel_when_no_bot_uin(monkeypatch):
    """缺省 bot_uin → 默认通道（向后兼容）。"""
    class FakeCh:
        async def recent_contacts(self):
            return {"ok": True, "contacts": [
                {"peerUin": "1", "peerName": "A", "chatType": 1}]}

    monkeypatch.setattr(qq, "get_channel", lambda: FakeCh())
    result = _run(qq.api_recent_contacts())
    assert result["ok"] is True
    assert result["contacts"][0]["peerUin"] == "1"


def test_api_channels_lists_registry(monkeypatch):
    """/api/qq/channels 列出注册通道：name/bot_uin/connected；空 bot_uin 保留。"""
    from types import SimpleNamespace

    class FakeCh:
        name = "llonebot"

        def __init__(self, bot_uin, connected=True):
            self.config = SimpleNamespace(bot_uin=bot_uin)
            self._connected = connected

        async def is_connected(self):
            return self._connected

    c1 = FakeCh("3494144273")
    c2 = FakeCh("1470993983", connected=False)
    c3 = FakeCh(None)  # 未配置 bot_uin 的通道（单通道兼容）
    monkeypatch.setattr(qq._qq_channels, "_ACTIVE_CHANNELS",
                        {"llonebot": c1, "llonebot2": c2, "napcat": c3})
    monkeypatch.setattr(qq._qq_channels, "_ACTIVE", c1)
    result = _run(qq.api_channels())
    assert result["ok"] is True
    by_uin = {c["bot_uin"]: c for c in result["channels"]}
    assert set(by_uin) == {"3494144273", "1470993983", ""}
    assert by_uin["3494144273"]["connected"] is True
    assert by_uin["1470993983"]["connected"] is False
    assert by_uin["3494144273"]["name"] == "llonebot"


def test_api_channels_active_fallback(monkeypatch):
    """注册表为空但存在默认通道时，至少列出一个（单通道部署）。"""
    from types import SimpleNamespace

    class FakeCh:
        name = "napcat"

        def __init__(self):
            self.config = SimpleNamespace(bot_uin="3494144273")

        async def is_connected(self):
            return True

    ch = FakeCh()
    monkeypatch.setattr(qq._qq_channels, "_ACTIVE_CHANNELS", {})
    monkeypatch.setattr(qq._qq_channels, "_ACTIVE", ch)
    result = _run(qq.api_channels())
    assert result["ok"] is True
    assert len(result["channels"]) == 1
    assert result["channels"][0]["bot_uin"] == "3494144273"
