"""Regression tests for handing a Codex thread from Pan to its native TUI."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import psutil
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CbcAdapter
from packages.core.adapters.codex import CodexAdapter


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


@pytest.mark.parametrize("operation", ["restart", "kill"])
def test_takeover_stop_failure_preserves_owner_and_blocks_spawn(monkeypatch, operation):
    _cleanup()
    class Owner:
        def stop(self):
            raise TimeoutError("writer still active")
    owner = Owner()
    w = worker.Worker(worker_id="owned", session_id="isolated", adapter=CodexAdapter(),
                      status="held", takeover_pid=123, takeover_job=owner)
    worker.workers[w.worker_id] = w
    spawn = AsyncMock()
    monkeypatch.setattr(worker, "_spawn_process", spawn)
    try:
        action = worker.restart_worker if operation == "restart" else worker.kill_worker
        error = asyncio.run(action(w.worker_id))
        assert "writer still active" in error
        assert w.status == "held"
        assert w.takeover_job is owner and w.takeover_pid == 123
        assert worker.get_worker(w.worker_id) is w
        spawn.assert_not_awaited()
    finally:
        _cleanup()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
@pytest.mark.parametrize("close_terminal", [True, False])
@pytest.mark.parametrize("operation", ["restart", "kill"])
def test_job_stops_writer_even_after_terminal_exit(tmp_path, close_terminal, operation, monkeypatch):
    """Real isolated process tree; no Codex state or running Pan service."""
    import time
    from packages.core.takeover_job import TakeoverJob
    marker = tmp_path / "child.pid"
    script = tmp_path / "terminal.py"
    script.write_text(
        "import subprocess, sys, pathlib, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))\n"
        + ("" if close_terminal else "time.sleep(30)\n"), encoding="utf-8")
    job = TakeoverJob()
    pid = job.launch([sys.executable, str(script)], cwd=str(tmp_path))
    w = worker.Worker(worker_id="job", session_id="isolated", adapter=CodexAdapter(),
                      status="held", takeover_pid=pid, takeover_job=job)
    _cleanup()
    worker.workers[w.worker_id] = w
    monkeypatch.setattr(worker, "_record_legal_worker_state", AsyncMock())
    monkeypatch.setattr(worker, "_restart_tasks", AsyncMock())
    async def spawn(*args, **kwargs):
        assert w.takeover_job is None
        child.wait(timeout=2)
        assert not child.is_running() or child.status() == psutil.STATUS_DEAD
        return object()
    spawn_mock = AsyncMock(side_effect=spawn)
    monkeypatch.setattr(worker, "_spawn_process", spawn_mock)
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() or (close_terminal and psutil.pid_exists(pid)):
            assert time.monotonic() < deadline
            time.sleep(.05)
        child = psutil.Process(int(marker.read_text()))
        assert child.is_running()
        action = worker.restart_worker if operation == "restart" else worker.kill_worker
        assert asyncio.run(action(w.worker_id)) is None
        child.wait(timeout=2)  # Windows may report None for a vanished PID.
        assert not child.is_running() or child.status() == psutil.STATUS_DEAD
        assert w.takeover_job is None and w.takeover_pid is None
        if operation == "restart":
            spawn_mock.assert_awaited_once()
            assert w.status == "idle"
        else:
            spawn_mock.assert_not_awaited()
            assert worker.get_worker(w.worker_id) is None
    finally:
        if job.handle:
            job.stop()
        _cleanup()


def test_terminal_launch_rejects_superseded_takeover(monkeypatch):
    from packages.web import server
    _cleanup()
    w = worker.Worker(worker_id="race", session_id="isolated", adapter=CodexAdapter(), status="idle")
    worker.workers[w.worker_id] = w
    opened = []
    monkeypatch.setattr(server, "_open_terminal", lambda *a, **kw: opened.append(a))
    try:
        with pytest.raises(OSError, match="superseded"):
            asyncio.run(server._open_takeover_terminal(w, "unused", Path.cwd(), w.generation))
        w.status = "held"
        with pytest.raises(OSError, match="superseded"):
            asyncio.run(server._open_takeover_terminal(w, "unused", Path.cwd(), w.generation - 1))
        assert not opened
    finally:
        _cleanup()


@pytest.mark.parametrize("session_route", [True, False])
def test_takeover_route_restart_during_broadcast_does_not_open_writer(monkeypatch, session_route):
    from packages.web import server
    _cleanup()
    s = _sess.Session(id="route-race", name="isolated", adapter="codex")
    s.cli_session_id = "fake-thread-no-runtime"
    _sess._cache[s.id] = s
    w = worker.Worker(worker_id="route-worker", session_id=s.id, adapter=CodexAdapter(), status="idle")
    worker.workers[w.worker_id] = w
    async def hold(_):
        w.status = "held"
    async def hold_session(_):
        await hold(_)
        return w
    monkeypatch.setattr(worker, "takeover_worker", hold)
    monkeypatch.setattr(worker, "takeover_session_worker", hold_session)
    async def restart_during_broadcast(_):
        w.status = "idle"
        worker._bump_worker_generation(w)
    monkeypatch.setattr(server, "broadcast", restart_during_broadcast)
    opened = []
    monkeypatch.setattr(server, "_open_terminal", lambda *a, **kw: opened.append(a))
    try:
        route = server.api_session_takeover if session_route else server.api_takeover
        result = asyncio.run(route(s.id if session_route else w.worker_id))
        assert "superseded" in result["error"]
        assert not opened
    finally:
        _cleanup()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
def test_job_assignment_failure_never_resumes_terminal(tmp_path, monkeypatch):
    from packages.core.takeover_job import TakeoverJob
    import subprocess
    job = TakeoverJob()
    processes = []
    original = subprocess.Popen
    def capture(*args, **kwargs):
        proc = original(*args, **kwargs)
        processes.append(proc)
        return proc
    monkeypatch.setattr(subprocess, "Popen", capture)
    monkeypatch.setattr(job.api, "AssignProcessToJobObject", lambda *args: False)
    marker = tmp_path / "must-not-run"
    try:
        with pytest.raises(OSError):
            job.launch([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"])
        assert processes[0].returncode is not None
        assert not marker.exists()
    finally:
        job.stop()


def test_concurrent_restarts_wait_for_takeover_stop_and_spawn_once(monkeypatch):
    _cleanup()
    w = worker.Worker(worker_id="serialized", session_id="isolated", adapter=CodexAdapter(), status="held")
    worker.workers[w.worker_id] = w
    monkeypatch.setattr(worker, "_record_legal_worker_state", AsyncMock())
    monkeypatch.setattr(worker, "_restart_tasks", AsyncMock())
    monkeypatch.setattr(worker, "_kill_process_tree", AsyncMock())
    spawn = AsyncMock(return_value=object())
    monkeypatch.setattr(worker, "_spawn_process", spawn)
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def stop(_):
            entered.set()
            await release.wait()
            w.takeover_job = None
        monkeypatch.setattr(worker, "_kill_takeover_terminal", stop)
        w.takeover_job = object()
        first = asyncio.create_task(worker.restart_worker(w.worker_id))
        await entered.wait()
        second = asyncio.create_task(worker.restart_worker(w.worker_id))
        await asyncio.sleep(0)
        spawn.assert_not_awaited()
        release.set()
        assert await first is None
        assert await second is None
        spawn.assert_awaited_once()
    try:
        asyncio.run(scenario())
    finally:
        _cleanup()
