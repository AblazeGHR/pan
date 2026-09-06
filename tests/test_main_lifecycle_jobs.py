"""Pure registry and mocked-supervisor coverage; no Pan process is started."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.core import background_jobs as jobs
from packages.core import main_lifecycle
import packages.web.server as web_server


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "DEFAULT_ROOT", tmp_path / "registry")
    monkeypatch.setattr(jobs, "PROJECT_ROOT", tmp_path)


def test_legacy_job_json_gets_read_time_kind_defaults(tmp_path):
    path = tmp_path / "legacy"
    jobs._save({"jobId": "job_legacy", "status": "running", "createdAt": 1}, path)
    raw = json.loads((path / "jobs" / "job_legacy.json").read_text(encoding="utf-8"))

    assert "kind" not in raw
    loaded = jobs.get("job_legacy", path)
    assert loaded["kind"] == "background-process"
    assert loaded["operation"] == "run"


def test_legacy_service_job_gets_read_time_empty_options_default(tmp_path):
    path = tmp_path / "legacy-service"
    jobs._save({
        "jobId": "job_legacy-service", "kind": "main-lifecycle",
        "operation": "restart", "createdAt": 1,
        "error": "legacy lifecycle error",
    }, path)

    loaded = jobs.get("job_legacy-service", path)

    assert loaded["options"] == {}
    assert loaded["errors"] == ["legacy lifecycle error"]
    raw = json.loads((path / "jobs" / "job_legacy-service.json").read_text(encoding="utf-8"))
    assert "options" not in raw
    assert "errors" not in raw


def test_service_job_is_durable_atomic_and_has_no_session_target(tmp_path):
    registry = tmp_path / "registry"
    job = jobs.create_service_job(
        request_id="request-1", operation="restart", root=str(tmp_path), port=8765,
        old_pid=41, old_pid_created_at=12.5, registry_root=registry,
    )
    assert job["kind"] == "main-lifecycle"
    assert job["operation"] == "restart"
    assert job["options"] == {}
    assert "targetSessionId" not in job
    assert jobs.get_active_service_job(str(tmp_path), 8765, registry)["jobId"] == job["jobId"]

    for phase in ("stopping", "stopped", "starting", "ready"):
        job = jobs.transition_service_job(job["jobId"], phase, registry_root=registry)
    assert job["phase"] == "ready"
    assert job["status"] == "completed"
    assert jobs.get_active_service_job(str(tmp_path), 8765, registry) is None

    second = jobs.create_service_job(
        request_id="request-2", operation="restart", root=str(tmp_path), port=8765,
        registry_root=registry,
    )
    assert second["requestId"] == "request-2"


def test_duplicate_service_job_is_rejected_from_persisted_registry(tmp_path):
    registry = tmp_path / "registry"
    first = jobs.create_service_job(
        request_id="request-1", operation="restart", root=str(tmp_path), port=8765,
        registry_root=registry,
    )
    with pytest.raises(jobs.ServiceJobBusy) as caught:
        jobs.create_service_job(
            request_id="request-2", operation="restart", root=str(tmp_path), port=8765,
            registry_root=registry,
        )
    assert caught.value.job["jobId"] == first["jobId"]


def test_restart_status_recovers_pending_job_without_memory_state(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("restart_pan.ps1", "stop_pan.bat", "start_pan.bat"):
        (scripts / name).write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(web_server, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(web_server, "_main_restart_pending", False)
    monkeypatch.setattr(web_server, "_main_restart_request_id", None)
    job = jobs.create_service_job(
        request_id="request-after-reload", operation="restart", root=str(tmp_path), port=8768,
        registry_root=tmp_path / "data" / "background_jobs",
    )

    status = web_server._main_restart_status()
    assert status["pending"] is True
    assert status["jobId"] == job["jobId"]
    assert status["requestId"] == "request-after-reload"
    assert status["phase"] == "requested"


def test_ready_checks_reject_old_listener_before_http_health(monkeypatch):
    monkeypatch.setattr(main_lifecycle, "listener_owner", lambda port: 41)
    monkeypatch.setattr(main_lifecycle, "process_create_time", lambda pid: 12.5)
    monkeypatch.setattr(main_lifecycle, "_health_ready", lambda port: pytest.fail("health must not run"))
    result = main_lifecycle.ready_checks(
        root="C:/Pan", port=8765, old_pid=41, old_pid_created_at=12.5,
    )
    assert result["ok"] is False
    assert "old PID" in result["error"]


def test_supervisor_persists_stop_failure(monkeypatch, tmp_path):
    registry = tmp_path / "registry"
    job = jobs.create_service_job(
        request_id="request-stop-fail", operation="restart", root=str(tmp_path), port=8765,
        registry_root=registry,
    )
    monkeypatch.setattr(main_lifecycle, "_run_script", lambda *args, **kwargs: SimpleNamespace(returncode=7))
    result = main_lifecycle.run_supervisor(
        job["jobId"], str(tmp_path), 8765, registry_root=str(registry),
    )
    saved = jobs.get(job["jobId"], registry)
    assert result == 1
    assert saved["phase"] == "failed"
    assert "exit code 7" in saved["error"]


def test_supervisor_persists_ready_only_after_all_checks(monkeypatch, tmp_path):
    registry = tmp_path / "registry"
    job = jobs.create_service_job(
        request_id="request-ready", operation="restart", root=str(tmp_path), port=8765,
        old_pid=41, old_pid_created_at=12.5, registry_root=registry,
    )
    identity_results = iter(({"ok": True}, {"ok": False}))
    monkeypatch.setattr(main_lifecycle, "service_process_identity", lambda *args: next(identity_results))
    monkeypatch.setattr(main_lifecycle, "listener_owner", lambda port: None)
    monkeypatch.setattr(main_lifecycle, "ready_checks", lambda **kwargs: {
        "ok": True, "newPid": 42, "newPidCreatedAt": 13.5,
    })
    monkeypatch.setattr(main_lifecycle, "_run_script", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(main_lifecycle, "READY_POLL_SEC", 0)

    assert main_lifecycle.run_supervisor(
        job["jobId"], str(tmp_path), 8765, 41, 12.5, str(registry),
    ) == 0
    saved = jobs.get(job["jobId"], registry)
    assert saved["phase"] == "ready"
    assert saved["newPid"] == 42
    assert saved["newPidCreatedAt"] == 13.5


def test_exit_supervisor_persists_offline_only_after_verified_stop(monkeypatch, tmp_path):
    registry = tmp_path / "registry"
    job = jobs.create_service_job(
        request_id="request-exit", operation="exit", root=str(tmp_path), port=8765,
        old_pid=41, old_pid_created_at=12.5, registry_root=registry,
    )
    jobs.transition_service_job(job["jobId"], "stopping_workers", registry_root=registry)
    jobs.transition_service_job(job["jobId"], "stopping_service", registry_root=registry)
    identity_results = iter(({"ok": True}, {"ok": False}))
    monkeypatch.setattr(
        main_lifecycle, "service_process_identity",
        lambda *args: next(identity_results),
    )
    monkeypatch.setattr(main_lifecycle, "listener_owner", lambda port: None)
    monkeypatch.setattr(
        main_lifecycle, "_run_script",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(main_lifecycle, "READY_POLL_SEC", 0)

    assert main_lifecycle.run_supervisor(
        job["jobId"], str(tmp_path), 8765, 41, 12.5, str(registry),
    ) == 0
    saved = jobs.get(job["jobId"], registry)
    assert saved["phase"] == "offline"
    assert saved["status"] == "completed"
    assert saved["operation"] == "exit"
    assert saved["options"] == {}


def test_exit_offline_keeps_worker_failure_and_marks_job_partial_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(web_server, "_PROJECT_DIR", tmp_path)
    registry = tmp_path / "data" / "background_jobs"
    job = jobs.create_service_job(
        request_id="request-exit-worker-failure", operation="exit", root=str(tmp_path),
        port=8765, old_pid=41, old_pid_created_at=12.5, registry_root=registry,
    )

    async def failed_worker_shutdown(**kwargs):
        raise RuntimeError("worker shutdown failed: worker-7 did not stop")

    monkeypatch.setattr(web_server.worker, "shutdown_all", failed_worker_shutdown)
    monkeypatch.setattr(web_server, "_main_exit_request_id", job["requestId"])
    monkeypatch.setattr(web_server, "_main_exit_pending", True)
    monkeypatch.setattr(
        web_server, "_launch_main_exit_supervisor", lambda request_id: SimpleNamespace(pid=4242),
    )
    asyncio.run(web_server._perform_main_exit(job["requestId"]))

    recorded = jobs.get(job["jobId"], registry)
    assert recorded["phase"] == "stopping_service"
    assert recorded["error"] == "worker shutdown failed: worker-7 did not stop"

    identity_results = iter(({"ok": True}, {"ok": False}))
    monkeypatch.setattr(
        main_lifecycle, "service_process_identity",
        lambda *args: next(identity_results),
    )
    monkeypatch.setattr(main_lifecycle, "listener_owner", lambda port: None)
    monkeypatch.setattr(
        main_lifecycle, "_run_script",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(main_lifecycle, "READY_POLL_SEC", 0)

    assert main_lifecycle.run_exit_supervisor(
        job["jobId"], str(tmp_path), 8765, 41, 12.5, str(registry),
    ) == 0
    saved = jobs.get(job["jobId"], registry)
    assert saved["phase"] == "offline"
    assert saved["status"] == "failed"
    assert saved["error"] == "worker shutdown failed: worker-7 did not stop"
    assert saved["errors"] == ["worker shutdown failed: worker-7 did not stop"]
    assert web_server._main_restart_job_view(saved)["errors"] == saved["errors"]


@pytest.mark.parametrize(
    ("script_name", "required_scripts", "terminal_phases"),
    [
        ("exit_pan.ps1", ["stop_pan.bat"], {"offline", "failed", "timed_out"}),
        ("restart_pan.ps1", ["stop_pan.bat", "start_pan.bat"], {"ready", "failed", "timed_out"}),
    ],
)
def test_detached_supervisor_persists_runner_start_failure(
    tmp_path, script_name, required_scripts, terminal_phases,
):
    """A PowerShell supervisor failure must not strand its durable Job."""
    source_root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in required_scripts:
        (scripts / name).write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    shutil.copy2(source_root / "scripts" / script_name, scripts / script_name)
    shutil.copy2(
        source_root / "scripts" / "mark_lifecycle_job_failed.ps1",
        scripts / "mark_lifecycle_job_failed.ps1",
    )
    registry = tmp_path / "registry"
    job = jobs.create_service_job(
        request_id="request-runner-start-failure", operation=(
            "exit" if script_name.startswith("exit") else "restart"
        ), root=str(tmp_path), port=8765, registry_root=registry,
    )

    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(scripts / script_name), "-Root", str(tmp_path),
            "-JobId", job["jobId"], "-RegistryRoot", str(registry), "-Port", "8765",
            "-RunnerPython", str(tmp_path / "missing-python.exe"), "-Supervisor",
        ],
        cwd=str(tmp_path), capture_output=True, text=True,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    saved = jobs.get(job["jobId"], registry)
    assert saved["phase"] in terminal_phases
    assert saved["status"] == "failed"
    assert "Python interpreter not found" in saved["error"]
    assert saved["errors"]


@pytest.mark.parametrize(
    ("operation", "endpoint"),
    [("restart", web_server.api_main_restart), ("exit", web_server.api_main_exit)],
)
def test_empty_options_preserves_disabled_request_behavior(tmp_path, monkeypatch, operation, endpoint):
    monkeypatch.setattr(web_server, "_PROJECT_DIR", tmp_path)

    result = asyncio.run(endpoint({"options": {}}))

    assert result["ok"] is False
    assert result["status"] == "disabled"
    assert operation in result["error"]


@pytest.mark.parametrize(
    ("operation", "endpoint"),
    [("restart", web_server.api_main_restart), ("exit", web_server.api_main_exit)],
)
def test_unknown_lifecycle_option_is_rejected_before_any_side_effect(
    tmp_path, monkeypatch, operation, endpoint,
):
    monkeypatch.setattr(web_server, "_PROJECT_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(
        web_server.background_jobs,
        "create_service_job",
        lambda **kwargs: calls.append(kwargs),
    )

    with pytest.raises(web_server.HTTPException) as caught:
        asyncio.run(endpoint({"options": {"drain": True}}))

    assert caught.value.status_code == 400
    assert caught.value.detail["code"] == "unsupported_lifecycle_options"
    assert caught.value.detail["operation"] == operation
    assert caught.value.detail["fields"] == ["drain"]
    assert calls == []


def test_stop_script_checks_identity_variants_and_nonzero_stop_result():
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts" / "stop_pan.bat").read_text(encoding="utf-8")
    assert "python|pythonw|uvicorn" in text
    assert "packages[\\\\/]web[\\\\/]server" in text
    assert "listener on port" in text
    assert "exit /b 1" in text
