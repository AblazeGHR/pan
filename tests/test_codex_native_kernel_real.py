"""Optional live CLI check with private home and a synthetic local provider."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import threading
import subprocess
from queue import Queue, Empty
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class SyntheticResponses:
    """Local finite Responses SSE fixture: no external model/account traffic."""
    def __init__(self):
        self.release = threading.Event()
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if not fixture.release.wait(20):
                    return
                item = {"id": "msg_native", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "synthetic native answer", "annotations": []}]}
                events = [
                    {"type": "response.created", "response": {"id": "resp_native", "status": "in_progress"}},
                    {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "content": []}},
                    {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0, "content_index": 0, "delta": "synthetic native answer"},
                    {"type": "response.output_item.done", "output_index": 0, "item": item},
                    {"type": "response.completed", "response": {"id": "resp_native", "status": "completed", "output": [item],
                      "usage": {"input_tokens": 1, "output_tokens": 3, "total_tokens": 4}}},
                ]
                raw = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ConPTY proof")
@pytest.mark.parametrize("native_start", [False, True])
def test_real_wrapper_mode_round_trip_does_not_exit_kernel(tmp_path, native_start):
    from packages.core.terminal.guard import JobObjectGuard
    from packages.core.adapters.codex.adapter import _resolve_codex_node, _resolve_codex_js
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("native_probe_helper", root / "audit/terminal/codex/probe_codex_remote_tui.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    home = tmp_path / "codex-home"
    home.mkdir()
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    helper.seed_placeholder_credentials(str(home), [])
    provider = SyntheticResponses()
    backend = None
    guard = JobObjectGuard()
    try:
        options = ["-c", 'model_provider="probe_bh"', "-c", 'model="probe-model"', "-c",
            f'model_providers.probe_bh={{name="probe_bh",base_url="{provider.base_url}",wire_api="responses"}}']
        env = dict(os.environ, CODEX_HOME=str(home))
        argv = [sys.executable, "-u", str(root / "packages/core/adapters/codex/wrapper.py"),
            "--app-server", "--native-control", "--node-path", _resolve_codex_node(),
            "--codex-path", _resolve_codex_js(), "--codex-extra-args", json.dumps(options)]
        # Production Worker uses pipes, not a PTY around JSON stdout. A PTY can
        # wrap a long base64 JSON frame and makes newline parsing dishonest.
        backend = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        guard.assign(int(backend._handle))
        assert guard.is_member(int(backend._handle))
        lines = Queue()
        stderr = []
        def read_lines():
            for line in backend.stdout:
                lines.put(line)
        def read_stderr():
            for line in backend.stderr:
                stderr.append(line)
        reader = threading.Thread(target=read_lines, daemon=True)
        error_reader = threading.Thread(target=read_stderr, daemon=True)
        reader.start(); error_reader.start()
        def write(raw):
            backend.stdin.write(raw.replace(b"\r", b"\n"))
            backend.stdin.flush()
        events = []
        rejected_lines = []
        def wait_for(predicate, seconds=15):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=.1)
                except Empty:
                    line = None
                if line is not None:
                    try:
                        event = json.loads(line.decode("utf-8", errors="strict"))
                    except (ValueError, UnicodeError):
                        rejected_lines.append(line)
                        continue
                    if isinstance(event, dict):
                        events.append(event)
                found = next((event for event in events if predicate(event)), None)
                if found is not None:
                    return found
                assert backend.poll() is None, f"Wrapper exited; stderr={stderr[-5:]}"
            import base64
            screen = b"".join(base64.b64decode(e["data"]) for e in events if e.get("type") == "pan.native_output")
            raise AssertionError(f"Expected native wrapper event missing; types={[e.get('type') for e in events]}; rejected={rejected_lines[-4:]!r}; stderr={stderr[-5:]}; screen={helper.strip_ansi(screen.decode(errors='replace'))[-4500:]}")
        init = wait_for(lambda e: e.get("type") == "thread.started")
        assert init["thread_id"]
        # Live policy changes don't require a TUI, a turn, or a kernel restart.
        for enabled in (True, False):
            request_id = 'policy-' + str(enabled)
            write((json.dumps({'type': 'native_passthrough', 'requestId': request_id, 'enabled': enabled}) + '\n').encode())
            receipt = wait_for(lambda e: e.get('type') == 'pan.native_write' and e.get('requestId') == request_id)
            assert receipt['ok'] is True and backend.poll() is None
        if native_start:
            write(b'{"type":"control_mode","requestId":"initial","mode":"native","generation":0}\r')
            first = wait_for(lambda e: e.get("type") == "pan.control_mode" and e.get("requestId") == "initial")
            import base64
            screen = b"".join(base64.b64decode(e["data"]) for e in events if e.get("type") == "pan.native_output")
            assert first["ok"] is True, screen[-6000:].decode(errors="replace")
            # Real native keyboard path, not a direct turn/start RPC substitute.
            # Answer the terminal cursor query and then submit via the TUI.
            write((json.dumps({"type": "native_input", "generation": 1, "text": "\u001b[1;1R"}) + "\r").encode())
            time.sleep(.15)
            write((json.dumps({"type": "native_input", "generation": 1, "text": "NATIVE 中文"}) + "\r").encode())
            time.sleep(1.0)  # Real TUI paste coalescing ends before Enter.
            write((json.dumps({"type": "native_input", "generation": 1, "text": "\r"}) + "\r").encode())
            wait_for(lambda e: e.get("type") == "pan.native_turn_started")
            switches = [("p1", "pan", 1), ("n2", "native", 2), ("p2", "pan", 3)]
        else:
            write(b'{"text":"LOCAL SYNTHETIC PENDING TURN"}\r')
            switches = [("n1", "native", 0), ("p1", "pan", 1), ("n2", "native", 2), ("p2", "pan", 3)]
        wait_for(lambda e: e.get("type") == "codex.thread_status" and e.get("native_status", {}).get("type") == "active")
        kernel = None
        for request, mode, generation in switches:
            write((json.dumps({"type": "control_mode", "requestId": request, "mode": mode, "generation": generation}) + "\r").encode())
            receipt = wait_for(lambda e: e.get("type") == "pan.control_mode" and e.get("requestId") == request)
            import base64
            native_output = b"".join(base64.b64decode(e["data"]) for e in events if e.get("type") == "pan.native_output")
            assert receipt["ok"] is True and receipt["mode"] == mode, native_output[-6000:].decode("utf-8", errors="replace")
            assert receipt["generation"] == generation + 1
            current_kernel = (receipt["thread_id"], receipt["kernel_pid"], receipt["kernel_filetime"], receipt["tui_pid"])
            if kernel is None:
                kernel = current_kernel
            assert current_kernel == kernel
            assert backend.poll() is None
        assert not any(e.get("type") == "result" for e in events)
        provider.release.set()
        result = wait_for(lambda e: e.get("type") == "result")
        assert result.get("is_error") is False and "synthetic native answer" in result["result"]
        if native_start:
            assert result["external_turn"] is True
            assert any(e.get("type") == "pan.native_user" and "中文" in e.get("text", "") for e in events)
    finally:
        if backend is not None:
            _, remaining = guard.terminate_tree(backend.pid, timeout=3)
            assert not remaining
            backend.wait(3)
            reader.join(2); error_reader.join(2)
            for stream in (backend.stdin, backend.stdout, backend.stderr):
                stream.close()
        assert guard.close()
        provider.close()
