"""Owned profiles only: physical rollout IDs differ after native revert."""
import json
import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

from packages.core.adapters.codex import sessions as codex
from packages.core.adapters.codex import app_server_wrapper as wrapper
from packages.core.adapters.codex.adapter import _resolve_codex_node, _resolve_codex_js


def profile(monkeypatch, home):
    monkeypatch.setenv("CODEX_HOME", str(home))
    for key, value in {"_CODEX_DIR": home, "_STATE_DB": home / "state_5.sqlite",
                       "_HISTORY_DB": home / "thread_history_1.sqlite",
                       "_SESSIONS_DIR": home / "sessions"}.items():
        monkeypatch.setattr(codex, key, value)


@pytest.fixture
def reverted(tmp_path, monkeypatch):
    from test_codex_adapter import _build_fake_codex_dir
    home = tmp_path / "home"
    home.mkdir()
    _build_fake_codex_dir(home)
    profile(monkeypatch, home)
    logical = "11111111-1111-4111-8111-111111111111"
    physical = "22222222-2222-4222-8222-222222222222"
    root = codex._SESSIONS_DIR / f"rollout-2026-10-21T00-00-00-{logical}.jsonl"
    current = codex._SESSIONS_DIR / f"rollout-2026-10-21T00-00-00-{logical}_{physical}.jsonl"
    def write(path, events):
        path.write_bytes(b"".join((json.dumps(e, ensure_ascii=False) + "\n").encode() for e in events))
    def message(ordinal, turn, text):
        return [{"ordinal": ordinal, "type": "turn_context", "payload": {"turn_id": turn}},
                {"ordinal": ordinal + 1, "type": "response_item", "payload": {
                    "type": "message", "id": turn, "role": "user", "content": [
                        {"type": "input_text", "text": text}]}}]
    root_events = [{"ordinal": 0, "type": "session_meta", "payload": {"id": logical, "history_mode": "paginated"}}]
    root_events += message(1, "t1", f"中文 {logical}")
    root_events += message(3, "t2", f"中文 {logical}")
    write(root, root_events)
    base = {"thread_id": logical, "end_ordinal_exclusive": 5, "end_byte_offset": root.stat().st_size}
    write(root, root_events + message(5, "removed", "must not reappear"))
    write(current, [{"ordinal": 5, "type": "session_meta", "payload": {
        "id": logical, "session_id": logical, "history_mode": "paginated", "history_base": base,
        "context_window": {"window_id": logical}}}] + message(6, "local", "after revert"))
    with sqlite3.connect(codex._STATE_DB) as con:
        con.execute("alter table threads add column history_mode text")
        con.execute("update threads set id=?,rollout_path=?,history_mode='paginated'", (logical, str(current)))
    return logical, physical, root, current


def test_physical_lineage_prefix_cache_and_identity(reverted):
    logical, physical, root, current = reverted
    before = {p: p.read_bytes() for p in (root, current)}
    expected = [f"中文 {logical}"] * 2 + ["after revert"]
    with sqlite3.connect(codex._HISTORY_DB) as con:
        con.execute("update thread_items set thread_id=?", (logical,))
    assert [r["content"] for r in codex.parse_history(logical)] == expected
    child = codex.fork_session(logical, "branch")
    assert [r["content"] for r in codex.parse_history(child)] == expected
    with sqlite3.connect(codex._STATE_DB) as con:
        path = Path(con.execute("select rollout_path from threads where id=?", (child,)).fetchone()[0])
    meta = json.loads(path.read_bytes().splitlines()[0])["payload"]
    assert meta["id"] == child
    assert meta["session_id"] == logical
    assert meta["forked_from_id"] == logical
    assert meta["context_window"]["window_id"] == logical
    assert meta["history_base"]["thread_id"] == logical
    with sqlite3.connect(codex._HISTORY_DB) as con:
        assert con.execute("select count(*) from thread_items where thread_id=?", (child,)).fetchone()[0] == 0
    assert {p: p.read_bytes() for p in before} == before


def test_branch_write_failure_rolls_back_owned_child(reverted, monkeypatch):
    logical, _, _, _ = reverted
    before = set(codex._SESSIONS_DIR.rglob("*.jsonl"))
    original = Path.open
    def fail(path, mode="r", *args, **kwargs):
        if mode == "xb":
            raise OSError("owned write failure")
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError, match="owned write failure"):
        codex.fork_session(logical, "failed")
    assert set(codex._SESSIONS_DIR.rglob("*.jsonl")) == before
    with sqlite3.connect(codex._STATE_DB) as con:
        assert con.execute("select count(*) from threads").fetchone()[0] == 1


def test_empty_paginated_rollout_does_not_resurrect_stale_cache(reverted):
    logical, _, _, current = reverted
    current.write_bytes((json.dumps({"ordinal": 0, "type": "session_meta", "payload": {
        "id": logical, "history_mode": "paginated"}}) + "\n").encode())
    with sqlite3.connect(codex._HISTORY_DB) as con:
        con.execute("update thread_items set thread_id=?", (logical,))
    assert codex.parse_history(logical) == []
    current.unlink()
    with pytest.raises(ValueError, match="missing source rollout"):
        codex.parse_history(logical)


@pytest.mark.parametrize("damage", ["self", "two-node", "missing", "bytes", "ordinal", "boundary"])
def test_invalid_lineage_fails_before_branch_writes(reverted, damage):
    logical, physical, root, current = reverted
    events = [json.loads(line) for line in current.read_bytes().splitlines()]
    base = events[0]["payload"]["history_base"]
    if damage == "self":
        base["thread_id"] = physical
    elif damage == "two-node":
        rows = [json.loads(line) for line in root.read_bytes().splitlines()]
        rows[0]["payload"]["history_base"] = {
            "thread_id": physical, "end_ordinal_exclusive": 100, "end_byte_offset": current.stat().st_size}
        root.write_bytes(b"".join((json.dumps(e, ensure_ascii=False) + "\n").encode() for e in rows))
        base["end_byte_offset"] = root.stat().st_size
        base["end_ordinal_exclusive"] = 100
    elif damage == "missing":
        base["thread_id"] = "33333333-3333-4333-8333-333333333333"
    elif damage == "bytes":
        base["end_byte_offset"] = root.stat().st_size + 100
    elif damage == "ordinal":
        base["end_ordinal_exclusive"] = 1
    else:
        base["end_byte_offset"] -= 1
    current.write_bytes(b"".join((json.dumps(e, ensure_ascii=False) + "\n").encode() for e in events))
    before = {p: p.read_bytes() for p in codex._SESSIONS_DIR.rglob("*.jsonl")}
    for read in (lambda: codex.parse_history(logical), lambda: codex.fork_session(logical, "bad")):
        with pytest.raises(ValueError, match="invalid paginated history lineage"):
            read()
    assert {p: p.read_bytes() for p in codex._SESSIONS_DIR.rglob("*.jsonl")} == before
    with sqlite3.connect(codex._STATE_DB) as con:
        assert con.execute("select count(*) from threads").fetchone()[0] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="installed Windows Codex CLI")
def test_real_revert_branch_resume_and_history(tmp_path, monkeypatch):
    from test_codex_native_kernel_real import SyntheticResponses
    home = tmp_path / "home"
    home.mkdir()
    profile(monkeypatch, home)
    provider = SyntheticResponses()
    provider.release.set()
    options = ["-c", 'model="probe-model"', "-c", 'model_provider="local_fixture"', "-c",
               f'model_providers.local_fixture={{name="fixture",base_url="{provider.base_url}",wire_api="responses",requires_openai_auth=false}}']
    monkeypatch.setattr(wrapper, "_write_stdout", lambda _: None)
    app = wrapper.AppServer(_resolve_codex_node(), _resolve_codex_js(), str(tmp_path), options)
    params = app._thread_params
    monkeypatch.setattr(app, "_thread_params", lambda: {**params(), "historyMode": "paginated"})
    request = app._request
    monkeypatch.setattr(app, "_request", lambda method, p: request(method, {
        **p, "capabilities": {"experimentalApi": True}} if method == "initialize" else p))
    def rpc(method, p):
        return app._wait_response(app._request(method, p))["result"]
    try:
        app.start()
        source = app.thread_id
        turns = []
        for index in range(3):
            result = rpc("turn/start", {"threadId": source, "input": [
                {"type": "text", "text": f"repeat {source}", "text_elements": []}]})
            turns.append(result["turn"]["id"])
            while True:
                event = app._next(timeout=20)
                if event.get("method") == "turn/completed":
                    assert event["params"]["turn"]["status"] == "completed"
                    break
        rpc("thread/revert", {"threadId": source, "beforeTurnId": turns[2]})
        # A logical self-reference is valid here: the current physical rollout
        # has another ID and the base refers to its immutable predecessor.
        with sqlite3.connect(codex._STATE_DB) as con:
            source_path = Path(con.execute("select rollout_path from threads where id=?", (source,)).fetchone()[0])
        meta = json.loads(source_path.read_text(encoding="utf-8").splitlines()[0])["payload"]
        assert meta["history_base"]["thread_id"] == source
        assert not source_path.stem.endswith("-" + source)
        frozen = source_path.read_bytes()
        from packages.core import session, worker
        from packages.web import server
        from unittest.mock import AsyncMock
        monkeypatch.setattr(server, "broadcast", AsyncMock())
        monkeypatch.setattr(server, "_sessions_provider", lambda _: codex)
        imported = asyncio.run(server.api_adapter_sessions_import("codex", {
            "session_id": source, "name": "owned-import", "cwd": str(tmp_path)}))
        assert "error" not in imported, imported
        parent = next(s for s in session.list_all() if s.cli_session_id == source)
        branched = asyncio.run(server.api_branch_session(parent.id, {"name": "owned-branch"}))
        assert "error" not in branched, branched
        pan_child = next(s for s in session.list_all() if s.id != parent.id)
        child = pan_child.cli_session_id
        assert source_path.read_bytes() == frozen
        resumed = rpc("thread/resume", {"threadId": child, "excludeTurns": True})
        assert resumed["thread"]["id"] == child
        assert resumed["thread"]["sessionId"] == source
        assert resumed["thread"]["forkedFromId"] == source
        page = rpc("thread/turns/list", {"threadId": child, "limit": 1, "itemsView": "full"})
        assert [t["id"] for t in page["data"]] == [turns[1]]
        older = rpc("thread/turns/list", {"threadId": child, "limit": 1, "itemsView": "full", "cursor": page["nextCursor"]})
        assert [t["id"] for t in older["data"]] == [turns[0]]
        assert older["nextCursor"] is None
        history = codex.parse_history(child)
        expected = [f"repeat {source}"] * 2
        assert [r["content"] for r in history if r["role"] == "user"] == expected
        assert [r["content"] for r in pan_child.history if r["role"] == "user"] == expected
        # Rebuild stale/duplicate caches from the file on a second branch.
        grandchild = codex.fork_session(child, "second branch")
        assert [r["content"] for r in codex.parse_history(grandchild) if r["role"] == "user"] == expected
        rpc("thread/resume", {"threadId": grandchild, "excludeTurns": True})
        native = rpc("thread/fork", {"threadId": child, "excludeTurns": True})["thread"]["id"]
        assert [r["content"] for r in codex.parse_history(native) if r["role"] == "user"] == expected
        # Reproduce the old corruption on an owned copy, then prove the narrow
        # recovery: restore the proven physical base, never delete lineage.
        damaged = codex.fork_session(source, "damaged-copy")
        with sqlite3.connect(codex._STATE_DB) as con:
            damaged_path = Path(con.execute("select rollout_path from threads where id=?", (damaged,)).fetchone()[0])
        intact = damaged_path.read_bytes()
        lines = intact.splitlines(keepends=True)
        damaged_meta = json.loads(lines[0])
        damaged_meta["payload"]["history_base"]["thread_id"] = damaged
        damaged_path.write_bytes((json.dumps(damaged_meta) + "\n").encode() + b"".join(lines[1:]))
        with pytest.raises(RuntimeError, match="cycle detected"):
            rpc("thread/resume", {"threadId": damaged, "excludeTurns": True})
        with pytest.raises(ValueError, match="cycle detected"):
            codex.parse_history(damaged)
        damaged_path.write_bytes(intact)
        rpc("thread/resume", {"threadId": damaged, "excludeTurns": True})
        assert [r["content"] for r in codex.parse_history(damaged) if r["role"] == "user"] == expected
        assert source_path.read_bytes() == frozen
        app.close()  # Release the owned writer before another app-server resumes.
        # Actual Pan Start -> wrapper -> native resume, retaining child identity.
        from packages.core.adapters.codex import CodexAdapter
        monkeypatch.setattr(CodexAdapter, "mcp_args", lambda *args: options)
        monkeypatch.setattr(CodexAdapter, "enrich_after_result", lambda *args: None)
        monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)
        monkeypatch.setattr(worker, "_bcast", AsyncMock())
        pan_child.model = "probe-model"
        async def start():
            w = await worker.create_worker(pan_child.id)
            assert isinstance(w, worker.Worker), w
            process = w.process
            try:
                async with asyncio.timeout(20):
                    while not w.native_kernel_ready:
                        assert w.process.returncode is None, pan_child.last_result
                        await asyncio.sleep(.02)
                assert pan_child.cli_session_id == child
            finally:
                await worker.kill_worker(w.worker_id)
                assert process.returncode is not None
        asyncio.run(start())
        assert source_path.read_bytes() == frozen
    finally:
        app.close()
        assert app.process is None or app.process.poll() is not None
        provider.close()
