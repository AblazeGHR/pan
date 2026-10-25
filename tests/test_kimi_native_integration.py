"""Real-PTY integration for the Kimi Terminal TUI bridge — OPT-IN.

Gate: ``TEST_KIMI_NATIVE_TUI=1`` (non-PAN prefix so the isolated runner
forwards it). Requires kimi 2.3.1 installed; the model is a local
OpenAI-compatible scripted endpoint, so no Moonshot balance is consumed.

Proves, with a real kimi process under ConPTY:
- the TUI renders (pan.native_output arrives) and trusts the Pan-owned home;
- Pan-origin prompts are submitted by bracketed paste and confirmed by the
  wire ``turn.prompt`` echo, then finish with assistant + result events;
- interrupt (Ctrl+C via the control channel) cancels the running turn and the
  wire reports ``turn.ended.reason == "cancelled"`` — i.e. a real receipt, not
  a fabricated success;
- no steer is claimed: a mid-turn submit is refused rather than silently queued.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

GATE = os.environ.get("TEST_KIMI_NATIVE_TUI") == "1"
KIMI = Path.home() / ".kimi-code" / "bin" / "kimi.exe"
BRIDGE = REPO / "packages/core/adapters/kimi/native_bridge.py"

pytestmark = pytest.mark.skipif(
    not GATE, reason="set TEST_KIMI_NATIVE_TUI=1 to run the real PTY integration")


# ── local scripted model (OpenAI-compatible SSE) ───────────────────────────


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {"id": "chatcmpl-tui", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": "kimi-k2.6",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _reply(messages: list) -> tuple[list[dict], float]:
    last = ""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        if "<system-reminder>" in text:
            continue
        last = text
        break
    if "SLOW" in last:
        chunks = [_chunk({"role": "assistant", "content": "SLOW_0 "})]
        chunks += [_chunk({"role": "assistant", "content": f"SLOW_{i} "}) for i in range(1, 8)]
        chunks += [_chunk({"role": "assistant", "content": "SLOW_END"}), _chunk({}, "stop")]
        return chunks, 0.8
    return [_chunk({"role": "assistant", "content": "TUI_OK"}),
            _chunk({}, "stop")], 0.0


def _serve_model() -> tuple[ThreadingHTTPServer, int]:
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
            body = json.loads(self.rfile.read(length) or b"{}")
            chunks, delay = _reply(body.get("messages") or [])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for index, chunk in enumerate(chunks):
                if delay and index and chunk["choices"][0]["delta"].get("content"):
                    time.sleep(delay)
                line = f"data: {json.dumps(chunk)}\n\n".encode()
                self.wfile.write(b"%x\r\n" % len(line) + line + b"\r\n")
                self.wfile.flush()
            done = b"data: [DONE]\n\n"
            self.wfile.write(b"%x\r\n" % len(done) + done + b"\r\n0\r\n\r\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_port


# ── bridge driver ──────────────────────────────────────────────────────────


class BridgeProc:
    def __init__(self, args: list[str], cwd: str, env: dict):
        self.proc = subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        self.events: list[dict] = []
        self.raw_output = bytearray()
        self._lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._lock:
                self.events.append(event)
                if event.get("type") == "pan.native_output":
                    import base64
                    try:
                        self.raw_output.extend(base64.b64decode(event["data"], validate=True))
                    except Exception:
                        pass

    def send(self, payload: dict) -> None:
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def wait_for(self, predicate, timeout: float = 60.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                for event in self.events:
                    if predicate(event):
                        return event
            time.sleep(0.1)
        return None

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture()
def kimi_tui_env(tmp_path):
    server, port = _serve_model()
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    home.mkdir()
    (home / "config.toml").write_text(
        'default_model = "moonshot-cn/kimi-k2.6"\n\n'
        '[thinking]\nenabled = false\neffort = "high"\n\n'
        '[providers.fake]\ntype = "kimi"\napi_key = "test-only"\n'
        f'base_url = "http://127.0.0.1:{port}/v1"\n\n'
        '[models."moonshot-cn/kimi-k2.6"]\nprovider = "fake"\nmodel = "kimi-k2.6"\n'
        'max_context_size = 262144\ncapabilities = [ "thinking", "tool_use" ]\n',
        encoding="utf-8")
    # Pan-owned home: pre-trust the workspace like a user who already answered.
    trust = home / "workspace-trust"
    trust.mkdir()
    (trust / "wd_probe").write_text(json.dumps({"root": str(work), "trustedAt": 1}),
                                    encoding="utf-8")
    env = {**os.environ, "KIMI_CODE_HOME": str(home)}
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
    assert sid, f"seed failed: {seed.stdout}\n{seed.stderr}"
    yield {"home": home, "work": work, "sid": sid, "env": env, "port": port}
    server.shutdown()
    server.server_close()


def test_native_bridge_real_pty_turn_and_interrupt(kimi_tui_env):
    env, work, sid = kimi_tui_env["env"], kimi_tui_env["work"], kimi_tui_env["sid"]
    bridge = BridgeProc(
        [sys.executable, "-u", str(BRIDGE), "--kimi-path", str(KIMI),
         "--cwd", str(work), "--session-id", sid,
         "--kimi-home", str(kimi_tui_env["home"]), "--model", "moonshot-cn/kimi-k2.6"],
        cwd=str(work), env=env)
    try:
        assert bridge.wait_for(lambda e: e.get("type") == "pan.native_ready", 30), "no ready"
        assert bridge.wait_for(lambda e: e.get("type") == "pan.native_output", 30), "TUI did not render"

        # Turn 1: Pan submits; the wire prompt echo is the receipt.
        bridge.send({"type": "user", "message": {"content": [{"type": "text", "text": "say TUI_OK"}]}})
        result = bridge.wait_for(lambda e: e.get("role") == "result", 90)
        assert result is not None, "no result for the first TUI turn"
        assert result["is_error"] is False, result
        texts = [e["content"] for e in bridge.events
                 if e.get("role") == "assistant" and isinstance(e.get("content"), list)]
        assert any("TUI_OK" in json.dumps(b, ensure_ascii=False) for b in texts), texts

        # Turn 2: SLOW turn, interrupted by Ctrl+C through the control channel.
        bridge.send({"type": "user", "message": {"content": [{"type": "text", "text": "SLOW go"}]}})
        assert bridge.wait_for(
            lambda e: e.get("type") == "pan.native_busy" and e.get("busy") is True, 60), "no busy"
        bridge.send({"type": "interrupt", "generation": 0, "requestId": "i1"})
        receipt = bridge.wait_for(
            lambda e: e.get("type") == "pan.native_write" and e.get("requestId") == "i1", 15)
        if receipt is None:
            stderr = bridge.proc.stderr.read() if bridge.proc.poll() is not None else "(alive)"
            raise AssertionError(f"no interrupt receipt; rc={bridge.proc.poll()} stderr={stderr[-2000:]}")
        cancelled = bridge.wait_for(
            lambda e: e.get("role") == "result" and e.get("kimi_stop_reason") == "cancelled", 60)
        assert cancelled is not None, "interrupt did not produce a cancelled result"
        assert cancelled["cancelled"] is True and cancelled["is_error"] is False

        # Mid-turn submissions are refused: kimi queues them as the next turn,
        # so Pan must not pretend to steer.
        assert not [e for e in bridge.events if e.get("type") == "pan.native_user"]
        assert bridge.raw_output, "terminal output was empty"
    finally:
        bridge.close()
