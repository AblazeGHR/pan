"""User acceptance helper: keep a logging server alive, post one real toast
whose button opens the loopback deep link via the DEFAULT BROWSER, and record
the click when it happens.

Run in the background; it stays alive for the click. Writes user-check.json
with the port/URL and, on each server hit, appends to server-access.jsonl.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PS = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-STA"]
AUMID = "Pan.NotificationsProbe"

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 64177
ACCESS_LOG = HERE / "user-check-access.jsonl"
URL = f"http://127.0.0.1:{PORT}/react/?session=ses_user_click"


class Handler(BaseHTTPRequestHandler):
    def _record(self) -> None:
        line = json.dumps({
            "utc": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "local": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "method": self.command,
            "path": self.path,
            "ua": self.headers.get("User-Agent", ""),
        }, ensure_ascii=False)
        with open(ACCESS_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        body = ("pan-probe-ok: click verified\n"
                "Pan 通知中心点击验收通过：默认浏览器打开了这条 loopback 深链。\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802
        self._record()
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args) -> None:
        return


def xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def post_toast() -> dict:
    title = "Pan: 通知中心验收 — 请点击按钮"
    body = f"真实 Windows toast。点击[打开会话]按钮后，默认浏览器应打开 {URL}"
    toast_xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<toast launch="{xml_escape(URL)}">\n'
        '  <visual>\n'
        '    <binding template="ToastGeneric">\n'
        f'      <text>{xml_escape(title)}</text>\n'
        f'      <text>{xml_escape(body)}</text>\n'
        '    </binding>\n'
        '  </visual>\n'
        '  <actions>\n'
        f'    <action activationType="protocol" arguments="{xml_escape(URL)}" content="打开会话"/>\n'
        '  </actions>\n'
        '</toast>\n'
    )
    r = subprocess.run(
        PS + ["-File", str(HERE / "toast.ps1"), "-Aumid", AUMID,
              "-XmlBase64", base64.b64encode(toast_xml.encode("utf-8")).decode("ascii"),
              "-Tag", "usercheck-click", "-Group", "Pan-usercheck"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False)
    return {"returncode": r.returncode, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()[-800:]}


def main() -> int:
    # Append: an earlier click already recorded in this log must survive a
    # re-run of the acceptance helper.
    ACCESS_LOG.touch(exist_ok=True)
    toast = post_toast()
    info = {"port": PORT, "url": URL, "aumid": AUMID, "toast": toast,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (HERE / "user-check.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False), flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"waiting for the click on {URL}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
