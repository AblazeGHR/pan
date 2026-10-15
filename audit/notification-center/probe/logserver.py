"""Probe logging HTTP server: proves the default browser really navigated to
the loopback deep link carried by the toast (access log line per request).
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        line = json.dumps({
            "utc": self.log_date_time_string(),
            "method": "GET",
            "path": self.path,
            "ua": self.headers.get("User-Agent", ""),
        }, ensure_ascii=False)
        LOG_PATH.write_text(LOG_PATH.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
        body = b"pan-probe-ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence stderr noise
        return


LOG_PATH = Path(sys.argv[2])
LOG_PATH.write_text("", encoding="utf-8")
port = int(sys.argv[1])
server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
print(f"probe server on 127.0.0.1:{port}", flush=True)
server.serve_forever()
