import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import types

import pytest

from packages.core import cli, launcher
from packages.core.host_console import HostConsole, ConsoleError, Output


def registration_module():
    spec = importlib.util.spec_from_file_location("register_pan_cli", Path(__file__).parents[1] / "scripts/register_pan_cli.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registration_unicode_spaces_unrelated_conflict_and_exact_uninstall(tmp_path):
    registration = registration_module()
    root = tmp_path / "安装 Pan"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/pan_cli.py").write_text("# entry")
    directory = tmp_path / "命令 bin"
    manifest = registration.register(directory, root, Path(sys.executable), search_path="")
    assert registration.register(directory, root, Path(sys.executable), search_path="") == manifest
    assert str(root) in (directory / "pan.ps1").read_text(encoding="utf-8-sig")
    (directory / "pan.cmd").write_bytes(b"foreign")
    with pytest.raises(ValueError, match="conflict"):
        registration.unregister(directory)
    (directory / "pan.cmd").write_bytes(registration.render(root, sys.executable)["pan.cmd"])
    registration.unregister(directory)
    assert list(directory.iterdir()) == []
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "pan.cmd").write_bytes(b"sentinel")
    with pytest.raises(ValueError, match="Unrelated"):
        registration.register(directory, root, Path(sys.executable), search_path=str(foreign))
    assert (foreign / "pan.cmd").read_bytes() == b"sentinel"
    (directory / "pan.ps1").write_bytes(b"unowned")
    with pytest.raises(ValueError, match="ownership"):
        registration.register(directory, root, Path(sys.executable), search_path="")


def test_registration_user_path_refuses_task_branch_before_mutation(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows user PATH")
    registration = registration_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/pan_cli.py").write_text("# entry")
    monkeypatch.setattr(registration.subprocess, "check_output", lambda *a, **kw: "feature/task")
    monkeypatch.setattr(registration, "user_path", lambda *a: pytest.fail("must not access real user PATH"))
    with pytest.raises(ValueError, match="practical"):
        registration.register(tmp_path / "bin", tmp_path, Path(sys.executable), change_path=True)


def test_user_registration_path_readback_and_missing_entry(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows user PATH")
    registration = registration_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/pan_cli.py").write_text("# entry")
    monkeypatch.setattr(registration.subprocess, "check_output", lambda *a, **kw: "practical")
    stored = [str(tmp_path / "existing bin")]
    def fake_path(value=None):
        if value is not None:
            stored[0] = value
        return stored[0]
    monkeypatch.setattr(registration, "user_path", fake_path)
    directory = tmp_path / "Pan bin"
    before = stored[0]
    registration.register(directory, tmp_path, Path(sys.executable), change_path=True, search_path="")
    assert stored[0] == before + os.pathsep + str(directory)
    registration.register(directory, tmp_path, Path(sys.executable), change_path=True, search_path="")
    stored[0] = before
    with pytest.raises(ValueError, match="missing"):
        registration.register(directory, tmp_path, Path(sys.executable), change_path=True, search_path="")
    stored[0] += os.pathsep + str(directory)
    registration.unregister(directory, change_path=True)
    assert stored[0] == before


def test_cli_all_commands_and_help(capsys):
    for command in ("start", "exit", "restart", "debug", "status", "doctor", "logs", "show console", "hide console"):
        assert cli.parser().parse_args(command.split()).command == command.split()[0]
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "Pan " in capsys.readouterr().out


def test_status_never_calls_stale_pid_running(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "load_state", lambda root: {"main": {"pid": 5, "createdAt": 1}, "port": 8765})
    monkeypatch.setattr(launcher, "configured_port", lambda *a, **kw: 8765)
    monkeypatch.setattr(launcher, "service_process_identity", lambda *a: {"ok": False})
    monkeypatch.setattr(launcher, "process_alive", lambda *a: False)
    monkeypatch.setattr(launcher, "listener_owner", lambda *a: 5)
    monkeypatch.setattr(cli, "request", lambda *a: pytest.fail("must not request an unverified listener"))
    assert cli.status(tmp_path)["state"] == "unknown"
    monkeypatch.setattr(launcher, "listener_owner", lambda *a: None)
    result = cli.status(tmp_path)
    assert result["state"] == "stopped"
    assert result["runningVersion"] is None


def test_http_wrong_instance_blocks_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "configured_port", lambda *a, **kw: 8765)
    monkeypatch.setattr(launcher, "load_state", lambda root: {"port": 8765, "main": {"pid": 5, "createdAt": 1}})
    monkeypatch.setattr(launcher, "service_process_identity", lambda *a: {"ok": True})
    monkeypatch.setattr(launcher, "listener_owner", lambda *a: 5)
    monkeypatch.setattr(cli, "request", lambda *a: {"pid": 5, "createdAt": 1, "root": str(tmp_path / "other")})
    with pytest.raises(launcher.LauncherError, match="identity mismatch"):
        cli.lifecycle(tmp_path, "exit", 1)


@pytest.mark.parametrize("phase,error", [("failed", None), ("offline", "worker failed")])
def test_lifecycle_does_not_promote_failed_job(tmp_path, monkeypatch, phase, error):
    monkeypatch.setattr(cli, "verified", lambda root: (8765, {"pid": 5, "createdAt": 1}, {}))
    monkeypatch.setattr(cli, "request", lambda *a: {"ok": True, "jobId": "job_test", "requestId": "request"})
    path = tmp_path / "data/background_jobs/jobs/job_test.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"operation": "exit", "root": str(tmp_path), "port": 8765,
                               "oldPid": 5, "oldPidCreatedAt": 1, "requestId": "request",
                               "phase": phase, "error": error}))
    with pytest.raises(launcher.LauncherError, match="failed"):
        cli.lifecycle(tmp_path, "exit", 1)


def test_start_lock_refuses_second_start(tmp_path):
    with cli.start_lock(tmp_path):
        with pytest.raises(launcher.LauncherError, match="in progress"):
            with cli.start_lock(tmp_path):
                pytest.fail("second start must not enter")


def test_logs_tail_and_read_only_doctor(tmp_path, monkeypatch, capsys):
    logs = tmp_path / "data/logs/pan.log"
    logs.parent.mkdir(parents=True)
    logs.write_text("first\n中文\nlast\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("# entry")
    (tmp_path / "config.json").write_text(json.dumps({"python": sys.executable, "port": 8765}))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    cli.logs(tmp_path, lines=2)
    output = capsys.readouterr().out
    assert "first" not in output and "中文\nlast\n" in output
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **kw: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(launcher, "listener_owner", lambda *a: None)
    assert cli.doctor(tmp_path)["ok"] is True
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_console_output_survives_broken_console():
    import io
    import threading
    durable = io.StringIO()
    manager = types.SimpleNamespace(lock=threading.RLock(), output=types.SimpleNamespace(
        write=lambda v: (_ for _ in ()).throw(OSError("closed"))))
    Output(manager, durable).write("still logged")
    assert durable.getvalue() == "still logged"


def test_console_refuses_unmanaged_and_shared(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows console")
    host = HostConsole()
    host.managed = False
    with pytest.raises(ConsoleError, match="unmanaged"):
        host.change("hide")
    host.managed = True
    host.kernel = types.SimpleNamespace(GetConsoleWindow=lambda: 123)
    monkeypatch.setattr(host, "_exclusive", lambda: False)
    with pytest.raises(ConsoleError, match="shared"):
        host.change("hide")


def test_console_api_requires_exact_instance_identity(monkeypatch):
    import packages.web.server as srv
    from fastapi import HTTPException
    monkeypatch.setattr(srv.launcher, "process_create_time", lambda pid: 12)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(srv.api_main_console({"action": "hide", "pid": os.getpid(), "createdAt": 11}))
    assert exc.value.status_code == 409


def test_lifecycle_http_guard_refuses_replaced_instance(monkeypatch):
    import packages.web.server as srv
    from starlette.requests import Request
    monkeypatch.setattr(srv.launcher, "process_create_time", lambda pid: 12)
    async def next_call(request):
        pytest.fail("must not schedule a lifecycle Job on a replacement instance")
    request = Request({"type": "http", "method": "POST", "path": "/api/main/exit",
                       "headers": [(b"x-pan-expected-pid", str(os.getpid()).encode()),
                                   (b"x-pan-expected-created-at", b"11")]})
    result = asyncio.run(srv.cli_instance_guard(request, next_call))
    assert result.status_code == 409


def test_follow_reads_rotation_and_exits_without_service_action(tmp_path, monkeypatch, capsys):
    path = tmp_path / "data/logs/pan.log"
    path.parent.mkdir(parents=True)
    path.write_text("old\n", encoding="utf-8")
    ticks = []
    def advance(_):
        ticks.append(1)
        if len(ticks) == 1:
            path.rename(path.with_suffix(".log.1"))
            path.write_text("rotated 中文\n", encoding="utf-8")
        else:
            raise KeyboardInterrupt
    monkeypatch.setattr(cli.time, "sleep", advance)
    monkeypatch.setattr(cli, "request", lambda *a, **kw: pytest.fail("log follow must not control the service"))
    with pytest.raises(KeyboardInterrupt):
        cli.logs(tmp_path, follow=True)
    assert "rotated 中文" in capsys.readouterr().out


def test_non_windows_console_path_does_not_use_native_apis(monkeypatch):
    import packages.core.host_console as module
    import packages.core.console_exit as exit_module
    monkeypatch.setattr(module, "os", types.SimpleNamespace(name="posix", environ={}))
    host = module.HostConsole()
    host.initialize()
    assert host.snapshot() == {"state": "unsupported", "managed": False}
    sentinel = object()
    monkeypatch.setattr(exit_module, "start_console_exit", lambda port, strategy: sentinel)
    assert host.start_input(8765, lambda: "offline") is sentinel
