"""Reproduce the Kimi Terminal-TUI trust prompt with a resumed session.

Seeds one session against an owned loopback model (no paid request), then
removes the folder-trust marker and relaunches the bridge with -S so the TUI
asks "Trust this folder?" again. Sends a Pan task and native keystrokes at known
times to measure whether control input is serviced while the task waits for
trust.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BRIDGE = REPO / "packages" / "core" / "adapters" / "kimi" / "native_bridge.py"
KIMI = Path.home() / ".kimi-code" / "bin" / "kimi.exe"
ANSI = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[=>]")

CONFIG = """default_model = "moonshot-cn/kimi-k2.6"

[thinking]
enabled = false
effort = "high"

[providers.fake]
type = "kimi"
api_key = "test-only"
base_url = "http://127.0.0.1:{port}/v1"

[models."moonshot-cn/kimi-k2.6"]
provider = "fake"
model = "kimi-k2.6"
max_context_size = 262144
capabilities = [ "thinking", "tool_use" ]
"""


def serve_model():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"object": "list", "data": [{"id": "kimi-k2.6"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            chunk = {"id": "chatcmpl-x", "object": "chat.completion.chunk",
                     "created": int(time.time()), "model": "kimi-k2.6",
                     "choices": [{"index": 0, "delta": {"role": "assistant",
                                                        "content": "SEED_OK"},
                                  "finish_reason": None}]}
            end = {"id": "chatcmpl-x", "object": "chat.completion.chunk",
                   "created": int(time.time()), "model": "kimi-k2.6",
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for item in (chunk, end):
                line = f"data: {json.dumps(item)}\n\n".encode()
                self.wfile.write(b"%x\r\n" % len(line) + line + b"\r\n")
            done = b"data: [DONE]\n\n"
            self.wfile.write(b"%x\r\n" % len(done) + done + b"\r\n0\r\n\r\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--send", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="kimi-resume-", dir=str(out)))
    home, work = root / "home", root / "work"
    home.mkdir()
    work.mkdir()
    server = serve_model()
    (home / "config.toml").write_text(CONFIG.format(port=server.server_port), encoding="utf-8")
    trust = home / "workspace-trust"
    trust.mkdir()
    (trust / "wd_probe").write_text(json.dumps({"root": str(work), "trustedAt": 1}),
                                    encoding="utf-8")
    env = dict(os.environ, KIMI_CODE_HOME=str(home))
    seed = subprocess.run(
        [str(KIMI), "-p", "Reply with exactly: SEED", "-m", "moonshot-cn/kimi-k2.6",
         "--output-format", "stream-json"],
        cwd=str(work), env=env, capture_output=True, text=True, timeout=180)
    sid = None
    for line in seed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "session.resume_hint":
            sid = event.get("session_id")
    if not sid:
        print("SEED FAILED", seed.stdout[-2000:], seed.stderr[-2000:])
        return 1
    # Recreate the untrusted state exactly: drop the only marker.
    for marker in trust.iterdir():
        marker.unlink()

    bridge_args = [sys.executable, "-u", str(BRIDGE), "--kimi-path", str(KIMI),
                   "--cwd", str(work), "--session-id", sid,
                   "--kimi-home", str(home), "--model", "moonshot-cn/kimi-k2.6"]
    log = open(out / "transcript.jsonl", "w", encoding="utf-8")
    started = time.monotonic()
    p = subprocess.Popen(bridge_args, cwd=str(work), env={**os.environ},
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", bufsize=1)
    raw = bytearray()
    events = []
    lock = threading.Lock()

    def read():
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            with lock:
                events.append(event)
                if event.get("type") == "pan.native_output":
                    raw.extend(base64.b64decode(event["data"]))
            log.write(json.dumps({"t": round(time.monotonic() - started, 3), **event},
                                 ensure_ascii=False) + "\n")
            log.flush()

    threading.Thread(target=read, daemon=True).start()

    actions = []
    for spec in args.send:
        seconds, _, payload = spec.partition("=")
        actions.append((float(seconds), payload))
    actions.sort()

    def feed():
        for seconds, payload in actions:
            delay = seconds - (time.monotonic() - started)
            if delay > 0:
                time.sleep(delay)
            try:
                p.stdin.write(payload + "\n")
                p.stdin.flush()
                log.write(json.dumps({"t": round(time.monotonic() - started, 3),
                                      "sent": json.loads(payload)}) + "\n")
            except Exception as exc:  # noqa: BLE001
                log.write(json.dumps({"t": round(time.monotonic() - started, 3),
                                      "send_error": type(exc).__name__}) + "\n")
            log.flush()

    threading.Thread(target=feed, daemon=True).start()
    try:
        code = p.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        code = None
        p.kill()
        p.wait(timeout=10)
    elapsed = round(time.monotonic() - started, 3)
    (out / "raw_output.bin").write_bytes(bytes(raw))
    (out / "rendered.txt").write_text(ANSI.sub(b"", bytes(raw)).decode("utf-8", "replace"),
                                      encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({
        "elapsed": elapsed, "exit_code": code, "session_id": sid,
        "kimi_home": str(home), "workdir": str(work), "output_bytes": len(raw),
        "event_types": [e.get("type") or e.get("role") for e in events],
        "home_tree": sorted(str(x.relative_to(home)) for x in home.rglob("*")),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log.close()
    server.shutdown()
    print(json.dumps({"exit": code, "elapsed": elapsed, "sid": sid}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
