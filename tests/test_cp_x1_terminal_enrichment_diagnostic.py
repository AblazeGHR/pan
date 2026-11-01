"""Source-bound diagnostic for terminal publication versus blocked enrichment."""
import asyncio
import contextlib
import threading

from packages.core import session as session_module, worker
from tests import test_terminal_broadcast as original


def test_terminal_finishes_while_enrichment_is_held(monkeypatch, tmp_path):
    original._reset()
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path / "sessions")
    session, process_worker = original._stream_setup("ses_cp_x1_gate")
    process_worker._execution_seq = 2
    entered, release = threading.Event(), threading.Event()
    events = []
    idle_published = asyncio.Event()

    def held_enrichment(_snapshot):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("diagnostic enrichment gate was not released")

    async def broadcast(event):
        events.append(event)
        if event.get("type") == "worker.status" and event.get("status") == "idle":
            idle_published.set()

    monkeypatch.setattr(process_worker.adapter, "enrich_after_result", held_enrichment)
    worker.set_broadcaster(broadcast)
    class OpenProcess(original._Process):
        async def read(self, size=65536):
            if self._chunks:
                return await super().read(size)
            await asyncio.Event().wait()

    process_worker.process = OpenProcess([
        original._line({"type": "system", "subtype": "init", "session_id": "cli-t041"}),
        original._line({"type": "assistant", "message": {"content": [{"type": "text", "text": "answer"}]}}),
        original._line({"type": "result", "result": "answer", "is_error": False}),
    ])

    async def scenario():
        reader = asyncio.create_task(worker._read_stdout(process_worker))
        try:
            await asyncio.wait_for(idle_published.wait(), 5)
            assert not reader.done(), "the fake stream must remain open after idle"
            assert await asyncio.to_thread(entered.wait, 5)
            assert not release.is_set()
            result = next(i for i, event in enumerate(events) if event.get("type") == "worker.result")
            idle = next(i for i, event in enumerate(events) if event.get("type") == "worker.status" and event.get("status") == "idle")
            assert result < idle
            assert events[result]["executionSeq"] == events[idle]["executionSeq"] == 2
            assert session.last_result["executionSeq"] == 2
            task = worker._usage_enrichment_tasks[session.id]
            assert not task.done()
            release.set()
            await asyncio.wait_for(task, 5)
            assert not session.usage_enrichment_pending
        finally:
            release.set()
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader

    try:
        asyncio.run(scenario())
    finally:
        original._reset()
