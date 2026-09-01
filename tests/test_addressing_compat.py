"""Tests for addressing compat layer (阶段 6 编排寻址兼容).

- worker_send / worker_send_force / worker_kill 接受 session_id（与 worker_id 并存）
- send 无活 worker 时不报错：入 Session.queue_pending → 全局 watchdog spawn →
  _recover_pending_signals 补发信号分发（「send = 写给 agent，进程是顺带的」）
- worker_id 旧路径行为完全保留
- M18 隔离不倒退：session_id 路径同样过 _check_access
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter
import packages.mcp.server as mcp_server
import packages.web.server as web_srv


# ── shared fixtures / fakes ──

def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    worker._spawn_locks.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid):
    s = _sess.Session(id=sid, name=sid, adapter="cbc", model="test-model")
    _sess._cache[sid] = s
    return s


def _setup_worker(session_id, worker_id="worker-test"):
    """oneshot 式注册 worker（process=None，send_task 视为活）。"""
    w = worker.Worker(
        worker_id=worker_id,
        session_id=session_id,
        adapter=CbcAdapter(),
        status="idle",
        process=None,
        pending_signal=asyncio.Queue(),
    )
    worker.workers[w.worker_id] = w
    return w


async def _noop_save_async(s):
    pass


class _FakeAPI:
    """Routing fake for packages.mcp.server._api（仿 test_mcp_isolation）。"""

    def __init__(self, sessions, workers=None, allow_claim=True):
        self.sessions = sessions
        self.workers = workers if workers is not None else []
        self.allow_claim = allow_claim
        self.calls = []

    def __call__(self, method, path, body=None, timeout=30.0):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/api/sessions/"):
            sid = path.split("/api/sessions/", 1)[1].split("?")[0]
            s = self.sessions.get(sid)
            return dict(s) if s else {"error": f"Session {sid} not found"}
        if method == "POST" and path == "/api/claim":
            mgr = self.sessions.get(body["managerId"])
            tgt = self.sessions.get(body["sessionId"])
            if mgr is None or tgt is None:
                return {"ok": False, "error": {"message": "not found"}}
            if tgt.get("managedBy") and tgt["managedBy"] != body["managerId"]:
                return {"ok": False, "error": {"message": "foreign managed"}}
            if not self.allow_claim:
                return {"ok": False, "error": {"message": "claim refused"}}
            tgt["managedBy"] = body["managerId"]
            return {"ok": True}
        if method == "GET" and path == "/api/list":
            return {"workers": list(self.workers)}
        if path in ("/api/assign", "/api/task", "/api/spawn", "/api/send"):
            return {"ok": True, "status": "queued"}
        return {"ok": True}


def _ma_session(managed=None, sid="ses_ma"):
    return {sid: {"id": sid,
                  "panAccess": {"restrictToManaged": True, "canClaimUnmanaged": True,
                                "autoClaimCreated": True},
                  "restrictToManaged": True, "canClaimUnmanaged": True,
                  "autoClaimCreated": True, "managed": list(managed or [])}}


# ── core: send_session ──

def test_send_session_live_worker_delivers():
    """有活 worker → 常规投递（send_task 路径），返回该 workerId。"""
    _cleanup()
    s = _setup_session("ses_a")
    w = _setup_worker(s.id, worker_id="worker-a1")

    async def scenario():
        return await worker.send_session(s.id, "hello agent")

    result = asyncio.run(scenario())
    assert result["status"] == "queued", result
    assert result["workerId"] == "worker-a1"
    assert w.pending_signal.qsize() == 1
    # 正文落在持久队列（L4），信号只唤醒
    assert any(it.get("type") == "task" and it.get("text") == "hello agent"
               for it in s.queue_pending)
    _cleanup()


def test_send_session_oneshot_worker_delivers():
    """oneshot MCP worker（process=None）视作活 worker，正常投递。"""
    _cleanup()
    s = _setup_session("ses_os")
    w = worker.Worker(
        worker_id="worker-os", session_id=s.id, adapter=CbcAdapter(),
        status="idle", process=None, pending_signal=asyncio.Queue())
    worker.workers[w.worker_id] = w

    async def scenario():
        return await worker.send_session(s.id, "hi")

    result = asyncio.run(scenario())
    assert result["status"] == "queued" and result["workerId"] == "worker-os", result
    assert w.pending_signal.qsize() == 1
    _cleanup()


def test_send_session_no_worker_enqueues_pending(monkeypatch):
    """核心语义：无活 worker 不报错 → 入持久队列，pendingSpawn=True。"""
    _cleanup()
    s = _setup_session("ses_b")
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    async def scenario():
        return await worker.send_session(s.id, "offline msg")

    result = asyncio.run(scenario())
    assert result["status"] == "queued", result
    assert result["workerId"] is None
    assert result["pendingSpawn"] is True
    assert result["sessionId"] == "ses_b"
    assert len(s.queue_pending) == 1
    item = s.queue_pending[0]
    assert item["type"] == "task" and item["text"] == "offline msg"
    assert item["id"], "item 必须带 id（task_signal 按 id 认领）"
    _cleanup()


def test_send_session_dead_process_worker_enqueues(monkeypatch):
    """注册 worker 但进程已死 → 视同无活 worker，入队不报错。"""
    _cleanup()
    s = _setup_session("ses_c")
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    proc = AsyncMock()
    proc.returncode = 1
    worker.workers["worker-dead"] = worker.Worker(
        worker_id="worker-dead", session_id=s.id, adapter=CbcAdapter(),
        status="idle", process=proc, pending_signal=asyncio.Queue())

    async def scenario():
        return await worker.send_session(s.id, "to dead worker")

    result = asyncio.run(scenario())
    assert result["status"] == "queued" and result["pendingSpawn"] is True, result
    assert s.queue_pending and s.queue_pending[0]["text"] == "to dead worker"
    _cleanup()


def test_send_session_unknown_session_errors():
    """session 不存在 → 明确错误（与 worker_id 路径的 unresolvable 对齐）。"""
    _cleanup()

    async def scenario():
        return await worker.send_session("ses_ghost", "hi")

    result = asyncio.run(scenario())
    assert result["status"] == "error" and "ses_ghost" in result["result"], result
    _cleanup()


def test_send_session_force_live_worker_restarts(monkeypatch):
    """force=True + 活 worker → 先 restart 再投递。"""
    _cleanup()
    s = _setup_session("ses_d")
    _setup_worker(s.id, worker_id="worker-d1")
    restarts = []

    async def fake_restart(worker_id):
        restarts.append(worker_id)
        return None

    monkeypatch.setattr(worker, "restart_worker", fake_restart)

    async def scenario():
        return await worker.send_session(s.id, "urgent", force=True)

    result = asyncio.run(scenario())
    assert result["status"] == "queued", result
    assert restarts == ["worker-d1"], restarts
    _cleanup()


def test_send_session_held_worker_errors(monkeypatch):
    """held（takeover 模式）→ 透传错误，不吞错不入队。"""
    _cleanup()
    s = _setup_session("ses_e")
    w = _setup_worker(s.id, worker_id="worker-e1")
    w.status = "held"
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)

    async def scenario():
        return await worker.send_session(s.id, "msg")

    result = asyncio.run(scenario())
    assert result["status"] == "error" and "held" in result["result"], result
    assert s.queue_pending == [], "held 不得入队"
    _cleanup()


def test_send_no_worker_enqueue_spawn_recover_closed_loop(monkeypatch):
    """闭环：send 入队 → 全局 watchdog spawn → generic wakeup → FIFO claim。"""
    _cleanup()
    s = _setup_session("ses_loop")
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    # watchdog tick 扫描 list_all()；限定只看本测试的 session，
    # 隔离其他用例经真实 save_async 落盘、可从磁盘重载的 session 文件
    monkeypatch.setattr(_sess, "list_all", lambda: [s])

    async def scenario():
        # 1) 无活 worker 时 send → 入队
        r = await worker.send_session(s.id, "queued then delivered")
        assert r["status"] == "queued" and r["pendingSpawn"] is True, r
        # 2) 全局 watchdog tick（queue_pending 非空 && 无活 worker → spawn）
        spawned = []

        async def fake_create(session_id):
            spawned.append(session_id)
            w = worker.Worker(
                worker_id="worker-loop", session_id=session_id,
                adapter=CbcAdapter(), status="idle", process=None,
                pending_signal=asyncio.Queue())
            worker.workers[w.worker_id] = w
            # 真实 create_worker 会在 spawn 后调 _recover_pending_signals
            worker._recover_pending_signals(w, s)
            return w

        orig = worker.create_worker
        worker.create_worker = fake_create
        try:
            await worker._global_watchdog_tick()
        finally:
            worker.create_worker = orig
        assert spawned == ["ses_loop"], spawned
        # 3) spawn 后 generic wakeup 已补发，可按 durable id 认领
        w = worker.workers["worker-loop"]
        assert w.pending_signal.qsize() == 1
        sig = w.pending_signal.get_nowait()
        assert sig["type"] == "queue_signal"
        claimed = await worker._claim_pending_task(w, s.queue_pending[0]["id"])
        assert claimed is not None and claimed["text"] == "queued then delivered"
        assert s.queue_pending[0]["deliveryState"] == "reserved"
        return r

    asyncio.run(scenario())
    _cleanup()


def test_worker_id_send_path_unchanged():
    """兼容回归：core worker.send（worker_id 寻址）行为不变。"""
    _cleanup()
    s = _setup_session("ses_f")
    w = _setup_worker(s.id, worker_id="worker-f1")

    async def scenario():
        return await worker.send("worker-f1", "legacy path")

    result = asyncio.run(scenario())
    assert result["status"] == "queued" and result["workerId"] == "worker-f1", result
    _cleanup()


# ── MCP: session_id 寻址 + worker_id 兼容 + 隔离 ──

def test_mcp_worker_send_by_session_id_posts_api_send(monkeypatch):
    """worker_send(session_id=...) → POST /api/send，过 _check_access，带前缀。"""
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    monkeypatch.setenv("PAN_AGENT_SESSION_TITLE", "meta")
    r = mcp_server.worker_send(session_id="ses_child", text="hi")
    assert r.get("ok") is True, r
    assert any(c[0] == "POST" and c[1] == "/api/send"
               and c[2]["sessionId"] == "ses_child"
               and c[2]["text"] == "////by agent : ses_ma | meta\nhi"
               for c in fake.calls), fake.calls
    _cleanup()


def test_mcp_worker_send_by_session_id_denied_unmanaged(monkeypatch):
    """隔离不倒退：session_id 寻址未过 access check → 拒绝，不触达 /api/send。"""
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=[]),
        "ses_other": {"id": "ses_other", "managedBy": "ses_rival"},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_send(session_id="ses_other", text="hi")
    assert r.get("ok") is False
    assert r["error"]["code"] == "permission_denied"
    assert all(c[1] != "/api/send" for c in fake.calls), fake.calls
    _cleanup()


def test_mcp_worker_send_by_worker_id_unchanged(monkeypatch):
    """兼容回归：worker_id 寻址仍走 /api/task（旧调用不受影响）。"""
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    }, workers=[{"workerId": "worker-1", "sessionId": "ses_child"}])
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_send(worker_id="worker-1", text="legacy")
    assert r.get("ok") is True, r
    assert any(c[0] == "POST" and c[1] == "/api/task"
               and c[2]["workerId"] == "worker-1" for c in fake.calls), fake.calls
    assert all(c[1] != "/api/send" for c in fake.calls)
    _cleanup()


def test_mcp_worker_send_missing_params(monkeypatch):
    """两者都不传 → 明确的 missing_params 错误。"""
    _cleanup()
    fake = _FakeAPI(_ma_session())
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.worker_send(text="hi")
    assert r.get("ok") is False
    assert r["error"]["code"] == "missing_params"
    _cleanup()


def test_mcp_worker_send_force_by_session_no_worker_enqueues(monkeypatch):
    """worker_send_force(session_id) 无活 worker → 入队（不 restart、不报错）。"""
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    }, workers=[])  # 该 session 无注册 worker
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_send_force(session_id="ses_child", text="urgent")
    assert r.get("ok") is True, r
    assert any(c[1] == "/api/send" and c[2]["sessionId"] == "ses_child"
               for c in fake.calls), fake.calls
    assert all("/restart" not in c[1] for c in fake.calls), "无 worker 不得 restart"
    _cleanup()


def test_mcp_worker_send_force_by_session_with_worker_restarts(monkeypatch):
    """worker_send_force(session_id) 有活 worker → restart + /api/task。"""
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    }, workers=[{"workerId": "worker-9", "sessionId": "ses_child"}])
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_send_force(session_id="ses_child", text="urgent")
    assert r.get("ok") is True, r
    assert any(c[1] == "/api/worker/worker-9/restart" for c in fake.calls), fake.calls
    assert any(c[1] == "/api/task" and c[2]["workerId"] == "worker-9"
               for c in fake.calls), fake.calls
    _cleanup()


def test_mcp_worker_send_force_by_worker_id_unchanged(monkeypatch):
    """兼容回归：worker_id 寻址 force 路径不变（含 unresolvable deny）。"""
    _cleanup()
    fake = _FakeAPI({**_ma_session(managed=["ses_child"])},
                    workers=[{"workerId": "worker-1", "sessionId": "ses_child"}])
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_send_force("worker-nope", "text")
    assert r.get("ok") is False
    assert r["error"]["code"] == "worker_not_found"
    assert all("/restart" not in c[1] for c in fake.calls)
    _cleanup()


def test_mcp_worker_kill_by_session_id_no_worker_noop_ok(monkeypatch):
    """worker_kill(session_id) 无活 worker → ok（killed=false），编排对象是 agent。"""
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    }, workers=[])
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_kill(session_id="ses_child")
    assert r.get("ok") is True and r.get("killed") is False, r
    assert all("/api/kill/" not in c[1] for c in fake.calls), fake.calls
    _cleanup()


def test_mcp_worker_kill_by_session_id_with_worker(monkeypatch):
    """worker_kill(session_id) 有活 worker → 解析 workerId 并 kill。"""
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    }, workers=[{"workerId": "worker-7", "sessionId": "ses_child"}])
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_kill(session_id="ses_child")
    assert any(c[1] == "/api/kill/worker-7" for c in fake.calls), fake.calls
    _cleanup()


def test_mcp_worker_kill_by_session_id_denied_unmanaged(monkeypatch):
    """隔离不倒退：session_id kill 未过 access check → 拒绝。"""
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=[]),
        "ses_other": {"id": "ses_other", "managedBy": "ses_rival"},
    }, workers=[{"workerId": "worker-2", "sessionId": "ses_other"}])
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_kill(session_id="ses_other")
    assert r.get("ok") is False
    assert r["error"]["code"] == "permission_denied"
    assert all("/api/kill/" not in c[1] for c in fake.calls), fake.calls
    _cleanup()


def test_mcp_worker_kill_by_worker_id_unchanged(monkeypatch):
    """兼容回归：worker_id 寻址 kill 行为不变。"""
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    }, workers=[{"workerId": "worker-1", "sessionId": "ses_child"}])
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_kill("worker-1")
    assert any(c[1] == "/api/kill/worker-1" for c in fake.calls), fake.calls
    _cleanup()


# ── web: /api/send ──

def test_api_send_by_session_id_delegates(monkeypatch):
    """/api/send sessionId → worker.send_session；无 worker 不报错。"""
    _cleanup()
    captured = {}

    async def fake_send_session(session_id, text, source="agent", force=False):
        captured.update(session_id=session_id, text=text, force=force)
        return {"status": "queued", "workerId": None, "sessionId": session_id,
                "pendingSpawn": True}

    monkeypatch.setattr(worker, "send_session", fake_send_session)

    result = asyncio.run(web_srv.api_send({"sessionId": "ses_g", "text": "hi"}))
    assert result["status"] == "queued" and result["pendingSpawn"] is True
    assert captured == {"session_id": "ses_g", "text": "hi", "force": False}, captured
    _cleanup()


def test_api_send_workerid_resolves_session(monkeypatch):
    """/api/send workerId → 先解析出 session 再走 send_session。"""
    _cleanup()
    s = _setup_session("ses_h")
    _setup_worker(s.id, worker_id="worker-h1")
    captured = {}

    async def fake_send_session(session_id, text, source="agent", force=False):
        captured.update(session_id=session_id, force=force)
        return {"status": "queued", "workerId": "worker-h1", "sessionId": session_id}

    monkeypatch.setattr(worker, "send_session", fake_send_session)

    result = asyncio.run(web_srv.api_send(
        {"workerId": "worker-h1", "text": "hi", "force": True}))
    assert result["status"] == "queued", result
    assert captured == {"session_id": "ses_h", "force": True}, captured
    _cleanup()


def test_api_send_error_translation(monkeypatch):
    """send_session error → 端点翻译为 {"error": ...}（与 /api/task 约定一致）。"""
    _cleanup()

    async def fake_send_session(session_id, text, source="agent", force=False):
        return {"status": "error", "result": f"Session {session_id} not found"}

    monkeypatch.setattr(worker, "send_session", fake_send_session)

    result = asyncio.run(web_srv.api_send({"sessionId": "ses_ghost", "text": "hi"}))
    assert "error" in result and "ses_ghost" in result["error"], result
    _cleanup()


def test_api_send_requires_params():
    """/api/send 缺参 → 明确错误。"""
    assert "error" in asyncio.run(web_srv.api_send({"sessionId": "s"}))
    assert "error" in asyncio.run(web_srv.api_send({"text": "hi"}))


if __name__ == "__main__":
    test_send_session_live_worker_delivers()
    test_send_session_oneshot_worker_delivers()
    test_send_session_no_worker_enqueues_pending()
    test_send_session_dead_process_worker_enqueues()
    test_send_session_unknown_session_errors()
    test_send_session_force_live_worker_restarts()
    test_send_session_held_worker_errors()
    test_send_no_worker_enqueue_spawn_recover_closed_loop()
    test_worker_id_send_path_unchanged()
    test_mcp_worker_send_by_session_id_posts_api_send()
    test_mcp_worker_send_by_session_id_denied_unmanaged()
    test_mcp_worker_send_by_worker_id_unchanged()
    test_mcp_worker_send_missing_params()
    test_mcp_worker_send_force_by_session_no_worker_enqueues()
    test_mcp_worker_send_force_by_session_with_worker_restarts()
    test_mcp_worker_send_force_by_worker_id_unchanged()
    test_mcp_worker_kill_by_session_id_no_worker_noop_ok()
    test_mcp_worker_kill_by_session_id_with_worker()
    test_mcp_worker_kill_by_session_id_denied_unmanaged()
    test_mcp_worker_kill_by_worker_id_unchanged()
    test_api_send_by_session_id_delegates()
    test_api_send_workerid_resolves_session()
    test_api_send_error_translation()
    test_api_send_requires_params()
    print("\n=== ALL ADDRESSING COMPAT TESTS PASSED ===")
