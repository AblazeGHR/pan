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
