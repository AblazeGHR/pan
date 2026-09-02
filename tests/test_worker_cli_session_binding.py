"""Tests for cli_session_id binding protection in _consumer_oneshot.

Covers the meta-root corruption fix (#bind-override):
1. _extract_cbc_error parses cbc's structured error (resume failure).
2. _consumer_oneshot NEVER overwrites an existing cli_session_id with an
   unrelated captured id.
3. _consumer_oneshot captures cli_session_id when the session has none yet.
4. _consumer_oneshot surfaces cbc's error message instead of "(no output)"
   when resume fails (returncode 0, error event, no result event).
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


class MockProc:
    """Minimal asyncio subprocess mock for _consumer_oneshot's read()/wait()."""

    def __init__(self, output: bytes, returncode: int = 0):
        self.returncode = returncode
        self.pid = 1234
        self.stdin = AsyncMock()
        self.stdout = _Stdout(output)

    async def wait(self):
        return self.returncode

    def kill(self):
        pass


class _Stdout:
    """Reads MockProc output in chunks, then EOF (b'')."""

    def __init__(self, output: bytes):
        self._chunks = [output]

    async def read(self, n: int):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def _setup_session(cli_session_id: str | None = None):
    s = _sess.Session(id="ses_test", name="test", model="test-model", adapter="cbc")
    if cli_session_id:
        s.cli_session_id = cli_session_id
    s.adapter_config["mcp_servers"] = [{"name": "pan", "command": "x"}]
    _sess._cache[s.id] = s
    return s


def _setup_worker(session_id: str):
    w = worker.Worker(
        worker_id="worker-test",
        session_id=session_id,
        adapter=CbcAdapter(),
        status="idle",
        process=None,
        pending_signal=asyncio.Queue(),
        _replaying=False,
    )
    worker.workers[w.worker_id] = w
    return w


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)
    worker._DEFAULTS_INITIALIZED = True  # skip load_worker_config file reads


# ── _extract_cbc_error ──

def test_extract_cbc_error_parses_resume_error():
    out = (b'{"type":"system","subtype":"status","status":null}\n'
           b'{"type":"error","error":"No conversation found with session ID: 58a2baf6-33d2-4163-a8e7-2753c36ed383"}\n')
    assert worker._extract_cbc_error(out) == \
        "No conversation found with session ID: 58a2baf6-33d2-4163-a8e7-2753c36ed383"
    _cleanup()


def test_extract_cbc_error_none_on_normal_output():
    out = (b'{"type":"system","subtype":"init","session_id":"abc"}\n'
           b'{"type":"result","subtype":"success","is_error":false,"result":"ok"}\n')
    assert worker._extract_cbc_error(out) is None
    _cleanup()


def test_extract_cbc_error_ignores_non_json_lines():
    out = b"garbage\n{\"type\":\"error\",\"error\":\"boom\"}\n"
    assert worker._extract_cbc_error(out) == "boom"
    _cleanup()


# ── binding protection ──

def test_consumer_oneshot_keeps_existing_binding_on_mismatch():
    """A captured init session_id that differs from the existing binding must
    NOT overwrite it — the real cbc session id is the binding ground truth."""
    _cleanup()
    s = _setup_session(cli_session_id="existing-good-cbc-id")
    w = _setup_worker(s.id)

    init = b'{"type":"system","subtype":"init","session_id":"totally-different-id"}\n'
    result = b'{"type":"result","subtype":"success","is_error":false,"result":"ok"}\n'

    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=MockProc(init + result))), \
         patch.object(worker, "_bcast", new=AsyncMock()), \
         patch.object(worker._sess, "save_async", new=AsyncMock()):
        asyncio.run(worker._consumer_oneshot(w, "hi", "test", s))

    assert s.cli_session_id == "existing-good-cbc-id", \
        f"existing binding clobbered: {s.cli_session_id}"
    assert s.last_result["status"] == "done"
    assert s.last_result["cli_session_id"] == "existing-good-cbc-id"
    _cleanup()


def test_consumer_oneshot_captures_binding_when_empty():
    """A fresh session (no cli_session_id yet) must capture the init id."""
    _cleanup()
    s = _setup_session(cli_session_id=None)
    w = _setup_worker(s.id)

    init = b'{"type":"system","subtype":"init","session_id":"fresh-cbc-id"}\n'
    result = b'{"type":"result","subtype":"success","is_error":false,"result":"ok"}\n'

    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=MockProc(init + result))), \
         patch.object(worker, "_bcast", new=AsyncMock()), \
         patch.object(worker._sess, "save_async", new=AsyncMock()):
        asyncio.run(worker._consumer_oneshot(w, "hi", "test", s))

    assert s.cli_session_id == "fresh-cbc-id"
    assert s.last_result["status"] == "done"
    _cleanup()


def test_consumer_oneshot_keeps_binding_when_no_init_event():
    """Resume failure (cbc exits 0 with an error event and NO init event) must
    leave the existing binding untouched."""
    _cleanup()
    s = _setup_session(cli_session_id="existing-good-cbc-id")
    w = _setup_worker(s.id)

    err = b'{"type":"error","error":"No conversation found with session ID: existing-good-cbc-id"}\n'

    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=MockProc(err, returncode=0))), \
         patch.object(worker, "_bcast", new=AsyncMock()), \
         patch.object(worker._sess, "save_async", new=AsyncMock()):
        asyncio.run(worker._consumer_oneshot(w, "hi", "test", s))

    assert s.cli_session_id == "existing-good-cbc-id"
    assert s.last_result["status"] == "error"
    assert "No conversation found" in s.last_result["result"], \
        f"resume error not surfaced: {s.last_result['result']!r}"
    _cleanup()


def test_consumer_oneshot_surfaces_cbc_error_on_exit0():
    """Silent exit-0 failure must show cbc's error text, not '(no output)'."""
    _cleanup()
    s = _setup_session(cli_session_id=None)
    w = _setup_worker(s.id)

    err = b'{"type":"error","error":"No conversation found with session ID: 58a2baf6-33d2-4163-a8e7-2753c36ed383"}\n'

    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=MockProc(err, returncode=0))), \
         patch.object(worker, "_bcast", new=AsyncMock()), \
         patch.object(worker._sess, "save_async", new=AsyncMock()):
        asyncio.run(worker._consumer_oneshot(w, "hi", "test", s))

    assert s.last_result["status"] == "error"
    assert "No conversation found" in s.last_result["result"]
    assert s.last_result["result"] != "(no output)"
    _cleanup()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"RUN {t.__name__}...")
        t()
        print(f"PASS {t.__name__}")
