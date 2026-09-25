"""WeChatPlugin 业务编排 + server.py HTTP API 的单元测试（完全离线）。

覆盖：
- selective 模式：history + inbox 落盘、Pan Core notify（can_reply 标记）、不自动回复
- mirror 模式：建 session → spawn → task → 轮询取回复 → 回发 + assistant 落盘
- command-route：两种模式命中即直连外部目标，mirror 路径不再走
- outbox 补发：token 刷新后按序补发、成功才出队；token 不新鲜时不补
- api_send / api_history / api_inbox / api_inbox_clear
- server.create_app 全部端点（httpx ASGITransport）+ lifespan 钩子

运行：python -m pytest packages/wechat/test_plugin.py -q
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
import pytest  # noqa: E402

from packages.wechat import plugin as plugin_mod  # noqa: E402
from packages.wechat import store  # noqa: E402
from packages.wechat.auth import BotSession  # noqa: E402
from packages.wechat.channel import WeChatChannel, WeChatMessage  # noqa: E402
from packages.wechat.ilink import ILinkClient  # noqa: E402
from packages.wechat.ilink_channel import ILinkChannel  # noqa: E402
from packages.wechat.ilink_spec import ILinkSpec  # noqa: E402
from packages.wechat.plugin import WeChatPlugin  # noqa: E402
from packages.wechat.server import create_app  # noqa: E402
from packages.wechat.store import ContextTokenStore  # noqa: E402
from packages.wechat.test_ilink import FakeServer  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path):
    saved = store.data_root()
    store.set_data_root(tmp_path)
    try:
        yield tmp_path
    finally:
        store.set_data_root(saved)


def _msg(text: str, scope_id: str = "u1", nickname: str = "小明",
         context_token: str = "") -> WeChatMessage:
    return WeChatMessage(scope="user", scope_id=scope_id, text=text,
                         sender_nickname=nickname, context_token=context_token)


class FakeChannel(WeChatChannel):
    """记录 send 调用的通道替身；token 语义与 ILinkChannel 相同。"""

    name = "fake"

    def __init__(self) -> None:
        super().__init__()
        self.tokens = ContextTokenStore()
        self.sent: list[tuple[str, str, str]] = []
        self.fail_next = False
        self.fail_texts: set[str] = set()

    async def startup(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def send(self, target_type, target_id, text) -> dict:
        if self.fail_next or text in self.fail_texts:
            return {"ok": False, "error": {"code": "server_error", "message": "x"}}
        self.sent.append((target_type, str(target_id), text))
        return {"ok": True}

    async def recent_contacts(self) -> dict:
        return {"ok": True, "contacts": [], "source": "local"}

    async def is_connected(self) -> bool:
        return True

    def get_token(self, user_id) -> str:
        return self.tokens.get(user_id)

    def has_fresh_token(self, user_id) -> bool:
        return bool(self.tokens.get(user_id)) and not self.tokens.is_stale(user_id)


class FakeCore:
    """按 (method, path 前缀) 路由的 Pan Core 替身，记录全部请求。"""

    def __init__(self, routes: dict[tuple[str, str], object]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        self.requests.append((request.method, request.url.path,
                              json.loads(request.content.decode("utf-8") or "{}")))
        if key not in self.routes:
            return httpx.Response(404, json={"error": f"no route {key}"})
        item = self.routes[key]
        if isinstance(item, Exception):
            raise item
        return httpx.Response(200, json=item)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def bodies(self, path: str) -> list[dict]:
        return [b for m, p, b in self.requests if p == path]


def _plugin(channel: FakeChannel, core: FakeCore, *, mode: str = "selective",
            **kw) -> WeChatPlugin:
    return WeChatPlugin(
        channel, core_url="http://core.test", mode=mode,
        transport=core.transport(), **kw
    )


# ── 模式解析 ──


def test_wechat_mode_resolution(monkeypatch):
    assert plugin_mod.wechat_mode({}) == "mirror"
    assert plugin_mod.wechat_mode({"mode": "selective"}) == "selective"
    assert plugin_mod.wechat_mode({"mode": "bogus"}) == "mirror", "非法值回退 mirror"
    monkeypatch.setenv("PAN_WECHAT_MODE", "selective")
    assert plugin_mod.wechat_mode({}) == "selective", "环境变量优先于 config"
    monkeypatch.setenv("PAN_WECHAT_MODE", "junk")
    assert plugin_mod.wechat_mode({"mode": "mirror"}) == "mirror"


# ── selective 模式 ──


def test_selective_mode_inbox_notify_and_no_autoreply():
    channel = FakeChannel()
    channel.tokens.set("u1", "ct-1")  # 通道收上行时会刷新 token → can_reply True
    core = FakeCore({("POST", "/api/wechat/notify"): {"ok": True, "delivered": 0}})
    plugin = _plugin(channel, core, mode="selective")

    _run(plugin.handle_wechat_message(_msg("你好", context_token="ct-1")))

    inbox = store.load_inbox("u1")
    assert len(inbox) == 1 and inbox[0]["text"] == "你好"
    assert inbox[0]["nickname"] == "小明"
    notifies = core.bodies("/api/wechat/notify")
    assert len(notifies) == 1
    assert notifies[0]["target_id"] == "u1"
    assert notifies[0]["text"] == "你好"
    assert notifies[0]["can_reply"] is True
    assert channel.sent == [], "selective 模式绝不自动回复"


def test_notify_can_reply_false_when_token_missing():
    channel = FakeChannel()  # token store 为空 → has_fresh_token False
    core = FakeCore({("POST", "/api/wechat/notify"): {}})
    plugin = _plugin(channel, core, mode="selective")
    _run(plugin.handle_wechat_message(_msg("在吗")))
    assert core.bodies("/api/wechat/notify")[0]["can_reply"] is False


def test_notify_failure_is_nonfatal():
    channel = FakeChannel()
    core = FakeCore({("POST", "/api/wechat/notify"): httpx.ConnectError("down")})
    plugin = _plugin(channel, core, mode="selective")
    _run(plugin.handle_wechat_message(_msg("hi")))
    assert len(store.load_inbox("u1")) == 1, "Core 不可达不能阻断 inbox 落盘"


def test_empty_text_ignored():
    plugin = _plugin(FakeChannel(), FakeCore({}))
    _run(plugin.handle_wechat_message(_msg("   ")))
    assert store.load_history("u1") == []


# ── outbox 补发 ──


def test_outbox_flushed_on_incoming_message():
    channel = FakeChannel()
    channel.tokens.set("u1", "ct-fresh")  # 上行刷新了 token
    store.append_outbox("u1", "积压1")
    store.append_outbox("u1", "积压2")
    core = FakeCore({("POST", "/api/wechat/notify"): {}})
    plugin = _plugin(channel, core, mode="selective")

    _run(plugin.handle_wechat_message(_msg("在吗")))

    sent = [t for _, _, t in channel.sent]
    assert sent == ["积压1", "积压2"], "补发发生在处理新消息之前且保序"
    assert store.load_outbox("u1") == [], "补发成功后 outbox 清空"
    roles = [(e["role"], e["text"]) for e in store.load_history("u1")]
    assert ("assistant", "积压1") in roles and ("assistant", "积压2") in roles


def test_outbox_flush_partial_keeps_order():
    """第二条发送失败 → 成功的第一条出队，失败的留在队首。"""
    channel = FakeChannel()
    channel.tokens.set("u1", "ct-fresh")
    channel.fail_texts = {"积压2"}
    store.append_outbox("u1", "积压1")
    store.append_outbox("u1", "积压2")
    plugin = _plugin(channel, FakeCore({}), mode="selective")

    sent = _run(plugin.flush_outbox("u1"))

    assert sent == 1
    assert [m["text"] for m in store.load_outbox("u1")] == ["积压2"]


def test_outbox_not_flushed_when_token_stale():
    channel = FakeChannel()
    store._atomic_write_json(
        store.context_tokens_path(), {"u1": {"token": "ct-old", "ts": 1.0}}
    )
    store.append_outbox("u1", "积压")
    plugin = _plugin(channel, FakeCore({}), mode="selective")

    assert _run(plugin.flush_outbox("u1")) == 0
    assert channel.sent == []
    assert [m["text"] for m in store.load_outbox("u1")] == ["积压"]


# ── mirror 模式 ──


def test_mirror_mode_full_flow():
    channel = FakeChannel()
    core = FakeCore({
        ("GET", "/api/sessions"): {"sessions": []},
        ("POST", "/api/sessions"): {"id": "ses_1"},
        ("POST", "/api/spawn"): {"workerId": "w1"},
        ("POST", "/api/task"): {"ok": True},
        ("GET", "/api/sessions/ses_1"): {
            "id": "ses_1", "workerId": "w1", "workerStatus": "idle",
            "lastResult": {"taskSeq": 1, "timestamp": "t1",
                           "result": "你好呀\n🔧 tool-call"},
            "history": [],
        },
    })
    plugin = _plugin(channel, core, mode="mirror", poll_interval=0,
                     max_poll_time=5)

    _run(plugin.handle_wechat_message(_msg("你好")))

    texts = [t for _, _, t in channel.sent]
    assert texts == ["你好呀"], "只发最终回复：无占位提示，且装饰行已剥掉"
    # 不拼接历史：持久 session 自带上下文，任务里就是原始消息本身
    task_text = core.bodies("/api/task")[0]["text"]
    assert task_text == "你好", "任务里应是原始消息（历史由 session 上下文承载）"
    roles = [(e["role"], e["text"]) for e in store.load_history("u1")]
    assert ("user", "你好") in roles and ("assistant", "你好呀") in roles


def test_mirror_mode_adopts_existing_session():
    channel = FakeChannel()
    old_name = plugin_mod.WeChatPlugin._session_name("123456")  # wx-<完整id哈希>，避免尾6位串号
    core = FakeCore({
        ("GET", "/api/sessions"): {"sessions": [
            {"id": "ses_old", "name": old_name, "workerId": None,
             "lastResult": {"taskSeq": 7, "timestamp": "t0"}},
        ]},
        ("POST", "/api/spawn"): {"workerId": "w2"},
        ("POST", "/api/task"): {"ok": True},
        ("GET", "/api/sessions/ses_old"): {
            "id": "ses_old", "workerId": "w2",
            "lastResult": {"taskSeq": 8, "timestamp": "t1", "result": "新回复"},
        },
    })
    plugin = _plugin(channel, core, mode="mirror", poll_interval=0,
                     max_poll_time=5)

    _run(plugin.handle_wechat_message(_msg("再来", scope_id="123456")))

    # 命中旧 session（按名字 wx-<hash> 精确认领），不再新建：/api/sessions 上
    # 只有认领扫描那一次 GET，没有任何 POST
    session_path_calls = [(m, p) for m, p, _ in core.requests
                          if p == "/api/sessions"]
    assert session_path_calls == [("GET", "/api/sessions")]
    assert [b for b in core.bodies("/api/spawn")] != [], "worker 缺失要 re-spawn"
    texts = [t for _, _, t in channel.sent]
    assert "新回复" in texts
    # taskSeq 播种：认领时 7 → 新结果必须是 8 才算新回复（防旧结果重放）
    assert plugin._sessions["user:123456"]["seq"] == 8


# ── command-routes ──


def _core_with_command_route(target_path="/api/ping", result="pong"):
    return FakeCore({
        ("GET", "/api/manifest/command-routes"): {"routes": [
            {"prefixes": [".ping"], "target": f"http://svc.test{target_path}"},
        ]},
        ("POST", target_path): {"result": result},
        # mirror 兜底路径（不应被走到）
        ("GET", "/api/sessions"): {"sessions": []},
        ("POST", "/api/sessions"): {"id": "ses_x"},
        ("POST", "/api/spawn"): {"workerId": "w"},
        ("POST", "/api/task"): {"ok": True},
        ("GET", "/api/sessions/ses_x"): {"id": "ses_x", "workerId": "w",
                                         "lastResult": {}},
    })


def test_command_route_hit_in_mirror_mode():
    channel = FakeChannel()
    core = _core_with_command_route()
    plugin = _plugin(channel, core, mode="mirror", poll_interval=0)

    _run(plugin.handle_wechat_message(_msg(".ping now")))

    assert core.bodies("/api/ping")[0] == {"text": "now"}, "前缀要剥掉"
    texts = [t for _, _, t in channel.sent]
    assert "pong" in texts
    assert plugin._sessions == {}, "命中 route 后不再走 mirror 编排"


def test_command_route_hit_in_selective_mode():
    channel = FakeChannel()
    core = _core_with_command_route()
    plugin = _plugin(channel, core, mode="selective")

    _run(plugin.handle_wechat_message(_msg(".ping")))

    assert "pong" in [t for _, _, t in channel.sent]
    assert len(store.load_inbox("u1")) == 1, "selective 模式下消息仍入 inbox"


def test_command_route_no_hit_falls_through():
    channel = FakeChannel()
    core = _core_with_command_route()
    plugin = _plugin(channel, core, mode="selective")
    _run(plugin.handle_wechat_message(_msg("普通消息")))
    assert core.bodies("/api/ping") == []


# ── api_* 处理器 ──


def test_api_send_appends_history():
    channel = FakeChannel()
    channel.tokens.set("u1", "ct-1")
    plugin = _plugin(channel, FakeCore({}))

    result = _run(plugin.api_send("user", "u1", "主动消息"))

    assert result == {"ok": True}
    assert ("user", "u1", "主动消息") in channel.sent
    assert ("assistant", "主动消息") in [
        (e["role"], e["text"]) for e in store.load_history("u1")]


def test_api_history_roundtrip_and_limit():
    plugin = _plugin(FakeChannel(), FakeCore({}))
    for i in range(5):
        store.append_history("u1", "user", f"m{i}")
    data = _run(plugin.api_history("u1", 3))
    assert [m["text"] for m in data["messages"]] == ["m2", "m3", "m4"]


def test_api_inbox_consume_and_clear():
    plugin = _plugin(FakeChannel(), FakeCore({}))
    store.append_inbox("u1", "a")
    store.append_inbox("u1", "b")

    peek = _run(plugin.api_inbox("u1", 10))
    assert [m["text"] for m in peek["messages"]] == ["a", "b"]
    assert len(store.load_inbox("u1")) == 2, "不 consume 不删除"

    consumed = _run(plugin.api_inbox("u1", 1, consume=True))
    assert [m["text"] for m in consumed["messages"]] == ["a"]
    assert [m["text"] for m in store.load_inbox("u1")] == ["b"]

    _run(plugin.api_inbox_clear("u1"))
    assert store.load_inbox("u1") == []


def test_api_status_shape():
    channel = FakeChannel()
    channel.tokens.set("u1", "t")
    store.append_outbox("u2", "积压")
    plugin = _plugin(channel, FakeCore({}))
    status = _run(plugin.api_status())
    assert status["ok"] is True
    assert status["channel"] == "fake"
    assert status["mode"] == "selective"
    assert status["outbox"] == [{"target_id": "u2", "pending": 1}]


# ── server.py HTTP API ──


def _client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://plugin.test")


def test_server_endpoints_roundtrip():
    channel = FakeChannel()
    channel.tokens.set("u1", "ct-1")
    plugin = _plugin(channel, FakeCore({}))
    app = create_app(plugin)

    async def scenario():
        async with _client_for(app) as client:
            r = await client.post("/api/wechat/send", json={
                "target_type": "user", "target_id": "u1", "text": "hello"})
            assert r.json() == {"ok": True}

            r = await client.get("/api/wechat/history",
                                 params={"target_id": "u1"})
            assert r.json()["messages"][-1]["text"] == "hello"

            store.append_inbox("u1", "排队")
            r = await client.get("/api/wechat/inbox",
                                 params={"target_id": "u1"})
            assert r.json()["messages"][0]["text"] == "排队"
            r = await client.delete("/api/wechat/inbox",
                                    params={"target_id": "u1"})
            assert r.json()["cleared"] is True

            r = await client.get("/api/wechat/recent_contacts")
            assert r.json() == {"ok": True, "contacts": [], "source": "local"}

            r = await client.get("/api/wechat/channels")
            assert r.json() == {"ok": True, "channels": [
                {"name": "fake", "bot_uin": "", "connected": True}]}

            r = await client.get("/api/wechat/status")
            body = r.json()
            assert body["ok"] is True and body["mode"] == "selective"

            r = await client.post("/api/wechat/typing",
                                  json={"target_id": "u1"})
            assert r.json()["error"]["code"] == "unsupported", "FakeChannel 未覆写 typing"

    _run(scenario())


def test_server_lifespan_hooks():
    plugin = _plugin(FakeChannel(), FakeCore({}))
    events: list[str] = []

    async def on_startup():
        events.append("up")

    async def on_shutdown():
        events.append("down")

    app = create_app(plugin, on_startup=on_startup, on_shutdown=on_shutdown)

    async def scenario():
        async with app.router.lifespan_context(app):
            assert events == ["up"]
        assert events == ["up", "down"]

    _run(scenario())


def test_server_send_error_passthrough():
    """no_context_token 等业务错误照实透出（HTTP 200 + ok:false 语义）。

    这里用真 ILinkChannel（缺 token 的行为在通道里），而非 FakeChannel。
    """
    client = ILinkClient(ILinkSpec(), FakeServer({}).transport())
    client.set_credential("tok-1")
    channel = ILinkChannel(client, BotSession(client))
    plugin = WeChatPlugin(channel, core_url="http://core.test",
                          mode="selective",
                          transport=FakeCore({}).transport())
    app = create_app(plugin)

    async def scenario():
        async with _client_for(app) as client_http:
            r = await client_http.post("/api/wechat/send", json={
                "target_type": "user", "target_id": "u9", "text": "x"})
            body = r.json()
            assert body["ok"] is False
            assert body["error"]["code"] == "no_context_token"
            assert body["error"]["buffered"] is True
            assert [m["text"] for m in store.load_outbox("u9")] == ["x"]

    _run(scenario())
