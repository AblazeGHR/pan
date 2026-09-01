"""Safety tests for the App Settings main-service restart API.

These tests only exercise path validation and mocked process creation.  They
never call stop_pan.bat/start_pan.bat and never touch a real Pan listener.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.web.server as srv  # noqa: E402


def _fake_scripts(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("restart_pan.ps1", "stop_pan.bat", "start_pan.bat"):
        (scripts / name).write_text("# test placeholder", encoding="utf-8")


@pytest.fixture(autouse=True)
def reset_restart_state(monkeypatch):
    monkeypatch.setattr(srv, "_main_restart_pending", False)
    monkeypatch.setattr(srv, "_main_restart_request_id", None)


def test_status_is_disabled_when_safe_scripts_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    status = asyncio.run(srv.api_main_restart_status())
    assert status["available"] is False
    assert status["pending"] is False
    assert "missing" in status["reason"] or "Windows" in status["reason"]


def test_restart_does_not_spawn_when_scripts_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(srv.subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))
    result = asyncio.run(srv.api_main_restart())
    assert result["ok"] is False
    assert result["status"] == "disabled"
    assert result["error"]


def test_duplicate_restart_is_rejected_without_second_spawn(tmp_path, monkeypatch):
    _fake_scripts(tmp_path)
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    if os.name != "nt":
        pytest.skip("Windows supervisor is intentionally disabled on POSIX")
    monkeypatch.setattr(srv, "_main_restart_pending", True)
    monkeypatch.setattr(srv, "_main_restart_request_id", "already-running")
    monkeypatch.setattr(srv.subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))

    result = asyncio.run(srv.api_main_restart())
    assert result["ok"] is False
    assert result["status"] == "busy"
    assert result["pending"] is True
    assert result["requestId"] == "already-running"


def test_restart_returns_scheduled_before_supervisor_finishes(tmp_path, monkeypatch):
    _fake_scripts(tmp_path)
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    if os.name != "nt":
        pytest.skip("Windows supervisor is intentionally disabled on POSIX")

    class FakeProcess:
        pid = 4242

        def wait(self):
            raise AssertionError("the API must not wait for the supervisor")

    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(srv.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(srv, "_watch_main_restart", lambda process, request_id: None)

    result = asyncio.run(srv.api_main_restart())
    assert result["ok"] is True
    assert result["status"] == "scheduled"
    assert result["requestId"]
    assert calls[0][0][0] == "powershell.exe"
    assert str(tmp_path / "scripts" / "restart_pan.ps1") in calls[0][0]
    assert calls[0][1]["cwd"] == str(tmp_path)
    assert calls[0][1]["stdin"] is srv.subprocess.DEVNULL


def test_restart_spawn_failure_clears_duplicate_guard(tmp_path, monkeypatch):
    _fake_scripts(tmp_path)
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    if os.name != "nt":
        pytest.skip("Windows supervisor is intentionally disabled on POSIX")

    def fail_popen(*args, **kwargs):
        raise OSError("PowerShell unavailable")

    monkeypatch.setattr(srv.subprocess, "Popen", fail_popen)
    result = asyncio.run(srv.api_main_restart())
    assert result["ok"] is False
    assert result["status"] == "error"
    assert "PowerShell unavailable" in result["error"]
    assert srv._main_restart_pending is False


def test_supervisor_script_is_a_stop_then_start_chain():
    script = Path(__file__).resolve().parent.parent / "scripts" / "restart_pan.ps1"
    text = script.read_text(encoding="utf-8")
    assert "stop_pan.bat" in text
    assert "start_pan.bat" in text
    assert "Start-Sleep -Seconds 1" in text
    assert "-Supervisor" in text
