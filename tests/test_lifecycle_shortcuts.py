"""Windows lifecycle BAT shortcuts: API-only static and fake-HTTP coverage."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SHORTCUTS = {
    "restart": (ROOT / "scripts" / "restart.bat", "/api/main/restart"),
    "stop": (ROOT / "scripts" / "stop.bat", "/api/main/exit"),
}
WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows batch runtime")
FORBIDDEN = (
    "taskkill",
    "tasklist",
    "wmic",
    "stop-process",
    "get-process",
    "get-ciminstance",
    "netstat",
    "start_pan.bat",
    "stop_pan.bat",
    "restart_pan.ps1",
    "exit_pan.ps1",
    "main_lifecycle",
    "launcher",
    "supervisor",
    "pid",
)


def test_shortcuts_are_thin_api_clients_with_project_port_defaults():
    for script, endpoint in SHORTCUTS.values():
        text = script.read_text(encoding="utf-8").lower()
        assert endpoint in text
        assert "pan_port" in text
        assert "config.json" in text
        assert "8768" in text
        assert "curl.exe" in text
        assert "convertfrom-json" in text
        assert "payload.ok -ne $true" in text
        assert "exit /b %errorlevel%" in text
        assert not any(term in text for term in FORBIDDEN), script


class _FakeApiHandler(BaseHTTPRequestHandler):
    response_status = 200
    response_body = {"ok": True, "status": "scheduled"}
    requests: list[tuple[str, bytes]] = []

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API name
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).requests.append((self.path, body))
        encoded = json.dumps(type(self).response_body).encode("utf-8")
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler API name
        return


@pytest.fixture
def fake_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeApiHandler)
    _FakeApiHandler.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run_shortcut(name: str, port: int, extra_env: dict[str, str] | None = None):
    script, _ = SHORTCUTS[name]
    env = os.environ.copy()
    env.update({"PAN_PORT": str(port), **(extra_env or {})})
    command = f"call {script}"
    return subprocess.run(
        ["cmd.exe", "/d", "/c", command],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@WINDOWS_ONLY
@pytest.mark.parametrize("name", sorted(SHORTCUTS))
def test_shortcut_returns_zero_and_prints_scheduled_response(name, fake_api):
    _FakeApiHandler.response_status = 200
    _FakeApiHandler.response_body = {"ok": True, "status": "scheduled"}

    result = _run_shortcut(name, fake_api)

    assert result.returncode == 0, result.stderr or result.stdout
    assert '"status": "scheduled"' in result.stdout
    assert _FakeApiHandler.requests == [(SHORTCUTS[name][1], b"{}")]


@WINDOWS_ONLY
@pytest.mark.parametrize("name", sorted(SHORTCUTS))
def test_shortcut_returns_nonzero_for_business_failure(name, fake_api):
    _FakeApiHandler.response_status = 200
    _FakeApiHandler.response_body = {"ok": False, "status": "busy", "error": "already scheduled"}

    result = _run_shortcut(name, fake_api)

    assert result.returncode != 0
    assert '"ok": false' in result.stdout.lower()


@WINDOWS_ONLY
@pytest.mark.parametrize("name", sorted(SHORTCUTS))
def test_shortcut_returns_nonzero_for_http_failure(name, fake_api):
    _FakeApiHandler.response_status = 503
    _FakeApiHandler.response_body = {"detail": "unavailable"}

    result = _run_shortcut(name, fake_api)

    assert result.returncode != 0
    assert "unavailable" in result.stdout
