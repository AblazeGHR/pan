"""Probe driver v2: real Windows toast -> notification-center DB evidence.

Design under test (user suggestion: rely on the default browser):
  * action button carries the full loopback URL, activationType="protocol"
    (http: is handled by the installed default browser - no custom protocol)
  * no shortcut, no protocol registration; the WPN platform auto-registers a
    NonImmersivePackage handler for the AUMID on first toast
  * sender process exits immediately; click activation is handled by the shell

Evidence:
  * a read-only snapshot of %LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\
    wpndatabase.db (SQLite) queried for the notification row (Tag/AUMID/
    arrival/payload), independent of the Action Center UI
  * the exact toast XML stored in the payload

Writes a JSON summary next to this file.
"""
from __future__ import annotations

import base64
import datetime
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
PS = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-STA"]
AUMID = "Pan.NotificationsProbe"
WPN_DIR = Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Notifications"
SNAPSHOT_DIR = HERE / "wpndb-snapshot"

summary: dict = {"aumid": AUMID}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def run_ps(script: str, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(PS + ["-File", str(HERE / script), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False)


def snapshot_wpndb() -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in ("wpndatabase.db", "wpndatabase.db-wal", "wpndatabase.db-shm"):
        src = WPN_DIR / name
        if not src.exists():
            continue
        dst = SNAPSHOT_DIR / name
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
        copied.append(name)
    return SNAPSHOT_DIR / "wpndatabase.db"


def query_row(tag: str) -> dict | None:
    con = sqlite3.connect(SNAPSHOT_DIR / "wpndatabase.db")
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            """SELECT n."Order", n.Id, n.Tag, n."Group", n.ArrivalTime, n.ExpiryTime,
                      n.Payload, n.PayloadType, h.PrimaryId AS AumId, h.HandlerType, h.WNSId
               FROM Notification n LEFT JOIN NotificationHandler h ON h.RecordId = n.HandlerId
               WHERE n.Tag = ? ORDER BY n."Order" DESC LIMIT 1""",
            (tag,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    result = {k: row[k] for k in row.keys()}
    payload = result.pop("Payload")
    result["payloadText"] = bytes(payload).decode("utf-8", "replace")
    result["arrivalLocal"] = (
        datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
        + datetime.timedelta(microseconds=row["ArrivalTime"] / 10)
    ).astimezone().isoformat()
    return result


def main() -> int:
    port = free_port()
    assert port not in (8767, 8768), "must not touch protected ports"
    summary["port"] = port
    server_log = HERE / "server-access.jsonl"
    server = subprocess.Popen(
        [sys.executable, str(HERE / "logserver.py"), str(port), str(server_log)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.7)

        session_id = "ses_probe_click"
        target = f"http://127.0.0.1:{port}/react/?session={session_id}"
        tag = f"e2e-{session_id}"
        group = "Pan-completions"
        title = "Pan: E2E probe session completed"
        body = "NCTEST click the button; browser should open the loopback URL"

        toast_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<toast launch="{xml_escape(target)}">\n'
            '  <visual>\n'
            '    <binding template="ToastGeneric">\n'
            f'      <text>{xml_escape(title)}</text>\n'
            f'      <text>{xml_escape(body)}</text>\n'
            '    </binding>\n'
            '  </visual>\n'
            '  <actions>\n'
            f'    <action activationType="protocol" arguments="{xml_escape(target)}" content="Open session"/>\n'
            '  </actions>\n'
            '</toast>\n'
        )
        summary["toastXml"] = toast_xml

        r = run_ps("toast.ps1", "-Aumid", AUMID,
                   "-XmlBase64", base64.b64encode(toast_xml.encode("utf-8")).decode("ascii"),
                   "-Tag", tag, "-Group", group)
        summary["toast"] = {"returncode": r.returncode, "stdout": r.stdout.strip(),
                            "stderr": r.stderr.strip()[-2000:]}
        time.sleep(1.0)

        snapshot_wpndb()
        row = query_row(tag)
        summary["dbRow"] = row
        summary["recorded"] = bool(row and row.get("AumId") == AUMID
                                   and session_id in (row.get("payloadText") or ""))
    finally:
        # keep logserver running? No: this driver stops it; the manual click
        # acceptance uses a separately started long-lived server.
        server.terminate()
        summary["serverAccess"] = (server_log.read_text(encoding="utf-8").strip().splitlines()
                                   if server_log.exists() else [])
    out = HERE / "probe-summary-v2.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("PROBE_V2_OK" if summary.get("recorded") else "PROBE_V2_INCOMPLETE")
    return 0 if summary.get("recorded") else 1


if __name__ == "__main__":
    raise SystemExit(main())
