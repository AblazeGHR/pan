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
