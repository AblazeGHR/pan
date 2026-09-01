"""Session agent queue 三端点测试（GET /queue、DELETE /queue/{item_id}、PATCH /queue/order）。

覆盖：
- GET 序列化：task / 普通 report（无 type 字段）/ zombie report（type=zombie）/
  qq / 畸形项（非 dict、无 result 的怪 dict）→ 归一化 AgentQueueItem，保持原顺序
- 稳定 id：task 用自身 uuid；report/qq 用 sha1(json.dumps(sort_keys)) 生成
  （同内容幂等、同 id 稳定）
- report text 截断：result > 200 字 → 截断 + "…"
- DELETE：task 按 id 命中、report/qq 按 hash id 命中、未知 id → not_found、
  session 不存在 → error
- PATCH order：task 重排且 report/qq 保持原槽位；未提及的 task 追加在 task 序列
  末尾；缺 order 参数 / session 不存在 → error；返回重排后的 items

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
    assert items[0] == {
        "id": "task-aaa", "kind": "task", "text": "task A", "createdAt": 0,
        "source": "agent", "meta": {"seq": 1, "taskId": "t-a", "dispatchState": "queued"},
    }
    # report（无 type 字段）：result 即 text
    assert items[1]["kind"] == "report"
    assert items[1]["text"] == "report r1"
    assert items[1]["meta"] == {"status": "done", "taskId": "t-a", "workerId": "worker-1", "dispatchState": "queued"}
    # zombie report（type=zombie）也归为 report
    assert items[3]["kind"] == "report"
    assert items[3]["text"] == "worker died"
    assert items[2]["source"] == "user", "user task source must survive normalization"
    # qq
    assert items[4]["kind"] == "qq"
    assert items[4]["text"] == "hi from qq"
    assert items[4]["meta"] == {"qqTarget": "user:12345", "time": "12:00", "dispatchState": "queued"}
    _cleanup()


def test_get_queue_stable_ids(monkeypatch):
    """report/qq 无 id 字段 → sha1(canonical json) 稳定 id；同内容幂等。"""
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = [REPORT_PLAIN, dict(REPORT_PLAIN), QQ_ITEM]

    items = asyncio.run(srv.api_session_queue("ses_q"))["items"]
    assert items[0]["id"] == items[1]["id"], "same content → same id (idempotent)"
    assert items[0]["id"].startswith("sha1:")
    assert items[2]["id"].startswith("sha1:")
    assert items[0]["id"] != items[2]["id"]
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
    assert asyncio.run(srv.api_session_queue("ses_q")) == {"items": []}
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
    assert r == {"ok": True}
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
    assert r == {"ok": True}
    assert REPORT_PLAIN not in s.queue_pending
    assert len(s.queue_pending) == 4

    qq_id = srv._queue_item_id(QQ_ITEM)
    r = asyncio.run(srv.api_session_queue_delete("ses_q", qq_id))
    assert r == {"ok": True}
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

def test_patch_order_tasks_only(monkeypatch):
    """task 按新 order 重排；report/qq 保持原槽位。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = list(MIXED)  # [TA, REP, TB, ZOMBIE, QQ]

    r = asyncio.run(srv.api_session_queue_order("ses_q", {"order": ["task-bbb", "task-aaa"]}))
    items = r["items"]
    # 槽位形状不变：task/report/task/report/qq，task 互换
    assert [it["kind"] for it in items] == ["task", "report", "task", "report", "qq"]
    assert items[0]["id"] == "task-bbb"
    assert items[2]["id"] == "task-aaa"
    assert items[1]["text"] == "report r1"
    assert items[4]["kind"] == "qq"
    # 落盘顺序同步更新
    assert [it["id"] for it in s.queue_pending if isinstance(it, dict) and it.get("type") == "task"] == ["task-bbb", "task-aaa"]
    _cleanup()


def test_patch_order_partial_keeps_tail_order(monkeypatch):
    """order 只含部分 task id → 未提及的 task 依原相对顺序追加在 task 序列末尾。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = list(MIXED)

    r = asyncio.run(srv.api_session_queue_order("ses_q", {"order": ["task-bbb"]}))
    items = r["items"]
    assert items[0]["id"] == "task-bbb"
    assert items[2]["id"] == "task-aaa"
    _cleanup()


def test_patch_order_unknown_and_missing(monkeypatch):
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    s = _setup_session("ses_q")
    s.queue_pending = list(MIXED)

    # 未知 id / report 的 hash id 混入 → 忽略，不影响 task 排布
    r = asyncio.run(srv.api_session_queue_order(
        "ses_q", {"order": [srv._queue_item_id(REPORT_PLAIN), "task-bbb", "ghost"]}))
    kinds = [it["kind"] for it in r["items"]]
    assert kinds == ["task", "report", "task", "report", "qq"]
    assert r["items"][0]["id"] == "task-bbb"

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
    test_patch_order_tasks_only()
    test_patch_order_partial_keeps_tail_order()
    test_patch_order_unknown_and_missing()
    test_patch_order_session_not_found()
    print("\n=== ALL SESSION QUEUE API TESTS PASSED ===")
