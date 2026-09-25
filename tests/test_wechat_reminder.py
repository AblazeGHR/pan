"""enqueue_channel_reminder（通道无关提醒入队）测试。

覆盖：
- channel="wechat" 能投递给订阅者、返回正确数量
- 只读 wechat_subscriptions，不误投给只订了 QQ 的 session（反之亦然）
- 未知 channel（如 "feishu"）安全返回 0 不抛
- 队列项含 channelTarget / canReply，且保留 qqTarget 兼容键
- 每个订阅者拿到独立副本（改一个的 deliveryState 不影响另一个）
- enqueue_qq_reminder 的旧签名仍可用、行为不变

不 spawn 真实子进程、不 bind 端口：save_async / _wake_worker 都被打桩；
_wake_worker 默认会触发 create_worker（真 spawn），此处静默化。
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker


def _cleanup():
    _sess._cache.clear()
    _sess._all_loaded = False
    worker.workers.clear()
    worker.set_broadcaster(None)


def _make(sid, name, **kw):
    s = _sess.Session(id=sid, name=name, adapter=kw.pop("adapter", "cbc"), **kw)
    _sess._cache[sid] = s
    return s


def _isolate(monkeypatch):
    # save_async 落盘无关；_wake_worker 默认会 create_worker（真 spawn）→ 静默
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    monkeypatch.setattr(worker, "_wake_worker", AsyncMock())
    _cleanup()


def test_wechat_reminder_delivers_to_subscriber(monkeypatch):
    """channel=wechat 投递给订阅 user:wx1 的 session，返回 1。"""
    _isolate(monkeypatch)
    sub = _make("ses_wx", "wx", wechat_subscriptions={"user:wx1"})

    async def run():
        return await worker.enqueue_channel_reminder("wechat", "user", "wx1", text="hi")
    assert asyncio.run(run()) == 1
    assert len(sub.queue_pending) == 1
    item = sub.queue_pending[0]
    # 双写：channelTarget（新统一字段）+ qqTarget（老兼容键，勿删）
    assert item["channelTarget"] == "user:wx1"
    assert item["qqTarget"] == "user:wx1"
    assert item["canReply"] is True
    assert item["channel"] == "wechat"
    assert item["kind"] == "wechat"
    _cleanup()


def test_wechat_reminder_does_not_cross_channel(monkeypatch):
    """微信提醒不误投给只订了 QQ 的 session（反之亦然）。"""
    _isolate(monkeypatch)
    wx_sub = _make("ses_wx", "wx", wechat_subscriptions={"user:wx1"})
    # 同一 target 但只在 QQ 侧订阅
    qq_sub = _make("ses_qq", "qq", qq_subscriptions={"user:wx1"})

    async def run():
        return await worker.enqueue_channel_reminder("wechat", "user", "wx1", text="hi")
    assert asyncio.run(run()) == 1
    assert len(wx_sub.queue_pending) == 1
    assert len(qq_sub.queue_pending) == 0  # 只订 QQ 的不应收到微信提醒
    _cleanup()


def test_unknown_channel_returns_zero(monkeypatch):
    """未知 channel（feishu 暂无插件）→ getattr 取不到属性 → 空集合 → 0、不抛。"""
    _isolate(monkeypatch)
    _make("ses_any", "any", wechat_subscriptions={"user:wx1"})  # 故意不订阅 feishu

    async def run():
        return await worker.enqueue_channel_reminder("feishu", "user", "f1")
    assert asyncio.run(run()) == 0
    _cleanup()


def test_subscribers_get_independent_copies(monkeypatch):
    """每个订阅者拿到独立 dict 副本：改一个的 deliveryState 不影响另一个。"""
    _isolate(monkeypatch)
    s1 = _make("s1", "a", wechat_subscriptions={"user:wx1"})
    s2 = _make("s2", "b", wechat_subscriptions={"user:wx1"})

    asyncio.run(worker.enqueue_channel_reminder("wechat", "user", "wx1"))
    i1 = s1.queue_pending[0]
    i2 = s2.queue_pending[0]
    assert i1 is not i2, "订阅者应拿到独立副本"
    i1["deliveryState"] = "sent"
    assert i2["deliveryState"] != "sent"
    _cleanup()


def test_enqueue_qq_reminder_wrapper(monkeypatch):
    """enqueue_qq_reminder 旧签名仍可用、行为不变（转调 channel=qq）。"""
    _isolate(monkeypatch)
    sub = _make("ses_q", "q", qq_subscriptions={"user:qq1"})

    async def run():
        return await worker.enqueue_qq_reminder("user", "qq1", text="old")
    assert asyncio.run(run()) == 1
    item = sub.queue_pending[0]
    assert item["channel"] == "qq"
    assert item["qqTarget"] == "user:qq1"
    assert item["channelTarget"] == "user:qq1"
    _cleanup()
