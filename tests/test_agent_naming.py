"""Tests for agent-naming layer (agent-naming 分支：Agent=Session 一等工具).

- agent_* 一等 MCP 工具可用且与 worker_* 等价（同 session_id 调用产生同一
  API 调用序列——worker_* 内部委托同一实现，不复制逻辑）
- agent_send / agent_send_force 无活 worker 不报错：入队路径（POST /api/send，
  阶段 6 pendingSpawn 语义）
- agent_kill 无活 worker → ok（killed=false）无害 no-op
- M18 隔离不倒退：agent_* 同样过 _check_access
- worker_id 遗留寻址路径行为保留（详测见 test_addressing_compat.py）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.mcp.server as mcp_server


# ── shared fake ──

class _FakeAPI:
    """Routing fake for packages.mcp.server._api（仿 test_addressing_compat）。"""

    def __init__(self, sessions, workers=None, allow_claim=True):
        self.sessions = sessions
        self.workers = workers if workers is not None else []
        self.allow_claim = allow_claim
        self.calls = []

    def __call__(self, method, path, body=None, timeout=30.0):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/api/sessions?"):
            return {"sessions": list(self.sessions.values())}
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


def _env(monkeypatch, sid="ses_ma", title="meta"):
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", sid)
    monkeypatch.setenv("PAN_AGENT_SESSION_TITLE", title)


def _clear_env(monkeypatch):
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    monkeypatch.delenv("PAN_AGENT_SESSION_TITLE", raising=False)


def _strip_calls(fake):
    """只留 method+path（等价性比较用；body 由各断言单独核）。"""
    return [(m, p) for m, p, _ in fake.calls]


# ── 工具存在性 ──

def test_agent_tools_exist():
    for name in ("agent_spawn", "agent_task", "agent_assign", "agent_send",
                 "agent_send_force", "agent_kill", "agent_list"):
        assert callable(getattr(mcp_server, name)), name


# ── agent_send ──

def test_agent_send_posts_api_send_with_prefix(monkeypatch):
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    _env(monkeypatch)
    r = mcp_server.agent_send(session_id="ses_child", text="hi")
    assert r.get("ok") is True, r
    assert any(c[0] == "POST" and c[1] == "/api/send"
               and c[2]["sessionId"] == "ses_child"
               and c[2]["source"] == "agent"
               and c[2]["sourceSessionId"] == "ses_ma"
               and c[2]["text"] == "////by agent : ses_ma | meta\nhi"
               for c in fake.calls), fake.calls


def test_agent_send_denied_unmanaged(monkeypatch):
    fake = _FakeAPI({
        **_ma_session(managed=[]),
        "ses_other": {"id": "ses_other", "managedBy": "ses_rival"},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    _env(monkeypatch)
    r = mcp_server.agent_send(session_id="ses_other", text="hi")
    assert r.get("ok") is False
    assert r["error"]["code"] == "permission_denied"
    assert all(c[1] != "/api/send" for c in fake.calls)


def test_agent_send_equals_worker_send(monkeypatch):
    """等价性：agent_send 与 worker_send(session_id=...) 产生同一调用序列。"""
    _env(monkeypatch)
    calls_list = []
    for fn in (mcp_server.agent_send, mcp_server.worker_send):
        fake = _FakeAPI({
            **_ma_session(managed=["ses_child"]),
            "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
        })
        monkeypatch.setattr(mcp_server, "_api", fake)
        fn(session_id="ses_child", text="same")
        calls_list.append(_strip_calls(fake))
    assert calls_list[0] == calls_list[1] and "/api/send" in calls_list[0][-1]


# ── agent_send_force ──

def test_agent_send_force_no_worker_starts_then_sends_by_session(monkeypatch):
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    }, workers=[])
    monkeypatch.setattr(mcp_server, "_api", fake)
    _env(monkeypatch)
    r = mcp_server.agent_send_force(session_id="ses_child", text="urgent")
    assert r.get("ok") is True, r
    assert any(c[1] == "/api/sessions/ses_child/worker/restart" for c in fake.calls)
    assert any(c[1] == "/api/task" and c[2]["sessionId"] == "ses_child"
               and c[2]["source"] == "agent"
               and c[2]["sourceSessionId"] == "ses_ma"
               for c in fake.calls)


def test_agent_send_force_with_worker_restarts(monkeypatch):
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    }, workers=[{"workerId": "worker-9", "sessionId": "ses_child"}])
    monkeypatch.setattr(mcp_server, "_api", fake)
    _env(monkeypatch)
    r = mcp_server.agent_send_force(session_id="ses_child", text="urgent")
    assert r.get("ok") is True, r
    assert any(c[1] == "/api/sessions/ses_child/worker/restart" for c in fake.calls)
    assert any(c[1] == "/api/task" and c[2]["sessionId"] == "ses_child"
               and c[2]["source"] == "agent"
               and c[2]["sourceSessionId"] == "ses_ma"
               for c in fake.calls)


def test_agent_send_force_equals_worker_send_force(monkeypatch):
    _env(monkeypatch)
    calls_list = []
    for fn in (mcp_server.agent_send_force, mcp_server.worker_send_force):
        fake = _FakeAPI({
            **_ma_session(managed=["ses_child"]),
            "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
        }, workers=[{"workerId": "worker-x", "sessionId": "ses_child"}])
        monkeypatch.setattr(mcp_server, "_api", fake)
        fn(session_id="ses_child", text="go")
        calls_list.append(_strip_calls(fake))
    assert calls_list[0] == calls_list[1]


# ── agent_kill ──

def test_agent_kill_no_worker_noop_ok(monkeypatch):
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    }, workers=[])
    monkeypatch.setattr(mcp_server, "_api", fake)
    _env(monkeypatch)
    r = mcp_server.agent_kill(session_id="ses_child")
    assert r.get("ok") is True and r.get("killed") is False, r
    assert all("/api/kill/" not in c[1] for c in fake.calls)


def test_agent_kill_with_worker(monkeypatch):
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    }, workers=[{"workerId": "worker-7", "sessionId": "ses_child"}])
    monkeypatch.setattr(mcp_server, "_api", fake)
    _env(monkeypatch)
    mcp_server.agent_kill(session_id="ses_child")
    assert any(c[1] == "/api/sessions/ses_child/worker/kill" for c in fake.calls)


def test_agent_kill_denied_unmanaged(monkeypatch):
    fake = _FakeAPI({
        **_ma_session(managed=[]),
        "ses_other": {"id": "ses_other", "managedBy": "ses_rival"},
    }, workers=[{"workerId": "worker-2", "sessionId": "ses_other"}])
    monkeypatch.setattr(mcp_server, "_api", fake)
    _env(monkeypatch)
    r = mcp_server.agent_kill(session_id="ses_other")
    assert r.get("ok") is False
    assert r["error"]["code"] == "permission_denied"
    assert all("/api/kill/" not in c[1] for c in fake.calls)


def test_agent_kill_equals_worker_kill(monkeypatch):
    _env(monkeypatch)
    calls_list = []
    for fn in (mcp_server.agent_kill, mcp_server.worker_kill):
        fake = _FakeAPI({
            **_ma_session(managed=["ses_child"]),
            "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
        }, workers=[])
        monkeypatch.setattr(mcp_server, "_api", fake)
        fn(session_id="ses_child")
        calls_list.append(_strip_calls(fake))
    assert calls_list[0] == calls_list[1]


# ── agent_assign ──

def test_agent_assign_posts_api_assign(monkeypatch):
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    _env(monkeypatch)
    r = mcp_server.agent_assign(session_id="ses_child", text="task",
                                task_id="tid-1")
    assert r.get("ok") is True, r
    assert any(c[0] == "POST" and c[1] == "/api/assign"
               and c[2]["sessionId"] == "ses_child" and c[2]["text"] == "task"
               and c[2]["source"] == "agent"
               and c[2]["sourceSessionId"] == "ses_ma"
               and c[2]["taskId"] == "tid-1" for c in fake.calls), fake.calls


def test_agent_assign_equals_worker_assign(monkeypatch):
    _env(monkeypatch)
    calls_list = []
    for fn in (mcp_server.agent_assign, mcp_server.worker_assign):
        fake = _FakeAPI({
            **_ma_session(managed=["ses_child"]),
            "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
        })
        monkeypatch.setattr(mcp_server, "_api", fake)
        fn(session_id="ses_child", text="t", task_id="k1")
        calls_list.append(fake.calls)
    assert calls_list[0] == calls_list[1]


# ── agent_task ──

def test_agent_task_posts_api_task(monkeypatch):
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    _env(monkeypatch)
    r = mcp_server.agent_task(session_id="ses_child", text="t", source="agent")
    assert r.get("ok") is True, r
    assert any(c[1] == "/api/task" and c[2]["sessionId"] == "ses_child"
               and c[2]["source"] == "agent"
               and c[2]["sourceSessionId"] == "ses_ma" for c in fake.calls), fake.calls


def test_agent_task_equals_worker_task(monkeypatch):
    _env(monkeypatch)
    calls_list = []
    for fn in (mcp_server.agent_task, mcp_server.worker_task):
        fake = _FakeAPI({
            **_ma_session(managed=["ses_child"]),
            "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
        })
        monkeypatch.setattr(mcp_server, "_api", fake)
        fn(session_id="ses_child", text="t")
        calls_list.append(fake.calls)
    assert calls_list[0] == calls_list[1]


# ── agent_spawn ──

def test_agent_spawn_posts_api_spawn(monkeypatch):
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    _env(monkeypatch)
    r = mcp_server.agent_spawn(session_id="ses_child", adapter="kimi")
    assert r.get("ok") is True, r
    assert any(c[1] == "/api/spawn" and c[2]["sessionId"] == "ses_child"
               and c[2]["adapter"] == "kimi" for c in fake.calls), fake.calls


# ── agent_list ──

def test_agent_list_summary_delegates_to_session_list(monkeypatch):
    fake = _FakeAPI({
        "ses_a": {"id": "ses_a", "name": "a", "workerStatus": None},
        "ses_b": {"id": "ses_b", "name": "b", "workerStatus": "idle"},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    _clear_env(monkeypatch)
    r = mcp_server.agent_list(summary=True)
    assert isinstance(r, list) and [s["id"] for s in r] == ["ses_a", "ses_b"], r
    assert any(c[0] == "GET" and c[1] == "/api/sessions?summary=1"
               for c in fake.calls), fake.calls


if __name__ == "__main__":
    test_agent_tools_exist()
    test_agent_send_posts_api_send_with_prefix()
    test_agent_send_denied_unmanaged()
    test_agent_send_equals_worker_send()
    test_agent_send_force_no_worker_enqueues()
    test_agent_send_force_with_worker_restarts()
    test_agent_send_force_equals_worker_send_force()
    test_agent_kill_no_worker_noop_ok()
    test_agent_kill_with_worker()
    test_agent_kill_denied_unmanaged()
    test_agent_kill_equals_worker_kill()
    test_agent_assign_posts_api_assign()
    test_agent_assign_equals_worker_assign()
    test_agent_task_posts_api_task()
    test_agent_task_equals_worker_task()
    test_agent_spawn_posts_api_spawn()
    test_agent_list_summary_delegates_to_session_list()
    print("\n=== ALL AGENT NAMING TESTS PASSED ===")
