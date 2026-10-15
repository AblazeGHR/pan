"""Default hidden command windows, explicit opt-out, and old-record policy."""
import asyncio
import json
import os
import subprocess
import sys
from unittest.mock import Mock, MagicMock
from pathlib import Path

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


def test_non_windows_options_have_no_windows_only_kwargs(monkeypatch):
    from packages.jobs import window
    monkeypatch.setattr(window, "os", Mock(name="posix"))
    window.os.name = "posix"
    assert window.command_window_options() == {}
    assert window.command_window_options(False) == {}


@pytest.mark.parametrize("shell", [False, True])
@pytest.mark.parametrize("hidden", [None, True, False])
def test_legacy_runner_uses_policy_for_argv_shell_and_old_records(monkeypatch, tmp_path, shell, hidden):
    from packages.core import background_runner as runner
    job = {"jobId": "owned-test", "argv": ["python", "test.py"], "cwd": str(tmp_path), "logPath": str(tmp_path / "job.txt")}
    if shell:
        job["shellCommand"] = "echo owned"
    if hidden is not None:
        job["hideWindow"] = hidden
    process = MagicMock(pid=123, wait=Mock(return_value=0))
    process.__enter__.return_value = process
    spawn = Mock(return_value=process)
    desktop_spawn = Mock(return_value=process)
    monkeypatch.setattr(runner, "DesktopCommand", desktop_spawn)
    monkeypatch.setattr(runner.jobs, "get", lambda jid: job)
    monkeypatch.setattr(runner.jobs, "runner_update", Mock())
    monkeypatch.setattr(runner.jobs, "claim_hidden_runner", Mock(return_value=True))
    monkeypatch.setattr(runner.jobs, "_process_create_time", lambda pid: 1)
    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    monkeypatch.setattr(sys, "argv", ["runner", "--job-id", job["jobId"]])
    assert runner.main() == 0
    if os.name == "nt" and hidden is not False:
        spawn.assert_not_called()
        assert desktop_spawn.call_args.args[0] == (job["shellCommand"] if shell else job["argv"])
        assert desktop_spawn.call_args.args[1] == job["cwd"]
        assert desktop_spawn.call_args.kwargs["shell"] is shell
        process.close.assert_not_called()  # __exit__ owns cleanup.
        process.__exit__.assert_called_once()
        return
    desktop_spawn.assert_not_called()
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


@pytest.mark.skipif(os.name != "nt", reason="Windows console/window APIs")
@pytest.mark.parametrize("hidden", [True, False])
def test_owned_python_tree_window_visibility(tmp_path, hidden):
    from ctypes import wintypes
    from packages.core.terminal.identity import read_creation_filetime, kernel32
    script = Path(__file__).parent / "helpers" / "window_tree_probe.py"
    process = subprocess.Popen([sys.executable, str(script.resolve()),
                                "hidden" if hidden else "visible", "2"],
                               cwd=tmp_path, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               **command_window_options(hidden))
    handle = int(process._handle)
    filetime = read_creation_filetime(handle)
    assert filetime is not None
    try:
        output, error = process.communicate(timeout=30)
        assert process.returncode == 0, error.decode("utf-8", "replace")
        evidence = json.loads(output)
        print("WINDOW_TREE_EVIDENCE " + json.dumps({"hidden": hidden,
              "launchPid": process.pid, "launchFiletime": str(filetime), "tree": evidence}))
        for depth in (2, 1, 0):
            assert evidence["depth"] == depth
            assert evidence["filetime"].isdigit()
            assert bool(evidence["consoleHwnd"]) is (not hidden)
            assert evidence["visible"] is (not hidden)
            if depth:
                assert evidence["childLaunch"]["exitCode"] == 0
                evidence = evidence["child"]
    finally:
        if process.poll() is None:
            kernel = kernel32()
            assert kernel.GetProcessId(wintypes.HANDLE(handle)) == process.pid
            assert read_creation_filetime(handle) == filetime
            assert kernel.TerminateProcess(wintypes.HANDLE(handle), 1)
            process.wait(timeout=5)


@pytest.mark.parametrize("relative", ["codex/wrapper.py", "codex/app_server_wrapper.py",
                                    "kimi/wrapper.py", "opencode/wrapper.py"])
def test_bridge_filename_entrypoint_imports_policy_outside_repo(tmp_path, relative):
    repo = Path(__file__).resolve().parents[1]
    script = repo / "packages" / "core" / "adapters" / relative
    env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path,
                            env=env, capture_output=True, timeout=10,
                            **command_window_options())
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert b"usage:" in result.stdout


def test_isolated_entrypoint_preserves_output_and_exit_code(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    program = "import sys; print('owned stdout', flush=True); print('owned stderr', file=sys.stderr); sys.exit(7)"
    result = subprocess.run([sys.executable, str(repo / "scripts/run_tests_isolated.py"),
                             "--", sys.executable, "-c", program], cwd=tmp_path,
                            capture_output=True, timeout=10, **command_window_options())
    assert result.returncode == 7
    assert b"owned stdout" in result.stdout
    assert b"owned stderr" in result.stderr


def test_kimi_wrapper_help_preserves_unicode_with_cp1252_stdout(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    script = repo / "packages" / "core" / "adapters" / "kimi" / "wrapper.py"
    env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    env.update(PYTHONUTF8="0", PYTHONIOENCODING="cp1252")
    result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path,
                            env=env, capture_output=True, timeout=10,
                            **command_window_options())
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    help_text = result.stdout.decode("utf-8")
    assert "usage:" in help_text
    assert "worker 注入的系统提示词（首轮转 --agent-file）" in help_text
