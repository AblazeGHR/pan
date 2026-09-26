"""Completed-Job retention settings and safe automatic cleanup regressions."""

import json
import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.core import background_jobs as jobs
from packages.core import config
from packages.jobs import api as jobs_api


@pytest.fixture
def retention_env(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(jobs, "DEFAULT_ROOT", root)
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)
    monkeypatch.delenv("PAN_BACKGROUND_JOBS_DIR", raising=False)
    monkeypatch.delenv("PAN_SCHEDULER_DIR", raising=False)
    monkeypatch.setattr(jobs, "_scheduled_task_hooks", {
        "root_resolver": None, "config_resolver": None, "on_event": None,
    })
    monkeypatch.setattr(jobs, "_completed_retention_hooks", {"on_deleted": None})
    monkeypatch.setattr(jobs_api, "_state", {"broadcast": None})
    return root, config_path


@pytest.fixture
def client(retention_env):
    app = FastAPI()
    app.include_router(jobs_api.router)
    return TestClient(app)


def _write_config(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _record(root, job_id, *, status="completed", updated_at=0,
            kind=jobs.BACKGROUND_PROCESS_KIND, **extra):
    record = {
        "jobId": job_id,
        "kind": kind,
        "status": status,
        "updatedAt": updated_at,
        "name": job_id,
        "description": "",
    }
    record.update(extra)
    return jobs._create(record, registry_root=root)


def test_retention_settings_get_put_defaults_and_preserve_other_config(client, retention_env):
    root, path = retention_env
    initial = client.get("/api/jobs/settings/completed-retention").json()
    assert initial["settings"] == {"enabled": False, "days": 30}
    assert initial["configValid"] is True
    assert initial["lastRun"] is None

    original = {
        "port": 9001,
        "custom": {"kept": [1, 2]},
        "jobs": {
            "unrelated": {"value": "preserve"},
            "completedRetention": {"enabled": False, "days": 7, "future": "keep"},
        },
    }
    _write_config(path, original)
    response = client.put("/api/jobs/settings/completed-retention", json={"enabled": True}).json()
    assert response["ok"] is True
    assert response["settings"] == {"enabled": True, "days": 7}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["port"] == original["port"]
    assert persisted["custom"] == original["custom"]
    assert persisted["jobs"]["unrelated"] == original["jobs"]["unrelated"]
    assert persisted["jobs"]["completedRetention"] == {
        "enabled": True, "days": 7, "future": "keep",
    }
    assert client.get("/api/jobs/settings/completed-retention").json()["settings"] == {
        "enabled": True, "days": 7,
    }


@pytest.mark.parametrize("patch", [
    {"enabled": 1},
    {"enabled": "true"},
    {"days": True},
    {"days": 0},
    {"days": -1},
    {"days": 36501},
    {"days": 1.5},
    {"days": "30"},
    {"unknown": 1},
    {},
])
def test_retention_settings_reject_invalid_updates_without_writing(client, retention_env, patch):
    _, path = retention_env
    original = {"port": 8767, "jobs": {"unrelated": {"keep": True}}}
    _write_config(path, original)
    response = client.put("/api/jobs/settings/completed-retention", json=patch).json()
    assert response["ok"] is False
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_cleanup_uses_exact_status_and_updated_at_cutoff_for_every_kind(retention_env):
    root, _ = retention_env
    now = 2_000_000.0
    cutoff = now - 10 * 86400
    _record(root, "job_old_default", updated_at=cutoff - 1)
    _record(root, "job_cutoff", updated_at=cutoff)
    _record(root, "job_old_scheduled", updated_at=cutoff - 1,
            kind=jobs.SCHEDULED_TASK_KIND)
    _record(root, "job_recent", updated_at=cutoff + 1)
    for index, status in enumerate(("failed", "cancelled", "timed_out", "running")):
        _record(root, f"job_status_{index}", status=status, updated_at=0)
    for index, stamp in enumerate((None, "0", True, math.nan, math.inf, 10 ** 1000)):
        _record(root, f"job_invalid_{index}", updated_at=stamp)
    _record(root, "job_missing_timestamp", **{"updatedAt": None})
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "kept.log").write_text("log", encoding="utf-8")
    (root / "runs.jsonl").write_text("run history\n", encoding="utf-8")

    deleted_events = []
    jobs.register_completed_job_retention(on_deleted=deleted_events.append)
    result = jobs.cleanup_completed_jobs(registry_root=root, retention_days=10, now=now)

    assert result["deleted"] == 3
    assert result["scanned"] == 15
    assert {"job_old_default", "job_cutoff", "job_old_scheduled"} == set(deleted_events)
    for job_id in ("job_old_default", "job_cutoff", "job_old_scheduled"):
        assert jobs.get(job_id, root) is None
    for job_id in ("job_recent", "job_status_0", "job_status_1", "job_status_2", "job_status_3",
                   *(f"job_invalid_{index}" for index in range(6)), "job_missing_timestamp"):
        assert jobs.get(job_id, root) is not None
    assert (root / "logs" / "kept.log").read_text(encoding="utf-8") == "log"
    assert (root / "runs.jsonl").read_text(encoding="utf-8") == "run history\n"


def test_cleanup_rechecks_record_under_lock_before_deleting(retention_env, monkeypatch):
    root, _ = retention_env
    now = 1_000_000.0
    _record(root, "job_race", updated_at=0)
    list_jobs = jobs.list_jobs

    def stale_snapshot(registry_root=None):
        rows = list_jobs(registry_root)
        jobs._update("job_race", {"status": "running", "updatedAt": now},
                     registry_root=registry_root)
        monkeypatch.setattr(jobs, "list_jobs", list_jobs)
        return rows

    monkeypatch.setattr(jobs, "list_jobs", stale_snapshot)
    result = jobs.cleanup_completed_jobs(registry_root=root, retention_days=1, now=now)
    assert result["deleted"] == 0
    assert result["skipped"] == 1
    assert jobs.get("job_race", root)["status"] == "running"


def test_cleanup_keeps_completed_scheduled_shell_parent_with_live_child(retention_env):
    root, _ = retention_env
    _record(root, "job_shell_parent", updated_at=0,
            kind=jobs.SCHEDULED_TASK_KIND,
            action={"api": "shell", "args": {"command": "never run"}})
    _record(root, "job_shell_child", updated_at=0, status="running",
            scheduledParentJobId="job_shell_parent")

    result = jobs.cleanup_completed_jobs(registry_root=root, retention_days=1, now=1_000_000)
    assert result["deleted"] == 0
    assert result["skipped"] == 1
    assert jobs.get("job_shell_parent", root) is not None
    assert jobs.get("job_shell_child", root)["status"] == "running"


def test_automatic_cleanup_is_disabled_by_default_daily_gated_and_hot_reenabled(
        retention_env, monkeypatch):
    root, path = retention_env
    now = 1_000_000.0
    _record(root, "job_first", updated_at=0)
    _write_config(path, {"jobs": {"completedRetention": {"enabled": False, "days": 1}}})

    calls = []
    cleanup = jobs.cleanup_completed_jobs

    def counted_cleanup(**kwargs):
        calls.append(kwargs["now"])
        return cleanup(**kwargs)

    monkeypatch.setattr(jobs, "cleanup_completed_jobs", counted_cleanup)
    disabled = jobs.run_completed_job_retention(now=now)
    assert disabled["deleted"] == 0
    assert calls == []
    assert jobs.get("job_first", root) is not None

    _write_config(path, {"jobs": {"completedRetention": {"enabled": True, "days": 1}}})
    first = jobs.run_completed_job_retention(now=now + 1)
    second = jobs.run_completed_job_retention(now=now + 3600)
    assert first["deleted"] == 1
    assert second["scannedAt"] is None
    assert calls == [now + 1]

    _record(root, "job_after_reenable", updated_at=0)
    _write_config(path, {"jobs": {"completedRetention": {"enabled": False, "days": 1}}})
    jobs.run_completed_job_retention(now=now + 7200)
    assert jobs.get("job_after_reenable", root) is not None
    _write_config(path, {"jobs": {"completedRetention": {"enabled": True, "days": 1}}})
    reenabled = jobs.run_completed_job_retention(now=now + 7201)
    assert reenabled["scannedAt"] is None
    assert calls == [now + 1]
    next_daily_pass = jobs.run_completed_job_retention(now=now + 86401)
    assert next_daily_pass["deleted"] == 1
    assert calls == [now + 1, now + 86401]
    assert jobs.get("job_after_reenable", root) is None


def test_invalid_persisted_retention_config_disables_automatic_deletion(
        client, retention_env):
    root, path = retention_env
    _record(root, "job_invalid_config", updated_at=0)
    _write_config(path, {"jobs": {"completedRetention": {
        "enabled": True, "days": "one",
    }}})

    settings = client.get("/api/jobs/settings/completed-retention").json()
    result = jobs.run_completed_job_retention(now=1_000_000)

    assert settings["configValid"] is False
    assert settings["settings"] == {"enabled": False, "days": 30}
    assert result["deleted"] == 0
    assert jobs.get("job_invalid_config", root) is not None


def test_recovery_cycle_invokes_the_retention_gate(retention_env, monkeypatch):
    import asyncio

    called = []

    async def no_op(*, registry_root=None):
        return 0

    monkeypatch.setattr(jobs, "run_due_message_jobs", no_op)
    monkeypatch.setattr(jobs, "run_due_scheduled_tasks", no_op)
    monkeypatch.setattr(jobs, "reconcile_running", lambda registry_root=None: 0)
    monkeypatch.setattr(jobs, "run_completed_job_retention",
                        lambda: called.append("retention"))

    asyncio.run(jobs.recover_notifications())

    assert called == ["retention"]


def test_cleanup_deletion_emits_job_deleted_through_jobs_api(client, retention_env):
    root, _ = retention_env
    now = 1_000_000.0
    _record(root, "job_event", updated_at=0)
    events = []
    jobs_api.bind(broadcast=events.append)

    result = jobs.cleanup_completed_jobs(registry_root=root, retention_days=1, now=now)

    assert result["deleted"] == 1
    assert events == [{"type": "job.deleted", "jobId": "job_event"}]
