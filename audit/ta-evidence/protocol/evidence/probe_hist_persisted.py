"""Read-only probe for the `_hist_persisted` incremental cursor scope.

Uses the REAL persistence writer against a temporary SESSION_DIR so the JSONL
mirror and the in-process cursor can be compared directly.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess  # noqa: E402
from packages.core import worker  # noqa: E402
from packages.core.adapters.codex import CodexAdapter  # noqa: E402


class _FakeProcess:
    def __init__(self, events: list[dict]):
        blob = b"".join(
            (json.dumps(e, ensure_ascii=False) + "\n").encode("utf-8") for e in events
        )
        self._blob = blob
        self._pos = 0
        self.returncode = 0
        self.pid = 4242
        self.stdout = self

    async def read(self, _size: int = -1) -> bytes:
        if self._pos >= len(self._blob):
            return b""
        chunk = self._blob[self._pos:self._pos + 65536]
        self._pos += len(chunk)
        return chunk


def _jsonl_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _run(events: list[dict], tmp: Path, sid: str) -> dict:
    _sess.SESSION_DIR = tmp
    worker.workers.clear()
    worker._task_status.clear()
    worker._inflight_task_ids.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)

    session = _sess.Session(id=sid, name=sid, adapter="codex")
    _sess._cache[sid] = session

    broadcasts: list[dict] = []

    async def capture(data):
        broadcasts.append(data)

    worker.set_broadcaster(capture)
    current = worker.Worker(
        worker_id="worker-hist",
        session_id=sid,
        adapter=CodexAdapter(),
        status="running",
        process=_FakeProcess(events),
        pending_signal=asyncio.Queue(),
    )
    current._current_seq = 5
    current._current_task_id = "task-5"
    current._current_task_idempotent = True
    worker.workers[current.worker_id] = current
    asyncio.run(worker._read_stdout(current))

    hist_path = tmp / f"{sid}.history.jsonl"
    rows = _jsonl_rows(hist_path)
    terminal = [b for b in broadcasts if b.get("type") == "worker.result"]
    result = {
        "in_memory_history": [r.get("content") for r in session.history],
        "in_memory_len": len(session.history),
        "hist_persisted": getattr(session, "_hist_persisted", None),
        "history_revision": session.history_revision,
        "jsonl_rows": [r.get("content") for r in rows],
        "jsonl_len": len(rows),
        "terminal_historyRevision": terminal[0].get("historyRevision") if terminal else None,
    }
    # Reload from disk to confirm the mirror is authoritative after restart.
    _sess._cache.clear()
    reloaded = _sess.get(sid)
    result["reloaded_history"] = [r.get("content") for r in (reloaded.history if reloaded else [])]
    result["reloaded_hist_persisted"] = getattr(reloaded, "_hist_persisted", None)
    return result


def main() -> int:
    adapter = CodexAdapter()
    report = {}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        report["normal"] = _run([
            {"type": "assistant", "final": True, "item_id": "a1",
             "message": {"content": [{"type": "text", "text": "A"}]}},
            {"type": "result", "is_error": False, "result": "A"},
        ], tmp, "ses-hist-normal")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        report["tool_last"] = _run([
            {"type": "assistant", "final": True, "item_id": "a1",
             "message": {"content": [{"type": "text", "text": "A"}]}},
            {"type": "assistant", "final": True, "item_id": "t1",
             "message": {"content": [{"type": "tool_use", "name": "Command",
                                      "input": {"command": "ls"}}]}},
            {"type": "result", "is_error": False, "result": "A"},
        ], tmp, "ses-hist-tool")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        report["late_after_result"] = _run([
            {"type": "assistant", "final": True, "item_id": "a1",
             "message": {"content": [{"type": "text", "text": "partial"}]}},
            {"type": "result", "is_error": False, "result": "final answer"},
            {"type": "assistant", "final": True, "item_id": "a2",
             "message": {"content": [{"type": "text", "text": "final answer"}]}},
        ], tmp, "ses-hist-late")

    target = sys.argv[1] if len(sys.argv) > 1 else None
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if target:
        Path(target).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
