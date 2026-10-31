"""R2 owned synthetic source -> existing P9D runtime import -> OS cold reopen.

Never points to a real profile, opens a provider or creates a second importer.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import json
import shutil
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "tests")]

from packages.jobs import final_contracts as fc, migration_history
from packages.jobs.migration_authorization import USER
from packages.jobs.runtime_owner import OwnerFence
from scripts.job_final_migration import apply as migration
from scripts.job_final_migration.inventory import build_inventory
from scripts.job_final_migration.p9d_rehearsal import counts, run as p9d_run, service_for, assert_fenced
from scripts.job_final_migration.p9d_support import prepare
from scripts.job_final_migration.review_packet import build_packet
from scripts.job_final_migration.source_coverage import inventory_coverage
from tests.test_job_final_migration import _background_job, _write_json

INSTRUCTION = ("Synthetic-only test user instruction: import the exact simple synthetic background records as "
               "user/local into a NEW owned NoEffects profile. Keep all Jobs disabled and all incompatible, "
               "active, pending and unknown identities unrecognized, unchanged, unexecuted and unadopted. "
               "This fixture is not a real user approval, grant or production migration authorization.")
REFERENCE = "conversation/user/synthetic-runtime-readiness-r2"
AT = "2026-10-29T16:00:00+00:00"  # Fixed synthetic clock, not a real message timestamp.


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def fixture(output):
    evidence = output / "snapshot"
    root = evidence / "source-copy"
    root.mkdir(parents=True)
    states = ("completed", "completed", "running", "uncertain", "pending-notice")
    for i, state in enumerate(states):
        job_id = "job_r2_" + str(i)
        record = _background_job(root)
        log = root / "background_jobs/logs" / (job_id + ".log")
        record.update(jobId=job_id, terminalEventId="terminal:" + job_id,
                      logPath=str(log), notificationState="delivered")
        if state in {"running", "uncertain"}:
            record.update(status=state, processTreeEnded=False)
        elif state == "pending-notice":
            record["notificationState"] = "pending"
        _write_json(root / "background_jobs/jobs" / (job_id + ".json"), record)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_bytes(("synthetic historical log " + job_id + "\n").encode())
    (root / "background_jobs/jobs/job_bad.json").write_bytes(b'{unknown partial raw')
    _write_json(root / "background_jobs/jobs/job_future.json", {"jobId": "job_future", "kind": "future"})
    for rel, raw in {
        "scheduler/drive-cursor.json": b'{"next":"unmapped-cursor"}',
        "scheduler/time-events.sqlite3": b"opaque source time SQLite fixture; never connect",
        "independent/outbox.sqlite3": b"opaque unknown side-effect/outbox fixture; never connect",
        "sessions/queue-pending.json": b'{"pending":"unadopted"}',
        "unknown-profile/blob.bin": b"unknown schema bytes\x00\xff",
        "background_jobs/pins.json": b'{"job_r2_0":true}',
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = [{"path": name, "kind": "file", "state": "stable", "sha256": sha,
                 "bytes": (root / name).stat().st_size} for name, sha in hashes(root).items()]
    _write_json(evidence / "snapshot-manifest.json", manifest)
    _write_json(evidence / "wp08-inventory.json", build_inventory(root, allow_real_profile=True))
    _write_json(evidence / "preservation-bundle-manifest.json", {"canonicalBundleProduced": False})
    digest = hashlib.sha256((evidence / "snapshot-manifest.json").read_bytes()).hexdigest()
    packet = output / "review-packet"
    build_packet(evidence, packet, expected_manifest_sha256=digest)
    return evidence, root, packet, digest


def prepared_case(output):
    """Fresh pre-import negative-control fixture; reuse production prepare/apply."""
    output.mkdir(parents=True)
    evidence, source, packet, _ = fixture(output)
    profile = output / "profile"
    shutil.copytree(source, profile)
    service = service_for(profile)
    service.owner.claim(0)
    service.owner.release()
    inventory, context, receipt = prepare(service, packet, instruction=INSTRUCTION, reference=REFERENCE,
                                         recorded_at=AT, original_root=str(source))
    target = output / "bundle.json"
    migration.migrate(profile, target, context, apply=True, mark_read_only=True,
        preserve_incompatible=True, allow_real_profile=True, expected_digest=migration.plan_digest(inventory, context),
        user_authorization=receipt, expected_user_authorization_digest=receipt["receiptDigest"])
    bundle = migration.read_bundle(target)
    spec = {"path": str(target), "expected_hash": hashlib.sha256(target.read_bytes()).hexdigest(),
            "expected_plan_digest": bundle["planDigest"], "active_ids": [],
            "expected_user_authorization_digest": receipt["receiptDigest"]}
    return source, profile, service_for(profile), target, spec


async def cold(profile, spec):
    service = service_for(profile)
    before = counts(service)
    await service.start(activation=spec)
    try:
        rows = []
        with service.transaction(readonly=True) as db:
            for stored in service._all(db, "job"):
                job = fc.validate_object("job", stored["canonical"])
                state = stored["finalRuntime"]
                assert job["state"] == "disabled"
                assert all(not state[k] for k in ("runs", "outbox", "effects"))
                rows.append({"id": job["id"], "state": job["state"], "schemaVersion": job["schemaVersion"]})
        assert counts(service) == before and not service.executor.calls
        epoch = service.owner.status()
        assert_fenced(profile)
        return {"pid": __import__("os").getpid(), "counts": before, "objects": rows,
                "owner": epoch, "effects": len(service.executor.calls), "legacyWriterRejected": True}
    finally:
        await service.close()


async def run(output):
    output = Path(output).absolute()
    assert not output.exists(), "Fresh owned output required"
    output.mkdir(parents=True)
    evidence, source, packet, manifest_sha = fixture(output)
    before = hashes(source)
    _write_json(output / "source-before.json", before)
    coverage = inventory_coverage(source)
    _write_json(output / "source-coverage.json", coverage)
    private = output / "p9d-runtime"
    result = await p9d_run(evidence, packet, private, instruction=INSTRUCTION, reference=REFERENCE,
                           recorded_at=AT, original_root=str(source), expected_manifest_sha256=manifest_sha,
                           expected_simple=2, expected_preserved=5)
    profile = private / "profile"
    spec = json.loads((private / "activation-spec.json").read_text(encoding="utf-8"))
    bundle = migration.read_bundle(Path(spec["path"]))
    # Actual new Python process, no inherited service object, event loop or owner token.
    command = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "cold",
               "--profile", str(profile), "--spec", str(private / "activation-spec.json"),
               "--out", str(output / "cold-process.json")]
    child = subprocess.run(command, cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=45)
    _write_json(output / "cold-command.json", {"argv": command, "exit": child.returncode,
                                               "stdout": child.stdout, "stderr": child.stderr})
    assert child.returncode == 0, child.stderr
    cold_result = json.loads((output / "cold-process.json").read_text())
    assert cold_result["owner"]["epoch"] > result["ownerEpochAfterRestart"]
    # Existing owner APIs: stale original token/CAS and competing process-lifetime
    # lock must be refused without altering any source/canonical identity.
    active = service_for(profile)
    closed_epoch = active.owner.status()["epoch"]
    stale = service_for(profile)
    try:
        await stale.start(activation=spec, expected_epoch=closed_epoch - 1)
        raise AssertionError("Stale epoch admitted")
    except fc.ContractError as exc:
        assert exc.code == "revision_conflict"
    assert active.owner.status()["epoch"] == closed_epoch
    await active.start(activation=spec, expected_epoch=closed_epoch)
    try:
        competitor = service_for(profile)
        try:
            await competitor.start(activation=spec)
            raise AssertionError("Concurrent owner admitted")
        except fc.ContractError as exc:
            assert exc.code == "revision_conflict"
        assert not active.executor.calls and not competitor.executor.calls and competitor.task is None
        owner_live = active.owner.status()
    finally:
        await active.close()
    next_owner = service_for(profile)
    await next_owner.start(activation=spec)
    try:
        try:
            with active.owner.writer(canonical=True):
                raise AssertionError("Old epoch writer admitted")
        except fc.ContractError as exc:
            assert exc.code == "revision_conflict"
        assert not next_owner.executor.calls
    finally:
        await next_owner.close()
    # Offline rollback AFTER runtime import is intentionally not a runtime
    # rollback. The original store remains intact; rolled bundle cannot activate.
    original_counts = counts(service_for(profile))
    migration.rollback(profile, Path(spec["path"]), expected_digest=bundle["bundleDigest"])
    after_rollback = counts(service_for(profile))
    assert original_counts == after_rollback
    original_context = json.loads((private / "reviewed-context.json").read_text())
    receipt = json.loads((private / "user-authorization.json").read_text())
    inv = build_inventory(profile, allow_real_profile=True)
    replay = migration.migrate(profile, Path(spec["path"]), original_context, apply=True, mark_read_only=True,
        preserve_incompatible=True, allow_real_profile=True, expected_digest=migration.plan_digest(inv, original_context),
        user_authorization=receipt, expected_user_authorization_digest=receipt["receiptDigest"])
    assert replay["bundleDigest"] == bundle["bundleDigest"]
    bindings = {"sourceManifestSha256": manifest_sha, "bundleFileSha256": spec["expected_hash"],
                "bundleDigest": bundle["bundleDigest"], "planDigest": spec["expected_plan_digest"],
                "contextDigest": bundle["contextDigest"],
                "externallyPinnedSyntheticReceiptDigest": spec["expected_user_authorization_digest"],
                "canonicalSchemaManifestSha256": hashlib.sha256(
                    (REPO / "packages/jobs/schemas/final/contract_manifest.json").read_bytes()).hexdigest(),
                "runtimeSchemaArtifactsDigest": fc.digest(fc.schema_artifacts()),
                "actions": {k: v["ref"] for k, v in original_context["actions"].items()},
                "trustScope": "synthetic fixture only; no real user approval or grant; schema/source binding is QA evidence"}
    _write_json(output / "validation-bindings.json", bindings)
    # No reset, re-signed receipt or new identity is used to restore the artifact.
    assert hashes(source) == before
    copied = {r["path"]: r["sha256"] for r in json.loads((private / "isolated-copy-manifest.json").read_text())}
    assert all(hashlib.sha256((profile / name).read_bytes()).hexdigest() == sha for name, sha in copied.items())
    closed = service_for(profile)
    assert closed.owner.status()["state"] == "closed" and closed.owner.lock.file is None
    assert_fenced(profile)
    summary = {"source": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
               "baseline": "b288613e8a1862ea82d16a27d27992b715d3e0ba", "syntheticOnly": True,
               "p9d": result, "coldProcess": cold_result, "ownerBeforeRelease": owner_live,
               "staleEpochRejected": True, "concurrentOwnerRejected": True, "oldWriterRejected": True,
               "offlineRollbackRemovedRuntimeRows": False, "runtimeRowsUnchangedAcrossOfflineRollback": True,
               "sourceUnchanged": hashes(source) == before, "copiedOriginalsUnchanged": True,
               "coverageDigest": coverage["coverageDigest"], "wholeProfileMigrationComplete": False,
               "validationBindings": bindings,
               "resourceCleanup": {"childExit": child.returncode, "runtimeTaskClosed": True,
                                   "ownerLockReleased": True, "ownerState": closed.owner.status()["state"],
                                   "portsOpened": [], "temporaryFiles": [str(p) for p in output.rglob("*.tmp")]}}
    _write_json(output / "source-after.json", hashes(source))
    _write_json(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("run")
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("cold")
    for name in ("profile", "spec", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "cold":
        _write_json(args.out, asyncio.run(cold(args.profile, json.loads(args.spec.read_text()))))
    else:
        print(json.dumps(asyncio.run(run(args.output)), indent=2))
