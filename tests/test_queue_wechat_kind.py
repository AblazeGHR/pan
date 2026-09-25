"""队列分类 / 序列化对 wechat 项的支持。

覆盖：
- _queue_item_kind 对 type:"wechat" 返回 "wechat"
- _format_report_batch 渲染 @@@@by wechat 抬头；canReply:false 时出现提示行
- _serialize_queue_item 对 wechat 项返回 kind/source="wechat" 且 meta 含 channelTarget

均为纯函数 / 纯数据测试，不 spawn、不 bind 端口。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker
from packages.web import server as _srv


def _wechat_item(**kw):
    base = {
        "type": "wechat", "kind": "wechat", "id": "q_w1", "queueItemId": "q_w1",
        "qqTarget": "user:wx1", "channelTarget": "user:wx1", "channel": "wechat",
        "canReply": True, "targetType": "user", "targetId": "wx1",
        "nickname": "小明", "text": "在吗", "deliveryState": "queued",
        "dispatchState": "queued", "revision": 1, "createdAt": 0,
    }
    base.update(kw)
    return base


def test_queue_item_kind_wechat():
    """type:wechat → kind "wechat"（与 qq 平行，各自成一种 kind）。"""
    assert worker._queue_item_kind(_wechat_item()) == "wechat"


def test_format_report_batch_wechat_header():
    """wechat 提醒渲染出 @@@@by wechat 抬头，包含 target 与 nickname。"""
    text = worker._format_report_batch([_wechat_item()])
    assert "@@@@by wechat" in text
    assert "user:wx1" in text
    assert "小明" in text
    # canReply 默认 True → 不应出现提示行
    assert "canReply: false" not in text


def test_format_report_batch_wechat_canreply_false():
    """canReply=False 时抬头下追加一行 canReply: false，告知 agent 此刻无法回复。"""
    text = worker._format_report_batch([_wechat_item(canReply=False)])
    assert "canReply: false" in text


def test_serialize_wechat_item():
    """wechat 队列项序列化：kind/source=wechat，meta 同时给 channelTarget 与 qqTarget。"""
    out = _srv._serialize_queue_item(_wechat_item())
    assert out["kind"] == "wechat"
    assert out["source"] == "wechat"
    assert out["meta"]["channelTarget"] == "user:wx1"
    assert out["meta"]["qqTarget"] == "user:wx1"   # 老兼容键保留
    assert out["meta"]["canReply"] is True
