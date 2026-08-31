"""Mock stream Worker test for Claude's input/output contract."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters.claude import adapter as claude_adapter
from packages.core.adapters.claude.adapter import ClaudeAdapter


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None


class _FakeStdout:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self._blocked = asyncio.Event()

    async def read(self, _size: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        await self._blocked.wait()
        return b""


class _FakeStreamProcess:
    returncode = None
    pid = 7777

    def __init__(self, chunks: list[bytes]) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(chunks)


def _line(event: dict) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


def test_claude_stream_worker_writes_envelope_and_completes(monkeypatch):
    worker.workers.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)
    s = _sess.Session(id="ses_claude_stream", name="claude-stream", adapter="claude")
    _sess._cache[s.id] = s
    adapter = ClaudeAdapter()
    adapter.enrich_after_result = lambda session: None
    proc = _FakeStreamProcess([
        _line({
            "type": "system", "subtype": "init",
            "session_id": "claude-stream-1", "model": "sonnet",
        }),
        _line({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "你好，Pan"}]},
        }),
        _line({
            "type": "result", "is_error": False,
            "session_id": "claude-stream-1", "result": "你好，Pan",
        }),
    ])
    w = worker.Worker(
        worker_id="worker-claude-stream",
        session_id=s.id,
        adapter=adapter,
        process=proc,
        pending_signal=asyncio.Queue(),
        _task_done=asyncio.Event(),
    )
    worker.workers[w.worker_id] = w
    events: list[dict] = []

    async def fake_save(_session) -> None:
        return None

    async def broadcast(event: dict) -> None:
        events.append(event)

    async def run() -> None:
        monkeypatch.setattr(_sess, "save_async", fake_save)
        monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)
        worker.set_broadcaster(broadcast)
        s.history.append({"role": "user", "content": "你好"})
        reader = asyncio.create_task(worker._read_stdout(w))
        await worker._consumer_stream(w, "你好", "user", s)
        assert json.loads(proc.stdin.writes[0].decode("utf-8")) == {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "你好"}],
            },
        }
        assert s.cli_session_id == "claude-stream-1"
        assert s.model == "sonnet"
        assert s.last_result["status"] == "done"
        assert s.last_result["result"] == "你好，Pan"
        assert any(
            event["type"] == "worker.stream"
            and event["event"].get("type") == "assistant"
            for event in events
        )
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        claude_adapter._PENDING_RESULT_USAGE.clear()
        worker.workers.clear()
        _sess._cache.clear()
        worker.set_broadcaster(None)
