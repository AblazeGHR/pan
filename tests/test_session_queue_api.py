"""Session agent queue 三端点测试（GET /queue、DELETE /queue/{item_id}、PATCH /queue/order）。

覆盖：
- GET 序列化：task / 普通 report（无 type 字段）/ zombie report（type=zombie）/
  qq / 畸形项（非 dict、无 result 的怪 dict）→ 归一化 AgentQueueItem，保持原顺序
- 原生 id：task/report/qq 均有独立 queueItemId；相同内容也不能合并
- report text 截断：result > 200 字 → 截断 + "…"
- DELETE：task 按 id 命中、report/qq 按 hash id 命中、未知 id → not_found、
  session 不存在 → error
- PATCH order：所有来源使用同一 orderedIds；缺 order 参数 / session 不存在 → error

全部走 _cache 临时 session + monkeypatch save/save_async（no-op），不落盘、
不触达真实服务。
"""

import asyncio
import sys
from pathlib import Path

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess  # noqa: E402
import packages.web.server as srv  # noqa: E402


def _cleanup():
    _sess._cache.clear()


def _setup_session(sid="ses_q", **kwargs):
    s = _sess.Session(id=sid, name="test", **kwargs)
    _sess._cache[sid] = s
    return s


async def _noop_save_async(s):
    pass


def _noop_save(s):
    pass


# queue_pending fixture：覆盖全部异构形状
TASK_A = {"type": "task", "id": "task-aaa", "text": "task A", "source": "agent", "seq": 1, "taskId": "t-a", "deliveryState": "queued"}
TASK_B = {"type": "task", "id": "task-bbb", "text": "task B", "source": "user", "seq": 2, "taskId": "t-b", "deliveryState": "queued"}
REPORT_PLAIN = {"status": "done", "result": "report r1", "sessionId": "ses_child", "taskId": "t-a", "workerId": "worker-1", "deliveryState": "queued"}
REPORT_ZOMBIE = {"type": "zombie", "status": "error", "result": "worker died", "sessionId": "ses_child", "taskId": "t-b", "workerId": "worker-1", "deliveryState": "queued"}
QQ_ITEM = {"type": "qq", "qqTarget": "user:12345", "targetType": "user", "targetId": "12345", "nickname": "bob", "text": "hi from qq", "time": "12:00", "deliveryState": "queued"}

MIXED = [TASK_A, REPORT_PLAIN, TASK_B, REPORT_ZOMBIE, QQ_ITEM]


def _ids(items):
    return [it["id"] for it in items]


# ── GET /queue ──

def test_get_queue_mixed(monkeypatch):
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = list(MIXED)

    r = asyncio.run(srv.api_session_queue("ses_q"))
    items = r["items"]
    assert [it["kind"] for it in items] == ["task", "report", "task", "report", "qq"]

    # task：透传自身 id / text / source / meta
    assert items[0]["id"] == "task-aaa"
    assert items[0]["queueItemId"] == "task-aaa"
    assert items[0]["createdAt"] != 0
    assert items[0]["meta"] == {"seq": 1, "taskId": "t-a", "revision": 1,
                                 "dispatchState": "queued"}
    # report（无 type 字段）：result 即 text
    assert items[1]["kind"] == "report"
    assert items[1]["text"] == "report r1"
    assert items[1]["meta"] == {"status": "done", "taskId": "t-a", "workerId": "worker-1",
                                  "revision": 1, "dispatchState": "queued"}
    # zombie report（type=zombie）也归为 report
    assert items[3]["kind"] == "report"
    assert items[3]["text"] == "worker died"
    assert items[2]["source"] == "user", "user task source must survive normalization"
    # qq
    assert items[4]["kind"] == "qq"
    assert items[4]["text"] == "hi from qq"
    assert items[4]["meta"] == {"qqTarget": "user:12345", "time": "12:00",
                                  "revision": 1, "dispatchState": "queued"}
    _cleanup()


def test_get_queue_assigns_distinct_native_ids(monkeypatch):
    """report/qq 相同内容也获得独立原生 id，并在后续读取中保持稳定。"""
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    import copy
    qq = {"type": "qq", "qqTarget": "user:12345", "targetType": "user",
          "targetId": "12345", "nickname": "bob", "text": "hi from qq",
          "time": "12:00", "deliveryState": "queued"}
    report_a = {"status": "done", "result": "report r1", "sessionId": "ses_child",
                "taskId": "t-a", "workerId": "worker-1", "deliveryState": "queued"}
    report_b = copy.deepcopy(report_a)
    s.queue_pending = [report_a, report_b, qq]

    items = asyncio.run(srv.api_session_queue("ses_q"))["items"]
    assert items[0]["id"] != items[1]["id"], "same content remains separate queue items"
    assert items[0]["id"].startswith("q_")
    assert items[1]["id"].startswith("q_")
    assert items[2]["id"].startswith("q_")
    # id 稳定：重新序列化结果一致
    again = asyncio.run(srv.api_session_queue("ses_q"))["items"]
    assert _ids(again) == _ids(items)
    _cleanup()


def test_get_queue_skips_malformed(monkeypatch):
    """非 dict 项 / 无 result 且有未知 type 的怪 dict → 跳过。"""
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = ["garbage", None, {"type": "mystery", "foo": 1}, REPORT_PLAIN]

    items = asyncio.run(srv.api_session_queue("ses_q"))["items"]
    assert [it["kind"] for it in items] == ["report"]
    _cleanup()


def test_get_queue_report_truncated(monkeypatch):
    """report result 超 200 字 → 截断加省略号。"""
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = [{"status": "done", "result": "x" * 300, "sessionId": "c", "taskId": "t", "workerId": "w"}]

    items = asyncio.run(srv.api_session_queue("ses_q"))["items"]
    text = items[0]["text"]
    assert len(text) == 201
    assert text.startswith("x" * 200)
    assert text.endswith("…")
    _cleanup()


def test_get_queue_session_not_found():
    r = asyncio.run(srv.api_session_queue("ses_nope"))
    assert "not found" in r.get("error", "")


def test_get_queue_empty(monkeypatch):
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    assert asyncio.run(srv.api_session_queue("ses_q")) == {"items": [], "queueRevision": 0}
    _cleanup()


def test_get_queue_normalizes_legacy_no_type_text_as_user_task(monkeypatch):
    """Legacy text envelopes must not be rendered as Agent reports."""
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = [{"text": "already sent by dashboard", "source": "user"}]

    items = asyncio.run(srv.api_session_queue("ses_q"))["items"]

    assert items[0]["kind"] == "task"
    assert items[0]["source"] == "user"
    assert items[0]["text"] == "already sent by dashboard"
    _cleanup()


def test_retry_queue_item_rearms_original_receipt(monkeypatch):
    """retry clears backoff on the original queued item without duplicating it."""
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    monkeypatch.setattr("packages.web.server.worker._schedule_session_recovery", lambda _sid: None)
    s = _setup_session("ses_q")
    task = dict(TASK_A, deliveryState="queued", nextAttemptAt=9999999999,
                lastDeliveryError="temporary")
    s.queue_pending = [task]

    r = asyncio.run(srv.api_session_queue_retry("ses_q", "task-aaa"))

    assert r["ok"] is True
    assert r["item"]["id"] == "task-aaa"
    assert len(s.queue_pending) == 1
    assert s.queue_pending[0]["deliveryState"] == "queued"
    assert "nextAttemptAt" not in s.queue_pending[0]
    _cleanup()


def test_retry_legacy_report_by_stable_hash_id(monkeypatch):
    """Old report rows without an id still retry the original receipt."""
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    monkeypatch.setattr("packages.web.server.worker._schedule_session_recovery", lambda _sid: None)
    s = _setup_session("ses_q")
    report = dict(REPORT_PLAIN, nextAttemptAt=9999999999, lastDeliveryError="temporary")
    s.queue_pending = [report]
    item_id = srv._queue_item_id(report)

    r = asyncio.run(srv.api_session_queue_retry("ses_q", item_id))

    assert r["ok"] is True
    assert r["item"]["id"] == item_id
    assert len(s.queue_pending) == 1
    assert "nextAttemptAt" not in s.queue_pending[0]
    _cleanup()


# ── DELETE /queue/{item_id} ──

def test_delete_task_by_id(monkeypatch):
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = list(MIXED)

    r = asyncio.run(srv.api_session_queue_delete("ses_q", "task-aaa"))
    assert r["ok"] is True and r["queueItemId"] == "task-aaa"
    assert [it.get("id") for it in s.queue_pending if isinstance(it, dict) and it.get("type") == "task"] == ["task-bbb"]
    assert len(s.queue_pending) == 4, "report/qq 项不受影响"
    _cleanup()


def test_delete_report_and_qq_by_hash_id(monkeypatch):
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = list(MIXED)

    report_id = srv._queue_item_id(REPORT_PLAIN)
    r = asyncio.run(srv.api_session_queue_delete("ses_q", report_id))
    assert r["ok"] is True and r["queueItemId"] == report_id
    assert REPORT_PLAIN not in s.queue_pending
    assert len(s.queue_pending) == 4

    qq_id = srv._queue_item_id(QQ_ITEM)
    r = asyncio.run(srv.api_session_queue_delete("ses_q", qq_id))
    assert r["ok"] is True and r["queueItemId"] == qq_id
    assert QQ_ITEM not in s.queue_pending
    assert len(s.queue_pending) == 3
    _cleanup()


def test_delete_not_found(monkeypatch):
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = list(MIXED)

    r = asyncio.run(srv.api_session_queue_delete("ses_q", "no-such-id"))
    assert r == {"ok": False, "error": "not_found"}
    assert len(s.queue_pending) == 5, "not_found 不得改动队列"
    _cleanup()


def test_delete_session_not_found():
    r = asyncio.run(srv.api_session_queue_delete("ses_nope", "whatever"))
    assert "not found" in r.get("error", "")


# ── PATCH /queue/order ──

def test_patch_order_all_sources(monkeypatch):
    """user/agent/report/QQ 可以通过同一 order 任意交换位置。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    import copy
    mixed = copy.deepcopy(MIXED)
    s.queue_pending = mixed
    ids = [srv._queue_item_id(item) for item in mixed]
    r = asyncio.run(srv.api_session_queue_order(
        "ses_q", {"order": [ids[2], ids[1], ids[4], ids[0], ids[3]]}))
    assert [it["id"] for it in r["items"]] == [ids[2], ids[1], ids[4], ids[0], ids[3]]
    assert [srv._queue_item_id(it) for it in s.queue_pending] == [
        ids[2], ids[1], ids[4], ids[0], ids[3]]
    _cleanup()


def test_patch_order_http_route_precedes_item_route(monkeypatch):
    """The literal /order path must not be captured as item_id='order'."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    import copy
    mixed = copy.deepcopy(MIXED)
    s.queue_pending = mixed
    ids = [srv._queue_item_id(item) for item in mixed]

    with TestClient(srv.app) as client:
        response = client.patch(
            "/api/sessions/ses_q/queue/order",
            json={"orderedIds": [ids[2], ids[1], ids[4], ids[0], ids[3]]},
        )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        ids[2], ids[1], ids[4], ids[0], ids[3]]
    _cleanup()


def test_patch_order_partial_keeps_tail_order(monkeypatch):
    """只指定部分 queued id 时，其余所有来源按原相对顺序追加。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    import copy
    mixed = copy.deepcopy(MIXED)
    s.queue_pending = mixed

    r = asyncio.run(srv.api_session_queue_order("ses_q", {"order": ["task-bbb"]}))
    items = r["items"]
    assert items[0]["id"] == "task-bbb"
    assert [item["id"] for item in items] == [
        "task-bbb", "task-aaa", srv._queue_item_id(mixed[1]),
        srv._queue_item_id(mixed[3]), srv._queue_item_id(mixed[4])]
    _cleanup()


def test_patch_order_unknown_and_missing(monkeypatch):
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = list(MIXED)

    # unknown id is rejected rather than silently changing a partial order.
    r = asyncio.run(srv.api_session_queue_order(
        "ses_q", {"order": [srv._queue_item_id(REPORT_PLAIN), "task-bbb", "ghost"]}))
    assert r["ok"] is False
    assert r["error"]["code"] == "invalid_queue_order"

    # 缺 order 参数 → error
    r = asyncio.run(srv.api_session_queue_order("ses_q", {}))
    assert "order" in r.get("error", "")
    _cleanup()


def test_patch_order_session_not_found():
    r = asyncio.run(srv.api_session_queue_order("ses_nope", {"order": []}))
    assert "not found" in r.get("error", "")


if __name__ == "__main__":
    test_get_queue_mixed()
    test_get_queue_stable_ids()
    test_get_queue_skips_malformed()
    test_get_queue_report_truncated()
    test_get_queue_session_not_found()
    test_get_queue_empty()
    test_delete_task_by_id()
    test_delete_report_and_qq_by_hash_id()
    test_delete_not_found()
    test_delete_session_not_found()
    test_patch_order_all_sources()
    test_patch_order_partial_keeps_tail_order()
    test_patch_order_unknown_and_missing()
    test_patch_order_session_not_found()
    print("\n=== ALL SESSION QUEUE API TESTS PASSED ===")

