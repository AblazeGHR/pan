"""packages/wechat/mcp.py 工具层的单元测试（monkeypatch _api，零网络）。

覆盖：
- 工具注册：7 个工具全部挂到 FastMCP（与 manifest pan-wechat 的约定一致）
- 参数校验：空 target_id / 空 text / PAN_AGENT_SESSION_ID 缺失
- 路由与载荷：send/history/inbox 打到插件 API（默认 8081），
  bind/unbind 打到 Pan Core（默认 8768）的 subscribe/unsubscribe

运行：python -m pytest packages/wechat/test_mcp.py -q
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from packages.wechat import mcp as wechat_mcp  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class Recorder:
    """顶替 mcp._api 的桩：记录 (method, path, body, base_url)，返回预设值。"""

    def __init__(self, result: dict | None = None) -> None:
        self.result = result if result is not None else {"ok": True}
        self.calls: list[tuple[str, str, dict | None, str | None]] = []

    async def __call__(self, method, path, body=None, timeout=30.0,
                       base_url=None) -> dict:
        self.calls.append((method, path, body, base_url))
        return dict(self.result)


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(wechat_mcp, "_api", rec)
    return rec


@pytest.fixture(autouse=True)
def _api_urls(monkeypatch):
    """固定两个 base URL，测试不读环境变量（确定性）。"""
    monkeypatch.setattr(wechat_mcp, "_wechat_api_url", "http://wx.test:8081")
    monkeypatch.setattr(wechat_mcp, "_pan_api_url", "http://pan.test:8768")


# ── 工具注册 ──


def test_all_tools_registered():
    tools = _run(wechat_mcp.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "wechat_send_message",
        "wechat_read_conversation",
        "wechat_read_inbox",
        "wechat_list_contacts",
        "wechat_bind",
        "wechat_unbind",
        "wechat_status",
    }


# ── wechat_send_message ──


def test_send_message_validation(recorder):
    for bad in (
        lambda: wechat_mcp.wechat_send_message("", "hi"),
        lambda: wechat_mcp.wechat_send_message("  ", "hi"),
        lambda: wechat_mcp.wechat_send_message("u1", ""),
        lambda: wechat_mcp.wechat_send_message("u1", "  "),
    ):
        result = _run(bad())
        assert result["ok"] is False
    assert recorder.calls == [], "校验失败时不应发起 HTTP"


def test_send_message_happy_path(recorder):
    result = _run(wechat_mcp.wechat_send_message("u1", "你好"))

    assert result == {"ok": True}
    method, path, body, base_url = recorder.calls[0]
    assert (method, path) == ("POST", "/api/wechat/send")
    assert base_url is None, "send 打插件 API（默认 base）"
    assert body == {"target_type": "user", "target_id": "u1", "text": "你好"}


def test_send_message_no_context_token_passthrough(recorder):
    """no_context_token（已缓冲 outbox）语义原样透给编排者。"""
    recorder.result = {"ok": False, "error": {
        "code": "no_context_token", "buffered": True}}
    result = _run(wechat_mcp.wechat_send_message("u1", "x"))
    assert result["error"]["code"] == "no_context_token"
    assert result["error"]["buffered"] is True


# ── 读类工具 ──


def test_read_conversation_params(recorder):
    _run(wechat_mcp.wechat_read_conversation("u1", limit=50))
    method, path, body, _ = recorder.calls[0]
    assert (method, path) == ("GET", "/api/wechat/history")
    assert body == {"target_id": "u1", "limit": 50}


def test_read_inbox_consume_flag(recorder):
    _run(wechat_mcp.wechat_read_inbox("u1", limit=5, consume=True))
    method, path, body, _ = recorder.calls[0]
    assert (method, path) == ("GET", "/api/wechat/inbox")
    assert body == {"target_id": "u1", "limit": 5, "consume": 1}


def test_list_contacts(recorder):
    recorder.result = {"ok": True, "contacts": [], "source": "local"}
    result = _run(wechat_mcp.wechat_list_contacts())
    assert result["source"] == "local"
    assert recorder.calls[0][:2] == ("GET", "/api/wechat/recent_contacts")


def test_status(recorder):
    recorder.result = {"ok": True, "logged_in": True, "connected": True,
                       "mode": "selective", "outbox": []}
    result = _run(wechat_mcp.wechat_status())
    assert result["logged_in"] is True
    assert recorder.calls[0][:2] == ("GET", "/api/wechat/status")


# ── bind / unbind（打 Pan Core）──


def test_bind_requires_pan_session_identity(recorder, monkeypatch):
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    result = _run(wechat_mcp.wechat_bind("u1"))
    assert result["error"]["code"] == "missing_identity"
    assert recorder.calls == []


def test_bind_targets_pan_core_subscribe(recorder, monkeypatch):
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_me")
    result = _run(wechat_mcp.wechat_bind("u1"))

    assert result == {"ok": True}
    method, path, body, base_url = recorder.calls[0]
    assert (method, path) == ("POST", "/api/wechat/subscribe")
    assert base_url == "http://pan.test:8768", "bind 打 Pan Core，不是插件"
    assert body == {"sessionId": "ses_me", "target_type": "user",
                    "target_id": "u1"}


def test_unbind_targets_pan_core(recorder, monkeypatch):
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_me")
    _run(wechat_mcp.wechat_unbind("u1"))
    method, path, body, base_url = recorder.calls[0]
    assert (method, path) == ("POST", "/api/wechat/unsubscribe")
    assert base_url == "http://pan.test:8768"
    assert body["sessionId"] == "ses_me"


def test_bind_empty_target_id(recorder, monkeypatch):
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_me")
    result = _run(wechat_mcp.wechat_bind(""))
    assert result["error"]["code"] == "invalid_target_id"
    assert recorder.calls == []
