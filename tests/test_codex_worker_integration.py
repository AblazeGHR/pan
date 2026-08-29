"""Codex adapter + worker stream integration regressions.

The process is a protocol-shaped fake, so the test never calls a model.  It
still exercises the real worker stdout path, including Codex event parsing,
history/result persistence, and task queue acknowledgement.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters.codex import CodexAdapter


class _CodexProcess:
    def __init__(self, result: str):
        self._chunks = [
            self._line({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": result}]},
            }),
            self._line({"type": "result", "is_error": False, "result": result}),
        ]
        self.returncode = 0
        self.pid = 1001
        self.stdout = self

    @staticmethod
    def _line(event: dict) -> bytes:
        return (json.dumps(event) + "\n").encode("utf-8")

    async def read(self, _size: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    worker._inflight_task_ids.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def test_codex_stream_results_keep_the_current_task_sequence(monkeypatch):
    """两个连续 Codex stream 回合各自绑定当前 taskSeq，不串号。"""
    _cleanup()
    monkeypatch.setattr(_sess, "save_async", AsyncMock())
    session = _sess.Session(
        id="ses-codex-stream",
        name="codex-stream",
        adapter="codex",
        model="gpt-5.4-mini",
    )
    _sess._cache[session.id] = session

    try:
        for index, (seq, result) in enumerate(((11, "first"), (12, "second"))):
            task = {
                "type": "task", "id": f"task-{seq}", "text": f"job-{seq}",
                "source": "user", "seq": seq, "taskId": f"tid-{seq}",
            }
            session.queue_pending = [task]
            current = worker.Worker(
                worker_id=f"worker-codex-{index}",
                session_id=session.id,
                adapter=CodexAdapter(),
                status="running",
                process=_CodexProcess(result),
                pending_signal=asyncio.Queue(),
            )
            current._current_seq = seq
            current._current_task_id = task["taskId"]
            current._current_queue_item = task
            worker.workers[current.worker_id] = current

            asyncio.run(worker._read_stdout(current))

            assert session.last_result["status"] == "done"
            assert session.last_result["result"] == result
            assert session.last_result["taskSeq"] == seq
            assert session.queue_pending == []
    finally:
        _cleanup()

