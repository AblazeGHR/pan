"""Backend workspace model and HTTP contract tests (no UI or live service)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as sess  # noqa: E402
from packages.core import workspace  # noqa: E402
import packages.web.server as server  # noqa: E402


def test_workspace_crud_membership_and_ungrouped(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "WORKSPACE_DIR", tmp_path / "workspaces")
    workspace.clear_cache()
    a = sess.Session(id="ses_a", name="a")
    b = sess.Session(id="ses_b", name="b")
    sess._cache.update({a.id: a, b.id: b})
    w1 = asyncio.run(server.api_create_workspace({"name": "Inbox"}))
    w2 = asyncio.run(server.api_create_workspace({"name": "Review"}))
    wid1 = w1["workspace"]["id"]
    wid2 = w2["workspace"]["id"]

    result = asyncio.run(server.api_set_session_workspaces(
        "ses_a", {"workspaceIds": [wid1, wid2]}))
    assert result["error"]["code"] == "invalid_workspace_ids"
    assert asyncio.run(server.api_set_session_workspaces(
        "ses_a", {"workspaceIds": [wid1]}))["session"]["workspaceIds"] == [wid1]
    assert [s["id"] for s in asyncio.run(
        server.api_get_workspace_sessions(wid1, summary=1))["sessions"]] == ["ses_a"]
    assert [s["id"] for s in asyncio.run(
        server.api_list_sessions(workspaceId="ungrouped"))["sessions"]] == ["ses_b"]

    # A restart-like reload reads both metadata and Session membership.
    workspace.clear_cache()
    sess._cache.clear()
    sess._all_loaded = False
    assert workspace.get(wid1).name == "Inbox"
    assert sess.get("ses_a").workspace_ids == [wid1]


def test_workspace_order_and_delete_removes_memberships(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "WORKSPACE_DIR", tmp_path / "workspaces")
    workspace.clear_cache()
    a = sess.Session(id="ses_a", name="a", workspace_ids=[])
    sess._cache[a.id] = a
    first = workspace.create("First")
    second = workspace.create("Second")
    a.workspace_ids = [first.id]
    sess.save(a)
    assert asyncio.run(server.api_workspaces_order(
        {"workspaceIds": [second.id, first.id]}))["order"] == [second.id, first.id]
    assert asyncio.run(server.api_delete_workspace(first.id))["ok"]
    assert sess.get(a.id).workspace_ids == []
    assert workspace.get(first.id) is None


def test_workspace_membership_rejects_unknown_and_restricted_actor(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "WORKSPACE_DIR", tmp_path / "workspaces")
    workspace.clear_cache()
    actor = sess.Session(id="ses_actor", name="actor")
    target = sess.Session(id="ses_target", name="target", restrict_to_managed=True)
    sess._cache.update({actor.id: actor, target.id: target})
    w = workspace.create("Private")
    denied = asyncio.run(server.api_set_session_workspaces(
        target.id, {"workspaceIds": [w.id], "actorSessionId": actor.id}))
    assert denied["error"]["code"] == "forbidden"
    unknown = asyncio.run(server.api_set_session_workspaces(
        actor.id, {"workspaceIds": ["ws_missing"]}))
    assert unknown["error"]["code"] == "workspace_not_found"


def test_workspace_membership_moves_session_and_create_rejects_multiple(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "WORKSPACE_DIR", tmp_path / "workspaces")
    workspace.clear_cache()
    one = workspace.create("One")
    two = workspace.create("Two")
    a = sess.Session(id="ses_move", name="move", workspace_ids=[one.id])
    sess._cache[a.id] = a

    moved = asyncio.run(server._set_workspace_membership(two.id, [a.id]))
    assert moved["ok"]
    assert sess.get(a.id).workspace_ids == [two.id]

    rejected = asyncio.run(server.api_create_session({
        "name": "too-many", "workspaceIds": [one.id, two.id],
    }))
    assert "at most one" in rejected["error"]


def test_session_schema_compat_without_workspace_field():
    legacy = sess.Session._from_data({"id": "ses_legacy", "name": "legacy"})
    assert legacy.workspace_ids == []
    assert sess.Session._from_data(legacy.to_dict()).workspace_ids == []


def test_managed_workspace_inheritance_claim_detach_and_list(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "WORKSPACE_DIR", tmp_path / "workspaces")
    workspace.clear_cache()
    parent = sess.Session(id="ses_parent", name="parent")
    child = sess.Session(id="ses_child", name="child", workspace_ids=["ws_stale"])
    grandchild = sess.Session(id="ses_grandchild", name="grandchild", managed_by=child.id,
                              workspace_ids=["ws_stale"])
    one = workspace.create("One")
    two = workspace.create("Two")
    parent.workspace_ids = [one.id]
    sess._cache.update({parent.id: parent, child.id: child, grandchild.id: grandchild})
    sess.save(parent)
    sess.save(child)
    sess.save(grandchild)

    assert sess.claim(parent.id, child.id) is None
    assert child.workspace_ids == []
    assert sess.effective_workspace_ids(child) == [one.id]
    assert sess.effective_workspace_ids(grandchild) == [one.id]
    assert [s["id"] for s in asyncio.run(server.api_list_sessions(workspaceId=one.id))["sessions"]] == [
        parent.id, child.id, grandchild.id,
    ]
    assert asyncio.run(server.api_set_session_workspaces(
        child.id, {"workspaceIds": [two.id]}))["error"]["code"] == "managed_session"

    assert sess.unclaim(parent.id, child.id) is None
    assert child.workspace_ids == [one.id]
    child.workspace_ids = [two.id]
    sess.save(child)
    assert sess.effective_workspace_ids(grandchild) == [two.id]
    assert [s["id"] for s in asyncio.run(server.api_get_workspace_sessions(two.id, summary=1))["sessions"]] == [
        child.id, grandchild.id,
    ]
    # Reparenting clears the old root value and immediately inherits from the
    # new manager, even though the grandchild still has its legacy field.
    other_parent = sess.Session(id="ses_other_parent", name="other", workspace_ids=[one.id])
    sess._cache[other_parent.id] = other_parent
    assert sess.claim(other_parent.id, child.id) is None
    assert child.workspace_ids == []
    assert sess.effective_workspace_ids(grandchild) == [one.id]
    # Removing a manager promotes direct children to roots with a snapshot of
    # the inherited workspace; their descendants continue to follow.
    assert sess.release(other_parent.id) is None
    assert child.managed_by is None
    assert child.workspace_ids == [one.id]
    assert sess.effective_workspace_ids(grandchild) == [one.id]


def test_legacy_child_membership_is_ignored_without_mass_rewrite(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "WORKSPACE_DIR", tmp_path / "workspaces")
    workspace.clear_cache()
    parent = sess.Session(id="ses_legacy_parent", name="parent")
    child = sess.Session(id="ses_legacy_child", name="child", managed_by=parent.id,
                         workspace_ids=["ws_old"])
    before = child.to_dict()
    assert sess.effective_workspace_ids(child) == []
    assert child.to_dict() == before


def test_broken_or_cyclic_manager_chains_fail_closed_to_ungrouped():
    parent = sess.Session(id="ses_cycle_parent", name="parent", managed_by="ses_cycle_child",
                          workspace_ids=["ws_stale"])
    child = sess.Session(id="ses_cycle_child", name="child", managed_by=parent.id,
                         workspace_ids=["ws_old"])
    missing = sess.Session(id="ses_missing_parent", name="missing", managed_by="ses_absent",
                           workspace_ids=["ws_old"])
    sess._cache.update({parent.id: parent, child.id: child, missing.id: missing})
    assert sess.effective_workspace_ids(parent) == []
    assert sess.effective_workspace_ids(missing) == []


def test_claim_rejects_management_cycles_and_broken_manager_ancestry():
    root = sess.Session(id="ses_claim_root", name="root")
    child = sess.Session(id="ses_claim_child", name="child", managed_by=root.id)
    orphan = sess.Session(id="ses_claim_orphan", name="orphan", managed_by="ses_absent")
    sess._cache.update({root.id: root, child.id: child, orphan.id: orphan})
    assert "ancestor" in (sess.claim(child.id, root.id) or "")
    assert "broken manager chain" in (sess.claim(orphan.id, root.id) or "")


def test_core_session_create_rejects_multiple_workspace_ids():
    try:
        sess.create(name="invalid", workspace_ids=["ws_one", "ws_two"])
    except ValueError as exc:
        assert "at most one" in str(exc)
    else:
        raise AssertionError("core Session creation accepted multiple Workspace ids")
