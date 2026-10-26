"""P9D: audited user decision, real isolated SQLite imports, no effects."""
import asyncio
import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest

from packages.jobs import final_contracts as fc, migration_history
from packages.jobs.migration_authorization import USER
from scripts.job_final_migration import apply as migration
from scripts.job_final_migration.inventory import build_inventory
from scripts.job_final_migration.p9d_rehearsal import run, service_for, counts
from scripts.job_final_migration.p9d_support import prepare, manual_deletion_packet
from scripts.job_final_migration.review_packet import build_packet
from tests.test_job_final_migration import _background_job, _snapshot, _write_json

INSTRUCTION = ("Synthetic fixture user instruction: migrate all simple records as user/local; preserve all complex and unknown "
               "as unrecognized with unchanged bytes, no replay, no unknown settlement, no automatic deletion or execution.")
REFERENCE = "conversation/user/synthetic-fixture-instruction"
AT = "2026-10-26T11:06:33+00:00"


def evidence_for(tmp_path, n_simple=1, n_complex=1):
    evidence = tmp_path / "p9b"
    root = evidence / "source-copy"
    root.mkdir(parents=True)
    for index in range(n_simple + n_complex):
        job_id = "job_" + f"{index + 1:024x}"
        job = _background_job(root)
        job.update(jobId=job_id, terminalEventId=job_id + ":terminal", notificationState="delivered",
                   logPath=str(root / "background_jobs/logs" / (job_id + ".log")))
        if index >= n_simple:
            job.update(status="running", processTreeEnded=False)
        _write_json(root / "background_jobs/jobs" / (job_id + ".json"), job)
        log = root / "background_jobs/logs" / (job_id + ".log")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_bytes(b"original historical log\n")
    (root / "background_jobs/jobs/job_unknown.json").write_bytes(b'{broken partial unknown')
    _write_json(root / "background_jobs/jobs/.completed-retention.json", {"retentionDays": 30})
    manifest = [{"path": p.relative_to(root).as_posix(), "kind": "file", "state": "stable",
                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
                for p in sorted(root.rglob("*")) if p.is_file()]
    _write_json(evidence / "snapshot-manifest.json", manifest)
    _write_json(evidence / "wp08-inventory.json", build_inventory(root, allow_real_profile=True))
    _write_json(evidence / "preservation-bundle-manifest.json", {"canonicalBundleProduced": False})
    digest = hashlib.sha256((evidence / "snapshot-manifest.json").read_bytes()).hexdigest()
    packet = tmp_path / "packet"
    build_packet(evidence, packet, expected_manifest_sha256=digest)
    return evidence, root, packet, digest


def context_fixture(tmp_path):
    evidence, root, packet, digest = evidence_for(tmp_path)
    # Already-isolated fixture; only the full rehearsal uses a second copy.
    service = service_for(root)
    service.owner.claim(0)
    service.owner.release()
    inventory, context, receipt = prepare(service, packet, instruction=INSTRUCTION, reference=REFERENCE,
        recorded_at=AT, original_root=str(root))
    return root, service_for(root), inventory, context, receipt, packet


def commit(root, context, receipt, target, inventory):
    return migration.migrate(root, target, context, apply=True, mark_read_only=True, preserve_incompatible=True,
        allow_real_profile=True, expected_digest=migration.plan_digest(inventory, context),
        user_authorization=receipt, expected_user_authorization_digest=receipt["receiptDigest"])


def test_all_51_import_user_owner_history_receipts_preserve_60_and_recover_crash(tmp_path):
    evidence, source, packet, digest = evidence_for(tmp_path, 51, 59)
    before = _snapshot(evidence)
    output = tmp_path / "p9d"
    result = asyncio.run(run(evidence, packet, output, instruction=INSTRUCTION, reference=REFERENCE, recorded_at=AT,
        original_root=str(source), expected_manifest_sha256=digest))
    assert result["simpleImported"] == result["historyReadable"] == result["logsReadable"] == 51
    assert result["originalKeyReceiptsAdopted"] == 51 and result["unrecognizedPreserved"] == 60
    assert result["manualDeletionEntries"] == 60 and result["effectsExecuted"] == result["newRuns"] == result["newOutbox"] == 0
    assert result["canonicalRows"] == {"job": 51, "migration_history": 51, "migration_receipt": 51,
                                       "migration_unrecognized": 60, "runtime_activation": 1}
    assert result["unknownSettled"] == 0 and result["activeLegacyMigratedSet"] == 0
    assert result["allLegacyZeroProven"] is False and result["remainingLegacyIdentities"] == 60
    assert result["restartCountsUnchanged"] and result["offlineReplayWrites"] == 0
    failure = json.loads((output / "failure-recovery.json").read_text())
    assert failure["crashExit"] == 77 and all(n == 0 for n in failure["afterCrash"].values())
    assert all(n == 0 for n in failure["afterWriteFailure"].values())
    receipt = json.loads((output / "user-authorization.json").read_text(encoding="utf-8"))
    assert receipt["principal"] == receipt["owner"] == USER and receipt["signature"] is None
    assert len(receipt["records"]) == len(receipt["selectedMigrationIds"]) == 51
    assert _snapshot(evidence) == before
    assert not result["manualDeletionExecuted"]


@pytest.mark.parametrize("fault", ["agent", "placeholder", "signature", "missing_pin", "wrong_pin", "context_hash", "selection", "owner"])
def test_user_audit_rejects_self_approval_placeholder_and_scope_changes(tmp_path, fault):
    root, service, inventory, context, receipt, _ = context_fixture(tmp_path)
    receipt, context = copy.deepcopy(receipt), copy.deepcopy(context)
    if fault == "agent":
        receipt["provenance"]["channel"] = "agent"
    elif fault == "placeholder":
        receipt["provenance"]["reference"] = "conversation/user/placeholder-self-approved"
    elif fault == "signature":
        receipt["signature"] = "REVIEWED"
    if fault in {"agent", "placeholder", "signature"}:
        receipt.pop("receiptDigest")
        receipt["receiptDigest"] = fc.digest(receipt)
        context["userAuthorizationDigest"] = receipt["receiptDigest"]
    if fault == "context_hash":
        context["importPolicyBasis"] = "self-approved"
    elif fault == "selection":
        context["selectedMigrationIds"] = []
    elif fault == "owner":
        next(iter(context["records"].values()))["owner"] = {"type": "agent", "id": "self"}
    expected = None if fault == "missing_pin" else "0" * 64 if fault == "wrong_pin" else receipt["receiptDigest"]
    before = _snapshot(root)
    with pytest.raises(ValueError, match="user_authorization"):
        migration.migrate(root, tmp_path / "canonical/target.json", context, apply=True,
            user_authorization=receipt, expected_user_authorization_digest=expected)
    assert _snapshot(root) == before and not (tmp_path / "canonical").exists()


def test_embedded_user_approval_cannot_supply_its_own_trust_pin(tmp_path):
    root, _, _, context, receipt, _ = context_fixture(tmp_path)
    context["approved"] = True
    with pytest.raises(ValueError, match="externally pinned receipt required"):
        migration.migrate(root, tmp_path / "target.json", context, apply=True)
    assert not (tmp_path / "target.json").exists()


def test_runtime_rejects_unpinned_user_audit_and_preserves_closed_writer(tmp_path):
    root, service, inventory, context, receipt, _ = context_fixture(tmp_path)
    target = tmp_path / "bundle.json"
    commit(root, context, receipt, target, inventory)
    bundle = migration.read_bundle(target)
    spec = {"path": str(target), "expected_hash": hashlib.sha256(target.read_bytes()).hexdigest(),
            "expected_plan_digest": bundle["planDigest"], "active_ids": []}
    with pytest.raises(fc.ContractError, match="externally pinned receipt"):
        asyncio.run(service.start(activation=spec))
    assert all(n == 0 for n in counts(service).values()) and service.owner.status()["state"] == "failed"


def test_manual_deletion_packet_is_inert_scoped_and_includes_dependencies(tmp_path):
    evidence, root, packet, _ = evidence_for(tmp_path)
    before = _snapshot(evidence)
    rows = manual_deletion_packet(packet, tmp_path / "manual", original_root="D:/original-data")
    assert len(rows) == 2 and all(r["recognition"] == "unrecognized" for r in rows)
    assert all(r["dependenciesBeforeDelete"] and r["sourceFileSha256"].startswith("sha256:") for r in rows)
    assert all(r["manualCommand"].startswith("Remove-Item -LiteralPath 'D:/original-data/background_jobs/jobs/") for r in rows)
    assert all("-Confirm" in r["manualCommand"] and "-Recurse" not in r["manualCommand"] for r in rows)
    assert _snapshot(evidence) == before and not list((tmp_path / "manual").glob("*.ps1"))


def test_history_reads_without_legacy_source_loader_and_rejects_log_escape(tmp_path, monkeypatch):
    root, service, inventory, context, receipt, _ = context_fixture(tmp_path)
    target = tmp_path / "bundle.json"
    commit(root, context, receipt, target, inventory)
    bundle = migration.read_bundle(target)
    spec = {"path": str(target), "expected_hash": hashlib.sha256(target.read_bytes()).hexdigest(),
            "expected_plan_digest": bundle["planDigest"], "active_ids": [],
            "expected_user_authorization_digest": receipt["receiptDigest"]}
    entry = bundle["entries"][0]
    source_path = root / entry["sourceFile"]
    original_read = Path.read_bytes
    def block_legacy(path):
        if path == source_path:
            raise AssertionError("legacy source loader used")
        return original_read(path)
    async def check():
        await service.start(activation=spec)
        try:
            monkeypatch.setattr(Path, "read_bytes", block_legacy)
            doc = migration_history.read_original(service, entry["id"], actor=USER)
            old = migration_history.read_receipt(service, doc["source"]["terminalEventId"], actor=USER)
            assert old["state"] == "historical-delivered" and old["deliveryAttempted"] is False
            assert migration_history.read_log(service, entry["id"], actor=USER) == b"original historical log\n"
            doc["log"]["relativePath"] = "../outside.log"
            with service.transaction() as db:
                service._save(db, "migration_history", entry["migrationId"], doc)
            with pytest.raises(fc.ContractError, match="escapes profile"):
                migration_history.read_log(service, entry["id"], actor=USER)
            assert service.executor.calls == []
        finally:
            await service.close()
    asyncio.run(check())


def test_runtime_replay_rejects_changed_original_key_receipt(tmp_path):
    root, service, inventory, context, receipt, _ = context_fixture(tmp_path)
    target = tmp_path / "bundle.json"
    commit(root, context, receipt, target, inventory)
    bundle = migration.read_bundle(target)
    spec = {"path": str(target), "expected_hash": hashlib.sha256(target.read_bytes()).hexdigest(),
            "expected_plan_digest": bundle["planDigest"], "active_ids": [],
            "expected_user_authorization_digest": receipt["receiptDigest"]}
    async def check():
        await service.start(activation=spec)
        try:
            doc = migration_history.read_original(service, bundle["entries"][0]["id"], actor=USER)
            key = doc["source"]["terminalEventId"]
            with service.transaction() as db:
                old = service._get(db, "migration_receipt", key)
                old["deliveryAttempted"] = True
                service._save(db, "migration_receipt", key, old)
        finally:
            await service.close()
        restarted = service_for(root)
        with pytest.raises(fc.ContractError, match="receipt drift"):
            await restarted.start(activation=spec)
        assert restarted.task is None and restarted.executor.calls == []
        assert restarted.owner.status()["state"] == "failed"
    asyncio.run(check())


def test_retention_metadata_is_preserved_without_allocating_an_identity(tmp_path):
    root, _, inventory, context, receipt, _ = context_fixture(tmp_path)
    relative = "background_jobs/jobs/.completed-retention.json"
    before = (root / relative).read_bytes()
    target = tmp_path / "bundle.json"
    commit(root, context, receipt, target, inventory)
    bundle = migration.read_bundle(target)
    metadata = [e for e in bundle["preserved"] if e["sourceFile"] == relative]
    assert len(metadata) == 1 and metadata[0]["originalId"] is None and not metadata[0].get("migrationId")
    assert metadata[0]["targetIds"] == [] and metadata[0]["executable"] is False
    assert base64.b64decode(bundle["sourceBackups"][relative]["bytes"]) == before
    assert (root / relative).read_bytes() == before
    assert len(bundle["entries"]) == 1
