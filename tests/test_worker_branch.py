"""Regression tests for provider-backed Worker branching."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CodexAdapter


def test_codex_worker_branch_uses_sessions_provider(monkeypatch):
    """Codex's empty fork_args must not make /worker/{id}/branch fail."""
    worker.workers.clear()
    _sess._cache.clear()

    parent = _sess.Session(
        id="ses_parent",
        name="parent",
        adapter="codex",
        model="gpt-5.4-mini",
        permission_mode="workspace-write",
        workdir="C:/workspace",
        system_prompt="Be concise.",
        adapter_config={
            "cli_session_id": "thread-parent",
            "effort": "low",
            "mcp_servers": {"pan": {"command": "node"}},
        },
    )
    child = _sess.Session(
        id="ses_child",
        name="child",
        adapter="codex",
        workdir="C:/workspace",
    )
    _sess._cache[parent.id] = parent
    _sess._cache[child.id] = child

    live = worker.Worker(
        worker_id="worker-parent",
        session_id=parent.id,
        adapter=CodexAdapter(),
        process=MagicMock(),
        pending_signal=asyncio.Queue(),
    )
    worker.workers[live.worker_id] = live

    calls = []

    class Provider:
        def fork_session(self, parent_id, name, cwd=None):
            calls.append(("fork", parent_id, name, cwd))
            return "thread-child"

        def parse_history(self, session_id, cwd=None):
            calls.append(("history", session_id, cwd))
            return [{"role": "user", "content": "old"}]

        def get_raw_usage(self, session_id, cwd=None):
            calls.append(("usage", session_id, cwd))
            return [{"model": "gpt-5.4-mini", "rawUsage": {"total_tokens": 3}}]

    async def fake_spawn(session_id, adapter, extra_args=None):
        assert session_id == child.id
        assert extra_args == []
        return MagicMock()

    def fake_create_task(coro):
        # Branching should install the normal worker tasks.  Close the test
        # coroutines immediately so asyncio.run does not report leaks.
        coro.close()
        return MagicMock()

    monkeypatch.setattr(worker, "get_sessions_provider", lambda name: Provider())
    monkeypatch.setattr(worker, "_spawn_process", fake_spawn)
    monkeypatch.setattr(worker.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)

    result = asyncio.run(worker.branch_worker(live.worker_id, child.id))

    assert isinstance(result, worker.Worker)
    assert child.cli_session_id == "thread-child"
    assert child.history == [{"role": "user", "content": "old"}]
    assert child.model == "gpt-5.4-mini"
    assert child.permission_mode == "workspace-write"
    assert child.system_prompt == "Be concise."
    assert child.adapter_config["mcp_servers"] == {"pan": {"command": "node"}}
    assert [item[0] for item in calls] == ["fork", "history", "usage"]

    worker.workers.clear()
    _sess._cache.clear()
