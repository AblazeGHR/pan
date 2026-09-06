"""Safety tests for the App Settings main-service restart API.

These tests only exercise path validation and mocked process creation.  They
never call stop_pan.bat/start_pan.bat and never touch a real Pan listener.
"""

import asyncio
import json
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
    assert calls[0][0][0:6] == ["cmd.exe", "/d", "/c", "start", "", "/b"]
    assert calls[0][0][6] == "powershell.exe"
    assert "-NonInteractive" in calls[0][0]
    assert str(tmp_path / "scripts" / "restart_pan.ps1") in calls[0][0]
    assert calls[0][1]["cwd"] == str(tmp_path)
    assert calls[0][1]["stdin"] is srv.subprocess.DEVNULL
    assert calls[0][1]["stdout"].name == str(tmp_path / "data" / "logs" / "pan-restart-launcher.log")
    assert calls[0][1]["stderr"] is srv.subprocess.STDOUT
    assert "startupinfo" not in calls[0][1]
    assert calls[0][1]["creationflags"] & getattr(srv.subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert calls[0][1]["creationflags"] & getattr(srv.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    assert not calls[0][1]["creationflags"] & getattr(srv.subprocess, "DETACHED_PROCESS", 0x00000008)


def test_restart_launcher_enters_supervisor_directly_with_durable_binding(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "restart_pan.ps1").write_text("# test placeholder", encoding="utf-8")
    (tmp_path / "data" / "background_jobs").mkdir(parents=True)
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)

    request_id = "request-direct-supervisor"
    registry = tmp_path / "data" / "background_jobs"
    job = srv.background_jobs.create_service_job(
        request_id=request_id,
        operation="restart",
        root=str(tmp_path),
        port=8770,
        old_pid=42072,
        old_pid_created_at=1757127877.0,
        registry_root=registry,
    )
    calls = []

    class FakeProcess:
        pid = 4242

    monkeypatch.setattr(
        srv.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)) or FakeProcess(),
    )

    srv._launch_main_restart_supervisor(request_id)
    command = calls[0][0]
    assert command[0:6] == ["cmd.exe", "/d", "/c", "start", "", "/b"]
    assert command[6:13] == [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(scripts / "restart_pan.ps1"),
    ]
    for argument, value in (
        ("-Root", str(tmp_path)),
        ("-RequestId", request_id),
        ("-JobId", job["jobId"]),
        ("-RegistryRoot", str(registry)),
        ("-Port", "8770"),
        ("-OldPid", "42072"),
        ("-OldPidCreatedAt", "1757127877.0"),
    ):
        assert command[command.index(argument) + 1] == value
    assert command[command.index("-Supervisor") + 1:] == [
        "-OldPid", "42072", "-OldPidCreatedAt", "1757127877.0",
    ]
    assert calls[0][1]["stdout"].name == str(
        tmp_path / "data" / "logs" / "pan-restart-launcher.log"
    )
    assert calls[0][1]["stderr"] is srv.subprocess.STDOUT


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
    # The request-side hop must preserve the durable Job identity and the
    # checkout/port metadata when it creates the actual detached supervisor.
    hop = text.split("if (-not $Supervisor)", 1)[1].split("try {", 1)[0]
    for argument in ("-Root", "-RequestId", "-JobId", "-RegistryRoot", "-Port", "-Supervisor"):
        assert f'"{argument}"' in hop
    assert '"-OldPid", $OldPid' in hop
    assert '"-OldPidCreatedAt", $OldPidCreatedAt' in hop


def test_startup_scripts_use_detached_diagnostics_and_checkout_boundaries():
    root = Path(__file__).resolve().parent.parent
    start = (root / "scripts" / "start_pan.bat").read_text(encoding="utf-8")
    start_main = (root / "scripts" / "start_main.ps1").read_text(encoding="utf-8")
    stop = (root / "scripts" / "stop_pan.bat").read_text(encoding="utf-8")
    probe = (root / "scripts" / "start_pan_probe.ps1").read_text(encoding="utf-8")
    config = json.loads((root / "config.example.json").read_text(encoding="utf-8"))

    # A double-clicked batch file must leave enough evidence for failures that
    # happen before Pan's file logger is initialized, and the server must not
    # depend on the launcher's console lifetime in either window mode.
    assert "[bool]$ConsoleHidden = $true" in start_main
    assert "startup.console_hidden" in start_main
    assert "-WindowStyle Hidden" in start_main
    assert "-WindowStyle Normal" in start_main
    assert "-RedirectStandardOutput $StdoutFile" in start_main
    assert "-RedirectStandardError $StderrFile" in start_main
    assert "-StdoutFile \"%PAN_STDOUT%\"" in start
    assert "-StderrFile \"%PAN_STDERR%\"" in start
    assert config["startup"]["console_hidden"] is True

    # Prefixes such as D:\\project\\Pan-test must not be treated as this
    # checkout.  Start and stop use the same boundary-aware contract.
    assert ".Contains($root)" in probe
    assert ".Contains($root)" in stop
    assert "Replace('\\\\','/')" not in stop
    assert "Replace('\\','/')" in stop


def test_startup_batch_delegates_nested_powershell_to_parser_safe_helper():
    root = Path(__file__).resolve().parent.parent
    start = (root / "scripts" / "start_pan.bat").read_text(encoding="utf-8")
    probe = (root / "scripts" / "start_pan_probe.ps1").read_text(encoding="utf-8")

    assert "start_pan_probe.ps1" in start
    assert "-Action ExistingMainPid" in start
    assert "-Action Port" in start
    assert "-Action RemoteState" in start
    assert "-Action QuickState" in start
    assert "-Action QuickUrl" in start
    assert "-Action WaitReady" in start
    assert "-Action ProcessAlive" in start
    # These commands used to put PowerShell control-flow parentheses inside
    # CMD's parenthesized FOR/IF blocks.  The only remaining inline probe is
    # the simple process sleep, which has no PowerShell control-flow syntax.
    assert "for ($" not in start
    assert "Where-Object" not in start
    assert "ConvertFrom-Json" not in start
    assert "Invoke-WebRequest" not in start
    assert "ValidateSet" in probe


def test_startup_batch_does_not_block_on_unbounded_checkout_cache_sweep():
    start = (Path(__file__).resolve().parent.parent / "scripts" / "start_pan.bat").read_text(
        encoding="utf-8"
    )

    # The main service does not need a preflight bytecode purge.  A recursive
    # sweep traverses data/workdirs and frontend dependencies and can prevent
    # the restart supervisor from reaching main.py before its timeout.
    assert "for /d /r" not in start.lower()
    assert "del /s /f /q" not in start.lower()


def test_startup_batch_uses_one_continuous_readiness_probe():
    start = (Path(__file__).resolve().parent.parent / "scripts" / "start_pan.bat").read_text(
        encoding="utf-8"
    )
    probe = (Path(__file__).resolve().parent.parent / "scripts" / "start_pan_probe.ps1").read_text(
        encoding="utf-8"
    )

    assert '-Action WaitReady' in start
    assert 'for /l' not in start.lower()
    assert 'Start-Sleep -Seconds 1' not in start
    assert '"WaitReady"' in probe
    assert 'Start-Sleep -Milliseconds 250' in probe


def test_startup_batch_escapes_parentheses_in_remote_disabled_echo():
    start = (Path(__file__).resolve().parent.parent / "scripts" / "start_pan.bat").read_text(
        encoding="utf-8"
    )
    assert (
        "echo [INFO] remote.enabled is not explicitly true ^(%PAN_REMOTE_STATE%^), "
        "skipping Cloudflare Tunnel."
    ) in start
    assert "echo [INFO] remote.enabled is not explicitly true (%PAN_REMOTE_STATE%)," not in start

    # Every literal parenthesis on an echo line must be caret-escaped. This
    # protects future conditional-block messages from the same CMD regression.
    for line in start.splitlines():
        if line.lstrip().lower().startswith("echo "):
            assert "(" not in line.replace("^(", "")
            assert ")" not in line.replace("^)", "")
