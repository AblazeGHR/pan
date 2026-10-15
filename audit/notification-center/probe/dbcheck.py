"""Read-only Wpn notification database checker (evidence tool).

Copies the live SQLite database (with WAL/SHM) to a snapshot directory using
shared-read semantics, then queries notification rows. Never writes to the
live files. Usage:

    python dbcheck.py [aumid-filter] [tag-filter]
"""
from __future__ import annotations

import datetime
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WPN_DIR = Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Notifications"
SNAPSHOT = HERE / "wpndb-snapshot"


def snapshot() -> Path:
    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    for name in ("wpndatabase.db", "wpndatabase.db-wal", "wpndatabase.db-shm"):
        src = WPN_DIR / name
        if src.exists():
            with open(src, "rb") as fsrc, open(SNAPSHOT / name, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
    return SNAPSHOT / "wpndatabase.db"


def fil_to_local(value: int) -> str:
    return (datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
            + datetime.timedelta(microseconds=value / 10)).astimezone().isoformat()


def main() -> int:
    aumid_filter = sys.argv[1] if len(sys.argv) > 1 else None
    tag_filter = sys.argv[2] if len(sys.argv) > 2 else None
    db = snapshot()
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        query = (
            'SELECT n."Order", n.Tag, n."Group", n.ArrivalTime, n.Payload, '
            'h.PrimaryId AS AumId, h.HandlerType, h.WNSId '
            'FROM Notification n LEFT JOIN NotificationHandler h ON h.RecordId = n.HandlerId '
            'ORDER BY n."Order" DESC'
        )
        rows = con.execute(query).fetchall()
    finally:
        con.close()
    shown = 0
    for row in rows:
        if aumid_filter and row["AumId"] != aumid_filter:
            continue
        if tag_filter and row["Tag"] != tag_filter:
            continue
        payload = bytes(row["Payload"]).decode("utf-8", "replace") if row["Payload"] else ""
        print(f"order={row['Order']} tag={row['Tag']!r} group={row['Group']!r} "
              f"aumid={row['AumId']!r} type={row['HandlerType']!r} wns={row['WNSId']!r} "
              f"arrival={fil_to_local(row['ArrivalTime'])}")
        preview = payload.replace("\r", "").replace("\n", "")
        print(f"  payload[:300]={preview[:300]}")
        shown += 1
    print(f"total rows in Notification table: {len(rows)}; shown: {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
