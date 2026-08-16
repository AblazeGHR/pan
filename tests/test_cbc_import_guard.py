"""Tests for cbc import guard (#import-guard).

The reimport flow at /api/cbc/sessions/import can wipe an existing Pan
session when its cli_session_id matches the imported cbc session id but that
cbc session is gone / unparseable. Guards added:

1. Refuse import when the cbc session has no file on disk.
2. Refuse overwriting an existing Pan session when parse yields empty history.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.web import server


def _cleanup():
    _sess._cache.clear()
    _sess._all_loaded = False
    worker.workers.clear()
    worker.set_broadcaster(None)


def _fresh_session_dir() -> Path:
    """Point _sess at a temp dir so real data/sessions/ is never touched."""
    tmp = Path(tempfile.mkdtemp()) / "sessions"
    tmp.mkdir(parents=True, exist_ok=True)
    _sess.SESSION_DIR = tmp
    return tmp


def test_import_refuses_missing_cbc_session():
    """A cbc session id with no file on disk must be refused, not imported."""
    _cleanup()
    _fresh_session_dir()
    missing_id = "58a2baf6-33d2-4163-a8e7-2753c36ed383"

    with patch.object(server, "broadcast", new=AsyncMock()), \
         patch("packages.core.adapters.cbc.sessions._resolve_session_file",
               return_value=None):
        resp = asyncio.run(server.api_cbc_sessions_import(
            {"session_id": missing_id, "cwd": "D:/tmp/nonexistent"}))

    assert "not found on disk" in str(resp), f"unexpected response: {resp}"
    # No session may be created from a phantom cbc id
    assert _sess._cache == {}, f"phantom id created a session: {_sess._cache}"
    _cleanup()


def test_import_refuses_empty_history_overwrite():
    """Matching an existing session but getting empty history must not wipe it."""
    _cleanup()
    _fresh_session_dir()

    # Existing Pan session bound to a (now broken) cbc id
    s = _sess.Session(id="ses_existing", name="meta-root",
                      model="test-model", adapter="cbc")
    s.cli_session_id = "58a2baf6-33d2-4163-a8e7-2753c36ed383"
    s.history = [{"role": "user", "content": "你好，你可以正常使用pan mcp服务吗"}]
    _sess._cache[s.id] = s

    empty_jsonl = Path(tempfile.mkdtemp()) / "58a2baf6-33d2-4163-a8e7-2753c36ed383.jsonl"
    empty_jsonl.write_text("", encoding="utf-8")

    with patch.object(server, "broadcast", new=AsyncMock()), \
         patch("packages.core.adapters.cbc.sessions._resolve_session_file",
               return_value=empty_jsonl), \
         patch("packages.core.adapters.cbc.sessions.parse_cbc_history",
               return_value=[]):
        resp = asyncio.run(server.api_cbc_sessions_import(
            {"session_id": "58a2baf6-33d2-4163-a8e7-2753c36ed383",
             "cwd": "D:/tmp/nonexistent"}))

    assert "refusing to overwrite" in str(resp), f"unexpected response: {resp}"
    # Existing history must be untouched
    assert s.history == [{"role": "user", "content": "你好，你可以正常使用pan mcp服务吗"}], \
        f"existing history was wiped: {s.history}"
    _cleanup()


def test_import_valid_cbc_session_creates_new():
    """A real cbc session with parseable history still imports normally."""
    _cleanup()
    _fresh_session_dir()

    valid_id = "eef3fa71-36e3-4fba-9"
    tmp = Path(tempfile.mkdtemp())
    jsonl = tmp / f"{valid_id}.jsonl"
    ev = {"type": "message", "role": "user", "sessionId": valid_id,
          "content": [{"type": "text", "text": "hi"}],
          "timestamp": 1786800000000}
    jsonl.write_text(json.dumps(ev, ensure_ascii=False) + "\n", encoding="utf-8")

    with patch.object(server, "broadcast", new=AsyncMock()), \
         patch("packages.core.adapters.cbc.sessions._resolve_session_file",
               return_value=jsonl):
        resp = asyncio.run(server.api_cbc_sessions_import(
            {"session_id": valid_id, "cwd": "D:/tmp/valid"}))

    assert "error" not in resp, f"valid import failed: {resp}"
    # A new session was created with the imported id
    assert any(s.cli_session_id == valid_id for s in _sess.list_all()), \
        f"imported session not created: {_sess._cache}"
    _cleanup()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"RUN {t.__name__}...")
        t()
        print(f"PASS {t.__name__}")
