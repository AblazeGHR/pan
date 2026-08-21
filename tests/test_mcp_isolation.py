"""Tests for MCP isolation (立项 4.1 能力字段 + 4.2 managed).

- Session capability fields roundtrip (default False)
- session.claim() / session.release() bidirectional managed relationship
- HTTP POST /api/claim (can_claim_unmanaged gate, already-managed refusal)
- MCP tools: restrict_to_managed caller limited to its managed list;
  can_claim_unmanaged auto-claims on first touch; unrestricted caller passes
- capability propagation from session_template into session creation
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


# ── Session capability fields ──

def test_session_capabilities_default():
    s = _sess.Session(id="ses_x", name="x")
    assert s.restrict_to_managed is False
    assert s.can_claim_unmanaged is False
    assert s.auto_claim_created is False


def test_session_capabilities_roundtrip():
    s = _sess.Session(id="ses_ma", name="ma",
                      pan_access={"restrict_to_managed": True,
                                  "can_claim_unmanaged": True,
                                  "auto_claim_created": True})
    d = s.to_dict()
    assert d["pan_access"]["restrict_to_managed"] is True
    assert d["pan_access"]["can_claim_unmanaged"] is True
    assert d["pan_access"]["auto_claim_created"] is True
    s2 = _sess.Session._from_data(dict(d))
    assert s2.restrict_to_managed is True
    assert s2.can_claim_unmanaged is True
    assert s2.auto_claim_created is True


def test_session_capabilities_legacy_data_absent():
    """Old JSON without capability fields → default False."""
    data = {"id": "ses_x", "name": "x"}
    s = _sess.Session._from_data(data)
    assert s.restrict_to_managed is False
    assert s.can_claim_unmanaged is False
    assert s.auto_claim_created is False


# ── session.claim() / release() ──

def test_claim_sets_bidirectional():
    _cleanup()
    mgr = _setup_session("ses_mgr")
    child = _setup_session("ses_child")
    err = _sess.claim("ses_mgr", "ses_child")
    assert err is None
    assert mgr.managed == ["ses_child"]
    assert child.managed_by == "ses_mgr"
    _cleanup()


def test_claim_idempotent():
    _cleanup()
    mgr = _setup_session("ses_mgr")
    child = _setup_session("ses_child", managed_by="ses_mgr")
    mgr.managed = ["ses_child"]
    err = _sess.claim("ses_mgr", "ses_child")
    assert err is None
    assert mgr.managed == ["ses_child"]
    assert child.managed_by == "ses_mgr"
    _cleanup()


def test_claim_refuses_foreign_manager():
    _cleanup()
    mgr = _setup_session("ses_mgr")
    child = _setup_session("ses_child", managed_by="ses_other")
    err = _sess.claim("ses_mgr", "ses_child")
    assert err is not None and "managed by ses_other" in err
    assert mgr.managed == []
    assert child.managed_by == "ses_other"
    _cleanup()


def test_claim_missing_sessions():
    _cleanup()
    _setup_session("ses_mgr")
    assert "not found" in _sess.claim("ses_mgr", "ses_nope")
    assert "not found" in _sess.claim("ses_nope", "ses_mgr")
    _cleanup()


def test_release_cleans_manager():
    _cleanup()
    mgr = _setup_session("ses_mgr")
    child = _setup_session("ses_child", managed_by="ses_mgr")
    mgr.managed = ["ses_child"]
    err = _sess.release("ses_child")
    assert err is None
    assert mgr.managed == []
    assert child.managed_by is None
    _cleanup()


# ── HTTP /api/claim endpoint ──

def test_api_claim_requires_can_claim_unmanaged(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    _setup_session("ses_mgr")  # can_claim_unmanaged defaults False
    _setup_session("ses_child")
    r = asyncio.run(srv.api_claim({"managerId": "ses_mgr", "sessionId": "ses_child"}))
    assert r.get("ok") is False
    assert r["error"]["code"] == "cannot_claim_unmanaged"
    assert _sess.get("ses_child").managed_by is None
    _cleanup()


def test_api_claim_success(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    mgr = _setup_session("ses_mgr", can_claim_unmanaged=True)
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
    _setup_session("ses_mgr", can_claim_unmanaged=True)
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


# ── capability propagation: session_template → session params ──

def test_build_session_params_propagates_capabilities(monkeypatch):
    import packages.web.server as srv
    from packages.core.character import CharacterManager
    cm = CharacterManager()
    cm.load_manifest(["packages/mcp/manifest.json"])
    monkeypatch.setattr(srv, "_character_manager", cm)
    params = srv._build_session_params({"name": "ma", "sessionTemplate": "meta-agent"})
    assert params["pan_access"]["restrict_to_managed"] is True
    assert params["pan_access"]["can_claim_unmanaged"] is True
    assert params["pan_access"]["auto_claim_created"] is True


def test_build_session_params_capabilities_default(monkeypatch):
    import packages.web.server as srv
    monkeypatch.setattr(srv, "_character_manager", None)
    params = srv._build_session_params({"name": "plain"})
    assert params["pan_access"]["restrict_to_managed"] is False
    assert params["pan_access"]["can_claim_unmanaged"] is False
    assert params["pan_access"]["auto_claim_created"] is False


# ── MCP isolation helpers ──

class _FakeAPI:
    """Routing fake for packages.mcp.server._api.

    `sessions` maps session id → API dict (id/restrictToManaged/canClaimUnmanaged/
    autoClaimCreated/managed/managedBy). Claims mutate the dicts in place.
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
    return {sid: {"id": sid,
                  "panAccess": {"restrictToManaged": True, "canClaimUnmanaged": True,
                                "autoClaimCreated": True},
                  # deprecated flat aliases (server still emits these)
                  "restrictToManaged": True, "canClaimUnmanaged": True,
                  "autoClaimCreated": True, "managed": list(managed or [])}}


def _default_session(sid="ses_default", managed=None):
    return {sid: {"id": sid,
                  "panAccess": {"restrictToManaged": False, "canClaimUnmanaged": False,
                                "autoClaimCreated": False},
                  "restrictToManaged": False, "canClaimUnmanaged": False,
                  "autoClaimCreated": False, "managed": list(managed or [])}}


def test_mcp_meta_agent_can_read_managed(monkeypatch):
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=["ses_child"]),
        "ses_child": {"id": "ses_child", "managedBy": "ses_ma"},
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
        "ses_other": {"id": "ses_other", "managedBy": None},
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
        "ses_other": {"id": "ses_other", "managedBy": "ses_rival"},
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
        "ses_new": {"id": "ses_new", "managedBy": None},
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
        "ses_existing": {"id": "ses_existing", "managedBy": None},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    r = mcp_server.worker_spawn(session_id="ses_existing")
    assert r.get("ok") is True, r
    assert any(c[1] == "/api/claim" for c in fake.calls)
    assert fake.sessions["ses_existing"]["managedBy"] == "ses_ma"
    _cleanup()


def test_mcp_unrestricted_session_reads_any(monkeypatch):
    _cleanup()
    fake = _FakeAPI({
        **_default_session(managed=[]),
        "ses_any": {"id": "ses_any", "managedBy": None},
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
        "ses_any": {"id": "ses_any", "managedBy": None},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    r = mcp_server.session_get("ses_any")
    assert "error" not in r or r.get("ok") is True
    _cleanup()


def test_mcp_report_subscribe_autoclaims(monkeypatch):
    _cleanup()
    fake = _FakeAPI({
        **_ma_session(managed=[]),
        "ses_child": {"id": "ses_child", "managedBy": None},
    })
    monkeypatch.setattr(mcp_server, "_api", fake)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    # _api returns {"ok": True} for the actual subscribe call path
    r = mcp_server.report_subscribe("ses_child")
    assert any(c[1] == "/api/claim" for c in fake.calls)
    assert fake.sessions["ses_child"]["managedBy"] == "ses_ma"
    _cleanup()


if __name__ == "__main__":
    test_session_capabilities_default()
    test_session_capabilities_roundtrip()
    test_session_capabilities_legacy_data_absent()
    test_claim_sets_bidirectional()
    test_claim_idempotent()
    test_claim_refuses_foreign_manager()
    test_claim_missing_sessions()
    test_release_cleans_manager()
    test_api_claim_requires_can_claim_unmanaged()
    test_api_claim_success()
    test_api_claim_refuses_foreign()
    test_api_claim_missing_params()
    test_build_session_params_propagates_capabilities()
    test_build_session_params_capabilities_default()
    test_mcp_meta_agent_can_read_managed()
    test_mcp_meta_agent_denied_unmanaged_read()
    test_mcp_meta_agent_denied_foreign_managed()
    test_mcp_meta_agent_assign_autoclaims()
    test_mcp_meta_agent_spawn_autoclaims()
    test_mcp_unrestricted_session_reads_any()
    test_mcp_no_identity_unrestricted()
    test_mcp_report_subscribe_autoclaims()
    print("\n=== ALL MCP ISOLATION TESTS PASSED ===")
