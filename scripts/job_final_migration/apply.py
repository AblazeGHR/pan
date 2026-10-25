"""Transactional offline migration bundle; never imports or mutates a runtime.

One fsync + atomic replace publishes the entire graph, allocation table,
provenance, source backups and read-only intent. No per-record commit exists.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import os
import tempfile
from pathlib import Path

from packages.jobs import final_contracts as fc

from .inventory import SIMPLE, build_inventory, is_within_root
from .mapping import ApplyError, Mapper, require

BUNDLE_SCHEMA = "pan.job-final-migration.bundle/v1"


def plan_digest(inventory: dict, context: dict) -> str:
    """Bind the reviewed plan to both source classification and operator facts."""
    return fc.digest({"inventoryDigest": fc.digest(inventory), "contextDigest": fc.digest(context),
                      "mappingVersion": "1"})


def outside_root(root: Path, path: str | Path) -> Path:
    destination = Path(path).expanduser().absolute()
    # Refuse links even when they currently resolve outside root: a writer must
    # not follow an operator-controlled alias to another store/backup/lock.
    require(not any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
                    for p in (destination, *destination.parents)), "output path contains a link")
    destination = destination.resolve()
    require(not is_within_root(root, destination), "refusing to write output inside the scanned root")
    return destination


def _hash(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _inputs(root: Path) -> dict[str, bytes]:
    paths = [root / p for p in ("actions/registry.json", "automation/registry.json",
             "background_jobs/schedule_templates.json", "background_jobs/runs.jsonl", "scheduler/runs.jsonl")]
    for name in ("background_jobs/jobs", "scheduler/tasks"):
        paths.extend(sorted((root / name).glob("*.json")))
    result = {}
    for path in paths:
        if path.is_file():
            require(is_within_root(root, path), "source path escapes root")
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _load(raw: bytes):
    return fc.loads(raw.decode("utf-8-sig"))


def _source(item: dict, sources: dict[str, bytes]) -> dict:
    value = _load(sources[item["sourceFile"]])
    if item["type"] in {"MIG-TRIGGER", "MIG-TEMPLATE"}:
        collection = value["triggers" if item["type"] == "MIG-TRIGGER" else "jobTemplates"]
        candidates = list(collection.values()) if isinstance(collection, dict) else collection
        matches = [s for s in candidates if s.get("id") == item["id"]
                   and "sha256:" + fc.digest(s) == item["recordDigest"]]
        require(len(matches) == 1, "plan_stale: source registry record is ambiguous")
        return matches[0]
    return value


def _seal(bundle: dict) -> dict:
    result = copy.deepcopy(bundle)
    result.pop("bundleDigest", None)
    result["bundleDigest"] = fc.digest(result)
    return result


def read_bundle(path: Path) -> dict:
    bundle = _load(path.read_bytes())
    require(bundle.get("toolSchema") == BUNDLE_SCHEMA and bundle.get("mappingVersion") == "1",
            "unsupported migration operation version; refusing recovery")
    require(_seal(bundle)["bundleDigest"] == bundle.get("bundleDigest"), "bundle digest mismatch")
    for record in bundle["canonical"]:
        fc.validate_object(record["kind"], record["object"])
    require(bundle["state"] in {"committed-offline", "rolled-back-offline"}, "unknown migration operation state")
    return bundle


def _atomic_write(path: Path, raw: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _bytes(value: dict) -> bytes:
    return (fc.canonical_json(value) + "\n").encode("utf-8")


def _publish(target: Path, bundle: dict, check) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_name(target.name + ".lock")
    require(not lock.is_symlink(), "lock path contains a link")
    # Never guess that an existing lock is stale, even after a crash.
    stream = lock.open("xb")
    try:
        previous = read_bundle(target) if target.exists() else None
        if previous:
            require(previous["operationId"] == bundle["operationId"],
                    "target belongs to another operation; use a new target")
            require(previous["sourceDigest"] == bundle["sourceDigest"] and
                    previous["contextDigest"] == bundle["contextDigest"], "plan_stale: committed inputs changed")
            if previous == bundle:
                check()
                return False
            if bundle["state"] == "committed-offline" and previous["state"] == "committed-offline":
                require(not any(e["oldRecordReadOnly"] for e in previous["entries"]) or
                        all(e["oldRecordReadOnly"] for e in bundle["entries"]),
                        "read-only marker can only be removed by rollback")
            backup = target.with_name(target.name + ".bak")
            require(not backup.is_symlink() and not (hasattr(backup, "is_junction") and backup.is_junction()),
                    "backup path contains a link")
            _atomic_write(backup, target.read_bytes())
        check()
        _atomic_write(target, _bytes(bundle))
        return True
    finally:
        stream.close()
        lock.unlink()


def migrate(root: str | Path, target: str | Path, context: dict, *,
            apply: bool = False, mark_read_only: bool = False,
            expected_digest: str | None = None, allow_real_profile: bool = False) -> dict:
    """Build/commit a disabled canonical graph outside root. Default is dry-run.

    expected_digest is the planDigest from a reviewed context-bound dry-run.
    A committed bundle is an offline artifact, NOT runtime owner activation.
    """
    root = Path(root).resolve(strict=True)
    target = outside_root(root, target)
    inventory = build_inventory(root, allow_real_profile=allow_real_profile)
    inventory_digest = fc.digest(inventory)
    reviewed_digest = plan_digest(inventory, context)
    require(not apply or expected_digest == reviewed_digest, "plan_stale: --expected-digest required/mismatched")
    require(not inventory["summary"]["unknown"] and not inventory["scanErrors"],
            "unknown/corrupt source or scan error: refusing whole apply")
    sources = _inputs(root)
    classified_files = {item["sourceFile"] for item in inventory["instances"]}
    require(all(not name.startswith(("background_jobs/jobs/", "scheduler/tasks/"))
                or name in classified_files for name in sources), "unknown unclassified source file")
    require(fc.digest(build_inventory(root, allow_real_profile=allow_real_profile)) == inventory_digest,
            "plan_stale: inventory changed during snapshot")
    mapper = Mapper(context)
    entries = []
    for item in inventory["instances"]:
        if item["classification"] == SIMPLE:
            require(_hash(sources[item["sourceFile"]]) == item["originalDigest"], "plan_stale: source hash changed")
            entries.append({**copy.deepcopy(item), **mapper.map(item, _source(item, sources))})
    require(bool(entries), "no simple records to migrate")
    mapper.verify()
    source_digest = fc.digest({key: _hash(value) for key, value in sources.items()})
    context_digest = fc.digest(context)
    operation_id = "mop1_" + fc.digest({"root": str(root), "mappingVersion": "1"})[:24]
    def recheck():
        require(_inputs(root) == sources and fc.digest(build_inventory(
            root, allow_real_profile=allow_real_profile)) == inventory_digest,
            "plan_stale: source changed before commit")
    report = copy.deepcopy(inventory)
    report["inventoryDigest"] = inventory_digest
    report["planDigest"] = reviewed_digest
    report["contextDigest"] = context_digest
    report["operationId"] = operation_id
    report["target"] = str(target)
    report["runtimeActivated"] = False
    report["runtimeFenceVerified"] = False
    report["plan"]["targetIdAllocation"] = "stable-offline-bundle"
    for plan, entry in zip(report["plan"]["items"], entries):
        plan["targetIds"] = entry["targetIds"]
        plan["targetIdAllocation"] = "stable-offline-bundle"
    report["plan"]["executionAttempted"] = apply
    if not apply:
        recheck()
        return report
    if target.exists():
        previous = read_bundle(target)
        if previous["state"] == "committed-offline":
            mark_read_only = mark_read_only or any(e["oldRecordReadOnly"] for e in previous["entries"])
    for entry in entries:
        entry["status"] = "verified-offline"
        entry["suggestedAction"] = "review-disabled-canonical-bundle"
        entry["oldRecordReadOnly"] = mark_read_only
        entry["ownerSwitch"] = {"scope": "offline-bundle-only", "oldOwner": "stopped-attested",
            "newOwner": "canonical-disabled", "ownerEpoch": entry["attestation"]["oldOwnerEpoch"] + 1,
            "runtimeEnforced": False}
    bundle = _seal({"toolSchema": BUNDLE_SCHEMA, "mappingVersion": "1", "operationId": operation_id,
        "state": "committed-offline", "root": str(root), "inventoryDigest": inventory_digest,
        "planDigest": reviewed_digest,
        "sourceDigest": source_digest, "contextDigest": context_digest,
        "canonical": mapper.objects, "allocations": {v["migrationId"]: v["targetIds"] for v in entries},
        "entries": entries, "preserved": inventory["plan"]["preserved"],
        "sourceBackups": {key: {"sha256": _hash(raw), "encoding": "base64", "bytes": base64.b64encode(raw).decode("ascii")}
                          for key, raw in sources.items()},
        "runtimeActivated": False, "runtimeFenceVerified": False})
    wrote = _publish(target, bundle, recheck)
    report["readOnly"] = False
    report["migrationExecuted"] = True
    report["targetWrites"] = len(mapper.objects) if wrote else 0
    report["idempotentReplay"] = not wrote
    report["bundleDigest"] = bundle["bundleDigest"]
    report["plan"]["executable"] = True
    report["instances"] = [next((v for v in entries if v["migrationId"] == item["migrationId"]), item)
                           for item in inventory["instances"]]
    return report


def rollback(root: str | Path, target: str | Path, *, expected_digest: str,
             allow_real_profile: bool = False) -> dict:
    """Roll back the offline publication only, preserving allocations/evidence.

    No source restoration or runtime downgrade: all source bytes are untouched.
    The previous complete artifact is retained as .bak before atomic publication.
    """
    root = Path(root).resolve(strict=True)
    # Marker guard before any source read, including recovery.
    build_inventory(root, allow_real_profile=allow_real_profile)
    target = outside_root(root, target)
    previous = read_bundle(target)
    require(previous["root"] == str(root), "rollback root mismatch")
    require(previous["bundleDigest"] == expected_digest, "rollback digest mismatch")
    result = copy.deepcopy(previous)
    result["state"] = "rolled-back-offline"
    result["canonical"] = []
    for entry in result["entries"]:
        entry["status"] = "rolled-back-offline"
        entry["oldRecordReadOnly"] = False
        entry["ownerSwitch"]["newOwner"] = "none"
    result = _seal(result)
    def recheck():
        require(read_bundle(target)["bundleDigest"] == expected_digest, "rollback CAS conflict")
    _publish(target, result, recheck)
    return {"operationId": result["operationId"], "state": result["state"],
            "bundleDigest": result["bundleDigest"], "runtimeActivated": False,
            "plan": {"executable": False}, "sourceWrites": 0}
