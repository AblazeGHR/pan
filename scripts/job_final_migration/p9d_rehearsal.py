"""P9D actual snapshot import into a NEW isolated profile, no HTTP/service/port.

Only a supplied P9B source-copy is read. No real-source path is opened; the
original root is inert metadata for log relocation and manual deletion text.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

from packages.jobs import final_contracts as fc, migration_history
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_service import FinalService
from packages.jobs.migration_authorization import USER
from packages.jobs.runtime_owner import OwnerFence
from scripts.p9_practical_rehearsal import digest_file, dump
from . import apply as migration
from .inventory import build_inventory
from .mapping import require
from .p9d_support import prepare, manual_deletion_packet


class NoEffects:
    def __init__(self):
        self.calls = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("P9D never authorizes effects")


def service_for(profile):
    return FinalService(Path(profile) / "job-final", CanonicalUserAuth(Path(profile) / "p9d-auth"), executor=NoEffects())


def counts(service):
    with service.transaction(readonly=True) as db:
        return {kind: db.execute("SELECT count(*) FROM final_documents WHERE kind=?", (kind,)).fetchone()[0]
                for kind in ("job", "migration_history", "migration_receipt", "migration_unrecognized", "runtime_activation")}


def assert_fenced(profile):
    try:
        with OwnerFence(profile).writer():
            raise AssertionError("Legacy writer unexpectedly admitted")
    except fc.ContractError:
        return True


def crash_import(profile, spec, stop_at=17):
    service = service_for(profile)
    save = service._save
    seen = 0
    def crashing(db, kind, key, value):
        nonlocal seen
        save(db, kind, key, value)
        if kind == "job":
            seen += 1
            if seen == stop_at:
                os._exit(77)
    service._save = crashing
    asyncio.run(service.start(activation=spec))
    raise AssertionError("Crash injection did not occur")


async def run(evidence, packet, private, *, instruction, reference, recorded_at, original_root,
              expected_manifest_sha256, expected_simple=51, expected_preserved=60, crash=True):
    evidence, packet, private = Path(evidence).resolve(), Path(packet).resolve(), Path(private).absolute()
    source = evidence / "source-copy"
    private = migration.outside_root(source, private)
    require(not private.is_relative_to(evidence) and not evidence.is_relative_to(private)
            and not private.is_relative_to(packet) and not packet.is_relative_to(private), "Separate P9D output required")
    require(not private.exists(), "P9D output exists; never overwrite a rehearsal")
    manifest_path = evidence / "snapshot-manifest.json"
    require(digest_file(manifest_path) == expected_manifest_sha256, "Source manifest mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(not any(r["state"].startswith("blocked") for r in manifest), "Snapshot has blocked captures")
    require(not any(r["path"] == "job-runtime-owner.sqlite3" and r["kind"] == "file" for r in manifest),
            "Never adopt or reset a copied real owner authority")
    private.mkdir(parents=True)
    profile = private / "profile"
    profile.mkdir()
    copied = []
    for row in manifest:
        if row["kind"] != "file" or not row["state"].startswith("stable"):
            continue
        old, new = source / row["path"], profile / row["path"]
        require(old.resolve().is_relative_to(source) and not any(p.is_symlink() or
                (hasattr(p, "is_junction") and p.is_junction()) for p in (old, *old.parents)), "Snapshot link/path escape")
        require(digest_file(old) == row["sha256"], "Snapshot source SHA changed")
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(old, new)
        require(digest_file(new) == row["sha256"], "P9D isolated copy SHA mismatch")
        copied.append(row)
    dump(private / "isolated-copy-manifest.json", copied)
    service = service_for(profile)
    # A real local fence for this new profile, never a claim about source PIDs.
    service.owner.claim(0)
    service.owner.release()
    require(assert_fenced(profile), "Isolated fence absent")
    inventory, context, receipt = prepare(service, packet, instruction=instruction, reference=reference,
        recorded_at=recorded_at, original_root=original_root)
    require(inventory["summary"]["simple"] == expected_simple and inventory["summary"]["total"] - expected_simple == expected_preserved,
            "Unexpected selected/preserved counts")
    dump(private / "user-authorization.json", receipt)
    dump(private / "reviewed-context.json", context)
    dump(private / "inventory.json", inventory)
    manual = manual_deletion_packet(packet, private / "manual-deletion", original_root=original_root)
    target = private / "canonical-bundle.json"
    kwargs = dict(apply=True, mark_read_only=True, preserve_incompatible=True, allow_real_profile=True,
                  expected_digest=migration.plan_digest(inventory, context), user_authorization=receipt,
                  expected_user_authorization_digest=receipt["receiptDigest"])
    original_replace = os.replace
    def failing_replace(src, dst):
        if Path(dst) == target:
            raise OSError("P9D injected bundle publish failure")
        return original_replace(src, dst)
    try:
        with patch.object(migration.os, "replace", failing_replace):
            migration.migrate(profile, target, context, **kwargs)
        raise AssertionError("Expected publish failure")
    except OSError as exc:
        require("P9D injected" in str(exc), "Unexpected write failure")
    require(not target.exists() and not target.with_name(target.name + ".lock").exists(), "Failed publication left a target/lock")
    report = migration.migrate(profile, target, context, **kwargs)
    dump(private / "apply-report.json", report)
    bundle = migration.read_bundle(target)
    spec = {"path": str(target), "expected_hash": digest_file(target), "expected_plan_digest": bundle["planDigest"],
            "active_ids": [], "expected_user_authorization_digest": receipt["receiptDigest"]}
    dump(private / "activation-spec.json", spec)
    # Owner instances retain their epoch token after release; reopening uses
    # a new instance, as a real process restart would.
    service = service_for(profile)
    fault_rows = max(1, min(17, expected_simple))
    save, seen = service._save, 0
    def fail_history(db, kind, key, value):
        nonlocal seen
        save(db, kind, key, value)
        if kind == "migration_history":
            seen += 1
            if seen == fault_rows:
                raise OSError("P9D injected history transaction failure")
    service._save = fail_history
    try:
        await service.start(activation=spec)
        raise AssertionError("Expected import transaction failure")
    except OSError as exc:
        require("P9D injected" in str(exc), "Unexpected import failure")
    require(all(value == 0 for value in counts(service).values()), "Import failure left partial graph/history")
    failure = {"bundlePublishFailureTargetAbsent": True, "historyWriteFailure": str(service.owner.status()["state"]),
               "afterWriteFailure": counts(service), "legacyStillFenced": assert_fenced(profile)}
    if crash:
        child = subprocess.run([sys.executable, "-m", "scripts.job_final_migration.p9d_rehearsal", "crash-import",
            "--profile", str(profile), "--spec", str(private / "activation-spec.json"), "--stop-at", str(fault_rows)],
            capture_output=True, text=True, timeout=45)
        (private / "crash-child.log").write_text(child.stdout + child.stderr, encoding="utf-8")
        require(child.returncode == 77, "Crash child did not exit at injected boundary")
        failure.update(crashExit=77, afterCrash=counts(service), afterCrashOwner=service.owner.status(),
                       afterCrashLegacyFenced=assert_fenced(profile))
        require(all(value == 0 for value in counts(service).values()), "Crash left a partial canonical graph")
    dump(private / "failure-recovery.json", failure)
    restored = service_for(profile)
    await restored.start(activation=spec)
    try:
        require(len(restored.list("job", actor=USER)["items"]) == expected_simple, "Canonical import count mismatch")
        mapping, settled, logs = [], 0, 0
        for entry in bundle["entries"]:
            history = migration_history.read_original(restored, entry["id"], actor=USER)
            require(base64.b64decode(history["rawBytesBase64"]) == (profile / entry["sourceFile"]).read_bytes(), "Historical bytes differ")
            migration_history.read_log(restored, entry["id"], actor=USER)
            logs += 1
            for object_id in entry["targetIds"]:
                obj = restored.get("job", object_id, actor=USER)
                require(obj["owner"] == USER and obj["createdBy"] == USER and obj["state"] == "disabled", "Owner/state mismatch")
            if history["source"].get("notificationState") == "delivered":
                old_key = history["source"]["terminalEventId"]
                observed = migration_history.read_receipt(restored, old_key, actor=USER)
                require(observed["originalId"] == entry["id"] and not observed["deliveryAttempted"], "Original key not settled safely")
                settled += 1
            require(assert_fenced(profile), "Migrated legacy writer reopened")
            mapping.append({"originalId": entry["id"], "migrationId": entry["migrationId"], "canonicalIds": entry["targetIds"],
                            "sourceFileSha256": entry["originalDigest"], "historyReadable": True, "legacyWriterFenced": True})
        for item in bundle["preserved"]:
            if item.get("migrationId"):
                doc = migration_history.read_original(restored, item["originalId"], actor=USER, unrecognized=True)
                require(doc["recognition"] == "unrecognized" and not doc["executable"] and not doc["unknownSettlementAttempted"], "Preserved record changed disposition")
                require(base64.b64decode(doc["rawBytesBase64"]) == (profile / item["sourceFile"]).read_bytes(), "Preserved bytes changed")
        initial = counts(restored)
        require(restored.executor.calls == [] and restored.creators.list() == [], "Unexpected execution/creator")
        epoch = restored.owner.token[0]
        dump(private / "id-mapping.json", mapping)
    finally:
        await restored.close()
    rebooted = service_for(profile)
    await rebooted.start(activation=spec)
    try:
        require(counts(rebooted) == initial and rebooted.owner.token[0] > epoch and rebooted.executor.calls == [], "Restart duplicated or executed records")
        with rebooted.transaction(readonly=True) as db:
            states = [j["finalRuntime"] for j in rebooted._all(db, "job")]
            require(all(not s["runs"] and not s["outbox"] and not s["effects"] for s in states), "History created new effects")
        restart_epoch = rebooted.owner.token[0]
    finally:
        await rebooted.close()
    replay = migration.migrate(profile, target, context, **kwargs)
    require(replay["targetWrites"] == 0 and replay["idempotentReplay"], "Offline replay wrote duplicates")
    require(digest_file(target) == spec["expected_hash"], "Bundle mutated after import/restart")
    require(all(digest_file(profile / r["path"]) == r["sha256"] and digest_file(source / r["path"]) == r["sha256"]
                for r in copied), "Source or isolated original bytes changed")
    result = {"simpleImported": expected_simple, "unrecognizedPreserved": expected_preserved,
        "manualDeletionEntries": len(manual), "canonicalRows": initial, "historyReadable": len(mapping), "logsReadable": logs,
        "originalKeyReceiptsAdopted": settled, "receiptBasis": "source-stated delivered; not a new or externally verified delivery",
        "effectsExecuted": 0, "newRuns": 0, "newOutbox": 0, "unknownSettled": 0, "activeIds": [],
        "activeLegacyMigratedSet": 0, "legacyZeroScope": "51 imported identities in this isolated profile only" if expected_simple == 51 else "imported set in isolated profile",
        "remainingLegacyIdentities": expected_preserved, "allLegacyZeroProven": False, "sourceFilesUnchanged": len(copied),
        "offlineReplayWrites": 0, "restartCountsUnchanged": True, "ownerEpochBeforeRestart": epoch, "ownerEpochAfterRestart": restart_epoch,
        "authorizationReceiptDigest": receipt["receiptDigest"], "packetDigest": receipt["packetDigest"],
        "bundleDigest": bundle["bundleDigest"], "planDigest": bundle["planDigest"], "runtimeImported": True,
        "productionMigrated": False, "manualDeletionExecuted": False, "realSourceOpened": False}
    dump(private / "result.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    crash_parser = sub.add_parser("crash-import")
    crash_parser.add_argument("--profile", type=Path, required=True)
    crash_parser.add_argument("--spec", type=Path, required=True)
    crash_parser.add_argument("--stop-at", type=int, default=17)
    run_parser = sub.add_parser("run")
    for field in ("evidence", "packet", "private", "instruction"):
        run_parser.add_argument("--" + field, type=Path, required=True)
    for field in ("reference", "recorded-at", "original-root", "expected-manifest-sha256"):
        run_parser.add_argument("--" + field, required=True)
    args = parser.parse_args()
    if args.mode == "crash-import":
        crash_import(args.profile, json.loads(args.spec.read_text(encoding="utf-8")), args.stop_at)
    else:
        result = asyncio.run(run(args.evidence, args.packet, args.private, instruction=args.instruction.read_text(encoding="utf-8"),
            reference=args.reference, recorded_at=args.recorded_at, original_root=args.original_root,
            expected_manifest_sha256=args.expected_manifest_sha256))
        print(json.dumps(result))
