"""Regression tests for provider-backed Worker branching."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CodexAdapter


def _no_ts(entries):
    """剥掉 append_history 打的 ts 与 messageId 字段，便于断言消息本体。

    messageId 是 append_history 在追加边界分配的身份（见
    ``Session.append_history`` 与 ``b553e929``），属于传输/身份元数据而非
    消息业务形状。身份本身由 ``_message_ids`` 单独显式断言，避免"为了对齐
    业务断言而顺手忽略身份"导致身份契约失去覆盖。
    """
    return [
        {k: v for k, v in e.items() if k not in ("ts", "messageId")}
        for e in entries
    ]


def _message_ids(entries):
    """取出每行的 Pan 消息身份，供身份契约显式断言。"""
    return [e.get("messageId") for e in entries]


def test_codex_worker_branch_uses_sessions_provider(monkeypatch):
    """Codex's empty fork_args must not make /worker/{id}/branch fail."""
    worker.workers.clear()
    _sess._cache.clear()


def test_steer_worker_persists_only_after_control_write(monkeypatch):
    worker.workers.clear()
    _sess._cache.clear()
    session = _sess.Session(id="ses_steer", name="steer", adapter="codex")
    _sess._cache[session.id] = session
    live = worker.Worker(
        worker_id="worker-steer",
        session_id=session.id,
        adapter=CodexAdapter(),
        process=MagicMock(),
    )
    live.process.returncode = None
    live.process.stdin = MagicMock()
    worker.workers[live.worker_id] = live

    async def fake_control(worker_id, control):
        assert worker_id == live.worker_id
        assert control == {"type": "steer", "text": "focus here"}
        return None

    monkeypatch.setattr(worker, "send_control_message", fake_control)
    monkeypatch.setattr(_sess, "save_async", AsyncMock())

    assert asyncio.run(worker.steer_worker(live.worker_id, " focus here ")) is None
    assert _no_ts(session.history) == [{"role": "user", "content": "focus here"}]
    # steer 写入的是可搜索正文，append_history 必须给它分配 Pan 身份。
    (steer_id,) = _message_ids(session.history)
    assert _sess.is_pan_message_id(steer_id), (
        f"steer history must carry a Pan messageId, got {steer_id!r}"
    )

    worker.workers.clear()
    _sess._cache.clear()
    parent = _sess.Session(
        id="ses_parent",
        name="parent",
        adapter="codex",
        model="gpt-5.4-mini",
        permission_mode="workspace-write",
        workdir="C:/workspace",
        original_prompt="Be concise.",
        handoff_prompt="Latest brief.",
        adapter_config={
            "cli_session_id": "thread-parent",
            "effort": "low",
            "model_context_window": 64000,
            "model_auto_compact_token_limit": 60800,
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
    assert _no_ts(child.history) == [{"role": "user", "content": "old"}]
    # 分支导入的 provider 正文同样要经assign_pan_message_ids 拿到 Pan 身份。
    (forked_id,) = _message_ids(child.history)
    assert _sess.is_pan_message_id(forked_id), (
        f"branched history must carry a Pan messageId, got {forked_id!r}"
    )
    assert child.model == "gpt-5.4-mini"
    assert child.permission_mode == "workspace-write"
    assert child.original_prompt == "Be concise."
    assert child.handoff_prompt == "Latest brief."
    assert child.system_prompt == parent.system_prompt
    assert child.adapter_config["mcp_servers"] == {"pan": {"command": "node"}}
    assert child.adapter_config["model_context_window"] == 64000
    assert child.adapter_config["model_auto_compact_token_limit"] == 60800
    assert [item[0] for item in calls] == ["fork", "history", "usage"]

    worker.workers.clear()
    _sess._cache.clear()


def test_programmatic_steer_is_blocked_by_queue_edit_lease(monkeypatch):
    worker.workers.clear()
    worker._queue_locks.clear()
    _sess._cache.clear()
    session = _sess.Session(id="ses_steer_edit", name="steer edit", adapter="codex")
    _sess._cache[session.id] = session
    item = {
        "id": "q-being-edited",
        "queueItemId": "q-being-edited",
        "kind": "task",
        "type": "task",
        "source": "user",
        "text": "old text",
        "deliveryState": "queued",
        "revision": 1,
    }
    session.queue_pending.append(item)
    worker.acquire_queue_edit_lock(session, item["id"], "active-edit")

    stdin = MagicMock()
    stdin.drain = AsyncMock()
    process = MagicMock()
    process.returncode = None
    process.stdin = stdin
    live = worker.Worker(
        worker_id="worker-steer-edit",
        session_id=session.id,
        adapter=CodexAdapter(),
        process=process,
    )
    worker.workers[live.worker_id] = live
    monkeypatch.setattr(_sess, "save_async", AsyncMock())

    blocked = asyncio.run(worker.steer_worker(live.worker_id, "edited message"))

    assert blocked == "Cannot Steer while a queued message is being edited"
    stdin.write.assert_not_called()
    assert session.history == []

    worker.release_queue_edit_lock(session, item["id"], "active-edit")
    assert asyncio.run(worker.steer_worker(live.worker_id, "after cancel")) is None
    stdin.write.assert_called_once()
    assert _no_ts(session.history) == [{"role": "user", "content": "after cancel"}]
    # 解除编辑锁后 steer 写入的正文同样必须带 Pan 身份。
    (steer_id,) = _message_ids(session.history)
    assert _sess.is_pan_message_id(steer_id), (
        f"steer history must carry a Pan messageId, got {steer_id!r}"
    )

    worker.workers.clear()
    worker._queue_locks.clear()
    _sess._cache.clear()


def test_steer_worker_retries_one_transient_history_save_failure(monkeypatch):
    worker.workers.clear()
    _sess._cache.clear()
    session = _sess.Session(id="ses_steer_retry", name="steer-retry", adapter="codex")
    _sess._cache[session.id] = session
    live = worker.Worker(
        worker_id="worker-steer-retry",
        session_id=session.id,
        adapter=CodexAdapter(),
        process=MagicMock(),
    )
    worker.workers[live.worker_id] = live

    control = AsyncMock(return_value=None)
    monkeypatch.setattr(worker, "send_control_message", control)
    save = AsyncMock(side_effect=[OSError("temporary save failure"), None])
    monkeypatch.setattr(_sess, "save_async", save)

    assert asyncio.run(worker.steer_worker(live.worker_id, "retry this")) is None
    control.assert_awaited_once_with(
        live.worker_id, {"type": "steer", "text": "retry this"},
    )
    assert save.await_count == 2
    assert _no_ts(session.history) == [{"role": "user", "content": "retry this"}]
    # 落盘重试不得重复追加正文，身份也只应分配一次。
    (steer_id,) = _message_ids(session.history)
    assert _sess.is_pan_message_id(steer_id), (
        f"steer history must carry a Pan messageId, got {steer_id!r}"
    )

    worker.workers.clear()
    _sess._cache.clear()
