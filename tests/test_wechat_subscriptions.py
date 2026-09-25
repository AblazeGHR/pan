"""微信通道订阅字段（session.wechat_subscriptions）与订阅/解绑端点测试。

覆盖：
- 字段默认空 set、list→set 迁移、to_dict 含键
- 替身交接：B 继承 A 的 wechat_subscriptions，A 被清空
- /api/wechat/subscribe|unsubscribe 的键格式与返回值（走通道无关的
  _channel_subscribe/_channel_unsubscribe，响应键 wechatTarget/wechatSubscriptions）
- 老 session JSON 缺 wechat_subscriptions 时反序列化不报错（向后兼容）

不 spawn 子进程、不 bind 端口：_channel_subscribe 直接调用的内部函数，
与 /api/wechat/* 路由同一实现体，响应字段一字不改地复用。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.web import server as _srv


def _cleanup():
    _sess._cache.clear()
    _sess._all_loaded = False


def _make(sid, name, **kw):
    s = _sess.Session(id=sid, name=name, adapter=kw.pop("adapter", "cbc"), **kw)
    _sess._cache[sid] = s
    return s


def test_wechat_subscriptions_default_empty():
    """新 session 默认空 set（default_factory 兜底）。"""
    s = _sess.Session(id="ses_w1", name="w1")
    assert s.wechat_subscriptions == set()
    assert isinstance(s.wechat_subscriptions, set)


def test_wechat_subscriptions_list_migrated_to_set():
    """落盘 JSON 里 set 序列化为 list → 读回应还原为 set。"""
    s = _sess.Session(id="ses_w2", name="w2",
                      wechat_subscriptions=["user:1", "group:2"])
    assert s.wechat_subscriptions == {"user:1", "group:2"}
    assert isinstance(s.wechat_subscriptions, set)


def test_to_dict_contains_wechat_subscriptions():
    """to_dict 含 wechat_subscriptions 键（sorted list 形态）。"""
    s = _sess.Session(id="ses_w3", name="w3",
                      wechat_subscriptions={"user:9"})
    d = s.to_dict()
    assert "wechat_subscriptions" in d
    assert d["wechat_subscriptions"] == ["user:9"]


def test_handoff_transfers_wechat_subscriptions():
    """替身交接：B 接替 A 的微信 postbox 绑定，A 被清空。"""
    _cleanup()
    a = _make("ses_a", "dev", wechat_subscriptions={"user:wx1", "group:wg"})
    a2, b = _sess.handoff_session("ses_a", "交接", copy_settings=True)
    # B 接替 A 的微信 postbox 绑定（与 qq_subscriptions 平行）
    assert b.wechat_subscriptions == {"user:wx1", "group:wg"}
    # A 解除原绑定
    assert a2.wechat_subscriptions == set()
    _cleanup()


def test_wechat_subscribe_unsubscribe_keys_and_format():
    """/api/wechat/subscribe|unsubscribe 的键格式与返回值。"""
    _cleanup()
    _make("ses_sub", "sub")
    resp = _srv._channel_subscribe("wechat", {
        "sessionId": "ses_sub", "target_type": "user", "target_id": "wxid_abc",
    })
    assert resp["subscribed"] is True
    # 微信端点用 wechatTarget / wechatSubscriptions（与 QQ 平行命名）
    assert resp["wechatTarget"] == "user:wxid_abc"
    assert resp["wechatSubscriptions"] == ["user:wxid_abc"]
    # 微信响应里不应出现 qq 平行键（字段分离，互不串台）
    assert "qqTarget" not in resp
    assert "qqSubscriptions" not in resp

    resp2 = _srv._channel_unsubscribe("wechat", {
        "sessionId": "ses_sub", "target_type": "user", "target_id": "wxid_abc",
    })
    assert resp2["subscribed"] is False
    assert resp2["wechatTarget"] == "user:wxid_abc"
    assert resp2["wechatSubscriptions"] == []
    _cleanup()


def test_old_session_json_missing_wechat_subscriptions_loads():
    """老 session JSON 无 wechat_subscriptions 键 → default_factory 兜底空 set。"""
    data = {
        "id": "ses_old", "name": "old", "adapter": "cbc",
        "qq_subscriptions": ["user:1"],  # 有老字段，故意缺 wechat_subscriptions
    }
    s = _sess.Session._from_data(data)
    assert s.wechat_subscriptions == set()       # 向后兼容：缺键不报错
    assert s.qq_subscriptions == {"user:1"}
