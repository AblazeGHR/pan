"""R2: owned synthetic profiles only; actual runtime boundary, zero effects."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import hashlib
import pytest

from packages.jobs import final_contracts as fc
from scripts.job_final_migration import apply as migration
from scripts.job_final_migration.p9d_rehearsal import counts, assert_fenced, service_for
from tests.support.job_migration_runtime_rehearsal import prepared_case, hashes

from tests.test_job_final_migration_user_authorization import evidence_for, INSTRUCTION, REFERENCE, AT
from scripts.job_final_migration.p9d_rehearsal import run


def test_coverage_lists_lite_omissions_without_sqlite_open(tmp_path, monkeypatch):
    from scripts.job_final_migration.source_coverage import inventory_coverage
    evidence, root, _, _ = evidence_for(tmp_path, n_simple=2, n_complex=2)
    (root / "scheduler").mkdir()
    (root / "scheduler/cursor.json").write_bytes(b'{"cursor":"original"}')
    (root / "scheduler/time-events.sqlite3").write_bytes(b"opaque sqlite bytes; never open")
    (root / "independent").mkdir()
    (root / "independent/outbox.sqlite3").write_bytes(b"pending unknown effects")
    import sqlite3
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("SQLite opened")))
    result = inventory_coverage(root)
    rows = {r["path"]: r for r in result["paths"]}
    for path in ("scheduler/cursor.json", "scheduler/time-events.sqlite3", "independent/outbox.sqlite3"):
        assert rows[path]["disposition"] in {"preserved", "unrecognized"}
        assert rows[path]["migrationHandled"] is False and rows[path]["sha256"]
    assert not result["wholeProfileMigrationComplete"]
    assert result["accounting"]["discoveredFiles"] == sum(result["accounting"][k] for k in
                                                             ("handledFiles", "preservedFiles", "unrecognizedFiles"))


def test_p9d_cli_exposes_explicit_synthetic_counts():
    p = subprocess.run([sys.executable, "-m", "scripts.job_final_migration.p9d_rehearsal", "run", "--help"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0
    assert "--expected-simple" in p.stdout and "--expected-preserved" in p.stdout


def test_existing_p9d_real_import_small_synthetic_set(tmp_path):
    evidence, root, packet, digest = evidence_for(tmp_path, n_simple=2, n_complex=2)
    result = asyncio.run(run(evidence, packet, tmp_path / "runtime", instruction=INSTRUCTION,
                            reference=REFERENCE, recorded_at=AT, original_root=str(root),
                            expected_manifest_sha256=digest, expected_simple=2, expected_preserved=3))
    assert result["runtimeImported"] and result["simpleImported"] == 2
    assert result["effectsExecuted"] == 0 and result["restartCountsUnchanged"]


def test_complete_runtime_chain_cold_process_and_epoch_controls(tmp_path):
    from tests.support.job_migration_runtime_rehearsal import run as rehearsal
    result = asyncio.run(rehearsal(tmp_path / "full"))
    assert result["p9d"]["canonicalRows"] == {"job": 2, "migration_history": 2, "migration_receipt": 2,
                                             "migration_unrecognized": 5, "runtime_activation": 1}
    assert result["coldProcess"]["effects"] == 0
    assert result["sourceUnchanged"] and result["copiedOriginalsUnchanged"]
    assert result["staleEpochRejected"] and result["concurrentOwnerRejected"] and result["oldWriterRejected"]
    assert not result["wholeProfileMigrationComplete"]


@pytest.mark.parametrize("fault,message", [
    ("receipt-pin", "receipt"), ("missing-pin", "externally pinned"),
    ("bundle-hash", "hash mismatch"), ("plan-digest", "planDigest"),
    ("release-revision", "revision"), ("release-digest", "digest"),
    ("schema", "schema"), ("active-ids", "does not authorize execution"),
    ("rolled-back", "Unsupported canonical bundle"),
])
def test_real_runtime_rejects_tampered_pins_releases_and_scope_atomically(tmp_path, fault, message):
    source, profile, service, target, spec = prepared_case(tmp_path / "negative")
    original_source = hashes(source)
    bundle = migration.read_bundle(target)
    if fault == "receipt-pin":
        spec["expected_user_authorization_digest"] = "0" * 64
    elif fault == "missing-pin":
        spec.pop("expected_user_authorization_digest")
    elif fault == "bundle-hash":
        spec["expected_hash"] = "0" * 64
    elif fault == "plan-digest":
        spec["expected_plan_digest"] = "0" * 64
    elif fault == "active-ids":
        spec["active_ids"] = bundle["entries"][0]["targetIds"]
    elif fault == "rolled-back":
        migration.rollback(profile, target, expected_digest=bundle["bundleDigest"])
        spec["expected_hash"] = hashlib.sha256(target.read_bytes()).hexdigest()
    else:
        job = next(r["object"] for r in bundle["canonical"] if r["kind"] == "job")
        if fault == "schema":
            job["schemaVersion"] = 99
        else:
            ref = job["steps"][0]["actionRef"]
            ref["revision" if fault == "release-revision" else "digest"] = 999 if fault == "release-revision" else "0" * 64
        # Deliberate corrupt candidate with an externally supplied new file hash;
        # keep original user receipt/context/identity untouched so deeper schema
        # and real pinned-release checks must reject it. No valid pin is forged.
        target.write_bytes(migration._bytes(migration._seal(bundle)))
        spec["expected_hash"] = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(fc.ContractError, match=message):
        asyncio.run(service.start(activation=spec))
    assert all(n == 0 for n in counts(service).values())
    assert service.owner.status()["state"] == "failed" and service.owner.lock.file is None
    assert service.task is None and service.executor.calls == [] and assert_fenced(profile)
    assert hashes(source) == original_source
    # No fence reset or activation-intent replacement to conceal a failure.
    assert service.owner.activation_intent() == fc.digest(spec)


@pytest.mark.parametrize("case", ["unlisted-cursor", "copied-authority"])
def test_p9d_refuses_missing_path_coverage_or_copied_owner_before_target_creation(tmp_path, case):
    evidence, root, packet, digest = evidence_for(tmp_path, n_simple=2, n_complex=2)
    extra = root / ("scheduler/cursor.json" if case == "unlisted-cursor" else "nested/job-runtime-owner.sqlite3")
    extra.parent.mkdir(parents=True)
    extra.write_bytes(b"unlisted bytes never adopted")
    before = hashes(root)
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="coverage"):
        asyncio.run(run(evidence, packet, output, instruction=INSTRUCTION, reference=REFERENCE,
                        recorded_at=AT, original_root=str(root), expected_manifest_sha256=digest,
                        expected_simple=2, expected_preserved=3))
    assert not output.exists() and hashes(root) == before


def test_coverage_refuses_real_profile_marker_before_arbitrary_reads(tmp_path):
    from scripts.job_final_migration.source_coverage import inventory_coverage
    from scripts.job_final_migration.inventory import RealProfileError
    (tmp_path / "process.json").write_text('{}')
    with pytest.raises(RealProfileError):
        inventory_coverage(tmp_path)


def test_synthetic_manual_packet_does_not_claim_historical_user_decisions(tmp_path):
    from scripts.job_final_migration.p9d_support import manual_deletion_packet
    _, root, packet, _ = evidence_for(tmp_path, n_simple=2, n_complex=2)
    output = tmp_path / "manual"
    rows = manual_deletion_packet(packet, output, original_root=str(root))
    guide = (output / "MANUAL_DELETE.md").read_text(encoding="utf-8")
    assert len(rows) == 3
    assert "此前 21 项" not in guide and "4 条消息计划" not in guide
    assert "用户已决定全部" not in guide


def test_authority_classification_cannot_be_overridden_by_lite_directory(tmp_path):
    from scripts.job_final_migration.source_coverage import inventory_coverage
    _, root, _, _ = evidence_for(tmp_path, n_simple=2, n_complex=2)
    relative = "background_jobs/jobs/job-runtime-owner.json"
    (root / relative).write_text('{}', encoding="utf-8")
    result = inventory_coverage(root)
    assert relative in result["blockingAuthorityPaths"]
    row = next(r for r in result["paths"] if r["path"] == relative)
    assert row["sourceType"] == "existing-runtime-authority"
    assert not row["migrationHandled"] and row["targetIds"] == []


def _runtime_delivery_source(root, kind):
    from tests.test_job_final_migration import _legacy_task, _message_job, _scheduled_job
    if kind == "legacy-assign":
        source = _legacy_task()
        path = root / "scheduler/tasks/runtime.json"
    elif kind in {"assign", "send_session"}:
        source = _scheduled_job()
        source["action"] = {"api": kind}
        path = root / "background_jobs/jobs/job_runtime.json"
    else:
        source = _message_job()
        if kind == "broadcast":
            source.update(kind="session-broadcast", targetSessionId=None,
                          targetSessionIds=["target-first", "target-second", "target-first"],
                          targetStruct={"sessionIds": ["target-first", "target-second", "target-first"]})
        path = root / "background_jobs/jobs/job_runtime.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(source), encoding="utf-8")
    return path, source


@pytest.mark.parametrize("kind", ["assign", "send_session", "legacy-assign", "message", "broadcast"])
def test_runtime_delivery_inventory_preserves_instead_of_freezing_literal(tmp_path, kind):
    from scripts.job_final_migration.inventory import build_inventory
    root = tmp_path / "synthetic-source"
    path, _ = _runtime_delivery_source(root, kind)
    before = hashes(root)
    inventory = build_inventory(root)
    item, = inventory["instances"]
    assert item["classification"] == "incompatible-preserved"
    assert "runtime_binding_unavailable" in item["reasonCodes"]
    assert item["targetIds"] == [] and item["fieldMap"] is None
    gap = item["runtimeBindingGap"]
    assert gap["owner"] == "Creator/Delivery Manager"
    assert gap["allowedStates"] == ["pending", "reserved"]
    assert gap["effectiveStage"] == "event-published-before-queue-or-external-acceptance"
    assert gap["freezePerTargetAt"] == ["accepted", "success", "unknown", "partial"]
    assert gap["bindingGenerated"] is False and gap["acceptanceState"] == "unverified"
    assert "runtimeRevision" in gap["requiredEvidence"] and "per-target receipt" in gap["requiredEvidence"]
    assert any(field["sourceField"] == "text" and field["requiresActionDeclaration"] ==
               "runtimeBeforeAcceptance" for field in gap["fields"])
    assert inventory["plan"]["items"] == []
    assert inventory["plan"]["preserved"][0]["runtimeBindingGap"] == gap
    assert inventory["plan"]["preserved"][0]["suggestedAction"] == "retain-source-manager-await-runtime-contract"
    assert path.is_file() and hashes(root) == before


def test_runtime_delivery_stale_simple_item_cannot_bypass_mapper_guard(tmp_path):
    from scripts.job_final_migration.inventory import build_inventory, SIMPLE
    from scripts.job_final_migration.mapping import Mapper, ApplyError
    from tests.test_job_final_migration_apply import context_for
    root = tmp_path / "synthetic-source"
    _, source = _runtime_delivery_source(root, "assign")
    item, = build_inventory(root)["instances"]
    item["classification"] = SIMPLE  # stale pre-amendment classification is not proof
    mapper = Mapper(context_for(root))
    with pytest.raises(ApplyError, match="runtime_binding_unavailable"):
        mapper.map(item, source)
    assert mapper.objects == []


def test_runtime_delivery_partial_bundle_keeps_original_bytes_and_no_target_ids(tmp_path):
    import base64
    from scripts.job_final_migration.inventory import build_inventory
    from tests.test_job_final_migration import _background_job, _write_json
    from tests.test_job_final_migration_apply import context_for
    root = tmp_path / "synthetic-source"
    path, _ = _runtime_delivery_source(root, "send_session")
    original = path.read_bytes()
    _write_json(root / "background_jobs/jobs/job_process.json", _background_job(root))
    before = hashes(root)
    context = {**context_for(root), "partialPolicy": {"version": 1, "mode": "preserve-incompatible"}}
    target = tmp_path / "offline-bundle.json"
    report = migration.migrate(root, target, context, apply=True, preserve_incompatible=True,
        expected_digest=migration.plan_digest(build_inventory(root), context))
    bundle = migration.read_bundle(target)
    assert len(bundle["entries"]) == 1 and bundle["entries"][0]["type"] == "MIG-BG"
    preserved, = bundle["preserved"]
    assert preserved["targetIds"] == [] and preserved["executable"] is False
    assert preserved["runtimeBindingGap"]["bindingGenerated"] is False
    relative = path.relative_to(root).as_posix()
    assert base64.b64decode(bundle["sourceBackups"][relative]["bytes"]) == original
    assert report["partialApply"]["allLegacyCleared"] is False
    assert hashes(root) == before


@pytest.mark.parametrize("state", ["missing-target", "pending-backlog", "unknown", "partial"])
def test_runtime_delivery_guard_keeps_original_state_evidence(tmp_path, state):
    from scripts.job_final_migration.inventory import build_inventory
    root = tmp_path / "synthetic-source"
    path, source = _runtime_delivery_source(root, "send_session")
    if state == "missing-target":
        source["targetSessionId"] = None
    elif state == "pending-backlog":
        source["undeliveredFires"] = [{"dispatchKey": "original-dispatch", "outcome": "known-no-attempt"}]
    else:
        source["lastDelivery"] = {"outcome": state, "receiptId": "original-receipt"}
    path.write_text(json.dumps(source), encoding="utf-8")
    before = path.read_bytes()
    item, = build_inventory(root)["instances"]
    assert item["classification"] == "incompatible-preserved" and item["targetIds"] == []
    assert "runtime_binding_unavailable" in item["reasonCodes"]
    assert item["runtimeBindingGap"]["acceptanceState"] == "unverified"
    assert ("target_missing" if state == "missing-target" else "pending_notice" if state == "pending-backlog"
            else "uncertain_effect") in item["reasonCodes"]
    assert path.read_bytes() == before


def test_runtime_delivery_proposed_input_reuses_schema_and_pre_admission_resolution(tmp_path):
    import jsonschema
    from packages.jobs.chains import resolve
    from scripts.job_final_migration.inventory import build_inventory
    from scripts.job_final_migration.render import render_markdown
    from scripts.job_final_migration.review_packet import _disposition
    root = tmp_path / "synthetic-source"
    _, source = _runtime_delivery_source(root, "send_session")
    report = build_inventory(root)
    item, = report["instances"]
    bindings = {field["argument"]: field["proposedBinding"] for field in item["runtimeBindingGap"]["fields"]}
    for binding in bindings.values():
        jsonschema.validate(binding, fc.schema_artifacts()["common.schema.json"]["$defs"]["Binding"])
    assert resolve(bindings, {"delivery": {"sessionId": "old-target", "text": "old-body"}}, {}, {}) == {
        "sessionId": "old-target", "text": "old-body"}
    assert resolve(bindings, {"delivery": {"sessionId": "new-target", "text": "updated-before-admission"}}, {}, {}) == {
        "sessionId": "new-target", "text": "updated-before-admission"}
    # This only proves the existing input path. It cannot supply post-admission
    # reservation/acceptance evidence and must not enable a migrated Job.
    disposition = _disposition(item, source)
    assert disposition["decisionIds"] == [] and disposition["implementationFollowUp"] is True
    assert disposition["historicalExecutionAllowed"] is False
    rendered = render_markdown(report)
    assert "Creator/Delivery Manager" in rendered and "no executable binding generated" in rendered


def test_runtime_delivery_only_source_reports_and_refuses_empty_canonical_publication(tmp_path, capsys):
    from scripts.job_final_migration.cli import main
    from scripts.job_final_migration.inventory import build_inventory
    from scripts.job_final_migration.mapping import ApplyError
    from tests.test_job_final_migration_apply import context_for
    root = tmp_path / "synthetic-source"
    _runtime_delivery_source(root, "assign")
    before = hashes(root)
    assert main(["--root", str(root), "--out", "-"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["simple"] == 0 and report["summary"]["complex"] == 1
    assert "runtime_binding_unavailable" in report["instances"][0]["reasonCodes"]
    context = {**context_for(root), "partialPolicy": {"version": 1, "mode": "preserve-incompatible"}}
    target = tmp_path / "must-not-exist/bundle.json"
    with pytest.raises(ApplyError, match="no simple records"):
        migration.migrate(root, target, context, apply=True, preserve_incompatible=True,
            expected_digest=migration.plan_digest(build_inventory(root), context))
    assert not target.parent.exists() and hashes(root) == before


def test_runtime_delivery_review_csv_keeps_optional_gap_columns_in_either_order(tmp_path):
    import csv
    import io
    from scripts.job_final_migration.inventory import build_inventory
    from scripts.job_final_migration.review_packet import _csv, _disposition
    root = tmp_path / "synthetic-source"
    _, source = _runtime_delivery_source(root, "assign")
    item, = build_inventory(root)["instances"]
    runtime = {"id": "runtime-source", "runtimeBindingGap": item["runtimeBindingGap"], **_disposition(item, source)}
    historical = {"id": "unresolved-original", **_disposition(
        {**item, "reasonCodes": ["uncertain_effect"]}, {})}
    for rows in ([historical, runtime], [runtime, historical]):
        parsed = list(csv.DictReader(io.StringIO(_csv(rows).decode("utf-8"))))
        row = next(r for r in parsed if r["id"] == "runtime-source")
        assert json.loads(row["runtimeBindingGap"])["owner"] == "Creator/Delivery Manager"
        assert json.loads(row["decisionIds"]) == [] and row["implementationFollowUp"] == "True"
