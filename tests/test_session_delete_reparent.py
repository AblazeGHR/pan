"""Managed Session reparenting rules for deletion (isolated Session storage)."""

import asyncio
import pytest

from packages.core import session as sess
from packages.web import server


def _persist(*sessions):
    for session in sessions:
        sess._cache[session.id] = session
        sess.save(session)


def _install_delete_api_stubs(monkeypatch):
    events = []

    class FakeWorker:
        def find_worker_by_session(self, _session_id):
            return None

    async def fake_broadcast(payload):
        events.append(payload)

    monkeypatch.setattr(server, "worker", FakeWorker())
    monkeypatch.setattr(server, "broadcast", fake_broadcast)
    monkeypatch.setattr(server, "_cleanup_mcp_config", lambda _sid: None)
    monkeypatch.setattr(server, "_cleanup_kimi_home", lambda _sid: None)
    return events


@pytest.mark.parametrize(
    ("manager_is_root", "workspace_id"),
    [
        (True, "ws_manager"),
        (True, None),
        (False, "ws_ancestor"),
        (False, None),
    ],
)
def test_delete_reparents_direct_children_and_preserves_workspace_and_reports(
    manager_is_root, workspace_id,
):
    if manager_is_root:
        manager = sess.Session(
            id="manager", name="manager",
            managed=["doomed", "manager-child"],
            workspace_ids=[workspace_id] if workspace_id else [],
        )
        ancestor = None
    else:
        ancestor = sess.Session(
            id="ancestor", name="ancestor", managed=["manager"],
            workspace_ids=[workspace_id] if workspace_id else [],
        )
        manager = sess.Session(
            id="manager", name="manager", managed=["doomed", "manager-child"],
            managed_by="ancestor",
        )
    doomed = sess.Session(
        id="doomed", name="doomed", managed=["child-one", "child-two"],
        managed_by="manager", report_subscriptions={"child-one"},
    )
    child_one = sess.Session(
        id="child-one", name="child-one", managed=["grandchild"],
        managed_by="doomed",
    )
    child_two = sess.Session(id="child-two", name="child-two", managed_by="doomed")
    grandchild = sess.Session(
        id="grandchild", name="grandchild", managed_by="child-one",
        workspace_ids=["ws_legacy_ignored"],
    )
    manager_child = sess.Session(id="manager-child", name="manager-child", managed_by="manager")
    observer = sess.Session(
        id="observer", name="observer", report_subscriptions={"doomed"},
    )
    manager.report_subscriptions = {"doomed", "manager-child", "child-two"}
    _persist(*([ancestor] if ancestor else []), manager, doomed, child_one,
             child_two, grandchild, manager_child, observer)

    assert sess.delete_with_release("doomed") is None

    # A restart-like reload proves that both sides of the surviving edges,
    # report switches, and inherited Workspace values were persisted.
    sess._cache.clear()
    sess._all_loaded = False
    loaded = {item.id: item for item in sess.list_all(load_history=False)}
    assert "doomed" not in loaded
    assert loaded["manager"].managed == ["child-one", "child-two", "manager-child"]
    assert loaded["child-one"].managed_by == "manager"
    assert loaded["child-two"].managed_by == "manager"
    assert loaded["child-one"].workspace_ids == []
    assert loaded["child-two"].workspace_ids == []
    assert loaded["grandchild"].managed_by == "child-one"
    assert loaded["grandchild"].workspace_ids == ["ws_legacy_ignored"]
    assert loaded["observer"].report_subscriptions == set()
    # The old per-child state follows the children: on remains on, off remains
    # off, and the new manager's unrelated child subscription remains on.
    assert loaded["manager"].report_subscriptions == {"child-one", "manager-child"}
    assert sess.effective_workspace_ids("child-one") == ([workspace_id] if workspace_id else [])
    assert sess.effective_workspace_ids("grandchild") == ([workspace_id] if workspace_id else [])
    if ancestor:
        assert loaded["manager"].managed_by == "ancestor"
        assert loaded["manager"].workspace_ids == []
        assert loaded["ancestor"].managed == ["manager"]
    else:
        assert loaded["manager"].managed_by is None
        assert loaded["manager"].workspace_ids == ([workspace_id] if workspace_id else [])


@pytest.mark.parametrize("workspace_ids", [["ws_root", "ws_legacy"], []])
def test_deleting_root_promotes_only_direct_children_with_one_workspace(workspace_ids):
    root = sess.Session(
        id="root", name="root", managed=["child-one", "child-two"],
        workspace_ids=list(workspace_ids),
    )
    child_one = sess.Session(
        id="child-one", name="child-one", managed=["grandchild"], managed_by="root",
    )
    child_two = sess.Session(id="child-two", name="child-two", managed_by="root")
    grandchild = sess.Session(id="grandchild", name="grandchild", managed_by="child-one")
    _persist(root, child_one, child_two, grandchild)

    assert sess.delete_with_release("root") is None
    sess._cache.clear()
    sess._all_loaded = False
    loaded = {item.id: item for item in sess.list_all(load_history=False)}
    expected_workspace = workspace_ids[:1]
    for child_id in ("child-one", "child-two"):
        assert loaded[child_id].managed_by is None
        assert loaded[child_id].workspace_ids == expected_workspace
        assert sess.effective_workspace_ids(child_id) == expected_workspace
    assert loaded["grandchild"].managed_by == "child-one"
    assert sess.effective_workspace_ids("grandchild") == expected_workspace


def test_single_delete_endpoint_uses_reparenting_core(monkeypatch):
    events = _install_delete_api_stubs(monkeypatch)
    manager = sess.Session(id="manager", name="manager", managed=["doomed"])
    doomed = sess.Session(
        id="doomed", name="doomed", managed=["child"], managed_by="manager",
    )
    child = sess.Session(id="child", name="child", managed_by="doomed")
    _persist(manager, doomed, child)

    result = asyncio.run(server.api_delete_session("doomed"))

    assert result["status"] == "deleted"
    assert sess.get("doomed") is None
    assert sess.get("manager").managed == ["child"]
    assert sess.get("child").managed_by == "manager"
    assert events == [{"type": "session.deleted", "sessionId": "doomed"}]


def test_exact_batch_delete_reparents_unselected_children(monkeypatch):
    events = _install_delete_api_stubs(monkeypatch)
    manager = sess.Session(id="manager", name="manager", managed=["doomed"])
    doomed = sess.Session(
        id="doomed", name="doomed", managed=["child"], managed_by="manager",
    )
    child = sess.Session(id="child", name="child", managed_by="doomed")
    unrelated = sess.Session(id="unrelated", name="unrelated")
    _persist(manager, doomed, child, unrelated)

    result = asyncio.run(server.api_batch_delete_sessions({
        "sessionIds": ["doomed", "unrelated"],
    }))

    assert result == {"deleted": 2, "sessionIds": ["doomed", "unrelated"]}
    assert sess.get("child").managed_by == "manager"
    assert sess.get("manager").managed == ["child"]
    assert sess.get("unrelated") is None
    assert events == [{
        "type": "sessions.deleted", "sessionIds": ["doomed", "unrelated"],
    }]


def test_retention_delete_uses_same_reparenting_storage_path(monkeypatch):
    _install_delete_api_stubs(monkeypatch)
    manager = sess.Session(id="manager", name="manager", managed=["doomed"])
    doomed = sess.Session(
        id="doomed", name="doomed", managed=["child"], managed_by="manager",
    )
    child = sess.Session(id="child", name="child", managed_by="doomed")
    _persist(manager, doomed, child)
    monkeypatch.setattr(server, "_retention_cleanup_auxiliary", lambda _sid: [])

    result = asyncio.run(server._delete_session_records(
        "doomed", cleanup_auxiliary=True, storage_in_thread=True,
        retention_cleanup=True,
    ))

    assert result["status"] == "deleted"
    assert result["lifecycleSkipReasons"] == []
    assert sess.get("doomed") is None
    assert sess.get("manager").managed == ["child"]
    assert sess.get("child").managed_by == "manager"


def test_cascade_batch_deletes_descendants_child_first(monkeypatch):
    events = _install_delete_api_stubs(monkeypatch)
    manager = sess.Session(id="manager", name="manager", managed=["doomed"])
    doomed = sess.Session(
        id="doomed", name="doomed", managed=["child"], managed_by="manager",
        report_subscriptions={"child"},
    )
    child = sess.Session(id="child", name="child", managed=["grandchild"], managed_by="doomed")
    grandchild = sess.Session(id="grandchild", name="grandchild", managed_by="child")
    _persist(manager, doomed, child, grandchild)

    result = asyncio.run(server.api_batch_delete_sessions({
        "sessionIds": ["doomed"], "cascadeSessionIds": ["doomed"],
    }))

    assert result == {
        "deleted": 3,
        "sessionIds": ["grandchild", "child", "doomed"],
    }
    assert all(sess.get(sid) is None for sid in ("doomed", "child", "grandchild"))
    assert sess.get("manager").managed == []
    assert sess.get("manager").report_subscriptions == set()
    assert events == [{
        "type": "sessions.deleted",
        "sessionIds": ["grandchild", "child", "doomed"],
    }]


@pytest.mark.parametrize("corruption", ["missing_manager", "backlink", "cycle"])
def test_release_refuses_corrupt_relationships_without_mutation(corruption):
    manager = sess.Session(id="manager", name="manager")
    doomed = sess.Session(id="doomed", name="doomed")
    child = sess.Session(id="child", name="child")
    if corruption == "missing_manager":
        doomed.managed_by = "missing-manager"
    elif corruption == "backlink":
        manager.managed = ["doomed"]
    else:
        doomed.managed = ["child"]
        doomed.managed_by = child.id
        child.managed = ["doomed"]
        child.managed_by = doomed.id
    _persist(manager, doomed, child)
    before = {s.id: s.to_dict() for s in (manager, doomed, child)}

    error = sess.release("doomed")

    assert error and "Cannot release Session" in error
    assert {s.id: s.to_dict() for s in (manager, doomed, child)} == before


def test_release_rolls_back_disk_and_memory_if_manager_save_fails(monkeypatch):
    manager = sess.Session(id="manager", name="manager", managed=["doomed"])
    doomed = sess.Session(
        id="doomed", name="doomed", managed=["child"], managed_by="manager",
        report_subscriptions={"child"},
    )
    child = sess.Session(id="child", name="child", managed_by="doomed")
    observer = sess.Session(id="observer", name="observer", report_subscriptions={"doomed"})
    _persist(manager, doomed, child, observer)
    original_save = sess.save
    failed = False

    def fail_manager_once(session):
        nonlocal failed
        if session.id == "manager" and not failed:
            failed = True
            raise OSError("simulated manager metadata failure")
        original_save(session)

    monkeypatch.setattr(sess, "save", fail_manager_once)
    error = sess.release("doomed")

    assert error and "simulated manager metadata failure" in error
    assert sess.get("manager").managed == ["doomed"]
    assert sess.get("child").managed_by == "doomed"
    assert sess.get("observer").report_subscriptions == {"doomed"}
    sess._cache.clear()
    sess._all_loaded = False
    loaded = {item.id: item for item in sess.list_all(load_history=False)}
    assert loaded["manager"].managed == ["doomed"]
    assert loaded["child"].managed_by == "doomed"
    assert loaded["observer"].report_subscriptions == {"doomed"}


def test_delete_file_failure_restores_relationships_and_keeps_session(monkeypatch):
    manager = sess.Session(id="manager", name="manager", managed=["doomed"])
    doomed = sess.Session(
        id="doomed", name="doomed", managed=["child"], managed_by="manager",
    )
    child = sess.Session(id="child", name="child", managed_by="doomed")
    _persist(manager, doomed, child)

    def fail_delete(_session_id):
        raise OSError("simulated delete failure")

    monkeypatch.setattr(sess, "delete", fail_delete)
    error = sess.delete_with_release("doomed")

    assert error and "simulated delete failure" in error
    assert sess.get("doomed") is not None
    assert sess.get("manager").managed == ["doomed"]
    assert sess.get("child").managed_by == "doomed"
    sess._cache.clear()
    sess._all_loaded = False
    loaded = {item.id: item for item in sess.list_all(load_history=False)}
    assert loaded["doomed"].managed == ["child"]
    assert loaded["child"].managed_by == "doomed"


def test_api_does_not_delete_when_relationship_release_is_refused(monkeypatch):
    events = _install_delete_api_stubs(monkeypatch)
    manager = sess.Session(id="manager", name="manager")
    doomed = sess.Session(id="doomed", name="doomed", managed_by="missing-manager")
    _persist(manager, doomed)

    result = asyncio.run(server.api_delete_session("doomed"))

    assert "error" in result and "missing manager" in result["error"]
    assert sess.get("doomed") is not None
    assert events == []
