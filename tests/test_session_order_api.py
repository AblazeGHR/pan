"""自定义 Session 排序（custom session order）测试。

覆盖：
- POST /api/sessions/order 成功路径：完整排序 / 部分排序（未列出的 session
  按当前相对顺序追加），每个 session 获得显式 order 值
- 非法输入：缺/错 sessionIds、重复 id、不存在的 id → ok:false + 精确错误码，
  且不改动任何顺序
- 持久化：排序落盘后清空 _cache 重载，顺序保持（真实读写 conftest 隔离目录）
- 一致性：重排后新建 session（order=None）排在末尾；删除 session 不破坏剩余
  相对顺序；重命名不影响 order
- 快捷管理（拖 A 到 B 卡片正中间 = B manage A）：复用 POST /api/claim 成功
  建立 managed 关系 + report 订阅；A 已被他人管理时拒绝

风格与 test_session_queue_api.py 一致：_cache 临时 session + monkeypatch
save/save_async（no-op）做逻辑测试；持久化测试用真实落盘（conftest 已把
SESSION_DIR 隔离到 tmp_path）。
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
    _sess._all_loaded = False


def _setup_session(sid, **kwargs):
    s = _sess.Session(id=sid, name=sid, **kwargs)
    _sess._cache[sid] = s
    return s


async def _noop_save_async(s):
    pass


def _noop_save(s):
    pass


def _reorder(ids, monkeypatch=None):
    if monkeypatch:
        monkeypatch.setattr(_sess, "save", _noop_save)
        monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    return asyncio.run(srv.api_sessions_order({"sessionIds": ids}))


def _list_ids():
    return [s.id for s in _sess.list_all()]


# ── 成功路径 ──

def test_order_full_reorder(monkeypatch):
    """完整排序：提交 [C, A, B] → list_all 顺序与 order 值 0/1/2。"""
    a = _setup_session("ses_a")
    b = _setup_session("ses_b")
    c = _setup_session("ses_c")

    r = _reorder(["ses_c", "ses_a", "ses_b"], monkeypatch)

    assert r["ok"] is True
    assert r["order"] == ["ses_c", "ses_a", "ses_b"]
    assert _list_ids() == ["ses_c", "ses_a", "ses_b"]
    assert c.order == 0 and a.order == 1 and b.order == 2
    _cleanup()


def test_order_partial_appends_rest_in_current_order(monkeypatch):
    """部分排序：只提交 [C]，A/B 按当前相对顺序追加在后面，且获得显式值。"""
    a = _setup_session("ses_a")
    b = _setup_session("ses_b")
    c = _setup_session("ses_c")

    r = _reorder(["ses_c"], monkeypatch)

    assert r["ok"] is True
    assert _list_ids() == ["ses_c", "ses_a", "ses_b"]
    assert [c.order, a.order, b.order] == [0, 1, 2]
    _cleanup()


def test_order_swap_between_two_sessions(monkeypatch):
    """拖拽把 A 放到 B/C 之间：提交新全序即可精确落位。"""
    a = _setup_session("ses_a")
    b = _setup_session("ses_b")
    c = _setup_session("ses_c")

    r = _reorder(["ses_b", "ses_a", "ses_c"], monkeypatch)

    assert r["ok"] is True
    assert _list_ids() == ["ses_b", "ses_a", "ses_c"]
    assert a.order == 1, "A 位于 B 与 C 之间"
    _cleanup()


def test_order_broadcasts_order_updated(monkeypatch):
    """排序成功后广播 session.orderUpdated 事件。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    sent = []

    async def _capture(data):
        sent.append(data)

    _setup_session("ses_a")
    _setup_session("ses_b")
    monkeypatch.setattr(srv, "broadcast", _capture)

    asyncio.run(srv.api_sessions_order({"sessionIds": ["ses_b", "ses_a"]}))

    assert len(sent) == 1
    assert sent[0]["type"] == "session.orderUpdated"
    assert sent[0]["order"] == ["ses_b", "ses_a"]
    _cleanup()


# ── 非法输入 ──

def test_order_invalid_params(monkeypatch):
    """缺 sessionIds / 非 list / 含非字符串 → invalid_params，顺序不动。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    _setup_session("ses_a")

    for body in [{}, {"sessionIds": "ses_a"}, {"sessionIds": [1, 2]},
                 {"sessionIds": [""]}, {"sessionIds": None}]:
        r = asyncio.run(srv.api_sessions_order(body))
        assert r["ok"] is False, body
        assert r["error"]["code"] == "invalid_params", body
    assert _list_ids() == ["ses_a"]
    _cleanup()


def test_order_duplicate_ids_rejected(monkeypatch):
    """重复 id → duplicate_session_ids，且不改动任何顺序。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    a = _setup_session("ses_a")
    b = _setup_session("ses_b")

    r = _reorder(["ses_a", "ses_b", "ses_a"], monkeypatch)

    assert r["ok"] is False
    assert r["error"]["code"] == "duplicate_session_ids"
    assert _list_ids() == ["ses_a", "ses_b"], "拒绝时顺序不变"
    assert a.order is None and b.order is None
    _cleanup()


def test_order_unknown_session_rejected(monkeypatch):
    """不存在的 sessionId → session_not_found，且不改动任何顺序。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    a = _setup_session("ses_a")
    b = _setup_session("ses_b")

    r = _reorder(["ses_b", "ses_ghost"], monkeypatch)

    assert r["ok"] is False
    assert r["error"]["code"] == "session_not_found"
    assert "ses_ghost" in r["error"]["message"]
    assert _list_ids() == ["ses_a", "ses_b"], "拒绝时顺序不变"
    assert a.order is None and b.order is None
    _cleanup()


def test_order_no_sessions(monkeypatch):
    """空列表（无 session）→ 成功且返回空 order。"""
    r = _reorder([], monkeypatch)
    assert r == {"ok": True, "order": []}
    _cleanup()


# ── 持久化 ──

def test_order_persists_across_cache_reload():
    """真实落盘：排序后清空 _cache 重载，顺序与 order 值保持。"""
    a = _sess.create(name="ses_a")
    b = _sess.create(name="ses_b")
    c = _sess.create(name="ses_c")

    r = asyncio.run(srv.api_sessions_order(
        {"sessionIds": [c.id, a.id, b.id]}))

    assert r["ok"] is True
    # 模拟重启：清缓存 + 重置加载标记，从磁盘重读
    _sess._cache.clear()
    _sess._all_loaded = False
    assert _list_ids() == [c.id, a.id, b.id]
    assert [c.order, a.order, b.order] == [0, 1, 2]

    d = _sess.Session._from_data(c.to_dict())
    assert d.order == 0, "order 字段经 to_dict/_from_data 往返保持"
    _cleanup()


# ── 一致性：新增 / 删除 / 重命名 ──

def test_new_session_sorts_after_existing_order():
    """重排后新建 session（order=None）排在末尾，不打乱已有顺序。"""
    a = _sess.create(name="ses_a")
    b = _sess.create(name="ses_b")
    asyncio.run(srv.api_sessions_order({"sessionIds": [b.id, a.id]}))
    c = _sess.create(name="ses_c")

    assert _list_ids() == [b.id, a.id, c.id]
    _cleanup()


def test_delete_keeps_remaining_relative_order():
    """删除已排序 session 后，剩余 session 相对顺序不变（order 值允许有空洞）。"""
    a = _sess.create(name="ses_a")
    b = _sess.create(name="ses_b")
    c = _sess.create(name="ses_c")
    asyncio.run(srv.api_sessions_order({"sessionIds": [a.id, b.id, c.id]}))

    _sess.delete(b.id)

    assert _list_ids() == [a.id, c.id]
    _cleanup()


def test_rename_keeps_order(monkeypatch):
    """重命名不改 order。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    b = _setup_session("ses_b")
    a = _setup_session("ses_a")
    asyncio.run(srv.api_sessions_order({"sessionIds": ["ses_b", "ses_a"]}))

    b.name = "renamed"

    assert b.order == 0
    assert _list_ids() == ["ses_b", "ses_a"]
    _cleanup()


# ── 快捷管理：拖 A 到 B 卡片正中间 = B manage A（复用 POST /api/claim）──

def test_quick_manage_claim_success(monkeypatch):
    """快捷管理成功：B.managed 包含 A，A.managed_by=B，并自动订阅完成报告。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    a = _setup_session("ses_a")
    b = _setup_session("ses_b")

    r = asyncio.run(srv.api_claim({"managerId": "ses_b", "sessionId": "ses_a"}))

    assert r["ok"] is True
    assert a.managed_by == "ses_b"
    assert "ses_a" in b.managed
    assert "ses_a" in b.report_subscriptions, "claim 自动订阅完成报告"
    _cleanup()


def test_quick_manage_claim_rejected_when_already_managed(monkeypatch):
    """A 已被 C 管理时，拖到 B 上 → claim_failed，关系不被破坏。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    a = _setup_session("ses_a")
    b = _setup_session("ses_b")
    c = _setup_session("ses_c")
    assert _sess.claim("ses_c", "ses_a") is None

    r = asyncio.run(srv.api_claim({"managerId": "ses_b", "sessionId": "ses_a"}))

    assert r["ok"] is False
    assert r["error"]["code"] == "claim_failed"
    assert a.managed_by == "ses_c", "原关系保持"
    assert "ses_a" not in b.managed
    _cleanup()


def test_quick_manage_after_reorder_combined_flow(monkeypatch):
    """完整拖拽场景：先排序（A 落到 B/C 之间），再快捷管理（B manage A）。"""
    monkeypatch.setattr(_sess, "save", _noop_save)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    a = _setup_session("ses_a")
    b = _setup_session("ses_b")
    c = _setup_session("ses_c")

    r1 = _reorder(["ses_b", "ses_a", "ses_c"], monkeypatch)
    r2 = asyncio.run(srv.api_claim({"managerId": "ses_b", "sessionId": "ses_a"}))

    assert r1["ok"] is True and r2["ok"] is True
    assert _list_ids() == ["ses_b", "ses_a", "ses_c"]
    assert a.managed_by == "ses_b"
    assert "ses_a" in b.managed
    _cleanup()


if __name__ == "__main__":
    test_order_full_reorder()
    test_order_partial_appends_rest_in_current_order()
    test_order_swap_between_two_sessions()
    test_order_broadcasts_order_updated()
    test_order_invalid_params()
    test_order_duplicate_ids_rejected()
    test_order_unknown_session_rejected()
    test_order_no_sessions()
    test_order_persists_across_cache_reload()
    test_new_session_sorts_after_existing_order()
    test_delete_keeps_remaining_relative_order()
    test_rename_keeps_order()
    test_quick_manage_claim_success()
    test_quick_manage_claim_rejected_when_already_managed()
    test_quick_manage_after_reorder_combined_flow()
    print("\n=== ALL SESSION ORDER API TESTS PASSED ===")
