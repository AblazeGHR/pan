"""Reproduce the Kimi Terminal-TUI folder-trust startup path.

Runs the real packages/core/adapters/kimi/native_bridge.py against the real
installed kimi CLI under ConPTY, with an owned KIMI_CODE_HOME and an untrusted
temporary workdir. No model request is needed for the trust prompt.

Usage:
  python repro_bridge.py --out DIR [--send "3.0=native_ready" ...]
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


def render(raw: bytes) -> str:
    text = ANSI.sub(b"", raw).decode("utf-8", "replace")
    return text


def serve_model():
    """Owned loopback OpenAI-compatible SSE server (no paid provider)."""

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
                                                        "content": "TRUST_TURN_OK"},
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
    parser.add_argument("--send", action="append", default=[],
                        help="SECONDS=control-json (e.g. 4.0={\"type\":\"control_mode\",\"mode\":\"native\",\"generation\":0})")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--pretrust", action="store_true")
    parser.add_argument("--close-stdin-at", type=float, default=0.0)
    args = parser.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="kimi-trust-", dir=str(out)))
    home = root / "home"
    work = root / "work"
    home.mkdir()
    work.mkdir()
    server = serve_model()
    (home / "config.toml").write_text(CONFIG.format(port=server.server_port), encoding="utf-8")
    if args.pretrust:
        trust = home / "workspace-trust"
        trust.mkdir()
        (trust / "wd_probe").write_text(
            json.dumps({"root": str(work), "trustedAt": 1}), encoding="utf-8")

    # A closed loopback port: the trust screen appears before any model call,
    # so no provider request is ever attempted in this repro.
    argv = [str(KIMI)]
    if args.session_id:
        argv.extend(["-S", args.session_id])
    argv.extend(["-m", "moonshot-cn/kimi-k2.6"])
    bridge_args = [sys.executable, "-u", str(BRIDGE), "--kimi-path", str(KIMI),
                   "--cwd", str(work), "--session-id", args.session_id,
                   "--kimi-home", str(home), "--model", "moonshot-cn/kimi-k2.6"]
    log_path = out / "transcript.jsonl"
    raw_path = out / "raw_output.bin"

    env = dict(os.environ)
    env.pop("KIMI_CODE_HOME", None)
    env["KIMI_TRUST_CASE_ROOT"] = str(root)
    started = time.monotonic()
    proc = subprocess.Popen(bridge_args, cwd=str(work), env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", bufsize=1)
    events: list[dict] = []
    raw = bytearray()
    lock = threading.Lock()
    log = open(log_path, "w", encoding="utf-8")

    def read_stdout():
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                log.write(json.dumps({"t": round(time.monotonic() - started, 3),
                                      "text": line}) + "\n")
                log.flush()
                continue
            with lock:
                events.append(event)
                if event.get("type") == "pan.native_output":
                    try:
                        raw.extend(base64.b64decode(event["data"]))
                    except Exception:
                        pass
            log.write(json.dumps({"t": round(time.monotonic() - started, 3), **event},
                                 ensure_ascii=False) + "\n")
            log.flush()

    threading.Thread(target=read_stdout, daemon=True).start()
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
                proc.stdin.write(payload + "\n")
                proc.stdin.flush()
                log.write(json.dumps({"t": round(time.monotonic() - started, 3),
                                      "sent": json.loads(payload)}) + "\n")
            except Exception as exc:  # noqa: BLE001
                log.write(json.dumps({"t": round(time.monotonic() - started, 3),
                                      "send_error": type(exc).__name__}) + "\n")
            log.flush()

    threading.Thread(target=feed, daemon=True).start()

    if args.close_stdin_at > 0:
        def close_stdin():
            delay = args.close_stdin_at - (time.monotonic() - started)
            if delay > 0:
                time.sleep(delay)
            try:
                proc.stdin.close()
                log.write(json.dumps({"t": round(time.monotonic() - started, 3),
                                      "stdin": "closed"}) + "\n")
                log.flush()
            except Exception as exc:  # noqa: BLE001
                log.write(json.dumps({"t": round(time.monotonic() - started, 3),
                                      "close_error": type(exc).__name__}) + "\n")
                log.flush()

        threading.Thread(target=close_stdin, daemon=True).start()

    try:
        code = proc.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        code = None
        proc.kill()
        proc.wait(timeout=10)
    elapsed = round(time.monotonic() - started, 3)
    stderr = ""
    try:
        stderr = proc.stderr.read() or ""
    except Exception:
        pass
    raw_path.write_bytes(bytes(raw))
    (out / "rendered.txt").write_text(render(bytes(raw)), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({
        "elapsed": elapsed,
        "exit_code": code,
        "argv": argv,
        "bridge_argv": bridge_args,
        "kimi_home": str(home),
        "workdir": str(work),
        "event_types": [e.get("type") or e.get("role") for e in events],
        "output_bytes": len(raw),
        "stderr_tail": stderr[-2000:],
        "home_tree": sorted(str(p.relative_to(home)) for p in home.rglob("*")),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log.close()
    server.shutdown()
    server.server_close()
    print(json.dumps({"exit": code, "elapsed": elapsed, "home_tree":
                      sorted(str(p.relative_to(home)) for p in home.rglob("*"))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
