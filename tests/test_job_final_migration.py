"""Fixtures for the read-only Job final-model migration inventory."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.job_final_migration.cli import main
from scripts.job_final_migration.inventory import (
    MAPPING_VERSION,
    PRESERVED,
    SIMPLE,
    build_inventory,
    is_within_root,
)
from scripts.job_final_migration.render import render_json, render_markdown

ACTION_ID = "system_session_message"
TEMPLATE_ID = "jtpl_fixture0000000000000001"
TRIGGER_ID = "trg_fixture0000000000000001"
BG_ID = "job_" + "b" * 24
MSG_ID = "job_" + "m" * 24
ACTION_JOB_ID = "job_" + "a" * 24
SCHED_JOB_ID = "job_" + "s" * 24
TASK_ID = "sch_fixture0000000001"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _job_config() -> dict:
    return {
        "name": "Fixture Action Job",
        "description": "",
        "actionId": ACTION_ID,
        "arguments": {
            "sessionId": {"from": "literal", "value": "sess_target"},
            "text": {"from": "literal", "value": "hello"},
        },
        "schedule": [],
        "trigger": {"kind": "immediate"},
        "recovery": {
            "enabled": False,
            "monitoring": False,
            "maxAttempts": 3,
            "backoffSec": [5, 15, 60],
            "stableResetSec": 300,
        },
        "overlap": {"mode": "forbid", "parallelLimit": 3},
        "notification": {
            "on": ["completed", "failed", "cancelled", "unknown", "uncertain",
                   "cleaning"],
            "target": "job",
        },
        "completion": {"kind": "ordinary"},
        "enabled": True,
        "maxRuns": None,
        "misfirePolicy": "fire_now",
    }


def _background_job(root: Path) -> dict:
    return {
        "jobId": BG_ID,
        "kind": "background-process",
        "operation": "run",
        "targetSessionId": "sess_target",
        "argv": ["cmd.exe", "/c", "echo", "fixture"],
        "hideWindow": True,
        "creatorSessionId": "sess_creator",
        "sourceStruct": {"type": "agent", "sessionId": "sess_creator"},
        "targetStruct": {"sessionId": "sess_target"},
        "name": "Fixture background",
        "description": "",
        "commandSummary": "cmd.exe /c echo …",
        "cwd": str(root / "work"),
        "label": None,
        "status": "completed",
        "createdAt": 1761300000.0,
        "updatedAt": 1761300100.0,
        "pid": 1234,
        "processCreatedAt": 1761300001.0,
        "logPath": str(root / "logs" / f"{BG_ID}.log"),
        "notificationState": "not_applicable",
        "terminalEventId": "evt_fixture",
        "processTreeEnded": True,
        "exitCode": 0,
    }


def _message_job() -> dict:
    return {
        "jobId": MSG_ID,
        "kind": "session-message",
        "operation": "send",
        "targetSessionId": "sess_target",
        "text": "hello later",
        "name": "Fixture message",
        "description": "",
        "source": "agent",
        "sourceSessionId": "sess_creator",
        "sourceStruct": {"type": "agent", "sessionId": "sess_creator"},
        "targetStruct": {"sessionId": "sess_target"},
        "creatorSessionId": "sess_creator",
        "schedule": {"type": "once", "at": "2030-01-01T00:00:00+00:00"},
        "nextRunAt": "2030-01-01T00:00:00+00:00",
        "status": "pending",
        "runCount": 0,
        "lastRunAt": None,
        "lastDelivery": None,
        "lastError": None,
        "createdAt": 1761300000.0,
        "updatedAt": 1761300000.0,
    }


def _legacy_task() -> dict:
    return {
        "id": TASK_ID,
        "name": "Fixture legacy scheduled task",
        "description": "",
        "target_session_id": "sess_target",
        "text": "hello schedule",
        "enabled": True,
        "paused": False,
        "schedule": {
            "kind": "interval",
            "interval_sec": 3600,
            "intervalSec": 3600,
            "anchor": "2026-10-01T00:00:00",
            "timezone": "Asia/Shanghai",
        },
        "next_fire_at": "2030-01-01T00:00:00",
        "last_fire_at": None,
        "last_status": None,
        "last_error": None,
        "run_count": 0,
        "max_runs": None,
        "misfire_policy": "fire_now",
        "created_at": "2026-10-01T00:00:00",
        "updated_at": "2026-10-01T00:00:00",
    }


def _scheduled_job(schedule: list[dict] | None = None) -> dict:
    entries = schedule or [
        {
            "id": "sce_fixture0001",
            "kind": "once",
            "timezone": "Asia/Shanghai",
            "at": "2030-01-01T00:00:00",
            "misfirePolicy": "fire_now",
            "enabled": True,
            "nextFireAt": "2030-01-01T00:00:00",
            "lastFireAt": None,
        }
    ]
    return {
        "jobId": SCHED_JOB_ID,
        "kind": "scheduled-task",
        "taskId": TASK_ID,
        "name": "Fixture scheduled job",
        "description": "",
        "targetSessionId": "sess_target",
        "sourceStruct": {"type": "system"},
        "targetStruct": {"sessionId": "sess_target"},
        "text": "hello schedule",
        "action": {"api": "assign"},
        "source": "automation",
        "creatorSessionId": None,
        "enabled": True,
        "paused": False,
        "schedule": entries,
        "misfirePolicy": "fire_now",
        "nextFireAt": "2030-01-01T00:00:00",
        "lastFireAt": None,
        "lastStatus": None,
        "lastError": None,
        "runCount": 0,
        "maxRuns": None,
        "status": "scheduled",
        "runStartedAt": None,
        "undeliveredFires": [],
        "createdAt": 1761300000.0,
        "updatedAt": 1761300000.0,
    }


def _action_job() -> dict:
    return {
        "jobId": ACTION_JOB_ID,
        "kind": "scheduled-task",
        "taskId": "sch_actionfixture0001",
        "name": "Fixture Action Job",
        "description": "",
        "targetSessionId": "sess_target",
        "sourceStruct": {"type": "agent", "sessionId": "sess_creator"},
        "targetStruct": {"sessionId": "sess_target"},
        "text": "hello",
        "action": {
            "api": "action_reference",
            "actionId": ACTION_ID,
            "arguments": {"sessionId": "sess_target", "text": "hello"},
        },
        "source": "agent",
        "creatorSessionId": "sess_creator",
        "enabled": True,
        "paused": False,
        "schedule": [],
        "misfirePolicy": "fire_now",
        "nextFireAt": None,
        "lastFireAt": "2026-10-24T10:00:00",
        "lastStatus": "completed",
        "lastError": None,
        "runCount": 1,
        "maxRuns": None,
        "status": "completed",
        "runStartedAt": None,
        "undeliveredFires": [],
        "createdAt": 1761300000.0,
        "updatedAt": 1761300000.0,
        "config": _job_config(),
        "configRevision": 1,
        "referenceGeneration": 1,
        "creationFingerprint": "sha256:fixture",
        "authorization": {"type": "user", "userId": "user_fixture"},
        "lastExecution": {"id": "run_fixture", "status": "succeeded"},
    }


def _trigger() -> dict:
    return {
        "id": TRIGGER_ID,
        "name": "Fixture trigger",
        "description": "",
        "eventTypes": ["session.updated"],
        "eventFilter": {"payload.type": "fixture"},
        "condition": None,
        "actionId": ACTION_ID,
        "arguments": {
            "sessionId": {"from": "literal", "value": "sess_target"},
            "text": {"from": "event", "path": "payload.text"},
        },
        "executionMode": "serial",
        "maxConcurrency": 1,
        "retry": {"enabled": False, "maxAttempts": 1},
        "enabled": True,
        "revision": 1,
        "createdBy": {"type": "agent", "id": "agent_fixture"},
        "createdAt": "2026-10-01T00:00:00+00:00",
        "updatedAt": "2026-10-01T00:00:00+00:00",
        "authorization": {"type": "agent", "agentId": "agent_fixture"},
    }


def _template() -> dict:
    return {
        "id": TEMPLATE_ID,
        "name": "Fixture Template",
        "description": "",
        "config": _job_config(),
        "revision": 1,
        "referenceGeneration": 1,
    }


def _actions_registry(include_template: bool = True) -> dict:
    state = {
        "revision": 3,
        "actions": {
            ACTION_ID: {
                "id": ACTION_ID,
                "name": "Session message",
                "description": "",
                "system": True,
                "enabled": True,
                "execution": {"kind": "system_job"},
            }
        },
        "actionGenerations": {ACTION_ID: 1},
    }
    if include_template:
        state["jobTemplates"] = {TEMPLATE_ID: _template()}
    return state


def _automation_registry() -> dict:
    return {
        "revision": 2,
        "triggers": {TRIGGER_ID: _trigger()},
        "runs": {},
        "linkOperations": {},
    }


def _make_root(tmp_path: Path, *, include_template: bool = True,
              include_trigger: bool = True) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    _write_json(root / "background_jobs" / "jobs" / f"{BG_ID}.json",
                _background_job(root))
    _write_json(root / "background_jobs" / "jobs" / f"{MSG_ID}.json",
                _message_job())
    _write_json(root / "background_jobs" / "jobs" / f"{ACTION_JOB_ID}.json",
                _action_job())
    _write_json(root / "scheduler" / "tasks" / f"{TASK_ID}.json",
                _legacy_task())
    _write_json(root / "actions" / "registry.json",
                _actions_registry(include_template=include_template))
    if include_trigger:
        _write_json(root / "automation" / "registry.json",
                    _automation_registry())
    return root


def _snapshot(root: Path) -> dict[str, tuple[str, str | None]]:
    result: dict[str, tuple[str, str | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            result[relative] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():
            result[relative] = ("dir", None)
    return result


def _by_id(report: dict, original_id: str) -> dict:
    matches = [item for item in report["instances"] if item["id"] == original_id]
    assert len(matches) == 1, matches
    return matches[0]


def test_six_simple_instances_are_classified_and_planned(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    report = build_inventory(root)

    assert report["readOnly"] is True
    assert report["migrationExecuted"] is False
    assert report["targetWrites"] == 0
    assert report["summary"]["total"] == 6
    assert report["summary"]["simple"] == 6
    assert report["summary"]["complex"] == 0
    assert report["summary"]["unknown"] == 0
    assert report["summary"]["scanErrors"] == 0
    assert report["summary"]["bySourceType"] == {
        "MIG-ACTION-JOB": {"total": 1, "simple": 1, "complex": 0, "unknown": 0},
        "MIG-BG": {"total": 1, "simple": 1, "complex": 0, "unknown": 0},
        "MIG-MSG": {"total": 1, "simple": 1, "complex": 0, "unknown": 0},
        "MIG-SCHED": {"total": 1, "simple": 1, "complex": 0, "unknown": 0},
        "MIG-TEMPLATE": {"total": 1, "simple": 1, "complex": 0, "unknown": 0},
        "MIG-TRIGGER": {"total": 1, "simple": 1, "complex": 0, "unknown": 0},
    }
    assert len(report["plan"]["items"]) == 6
    assert report["plan"]["preserved"] == []
    assert report["plan"]["executable"] is False
    assert report["plan"]["executionAttempted"] is False

    for item in report["instances"]:
        assert item["classification"] == SIMPLE
        assert item["classificationDetail"] == "simple"
        assert item["mappingVersion"] == MAPPING_VERSION
        assert item["fieldMap"]
        assert item["suggestedAction"] == "plan-only"
        assert item["sha256"] is not None
        assert item["proofs"]["authorityVerified"] is False
        source = root / item["sourceFile"]
        assert item["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_action_chain_reference_generation_is_supported(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    job = _action_job()
    config = _job_config()
    config.pop("actionId")
    config["actionId"] = ACTION_ID
    config["arguments"] = {}
    config["steps"] = [
        {"stepId": "step-one", "actionId": ACTION_ID,
         "arguments": {"sessionId": {"from": "literal", "value": "sess_target"},
                       "text": {"from": "literal", "value": "one"}}},
        {"stepId": "step-two", "actionId": ACTION_ID,
         "arguments": {"sessionId": {"from": "literal", "value": "sess_target"},
                       "text": {"from": "literal", "value": "two"}}},
    ]
    job["config"] = config
    job["referenceGeneration"] = {ACTION_ID: 1}
    action = job["action"]
    action["arguments"] = {}
    _write_json(root / "background_jobs" / "jobs" / f"{ACTION_JOB_ID}.json", job)
    _write_json(root / "actions" / "registry.json", _actions_registry(
        include_template=False))

    report = build_inventory(root)
    item = _by_id(report, ACTION_JOB_ID)
    assert item["classification"] == SIMPLE
    assert item["reasonCodes"] == []


def test_unknown_field_is_preserved(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    path = root / "background_jobs" / "jobs" / f"{BG_ID}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["surprise"] = "not in the v1 shape"
    _write_json(path, data)

    report = build_inventory(root)
    item = _by_id(report, BG_ID)
    assert item["classification"] == PRESERVED
    assert item["classificationDetail"] == "complex"
    assert "unknown_field" in item["reasonCodes"]
    assert any("surprise" in reason for reason in item["rejectionReasons"])
    assert item["suggestedAction"] == "preserve-and-manual-recreate"
    assert all(plan["originalId"] != BG_ID for plan in report["plan"]["items"])


def test_multiple_schedule_entries_are_preserved(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    entries = [
        {
            "id": "sce_fixture0001", "kind": "once", "timezone": "Asia/Shanghai",
            "at": "2030-01-01T00:00:00", "misfirePolicy": "fire_now",
            "enabled": True, "nextFireAt": "2030-01-01T00:00:00",
            "lastFireAt": None,
        },
        {
            "id": "sce_fixture0002", "kind": "interval",
            "timezone": "Asia/Shanghai", "intervalSec": 3600,
            "anchor": "2026-10-01T00:00:00", "misfirePolicy": "fire_now",
            "enabled": True, "nextFireAt": "2030-01-01T00:00:00",
            "lastFireAt": None,
        },
    ]
    _write_json(root / "background_jobs" / "jobs" / f"{SCHED_JOB_ID}.json",
                _scheduled_job(entries))

    report = build_inventory(root)
    item = _by_id(report, TASK_ID)
    assert item["classification"] == PRESERVED
    assert item["classificationDetail"] == "complex"
    assert "schedule_semantic_loss" in item["reasonCodes"]
    assert any("exactly one schedule entry" in reason
               for reason in item["rejectionReasons"])


def test_unknown_action_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    data = _scheduled_job()
    data["action"] = {"api": "mystery"}
    _write_json(root / "background_jobs" / "jobs" / f"{SCHED_JOB_ID}.json", data)

    report = build_inventory(root)
    item = _by_id(report, TASK_ID)
    assert item["classification"] == PRESERVED
    assert "unsupported_complex_instance" in item["reasonCodes"]
    assert any("unknown scheduler action" in reason
               for reason in item["rejectionReasons"])


def test_active_background_is_preserved(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    path = root / "background_jobs" / "jobs" / f"{BG_ID}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "running"
    data["processTreeEnded"] = False
    _write_json(path, data)

    report = build_inventory(root)
    item = _by_id(report, BG_ID)
    assert item["classification"] == PRESERVED
    assert "active_resource" in item["reasonCodes"]
    assert item["activeEffectHandles"]


def test_pending_notice_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    data = _scheduled_job()
    data["undeliveredFires"] = [{
        "entryId": "sce_fixture0001",
        "fireAt": "2026-10-20T00:00:00",
        "dispatchKey": "fixture:dispatch:1",
        "text": "pending",
        "error": "target missing",
    }]
    _write_json(root / "background_jobs" / "jobs" / f"{SCHED_JOB_ID}.json", data)

    report = build_inventory(root)
    item = _by_id(report, TASK_ID)
    assert item["classification"] == PRESERVED
    assert "pending_notice" in item["reasonCodes"]
    assert "undeliveredFires" in item["noticeReceipts"]


def test_active_run_ledger_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    _write_json(root / "background_jobs" / "jobs" / f"{SCHED_JOB_ID}.json",
                _scheduled_job())
    ledger = root / "background_jobs" / "runs.jsonl"
    ledger.write_text(json.dumps({
        "run_id": "run_fixture",
        "task_id": TASK_ID,
        "status": "running",
        "fire_at": "2026-10-20T00:00:00",
    }) + "\n", encoding="utf-8")

    report = build_inventory(root)
    item = _by_id(report, TASK_ID)
    assert item["classification"] == PRESERVED
    assert "active_resource" in item["reasonCodes"]
    assert any("run_fixture" in reason for reason in item["rejectionReasons"])


def test_missing_identity_is_preserved(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    path = root / "background_jobs" / "jobs" / f"{BG_ID}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["creatorSessionId"] = None
    data["sourceStruct"] = {"type": "system"}
    _write_json(path, data)

    report = build_inventory(root)
    item = _by_id(report, BG_ID)
    assert item["classification"] == PRESERVED
    assert item["classificationDetail"] == "complex"
    assert "unverified_authority" in item["reasonCodes"]
    assert item["owner"] == {"kind": "unknown", "id": None}


def test_directory_traversal_helper_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    assert is_within_root(root, root / "inside.json") is True
    assert is_within_root(root, root / ".." / "outside.json") is False

    jobs = root / "background_jobs" / "jobs"
    jobs.mkdir(parents=True)
    link = jobs / f"{BG_ID}.json"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError) as exc:
        if os.name != "nt":
            pytest.skip(f"symlinks unavailable on this platform: {exc}")
        # Windows without symlink privilege still supports directory junctions,
        # which exercise the same resolved-path escape check.
        jobs.rmdir()
        outside_dir = tmp_path / "outside-dir"
        outside_dir.mkdir()
        (outside_dir / f"{BG_ID}.json").write_text("{}\n", encoding="utf-8")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(jobs), str(outside_dir)],
            capture_output=True,
        )
        if completed.returncode != 0:
            pytest.skip("neither symlinks nor junctions are available")

    report = build_inventory(root)
    assert report["summary"]["total"] == 0
    assert any(error["code"] == "path_escape" for error in report["scanErrors"])


def test_real_profile_requires_explicit_read_only_permission(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pan = tmp_path / "pan"
    data = pan / "data"
    data.mkdir(parents=True)
    (data / "process.json").write_text("{}\n", encoding="utf-8")
    _write_json(data / "background_jobs" / "jobs" / f"{BG_ID}.json",
                _background_job(data))
    output = tmp_path / "report.json"

    assert main(["--root", str(pan), "--out", str(output)]) == 2
    assert not output.exists()
    assert "refusing to scan a real Pan profile" in capsys.readouterr().err

    assert main(["--root", str(data), "--out", str(output)]) == 2
    assert not output.exists()

    before = _snapshot(data)
    assert main([
        "--root", str(data), "--out", str(output), "--allow-real-profile",
    ]) == 0
    assert _snapshot(data) == before
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["root"]["realProfile"] is True
    assert report["root"]["allowRealProfile"] is True
    assert report["summary"]["simple"] == 1


def test_output_is_idempotent_and_does_not_write_root(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    out_one = tmp_path / "one.json"
    out_two = tmp_path / "two.json"

    before = _snapshot(root)
    assert main(["--root", str(root), "--out", str(out_one)]) == 0
    assert main(["--root", str(root), "--out", str(out_two)]) == 0
    assert _snapshot(root) == before
    assert out_one.read_bytes() == out_two.read_bytes()

    first = build_inventory(root)
    second = build_inventory(root)
    assert render_json(first) == render_json(second)


def test_output_inside_root_is_refused(tmp_path: Path,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    root = _make_root(tmp_path)
    destination = root / "report.json"
    assert main(["--root", str(root), "--out", str(destination)]) == 2
    assert not destination.exists()
    assert "refusing to write output inside the scanned root" in capsys.readouterr().err


def test_duplicate_json_keys_are_unknown(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    path = root / "background_jobs" / "jobs" / f"{BG_ID}.json"
    path.parent.mkdir(parents=True)
    raw = json.dumps(_background_job(root), ensure_ascii=False, indent=2)
    raw = raw.replace('"status": "completed"',
                      '"status": "completed", "status": "completed"', 1)
    path.write_text(raw + "\n", encoding="utf-8")

    report = build_inventory(root)
    item = report["instances"][0]
    assert item["classification"] == PRESERVED
    assert item["classificationDetail"] == "unknown"
    assert "corrupt_record" in item["reasonCodes"]
    assert item["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_markdown_rendering_is_deterministic(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    report = build_inventory(root)
    first = render_markdown(report)
    second = render_markdown(build_inventory(root))
    assert first == second
    assert "# Job final migration inventory" in first
    assert "Read-only inventory" in first
    assert "## Summary" in first
    assert "MIG-BG" in first
    assert "Rejection reasons: none" in first


def test_cli_module_entry_point(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    output = tmp_path / "module-report.json"
    repo = Path(__file__).resolve().parents[1]
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("PAN_")}
    environment["PYTHONPATH"] = str(repo)
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.job_final_migration",
         "--root", str(root), "--out", str(output)],
        cwd=repo, env=environment, capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["simple"] == 6
