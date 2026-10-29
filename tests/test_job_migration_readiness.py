"""Owned offline readiness probes; no runtime import/activation or old assertions changed."""
import pytest

from scripts.job_final_migration import apply as migration
from scripts.job_final_migration.cli import main
from scripts.job_final_migration.inventory import build_inventory
from test_job_final_migration import (
    ACTION_JOB_ID, MSG_ID, _action_job, _make_root, _message_job, _snapshot, _write_json,
)
from test_job_final_migration_apply import context_for
from scripts.job_final_migration.mapping import timestamp


@pytest.mark.parametrize("feature", ["monitoring", "stable-reset"])
def test_unsupported_legacy_monitoring_is_preserved_not_silently_mapped(tmp_path, feature):
    root = _make_root(tmp_path)
    value = _action_job()
    value["config"]["recovery"].update(monitoring=True, enabled=feature == "stable-reset")
    _write_json(root / f"background_jobs/jobs/{ACTION_JOB_ID}.json", value)
    before = _snapshot(root)
    item = next(i for i in build_inventory(root)["instances"] if i["id"] == ACTION_JOB_ID)
    assert item["classification"] == "incompatible-preserved"
    assert "unsupported_complex_instance" in item["reasonCodes"]
    assert item["targetIds"] == [] and _snapshot(root) == before


def test_cli_rejects_context_collision_with_bundle_backup_before_publication(tmp_path, capsys):
    root = _make_root(tmp_path)
    target = tmp_path / "bundle.json"
    context_path = tmp_path / "bundle.json.bak"
    context = context_for(root)
    _write_json(context_path, context)
    before, context_bytes = _snapshot(root), context_path.read_bytes()
    digest = migration.plan_digest(build_inventory(root), context)
    code = main(["--root", str(root), "--target", str(target), "--context", str(context_path),
                 "--apply", "--expected-digest", digest, "--out", "-"])
    assert code == 2, capsys.readouterr()
    assert not target.exists() and context_path.read_bytes() == context_bytes
    assert _snapshot(root) == before


def test_message_mapping_keeps_explicit_source_session_identity(tmp_path):
    root = _make_root(tmp_path)
    value = _message_job()
    _write_json(root / f"background_jobs/jobs/{MSG_ID}.json", value)
    context = context_for(root)
    target = tmp_path / "bundle.json"
    migration.migrate(root, target, context, apply=True,
                      expected_digest=migration.plan_digest(build_inventory(root), context))
    bundle = migration.read_bundle(target)
    entry = next(e for e in bundle["entries"] if e["id"] == MSG_ID)
    job = next(r["object"] for r in bundle["canonical"]
               if r["kind"] == "job" and r["object"]["id"] in entry["targetIds"])
    assert job["steps"][0]["arguments"]["sourceSessionId"] == {
        "from": "literal", "value": value["sourceSessionId"]}


@pytest.mark.parametrize("value,zone,expected", [
    ("2030-01-01T00:00:00.123456789+08:00", None, "2030-01-01T00:00:00.123456789+08:00"),
    ("2030-01-01T00:00:00.000000001", "Asia/Shanghai", "2030-01-01T00:00:00.000000001+08:00"),
])
def test_explicit_timestamp_preserves_decimal_precision(value, zone, expected):
    assert timestamp(value, zone) == expected


def test_rollback_report_keeps_runtime_fence_boundary_explicit(tmp_path):
    root = _make_root(tmp_path)
    context = context_for(root)
    target = tmp_path / "bundle.json"
    result = migration.migrate(root, target, context, apply=True,
                               expected_digest=migration.plan_digest(build_inventory(root), context))
    report = migration.rollback(root, target, expected_digest=result["bundleDigest"])
    assert report["runtimeActivated"] is False
    assert report["runtimeFenceVerified"] is False


def test_complete_public_cli_synthetic_offline_chain(tmp_path):
    from tests.support.job_migration_readiness_rehearsal import run
    summary = run(tmp_path / "drill")
    assert summary["inventory"]["total"] == 20
    assert summary["migratedOffline"] == 8 and summary["preserved"] == 12
    assert summary["sourceUnchanged"] and not summary["remainingTemporaryArtifacts"]
    assert summary["unscannedSourceFiles"] == ["scheduler/cursors-unported.json"]


@pytest.mark.parametrize("field,value", [("oldOwnerStopped", False), ("authorizationEvidence", ""),
                                          ("fenceEvidence", ""), ("oldOwnerEpoch", 0)])
def test_unproven_owner_or_authority_cannot_publish(tmp_path, field, value):
    root = _make_root(tmp_path)
    context = context_for(root)
    next(iter(context["records"].values()))[field] = value
    target = tmp_path / "absent/bundle.json"
    with pytest.raises(migration.ApplyError, match="authority/fence"):
        migration.migrate(root, target, context, apply=True,
                          expected_digest=migration.plan_digest(build_inventory(root), context))
    assert not target.parent.exists()


@pytest.mark.parametrize("local", ["2026-11-01T01:30:00", "2026-03-08T02:30:00"])
def test_dst_ambiguous_or_missing_time_refuses_host_guess(local):
    with pytest.raises(migration.ApplyError, match="schedule_semantic_loss"):
        timestamp(local, "America/New_York")


@pytest.mark.parametrize("fault", ["input", "name", "action-ref", "unpublished", "missing-shell"])
def test_current_action_contract_negative_paths_never_publish(tmp_path, fault):
    from tests.support.job_migration_readiness_rehearsal import current_actions
    from test_job_final_migration import _scheduled_job
    root = _make_root(tmp_path)
    if fault == "missing-shell":
        value = _scheduled_job()
        value["action"] = {"api": "shell", "args": {"command": str(root / "synthetic.cmd"), "cwd": str(root)}}
        _write_json(root / "background_jobs/jobs/job_shell.json", value)
    context = context_for(root)
    context["actions"] = current_actions()
    if fault == "action-ref":
        context["actions"]["process.exec"]["ref"]["digest"] = "0" * 64
    elif fault == "unpublished":
        context["actions"]["process.exec"]["object"]["enabled"] = False
    elif fault in {"input", "name"}:
        value = _message_job()
        value["text" if fault == "input" else "name"] = 123 if fault == "input" else "n" * 10000
        # Exercise mapping/schema against actual current pins, separately from
        # lite inventory's whitelist checks, with a well-shaped inventoried root.
        from scripts.job_final_migration.mapping import Mapper
        item = next(i for i in build_inventory(root)["instances"] if i["id"] == MSG_ID)
        with pytest.raises(ValueError):
            Mapper(context).map(item, value)
        return
    target = tmp_path / "absent/bundle.json"
    with pytest.raises(ValueError):
        migration.migrate(root, target, context, apply=True,
                          expected_digest=migration.plan_digest(build_inventory(root), context))
    assert not target.parent.exists()
