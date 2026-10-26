"""Pure registry and mocked-supervisor coverage; no Pan process is started."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.core import background_jobs as jobs
from packages.core import launcher, main_lifecycle
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


def test_restart_job_allows_worker_drain_before_detached_supervisor(tmp_path):
    registry = tmp_path / "registry"
    job = jobs.create_service_job(
        request_id="request-worker-drain", operation="restart", root=str(tmp_path),
        port=8765, registry_root=registry,
    )

    job = jobs.transition_service_job(
        job["jobId"], "stopping_workers", registry_root=registry,
        workerShutdownCompleted=False,
    )
    assert job["phase"] == "stopping_workers"
    assert job["status"] == "running"

    job = jobs.transition_service_job(
        job["jobId"], "stopping_workers", registry_root=registry,
        workerShutdownCompleted=True,
    )
    assert job["workerShutdownCompleted"] is True
    job = jobs.transition_service_job(job["jobId"], "stopping", registry_root=registry)
    assert job["phase"] == "stopping"
    for phase in ("stopped", "starting", "ready"):
        job = jobs.transition_service_job(job["jobId"], phase, registry_root=registry)
    assert job["phase"] == "ready"
    assert job["status"] == "completed"


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


def test_startup_fails_interrupted_pre_handoff_jobs_without_relaunching_supervisor(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(web_server, "_main_restart_port", lambda: 8765)
    for request_id, phase, expected_recovered in (
        ("interrupted-requested", "requested", 1),
        ("interrupted-drain", "stopping_workers", 1),
        ("interrupted-handoff", "stopping", 1),
    ):
        root = tmp_path / request_id
        root.mkdir()
        registry = root / "data" / "background_jobs"
        monkeypatch.setattr(web_server, "_PROJECT_DIR", root)
        job = jobs.create_service_job(
            request_id=request_id, operation="restart", root=str(root), port=8765,
            registry_root=registry,
        )
        if phase != "requested":
            job = jobs.transition_service_job(
                job["jobId"], "stopping_workers", registry_root=registry,
                workerShutdownCompleted=(phase == "stopping"),
            )
        if phase == "stopping":
            job = jobs.transition_service_job(
                job["jobId"], "stopping", registry_root=registry,
                workerShutdownCompleted=True,
            )
        assert web_server._recover_interrupted_main_restart_jobs() == expected_recovered
        saved = jobs.get(job["jobId"], registry)
        assert saved["phase"] == "failed"
        assert "startupPreference" in saved["error"]


def test_ready_checks_reject_old_listener_before_http_health(monkeypatch):
    monkeypatch.setattr(launcher, "listener_owner", lambda port: 41)
    monkeypatch.setattr(launcher, "process_create_time", lambda pid: 12.5)
    monkeypatch.setattr(launcher, "process_identity", lambda *args, **kwargs: {"ok": True})
    result = main_lifecycle.ready_checks(
        root="C:/Pan", port=8765, old_pid=41, old_pid_created_at=12.5,
    )
    assert result["ok"] is False
    assert "old PID" in result["error"]


def test_supervisor_persists_stop_failure(monkeypatch, tmp_path):
    # Historical records remain queryable; old supervisors cannot mutate them
    # or invoke stop/start, including after worker drain or a prior failure.
    registry = tmp_path / "registry"
    job = jobs.create_service_job(request_id="test_supervisor_persists_stop_failure", operation="restart",
        root=str(tmp_path), port=43210, old_pid=41, old_pid_created_at=12.5, registry_root=registry)
    before = jobs.get(job["jobId"], registry)
    files = {path: path.read_bytes() for path in registry.rglob("*.json")}
    def forbidden(*args, **kwargs): pytest.fail("retired supervisor performed a write/effect")
    monkeypatch.setattr(main_lifecycle.launcher, "stop_service", forbidden)
    monkeypatch.setattr(main_lifecycle.launcher, "start_service", forbidden)
    monkeypatch.setattr(jobs, "transition_service_job", forbidden)
    assert main_lifecycle.run_supervisor(job["jobId"], str(tmp_path), 43210, 41, 12.5, str(registry)) == 1
    assert main_lifecycle.legacy_status(job["jobId"], str(registry)) == before
    assert {path: path.read_bytes() for path in registry.rglob("*.json")} == files


def test_supervisor_persists_ready_only_after_all_checks(monkeypatch, tmp_path):
    # Historical records remain queryable; old supervisors cannot mutate them
    # or invoke stop/start, including after worker drain or a prior failure.
    registry = tmp_path / "registry"
    job = jobs.create_service_job(request_id="test_supervisor_persists_ready_only_after_all_checks", operation="restart",
        root=str(tmp_path), port=43210, old_pid=41, old_pid_created_at=12.5, registry_root=registry)
    before = jobs.get(job["jobId"], registry)
    files = {path: path.read_bytes() for path in registry.rglob("*.json")}
    def forbidden(*args, **kwargs): pytest.fail("retired supervisor performed a write/effect")
    monkeypatch.setattr(main_lifecycle.launcher, "stop_service", forbidden)
    monkeypatch.setattr(main_lifecycle.launcher, "start_service", forbidden)
    monkeypatch.setattr(jobs, "transition_service_job", forbidden)
    assert main_lifecycle.run_supervisor(job["jobId"], str(tmp_path), 43210, 41, 12.5, str(registry)) == 1
    assert main_lifecycle.legacy_status(job["jobId"], str(registry)) == before
    assert {path: path.read_bytes() for path in registry.rglob("*.json")} == files


def test_supervisor_starts_after_persisted_worker_drain_phase(monkeypatch, tmp_path):
    # Historical records remain queryable; old supervisors cannot mutate them
    # or invoke stop/start, including after worker drain or a prior failure.
    registry = tmp_path / "registry"
    job = jobs.create_service_job(request_id="test_supervisor_starts_after_persisted_worker_drain_phase", operation="restart",
        root=str(tmp_path), port=43210, old_pid=41, old_pid_created_at=12.5, registry_root=registry)
    before = jobs.get(job["jobId"], registry)
    files = {path: path.read_bytes() for path in registry.rglob("*.json")}
    def forbidden(*args, **kwargs): pytest.fail("retired supervisor performed a write/effect")
    monkeypatch.setattr(main_lifecycle.launcher, "stop_service", forbidden)
    monkeypatch.setattr(main_lifecycle.launcher, "start_service", forbidden)
    monkeypatch.setattr(jobs, "transition_service_job", forbidden)
    assert main_lifecycle.run_supervisor(job["jobId"], str(tmp_path), 43210, 41, 12.5, str(registry)) == 1
    assert main_lifecycle.legacy_status(job["jobId"], str(registry)) == before
    assert {path: path.read_bytes() for path in registry.rglob("*.json")} == files


def test_exit_supervisor_persists_offline_only_after_verified_stop(monkeypatch, tmp_path):
    # Historical records remain queryable; old supervisors cannot mutate them
    # or invoke stop/start, including after worker drain or a prior failure.
    registry = tmp_path / "registry"
    job = jobs.create_service_job(request_id="test_exit_supervisor_persists_offline_only_after_verified_stop", operation="exit",
        root=str(tmp_path), port=43210, old_pid=41, old_pid_created_at=12.5, registry_root=registry)
    before = jobs.get(job["jobId"], registry)
    files = {path: path.read_bytes() for path in registry.rglob("*.json")}
    def forbidden(*args, **kwargs): pytest.fail("retired supervisor performed a write/effect")
    monkeypatch.setattr(main_lifecycle.launcher, "stop_service", forbidden)
    monkeypatch.setattr(main_lifecycle.launcher, "start_service", forbidden)
    monkeypatch.setattr(jobs, "transition_service_job", forbidden)
    assert main_lifecycle.run_exit_supervisor(job["jobId"], str(tmp_path), 43210, 41, 12.5, str(registry)) == 1
    assert main_lifecycle.legacy_status(job["jobId"], str(registry)) == before
    assert {path: path.read_bytes() for path in registry.rglob("*.json")} == files


def test_exit_offline_keeps_worker_failure_and_marks_job_partial_failure(monkeypatch, tmp_path):
    # Historical records remain queryable; old supervisors cannot mutate them
    # or invoke stop/start, including after worker drain or a prior failure.
    registry = tmp_path / "registry"
    job = jobs.create_service_job(request_id="test_exit_offline_keeps_worker_failure_and_marks_job_partial_failure", operation="exit",
        root=str(tmp_path), port=43210, old_pid=41, old_pid_created_at=12.5, registry_root=registry)
    before = jobs.get(job["jobId"], registry)
    files = {path: path.read_bytes() for path in registry.rglob("*.json")}
    def forbidden(*args, **kwargs): pytest.fail("retired supervisor performed a write/effect")
    monkeypatch.setattr(main_lifecycle.launcher, "stop_service", forbidden)
    monkeypatch.setattr(main_lifecycle.launcher, "start_service", forbidden)
    monkeypatch.setattr(jobs, "transition_service_job", forbidden)
    assert main_lifecycle.run_exit_supervisor(job["jobId"], str(tmp_path), 43210, 41, 12.5, str(registry)) == 1
    assert main_lifecycle.legacy_status(job["jobId"], str(registry)) == before
    assert {path: path.read_bytes() for path in registry.rglob("*.json")} == files


@pytest.mark.parametrize(
    ("mark_offline", "expected_offline", "expected_preserved"),
    [(True, ["running-session"], ()), (False, (), ["running-session"])],
)
def test_exit_shutdown_uses_persisted_user_choice_and_snapshot(
    monkeypatch, tmp_path, mark_offline, expected_offline, expected_preserved,
):
    monkeypatch.setattr(web_server, "_PROJECT_DIR", tmp_path)
    registry = tmp_path / "data" / "background_jobs"
    job = jobs.create_service_job(
        request_id=f"request-exit-{mark_offline}", operation="exit", root=str(tmp_path),
        port=8765, old_pid=41, old_pid_created_at=12.5, registry_root=registry,
        options={
            "markRunningSessionsOffline": mark_offline,
            "runningSessionIds": ["running-session"],
        },
    )
    monkeypatch.setattr(web_server, "_main_exit_request_id", job["requestId"])
    calls = []

    async def fake_shutdown(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(web_server.worker, "shutdown_all", fake_shutdown)
    monkeypatch.setattr(web_server, "_launch_main_exit_supervisor", lambda _request: SimpleNamespace(pid=4242))

    asyncio.run(web_server._perform_main_exit(job["requestId"]))

    assert calls == [{
        "mark_legal_offline": True,
        "mark_legal_offline_session_ids": expected_offline,
        "preserve_legal_running_session_ids": expected_preserved,
    }]


def test_compatibility_supervisors_are_launcher_wrappers():
    root = Path(__file__).resolve().parents[1]
    for name in ("exit_pan.ps1", "restart_pan.ps1"):
        text = (root / "scripts" / name).read_text(encoding="utf-8")
        assert "packages.core.main_lifecycle" in text
        assert "stop_pan.bat" not in text


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


def test_stop_batch_is_removed_and_launcher_owns_identity_checks():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "scripts" / "stop_pan.bat").exists()
    text = (root / "packages" / "core" / "launcher.py").read_text(encoding="utf-8")
    assert "createdAt" in text
    assert "taskkill" in text
    assert "_taskkill_verified" in text
