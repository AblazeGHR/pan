"""agent_notify（MCP 投递提醒）单测。

覆盖：
- core worker.enqueue_notice：三步投递链路（queue_pending append + save_async +
  _wake_worker(target, auto_spawn=True)）
  - 投递到自己 / 投递到其他（managed）session：item 形状（report 形状 +
    type="notice"）、ok 返回、队列计数
  - session 不存在 → {"ok": False, "error": {code: session_not_found}}
  - 无活 worker → 立即 create_worker（auto_spawn 生效）；有活 worker → 只发
    report_signal 不 spawn
- 渲染兼容：_format_report_batch 对 notice 项按 report 分支渲染
  （@@@@by agent : <source> 抬头 + status/type/result 字段行）
- 前端显示兼容：web/server._serialize_queue_item 对 notice 项 → kind="report"
- web POST /api/notify：ok 路径、缺参 error、session 不存在 error 透传
- MCP agent_notify 工具：自己/managed 放行（POST /api/notify 带
  source=agent + sourceSessionId=caller）；
  非 managed 目标 permission_denied（且不触发 /api/notify、不做 claim）；
  无身份调用方不受限
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter
import packages.mcp.server as mcp_server
import packages.web.server as srv


@pytest.fixture(autouse=True)
def _isolate_session_dir(tmp_path, monkeypatch):
    """把 session 落盘重定向到临时目录，避免测试污染真实 data/sessions/。"""
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(_sess, "_all_loaded", False)
    yield
    _cleanup()


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    worker._spawn_locks.clear()
    _sess._cache.clear()
    _sess._all_loaded = False
    worker.set_broadcaster(None)


def _setup_session(sid, queue_pending=None):
    s = _sess.Session(id=sid, name=sid)
    if queue_pending:
        s.queue_pending = list(queue_pending)
    _sess._cache[sid] = s
    return s


def _setup_worker(session_id, worker_id="worker-test"):
    proc = AsyncMock()
    proc.returncode = None
    w = worker.Worker(
        worker_id=worker_id,
        session_id=session_id,
        adapter=CbcAdapter(),
        status="idle",
        process=proc,
        pending_signal=asyncio.Queue(),
    )
    worker.workers[w.worker_id] = w
    return w


# ── core: enqueue_notice 三步链路 ──


def test_notice_to_self_enqueued(monkeypatch):
    """投递给自己：item 以 report 形状 + type=notice 入队，返回 ok + 队列计数。"""
    _cleanup()
    s = _setup_session("ses_self")
    saves = []

    async def fake_save_async(target):
        saves.append(target.id)

    monkeypatch.setattr(_sess, "save_async", fake_save_async)
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id)

    monkeypatch.setattr(worker, "create_worker", fake_create)
    _setup_worker("ses_self", worker_id="worker-self")  # 活 worker：只唤醒

    r = asyncio.run(worker.enqueue_notice(
        "ses_self", "后台任务完成", source="agent", source_session_id="ses_self"))
    assert r["ok"] is True
    assert r["sessionId"] == "ses_self"
    assert r["pending"] == 1
    assert saves == ["ses_self"], "投递后必须 save_async 落盘"
    assert len(s.queue_pending) == 1
    item = s.queue_pending[0]
    assert item["type"] == "notice"
    assert item["status"] == "notice"
    assert item["result"] == "后台任务完成"
    assert item["sessionId"] == "ses_self"
    assert item["source"] == "agent"
    assert item["sourceSessionId"] == "ses_self"
    assert item["taskId"] is None and item["workerId"] is None
    assert spawned == [], "有活 worker 不应 spawn"
    _cleanup()


def test_notice_to_other_session_enqueued(monkeypatch):
    """投递到另一（managed）session：目标 session 收到提醒项。"""
    _cleanup()
    src = _setup_session("ses_a")
    tgt = _setup_session("ses_b")
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    _setup_worker("ses_b", worker_id="worker-b")

    r = asyncio.run(worker.enqueue_notice(
        "ses_b", "hello b", source="agent", source_session_id="ses_a"))
    assert r["ok"] is True and r["sessionId"] == "ses_b"
    assert len(tgt.queue_pending) == 1
    assert tgt.queue_pending[0]["result"] == "hello b"
    assert tgt.queue_pending[0]["sessionId"] == "ses_b"
    assert tgt.queue_pending[0]["source"] == "agent"
    assert tgt.queue_pending[0]["sourceSessionId"] == "ses_a", "source 记录来源"
    assert len(src.queue_pending) == 0, "来源 session 队列不受影响"
    w = worker.workers["worker-b"]
    assert w.pending_signal.qsize() == 1
    assert w.pending_signal.get_nowait() == {"type": "report_signal"}
    _cleanup()


def test_notice_session_not_found(monkeypatch):
    """目标 session 不存在 → ok False + session_not_found，不 spawn。"""
    _cleanup()
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id)

    monkeypatch.setattr(worker, "create_worker", fake_create)
    r = asyncio.run(worker.enqueue_notice("ses_ghost", "x"))
    assert r["ok"] is False
    assert r["error"]["code"] == "session_not_found"
    assert spawned == []
    _cleanup()


def test_notice_auto_spawns_when_no_worker(monkeypatch):
    """无活 worker → _wake_worker(target, auto_spawn=True) 立即 create_worker。"""
    _cleanup()
    _setup_session("ses_dead")
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    spawned = []

    async def fake_create(session_id):
        spawned.append(session_id)
        return _setup_worker(session_id, worker_id="worker-new")

    monkeypatch.setattr(worker, "create_worker", fake_create)

    r = asyncio.run(worker.enqueue_notice("ses_dead", "wake up"))
    assert r["ok"] is True
    assert spawned == ["ses_dead"], "no-worker 目标必须立即 auto-spawn"
    _cleanup()


def test_notice_spawn_failure_swallowed(monkeypatch):
    """auto-spawn 失败（返回错误串）→ 不抛异常，提醒已落盘。"""
    _cleanup()
    s = _setup_session("ses_dead")
    monkeypatch.setattr(_sess, "save_async", AsyncMock())

    async def fake_create(session_id):
        return "spawn boom"

    monkeypatch.setattr(worker, "create_worker", fake_create)
    r = asyncio.run(worker.enqueue_notice("ses_dead", "still persisted"))
    assert r["ok"] is True
    assert len(s.queue_pending) == 1
    _cleanup()


def test_notice_persists_state_and_coexists_with_task(monkeypatch):
    """通知保持报告状态，恢复时只产生 report_signal，不吞掉 task。"""
    _cleanup()
    s = _setup_session("ses_mix", queue_pending=[{
        "type": "task", "id": "task-1", "text": "ordinary task",
        "source": "agent", "deliveryState": "queued",
    }])
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    w = _setup_worker("ses_mix")
    _setup_session("ses_src")
    r = asyncio.run(worker.enqueue_notice(
        "ses_mix", "background done", source="agent", source_session_id="ses_src"))
    assert r["ok"] is True
    assert s.queue_pending[-1]["deliveryState"] == "queued"
    assert worker._recover_pending_signals(w, s) is False
    signals = [w.pending_signal.get_nowait() for _ in range(w.pending_signal.qsize())]
    assert {signal["type"] for signal in signals} == {"task_signal", "report_signal"}
    assert all(signal.get("id") != s.queue_pending[-1].get("id")
               for signal in signals if signal["type"] == "task_signal")
    _cleanup()


def test_notice_save_failure_keeps_queued_item(monkeypatch):
    """落盘失败时通知不能被报告/恢复路径误认为已消费。"""
    _cleanup()
    s = _setup_session("ses_save_fail")

    async def fail_save(_session):
        raise OSError("disk full")

    monkeypatch.setattr(_sess, "save_async", fail_save)
    with pytest.raises(OSError):
        asyncio.run(worker.enqueue_notice("ses_save_fail", "must retry"))
    assert len(s.queue_pending) == 1
    assert s.queue_pending[0]["deliveryState"] == "queued"
    _cleanup()


# ── 渲染 / normalize 兼容 ──


def test_notice_rendered_by_report_branch():
    """_format_report_batch 按 report 分支渲染 notice 项（抬头带 source）。"""
    item = {"status": "notice", "type": "notice", "result": "job done",
            "source": "agent", "sourceSessionId": "ses_src",
            "sessionId": "ses_target", "taskId": None, "workerId": None}
    _setup_session("ses_src", queue_pending=[])
    text = worker._format_report_batch([item])
    assert "@@@@by agent : ses_src | ses_src" in text
    assert "status: notice" in text
    assert "type: notice" in text
    assert "result:" in text and "job done" in text
    _cleanup()


def test_notice_normalize_display(monkeypatch):
    """前端 normalize：notice 项 → kind=report、text=result，不被跳过。"""
    monkeypatch.setattr(_sess, "save_async", AsyncMock())

    async def fake_create(session_id):
        return _setup_worker(session_id, worker_id="worker-new")

    monkeypatch.setattr(worker, "create_worker", fake_create)
    s = _setup_session("ses_q")
    _setup_session("ses_src")
    r = asyncio.run(worker.enqueue_notice(
        "ses_q", "panel item", source="agent", source_session_id="ses_src"))
    assert r["ok"] is True
    si = srv._serialize_queue_item(s.queue_pending[0])
    assert si is not None
    assert si["kind"] == "report"
    assert si["text"] == "panel item"
    assert si["meta"]["status"] == "notice"
    _cleanup()


# ── web: POST /api/notify ──


def test_api_notify_ok(monkeypatch):
    _cleanup()
    s = _setup_session("ses_t")
    _setup_session("ses_s")
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    _setup_worker("ses_t")

    r = asyncio.run(srv.api_notify(
        {"targetSessionId": "ses_t", "text": "ntf",
         "source": "agent", "sourceSessionId": "ses_s"}))
    assert r["ok"] is True
    assert s.queue_pending[0]["result"] == "ntf"
    _cleanup()


def test_api_notify_missing_params():
    r = asyncio.run(srv.api_notify({"targetSessionId": "ses_t"}))
    assert "error" in r and "targetSessionId" in r["error"]
    r = asyncio.run(srv.api_notify({"text": "no target"}))
    assert "error" in r
    r = asyncio.run(srv.api_notify({"targetSessionId": "  ", "text": "x"}))
    assert "error" in r


def test_api_notify_session_not_found(monkeypatch):
    """目标不存在 → enqueue_notice 的 error 透传为 {"error": ...}。"""
    _cleanup()
    r = asyncio.run(srv.api_notify({"targetSessionId": "ses_ghost", "text": "x"}))
    assert "error" in r
    assert "ses_ghost" in r["error"]
    _cleanup()


# ── MCP: agent_notify 工具 ──


class _FakeNotifyAPI:
    """路由 fake：/api/notify 记录调用，sessions 支持 GET 身份解析。"""

    def __init__(self, sessions):
        self.sessions = sessions
        self.calls = []

    def __call__(self, method, path, body=None, timeout=30.0):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/api/sessions/"):
            sid = path.split("/api/sessions/", 1)[1].split("?")[0]
            s = self.sessions.get(sid)
            return dict(s) if s else {"error": f"Session {sid} not found"}
        if method == "POST" and path == "/api/notify":
            return {"ok": True, "sessionId": body.get("targetSessionId")}
        return {"ok": True}


def _ma_session(managed=None, sid="ses_ma"):
    return {sid: {"id": sid,
                  "panAccess": {"restrictToManaged": True, "canClaimUnmanaged": True,
                                "autoClaimCreated": True},
                  "restrictToManaged": True, "canClaimUnmanaged": True,
                  "autoClaimCreated": True, "managed": list(managed or [])}}


def test_agent_notify_self_allowed(monkeypatch):
    """restricted caller 投递给自己 → 放行，POST /api/notify 带 source=caller。"""
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    fake = _FakeNotifyAPI({**_ma_session(managed=["ses_child"])})
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.agent_notify("ses_ma", "self notice")
    assert r.get("ok") is True, r
    assert ("POST", "/api/notify", {"targetSessionId": "ses_ma",
                                        "text": "self notice",
                                        "source": "agent",
                                        "sourceSessionId": "ses_ma"}) in fake.calls
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    _cleanup()


def test_agent_notify_managed_allowed(monkeypatch):
    """restricted caller 投递到 managed session → 放行。"""
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    fake = _FakeNotifyAPI({**_ma_session(managed=["ses_child"])})
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.agent_notify("ses_child", "child notice")
    assert r.get("ok") is True, r
    assert any(c[1] == "/api/notify" for c in fake.calls)
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    _cleanup()


def test_agent_notify_denied_unmanaged(monkeypatch):
    """restricted caller 投递到非 managed 目标 → permission_denied，
    不触发 /api/notify，也不做 claim（notify 不扩权）。"""
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    fake = _FakeNotifyAPI({
        **_ma_session(managed=["ses_ok"]),
        "ses_other": {"id": "ses_other", "managedBy": None},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.agent_notify("ses_other", "intrude")
    assert r.get("ok") is False
    assert r["error"]["code"] == "permission_denied"
    assert all(c[1] != "/api/notify" for c in fake.calls)
    assert all(c[1] != "/api/claim" for c in fake.calls)
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    _cleanup()


def test_agent_notify_no_identity_unrestricted(monkeypatch):
    """无调用方身份 → 不受限，直达 /api/notify，body 不带 source。"""
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    fake = _FakeNotifyAPI({"ses_any": {"id": "ses_any"}})
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.agent_notify("ses_any", "anon notice")
    assert r.get("ok") is True, r
    assert fake.calls[-1][2] == {"targetSessionId": "ses_any",
                                 "text": "anon notice"}
    _cleanup()


if __name__ == "__main__":
    test_notice_to_self_enqueued(None)
    test_notice_to_other_session_enqueued(None)
    test_notice_session_not_found(None)
    test_notice_auto_spawns_when_no_worker(None)
    test_notice_spawn_failure_swallowed(None)
    test_notice_rendered_by_report_branch()
    test_notice_normalize_display(None)
    test_api_notify_ok(None)
    test_api_notify_missing_params()
    test_api_notify_session_not_found(None)
    test_agent_notify_self_allowed(None)
    test_agent_notify_managed_allowed(None)
    test_agent_notify_denied_unmanaged(None)
    test_agent_notify_no_identity_unrestricted(None)
    print("\n=== ALL AGENT NOTIFY TESTS PASSED ===")
