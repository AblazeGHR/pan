"""P9C review-only artifacts: no actual profile, import, owner or service."""
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from packages.jobs import final_contracts as fc
from scripts.job_final_migration import apply as migration
from scripts.job_final_migration.inventory import build_inventory
from scripts.job_final_migration.mapping import Mapper
from scripts.job_final_migration.review_packet import build_packet
from tests.test_job_final_migration import _make_root, _background_job, _write_json, _snapshot
from tests.test_job_final_migration_apply import context_for


def evidence_for(tmp_path):
    evidence = tmp_path / "p9b-private"
    evidence.mkdir()
    root = _make_root(evidence)
    # _make_root creates data/; packet inputs deliberately use source-copy/.
    root.rename(evidence / "source-copy")
    root = evidence / "source-copy"
    complex_record = _background_job(root)
    complex_record.update(jobId="job_live", status="running", processTreeEnded=False)
    _write_json(root / "background_jobs/jobs/job_live.json", complex_record)
    (root / "background_jobs/jobs/job_future.json").write_bytes(b'{unknown-partial')
    manifest = [{"path": p.relative_to(root).as_posix(), "kind": "file", "state": "stable",
                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
                for p in sorted(root.rglob("*")) if p.is_file()]
    _write_json(evidence / "snapshot-manifest.json", manifest)
    _write_json(evidence / "wp08-inventory.json", build_inventory(root, allow_real_profile=True))
    _write_json(evidence / "preservation-bundle-manifest.json", {"canonicalBundleProduced": False})
    digest = hashlib.sha256((evidence / "snapshot-manifest.json").read_bytes()).hexdigest()
    return evidence, root, digest


def test_review_packet_is_stable_complete_and_source_readonly(tmp_path):
    evidence, root, digest = evidence_for(tmp_path)
    before = _snapshot(evidence)
    first, second = tmp_path / "review-a", tmp_path / "review-b"
    manifest = build_packet(evidence, first, expected_manifest_sha256=digest)
    repeated = build_packet(evidence, second, expected_manifest_sha256=digest)
    assert repeated == manifest
    assert _snapshot(first) == _snapshot(second)
    assert _snapshot(evidence) == before
    assert manifest["counts"]["simple"] == 6 and manifest["counts"]["complex"] == 1
    assert manifest["counts"]["unknown"] == 1
    assert manifest["canonicalTargetCreated"] is False and manifest["active_ids"] == []
    assert all(hashlib.sha256((first / name).read_bytes()).hexdigest() == sha for name, sha in manifest["files"].items())
    sealed = copy.deepcopy(manifest)
    packet_digest = sealed.pop("packetDigest")
    assert fc.digest(sealed) == packet_digest
    draft = json.loads((first / "reviewed-context.draft.json").read_text(encoding="utf-8"))
    assert draft["status"] == "draft_unapproved" and draft["executable"] is False
    assert draft["planDigest"] == migration.plan_digest(build_inventory(root, allow_real_profile=True), draft["context"])
    assert len(draft["context"]["selectedMigrationIds"]) == len(draft["context"]["records"]) == 6
    assert all(r["principal"] is None and r["oldOwnerStopped"] is None for r in draft["context"]["records"].values())
    rows = json.loads((first / "records.json").read_text(encoding="utf-8"))
    assert all(r["targetIds"] == [] for r in rows)
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", r["recordSha256"]) for r in rows)
    preserved = [r for r in rows if r["classification"] != "simple"]
    assert len(preserved) == 2 and all(r["historicalExecutionAllowed"] is False and r["r6Blocker"] for r in preserved)
    assert all(r["whyComplexOrUnknown"] and r["oldReadDependencies"] for r in preserved)
    unknown = next(r for r in rows if r["classification"] == "unknown")
    assert unknown["recordSha256"] == unknown["sourceFileSha256"]
    assert unknown["recordSha256Basis"].startswith("raw-source-bytes")
    mapper = Mapper(context_for(root))
    inputs = migration._inputs(root)
    by_id = {r["migrationId"]: r for r in rows}
    for item in build_inventory(root)["instances"]:
        if item["classificationDetail"] == "simple":
            expected_ids = mapper.map(item, migration._source(item, inputs))["targetIds"]
            assert set(by_id[item["migrationId"]]["proposedCanonicalMapping"]["proposedTargetIds"]) == set(expected_ids)
    assert not (tmp_path / "canonical").exists()


@pytest.mark.parametrize("review", [
    {"status": "draft_unapproved", "signature": None},
    {"status": "approved"},
    {"status": "approved", "signature": None},
    {"status": "approved", "signer": "TODO", "signature": "REVIEWED"},
    {"status": "approved", "signer": "self-reported", "signature": "a" * 128},
])
def test_review_manifest_cannot_authorize_even_otherwise_valid_context(tmp_path, review):
    root = _make_root(tmp_path)
    before = _snapshot(root)
    context = context_for(root)
    context["reviewManifest"] = review
    target = tmp_path / "canonical/target.json"
    with pytest.raises(migration.ApplyError, match="review_packet_not_authorized"):
        migration.migrate(root, target, context, apply=True,
                          expected_digest=migration.plan_digest(build_inventory(root), context))
    assert _snapshot(root) == before and not target.parent.exists()


@pytest.mark.parametrize("extract", [False, True])
def test_generated_draft_and_nested_context_are_rejected_before_target_creation(tmp_path, extract):
    evidence, root, digest = evidence_for(tmp_path)
    output = tmp_path / "review"
    build_packet(evidence, output, expected_manifest_sha256=digest)
    before = _snapshot(evidence)
    draft = json.loads((output / "reviewed-context.draft.json").read_text(encoding="utf-8"))
    context = draft["context"] if extract else draft
    target = tmp_path / "canonical/target.json"
    with pytest.raises(migration.ApplyError, match="review_packet_not_authorized"):
        migration.migrate(root, target, context, apply=True, expected_digest=draft["planDigest"], preserve_incompatible=True)
    assert not target.parent.exists() and _snapshot(evidence) == before


def test_unsigned_packet_manifest_cannot_be_applied_even_if_status_is_edited(tmp_path):
    evidence, root, digest = evidence_for(tmp_path)
    manifest = build_packet(evidence, tmp_path / "review", expected_manifest_sha256=digest)
    manifest["status"] = "approved"
    with pytest.raises(migration.ApplyError, match="review_packet_not_authorized"):
        migration.migrate(root, tmp_path / "canonical/target.json", manifest, apply=True)
    assert not (tmp_path / "canonical").exists()


def test_draft_cannot_gain_authority_by_removing_review_marker(tmp_path):
    evidence, root, digest = evidence_for(tmp_path)
    output = tmp_path / "review"
    build_packet(evidence, output, expected_manifest_sha256=digest)
    draft = json.loads((output / "reviewed-context.draft.json").read_text(encoding="utf-8"))
    context = draft["context"]
    context.pop("reviewManifest")
    with pytest.raises(migration.ApplyError, match="missing authority/fence attestation"):
        migration.migrate(root, tmp_path / "canonical/target.json", context, apply=True,
                          expected_digest=migration.plan_digest(build_inventory(root, allow_real_profile=True), context),
                          allow_real_profile=True, preserve_incompatible=True)
    assert not (tmp_path / "canonical").exists()


def test_draft_plan_digest_binds_selection_and_per_id_hashes(tmp_path):
    evidence, root, digest = evidence_for(tmp_path)
    output = tmp_path / "review"
    build_packet(evidence, output, expected_manifest_sha256=digest)
    draft = json.loads((output / "reviewed-context.draft.json").read_text(encoding="utf-8"))
    inventory = build_inventory(root, allow_real_profile=True)
    changed = copy.deepcopy(draft["context"])
    changed["selectedMigrationIds"].pop()
    assert migration.plan_digest(inventory, changed) != draft["planDigest"]
    changed = copy.deepcopy(draft["context"])
    next(iter(changed["records"].values()))["recordDigest"] = "sha256:" + "0" * 64
    assert migration.plan_digest(inventory, changed) != draft["planDigest"]


def test_packet_generation_rejects_tampered_source_before_any_output(tmp_path):
    evidence, root, digest = evidence_for(tmp_path)
    path = next((root / "background_jobs/jobs").glob("*.json"))
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(migration.ApplyError, match="snapshot file hash mismatch"):
        build_packet(evidence, tmp_path / "review", expected_manifest_sha256=digest)
    assert not (tmp_path / "review").exists()


def test_packet_generation_rejects_wrong_manifest_and_writing_into_evidence(tmp_path):
    evidence, root, digest = evidence_for(tmp_path)
    before = _snapshot(evidence)
    with pytest.raises(migration.ApplyError, match="snapshot manifest hash mismatch"):
        build_packet(evidence, tmp_path / "review", expected_manifest_sha256="0" * 64)
    with pytest.raises(migration.ApplyError, match="separate from P9B"):
        build_packet(evidence, evidence / "packet", expected_manifest_sha256=digest)
    assert _snapshot(evidence) == before and not (tmp_path / "review").exists()
