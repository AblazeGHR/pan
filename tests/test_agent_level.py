"""Tests for agent level + manager chain (2026-08-27 features).

- session.agent_level(): level = 1 + manager level, walked up the managedBy
  chain; dangling managedBy stops the chain; cycle-safe; unknown id → 1
- Session serialization carries agentLevel (_session_to_api / _session_summary)
- HTTP GET /api/sessions/{id}/managers returns the manager chain
  (level 1 = topmost, live workerStatus + lastResultStatus)
- MCP manager_chain tool: forwards to the managers endpoint for the caller
  session; missing PAN_AGENT_SESSION_ID → missing_identity error
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.mcp.server as mcp_server
from packages.core import worker, session as _sess


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _setup_session(sid="ses_test", **kwargs):
    s = _sess.Session(id=sid, name=sid, **kwargs)
    _sess._cache[sid] = s
    return s


def _noop_save(s):
    pass


def _chain(ids: list[str]):
    """Build a linear chain: ids[0] is topmost, ids[-1] the deepest session."""
    for i, sid in enumerate(ids):
        _setup_session(sid, managed_by=ids[i - 1] if i > 0 else None)


# ── core: agent_level ──

def test_agent_level_no_manager():
    _cleanup()
    _setup_session("ses_lvl_a")
    assert _sess.agent_level("ses_lvl_a") == 1
    _cleanup()


def test_agent_level_chain():
    _cleanup()
    _chain(["ses_lvl_top", "ses_lvl_mid", "ses_lvl_leaf"])
    assert _sess.agent_level("ses_lvl_top") == 1
    assert _sess.agent_level("ses_lvl_mid") == 2
    assert _sess.agent_level("ses_lvl_leaf") == 3
    _cleanup()


def test_agent_level_dangling_manager_stops_chain():
    _cleanup()
    # orphan: managed_by points to a deleted (never existing) session → 1
    _setup_session("ses_lvl_orphan", managed_by="ses_lvl_ghost")
    assert _sess.agent_level("ses_lvl_orphan") == 1
    # chain breaks one hop up: leaf→mid exists, mid→ghost dangling → leaf is 2
    _setup_session("ses_lvl_mid2", managed_by="ses_lvl_ghost")
    _setup_session("ses_lvl_leaf2", managed_by="ses_lvl_mid2")
    assert _sess.agent_level("ses_lvl_mid2") == 1
    assert _sess.agent_level("ses_lvl_leaf2") == 2
    _cleanup()


def test_agent_level_cycle_safe():
    _cleanup()
    a = _setup_session("ses_lvl_cyc_a", managed_by="ses_lvl_cyc_b")
    b = _setup_session("ses_lvl_cyc_b", managed_by="ses_lvl_cyc_a")
    # each hop resolves once before the cycle is detected → terminates
    assert _sess.agent_level("ses_lvl_cyc_a") == 2
    assert _sess.agent_level("ses_lvl_cyc_b") == 2
    assert a.managed_by == "ses_lvl_cyc_b"  # data untouched
    assert b.managed_by == "ses_lvl_cyc_a"
    _cleanup()


def test_agent_level_unknown_session():
    _cleanup()
    assert _sess.agent_level("ses_lvl_never") == 1
    _cleanup()


def test_agent_level_self_reference():
    """Defensive: managed_by == self must not loop (treated as no manager)."""
    _cleanup()
    _setup_session("ses_lvl_self", managed_by="ses_lvl_self")
    assert _sess.agent_level("ses_lvl_self") == 1
    _cleanup()


# ── HTTP serialization carries agentLevel ──

def test_session_to_api_has_agent_level(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    _chain(["ses_lvl_top", "ses_lvl_mid", "ses_lvl_leaf"])
    api = srv._session_to_api(_sess.get("ses_lvl_leaf"))
    assert api["agentLevel"] == 3
    api_top = srv._session_summary(_sess.get("ses_lvl_top"))
    assert api_top["agentLevel"] == 1
    _cleanup()


# ── HTTP GET /api/sessions/{id}/managers ──

def test_api_managers_chain_levels_and_status(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    _chain(["ses_lvl_top", "ses_lvl_mid", "ses_lvl_leaf"])
    _sess.get("ses_lvl_top").last_result = {"status": "done"}
    r = asyncio.run(srv.api_session_managers("ses_lvl_leaf"))
    assert r["ok"] is True
    assert r["sessionId"] == "ses_lvl_leaf"
    assert [m["level"] for m in r["managers"]] == [1, 2]  # topmost first
    assert [m["id"] for m in r["managers"]] == ["ses_lvl_top", "ses_lvl_mid"]
    assert r["managers"][0]["name"] == "ses_lvl_top"
    assert r["managers"][0]["lastResultStatus"] == "done"
    assert r["managers"][0]["workerStatus"] is None  # no worker spawned
    _cleanup()


def test_api_managers_empty_and_errors(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    _setup_session("ses_lvl_solo")
    r = asyncio.run(srv.api_session_managers("ses_lvl_solo"))
    assert r["ok"] is True and r["managers"] == []
    r = asyncio.run(srv.api_session_managers("ses_lvl_missing"))
    assert r.get("error") == "Session not found"
    _cleanup()


def test_api_managers_dangling_stops(monkeypatch):
    import packages.web.server as srv
    _cleanup()
    monkeypatch.setattr(_sess, "save", _noop_save)
    _setup_session("ses_lvl_mid2", managed_by="ses_lvl_ghost")
    _setup_session("ses_lvl_leaf2", managed_by="ses_lvl_mid2")
    r = asyncio.run(srv.api_session_managers("ses_lvl_leaf2"))
    # ghost id excluded, chain ends at mid2
    assert [m["id"] for m in r["managers"]] == ["ses_lvl_mid2"]
    assert [m["level"] for m in r["managers"]] == [1]
    _cleanup()


# ── MCP manager_chain tool ──

def test_mcp_manager_chain_forwards(monkeypatch):
    _cleanup()
    calls = []

    def fake_api(method, path, body=None, timeout=30.0):
        calls.append((method, path))
        return {"ok": True, "sessionId": "ses_lvl_leaf",
                "managers": [{"level": 1, "id": "ses_lvl_top",
                              "name": "ses_lvl_top", "workerStatus": None,
                              "lastResultStatus": "done"}]}

    monkeypatch.setattr(mcp_server, "_api", fake_api)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_lvl_leaf")
    r = mcp_server.manager_chain()
    assert calls == [("GET", "/api/sessions/ses_lvl_leaf/managers")]
    assert r["ok"] is True
    assert r["managers"][0]["level"] == 1
    _cleanup()


def test_mcp_manager_chain_missing_identity(monkeypatch):
    _cleanup()
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    r = mcp_server.manager_chain()
    assert r.get("ok") is False
    assert r["error"]["code"] == "missing_identity"
    _cleanup()
