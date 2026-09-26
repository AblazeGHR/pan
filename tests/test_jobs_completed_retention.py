"""Completed-Job retention settings and safe automatic cleanup regressions."""

import json
import math
import os
import sys
from contextlib import contextmanager

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


def test_independent_retention_rules_persist_without_touching_other_config(
        client, retention_env):
    _, path = retention_env
    original = {
        "port": 8767,
        "custom": {"kept": True},
        "jobs": {"other": {"value": 4}},
    }
    _write_config(path, original)

    response = client.put("/api/jobs/settings/completed-retention", json={"rules": {
        "failed": {"enabled": True, "days": 6},
        "timed_out": {"enabled": False, "days": 9},
        "cancelled": {"enabled": True, "days": 12},
        "logs": {"enabled": True, "days": 20},
    }}).json()

    assert response["ok"] is True
    assert response["rules"]["failed"] == {"enabled": True, "days": 6}
    assert response["rules"]["timed_out"] == {"enabled": False, "days": 9}
    assert response["rules"]["completed"] == {"enabled": False, "days": 30}
    assert response["configValid"] is True
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["port"] == original["port"]
    assert persisted["custom"] == original["custom"]
    assert persisted["jobs"]["other"] == original["jobs"]["other"]
    assert persisted["jobs"]["failedRetention"] == {"enabled": True, "days": 6}
    assert persisted["jobs"]["timedOutRetention"] == {"enabled": False, "days": 9}
    assert persisted["jobs"]["cancelledRetention"] == {"enabled": True, "days": 12}
    assert persisted["jobs"]["logFileRetention"] == {"enabled": True, "days": 20}


@pytest.mark.parametrize("rules", [
    {"failed": {"enabled": 1, "days": 1}},
    {"timed_out": {"enabled": True, "days": True}},
    {"cancelled": {"enabled": "true", "days": 4}},
    {"failed": {"enabled": False, "days": 0}},
    {"timed_out": {"enabled": True, "days": 36501}},
    {"cancelled": {"enabled": True, "days": 1.5}},
    {"logs": {"enabled": True, "days": "30"}},
    {"unknown": {"enabled": True, "days": 2}},
    {"logs": {"enabled": True, "days": 2, "extra": True}},
    {"failed": {}},
])
def test_independent_retention_rules_reject_invalid_updates_without_writing(
        client, retention_env, rules):
    _, path = retention_env
    original = {"port": 8767, "jobs": {"unrelated": {"keep": True}}}
    _write_config(path, original)

    response = client.put("/api/jobs/settings/completed-retention",
                          json={"rules": rules}).json()

    assert response["ok"] is False
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_persisted_invalid_rule_is_disabled_independently(client, retention_env):
    root, path = retention_env
    _record(root, "job_failed_invalid_days", status="failed", updated_at=0)
    _record(root, "job_timeout_valid", status="timed_out", updated_at=0)
    _write_config(path, {"jobs": {
        "failedRetention": {"enabled": True, "days": True},
        "timedOutRetention": {"enabled": True, "days": 1},
    }})

    response = client.get("/api/jobs/settings/completed-retention").json()
    result = jobs.run_completed_job_retention(now=1_000_000)

    assert response["configValidity"]["failed"] is False
    assert response["rules"]["failed"] == {"enabled": False, "days": 30}
    assert response["configValidity"]["timed_out"] is True
    assert jobs.get("job_failed_invalid_days", root) is not None
    assert jobs.get("job_timeout_valid", root) is None
    assert result["rules"]["timed_out"]["deleted"] == 1


def test_each_rule_has_its_own_daily_gate_and_newly_enabled_rule_runs_hot(
        retention_env, monkeypatch):
    root, path = retention_env
    now = 1_000_000.0
    _record(root, "job_failed_first", status="failed", updated_at=0)
    _write_config(path, {"jobs": {
        "failedRetention": {"enabled": True, "days": 1},
        "timedOutRetention": {"enabled": False, "days": 1},
    }})
    original_cleaner = jobs.cleanup_jobs_by_statuses
    calls = []

    def counted_cleaner(**kwargs):
        calls.append(dict(kwargs["retention_days_by_status"]))
        return original_cleaner(**kwargs)

    monkeypatch.setattr(jobs, "cleanup_jobs_by_statuses", counted_cleaner)
    first = jobs.run_completed_job_retention(now=now)
    assert first["rules"]["failed"]["deleted"] == 1
    assert calls == [{"failed": 1}]

    _record(root, "job_timeout_hot_enable", status="timed_out", updated_at=0)
    _write_config(path, {"jobs": {
        "failedRetention": {"enabled": True, "days": 1},
        "timedOutRetention": {"enabled": True, "days": 1},
    }})
    hot_enabled = jobs.run_completed_job_retention(now=now + 3600)
    same_day = jobs.run_completed_job_retention(now=now + 7200)

    assert calls == [{"failed": 1}, {"timed_out": 1}]
    assert hot_enabled["rules"]["timed_out"]["deleted"] == 1
    assert hot_enabled["rules"].get("failed") is None
    assert same_day["scannedAt"] is None
    assert jobs.get("job_timeout_hot_enable", root) is None


def test_status_rules_use_independent_exact_status_and_updated_at_cutoffs(retention_env):
    root, _ = retention_env
    now = 4_000_000.0
    _record(root, "job_failed_cutoff", status="failed", updated_at=now - 1 * 86400)
    _record(root, "job_timeout_cutoff", status="timed_out", updated_at=now - 2 * 86400)
    _record(root, "job_cancelled_cutoff", status="cancelled", updated_at=now - 3 * 86400)
    _record(root, "job_failed_too_recent", status="failed", updated_at=now - 86400 + 1)
    _record(root, "job_timeout_only", status="timed_out", updated_at=0)
    _record(root, "job_other_terminal", status="scheduled", updated_at=0)

    results = jobs.cleanup_jobs_by_statuses(
        registry_root=root,
        retention_days_by_status={"failed": 1, "timed_out": 2, "cancelled": 3},
        now=now,
    )

    assert {key: value["deleted"] for key, value in results.items()} == {
        "failed": 1, "timed_out": 2, "cancelled": 1,
    }
    for job_id in ("job_failed_cutoff", "job_timeout_cutoff", "job_cancelled_cutoff"):
        assert jobs.get(job_id, root) is None
    for job_id in ("job_failed_too_recent", "job_other_terminal"):
        assert jobs.get(job_id, root) is not None

    _record(root, "job_failed_exact_only", status="failed", updated_at=0)
    _record(root, "job_timeout_not_failed", status="timed_out", updated_at=0)
    failed_only = jobs.cleanup_jobs_by_statuses(
        registry_root=root, retention_days_by_status={"failed": 1}, now=now)
    assert failed_only["failed"]["deleted"] == 1
    assert jobs.get("job_failed_exact_only", root) is None
    assert jobs.get("job_timeout_not_failed", root) is not None


def _set_mtime(path, timestamp):
    os.utime(path, (timestamp, timestamp))


def test_log_cleanup_is_independent_safe_and_only_removes_expired_owned_logs(
        retention_env, monkeypatch):
    root, _ = retention_env
    now = 1_000_000.0
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    expired = logs / "job_expired.log"
    expired.write_text("old", encoding="utf-8")
    _set_mtime(expired, now - 86400)
    recent = logs / "job_recent.log"
    recent.write_text("new", encoding="utf-8")
    _set_mtime(recent, now - 86400 + 1)
    running_log = logs / "job_running.log"
    running_log.write_text("active", encoding="utf-8")
    _set_mtime(running_log, now - 10 * 86400)
    starting_log = logs / "job_starting.log"
    starting_log.write_text("starting", encoding="utf-8")
    _set_mtime(starting_log, now - 10 * 86400)
    scheduled_log = logs / "job_scheduled.log"
    scheduled_log.write_text("scheduled", encoding="utf-8")
    _set_mtime(scheduled_log, now - 10 * 86400)
    active_runner_log = logs / "job_active_runner.log"
    active_runner_log.write_text("runner", encoding="utf-8")
    _set_mtime(active_runner_log, now - 10 * 86400)
    _record(root, "job_expired", status="completed", updated_at=0,
            logPath=str(expired))
    _record(root, "job_recent", status="failed", updated_at=0, logPath=str(recent))
    _record(root, "job_running", status="running", updated_at=0,
            logPath=str(running_log))
    _record(root, "job_starting", status="starting", updated_at=0,
            logPath=str(starting_log))
    _record(root, "job_scheduled", status="scheduled", updated_at=0,
            logPath=str(scheduled_log))
    _record(root, "job_active_runner", status="completed", updated_at=0,
            logPath=str(active_runner_log), runnerPid=4321,
            runnerProcessCreatedAt=100.0)
    (logs / "unowned.log").write_text("unknown owner", encoding="utf-8")
    (logs / "unowned.txt").write_text("not a log", encoding="utf-8")
    nested = logs / "nested"
    nested.mkdir()
    (nested / "job_nested.log").write_text("nested", encoding="utf-8")
    external = root.parent / "outside.log"
    external.write_text("outside", encoding="utf-8")
    _record(root, "job_external", status="completed", updated_at=0,
            logPath=str(external))
    runs = root / "runs.jsonl"
    runs.write_text("run history\n", encoding="utf-8")

    class ActiveProcess:
        def create_time(self):
            return 100.0

        def is_running(self):
            return True

        def status(self):
            return "running"

    class Psutil:
        STATUS_ZOMBIE = "zombie"
        NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        ZombieProcess = type("ZombieProcess", (Exception,), {})

        @staticmethod
        def Process(pid):
            assert pid == 4321
            return ActiveProcess()

    monkeypatch.setitem(sys.modules, "psutil", Psutil)
    result = jobs.cleanup_job_log_files(registry_root=root, retention_days=1, now=now)

    assert result["deleted"] == 2
    assert not expired.exists()
    assert not scheduled_log.exists()
    for path in (recent, running_log, starting_log, active_runner_log, logs / "unowned.log",
                 logs / "unowned.txt", nested, external):
        assert path.exists()
    assert jobs.get("job_expired", root) is not None
    assert jobs.get("job_active_runner", root) is not None
    assert jobs.get("job_scheduled", root) is not None
    assert runs.read_text(encoding="utf-8") == "run history\n"


def test_log_cleanup_rechecks_owner_status_under_job_lock(retention_env, monkeypatch):
    root, _ = retention_env
    now = 1_000_000.0
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "job_log_race.log"
    log_path.write_text("keep after transition", encoding="utf-8")
    _set_mtime(log_path, now - 10 * 86400)
    _record(root, "job_log_race", status="completed", updated_at=0,
            logPath=str(log_path))
    original_lock = jobs._job_lock
    changed = False

    @contextmanager
    def change_before_lock(job_id, registry_root=None):
        nonlocal changed
        if job_id == "job_log_race" and not changed:
            changed = True
            path = jobs._job_path(job_id, registry_root)
            record = json.loads(path.read_text(encoding="utf-8"))
            record.update(status="running", updatedAt=now)
            path.write_text(json.dumps(record), encoding="utf-8")
        with original_lock(job_id, registry_root):
            yield

    monkeypatch.setattr(jobs, "_job_lock", change_before_lock)
    result = jobs.cleanup_job_log_files(registry_root=root, retention_days=1, now=now)

    assert result["deleted"] == 0
    assert result["skipped"] == 1
    assert log_path.exists()
    assert jobs.get("job_log_race", root)["status"] == "running"


def test_logs_and_job_record_retention_are_independent_and_failures_are_isolated(
        retention_env, monkeypatch):
    root, path = retention_env
    now = 1_000_000.0
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    expired = logs / "job_log_only.log"
    expired.write_text("old", encoding="utf-8")
    _set_mtime(expired, now - 10 * 86400)
    _record(root, "job_log_only", status="failed", updated_at=0,
            logPath=str(expired))
    _write_config(path, {"jobs": {
        "failedRetention": {"enabled": True, "days": 1},
        "cancelledRetention": {"enabled": False, "days": 1},
        "logFileRetention": {"enabled": True, "days": 1},
    }})

    original_cleaner = jobs.cleanup_jobs_by_statuses
    calls = []

    def fail_status_cleaner(**kwargs):
        calls.append(kwargs["retention_days_by_status"])
        raise OSError("status rule scan failed")

    monkeypatch.setattr(jobs, "cleanup_jobs_by_statuses", fail_status_cleaner)
    result = jobs.run_completed_job_retention(now=now)

    assert calls == [{"failed": 1}]
    assert result["rules"]["failed"]["errorCount"] == 1
    assert result["rules"]["logs"]["deleted"] == 1
    assert not expired.exists()
    assert jobs.get("job_log_only", root) is not None
    status = jobs.completed_job_retention_status()["lastRuns"]
    assert status["failed"]["errors"] == ["Job scan failed: status rule scan failed"]
    assert status["logs"]["deleted"] == 1

    status_log = root / "logs" / "job_status_only.log"
    status_log.write_text("record rule must not remove me", encoding="utf-8")
    _set_mtime(status_log, now - 10 * 86400)
    _record(root, "job_status_only", status="cancelled", updated_at=0,
            logPath=str(status_log))
    _write_config(path, {"jobs": {
        "failedRetention": {"enabled": False, "days": 1},
        "cancelledRetention": {"enabled": True, "days": 1},
        "logFileRetention": {"enabled": False, "days": 1},
    }})
    monkeypatch.setattr(jobs, "cleanup_jobs_by_statuses", original_cleaner)
    record_result = jobs.run_completed_job_retention(now=now + 86401)
    assert record_result["rules"]["cancelled"]["deleted"] == 1
    assert jobs.get("job_status_only", root) is None
    assert status_log.exists()


def test_log_cleaner_rejects_reparse_logs_directory(retention_env):
    root, _ = retention_env
    external = root.parent / "external-logs"
    external.mkdir()
    outside_log = external / "job_linked.log"
    outside_log.write_text("outside", encoding="utf-8")
    real_logs = root / "logs"
    real_logs.mkdir(parents=True, exist_ok=True)
    real_logs.rmdir()
    try:
        os.symlink(external, real_logs, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable on this Windows host")

    result = jobs.cleanup_job_log_files(registry_root=root, retention_days=1, now=1_000_000)

    assert result["deleted"] == 0
    assert result["errorCount"] == 1
    assert outside_log.read_text(encoding="utf-8") == "outside"
