"""Private P9 evidence collection. No server, network, source writes or activation.

Raw snapshots/manifests MUST stay in an ignored private directory. The copy is a
best-effort observation across time, never an application snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import collections
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding="utf-8")


def digest_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def strict_loads(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def walk(root):
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for item in entries:
                path = Path(item.path)
                st = item.stat(follow_symlinks=False)
                if item.is_symlink() or getattr(st, "st_file_attributes", 0) & 0x400:
                    yield "link", path, st
                elif item.is_dir(follow_symlinks=False):
                    yield "dir", path, st
                    stack.append(path)
                else:
                    yield "file", path, st


def snapshot(source, private, resume=False):
    source = source.resolve(strict=True)
    private = private.resolve()
    assert not private.is_relative_to(source)
    private.mkdir(parents=True, exist_ok=True)
    target = private / "source-copy"
    target.mkdir(exist_ok=resume)
    counters = {"files": 0, "bytes": 0, "directories": 0, "links": 0,
                "errors": 0, "changedDuringRead": 0, "copyHashMismatches": 0}
    top = {}
    started = time.time()
    last = started
    previous = {}
    manifest_path = private / "copy-manifest.jsonl"
    if resume and manifest_path.exists():
        recovery = private / ("copy-manifest-interrupted-" + str(time.time_ns()) + ".jsonl")
        shutil.copyfile(manifest_path, recovery)
        for line in recovery.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if ((row.get("kind") in {"file", "link"} and "disposition" not in row)
                        or row.get("disposition") == "blocked-link-not-followed"):
                    previous[row["path"]] = row
            except ValueError:
                pass  # Partial manifest line remains in the recovery evidence.

    def copy_one(path, before):
        rel = path.relative_to(source)
        destination = target / rel
        record = {"path": rel.as_posix(), "kind": "file"}
        if rel.as_posix() in previous:
            prior = previous[rel.as_posix()]
            return {**prior, "changedSinceInitialRead": (before.st_size, before.st_mtime_ns) !=
                    (prior["sourceSizeAfter"], prior["mtimeAfter"])}
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                # An interrupted worker may have left a partial, unmanifested
                # destination. Keep those bytes privately before retrying.
                partial = private / "interrupted-partials" / hashlib.sha256(str(rel).encode()).hexdigest()
                partial.parent.mkdir(exist_ok=True)
                destination.replace(partial)
                record["interruptedPartialSha256"] = digest_file(partial)
            h = hashlib.sha256()
            with path.open("rb") as src, destination.open("xb") as dst:
                for block in iter(lambda: src.read(4 * 1024 * 1024), b""):
                    dst.write(block)
                    h.update(block)
            after = path.stat()
            copied = digest_file(destination)
            changed = (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            record.update(bytes=destination.stat().st_size, sha256=h.hexdigest(), copySha256=copied,
                          changedDuringRead=changed, sourceSizeBefore=before.st_size, sourceSizeAfter=after.st_size,
                          mtimeBefore=before.st_mtime_ns, mtimeAfter=after.st_mtime_ns)
        except OSError as exc:
            record.update(disposition="copy-error", error=type(exc).__name__, detail=str(exc))
            if destination.is_file():
                partial = private / "failed-copy-partials" / hashlib.sha256(str(rel).encode()).hexdigest()
                partial.parent.mkdir(exist_ok=True)
                record["failedPartialSha256"] = digest_file(destination)
                destination.replace(partial)
        return record

    with manifest_path.open("w", encoding="utf-8") as manifest, ThreadPoolExecutor(max_workers=12) as pool:
        pending = set()
        def record_one(record):
            nonlocal last
            if record["kind"] == "link":
                counters["links"] += 1
            elif "copySha256" in record:
                counters["files"] += 1
                counters["bytes"] += record["bytes"]
                counters["changedDuringRead"] += int(record["changedDuringRead"])
                counters["copyHashMismatches"] += int(record["copySha256"] != record["sha256"])
                bucket = top.setdefault(Path(record["path"]).parts[0], {"files": 0, "bytes": 0})
                bucket["files"] += 1
                bucket["bytes"] += record["bytes"]
            else:
                counters["errors"] += 1
            manifest.write(json.dumps(record, ensure_ascii=True) + "\n")
            if time.time() - last > 20:
                manifest.flush()
                print(json.dumps(counters), flush=True)
                last = time.time()
        try:
            for kind, path, before in walk(source):
                rel = path.relative_to(source)
                destination = target / rel
                record = {"path": rel.as_posix(), "kind": kind}
                try:
                    if kind == "dir":
                        destination.mkdir(parents=True, exist_ok=True)
                        counters["directories"] += 1
                        continue
                    if kind == "link":
                        # No traversal, no links back to live profile/other roots.
                        record.update(disposition="blocked-link-not-followed",
                                      target=os.readlink(path))
                        record_one(record)
                    else:
                        pending.add(pool.submit(copy_one, path, before))
                        if len(pending) >= 96:
                            done, pending = wait(pending, return_when=FIRST_COMPLETED)
                            for future in done:
                                record_one(future.result())
                except OSError as exc:
                    record.update(disposition="copy-error", error=type(exc).__name__, detail=str(exc))
                    record_one(record)
            for future in pending:
                record_one(future.result())
        except OSError as exc:
            counters["errors"] += 1
            manifest.write(json.dumps({"disposition": "enumeration-error", "detail": str(exc)}) + "\n")
    result = {**counters, "topLevel": top, "elapsedSeconds": round(time.time() - started, 2),
              "source": str(source), "copy": str(target),
              "sourceAccess": "read-only file opens and metadata only",
              "applicationConsistentSnapshot": False,
              "completePhysicalCopy": counters["errors"] == counters["links"] == 0,
              "manifestSha256": digest_file(private / "copy-manifest.jsonl")}
    dump(private / "copy-summary.json", result)
    print(json.dumps(result), flush=True)


def audit(private):
    """Read only the finished private copy; retain every observation separately."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.job_final_migration.inventory import build_inventory
    from scripts.job_final_migration import apply as migration
    from packages.jobs import final_contracts as fc

    private = private.resolve(strict=True)
    root = private / "source-copy"
    summary_path = private / "copy-summary.json"
    manifest_path = private / "copy-manifest.jsonl"
    if (private / "snapshot-summary.json").exists():
        summary = json.loads((private / "snapshot-summary.json").read_text(encoding="utf-8"))
        manifest = json.loads((private / "snapshot-manifest.json").read_text(encoding="utf-8"))
        summary["manifestSha256"] = digest_file(private / "snapshot-manifest.json")
        manifest = [{**row, "copySha256": row.get("sha256")} for row in manifest
                    if row.get("kind") == "file" and row.get("state", "").startswith("stable")]
    else:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        manifest = []
        with manifest_path.open(encoding="utf-8") as stream:
            for line in stream:
                manifest.append(json.loads(line))
        manifest = [row for row in manifest if row.get("kind") == "file" and "copySha256" in row]
    files = {r["path"]: r for r in manifest}
    inventory = build_inventory(root, allow_real_profile=True)
    dump(private / "wp08-inventory.json", inventory)
    reason_counts = collections.Counter(c for item in inventory["instances"] for c in item["reasonCodes"])
    categories = collections.Counter()
    formats = collections.defaultdict(collections.Counter)
    parse_errors, references, objects, sqlite_results = [], [], [], []
    critical_counts = collections.Counter()
    status_counts = collections.defaultdict(collections.Counter)
    wp08_files = set(migration._inputs(root))
    covered_bytes = sum(files[p]["bytes"] for p in wp08_files if p in files)
    record_index = (private / "record-index.jsonl").open("w", encoding="utf-8")

    def emit(category, value, location, container_version=None):
        categories[category] += 1
        version = value.get("schemaVersion", value.get("version", "unversioned")) if isinstance(value, dict) else "scalar"
        formats[category][str(version)] += 1
        critical = {}
        if isinstance(value, dict):
            for key in ("status", "state", "enabled", "executionOwner", "handoff_state"):
                if key in value:
                    status_counts[category + ":" + key][str(value[key])] += 1
            identity = next((value[k] for k in ("id", "jobId", "runId", "eventId", "effectId", "outboxId", "task_id") if k in value), None)
            for key, item in value.items():
                if re.search(r"pin|retention|handoff|notif|outbox|receipt|cursor|authoriz|owner|status|state|key", key, re.I):
                    critical[key] = hashlib.sha256(fc.canonical_json(item).encode()).hexdigest()
                    critical_counts[key] += 1
                if re.search(r"(?:Id|Ids|Ref|Key)$", key) and isinstance(item, (str, list, dict)):
                    references.append({"location": location, "field": key, "value": item})
            objects.append({"category": category, "id": identity, "location": location})
        record = {"category": category, "location": location, "schemaVersion": version,
                  "containerVersion": container_version, "recordDigest": fc.digest(value),
                  "criticalFieldDigests": critical}
        record_index.write(json.dumps(record, ensure_ascii=True) + "\n")

    def load_json(path):
        return strict_loads(path.read_text(encoding="utf-8-sig"))

    # No app Store/Session constructors: these can migrate/save on read.
    for rel in sorted(files):
        path = root / rel
        parts = Path(rel).parts
        top = parts[0]
        if top in {"workdirs", "validation_env", "cleanup-archive-20261024", "cleanup-archive-20261025"}:
            continue  # Retained byte-for-byte; never treated as this profile's records.
        try:
            if path.suffix in {".sqlite3", ".sqlite", ".db"}:
                scratch = private / "sqlite-read-copies" / rel
                scratch.parent.mkdir(parents=True, exist_ok=True)
                for suffix in ("", "-wal", "-shm"):
                    original = Path(str(path) + suffix)
                    if original.is_file():
                        shutil.copyfile(original, Path(str(scratch) + suffix))
                db = sqlite3.connect(scratch.as_uri() + "?mode=ro", uri=True)
                db.execute("PRAGMA query_only=ON")
                result = {"path": rel, "sha256": files[rel]["copySha256"],
                          "integrity": db.execute("PRAGMA integrity_check").fetchone()[0], "tables": {}}
                for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                    quoted = '"' + table.replace('"', '""') + '"'
                    result["tables"][table] = db.execute("SELECT count(*) FROM " + quoted).fetchone()[0]
                    columns = [r[1] for r in db.execute("PRAGMA table_info(" + quoted + ")")]
                    category = ("locker:" if top in {"user_inbox", "agent_inbox"} else "sqlite:") + table
                    for index, row in enumerate(db.execute("SELECT * FROM " + quoted)):
                        value = {k: ({"blobSha256": hashlib.sha256(v).hexdigest()} if isinstance(v, bytes) else v)
                                 for k, v in zip(columns, row)}
                        emit(category, value, rel + ":" + table + ":" + str(index))
                db.close()
                sqlite_results.append(result)
            elif path.suffix == ".jsonl" and top in {"sessions", "background_jobs", "automation", "scheduler"}:
                category = "session-history" if top == "sessions" else "runs-ledger" if path.name == "runs.jsonl" else "event-ledger"
                with path.open(encoding="utf-8-sig") as stream:
                    for number, line in enumerate(stream, 1):
                        if not line.strip():
                            continue
                        try:
                            emit(category, strict_loads(line), rel + ":" + str(number))
                        except (ValueError, TypeError) as exc:
                            parse_errors.append({"path": rel, "line": number, "error": type(exc).__name__, "partial": not line.endswith("\n")})
            elif path.suffix == ".json" and top in {"sessions", "background_jobs", "automation", "actions", "scheduler", "session-bookmarks"}:
                value = load_json(path)
                if rel in {"actions/registry.json", "automation/registry.json"}:
                    for key, category in {"actions": "actions", "jobTemplates": "templates", "triggers": "triggers", "runs": "automation-runs",
                                          "eventSources": "event-sources", "sources": "event-sources"}.items():
                        collection = value.get(key, {})
                        for identity, item in (collection.items() if isinstance(collection, dict) else enumerate(collection)):
                            emit(category, item, rel + ":" + key + ":" + str(identity), value.get("revision"))
                elif top == "sessions":
                    emit("sessions" if path.name.startswith("ses_") else "session-sidecar", value, rel)
                elif path.name == ".completed-retention.json":
                    emit("retention-marker", value, rel)
                elif len(parts) > 1 and parts[1] == "jobs":
                    emit("job:" + str(value.get("kind", "missing")), value, rel)
                else:
                    emit("metadata:" + top, value, rel)
            elif rel == "session-pins.json":
                emit("session-pins", load_json(path), rel)
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            parse_errors.append({"path": rel, "error": type(exc).__name__, "detail": str(exc)})
    record_index.close()
    dump(private / "references.json", references)
    dump(private / "object-identities.json", objects)
    dump(private / "sqlite-inventory.json", sqlite_results)
    dump(private / "parse-errors.json", parse_errors)
    dump(private / "status-counts.json", {k: dict(v) for k, v in status_counts.items()})
    dump(private / "blockers.json", inventory["plan"]["preserved"])
    # No invented Action pins, source grants, retention policy or live-owner
    # attestations. An empty reviewed context demonstrates fail-closed input.
    context = {"schemaVersion": 1, "profileId": "p9_unapproved_copy", "actions": {}, "records": {},
               "partialPolicy": {"version": 1, "mode": "preserve-incompatible"},
               "selectedMigrationIds": [item["migrationId"] for item in inventory["instances"]
                                         if item["classificationDetail"] == "simple"]}
    dump(private / "unapproved-context.json", context)
    reviewed = migration.plan_digest(inventory, context)
    attempts = []
    for label, apply, expected, partial_apply in (("dry-run", False, None, False),
                                                  ("stale-plan", True, "0" * 64, False),
                                                  ("explicit-apply", True, reviewed, False),
                                                  ("partial-preserve-apply", True, reviewed, True)):
        target = private / "canonical" / "practical-bundle.json"
        try:
            report = migration.migrate(root, target, context, apply=apply, mark_read_only=apply,
                                       expected_digest=expected, allow_real_profile=True,
                                       preserve_incompatible=partial_apply)
            attempts.append({"operation": label, "result": "returned", "report": report})
        except (ValueError, KeyError, OSError, TypeError) as exc:
            attempts.append({"operation": label, "result": "blocked", "error": type(exc).__name__, "detail": str(exc),
                             "targetExists": target.exists()})
    dump(private / "apply-attempts.json", attempts)
    # Full archive envelope references every physical source byte. It is
    # deliberately NOT labelled as a WP08 executable/canonical bundle.
    envelope = {"schema": "pan.p9.preservation-envelope/v1", "sourceCopy": str(root),
                "sourceManifestSha256": summary["manifestSha256"], "wp08InventoryDigest": fc.digest(inventory),
                "reviewedUnapprovedPlanDigest": reviewed, "canonicalBundleProduced": False,
                "canonicalRecords": 0, "activeIds": [], "fullMigrationGate": "BLOCKED",
                "preservation": "All copied regular bytes; links listed, never followed",
                "runtimeImport": "blocked: no approved complete WP08 bundle/context",
                "activeLegacy": "not-established for production; no legacy runtime started in rehearsal"}
    dump(private / "preservation-bundle-manifest.json", envelope)
    audit_result = {"categories": dict(categories), "formats": {k: dict(v) for k, v in formats.items()},
                    "criticalFields": dict(critical_counts), "parseErrors": len(parse_errors),
                    "wp08": inventory["summary"], "reasonCounts": dict(reason_counts),
                    "wp08InputFiles": len(wp08_files), "wp08InputBytes": covered_bytes,
                    "unscannedPhysicalFiles": len(files) - len(wp08_files),
                    "recordIndexSha256": digest_file(private / "record-index.jsonl"),
                    "sourceVersion": "heterogeneous per-record versions; process marker is not build provenance",
                    "fullMigrationGate": "BLOCKED"}
    dump(private / "audit-summary.json", audit_result)
    print(json.dumps(audit_result), flush=True)


def compare(private):
    """Verify the archive after all offline reads, without reopening the source."""
    private = private.resolve(strict=True)
    root = private / "source-copy"
    records = [json.loads(line) for line in (private / "copy-manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    files = [r for r in records if r.get("kind") == "file" and "copySha256" in r]
    def check(row):
        try:
            actual = digest_file(root / row["path"])
            return None if actual == row["sha256"] == row["copySha256"] else {"path": row["path"], "actual": actual, "expected": row["sha256"]}
        except OSError as exc:
            return {"path": row["path"], "error": str(exc)}
    with ThreadPoolExecutor(max_workers=12) as pool:
        mismatches = [v for v in pool.map(check, files) if v is not None]
    observed = {p.relative_to(root).as_posix() for k, p, _ in walk(root) if k == "file"}
    expected = {r["path"] for r in files}
    result = {"verifiedFiles": len(files), "verifiedBytes": sum(r["bytes"] for r in files),
              "mismatches": mismatches, "extraFiles": sorted(observed - expected),
              "missingFiles": sorted(expected - observed),
              "retainedLinks": sum(r.get("kind") == "link" for r in records),
              "changedDuringRead": sum(bool(r.get("changedDuringRead")) for r in files),
              "changedAcrossResume": sum(bool(r.get("changedSinceInitialRead")) for r in files),
              "sourceReopenedForComparison": False,
              "canonicalRecordsImported": 0,
              "recordComparison": "all archived bytes, IDs and fields identical if mismatches/missing/extra are empty; canonical import blocked"}
    dump(private / "before-after-comparison.json", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["snapshot", "audit", "compare"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.mode == "snapshot":
        if args.source is None:
            parser.error("snapshot requires explicit --source")
        snapshot(args.source, args.private, resume=args.resume)
    elif args.mode == "audit":
        audit(args.private)
    else:
        compare(args.private)
