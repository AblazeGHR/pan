"""Tests for worker history persistence and replay behavior.

Verifies the core invariants documented in
docs/architecture/history-replay-analysis.md:

- s.history is ground truth on disk; replay must not corrupt it
- replay events are discarded (not appended, not broadcast)
- user message during replay: _replaying cleared, subsequent assistant
  events append normally
- replay completing without user message: history unchanged

Uses mock cbc process (no real cbc needed).
"""

import asyncio
import json
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


# ── fixtures ──

def _make_event(event_type: str, **fields) -> bytes:
    """Build a cbc stream-json line as bytes."""
    event = {"type": event_type, **fields}
    return (json.dumps(event) + "\n").encode("utf-8")


def _assistant_event(text: str = None, thinking: str = None,
                     tool_name: str = None, tool_input: dict = None) -> bytes:
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    if tool_name:
        content.append({"type": "tool_use", "name": tool_name,
                        "input": tool_input or {}})
    if text:
        content.append({"type": "text", "text": text})
    return _make_event("assistant", message={"role": "assistant", "content": content})


def _user_event(text: str) -> bytes:
    return _make_event("user", message={"role": "user",
                                        "content": [{"type": "text", "text": text}]})


def _result_event(result: str = "ok", is_error: bool = False) -> bytes:
    return _make_event("result", result=result, is_error=is_error)


def _system_init_event(cbc_sid: str = "cbc-123", model: str = "test-model") -> bytes:
    return _make_event("system", subtype="init", session_id=cbc_sid, model=model)


class MockProcess:
    """Mock asyncio.subprocess.Process with controllable stdout + stdin."""

    def __init__(self, events: list[bytes], pid: int = 1000):
        self._events = list(events)
        self._eof = False
        self.returncode = None
        self.pid = pid
        self.stdin = AsyncMock()
        self.stdout = self._async_iter()

    async def _async_iter(self):
        for e in self._events:
            yield e
        # EOF — _read_stdout will then see returncode None (still alive)
        # but in real cbc EOF means exit. For tests we control returncode.

    def kill(self):
        self.returncode = -9


async def _drive_stdout(w: Worker, mock_proc: MockProcess):
    """Run _read_stdout to completion (consumes all queued events)."""
    await worker._read_stdout(w)


def _setup_session(history: list[dict] = None, cli_session_id: str = "cbc-123"):
    """Create a real on-disk session in a temp dir."""
    s = _sess.Session(
        id="ses_test",
        name="test",
        model="test-model",
        adapter_config={"cli_session_id": cli_session_id},
        history=history or [],
    )
    # Bypass file IO for speed — use cache directly
    _sess._cache[s.id] = s
    return s


def _setup_worker(session_id: str, replaying: bool = False):
    """Create a Worker with a mock process (no real CLI)."""
    w = worker.Worker(
        worker_id="worker-test",
        session_id=session_id,
        adapter=CbcAdapter(),
        status="idle",
        process=MagicMock(),
        pending_signal=asyncio.Queue(),
        _replaying=replaying,
    )
    worker.workers[w.worker_id] = w
    return w


def _cleanup():
    worker.workers.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


# ── tests ──

def test_normal_conversation_appends_history():
    """user → assistant(text) → result: history = [user, assistant, result_text]"""
    _cleanup()
    s = _setup_session(history=[])
    w = _setup_worker(s.id, replaying=False)

    events = [
        _system_init_event(),
        _assistant_event(text="hello"),
        _result_event(result="hello"),
    ]
    mock_proc = MockProcess(events)
    w.process = mock_proc

    asyncio.run(_drive_stdout(w, mock_proc))

    assert len(s.history) == 1, f"expected 1 assistant msg, got {s.history}"
    assert s.history[0] == {"role": "assistant", "content": "hello"}
    assert s.last_result["status"] == "done"
    assert s.last_result["result"] == "hello"
    print("PASS: normal conversation appends history")
    _cleanup()


def test_replay_events_do_not_touch_history():
    """Replay (user+assistant+result) with _replaying=True: history unchanged."""
    _cleanup()
    original = [
        {"role": "user", "content": "old q"},
        {"role": "assistant", "content": "old a"},
    ]
    s = _setup_session(history=original)
    w = _setup_worker(s.id, replaying=True)

    events = [
        _system_init_event(),
        _user_event("old q"),
        _assistant_event(text="old a"),
        _result_event(result="old a"),
    ]
    mock_proc = MockProcess(events)
    w.process = mock_proc

    asyncio.run(_drive_stdout(w, mock_proc))

    # History must be unchanged — replay events discarded
    assert s.history == original, f"replay corrupted history: {s.history}"
    # _replaying cleared after result
    assert w._replaying is False, "replay flag not cleared"
    # last_result not set during replay
    assert s.last_result is None, f"last_result set during replay: {s.last_result}"
    print("PASS: replay events do not touch history")
    _cleanup()


def test_replay_events_not_broadcast():
    """Replay events must not be broadcast to dashboard/bot."""
    _cleanup()
    broadcast_calls = []

    async def fake_broadcast(data):
        broadcast_calls.append(data)

    worker.set_broadcaster(fake_broadcast)
    s = _setup_session(history=[])
    w = _setup_worker(s.id, replaying=True)

    events = [
        _system_init_event(),
        _assistant_event(text="old a"),
        _result_event(result="old a"),
    ]
    mock_proc = MockProcess(events)
    w.process = mock_proc

    asyncio.run(_drive_stdout(w, mock_proc))

    # No worker.stream or worker.result broadcasts during replay
    stream_calls = [c for c in broadcast_calls if c.get("type") == "worker.stream"]
    result_calls = [c for c in broadcast_calls if c.get("type") == "worker.result"]
    assert len(stream_calls) == 0, f"replay broadcast stream events: {stream_calls}"
    assert len(result_calls) == 0, f"replay broadcast result events: {result_calls}"
    print("PASS: replay events not broadcast")
    _cleanup()


def test_user_message_during_replay_clears_flag():
    """User sends message during replay: _replaying cleared, history appended.

    This is the key bug fixed in this commit — before, _consumer didn't clear
    _replaying, so subsequent assistant events were skipped and replies lost.
    """
    _cleanup()
    original = [
        {"role": "user", "content": "old q"},
        {"role": "assistant", "content": "old a"},
    ]
    # Copy — s.history will be the same list object, mutations would affect original
    original_snapshot = [dict(m) for m in original]
    s = _setup_session(history=original)
    w = _setup_worker(s.id, replaying=True)

    # Simulate _consumer processing a user message
    asyncio.run(w.pending_signal.put({"text": "new q", "source": "agent"}))

    # Run one _consumer iteration
    async def run_consumer_once():
        # _consumer loops forever; we run it briefly then cancel
        task = asyncio.create_task(worker._consumer(w))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_consumer_once())

    # _replaying must be cleared
    assert w._replaying is False, "_consumer didn't clear _replaying"
    # User message appended to history
    assert s.history[-1] == {"role": "user", "content": "new q"}, \
        f"user message not appended: {s.history}"
    # Original history preserved (compare against snapshot, not the live list)
    assert s.history[:2] == original_snapshot, \
        f"original history lost: {s.history[:2]} vs {original_snapshot}"
    print("PASS: user message during replay clears flag and appends")
    _cleanup()


def test_result_text_not_duplicated_in_history():
    """If result text matches last assistant, don't append duplicate."""
    _cleanup()
    s = _setup_session(history=[])
    w = _setup_worker(s.id, replaying=False)

    events = [
        _assistant_event(text="answer"),
        _result_event(result="answer"),
    ]
    mock_proc = MockProcess(events)
    w.process = mock_proc

    asyncio.run(_drive_stdout(w, mock_proc))

    # Only one assistant "answer" — not duplicated
    assistant_count = sum(1 for h in s.history
                          if h.get("role") == "assistant" and h.get("content") == "answer")
    assert assistant_count == 1, f"result text duplicated: {s.history}"
    print("PASS: result text not duplicated")
    _cleanup()


def test_result_text_appended_when_no_assistant_event():
    """cbc sometimes only gives text in result event — append it."""
    _cleanup()
    s = _setup_session(history=[])
    w = _setup_worker(s.id, replaying=False)

    events = [
        _result_event(result="only in result"),
    ]
    mock_proc = MockProcess(events)
    w.process = mock_proc

    asyncio.run(_drive_stdout(w, mock_proc))

    assert any(h == {"role": "assistant", "content": "only in result"}
               for h in s.history), f"result text not appended: {s.history}"
    print("PASS: result text appended when no assistant event")
    _cleanup()


def test_thinking_and_tool_use_recorded():
    """thinking + tool_use blocks are appended to history."""
    _cleanup()
    s = _setup_session(history=[])
    w = _setup_worker(s.id, replaying=False)

    events = [
        _assistant_event(thinking="hmm", tool_name="bash", tool_input={"cmd": "ls"}),
        _result_event(result="done"),
    ]
    mock_proc = MockProcess(events)
    w.process = mock_proc

    asyncio.run(_drive_stdout(w, mock_proc))

    roles = [h["role"] for h in s.history]
    assert "thinking" in roles, f"thinking not recorded: {s.history}"
    assert "tool" in roles, f"tool_use not recorded: {s.history}"
    print("PASS: thinking and tool_use recorded")
    _cleanup()


if __name__ == "__main__":
    test_normal_conversation_appends_history()
    test_replay_events_do_not_touch_history()
    test_replay_events_not_broadcast()
    test_user_message_during_replay_clears_flag()
    test_result_text_not_duplicated_in_history()
    test_result_text_appended_when_no_assistant_event()
    test_thinking_and_tool_use_recorded()
    print("\n=== ALL TESTS PASSED ===")
