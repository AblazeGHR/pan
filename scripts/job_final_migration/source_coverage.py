"""Read-only path accounting beside lite inventory, never a SQLite inspector.

Opaque stores are hashed as files, never connected, imported or interpreted.
This report cannot assert whole-profile migration or live owner clearance.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import os
from pathlib import Path
import stat

from packages.jobs import final_contracts as fc
from .inventory import build_inventory
from .apply import _atomic_write, outside_root


def _kind(path):
    lower = path.lower()
    parts = Path(path).parts
    name = parts[-1].lower()
    if name.startswith("job-runtime-owner.") or parts[0].lower() in {"job-final", "p9d-auth"}:
        return "existing-runtime-authority", "existing runtime/authority must not be copied or reset"
    if ".sqlite" in lower or lower.endswith((".db", "-wal", "-shm", "-journal")):
        return "opaque-sqlite-or-sidecar", "schema/consistency/owner unverified; raw bytes only, SQLite never opened"
    if "cursor" in lower or "pending" in lower or "outbox" in lower:
        return "independent-cursor-or-pending", "not covered by lite mapper; no cursor/receipt adoption"
    if lower.startswith("background_jobs/logs/"):
        return "historical-log", "preserved historical bytes; read only via a separately pinned log reference"
    if name == "pins.json":
        return "pins-metadata", "preserved policy evidence, not a migrated Job identity"
    return "unrecognized-profile-content", "no fixed mapper/inspector for this path; preserve without interpretation"


def inventory_coverage(root, *, allow_real_profile=False):
    root = Path(root).absolute()
    if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in (root, *root.parents)):
        raise ValueError("coverage root contains a link")
    root = root.resolve(strict=True)
    inventory = build_inventory(root, allow_real_profile=allow_real_profile)
    by_path = {}
    for item in inventory["instances"]:
        by_path.setdefault(item["sourceFile"], []).append(item)
    fixed = {"actions/registry.json", "automation/registry.json", "background_jobs/schedule_templates.json",
             "background_jobs/runs.jsonl", "scheduler/runs.jsonl"}
    rows, errors = [], []

    def walk(directory):
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError as exc:
            errors.append({"path": directory.relative_to(root).as_posix(), "reason": type(exc).__name__})
            return
        for entry in entries:
            path = Path(entry.path)
            rel = path.relative_to(root).as_posix()
            try:
                info = path.lstat()
                link = entry.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
                if link or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    rows.append({"path": rel, "nodeType": "link-or-special", "disposition": "unrecognized",
                                 "sourceType": "unfollowed-node", "migrationHandled": False, "sha256": None,
                                 "reason": "link/special path not followed", "targetIds": []})
                elif stat.S_ISDIR(info.st_mode):
                    rows.append({"path": rel, "nodeType": "directory", "disposition": "container",
                                 "migrationHandled": False, "sha256": None})
                    walk(path)
                else:
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                    after = path.lstat()
                    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                        raise ValueError("file changed during hash")
                    kind, reason = _kind(rel)
                    items = by_path.get(rel, [])
                    if items or rel in fixed or (path.suffix == ".json" and
                            path.parent.relative_to(root).as_posix() in {"background_jobs/jobs", "scheduler/tasks"}):
                        kind, reason = "lite-input", "lite read/backup scope; per-record disposition remains authoritative"
                        disposition = "handled"
                    elif kind in {"historical-log", "pins-metadata", "independent-cursor-or-pending"}:
                        disposition = "preserved"
                    else:
                        disposition = "unrecognized"
                    rows.append({"path": rel, "nodeType": "file", "sourceType": kind,
                                 "disposition": disposition, "migrationHandled": disposition == "handled",
                                 "sha256": digest.hexdigest(), "bytes": info.st_size, "reason": reason,
                                 "recordDispositions": [{"migrationId": i["migrationId"], "originalId": i["id"],
                                     "classification": i["classificationDetail"], "reasonCodes": i["reasonCodes"]} for i in items],
                                 "targetIds": []})
            except (OSError, ValueError) as exc:
                errors.append({"path": rel, "reason": str(exc)})

    walk(root)
    files = [r for r in rows if r["nodeType"] == "file"]
    totals = Counter(r["disposition"] for r in files)
    result = {"schema": "pan.job-final-migration.source-coverage/v1", "root": str(root), "paths": rows,
              "inventoryDigest": fc.digest(inventory), "errors": errors,
              "discoveryComplete": not errors and not any(r["nodeType"] == "link-or-special" for r in rows),
              "accounting": {"discoveredPaths": len(rows), "discoveredFiles": len(files),
                             "handledFiles": totals["handled"], "preservedFiles": totals["preserved"],
                             "unrecognizedFiles": totals["unrecognized"]},
              "unmappedPaths": [r["path"] for r in files if not r["migrationHandled"]],
              "blockingAuthorityPaths": [r["path"] for r in files if r["sourceType"] == "existing-runtime-authority"],
              "wholeProfileMigrationComplete": False, "runtimeAdoptionProven": False}
    result["coverageDigest"] = fc.digest(result)
    return result


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = inventory_coverage(args.root)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out == "-":
        print(text, end="")
    else:
        output = outside_root(args.root.resolve(), args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(output, text.encode("utf-8"))
