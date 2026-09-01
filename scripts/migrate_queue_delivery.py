#!/usr/bin/env python3
"""Migrate persisted Pan queue rows to the post-handoff delivery contract.

The running server performs the same migration before a worker consumes a
session.  This command is useful for an operator who wants to convert all
old session files ahead of a rollout.  It is a dry run unless ``--apply`` is
given.  Applied files are copied to a timestamped backup directory first, so
the original data remains recoverable.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from packages.core import session as _sess
from packages.core import worker as _worker


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=_sess.SESSION_DIR,
        help="directory containing <session>.json files",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write migrated files after creating backups",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="backup root (default: <session-dir>/queue-migration-backups/<timestamp>)",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    session_dir = args.session_dir.resolve()
    _sess.SESSION_DIR = session_dir
    files = sorted(
        path for path in session_dir.glob("*.json")
        if not path.name.endswith(".json.tmp")
    )
    backup_dir = args.backup_dir
    if args.apply and backup_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = session_dir / "queue-migration-backups" / stamp

    changed_count = 0
    row_count = 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sid = str(data.get("id") or path.stem)
            session = _sess._from_data_with_history(sid, data)
            changed = _worker._migrate_queue_delivery_state(session)
        except (OSError, ValueError, TypeError) as exc:
            print(f"ERROR {path.name}: {exc}")
            continue
        if not changed:
            continue
        changed_count += 1
        row_count += len(session.queue_pending or [])
        print(f"MIGRATE {path.name}: {len(session.queue_pending or [])} queue row(s)")
        if not args.apply:
            continue
        assert backup_dir is not None
        target_backup = backup_dir / path.name
        target_backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target_backup)
        history_path = session_dir / f"{session.id}.history.jsonl"
        if history_path.exists():
            shutil.copy2(history_path, backup_dir / history_path.name)
        _sess.save_full(session)

    mode = "applied" if args.apply else "dry-run"
    print(f"{mode}: {changed_count} session file(s), {row_count} pending row(s)")
    if args.apply and changed_count:
        print(f"backup: {backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
