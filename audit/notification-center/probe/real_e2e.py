"""Real end-to-end: the production notification path against the real OS.

Calls packages.core.notifications.dispatch_completion / dispatch_reminder with
the production default sender (no injection), which posts real WinRT toasts
under the production AUMID, then reads the notification-center SQLite database
(read-only snapshot) to prove the entries were recorded.

The deep links point at the still-running user-click acceptance server on
127.0.0.1:64177 (its access log records a real click). No user service, no
8767/8768, no provider calls. Run via scripts/run_tests_isolated.py so no
deployed Pan settings are inherited.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

CLICK_PORT = 64177  # the running user-click acceptance server
SESSION_ID = "ses_e2e20261016aa"

os.environ["PAN_PORT"] = str(CLICK_PORT)  # resolve_pan_api_url -> loopback URL
os.environ.pop("PAN_API_URL", None)

from packages.core import notifications  # noqa: E402  (after env setup)

WPN_DIR = Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Notifications"
SNAPSHOT = HERE / "wpndb-snapshot"


def snapshot() -> sqlite3.Connection:
    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    for name in ("wpndatabase.db", "wpndatabase.db-wal", "wpndatabase.db-shm"):
        src = WPN_DIR / name
        if src.exists():
            with open(src, "rb") as fsrc, open(SNAPSHOT / name, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
    return sqlite3.connect(SNAPSHOT / "wpndatabase.db")


def query(con: sqlite3.Connection, tag: str) -> dict | None:
    con.row_factory = sqlite3.Row
    row = con.execute(
        """SELECT n."Order", n.Tag, n."Group", n.ArrivalTime, n.Payload,
                  h.PrimaryId AS AumId, h.HandlerType, h.WNSId
           FROM Notification n LEFT JOIN NotificationHandler h ON h.RecordId = n.HandlerId
           WHERE n.Tag = ? ORDER BY n."Order" DESC LIMIT 1""",
        (tag,),
    ).fetchone()
    if row is None:
        return None
    result = {k: row[k] for k in row.keys()}
    result["Payload"] = bytes(result["Payload"]).decode("utf-8", "replace")
    result["arrivalLocal"] = (
        datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
        + datetime.timedelta(microseconds=row["ArrivalTime"] / 10)
    ).astimezone().isoformat()
    return result


def main() -> int:
    summary: dict = {"sessionId": SESSION_ID, "clickPort": CLICK_PORT}

    session = SimpleNamespace(
        id=SESSION_ID,
        name="E2E 生产路径",
        notification_settings={"browser": False, "system": True},
    )
    result_text = '结果：<修复完成> & "引号" 中文正文 \\ 反斜杠 $(not-evaluated)'
    before = notifications.completion_deep_link(SESSION_ID)
    summary["expectedDeepLink"] = before
    event = notifications.dispatch_completion(session, "done", result_text)
    summary["completionEvent"] = event

    reminder = notifications.dispatch_reminder("提醒验证 reminder-e2e", "提醒正文 <&> 中文")
    summary["reminderEvent"] = reminder

    import time
    time.sleep(1.5)  # let the notification platform finish the DB write

    con = snapshot()
    try:
        completion_row = query(con, f"completion-{SESSION_ID}")
        reminder_row = query(con, notifications.reminder_dedup_tag("Pan: 提醒验证 reminder-e2e",
                                                                  "提醒正文 <&> 中文"))
    finally:
        con.close()
    summary["completionRow"] = completion_row
    summary["reminderRow"] = reminder_row

    ok = (
        bool(event and event.get("system", {}).get("ok"))
        and bool(completion_row)
        and completion_row["AumId"] == "Pan.Desktop.Notifications"
        and completion_row["HandlerType"] == "app:desktop"
        and before and before in completion_row["Payload"]
        # Windows re-serializes the XML (canonical form): element-critical
        # characters stay escaped, quotes in text nodes become literal.
        and "&lt;修复完成&gt;" in completion_row["Payload"]
        and "&amp;" in completion_row["Payload"]
        and '"引号"' in completion_row["Payload"]
        and bool(reminder_row)
        and reminder_row["AumId"] == "Pan.Desktop.Notifications"
        and "<actions>" not in reminder_row["Payload"]
    )
    summary["ok"] = ok
    out_file = HERE.parent / "evidence-real-e2e.json"
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
