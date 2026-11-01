"""Restart API regression tests for the Python launcher supervisor."""

import asyncio
import sys
from pathlib import Path

import pytest

import packages.core.worker as worker
import packages.web.server as srv
from tests import test_lifecycle_canonical_preparation as canonical_preparation
from tests.test_lifecycle_canonical_preparation import prepared  # noqa: F401
from packages.core import background_jobs


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, '_canonical_lifecycle_pending', {})
    monkeypatch.setattr(srv, '_lifecycle_operation_lock', asyncio.Lock())
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(background_jobs, "DEFAULT_ROOT", tmp_path / "registry")
    monkeypatch.setattr(background_jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(srv, "_main_restart_pending", False)
    monkeypatch.setattr(srv, "_main_restart_request_id", None)
    monkeypatch.setattr(srv, "_main_restart_worker_shutdown_lock", asyncio.Lock())
    monkeypatch.setattr(worker, "_shutdown_started", False)


def test_restart_is_disabled_only_off_platform(monkeypatch):
    status = srv._main_restart_status()
    if sys.platform == "win32":
        assert status["available"] is True
    else:
        assert status["available"] is False


def test_restart_returns_scheduled_before_python_supervisor_finishes(prepared):
    # Original API capability is verified through its canonical protected intent.
    canonical_preparation.test_concurrent_exit_and_restart_share_one_worker_preparation(prepared)


def test_restart_ask_requires_explicit_choice_before_any_side_effect(prepared):
    # Original API capability is verified through its canonical protected intent.
    canonical_preparation.test_ask_without_choice_rejects_before_admission_or_worker_gate(prepared, 'restart')


@pytest.mark.parametrize(
    ("strategy", "choice", "expected"),
    [
        ("ask", True, True),
        ("ask", False, False),
        ("offline", False, True),
        ("preserve-running", True, False),
    ],
)
def test_restart_freezes_configured_policy_and_running_snapshot(prepared, strategy, choice, expected):
    # Original API capability is verified through its canonical protected intent.
    canonical_preparation.test_freeze_before_gate_and_worker_shutdown_before_supervisor(
        prepared, 'restart', strategy, choice, expected)


def test_restart_drains_workers_before_single_supervisor_handoff(monkeypatch):
    registry = srv._main_restart_registry_root()
    job = background_jobs.create_service_job(
        request_id="restart-order", operation="restart", root=str(srv._PROJECT_DIR),
        port=8765, old_pid=41, old_pid_created_at=12.5, registry_root=registry,
        options={
            "exitStrategy": "ask",
            "markRunningSessionsOffline": False,
            "runningSessionIds": ["running-session"],
        },
    )
    monkeypatch.setattr(srv, "_main_restart_request_id", job["requestId"])
    monkeypatch.setattr(srv, "_main_restart_pending", True)
    events = []

    async def shutdown(**kwargs):
        saved = background_jobs.get(job["jobId"], registry)
        assert saved["phase"] == "stopping_workers"
        assert saved["workerShutdownCompleted"] is False
        events.append(("shutdown-start", kwargs))
        await asyncio.sleep(0)
        events.append(("shutdown-complete", None))

    def launch(request_id):
        assert request_id == job["requestId"]
        saved = background_jobs.get(job["jobId"], registry)
        assert saved["phase"] == "stopping"
        assert saved["workerShutdownCompleted"] is True
        events.append(("supervisor", None))
        return type("FakeProcess", (), {"pid": 4242})()

    monkeypatch.setattr(worker, "shutdown_all", shutdown)
    monkeypatch.setattr(srv, "_launch_main_restart_supervisor", launch)

    async def perform_twice_concurrently():
        await asyncio.gather(
            srv._perform_main_restart(job["requestId"]),
            srv._perform_main_restart(job["requestId"]),
        )
        await srv._perform_main_restart(job["requestId"])

    asyncio.run(perform_twice_concurrently())

    assert [event[0] for event in events] == [
        "shutdown-start", "shutdown-complete", "supervisor",
    ]
    assert events[0][1] == {
        "mark_legal_offline": True,
        "mark_legal_offline_session_ids": (),
        "preserve_legal_running_session_ids": ["running-session"],
        "legal_state_source": "pan/main-restart",
    }
    saved = background_jobs.get(job["jobId"], registry)
    assert saved["phase"] == "stopping"
    assert saved["workerShutdownCompleted"] is True


def test_restart_supervisor_failure_is_durable_and_does_not_repeat_worker_shutdown(
    monkeypatch,
):
    registry = srv._main_restart_registry_root()
    job = background_jobs.create_service_job(
        request_id="restart-launch-failure", operation="restart", root=str(srv._PROJECT_DIR),
        port=8765, registry_root=registry,
        options={
            "markRunningSessionsOffline": True,
            "runningSessionIds": ["running-session"],
        },
    )
    monkeypatch.setattr(srv, "_main_restart_request_id", job["requestId"])
    monkeypatch.setattr(srv, "_main_restart_pending", True)
    reopened = []
    monkeypatch.setattr(worker, "reopen_after_failed_shutdown", lambda: reopened.append(True))
    shutdown_calls = []

    async def shutdown(**kwargs):
        shutdown_calls.append(kwargs)

    monkeypatch.setattr(worker, "shutdown_all", shutdown)
    launch_calls = []

    def fail_launch(request_id):
        launch_calls.append(request_id)
        raise OSError("detached launcher unavailable")

    monkeypatch.setattr(srv, "_launch_main_restart_supervisor", fail_launch)
    asyncio.run(srv._perform_main_restart(job["requestId"]))
    asyncio.run(srv._perform_main_restart(job["requestId"]))

    saved = background_jobs.get(job["jobId"], registry)
    assert saved["phase"] == "failed"
    assert "detached launcher unavailable" in saved["error"]
    assert len(shutdown_calls) == 1
    assert launch_calls == [job["requestId"]]
    assert reopened == [True]


def test_restart_worker_shutdown_failure_does_not_launch_supervisor(monkeypatch):
    registry = srv._main_restart_registry_root()
    job = background_jobs.create_service_job(
        request_id="restart-worker-failure", operation="restart",
        root=str(srv._PROJECT_DIR), port=8765, registry_root=registry,
        options={
            "markRunningSessionsOffline": False,
            "runningSessionIds": ["running-session"],
        },
    )
    monkeypatch.setattr(srv, "_main_restart_request_id", job["requestId"])
    monkeypatch.setattr(srv, "_main_restart_pending", True)
    reopened = []
    monkeypatch.setattr(worker, "reopen_after_failed_shutdown", lambda: reopened.append(True))

    async def failed_shutdown(**kwargs):
        raise RuntimeError("legal state persistence failed")

    monkeypatch.setattr(worker, "shutdown_all", failed_shutdown)
    monkeypatch.setattr(
        srv, "_launch_main_restart_supervisor",
        lambda _request_id: pytest.fail("must not hand off after Worker failure"),
    )

    asyncio.run(srv._perform_main_restart(job["requestId"]))

    saved = background_jobs.get(job["jobId"], registry)
    assert saved["phase"] == "failed"
    assert "legal state persistence failed" in saved["error"]
    assert reopened == [True]


def test_restart_supervisor_launcher_keeps_detached_python_command(monkeypatch):
    registry = srv._main_restart_registry_root()
    job = background_jobs.create_service_job(
        request_id="restart-launch-command", operation="restart",
        root=str(srv._PROJECT_DIR), port=8765, old_pid=41,
        old_pid_created_at=12.5, registry_root=registry,
    )
    monkeypatch.setattr(
        srv.launcher, "resolve_python_argv",
        lambda *_args, **_kwargs: ([sys.executable], "test"),
    )
    calls = []

    class FakeProcess:
        pid = 4242

    monkeypatch.setattr(
        srv.subprocess, "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)) or FakeProcess(),
    )

    process = srv._launch_main_restart_supervisor(job["requestId"])

    assert process.pid == 4242
    command = calls[0][0]
    assert command[:6] == ["cmd.exe", "/d", "/c", "start", "", "/b"]
    assert "-m" in command
    assert "packages.core.main_lifecycle" in command
    assert "--supervise" in command
    assert "--old-pid" in command
    assert "stop_pan.bat" not in " ".join(command)


def test_persisted_duplicate_restart_is_rejected_without_spawn(prepared):
    canonical_preparation.test_lost_memory_still_fences_duplicate_or_cross_operation(prepared, 'restart')


def test_restart_supervisor_and_start_entry_have_no_batch_business_chain():
    root = Path(__file__).resolve().parents[1]
    lifecycle = (root / "packages" / "core" / "main_lifecycle.py").read_text(encoding="utf-8")
    start = (root / "scripts" / "start_pan.bat").read_text(encoding="utf-8").lower()
    assert "stop_pan.bat" not in lifecycle
    assert "start_pan.bat" not in lifecycle
    assert "packages.core.launcher" in start
    assert "powershell" not in start
    assert "taskkill" not in start


def test_restart_compatibility_wrapper_only_delegates_to_python():
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts" / "restart_pan.ps1").read_text(encoding="utf-8")
    assert "packages.core.main_lifecycle" in text
    assert "stop_pan.bat" not in text
    assert "start_pan.bat" not in text
