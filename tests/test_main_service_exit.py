"""Mock-only tests for legal Pan main-service exit.

No test in this file starts, stops, or connects to a Pan service.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.core.worker as worker  # noqa: E402
import packages.web.server as srv  # noqa: E402
from packages.core.adapters.cbc import CbcAdapter  # noqa: E402
from packages.core import background_jobs, session as sess  # noqa: E402


def _fake_exit_script(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "exit_pan.ps1").write_text("# test placeholder", encoding="utf-8")
    (scripts / "stop_pan.bat").write_text("@echo off", encoding="utf-8")


@pytest.fixture(autouse=True)
def reset_exit_state(monkeypatch):
    monkeypatch.setattr(srv, "_main_exit_pending", False)
    monkeypatch.setattr(srv, "_main_exit_request_id", None)
    monkeypatch.setattr(srv, "_main_exit_stage", "idle")
    monkeypatch.setattr(srv, "_main_exit_error", None)
    monkeypatch.setattr(worker, "_shutdown_started", False)
    worker.workers.clear()
    worker._workers_by_session.clear()
    sess._cache.clear()
    yield
    worker.workers.clear()
    worker._workers_by_session.clear()
    sess._cache.clear()


def test_exit_status_is_disabled_when_stop_only_supervisor_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    status = asyncio.run(srv.api_main_exit_status())
    assert status["available"] is False
    assert status["pending"] is False
    assert "missing" in status["reason"] or "Windows" in status["reason"]


def test_duplicate_exit_is_rejected_without_a_second_request(tmp_path, monkeypatch):
    _fake_exit_script(tmp_path)
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    if os.name != "nt":
        pytest.skip("Windows detached supervisor is intentionally disabled on POSIX")
    monkeypatch.setattr(srv, "_main_exit_pending", True)
    monkeypatch.setattr(srv, "_main_exit_request_id", "already-exiting")
    result = asyncio.run(srv.api_main_exit())
    assert result["ok"] is False
    assert result["status"] == "busy"
    assert result["pending"] is True
    assert result["requestId"] == "already-exiting"


def test_exit_schedules_worker_shutdown_and_stop_only_supervisor(tmp_path, monkeypatch):
    _fake_exit_script(tmp_path)
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    if os.name != "nt":
        pytest.skip("Windows detached supervisor is intentionally disabled on POSIX")

    calls = []

    async def fake_shutdown_all(**kwargs):
        calls.append(("shutdown", kwargs))

    class FakeProcess:
        pid = 4242

    async def run_request():
        monkeypatch.setattr(worker, "shutdown_all", fake_shutdown_all)
        monkeypatch.setattr(
            srv, "_launch_main_exit_supervisor",
            lambda request_id: calls.append(("supervisor", request_id)) or FakeProcess(),
        )
        result = await srv.api_main_exit()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return result

    result = asyncio.run(run_request())
    assert result["ok"] is True
    assert result["status"] == "scheduled"
    assert result["accepted"] is True
    assert result["phase"] == "requested"
    assert result["jobId"]
    assert worker._shutdown_started is True
    assert calls[0] == ("shutdown", {"mark_legal_offline": True})
    assert calls[1][0] == "supervisor"


def test_exit_writes_offline_only_after_runtime_is_confirmed_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path)
    session = sess.create("exit-worker")

    class Process:
        def __init__(self, returncode):
            self.returncode = returncode

    w = worker.Worker(
        worker_id="exit-worker-runtime",
        session_id=session.id,
        adapter=CbcAdapter(),
        status="idle",
        process=Process(None),
        pending_signal=asyncio.Queue(),
    )
    worker.workers[w.worker_id] = w
    worker._register_worker(w)
    monkeypatch.setattr(worker, "_kill_process_tree", lambda _w: asyncio.sleep(0))

    asyncio.run(worker.shutdown_all(mark_legal_offline=True))
    assert session.last_legal_worker_state is None

    # A second, independently confirmed stopped runtime is allowed to record
    # offline.  This covers the positive branch without starting a process.
    session2 = sess.create("exit-worker-confirmed")
    w2 = worker.Worker(
        worker_id="exit-worker-confirmed-runtime",
        session_id=session2.id,
        adapter=CbcAdapter(),
        status="idle",
        process=Process(0),
        pending_signal=asyncio.Queue(),
    )
    worker.workers[w2.worker_id] = w2
    worker._register_worker(w2)
    asyncio.run(worker.shutdown_all(mark_legal_offline=True))
    assert session2.last_legal_worker_state == "offline"


def test_exit_supervisor_is_stop_only_and_checkout_scoped():
    script = Path(__file__).resolve().parent.parent / "scripts" / "exit_pan.ps1"
    text = script.read_text(encoding="utf-8")
    assert "stop_pan.bat" in text
    assert "restart_pan.ps1" not in text
    assert "start_pan.bat" not in text
    assert "-Supervisor" in text
    assert "main.py" in text


def test_exit_status_recovers_persisted_job_without_memory_state(tmp_path, monkeypatch):
    _fake_exit_script(tmp_path)
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    registry = tmp_path / "data" / "background_jobs"
    job = background_jobs.create_service_job(
        request_id="request-exit-after-reload", operation="exit",
        root=str(tmp_path), port=8768, registry_root=registry,
    )
    monkeypatch.setattr(srv, "_main_exit_pending", False)
    monkeypatch.setattr(srv, "_main_exit_request_id", None)
    status = asyncio.run(srv.api_main_exit_status())
    assert status["pending"] is True
    assert status["jobId"] == job["jobId"]
    assert status["phase"] == "requested"
