"""MCP workspace tools + session_update workspace semantics.

These tests route ``packages.mcp.server._api`` into the *real* Pan HTTP
handlers (no network), so the MCP layer is exercised end-to-end against the
same backend/domain rules the Dashboard uses. That is the point: MCP must not
be able to reach a state the UI could not, and the managed-tree/workspace hard
constraint (a management tree lives inside exactly one Workspace) must hold.

Coverage:
    - workspace_list returns id + name (+ stable fields, no member id list)
    - workspace_create validation / duplicate-name parity with the UI
    - workspace_delete detaches members, descendants follow, no dangling id
    - session_update workspace move propagates down the manager chain
    - illegal cross-workspace change on a managed session is refused
    - detach-then-move (the Dashboard flow) then works
    - ungrouped ([]), unknown workspace, and multi-workspace rejection
    - managed-isolation permission boundary
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.mcp.server as mcp_server  # noqa: E402
import packages.web.server as server  # noqa: E402
from packages.core import session as sess  # noqa: E402
from packages.core import workspace as ws  # noqa: E402


def _bridge():
    """Route mcp_server._api into the real Pan async handlers."""

    def _api(method, path, body=None, timeout=30.0):
        body = body or {}
        if method == "GET" and path == "/api/workspaces":
            return asyncio.run(server.api_list_workspaces())
        if method == "POST" and path == "/api/workspaces":
            return asyncio.run(server.api_create_workspace(body))
        if method == "DELETE" and path.startswith("/api/workspaces/"):
            return asyncio.run(server.api_delete_workspace(path[len("/api/workspaces/"):]))
        if method == "PUT" and path.startswith("/api/sessions/") and path.endswith("/workspaces"):
            sid = path[len("/api/sessions/"):-len("/workspaces")]
            return asyncio.run(server.api_set_session_workspaces(sid, body))
        if method == "PATCH" and path.startswith("/api/sessions/"):
            return asyncio.run(server.api_update_session(path[len("/api/sessions/"):], body))
        if method == "GET" and path.startswith("/api/sessions/"):
            sid = path[len("/api/sessions/"):].split("?")[0]
            s = sess.get(sid, load_history=False)
            if s is None:
                return {"error": f"Session {sid} not found"}
            return {"id": s.id, "managed": list(s.managed), "managedBy": s.managed_by,
                    "panAccess": {"restrictToManaged": s.restrict_to_managed,
                                  "canClaimUnmanaged": s.can_claim_unmanaged,
                                  "autoClaimCreated": s.auto_claim_created}}
        return {"ok": True}

    return _api


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path / "workspaces")
    ws.clear_cache()
    monkeypatch.setattr(mcp_server, "_api", _bridge())
    monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
    yield
    ws.clear_cache()
    sess._cache.clear()


def _mk(sid, **kwargs):
    s = sess.Session(id=sid, name=sid, **kwargs)
    sess._cache[sid] = s
    return s


def _wid(result):
    return result["workspace"]["id"]


# ── workspace_list ──────────────────────────────────────────────────────────

def test_workspace_list_returns_id_and_name_without_member_ids(env):
    assert mcp_server.workspace_list() == {"ok": True, "workspaces": []}

    created = mcp_server.workspace_create("Inbox")
    assert created["ok"] is True
    wid = _wid(created)

    listing = mcp_server.workspace_list()
    assert listing["ok"] is True
    assert len(listing["workspaces"]) == 1
    entry = listing["workspaces"][0]
    assert entry["id"] == wid
    assert entry["name"] == "Inbox"
    assert entry["sessionCount"] == 0
    # Stable fields are present, but the member id list is deliberately dropped.
    assert "sessionIds" not in entry
    assert set(entry) == {"id", "name", "order", "dirs", "createdAt", "updatedAt", "sessionCount"}


# ── workspace_create validation (UI parity) ─────────────────────────────────

def test_workspace_create_validation_and_duplicate(env):
    assert mcp_server.workspace_create("")["error"]["code"] == "invalid_name"
    assert mcp_server.workspace_create("   ")["error"]["code"] == "invalid_name"
    assert mcp_server.workspace_create("x" * 129)["error"]["code"] == "invalid_name"
    assert mcp_server.workspace_create("Team")["ok"] is True
    assert mcp_server.workspace_create("Team")["error"]["code"] == "name_taken"


# ── workspace_delete keeps the tree consistent ──────────────────────────────

def test_workspace_delete_detaches_members_and_descendants(env):
    parent = _mk("ses_p")
    child = _mk("ses_c", managed_by=parent.id)
    parent.managed = [child.id]
    wid = _wid(mcp_server.workspace_create("Team"))
    assert mcp_server.session_update(parent.id, workspace_ids=[wid])["ok"] is True
    assert sess.effective_workspace_ids(child) == [wid]

    deleted = mcp_server.workspace_delete(wid)
    assert deleted == {"ok": True, "workspaceId": wid}
    assert ws.get(wid) is None
    # Root loses the id; the managed descendant follows it back to ungrouped.
    assert parent.workspace_ids == []
    assert sess.effective_workspace_ids(child) == []
    # No dangling workspace id survives.
    assert mcp_server.workspace_list()["workspaces"] == []

    # Deleting the same (now missing) workspace is a clean not-found.
    assert mcp_server.workspace_delete(wid)["error"]["code"] == "workspace_not_found"


# ── session_update propagation / rejection ──────────────────────────────────

def test_session_update_moves_root_and_propagates_to_descendants(env):
    parent = _mk("ses_root")
    child = _mk("ses_child", managed_by=parent.id)
    grandchild = _mk("ses_grand", managed_by=child.id)
    parent.managed = [child.id]
    child.managed = [grandchild.id]

    w1 = _wid(mcp_server.workspace_create("One"))
    w2 = _wid(mcp_server.workspace_create("Two"))
    assert mcp_server.session_update(parent.id, workspace_ids=[w1])["ok"] is True

    moved = mcp_server.session_update(parent.id, workspace_ids=[w2])
    assert moved["ok"] is True
    assert moved["session"]["workspaceIds"] == [w2]
    # Inheritance: the whole management tree follows the root.
    assert sess.effective_workspace_ids(parent) == [w2]
    assert sess.effective_workspace_ids(child) == [w2]
    assert sess.effective_workspace_ids(grandchild) == [w2]


def test_session_update_rejects_illegal_cross_workspace_on_managed(env):
    parent = _mk("ses_mgr")
    child = _mk("ses_managed", managed_by=parent.id)
    parent.managed = [child.id]
    w1 = _wid(mcp_server.workspace_create("One"))
    w2 = _wid(mcp_server.workspace_create("Two"))
    assert mcp_server.session_update(parent.id, workspace_ids=[w1])["ok"] is True

    # A managed child must not be pulled into a different workspace — that would
    # split the management tree across workspaces.
    denied = mcp_server.session_update(child.id, workspace_ids=[w2])
    assert denied["ok"] is False
    assert denied["error"]["code"] == "managed_session"
    assert child.managed_by == parent.id
    assert sess.effective_workspace_ids(child) == [w1]  # unchanged

    # Even the inherited workspace cannot be set directly on the managed child
    # (the Dashboard requires detaching first), so the value stays authoritative
    # only through the root.
    still_denied = mcp_server.session_update(child.id, workspace_ids=[w1])
    assert still_denied["error"]["code"] == "managed_session"


def test_detach_then_move_matches_dashboard_flow(env):
    parent = _mk("ses_mgr2")
    child = _mk("ses_managed2", managed_by=parent.id)
    grandchild = _mk("ses_grand2", managed_by=child.id)
    parent.managed = [child.id]
    child.managed = [grandchild.id]
    w1 = _wid(mcp_server.workspace_create("One"))
    w2 = _wid(mcp_server.workspace_create("Two"))
    assert mcp_server.session_update(parent.id, workspace_ids=[w1])["ok"] is True

    # Dashboard flow: detach (unclaim) the subtree, then move the new root.
    assert sess.unclaim(parent.id, child.id) is None
    assert child.managed_by is None
    assert child.workspace_ids == [w1]  # snapshot of the inherited membership

    moved = mcp_server.session_update(child.id, workspace_ids=[w2])
    assert moved["ok"] is True
    assert sess.effective_workspace_ids(child) == [w2]
    assert sess.effective_workspace_ids(grandchild) == [w2]  # subtree follows


# ── ungrouped / unknown / multi-workspace ───────────────────────────────────

def test_session_update_ungrouped_unknown_and_multi(env):
    a = _mk("ses_a")
    w1 = _wid(mcp_server.workspace_create("One"))
    w2 = _wid(mcp_server.workspace_create("Two"))
    assert mcp_server.session_update(a.id, workspace_ids=[w1])["ok"] is True

    # [] → ungrouped (there is no "default" workspace).
    ungrouped = mcp_server.session_update(a.id, workspace_ids=[])
    assert ungrouped["ok"] is True
    assert sess.effective_workspace_ids(a) == []

    # Unknown target → server-authoritative not-found.
    assert mcp_server.session_update(a.id, workspace_ids=["ws_missing"])["error"]["code"] == "workspace_not_found"

    # At most one workspace, and ids must be unique.
    assert mcp_server.session_update(a.id, workspace_ids=[w1, w2])["error"]["code"] == "invalid_workspace_ids"
    assert mcp_server.session_update(a.id, workspace_ids=[w1, w1])["error"]["code"] == "invalid_workspace_ids"
    assert mcp_server.session_update(a.id, workspace_ids=[""])["error"]["code"] == "invalid_workspace_ids"


# ── permission isolation ────────────────────────────────────────────────────

def test_session_update_workspace_respects_managed_isolation(env, monkeypatch):
    monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma")
    _mk("ses_ma", restrict_to_managed=True, can_claim_unmanaged=True,
        managed=["ses_ours"])
    _mk("ses_ours")
    _mk("ses_theirs")
    w = _wid(mcp_server.workspace_create("Team"))

    denied = mcp_server.session_update("ses_theirs", workspace_ids=[w])
    assert denied["ok"] is False
    assert denied["error"]["code"] == "permission_denied"
    assert sess.get("ses_theirs").workspace_ids == []

    allowed = mcp_server.session_update("ses_ours", workspace_ids=[w])
    assert allowed["ok"] is True
    assert sess.effective_workspace_ids(sess.get("ses_ours")) == [w]
