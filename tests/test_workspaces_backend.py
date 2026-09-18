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
    assert result["ok"] and result["session"]["workspaceIds"] == [wid1, wid2]
    assert [s["id"] for s in asyncio.run(
        server.api_get_workspace_sessions(wid1, summary=1))["sessions"]] == ["ses_a"]
    assert [s["id"] for s in asyncio.run(
        server.api_list_sessions(workspaceId="ungrouped"))["sessions"]] == ["ses_b"]

    # A restart-like reload reads both metadata and Session membership.
    workspace.clear_cache()
    sess._cache.clear()
    sess._all_loaded = False
    assert workspace.get(wid1).name == "Inbox"
    assert sess.get("ses_a").workspace_ids == [wid1, wid2]


def test_workspace_order_and_delete_removes_memberships(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "WORKSPACE_DIR", tmp_path / "workspaces")
    workspace.clear_cache()
    a = sess.Session(id="ses_a", name="a", workspace_ids=[])
    sess._cache[a.id] = a
    first = workspace.create("First")
    second = workspace.create("Second")
    a.workspace_ids = [first.id, second.id]
    sess.save(a)
    assert asyncio.run(server.api_workspaces_order(
        {"workspaceIds": [second.id, first.id]}))["order"] == [second.id, first.id]
    assert asyncio.run(server.api_delete_workspace(first.id))["ok"]
    assert sess.get(a.id).workspace_ids == [second.id]
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


def test_session_schema_compat_without_workspace_field():
    legacy = sess.Session._from_data({"id": "ses_legacy", "name": "legacy"})
    assert legacy.workspace_ids == []
    assert sess.Session._from_data(legacy.to_dict()).workspace_ids == []
