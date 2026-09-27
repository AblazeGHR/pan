"""Exit API and internal graceful-shutdown regression tests."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import packages.core.worker as worker
import packages.web.server as srv
from packages.core import background_jobs, launcher


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(background_jobs, "DEFAULT_ROOT", tmp_path / "registry")
    monkeypatch.setattr(background_jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(srv, "_main_exit_pending", False)
    monkeypatch.setattr(srv, "_main_exit_request_id", None)
    monkeypatch.setattr(srv, "_main_exit_stage", "idle")
    monkeypatch.setattr(srv, "_main_exit_error", None)
    monkeypatch.setattr(srv, "_session_lifecycle_preferences", lambda config=None: {
        "exitStrategy": "ask", "startupPreference": "ask",
    })
    monkeypatch.setattr(worker, "_shutdown_started", False)
    monkeypatch.setattr(srv.sess, "list_all", lambda **kwargs: [])


def test_exit_status_is_available_from_python_launcher_on_windows():
    status = srv._main_exit_status()
    assert status["available"] is (sys.platform == "win32")


def test_exit_job_records_choice_and_exact_pre_exit_running_snapshot(monkeypatch):
    monkeypatch.setattr(srv, "_main_exit_status", lambda: {
        "available": True, "pending": False, "port": 8765,
    })
    monkeypatch.setattr(srv, "_main_restart_status", lambda: {"pending": False})
    monkeypatch.setattr(srv.main_lifecycle, "listener_owner", lambda port: 41)
    monkeypatch.setattr(srv.main_lifecycle, "process_create_time", lambda pid: 12.5)
    monkeypatch.setattr(srv.sess, "list_all", lambda **kwargs: [
        SimpleNamespace(id="running-b", last_legal_worker_state="running"),
        SimpleNamespace(id="idle", last_legal_worker_state="idle"),
        SimpleNamespace(id="running-a", last_legal_worker_state="running"),
    ])
    created_tasks = []

    def capture_task(coro, **kwargs):
        created_tasks.append(kwargs.get("name"))
        coro.close()

    monkeypatch.setattr(srv.asyncio, "create_task", capture_task)
    result = asyncio.run(srv.api_main_exit({
        "options": {"markRunningSessionsOffline": False},
    }))

    job = background_jobs.find_service_job(
        result["requestId"], srv._main_restart_registry_root(),
    )
    assert result["ok"] is True
    assert job["options"] == {
        "exitStrategy": "ask",
        "markRunningSessionsOffline": False,
        "runningSessionIds": ["running-a", "running-b"],
    }
    assert created_tasks == ["pan-main-exit"]
    assert worker._shutdown_started is True


def test_exit_ask_requires_explicit_choice_before_job_or_worker_gate(monkeypatch):
    monkeypatch.setattr(srv, "_main_exit_status", lambda: {
        "available": True, "pending": False, "port": 8765,
    })
    monkeypatch.setattr(srv, "_main_restart_status", lambda: {"pending": False})
    monkeypatch.setattr(srv.sess, "list_all", lambda **kwargs: pytest.fail("must reject before snapshot"))
    monkeypatch.setattr(worker, "begin_shutdown", lambda: pytest.fail("must not close worker gate"))
    monkeypatch.setattr(srv.asyncio, "create_task", lambda *args, **kwargs: pytest.fail("must not schedule"))

    with pytest.raises(srv.HTTPException) as caught:
        asyncio.run(srv.api_main_exit())

    assert caught.value.status_code == 400
    assert caught.value.detail["code"] == "lifecycle_choice_required"
    assert background_jobs.list_jobs(srv._main_restart_registry_root()) == []


def test_legacy_ask_exit_job_without_choice_fails_before_worker_or_legal_state_changes(
    monkeypatch,
):
    registry = srv._main_restart_registry_root()
    job = background_jobs.create_service_job(
        request_id="legacy-exit-missing-choice", operation="exit",
        root=str(srv._PROJECT_DIR), port=8765, registry_root=registry,
        options={"exitStrategy": "ask", "runningSessionIds": ["running-session"]},
    )
    monkeypatch.setattr(srv, "_main_exit_pending", True)
    monkeypatch.setattr(srv, "_main_exit_request_id", job["requestId"])
    monkeypatch.setattr(worker, "_shutdown_started", True)
    shutdown_calls = []
    supervisor_calls = []
    reopened = []
    monkeypatch.setattr(
        worker, "shutdown_all", lambda **kwargs: shutdown_calls.append(kwargs),
    )
    monkeypatch.setattr(
        srv, "_launch_main_exit_supervisor", lambda request_id: supervisor_calls.append(request_id),
    )

    def reopen_gate():
        reopened.append(True)
        worker._shutdown_started = False

    monkeypatch.setattr(worker, "reopen_after_failed_shutdown", reopen_gate)

    asyncio.run(srv._perform_main_exit(job["requestId"]))

    saved = background_jobs.get(job["jobId"], registry)
    assert saved["phase"] == "failed"
    assert "missing an explicit markRunningSessionsOffline choice" in saved["error"]
    assert shutdown_calls == []
    assert supervisor_calls == []
    assert reopened == [True]
    assert worker._shutdown_started is False
    assert srv._main_exit_pending is False


@pytest.mark.parametrize(
    ("strategy", "legacy_choice", "expected_mark_offline"),
    [
        ("ask", True, True),
        ("ask", False, False),
        ("offline", False, True),
        ("preserve-running", True, False),
    ],
)
def test_exit_policy_is_frozen_in_job_and_overrides_legacy_payload(
    monkeypatch, strategy, legacy_choice, expected_mark_offline,
):
    monkeypatch.setattr(srv, "_session_lifecycle_preferences", lambda config=None: {
        "exitStrategy": strategy, "startupPreference": "ask",
    })
    monkeypatch.setattr(srv, "_main_exit_status", lambda: {
        "available": True, "pending": False, "port": 8765,
    })
    monkeypatch.setattr(srv, "_main_restart_status", lambda: {"pending": False})
    monkeypatch.setattr(srv.main_lifecycle, "listener_owner", lambda _port: 41)
    monkeypatch.setattr(srv.main_lifecycle, "process_create_time", lambda _pid: 12.5)
    monkeypatch.setattr(srv.sess, "list_all", lambda **_kwargs: [
        SimpleNamespace(id="running-session", last_legal_worker_state="running"),
    ])
    monkeypatch.setattr(srv.asyncio, "create_task", lambda coro, **_kwargs: coro.close())

    result = asyncio.run(srv.api_main_exit({
        "options": {"markRunningSessionsOffline": legacy_choice},
    }))
    job = background_jobs.find_service_job(
        result["requestId"], srv._main_restart_registry_root(),
    )

    assert job["options"] == {
        "exitStrategy": strategy,
        "markRunningSessionsOffline": expected_mark_offline,
        "runningSessionIds": ["running-session"],
    }


def test_exit_rejects_non_boolean_running_state_choice():
    with pytest.raises(srv.HTTPException) as caught:
        srv._parse_main_lifecycle_options(
            {"options": {"markRunningSessionsOffline": "no"}}, "exit",
        )
    assert caught.value.status_code == 400
    assert caught.value.detail["code"] == "invalid_lifecycle_options"


def test_exit_schedules_worker_shutdown_and_python_supervisor(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("detached Windows supervisor contract")
    monkeypatch.setattr(launcher, "resolve_python_argv", lambda *args, **kwargs: ([sys.executable], "test"))
    monkeypatch.setattr(srv.main_lifecycle, "listener_owner", lambda port: 41)
    monkeypatch.setattr(srv.main_lifecycle, "process_create_time", lambda pid: 12.5)
    calls = []

    async def fake_shutdown_all(**kwargs):
        calls.append(("shutdown", kwargs))

    class FakeProcess:
        pid = 4242

    monkeypatch.setattr(worker, "shutdown_all", fake_shutdown_all)
    monkeypatch.setattr(srv.subprocess, "Popen", lambda command, **kwargs: calls.append(("spawn", command)) or FakeProcess())
    result = asyncio.run(srv.api_main_exit({
        "options": {"markRunningSessionsOffline": True},
    }))
    asyncio.run(asyncio.sleep(0))
    assert result["ok"] is True
    assert result["status"] == "scheduled"
    assert worker._shutdown_started is True
    assert calls[0] == ("shutdown", {
        "mark_legal_offline": True,
        "mark_legal_offline_session_ids": [],
        "preserve_legal_running_session_ids": (),
    })
    command = calls[1][1]
    assert "packages.core.main_lifecycle" in command
    assert "--supervise" in command
    assert "stop_pan.bat" not in " ".join(command)


def test_duplicate_exit_is_rejected_without_second_request(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("detached Windows supervisor contract")
    monkeypatch.setattr(srv, "_main_exit_pending", True)
    monkeypatch.setattr(srv, "_main_exit_request_id", "already-exiting")
    result = asyncio.run(srv.api_main_exit())
    assert result["ok"] is False
    assert result["status"] == "busy"
    assert result["requestId"] == "already-exiting"


def test_internal_shutdown_requests_uvicorn_lifespan_exit():
    server = SimpleNamespace(should_exit=False)
    srv.app.state.pan_uvicorn_server = server
    result = asyncio.run(srv.api_internal_main_shutdown())
    assert result == {"ok": True, "status": "stopping"}
    assert server.should_exit is True
    srv.app.state.pan_uvicorn_server = None


def test_exit_wrapper_is_launcher_only():
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts" / "exit_pan.ps1").read_text(encoding="utf-8")
    assert "packages.core.main_lifecycle" in text
    assert "stop_pan.bat" not in text
    assert "restart_pan.ps1" not in text
