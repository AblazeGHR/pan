"""Tests for MCP isolation (立项 4.1 role + 4.2 managed).

- Session.role field roundtrip (default "default")
- session.claim() / session.release() bidirectional managed relationship
- HTTP POST /api/claim (meta-agent gate, already-managed refusal)
- MCP tools: meta-agent restricted to its managed list; auto-claim on first
  touch (worker_spawn/worker_assign/...); default-role unrestricted
- role propagation from character into session creation
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.mcp.server as mcp_server
from packages.core import worker, session as _sess


def _cleanup():
    worker.workers.clear()
    worker._result_waiters.clear()
    worker._task_status.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid="ses_test", **kwargs):
    s = _sess.Session(id=sid, name="test", **kwargs)
    _sess._cache[sid] = s
    return s


def _noop_save(s):
    pass


# ── Session.role field ──

def test_session_role_default():
    s = _sess.Session(id="ses_x", name="x")
    assert s.role == "default"


def test_session_role_roundtrip():
    s = _sess.Session(id="ses_ma", name="ma", role="meta-agent")
    d = s.to_dict()
    assert d["role"] == "meta-agent"
    s2 = _sess.Session._from_data(dict(d))
    assert s2.role == "meta-agent"


def test_session_role_legacy_data_absent():
    """Old JSON without the role field → default."""
    data = {"id": "ses_x", "name": "x"}
    s = _sess.Session._from_data(data)
    assert s.role == "default"


# ── session.claim() / release() ──

def test_claim_sets_bidirectional():
    _cleanup()
    mgr = _setup_session("ses_mgr", role="meta-agent")
    child = _setup_session("ses_child")
    err = _sess.claim("ses_mgr", "ses_child")
    assert err is None
    assert mgr.managed == ["ses_child"]
    assert child.managed_by == "ses_mgr"
    _cleanup()


def test_claim_idempotent():
    _cleanup()
    mgr = _setup_session("ses_mgr", role="meta-agent")
    child = _setup_session("ses_child", managed_by="ses_mgr")
    mgr.managed = ["ses_child"]
    err = _sess.claim("ses_mgr", "ses_child")
    assert err is None
    assert mgr.managed == ["ses_child"]
    assert child.managed_by == "ses_mgr"
    _cleanup()


def test_claim_refuses_foreign_manager():
    _cleanup()
    mgr = _setup_session("ses_mgr", role="meta-agent")
    child = _setup_session("ses_child", managed_by="ses_other")
    err = _sess.claim("ses_mgr", "ses_child")
    assert err is not None and "managed by ses_other" in err
    assert mgr.managed == []
    assert child.managed_by == "ses_other"
    _cleanup()


def test_claim_missing_sessions():
    _cleanup()
    _setup_session("ses_mgr", role="meta-agent")
    assert "not found" in _sess.claim("ses_mgr", "ses_nope")
    assert "not found" in _sess.claim("ses_nope", "ses_mgr")
    _cleanup()


def test_release_cleans_manager():
    _cleanup()
    mgr = _setup_session("ses_mgr", role="meta-agent")
    child = _setup_session("ses_child", managed_by="ses_mgr")
    mgr.managed = ["ses_child"]
    err = _sess.release("ses_child")
    assert err is None
    assert mgr.managed == []
    assert child.managed_by is None
    _cleanup()


# ── HTTP /api/claim endpoint ──

def test_api_claim_requires_meta_agent(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    _setup_session("ses_mgr", role="default")
    _setup_session("ses_child")
    r = asyncio.run(srv.api_claim({"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert r.get("ok") is False
    assert r["error"]["code"] == "not_meta_agent"
    assert _sess.get("ses_child").managed_by is None
    _cleanup()


def test_api_claim_success(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    mgr = _setup_session("ses_mgr", role="meta-agent")
    _setup_session("ses_child")
    r = asyncio.run(srv.api_claim({"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert r.get("ok") is True
    assert r["managed"] == ["ses_child"]
    assert _sess.get("ses_child").managed_by == "ses_mgr"
    _cleanup()


def test_api_claim_refuses_foreign(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    _setup_session("ses_mgr", role="meta-agent")
    _setup_session("ses_child", managed_by="ses_other")
    r = asyncio.run(srv.api_claim({"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert r.get("ok") is False
    assert r["error"]["code"] == "claim_failed"
    _cleanup()


def test_api_claim_missing_params(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    r = asyncio.run(srv.api_claim({}))
    assert r.get("ok") is False
    assert r["error"]["code"] == "missing_params"
    _cleanup()


# ── role propagation: character → session params ──

def test_build_session_params_propagates_role(monkeypatch):
    import packages.web.server as srv
    from packages.core.character import CharacterManager
    cm = CharacterManager()
    cm.load_manifest(["packages/mcp/manifest.json"])
    monkeypatch.setattr(srv, "_character_manager", cm)
    params = srv._build_session_params({"name": "ma", "characterId": "meta-agent"})
    assert params.get("role") == "meta-agent"


def test_build_session_params_no_role_default(monkeypatch):
    import packages.web.server as srv
    monkeypatch.setattr(srv, "_character_manager", None)
    params = srv._build_session_params({"name": "plain"})
    assert params.get("role") is None  # create() defaults to "default"


# ── MCP isolation helpers ──

class _FakeAPI:
    """Routing fake for packages.mcp.server._api.

    `sessions` maps session id → API dict (id/role/managed/managedBy).
    Claims mutate the dicts in place; returns a queued result for task tools.
    """

    def __init__(self, sessions, allow_claim=True):
        self.sessions = sessions
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
                return {"ok": False, "error": {
                    "message": f"Session {body['sessionId']} is managed by {tgt['managedBy']}"}}
            if not self.allow_claim:
                return {"ok": False, "error": {"message": "claim refused"}}
            tgt["managedBy"] = body["managerId"]
            if body["sessionId"] not in mgr["managed"]:
                mgr["managed"].append(body["sessionId"])
            return {"ok": True, "managerId": body["managerId"],
                    "sessionId": body["sessionId"], "managed": list(mgr["managed"])}
        if method == "GET" and path == "/api/list":
            return {"workers": []}
        if path in ("/api/assign", "/api/task", "/api/spawn"):
            return {"ok": True, "status": "queued", "sessionId": body.get("sessionId")}
        if method == "DELETE":
            return {"ok": True}
        return {"ok": True}


def _ma_session(managed=None, sid="ses_ma"):
    return {sid: {"id": sid, "role": "meta-agent", "managed": list(managed or [])}}


def _default_session(sid="ses_default", managed=None):
    return {sid: {"id": sid, "role": "default", "managed": list(managed or [])}}


def test_mcp_meta_agent_can_read_managed(monkeypatch):
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "role": "default", "managedBy": "ses_ma"},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.session_get("ses_child")
    assert r.get("ok") is True or "error" not in r, r
    assert fake.calls[0][0] == "GET" and "/api/sessions/ses_ma" in fake.calls[0][1]
    _cleanup()


def test_mcp_meta_agent_denied_unmanaged_read(monkeypatch):
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=["ses_ok"]),
        "ses_other": {"id": "ses_other", "role": "default", "managedBy": None},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.session_get("ses_other")
    assert r.get("ok") is False
    assert r["error"]["code"] == "permission_denied"
    # no further call beyond identity lookups
    assert all(c[1] != "/api/sessions/ses_other" or c[0] == "GET" for c in fake.calls)
    _cleanup()


def test_mcp_meta_agent_denied_foreign_managed(monkeypatch):
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=[]),
        "ses_other": {"id": "ses_other", "role": "default", "managedBy": "ses_rival"},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_assign("ses_other", "task")
    assert r.get("ok") is False
    assert r["error"]["code"] == "permission_denied"
    _cleanup()


def test_mcp_meta_agent_assign_autoclaims(monkeypatch):
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=[]),
        "ses_new": {"id": "ses_new", "role": "default", "managedBy": None},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_assign("ses_new", "task")
    assert r.get("ok") is True, r
    assert any(c[1] == "/api/claim" for c in fake.calls)
    assert fake.sessions["ses_new"]["managedBy"] == "ses_ma"
    assert fake.sessions["ses_ma"]["managed"] == ["ses_new"]
    _cleanup()


def test_mcp_meta_agent_spawn_autoclaims(monkeypatch):
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=[]),
        "ses_existing": {"id": "ses_existing", "role": "default", "managedBy": None},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_spawn(session_id="ses_existing")
    assert r.get("ok") is True, r
    assert any(c[1] == "/api/claim" for c in fake.calls)
    assert fake.sessions["ses_existing"]["managedBy"] == "ses_ma"
    _cleanup()


def test_mcp_default_role_unrestricted(monkeypatch):
    _cleanup()
    fake = _FakeAPI({
        **_default_session(managed=[]),
        "ses_any": {"id": "ses_any", "role": "default", "managedBy": None},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_default")
    r = mcp_server.session_get("ses_any")
    assert "error" not in r or r.get("ok") is True
    _cleanup()


def test_mcp_no_identity_unrestricted(monkeypatch):
    _cleanup()
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    fake = _FakeAPI({
        "ses_any": {"id": "ses_any", "role": "default", "managedBy": None},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_get("ses_any")
    assert "error" not in r or r.get("ok") is True
    _cleanup()


def test_mcp_report_subscribe_autoclaims(monkeypatch):
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=[]),
        "ses_child": {"id": "ses_child", "role": "default", "managedBy": None},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    # _api returns {"ok": True} for the actual subscribe call path
    r = mcp_server.report_subscribe("ses_child")
    assert any(c[1] == "/api/claim" for c in fake.calls)
    assert fake.sessions["ses_child"]["managedBy"] == "ses_ma"
    _cleanup()


if __name__ == "__main__":
    test_session_role_default()
    test_session_role_roundtrip()
    test_session_role_legacy_data_absent()
    test_claim_sets_bidirectional()
    test_claim_idempotent()
    test_claim_refuses_foreign_manager()
    test_claim_missing_sessions()
    test_release_cleans_manager()
    test_api_claim_requires_meta_agent()
    test_api_claim_success()
    test_api_claim_refuses_foreign()
    test_api_claim_missing_params()
    test_build_session_params_propagates_role()
    test_build_session_params_no_role_default()
    test_mcp_meta_agent_can_read_managed()
    test_mcp_meta_agent_denied_unmanaged_read()
    test_mcp_meta_agent_denied_foreign_managed()
    test_mcp_meta_agent_assign_autoclaims()
    test_mcp_meta_agent_spawn_autoclaims()
    test_mcp_default_role_unrestricted()
    test_mcp_no_identity_unrestricted()
    test_mcp_report_subscribe_autoclaims()
    print("\n=== ALL MCP ISOLATION TESTS PASSED ===")
