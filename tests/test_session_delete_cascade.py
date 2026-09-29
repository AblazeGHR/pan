"""Focused tests for the server-side managed-session deletion expansion."""

import asyncio
from types import SimpleNamespace

import pytest

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

        def delete_with_release(self, sid):
            error = self.release(sid)
            if error:
                return error
            self.delete(sid)
            return None

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


@pytest.mark.parametrize(
    ("retention_cleanup", "storage_in_thread"),
    [(False, False), (True, True)],
)
def test_delete_session_records_deletes_storage_once_and_uses_mode_cleanup(
    monkeypatch, retention_cleanup, storage_in_thread,
):
    calls = []

    class FakeSessions:
        def release(self, sid):
            calls.append(("release", sid))

        def delete(self, sid):
            calls.append(("delete", sid))

        def delete_with_release(self, sid):
            error = self.release(sid)
            if error:
                return error
            self.delete(sid)
            return None

    class FakeWorker:
        def find_worker_by_session(self, _sid):
            return None

    async def fake_broadcast(payload):
        calls.append(("broadcast", payload["sessionId"]))

    def safe_auxiliary_cleanup(sid):
        calls.append(("safe_auxiliary", sid))
        return ["mcp_session_config_unsafe"]

    def unrestricted_cleanup(which):
        def fail_if_called(sid):
            calls.append(("unrestricted_auxiliary", which, sid))
        return fail_if_called

    monkeypatch.setattr(server, "sess", FakeSessions())
    monkeypatch.setattr(server, "worker", FakeWorker())
    monkeypatch.setattr(server, "broadcast", fake_broadcast)
    monkeypatch.setattr(server, "_retention_cleanup_auxiliary", safe_auxiliary_cleanup)
    monkeypatch.setattr(server, "_cleanup_mcp_config", unrestricted_cleanup("mcp"))
    monkeypatch.setattr(server, "_cleanup_kimi_home", unrestricted_cleanup("kimi"))

    result = asyncio.run(server._delete_session_records(
        "session-1",
        cleanup_auxiliary=True,
        storage_in_thread=storage_in_thread,
        retention_cleanup=retention_cleanup,
    ))

    assert calls.count(("release", "session-1")) == 1
    assert calls.count(("delete", "session-1")) == 1
    assert calls.count(("broadcast", "session-1")) == 1
    if retention_cleanup:
        assert calls.count(("safe_auxiliary", "session-1")) == 1
        assert not any(call[0] == "unrestricted_auxiliary" for call in calls)
        assert result["lifecycleSkipReasons"] == ["mcp_session_config_unsafe"]
    else:
        assert calls.count(("safe_auxiliary", "session-1")) == 0
        assert calls.count(("unrestricted_auxiliary", "mcp", "session-1")) == 1
        assert calls.count(("unrestricted_auxiliary", "kimi", "session-1")) == 1


def test_retention_auxiliary_cleanup_keeps_paths_refused_by_safety_checks(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    mcp_root = data_dir / "mcp-configs"
    kimi_root = data_dir / "kimi-homes"
    mcp_root.mkdir(parents=True)
    kimi_root.mkdir(parents=True)
    mcp_path = mcp_root / "session-unsafe.mcp.json"
    kimi_home = kimi_root / "session-unsafe"
    mcp_path.write_text("synthetic config", encoding="utf-8")
    kimi_home.mkdir()
    kimi_file = kimi_home / "synthetic.txt"
    kimi_file.write_text("synthetic home", encoding="utf-8")

    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "_retention_safe_root", lambda root: root.resolve())
    monkeypatch.setattr(server, "_retention_safe_session_file", lambda *_args: None)
    monkeypatch.setattr(server, "_retention_plain_tree", lambda *_args: False)

    skipped = server._retention_cleanup_auxiliary("session-unsafe")

    assert skipped == ["mcp_session_config_unsafe", "kimi_session_home_unsafe"]
    assert mcp_path.read_text(encoding="utf-8") == "synthetic config"
    assert kimi_file.read_text(encoding="utf-8") == "synthetic home"
