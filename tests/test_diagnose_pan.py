import importlib.util
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location("diagnose_pan", Path(__file__).parents[1] / "scripts/diagnose_pan.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


@pytest.fixture
def http_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/blocked":
                time.sleep(0.3)
            body = b'{"healthy":true}'
            self.send_response(200)
            self.send_header("X-Pan-Request-Id", "owned-request")
            self.send_header("Content-Length", str(len(body) + (10 if self.path == "/short-body" else 0)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_external_probes_distinguish_headers_body_and_timeout(http_server):
    base = f"http://127.0.0.1:{http_server.server_port}"
    healthy = tool.probe(base, "/api/diagnostics/runtime")
    assert healthy["bodyComplete"] and healthy["status"] == 200
    assert healthy["requestId"] == "owned-request"
    assert healthy["diagnostics"] == {"healthy": True}
    blocked = tool.probe(base, "/blocked", timeout=0.05)
    assert "errorType" in blocked and blocked["totalMs"] < 500
    truncated = tool.probe(base, "/react/", max_bytes=2)
    assert truncated["bodyComplete"] is False and truncated["bodyBytes"] == 3
    short = tool.probe(base, "/short-body")
    assert short["bodyComplete"] is False
    assert short["bodyBytes"] < short["bodyExpectedBytes"]


def test_capture_preserves_both_log_locations_with_hashes_and_no_stop(tmp_path, http_server, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "main.py").write_text("pass", encoding="utf-8")
    for directory in ["data/logs", "data/background_jobs/logs"]:
        p = root / directory
        p.mkdir(parents=True)
        (p / "pan-console.out.log").write_bytes(b"original evidence")
    (root / "data/logs/runtime-diagnostics.jsonl").write_bytes(b"original evidence")
    monkeypatch.setattr(tool.psutil, "net_connections", lambda **kw: [])
    monkeypatch.setattr(tool.shutil, "which", lambda name: None)
    monkeypatch.setattr(tool.subprocess, "run", lambda argv, **kw: SimpleNamespace(stdout=b"git", stderr=b""))
    result = tool.capture(root, tmp_path / "output", http_server.server_port, stack_samples=0)
    manifest = json.loads((result / "log-manifest.json").read_text(encoding="utf-8"))
    assert len(manifest) == 3
    assert all(x["offset"] == 0 and x["capturedBytes"] == 17 for x in manifest)
    assert len({x["sha256"] for x in manifest}) == 1
    assert len(json.loads((result / "http-probes.json").read_text(encoding="utf-8"))) == 5
    assert (root / "data/logs/pan-console.out.log").read_bytes() == b"original evidence"
    with pytest.raises(FileExistsError):
        tool.capture(root, result, http_server.server_port)


def test_attach_requires_exact_entry_path_and_creation_time(tmp_path, monkeypatch):
    root = tmp_path / "Pan"
    root.mkdir()
    process = SimpleNamespace(cmdline=lambda: ["python", str(root / "main.py")],
                              create_time=lambda: 10.0, name=lambda: "python")
    monkeypatch.setattr(tool.psutil, "Process", lambda pid: process)
    assert tool.process_identity(123, root, 10)["pid"] == 123
    assert tool.process_identity(123, root, 12) is None
    assert tool.process_identity(123, tmp_path / "Pan-test", 10) is None
