"""Restart API regression tests for the Python launcher supervisor."""

import asyncio
import sys
from pathlib import Path

import pytest

import packages.web.server as srv
from packages.core import background_jobs, launcher


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(background_jobs, "DEFAULT_ROOT", tmp_path / "registry")
    monkeypatch.setattr(background_jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(srv, "_main_restart_pending", False)
    monkeypatch.setattr(srv, "_main_restart_request_id", None)


def test_restart_is_disabled_only_off_platform(monkeypatch):
    status = srv._main_restart_status()
    if sys.platform == "win32":
        assert status["available"] is True
    else:
        assert status["available"] is False


def test_restart_returns_scheduled_before_python_supervisor_finishes(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("detached Windows supervisor contract")
    monkeypatch.setattr(launcher, "resolve_python_argv", lambda *args, **kwargs: ([sys.executable], "test"))
    monkeypatch.setattr(srv.main_lifecycle, "listener_owner", lambda port: 41)
    monkeypatch.setattr(srv.main_lifecycle, "process_create_time", lambda pid: 12.5)
    calls = []

    class FakeProcess:
        pid = 4242

    monkeypatch.setattr(srv.subprocess, "Popen", lambda command, **kwargs: calls.append((command, kwargs)) or FakeProcess())
    result = asyncio.run(srv.api_main_restart())
    assert result["ok"] is True
    assert result["status"] == "scheduled"
    command = calls[0][0]
    assert command[:6] == ["cmd.exe", "/d", "/c", "start", "", "/b"]
    assert "-m" in command
    assert "packages.core.main_lifecycle" in command
    assert "--supervise" in command
    assert "stop_pan.bat" not in " ".join(command)


def test_persisted_duplicate_restart_is_rejected_without_spawn(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("detached Windows supervisor contract")
    monkeypatch.setattr(srv, "_main_restart_pending", True)
    monkeypatch.setattr(srv, "_main_restart_request_id", "already-running")
    monkeypatch.setattr(srv.subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))
    result = asyncio.run(srv.api_main_restart())
    assert result["status"] == "busy"
    assert result["pending"] is True


def test_restart_supervisor_and_start_entry_have_no_batch_business_chain():
    root = Path(__file__).resolve().parents[1]
    lifecycle = (root / "packages" / "core" / "main_lifecycle.py").read_text(encoding="utf-8")
    start = (root / "scripts" / "start_pan.bat").read_text(encoding="utf-8").lower()
    assert "stop_pan.bat" not in lifecycle
    assert "start_pan.bat" not in lifecycle
    assert "packages.core.launcher" in start
    assert "powershell" not in start
    assert "taskkill" not in start


def test_restart_compatibility_wrapper_only_delegates_to_python():
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts" / "restart_pan.ps1").read_text(encoding="utf-8")
    assert "packages.core.main_lifecycle" in text
    assert "stop_pan.bat" not in text
    assert "start_pan.bat" not in text
