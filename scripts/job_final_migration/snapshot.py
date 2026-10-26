"""P9B bounded data snapshot: source opens are rb/lstat/scandir only.

SQLite backup runs on a stable private DB/WAL copy, never opens the source DB
through SQLite (a read-only SQLite connection can still touch a source SHM).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from scripts.p9_practical_rehearsal import digest_file, dump

ALLOWLIST = frozenset({"sessions", "background_jobs", "automation", "scheduler", "actions", "attachments",
    "agent_inbox", "user_inbox", "session-bookmarks", "session-drafts", "session-pins.json", "workspaces",
    "qq_history", "qq_inbox", "qq_media", "characters", "memory", "startup_recovery", "terminals",
    "process.json", "job-runtime-owner.sqlite3"})
EXCLUDED_PARTS = frozenset({"node_modules", "__pycache__", ".cache", ".git", ".venv", "venv", "dist", "build",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", "playwright-report", "test-results"})
LOCKS = {"actions/job-runtime/service.lock": "packages/jobs/action_runtime.py:86-87 acquires ServiceLock; platform process lock, no business payload",
         "job-runtime-owner.lock": "packages/jobs/runtime_owner.py:33 lifetime lock; authority is job-runtime-owner.sqlite3"}


def linked(path):
    st = path.lstat()
    return path.is_symlink() or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def data_paths(root):
    """Never descend into a link, cache or an unallowlisted top-level tree."""
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                path = Path(entry.path)
                rel = path.relative_to(root)
                if linked(path):
                    yield "link", path, "link-not-followed"
                elif rel.as_posix() in LOCKS:
                    yield "excluded", path, LOCKS[rel.as_posix()]
                elif rel.parts[0] not in ALLOWLIST:
                    yield "excluded", path, "outside explicit Pan data allowlist"
                elif any(p in EXCLUDED_PARTS for p in rel.parts):
                    yield "excluded", path, "cache/dependency/build metadata"
                elif entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                elif path.name.endswith(("-wal", "-shm", "-journal")) and path.with_name(
                        path.name.rsplit("-", 1)[0]).suffix in {".sqlite3", ".sqlite", ".db"} and path.with_name(
                        path.name.rsplit("-", 1)[0]).is_file():
                    yield "excluded", path, "SQLite sidecar included only in private consistent DB capture"
                else:
                    yield "file", path, None


def stable_copy(source, target, *, attempts=4, observe=None):
    """Require two identical stream hashes and stable metadata, else block.

    observe is an injection seam for deterministic concurrent-writer tests.
    Failed captures are never installed at target.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    evidence = []
    for number in range(1, attempts + 1):
        temporary = target.with_name(target.name + ".capture")
        try:
            if linked(source):
                raise ValueError("source became a link")
            before = source.stat()
            h = hashlib.sha256()
            with source.open("rb") as reader, temporary.open("wb") as writer:
                for block in iter(lambda: reader.read(4 * 1024 * 1024), b""):
                    writer.write(block)
                    h.update(block)
            if observe:
                observe(number)
            after_copy = source.stat()
            second = digest_file(source)
            after_hash = source.stat()
            unchanged = ((before.st_size, before.st_mtime_ns) == (after_copy.st_size, after_copy.st_mtime_ns)
                         == (after_hash.st_size, after_hash.st_mtime_ns))
            stable = unchanged and h.hexdigest() == second
            evidence.append({"attempt": number, "firstSha256": h.hexdigest(), "secondSha256": second,
                             "bytes": after_hash.st_size, "stable": stable})
            if stable:
                temporary.replace(target)
                return {"state": "stable", "sha256": second, "bytes": target.stat().st_size, "attempts": evidence}
        except (OSError, ValueError) as exc:
            evidence.append({"attempt": number, "stable": False, "error": type(exc).__name__, "detail": str(exc)})
        finally:
            temporary.unlink(missing_ok=True)
    return {"state": "blocked-unstable-or-unreadable", "attempts": evidence}


def sqlite_snapshot(source, target, *, attempts=4):
    target.parent.mkdir(parents=True, exist_ok=True)
    evidence = []
    for number in range(1, attempts + 1):
        if Path(str(source) + "-journal").exists():
            evidence.append({"attempt": number, "rollbackJournalPresent": True,
                             "error": "rollback journal requires a quiescent snapshot; not silently omitted"})
            continue
        with tempfile.TemporaryDirectory(prefix="sqlite-capture-", dir=target.parent) as temp:
            scratch = Path(temp) / source.name
            candidates = [Path(str(source) + suffix) for suffix in ("", "-wal")]
            present = [p for p in candidates if p.exists()]
            captured = {p.name: stable_copy(p, Path(temp) / p.name, attempts=1) for p in present}
            stable = all(v["state"] == "stable" for v in captured.values())
            # Pair capture must be stable across the entire DB+WAL read window.
            stable = stable and [p.exists() for p in candidates] == [p in present for p in candidates]
            stable = stable and all(digest_file(p) == captured[p.name]["sha256"] for p in present)
            stable = stable and not Path(str(source) + "-journal").exists()
            row = {"attempt": number, "inputs": captured, "pairStable": stable}
            evidence.append(row)
            if not stable:
                continue
            output = Path(temp) / "backup.sqlite3"
            try:
                with closing(sqlite3.connect(scratch.as_uri() + "?mode=ro", uri=True)) as db, closing(sqlite3.connect(output)) as backup:
                    db.backup(backup)
                    backup.commit()
                    check = backup.execute("PRAGMA integrity_check").fetchone()[0]
                row["integrity"] = check
                if check != "ok":
                    continue
                output.replace(target)
                return {"state": "stable-sqlite-backup", "sha256": digest_file(target), "bytes": target.stat().st_size,
                        "rawSourceSha256": captured[source.name]["sha256"], "attempts": evidence}
            except sqlite3.Error as exc:
                row["error"] = str(exc)
    return {"state": "blocked-sqlite-snapshot", "attempts": evidence}


def collect(source, private, *, old_changed=()):
    source, private = Path(source).resolve(strict=True), Path(private).resolve()
    if private.is_relative_to(source):
        raise ValueError("private output must be outside source")
    root = private / "source-copy"
    root.mkdir(parents=True, exist_ok=False)
    paths = list(data_paths(source))
    def capture(item):
        kind, path, reason = item
        rel = path.relative_to(source).as_posix()
        if kind == "link":
            return {"path": rel, "kind": kind, "state": reason, "target": os.readlink(path)}
        if kind == "excluded":
            return {"path": rel, "kind": kind, "state": "explicit-exclusion", "reason": reason}
        try:
            result = (sqlite_snapshot(path, root / rel) if path.suffix in {".sqlite3", ".sqlite", ".db"}
                      else stable_copy(path, root / rel))
        except (OSError, ValueError) as exc:
            result = {"state": "blocked-capture", "error": str(exc)}
        return {"path": rel, "kind": kind, **result}
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(capture, paths))
    retries = []
    for rel in old_changed:
        relative = Path(rel)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid old changed path")
        # Diagnostic log retries remain outside migration source-copy.
        result = stable_copy(source / relative, private / "old-change-retries" / relative)
        retries.append({"path": rel, "inMigrationScope": relative.parts[0] in ALLOWLIST, **result})
    dump(private / "snapshot-manifest.json", rows)
    dump(private / "old-change-retries.json", retries)
    summary = {"allowlist": sorted(ALLOWLIST), "excludedParts": sorted(EXCLUDED_PARTS),
               "followlinks": False, "capturedFiles": sum(r["state"].startswith("stable") for r in rows),
               "capturedBytes": sum(r.get("bytes", 0) for r in rows),
               "captureBlockers": sum(r["state"].startswith("blocked") for r in rows),
               "linksNotFollowed": sum(r["kind"] == "link" for r in rows),
               "explicitExclusions": sum(r["kind"] == "excluded" for r in rows),
               "sqliteSnapshots": sum(r["state"] == "stable-sqlite-backup" for r in rows),
               "oldChangesStable": sum(r["state"] == "stable" for r in retries),
               "oldChangesBlocked": sum(r["state"] != "stable" for r in retries),
               "applicationConsistentSnapshot": False,
               "consistencyScope": "stable per file and SQLite DB/WAL group; no cross-store transaction claimed"}
    dump(private / "snapshot-summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--retry-paths-json", type=Path,
                        help="private JSON list of relative path strings or prior retry rows")
    args = parser.parse_args()
    retries = json.loads(args.retry_paths_json.read_text(encoding="utf-8")) if args.retry_paths_json else []
    print(json.dumps(collect(args.source, args.private,
                           old_changed=[r["path"] if isinstance(r, dict) else r for r in retries])))
