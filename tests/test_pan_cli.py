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



def test_user_registration_path_readback_and_missing_entry(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows user PATH")
    registration = registration_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/pan_cli.py").write_text("# entry")
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
    monkeypatch.setattr(srv.launcher, "process_create_time", lambda pid: 12)
    messages = []

    async def next_call(scope, receive, send):
        pytest.fail("must not schedule a lifecycle Job on a replacement instance")

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/main/exit",
             "headers": [(b"x-pan-expected-pid", str(os.getpid()).encode()),
                         (b"x-pan-expected-created-at", b"11")]}
    asyncio.run(srv.CLIInstanceGuardMiddleware(next_call)(scope, None, send))
    assert messages[0]["status"] == 409



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

@pytest.fixture
def isolated_registration(tmp_path, monkeypatch):
    registration = registration_module()
    stored = [str(tmp_path / "existing")]
    def storage(value=None):
        if value is not None:
            stored[0] = value
        return stored[0]
    monkeypatch.setattr(registration, "user_path", storage)
    monkeypatch.setattr(registration, "notify_environment", lambda: None)
    monkeypatch.setattr(registration.subprocess, "check_output", lambda *a, **kw: pytest.fail("Git must never be invoked"))
    root = tmp_path / "任意分支 no git"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/pan_cli.py").write_text("# entry")
    return registration, stored, root, tmp_path / "用户 CLI bin"


@pytest.mark.skipif(os.name != "nt", reason="Windows automatic registration")
def test_auto_no_git_idempotent_checkout_conflict_and_uninstall(isolated_registration, capsys):
    reg, stored, root, directory = isolated_registration
    before = stored[0]
    assert reg.auto_register(root, Path(sys.executable), directory=directory, search_path="") == 0
    snapshot = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert reg.auto_register(root, Path(sys.executable), directory=directory, search_path="") == 0
    assert stored[0] == before + os.pathsep + str(directory)
    other = root.parent / "other checkout"
    (other / "scripts").mkdir(parents=True)
    (other / "scripts/pan_cli.py").write_text("# entry")
    assert reg.auto_register(other, Path(sys.executable), directory=directory, search_path="") == 0
    assert "another checkout" in capsys.readouterr().err
    assert snapshot == {p.name: p.read_bytes() for p in directory.iterdir()}
    reg.unregister(directory, change_path=True)
    assert stored[0] == before
    assert not list(directory.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Windows automatic registration")
def test_auto_permission_failure_rolls_back_and_allows_start(isolated_registration, monkeypatch, capsys):
    reg, stored, root, directory = isolated_registration
    before = stored[0]
    def denied(value=None):
        if value is not None:
            raise PermissionError("HKCU denied")
        return before
    monkeypatch.setattr(reg, "user_path", denied)
    assert reg.auto_register(root, Path(sys.executable), directory=directory, search_path="") == 0
    assert "HKCU denied" in capsys.readouterr().err
    assert stored[0] == before
    assert not list(directory.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Windows automatic registration")
def test_auto_checks_persistent_path_and_modified_shims(isolated_registration, capsys):
    reg, stored, root, directory = isolated_registration
    foreign = root.parent / "foreign command"
    foreign.mkdir()
    (foreign / "pan.exe").write_bytes(b"unrelated")
    stored[0] = str(foreign)
    assert reg.auto_register(root, Path(sys.executable), directory=directory, search_path="") == 0
    assert "Unrelated" in capsys.readouterr().err
    assert not directory.exists()
    stored[0] = ""
    reg.auto_register(root, Path(sys.executable), directory=directory, search_path="")
    (directory / "pan.ps1").write_bytes(b"edited")
    reg.auto_register(root, Path(sys.executable), directory=directory, search_path="")
    assert "conflict" in capsys.readouterr().err
    with pytest.raises(ValueError, match="conflict"):
        reg.unregister(directory, change_path=True)
    assert (directory / "pan.ps1").read_bytes() == b"edited"
    assert stored[0] == str(directory)


@pytest.mark.skipif(os.name != "nt", reason="Windows user PATH")
def test_uninstall_retains_preexisting_path(isolated_registration):
    reg, stored, root, directory = isolated_registration
    stored[0] = str(directory)
    reg.register(directory, root, Path(sys.executable), change_path=True, search_path="")
    reg.unregister(directory, change_path=True)
    assert stored[0] == str(directory)


def test_registration_import_has_no_side_effects(monkeypatch):
    monkeypatch.setattr(Path, "mkdir", lambda *a, **kw: pytest.fail("import must not create shims"))
    registration_module()

@pytest.mark.skipif(os.name != "nt", reason="Windows batch entry")
@pytest.mark.parametrize("deny", [False, True])
def test_start_batch_registration_then_launcher_without_service(tmp_path, deny):
    import subprocess
    import shutil
    repo = Path(__file__).parents[1]
    root = tmp_path / "安装 Pan"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(repo / "scripts/start_pan.bat", scripts / "start_pan.bat")
    (scripts / "pan_cli.py").write_text("# entry")
    core = root / "packages/core"
    core.mkdir(parents=True)
    (root / "packages/__init__.py").write_text("")
    (core / "__init__.py").write_text("")
    # A fixture launcher proves batch continuation without opening any port.
    (core / "launcher.py").write_text(
        "from pathlib import Path\nPath('launcher-reached').write_text('yes')\nraise SystemExit(7)\n")
    wrapper = f'''import runpy
from pathlib import Path
import sys
reg = runpy.run_path({str(repo / 'scripts/register_pan_cli.py')!r})
state = Path({str(tmp_path / 'fake-user-path')!r})
def storage(value=None):
    if value is not None:
        if {deny!r}:
            raise PermissionError('isolated HKCU denied')
        state.write_text(value, encoding='utf-8')
    return state.read_text(encoding='utf-8') if state.exists() else ''
fn = reg['auto_register']
fn.__globals__['user_path'] = storage
fn.__globals__['notify_environment'] = lambda: None
raise SystemExit(fn(Path(sys.argv[-1]), Path(sys.executable), search_path=''))
'''
    (scripts / "register_pan_cli.py").write_text(wrapper, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_") and k.upper() != "PYTHONPATH"}
    env["LOCALAPPDATA"] = str(tmp_path / "隔离 App Data")
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(scripts / "start_pan.bat")],
                            cwd=tmp_path, env=env, capture_output=True, timeout=20)
    assert result.returncode == 7, result.stdout + result.stderr
    assert (root / "launcher-reached").read_text() == "yes"
    manifest = Path(env["LOCALAPPDATA"]) / "Pan/bin" / "pan-cli-registration.json"
    assert manifest.exists() is not deny
    if deny:
        assert b"isolated HKCU denied" in result.stderr
    else:
        assert str(root) == json.loads(manifest.read_text(encoding="utf-8"))["root"]

@pytest.mark.skipif(os.name != "nt", reason="Windows user PATH")
def test_uninstall_permission_failure_retains_owned_files(isolated_registration, monkeypatch):
    reg, stored, root, directory = isolated_registration
    reg.register(directory, root, Path(sys.executable), change_path=True, search_path="")
    snapshot = {p.name: p.read_bytes() for p in directory.iterdir()}
    def denied(value=None):
        if value is not None:
            raise PermissionError("HKCU denied")
        return stored[0]
    monkeypatch.setattr(reg, "user_path", denied)
    with pytest.raises(PermissionError, match="denied"):
        reg.unregister(directory, change_path=True)
    assert snapshot == {p.name: p.read_bytes() for p in directory.iterdir()}


@pytest.mark.skipif(os.name != "nt", reason="Windows automatic registration")
def test_auto_directory_permission_failure(isolated_registration, monkeypatch, capsys):
    reg, stored, root, directory = isolated_registration
    before = stored[0]
    original = Path.mkdir
    def denied(path, *args, **kwargs):
        if path == directory:
            raise PermissionError("shim directory denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", denied)
    assert reg.auto_register(root, Path(sys.executable), directory=directory, search_path="") == 0
    assert "shim directory denied" in capsys.readouterr().err
    assert stored[0] == before
    assert not directory.exists()
