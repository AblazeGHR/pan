"""Default hidden command windows, explicit opt-out, and old-record policy."""
import asyncio
import json
import os
import subprocess
import sys
from unittest.mock import Mock

import pytest

from packages.jobs.window import command_window_options, validate_hide_window, windows_creation_flags


@pytest.mark.parametrize("value", [None, "true", "false", 0, 1, [], {}])
def test_window_policy_rejects_non_boolean(value):
    with pytest.raises(ValueError):
        validate_hide_window(value)


@pytest.mark.parametrize("hidden", [True, False])
def test_flags_never_combine_new_console_with_no_window(hidden):
    assert windows_creation_flags(hidden) == (0x08000000 if hidden else 0x10)
    options = command_window_options(hidden)
    if os.name == "nt":
        assert options["startupinfo"].wShowWindow == (0 if hidden else 1)
        assert options["creationflags"] == windows_creation_flags(hidden)
    else:
        assert options == {}


@pytest.mark.parametrize("shell", [False, True])
@pytest.mark.parametrize("hidden", [None, True, False])
def test_legacy_runner_uses_policy_for_argv_shell_and_old_records(monkeypatch, tmp_path, shell, hidden):
    from packages.core import background_runner as runner
    job = {"jobId": "owned-test", "argv": ["python", "test.py"], "cwd": str(tmp_path), "logPath": str(tmp_path / "job.txt")}
    if shell:
        job["shellCommand"] = "echo owned"
    if hidden is not None:
        job["hideWindow"] = hidden
    spawn = Mock(return_value=Mock(pid=123, wait=Mock(return_value=0)))
    monkeypatch.setattr(runner.jobs, "get", lambda jid: job)
    monkeypatch.setattr(runner.jobs, "runner_update", Mock())
    monkeypatch.setattr(runner.jobs, "_process_create_time", lambda pid: 1)
    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    monkeypatch.setattr(sys, "argv", ["runner", "--job-id", job["jobId"]])
    assert runner.main() == 0
    kwargs = spawn.call_args.kwargs
    assert kwargs["stdin"] == subprocess.DEVNULL
    if os.name == "nt":
        assert kwargs["creationflags"] == windows_creation_flags(hidden is not False)
    assert kwargs.get("shell", False) is shell


def test_mcp_and_http_forward_explicit_visible_option(monkeypatch):
    from packages.mcp import server as mcp
    from packages.web import server as web
    calls = []
    monkeypatch.setattr(mcp, "_caller_identity", lambda: {"id": "ses_self"})
    monkeypatch.setattr(mcp, "_check_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(mcp, "_api", lambda method, path, body: calls.append(body) or body)
    assert mcp.agent_background_start(["python"], ".", hide_window=False)["hideWindow"] is False
    monkeypatch.setattr(web.background_jobs, "start", lambda *args, **kwargs: kwargs)
    assert asyncio.run(web.api_background_job_start({"hideWindow": False}))["hide_window"] is False
    assert asyncio.run(web.api_background_job_start({}))["hide_window"] is True


def test_independent_service_persists_and_retries_visible_policy(tmp_path, monkeypatch):
    from packages.jobs.independent_service import JobStore, ServiceError
    store = JobStore(str(tmp_path / "owned"))
    monkeypatch.setattr("packages.jobs.independent_service.subprocess.Popen", Mock())
    request = {"argv": [sys.executable], "cwd": str(tmp_path), "targetSessionId": "ses_self"}
    default = store.create(request)
    visible = store.create({**request, "hideWindow": False})
    assert default["hideWindow"] is True
    assert visible["hideWindow"] is False
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='failed' WHERE id=?", (visible["jobId"],))
    assert store.retry(visible["jobId"])["hideWindow"] is False
    with pytest.raises(ServiceError):
        store.create({**request, "hideWindow": "false"})


def test_independent_old_payload_idempotency_does_not_break(tmp_path, monkeypatch):
    from packages.jobs.independent_service import JobStore
    store = JobStore(str(tmp_path / "owned"))
    monkeypatch.setattr("packages.jobs.independent_service.subprocess.Popen", Mock())
    request = {"argv": [sys.executable], "cwd": str(tmp_path), "targetSessionId": "ses_self", "idempotencyKey": "old"}
    job = store.create(request)
    with store.connect() as db:
        row = db.execute("SELECT payload FROM jobs WHERE id=?", (job["jobId"],)).fetchone()
        old = json.loads(row["payload"]); old.pop("hideWindow")
        db.execute("UPDATE jobs SET payload=?,payload_hash=? WHERE id=?", (json.dumps(old), "historical-hash", job["jobId"]))
    assert store.create(request)["jobId"] == job["jobId"]


def test_shell_normalization_keeps_explicit_visibility_and_rejects_string(tmp_path):
    from packages.jobs.api import _normalize_scheduled_action as normalize_scheduled_action
    action = {"api": "shell", "args": {"command": "echo owned", "cwd": str(tmp_path), "hideWindow": False}}
    normalized, error = normalize_scheduled_action(action)
    assert error is None and normalized["args"]["hideWindow"] is False
    _, error = normalize_scheduled_action({**action, "args": {**action["args"], "hideWindow": "false"}})
    assert error is not None


@pytest.mark.skipif(os.name != "nt", reason="Windows console policy")
@pytest.mark.parametrize("hidden", [True, False])
def test_real_windows_child_console_allocation(hidden):
    # Only this owned, short-lived child. No window enumeration or external kill.
    program = "import ctypes,json; print(json.dumps({'console': bool(ctypes.windll.kernel32.GetConsoleWindow())}))"
    result = subprocess.run([sys.executable, "-c", program], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=10,
                            **command_window_options(hidden))
    assert result.returncode == 0
    assert json.loads(result.stdout)["console"] is (not hidden)
