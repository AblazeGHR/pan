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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import nonebot  # noqa: E402

# Must init the NoneBot driver before importing the plugin module (plugin.py
# calls get_driver() at import time and mounts routes on server_app).
nonebot.init()

from nonebot.adapters.onebot.v11 import GroupMessageEvent  # noqa: E402

from packages.qq import plugin as qq  # noqa: E402


class FakeBot:
    """Minimal OneBot Bot stand-in that records call_api invocations."""

    self_id = "10000"

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.api_result = {"message_id": 12345}

    async def call_api(self, api: str, **kwargs):
        self.calls.append((api, kwargs))
        if isinstance(self.api_result, Exception):
            raise self.api_result
        return self.api_result


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


# ── recent contacts (best-effort) ──

def test_recent_contacts_passthrough():
    bot = FakeBot()
    bot.api_result = [{"user_id": 10001, "nickname": "alice"}]
    qq._active_bot = bot
    try:
        result = _run(qq.api_recent_contacts())
        assert result["ok"] is True
        assert result["contacts"][0]["user_id"] == 10001
    finally:
        qq._active_bot = None


def test_recent_contacts_unsupported():
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

class FakeEvent:
    """Minimal MessageEvent stand-in for handle_message tests (private scope)."""

    def __init__(self, user_id, text):
        self._user_id = user_id
        self._text = text

    def get_user_id(self):
        return self._user_id

    def get_plaintext(self):
        return self._text


class _FakeSeg:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class FakeGroupEvent(GroupMessageEvent):
    """Real GroupMessageEvent stand-in; only fires handle_message when @ bot.

    Built via model_construct so pydantic skips field validation — only the
    fields handle_message touches (group_id / message) are populated.
    """

    def __init__(self, group_id, text, at_bot=True):
        seg = _FakeSeg(
            "at" if at_bot else "text",
            {"qq": int(FakeBot.self_id)} if at_bot else {"text": text},
        )
        inst = GroupMessageEvent.model_construct(group_id=group_id, message=[seg])
        object.__setattr__(self, "__dict__", dict(inst.__dict__))
        object.__setattr__(self, "_text", text)

    def get_user_id(self):
        return "12345"

    def get_plaintext(self):
        return self._text


class FakeBotWithSend(FakeBot):
    """FakeBot + bot.send() recording for handle_message tests."""

    def __init__(self):
        super().__init__()
        self.sent = []

    async def send(self, event, message):
        self.sent.append(message)


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
    """Common setup for handle_message tests: isolated dirs, clean state, env."""
    qq._HISTORY_DIR = tmp_path / "hist"
    qq._INBOX_DIR = tmp_path / "inbox"
    qq._sessions.clear()
    qq._command_routes_loaded = True
    qq._command_routes = []
    monkeypatch.setenv("PAN_QQ_MODE", mode)
    return FakeBotWithSend()


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
    _run(qq.handle_message(bot, FakeEvent("10001", "你好")))
    # 不自动回复
    assert bot.sent == []
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
    _run(qq.handle_message(bot, FakeGroupEvent("20002", "群聊测试")))
    assert bot.sent == []
    inbox = _run(qq.api_inbox("20002"))
    assert [m["text"] for m in inbox["messages"]] == ["群聊测试"]
    hist = _run(qq.api_history("20002"))
    assert [h["role"] for h in hist["messages"]] == ["user"]


def test_selective_group_without_at_ignored(tmp_path, monkeypatch):
    bot = _prepare_handle_test(tmp_path, monkeypatch, "selective")
    _run(qq.handle_message(bot, FakeGroupEvent("20002", "不 @ 不处理", at_bot=False)))
    assert bot.sent == []
    assert _run(qq.api_inbox("20002"))["messages"] == []
    assert _run(qq.api_history("20002"))["messages"] == []


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
    _run(qq.handle_message(bot, FakeEvent("10001", ".test hello")))
    # command route 直连外部 HTTP、绕过 LLM → selective 下仍执行并回复
    assert bot.sent == ["processing, please wait...", "route-ok"]
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
    _run(qq.handle_message(bot, FakeEvent("10001", "你好")))
    # 自动回复（现状兼容）
    assert bot.sent == ["processing, please wait...", "mirror reply"]
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
    item = {"type": "qq", "qqTarget": "user:1470993983", "targetType": "user",
            "targetId": "1470993983", "nickname": "焕之", "text": "你好",
            "time": "2026-08-22 01:00:00"}
    formatted = worker._format_report_batch([item])
    assert "@@@@by qq : user:1470993983 | 焕之" in formatted
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
    r = _run(qq_mcp.qq_bind("user", "1470993983"))
    assert r["ok"] is False
    assert r["error"]["code"] == "missing_identity"


def test_qq_unbind_missing_identity(monkeypatch):
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    from packages.qq import mcp as qq_mcp
    r = _run(qq_mcp.qq_unbind("user", "1470993983"))
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
        return {"sessionId": "ses_abc", "qqTarget": "user:1470993983",
                "subscribed": True, "qqSubscriptions": ["user:1470993983"]}

    monkeypatch.setattr(qq_mcp, "_api", _fake_api)
    result = _run(qq_mcp.qq_bind("user", "1470993983"))
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/qq/subscribe"
    assert captured["body"] == {"sessionId": "ses_abc", "target_type": "user",
                                "target_id": "1470993983"}
    assert captured["base_url"] == qq_mcp._pan_api_url
    assert result["subscribed"] is True


def test_session_qq_subscriptions_roundtrip(monkeypatch, tmp_path):
    from packages.core import session as sess
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    s = sess.create(name="test-qq-subs", model="deepseek-v4-flash")
    s.qq_subscriptions.add("user:1470993983")
    sess.save(s)
    loaded = sess.get(s.id)
    assert loaded.qq_subscriptions == {"user:1470993983"}
    sess.delete(s.id)


def test_enqueue_qq_reminder_delivers_to_subscribers(monkeypatch, tmp_path):
    import types
    from packages.core import session as sess
    from packages.core import worker
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    sub = sess.create(name="sub", model="m")
    other = sess.create(name="other", model="m")
    sub.qq_subscriptions.add("user:1470993983")
    # 隔离 worker 视角的 session store，只含测试 session（避免读真实落盘）
    monkeypatch.setattr(worker, "_sess", types.SimpleNamespace(
        list_all=lambda: [sub, other],
        save_async=lambda s: asyncio.sleep(0),
    ))
    delivered = _run(worker.enqueue_qq_reminder(
        "user", "1470993983", nickname="焕之", text="你好", time_str="t"))
    assert delivered == 1
    assert len(sub.queue_pending) == 1
    item = sub.queue_pending[0]
    assert item["type"] == "qq"
    assert item["qqTarget"] == "user:1470993983"
    assert item["nickname"] == "焕之" and item["text"] == "你好"
    assert other.queue_pending == []
