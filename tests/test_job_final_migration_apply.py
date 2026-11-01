"""FM01-FM05: offline synthetic-fixture apply, failures and source conservation."""

import base64
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from packages.jobs import final_contracts as fc
from scripts.job_final_migration import apply as migration
from scripts.job_final_migration.cli import main
from scripts.job_final_migration.inventory import build_inventory
from test_job_final_contracts import action_definition, object_document
from test_job_final_migration import (
    ACTION_ID, BG_ID, MSG_ID, SCHED_JOB_ID, TEMPLATE_ID, TRIGGER_ID,
    _action_job, _actions_registry, _background_job, _make_root,
    _message_job, _scheduled_job, _snapshot, _write_json,
)


def context_for(root: Path) -> dict:
    actions = {}
    for key in (ACTION_ID, "process.exec", "session.send", "session.send-many", "session.assign", "process.shell", "qq.send"):
        definition = fc.validate_definition("action", action_definition(
            name=key, inputSchema={"type": "object"}))
        obj = object_document("action", definition, id="act_" + key.replace(".", "_"), revision=1)
        actions[key] = {"object": obj, "ref": {"id": obj["id"], "revision": 1, "digest": fc.digest(definition)},
                        "completions": {"ordinary": {"contract": "process-exit"}}}
    context = {"schemaVersion": 1, "profileId": "fixture_profile", "actions": actions, "records": {}}
    for item in build_inventory(root)["instances"]:
        if item["classificationDetail"] != "simple":
            continue
        policies = {"overlap": {"mode": "forbid", "maxActive": 1, "maxPending": 0},
                    "recovery": {"automatic": False, "maxAttempts": 3,
                                 "backoffSeconds": ["5", "15", "60"], "stableResetSeconds": "300"},
                    "notification": {"on": [], "recipients": []},
                    "retention": {"policyRef": "fixture_policy", "pinned": False}}
        if item["type"] in {"MIG-ACTION-JOB", "MIG-TEMPLATE"}:
            policies["notification"]["on"] = ["completed", "failed", "cancelled", "uncertain", "settling"]
        if item["type"] == "MIG-TRIGGER":
            policies["recovery"]["maxAttempts"] = 1
        principal = {"type": "user", "id": "fixture_user"}
        if item["type"] == "MIG-ACTION-JOB":
            principal = {"type": "user", "id": "user_fixture"}
        if item["type"] == "MIG-TRIGGER":
            principal = {"type": "agent", "id": "agent_fixture"}
        context["records"][item["migrationId"]] = {
            "originalDigest": item["originalDigest"], "principal": principal,
            "owner": {"type": "user", "id": "fixture_user"},
            "oldOwnerStopped": True, "oldOwnerEpoch": 1,
            "fenceEvidence": "synthetic fixture: no owner has ever run",
            "authorizationEvidence": "synthetic fixture: explicit fixture grant",
            "importedAt": "2026-10-25T00:00:00+00:00", "policies": policies,
            "creatorSelector": {"mode": "registeredType", "providerType": "fixture"},
            "scheduler": {"advanceMode": "grid", "dstPolicy": "fixture-policy", "graceSeconds": "60",
                          "pausePolicy": "fixture-policy", "requiredCapabilities": [],
                          "providerRef": {"type": "time", "id": "scheduler", "releaseDigest": "c" * 64, "generation": 1},
                          "configSchemaRef": {"id": "scheduler_config", "revision": 1, "digest": "d" * 64}}}
    return context


def commit(root, target, context=None, **kwargs):
    context = context or context_for(root)
    return migration.migrate(root, target, context, apply=True,
                             expected_digest=migration.plan_digest(build_inventory(root), context), **kwargs)


def test_fm01_all_six_types_schema_hashes_provenance_and_idempotency(tmp_path):
    root = _make_root(tmp_path)
    target = tmp_path / "canonical.json"
    before = _snapshot(root)
    first = commit(root, target)
    bundle = migration.read_bundle(target)
    assert len(bundle["entries"]) == 6
    assert {e["type"] for e in bundle["entries"]} == {
        "MIG-BG", "MIG-MSG", "MIG-SCHED", "MIG-ACTION-JOB", "MIG-TRIGGER", "MIG-TEMPLATE"}
    for record in bundle["canonical"]:
        obj = record["object"]
        fc.validate_object(record["kind"], obj)
        assert obj["schemaVersion"] == 2
        assert obj["state"] in {"disabled", "draft"}
    for path, backup in bundle["sourceBackups"].items():
        assert base64.b64decode(backup["bytes"]) == (root / path).read_bytes()
    assert _snapshot(root) == before
    assert first["plan"]["executable"] is True
    assert first["runtimeActivated"] is False
    assert first["runtimeFenceVerified"] is False
    raw, mtime = target.read_bytes(), target.stat().st_mtime_ns
    second = commit(root, target)
    assert second["idempotentReplay"] and second["targetWrites"] == 0
    assert target.read_bytes() == raw and target.stat().st_mtime_ns == mtime
    assert not target.with_suffix(".json.bak").exists()
    assert _snapshot(root) == before
    assert bundle["allocations"] == migration.read_bundle(target)["allocations"]


def test_fm02_default_dry_run_and_complex_are_untouched(tmp_path):
    root = _make_root(tmp_path)
    complex_path = root / "background_jobs/jobs/job_complex.json"
    data = _background_job(root)
    data.update(jobId="job_complex", status="running", processTreeEnded=False)
    _write_json(complex_path, data)
    before = _snapshot(root)
    target = tmp_path / "not-created" / "canonical.json"
    report = migration.migrate(root, target, context_for(root))
    assert not target.parent.exists()
    assert report["plan"]["executable"] is False and not report["migrationExecuted"]
    report = commit(root, target)
    assert report["summary"]["complex"] == 1
    bundle = migration.read_bundle(target)
    assert len(bundle["preserved"]) == 1
    assert all(e["id"] != "job_complex" for e in bundle["entries"])
    assert _snapshot(root) == before


@pytest.mark.parametrize("raw", [b'{"kind":"future-kind"}', b'{invalid', b'{"kind":"a","kind":"b"}'])
def test_fm02_unknown_corrupt_duplicate_refuse_whole_apply(tmp_path, raw):
    root = _make_root(tmp_path)
    (root / "background_jobs/jobs/job_bad.json").write_bytes(raw)
    before = _snapshot(root)
    with pytest.raises(migration.ApplyError, match="unknown/corrupt"):
        commit(root, tmp_path / "canonical.json")
    assert not (tmp_path / "canonical.json").exists()
    assert _snapshot(root) == before


def test_fm03_stale_source_and_context_refused(tmp_path):
    root = _make_root(tmp_path)
    context = context_for(root)
    old_digest = fc.digest(build_inventory(root))
    path = root / f"background_jobs/jobs/{MSG_ID}.json"
    value = _message_job()
    value["text"] = "new edit"
    _write_json(path, value)
    target = tmp_path / "canonical.json"
    with pytest.raises(migration.ApplyError, match="plan_stale"):
        migration.migrate(root, target, context, apply=True, expected_digest=old_digest)
    with pytest.raises(migration.ApplyError, match="context source changed"):
        commit(root, target, context)
    assert not target.exists()


@pytest.mark.parametrize("fault", ["write", "fsync", "replace"])
def test_fm03_write_failure_never_publishes_partial_graph(tmp_path, monkeypatch, fault):
    root = _make_root(tmp_path)
    target = tmp_path / "canonical.json"
    before = _snapshot(root)
    def fail(*args, **kwargs):
        raise OSError("injected " + fault)
    if fault == "write":
        original = migration.os.fdopen
        class PartialWriter:
            def __init__(self, fd, mode):
                self.stream = original(fd, mode)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def write(self, raw):
                self.stream.write(raw[:20])
                raise OSError("injected partial write")
        monkeypatch.setattr(migration.os, "fdopen", PartialWriter)
    else:
        monkeypatch.setattr(migration.os, fault, fail)
    with pytest.raises(OSError, match="injected"):
        commit(root, target)
    assert not target.exists()
    assert not target.with_suffix(".json.lock").exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert _snapshot(root) == before


def test_fm03_failed_readonly_update_keeps_complete_old_bundle(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    target = tmp_path / "canonical.json"
    commit(root, target)
    original = target.read_bytes()
    replace = migration.os.replace
    def fail_target(src, dst):
        if Path(dst) == target:
            raise OSError("injected final replace")
        replace(src, dst)
    monkeypatch.setattr(migration.os, "replace", fail_target)
    with pytest.raises(OSError):
        commit(root, target, mark_read_only=True)
    assert target.read_bytes() == original
    assert target.with_suffix(".json.bak").read_bytes() == original
    assert all(not e["oldRecordReadOnly"] for e in migration.read_bundle(target)["entries"])


def test_fm04_readonly_owner_intent_rollback_reapply_same_ids(tmp_path):
    root = _make_root(tmp_path)
    before = _snapshot(root)
    target = tmp_path / "canonical.json"
    commit(root, target)
    initial = target.read_bytes()
    commit(root, target, mark_read_only=True)
    bundle = migration.read_bundle(target)
    assert target.with_suffix(".json.bak").read_bytes() == initial
    assert all(e["oldRecordReadOnly"] and e["ownerSwitch"]["ownerEpoch"] == 2
               and not e["ownerSwitch"]["runtimeEnforced"] for e in bundle["entries"])
    report = migration.rollback(root, target, expected_digest=bundle["bundleDigest"])
    assert report["plan"]["executable"] is False
    rolled = migration.read_bundle(target)
    assert rolled["canonical"] == []
    assert all(not e["oldRecordReadOnly"] for e in rolled["entries"])
    commit(root, target, mark_read_only=True)
    assert migration.read_bundle(target)["allocations"] == bundle["allocations"]
    assert _snapshot(root) == before


def test_fm03_lock_and_unknown_operation_fail_closed(tmp_path):
    root = _make_root(tmp_path)
    target = tmp_path / "canonical.json"
    lock = target.with_suffix(".json.lock")
    lock.write_text("another owner", encoding="utf-8")
    with pytest.raises(FileExistsError):
        commit(root, target)
    assert lock.read_text() == "another owner"
    lock.unlink()
    _write_json(target, {"toolSchema": "future/version"})
    original = target.read_bytes()
    with pytest.raises(migration.ApplyError, match="unsupported migration operation"):
        commit(root, target)
    assert target.read_bytes() == original


def test_fm03_source_race_before_commit_fails_without_target(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    target = tmp_path / "canonical.json"
    verify = migration.Mapper.verify
    def race(mapper):
        verify(mapper)
        path = root / f"background_jobs/jobs/{BG_ID}.json"
        path.write_bytes(path.read_bytes() + b"\n")
    monkeypatch.setattr(migration.Mapper, "verify", race)
    with pytest.raises(migration.ApplyError, match="source changed before commit"):
        commit(root, target)
    assert not target.exists()


def test_fm05_cli_preflights_output_root_profile_and_no_server_import(tmp_path, capsys):
    root = _make_root(tmp_path)
    context = tmp_path / "context.json"
    _write_json(context, context_for(root))
    before = _snapshot(root)
    target = tmp_path / "canonical.json"
    base = ["--root", str(root), "--target", str(target), "--context", str(context)]
    assert main(base + ["--apply", "--out", str(root / "report.json")]) == 2
    assert not target.exists() and _snapshot(root) == before
    assert main(base + ["--out", "-"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert not report["plan"]["executable"] and not target.exists()
    assert main(base + ["--apply", "--expected-digest", report["planDigest"], "--out", "-"]) == 0
    assert json.loads(capsys.readouterr().out)["migrationExecuted"]
    _write_json(root / "process.json", {"fixture": True})
    guarded = _snapshot(root)
    assert main(base + ["--out", "-"]) == 2
    assert "No file was read" in capsys.readouterr().err
    assert main(base + ["--allow-real-profile", "--out", "-"]) == 0
    assert _snapshot(root) == guarded
    code = "import scripts.job_final_migration.apply; import sys; assert 'packages.web.server' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("api,args", [("assign", None), ("send_session", None),
    ("shell", {"command": "D:/fixture/script.cmd", "cwd": "D:/fixture"}),
    ("send_qq", {"targetType": "group", "targetId": "123456", "botUin": "654321"})])
def test_fm01_scheduler_business_actions(tmp_path, api, args):
    root = tmp_path / "data"
    root.mkdir()
    value = _scheduled_job()
    value["action"] = {"api": api}
    if args:
        value["action"]["args"] = args
    if api == "send_qq":
        value["targetSessionId"] = None
    _write_json(root / f"background_jobs/jobs/{SCHED_JOB_ID}.json", value)
    report = commit(root, tmp_path / "canonical.json")
    assert report["summary"]["simple"] == 1


def test_fm01_broadcast_dedup_delay_and_chain(tmp_path):
    root = _make_root(tmp_path)
    value = _message_job()
    value.update(kind="session-broadcast", targetSessionId=None, targetSessionIds=["s1", "s2", "s1"],
                 targetStruct={"sessionIds": ["s1", "s2", "s1"]}, schedule={"type": "once", "delaySeconds": 60})
    _write_json(root / f"background_jobs/jobs/{MSG_ID}.json", value)
    job = _action_job()
    job["referenceGeneration"] = {ACTION_ID: 1}
    job["config"]["steps"] = [{"stepId": "one", "actionId": ACTION_ID, "arguments": {
        "text": {"from": "literal", "value": "one"}}},
        {"stepId": "two", "actionId": ACTION_ID, "arguments": {"text": {"from": "step", "stepId": "one", "path": "text"}}}]
    _write_json(root / "background_jobs/jobs/job_chain.json", job)
    commit(root, tmp_path / "canonical.json")
    bundle = migration.read_bundle(tmp_path / "canonical.json")
    jobs = [v["object"] for v in bundle["canonical"] if v["kind"] == "job"]
    broadcast = next(v for v in jobs if v["name"] == "Fixture message")
    assert broadcast["steps"][0]["arguments"]["sessionIds"]["value"] == ["s1", "s2"]
    assert any(len(v["steps"]) == 2 for v in jobs)


@pytest.mark.parametrize("field,value", [("oldOwnerStopped", False), ("authorizationEvidence", ""), ("oldOwnerEpoch", True)])
def test_fm05_missing_authority_or_fence_rejected(tmp_path, field, value):
    root = _make_root(tmp_path)
    context = context_for(root)
    next(iter(context["records"].values()))[field] = value
    with pytest.raises(migration.ApplyError, match="authority/fence"):
        commit(root, tmp_path / "canonical.json", context)


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_fm03_process_exit_at_publication_boundary(tmp_path, boundary):
    root = _make_root(tmp_path)
    target = tmp_path / "canonical.json"
    commit(root, target)
    before, original = _snapshot(root), target.read_bytes()
    code = '''
import sys, os
from pathlib import Path
sys.path.insert(0, 'tests')
from test_job_final_migration_apply import commit
from scripts.job_final_migration import apply as migration
root, target, boundary = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
replace = os.replace
def crash(src, dst):
    if Path(dst) == target:
        if boundary == 'after':
            replace(src, dst)
        os._exit(77)
    replace(src, dst)
os.replace = crash
commit(root, target, mark_read_only=True)
'''
    child = subprocess.run([sys.executable, "-c", code, str(root), str(target), boundary],
                           capture_output=True, text=True)
    assert child.returncode == 77, child.stderr
    bundle = migration.read_bundle(target)
    assert all(e["oldRecordReadOnly"] == (boundary == "after") for e in bundle["entries"])
    if boundary == "before":
        assert target.read_bytes() == original
    assert _snapshot(root) == before
    lock = target.with_suffix(".json.lock")
    assert lock.exists()
    with pytest.raises(FileExistsError):
        commit(root, target, mark_read_only=True)
    # The child is confirmed dead via its exit receipt. Operator-only clearance;
    # the tool itself must never guess lock liveness or reset the operation ID.
    lock.unlink()
    commit(root, target, mark_read_only=True)
    assert all(e["oldRecordReadOnly"] for e in migration.read_bundle(target)["entries"])


def test_fm04_readonly_marker_survives_ordinary_replay(tmp_path):
    root = _make_root(tmp_path)
    target = tmp_path / "canonical.json"
    commit(root, target, mark_read_only=True)
    original = target.read_bytes()
    assert commit(root, target)["idempotentReplay"]
    assert target.read_bytes() == original


@pytest.mark.parametrize("kind", ["once", "interval", "cron"])
def test_fm01_schedule_rule_pause_cursor_and_limit(tmp_path, kind):
    root = tmp_path / "data"
    root.mkdir()
    entry = {"id": "rule_fixture", "kind": kind, "timezone": "Asia/Shanghai",
             "misfirePolicy": "skip", "enabled": True,
             "nextFireAt": "2030-01-01T00:00:00", "lastFireAt": None}
    if kind == "once":
        entry["at"] = "2030-01-01T00:00:00"
    elif kind == "interval":
        entry.update(intervalSec=3600, anchor="2026-10-01T00:00:00")
    else:
        entry["cron"] = "0 * * * *"
    value = _scheduled_job([entry])
    value.update(paused=True, maxRuns=5, misfirePolicy="skip")
    _write_json(root / f"background_jobs/jobs/{SCHED_JOB_ID}.json", value)
    target = tmp_path / "canonical.json"
    commit(root, target)
    bundle = migration.read_bundle(target)
    creator = next(r["object"] for r in bundle["canonical"] if r["kind"] == "event_creator")
    assert creator["config"]["driveLimit"] == {"maxEvents": 5, "initialProducedCount": 0}
    assert creator["config"]["misfirePolicy"] == "skip"
    assert creator["config"]["rules"][0]["type"] == kind
    assert bundle["entries"][0]["desiredState"] == "paused"
    assert bundle["entries"][0]["cursor"]["nextFireAt"] == value["nextFireAt"]


def test_fm05_paths_refs_and_integrity_fail_closed(tmp_path):
    root = _make_root(tmp_path)
    before = _snapshot(root)
    context = context_for(root)
    context["actions"][ACTION_ID]["ref"]["digest"] = "e" * 64
    with pytest.raises(migration.ApplyError, match="digest/revision"):
        commit(root, tmp_path / "canonical.json", context)
    with pytest.raises(migration.ApplyError, match="inside the scanned root"):
        commit(root, root / "canonical.json")
    target = tmp_path / "canonical.json"
    commit(root, target)
    value = json.loads(target.read_text(encoding="utf-8"))
    value["entries"][0]["oldRecordReadOnly"] = True
    _write_json(target, value)
    with pytest.raises(migration.ApplyError, match="bundle digest mismatch"):
        commit(root, target)
    alias = tmp_path / "hardlink.json"
    os.link(root / f"background_jobs/jobs/{BG_ID}.json", alias)
    assert main(["--root", str(root), "--out", str(alias)]) == 2
    assert _snapshot(root) == before


def test_fm05_plan_digest_binds_context_not_only_source(tmp_path):
    root = _make_root(tmp_path)
    target = tmp_path / "canonical.json"
    context = context_for(root)
    report = migration.migrate(root, target, context)
    changed = copy.deepcopy(context)
    next(iter(changed["records"].values()))["policies"]["retention"]["policyRef"] = "different_policy"
    with pytest.raises(migration.ApplyError, match="plan_stale"):
        migration.migrate(root, target, changed, apply=True, expected_digest=report["planDigest"])
    assert not target.exists()


def test_fm05_cli_refuses_target_report_collisions_before_apply(tmp_path):
    root = _make_root(tmp_path)
    target = tmp_path / "canonical.json"
    context = tmp_path / "context.json"
    _write_json(context, context_for(root))
    for report in (target, target.with_suffix(".json.bak"), target.with_suffix(".json.lock")):
        assert main(["--root", str(root), "--target", str(target), "--context", str(context),
                     "--apply", "--out", str(report)]) == 2
    assert not target.exists()
