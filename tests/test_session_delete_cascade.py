"""Focused tests for the server-side managed-session deletion expansion."""

import asyncio
from types import SimpleNamespace

from packages.core import session as _sess
from packages.web import server


def _session(sid: str, managed=None):
    value = _sess.Session(id=sid, name=sid, managed=list(managed or []))
    _sess._cache[sid] = value
    return value


def test_expansion_is_postorder_and_deduplicates_shared_descendants():
    _session("root", ["left", "right"])
    _session("left", ["shared"])
    _session("right", ["shared"])
    _session("shared", ["leaf"])
    _session("leaf")

    assert _sess.expand_managed_descendants(["root"]) == ["leaf", "shared", "left", "right"]


def test_expansion_ignores_missing_ids_and_breaks_cycles():
    _session("a", ["b", "missing"])
    _session("b", ["a", "c"])
    _session("c")

    result = _sess.expand_managed_descendants(["a"])
    assert set(result) == {"b", "c"}
    assert len(result) == 2


def test_selected_parent_and_child_do_not_duplicate_or_delete_unrelated_session():
    _session("parent", ["child"])
    _session("child")
    _session("unrelated")

    expanded = _sess.expand_managed_descendants(["parent"])
    ordered = list(dict.fromkeys(expanded + ["parent", "child"]))
    assert ordered == ["child", "parent"]
    assert "unrelated" not in ordered


def test_batch_endpoint_expands_child_first_and_keeps_worker_cleanup(monkeypatch):
    calls = []
    scheduled = []

    class FakeSessions:
        def expand_managed_descendants(self, roots):
            assert roots == ["parent"]
            return ["child"]

        def release(self, sid):
            calls.append(("release", sid))

        def delete(self, sid):
            calls.append(("delete", sid))

    class FakeWorker:
        def find_worker_by_session(self, sid):
            return SimpleNamespace(worker_id="worker-child", session_id=sid) if sid == "child" else None

        async def cleanup_worker_background(self, worker_id, sid):
            calls.append(("cleanup", sid))

    async def fake_broadcast(payload):
        calls.append(("broadcast", payload["sessionIds"]))

    monkeypatch.setattr(server, "sess", FakeSessions())
    monkeypatch.setattr(server, "worker", FakeWorker())
    monkeypatch.setattr(server, "broadcast", fake_broadcast)
    monkeypatch.setattr(server, "_cleanup_mcp_config", lambda sid: calls.append(("mcp", sid)))
    monkeypatch.setattr(server, "_cleanup_kimi_home", lambda sid: calls.append(("kimi", sid)))
    def capture_task(coro):
        scheduled.append(coro)
        coro.close()
    monkeypatch.setattr(asyncio, "create_task", capture_task)

    result = asyncio.run(server.api_batch_delete_sessions({
        "sessionIds": ["parent"], "cascadeSessionIds": ["parent"],
    }))
    assert result["deleted"] == 2
    assert [sid for action, sid in calls if action == "release"] == ["child", "parent"]
    assert result["sessionIds"] == ["child", "parent"]
    assert len(scheduled) == 1  # worker cleanup remains detached/background work
    assert ("broadcast", ["child", "parent"]) in calls


def test_batch_endpoint_reports_missing_selection_without_side_effect(monkeypatch):
    called = False

    def fail(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(server.sess, "release", fail)
    assert asyncio.run(server.api_batch_delete_sessions({})) == {"error": "sessionIds is required"}
    assert called is False
