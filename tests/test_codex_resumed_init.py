"""Resumed history must not overflow the bounded Worker init channel."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from packages.core import worker
from packages.core.session import Session
from packages.core.adapters.codex import CodexAdapter
from packages.core.adapters.codex.app_server_wrapper import AppServer


def test_large_resumed_thread_init_survives_real_worker_framing(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        "packages.core.adapters.codex.app_server_wrapper._write_stdout", emitted.append
    )
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    app.thread_id = "old-thread"
    thread = {"id": app.thread_id, "turns": [{"text": "中文历史" * 100000}]}
    app._thread_resume_info = {"thread": thread}
    original = json.dumps(thread, ensure_ascii=False)
    app._emit_thread_started(thread)
    app._emit_thread_started(thread)
    assert len(emitted) == 1
    wire = (json.dumps(emitted[0], ensure_ascii=False) + "\n").encode()

    async def framed():
        stream = asyncio.StreamReader()
        stream.feed_data(wire)
        stream.feed_eof()
        w = SimpleNamespace(process=SimpleNamespace(stdout=stream), worker_id="probe")
        return [line async for line in worker._iter_stdout_lines(w)]

    lines = asyncio.run(framed())
    event = CodexAdapter().parse_event(lines[0].decode("utf-8", errors="replace"))
    assert event is not None, "bounded stdout discarded resumed init JSON"
    assert CodexAdapter().is_init_event(event)
    assert CodexAdapter().extract_session_id(event) == "old-thread"
    assert len(wire) < 1024
    assert "turns" not in event and "thread" not in event
    # Native resume retains its full independent history; no source mutation.
    assert app._thread_resume_info["thread"] is thread
    assert json.dumps(thread, ensure_ascii=False) == original


def test_resumed_init_marks_worker_ready_without_replaying_queue_or_history(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        "packages.core.adapters.codex.app_server_wrapper._write_stdout", emitted.append
    )
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    app.thread_id = "retained-old-thread"
    app._emit_thread_started({"id": app.thread_id, "turns": [{"text": "x" * 1000000}]})
    s = Session(id="ses-resumed-init", name="old", adapter="codex")
    s.cli_session_id = app.thread_id
    s.history = [{"role": "user", "content": "retained history"}]
    s.queue_pending = [{"type": "task", "text": "queued work", "seq": 1}]
    before = json.dumps([s.history, s.queue_pending])
    save = AsyncMock()
    monkeypatch.setitem(worker._sess._cache, s.id, s)
    monkeypatch.setattr(worker._sess, "save_async", save)
    monkeypatch.setattr(worker, "_bcast", AsyncMock())
    monkeypatch.setattr(worker, "workers", {})

    async def read():
        stream = asyncio.StreamReader()
        stream.feed_data((json.dumps(emitted[0]) + "\n").encode())
        stream.feed_eof()
        w = worker.Worker("init-probe", s.id, CodexAdapter(),
                          process=SimpleNamespace(stdout=stream, returncode=0))
        worker.workers[w.worker_id] = w
        assert not w.native_kernel_ready
        await worker._read_stdout(w)
        assert w.native_kernel_ready

    asyncio.run(read())
    save.assert_awaited_once_with(s)
    assert s.cli_session_id == app.thread_id
    assert json.dumps([s.history, s.queue_pending]) == before
