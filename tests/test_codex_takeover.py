"""Regression tests for handing a Codex thread from Pan to its native TUI."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CbcAdapter


def _cleanup() -> None:
    worker.workers.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def test_takeover_holds_worker_without_respawning(monkeypatch):
    """Takeover must stop Pan's writer and leave the worker held, not restart it."""
    _cleanup()
    session = _sess.Session(
        id="ses-takeover",
        name="takeover",
        adapter="cbc",
    )
    session.cli_session_id = "codex-thread-1"
    _sess._cache[session.id] = session
    w = worker.Worker(
        worker_id="worker-takeover",
        session_id=session.id,
        adapter=CbcAdapter(),
        status="idle",
        process=object(),
        pending_signal=asyncio.Queue(),
    )
    worker.workers[w.worker_id] = w
    kill_tree = AsyncMock()
    kill_terminal = AsyncMock()
    spawn = AsyncMock()
    monkeypatch.setattr(worker, "_kill_process_tree", kill_tree)
    monkeypatch.setattr(worker, "_kill_takeover_terminal", kill_terminal)
    monkeypatch.setattr(worker, "_spawn_process", spawn)

    try:
        result = asyncio.run(worker.takeover_worker(w.worker_id))

        assert result is None
        assert worker.get_worker(w.worker_id) is w
        assert w.status == "held"
        assert w.process is None
        kill_tree.assert_awaited_once_with(w)
        spawn.assert_not_awaited()
    finally:
        _cleanup()


def test_kill_process_tree_waits_until_cli_process_exits(tmp_path):
    """Killing a runtime must complete before another client resumes its thread."""
    async def scenario() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            cwd=str(tmp_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        w = worker.Worker(
            worker_id="worker-process-wait",
            session_id="ses-process-wait",
            adapter=CbcAdapter(),
            process=process,
        )
        try:
            await worker._kill_process_tree(w)
            assert process.returncode is not None
            assert not psutil.pid_exists(process.pid)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(scenario())
