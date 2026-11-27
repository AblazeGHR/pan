"""Lifecycle shortcuts: legacy restart HTTP and authenticated-local stop CLI."""

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
    "restart": (ROOT / "scripts" / "restart.bat", "/api/system-actions/main-lifecycle"),
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
    assert len(_FakeApiHandler.requests) == 1
    path, body = _FakeApiHandler.requests[0]
    assert path == SHORTCUTS[name][1]
    assert json.loads(body) == {"operation": "exit" if name == "stop" else "restart"}


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


def test_stop_uses_bound_cli_without_http_credentials_or_kill_fallback():
    text = (ROOT / "scripts/stop.bat").read_text(encoding="utf-8").lower()
    assert '"%~dp0pan_cli.py" --root "%~dp0.." exit' in text
    assert "exit /b %errorlevel%" in text
    assert "--diagnose" in text
    assert not any(x in text for x in ("curl", "authorization", "taskkill", "stop-process", "launcher"))


@WINDOWS_ONLY
@pytest.mark.parametrize("code", [0, 1, 7])
def test_stop_passes_checkout_root_and_propagates_cli_terminal_result(tmp_path, code):
    import shutil
    import sys
    root = tmp_path / "Pan space 安装"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/stop.bat", scripts / "stop.bat")
    import venv
    venv.EnvBuilder(with_pip=False).create(root / ".venv")
    (scripts / "pan_cli.py").write_text(
        "import sys,json;print(json.dumps(sys.argv[1:]));sys.exit(" + str(code) + ")",
        encoding="utf-8")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(scripts / "stop.bat")],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == code, result.stderr
    args = json.loads(result.stdout.strip())
    assert args[0] == "--root" and Path(args[1]).resolve() == root.resolve()
    assert args[2:] == ["exit"]
