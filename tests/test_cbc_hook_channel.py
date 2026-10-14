"""Zero-provider probes of the production command hook and loopback handler."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from packages.core.adapters.cbc import native_bridge, native_hook


@pytest.mark.parametrize("raw", ["{", "[]"])
def test_invalid_input_blocks_without_traceback_or_private_data(monkeypatch, capsys, raw):
    monkeypatch.setattr(native_hook.sys, "stdin", io.StringIO(raw))
    assert native_hook.main() == 2
    assert capsys.readouterr().err == "CBC control channel unavailable: invalid hook input\n"


def test_missing_environment_blocks_submit(monkeypatch, capsys):
    monkeypatch.delenv("PAN_CBC_HOOK_URL", raising=False)
    monkeypatch.delenv("PAN_CBC_HOOK_TOKEN", raising=False)
    monkeypatch.setattr(native_hook.sys, "stdin", io.StringIO('{"hook_event_name":"UserPromptSubmit"}'))
    assert native_hook.main() == 2
    assert "missing bridge environment" in capsys.readouterr().err


@pytest.mark.parametrize("error,reason", [
    (TimeoutError("private-capability"), "timed out"),
    (ValueError("private-capability"), "invalid bridge request or response"),
    (native_hook.urllib.error.URLError(ConnectionRefusedError("private-capability")), "loopback connection failed"),
])
@pytest.mark.parametrize("name,exit_code", [("UserPromptSubmit", 2), ("PostToolUse", 1)])
def test_transport_errors_are_sanitized_and_submit_stays_closed(monkeypatch, capsys, error, reason, name, exit_code):
    monkeypatch.setenv("PAN_CBC_HOOK_URL", "http://127.0.0.1:1/hook")
    monkeypatch.setenv("PAN_CBC_HOOK_TOKEN", "private-capability")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(native_hook.urllib.request, "build_opener", fail)
    monkeypatch.setattr(native_hook.sys, "stdin", io.StringIO(json.dumps({"hook_event_name": name})))
    assert native_hook.main() == exit_code
    diagnostic = capsys.readouterr().err
    assert reason in diagnostic and "private-capability" not in diagnostic


def hook_process(env, event, command=None):
    return subprocess.run(command or [sys.executable, str(Path(native_hook.__file__))],
        shell=command is not None, input=json.dumps(event), text=True,
        capture_output=True, env=env, timeout=15)


def isolated_env(url, token):
    # No user profile, provider credential or inherited proxy configuration.
    env = {key: os.environ[key] for key in ("SystemRoot", "PATH", "TEMP", "TMP") if key in os.environ}
    env.update(PAN_CBC_HOOK_URL=url, PAN_CBC_HOOK_TOKEN=token,
        HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1", ALL_PROXY="http://127.0.0.1:1")
    return env


def test_real_hook_http_lifecycle_and_rejections(monkeypatch):
    emitted, writes = [], []
    backend = SimpleNamespace(write=lambda data: writes.append(data) or len(data))
    bridge = native_bridge.NativeBridge(backend, emitted.append, "dedicated")
    token = "private-test-capability"
    server = ThreadingHTTPServer(("127.0.0.1", 0), native_bridge.hook_handler(lambda: bridge, token))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    env = isolated_env(f"http://127.0.0.1:{server.server_port}/hook", token)
    assert server.server_port not in (8767, 8768)
    def send(name, **kwargs):
        return hook_process(env, {"hook_event_name": name, "session_id": "dedicated", "generation_id": "run-1", **kwargs})
    try:
        assert send("SessionStart").returncode == 0
        for overrides, code in [({"session_id": "other"}, "wrong_session"),
                                ({"generation_id": None}, "stale_turn"), ({}, "missing_owner")]:
            result = send("UserPromptSubmit", prompt="private-prompt", **overrides)
            assert result.returncode == 2 and code in result.stderr
            assert token not in result.stderr and "private-prompt" not in result.stderr
        assert not writes and bridge.turn is None
        monkeypatch.setattr(native_bridge.time, "sleep", lambda _: None)
        bridge.submit("managed")
        result = send("UserPromptSubmit", prompt="different")
        assert result.returncode == 2 and "prompt_mismatch" in result.stderr
        assert bridge.pending_prompt == "managed" and bridge.turn is None
        assert send("UserPromptSubmit", prompt="managed").returncode == 0
        bridge.control({"type": "control_mode", "mode": "native", "generation": 0})
        bridge.control({"type": "control_mode", "mode": "pan", "generation": 1})
        assert bridge.turn == "run-1" and len(writes) == 2
        assert send("Stop", last_assistant_message="done").returncode == 0
        assert not any(e["type"] == "result" for e in emitted)
        assert send("FinalStop", final_stop_reason="completed").returncode == 0
        assert send("FinalStop", final_stop_reason="completed").returncode == 0
        assert len([e for e in emitted if e["type"] == "result"]) == 1
        assert send("UserPromptSubmit", prompt="managed").returncode == 2
        wrong_env = dict(env, PAN_CBC_HOOK_TOKEN="wrong")
        result = hook_process(wrong_env, {"hook_event_name": "UserPromptSubmit"})
        assert result.returncode == 2 and "authentication rejected" in result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
    result = send("UserPromptSubmit", prompt="managed")
    assert result.returncode == 2 and "loopback connection failed" in result.stderr


def test_generated_settings_command_environment_and_retirement(monkeypatch, tmp_path, capsys):
    from packages.core.terminal.backend import ConPtyBackend
    ready = threading.Event()
    results, settings_paths, environments = [], [], []
    class Input:
        def __iter__(self):
            assert ready.wait(5)
            return iter(())
    def spawn(argv, **kwargs):
        settings_path = Path(argv[argv.index("--settings") + 1])
        settings_paths.append(settings_path)
        settings = json.loads(settings_path.read_text())
        assert settings["alwaysThinkingEnabled"] is False
        assert settings["autoUpdates"] is False
        env = isolated_env(kwargs["env"]["PAN_CBC_HOOK_URL"], kwargs["env"]["PAN_CBC_HOOK_TOKEN"])
        environments.append(env)
        command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        def probe():
            try:
                results.append(hook_process(env, {"hook_event_name": "SessionStart", "session_id": "dedicated"}, command))
            finally:
                ready.set()
        threading.Thread(target=probe).start()
        return SimpleNamespace(read=lambda *args, **kwargs: b"", alive=lambda: True,
            terminate=lambda **kwargs: None, close=lambda: None)
    monkeypatch.setattr(ConPtyBackend, "spawn", spawn)
    monkeypatch.setattr(native_bridge.sys, "stdin", Input())
    for _ in range(2):
        ready.clear()
        native_bridge.run(["fixture-only", "--settings", '{"alwaysThinkingEnabled":false,"autoUpdates":false}'], str(tmp_path), "dedicated")
    assert len(results) == 2 and all(r.returncode == 0 for r in results)
    assert not any(p.exists() for p in settings_paths)
    assert environments[0]["PAN_CBC_HOOK_TOKEN"] != environments[1]["PAN_CBC_HOOK_TOKEN"]
    for env in environments:
        result = hook_process(env, {"hook_event_name": "UserPromptSubmit"})
        assert result.returncode == 2 and "loopback connection failed" in result.stderr
    assert "PAN_CBC_HOOK_TOKEN" not in capsys.readouterr().out


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ConPTY process/environment probe")
def test_owned_conpty_cli_fixture_receives_settings_and_hook_environment(monkeypatch, tmp_path, capsys):
    """Real Windows spawn -> generated CLI config -> command -> hook HTTP."""
    from packages.core.terminal.backend import ConPtyBackend
    import psutil
    marker = tmp_path / "receipt.json"
    fixture = tmp_path / "fixture.py"
    fixture.write_text('''
import json, os, pathlib, subprocess, sys, time
settings = json.loads(pathlib.Path(sys.argv[sys.argv.index("--settings") + 1]).read_text())
results = []
for name in ("SessionStart", "UserPromptSubmit"):
    command = settings["hooks"][name][0]["hooks"][0]["command"]
    event = {"hook_event_name": name, "session_id": "dedicated", "generation_id": "run-1", "prompt": "fixture"}
    result = subprocess.run(command, shell=True, input=json.dumps(event), text=True, capture_output=True, timeout=15)
    results.append({"exit": result.returncode, "stderr": result.stderr})
pathlib.Path(sys.argv[1]).write_text(json.dumps(results))
time.sleep(30)
''', encoding="utf-8")
    actual_spawn = ConPtyBackend.spawn
    retained = []
    def spawn(*args, **kwargs):
        backend = actual_spawn(*args, **kwargs)
        retained.append(backend)
        return backend
    class Input:
        def __iter__(self):
            deadline = time.monotonic() + 10
            while not marker.exists():
                assert time.monotonic() < deadline, "owned CLI hook receipt absent"
                time.sleep(.02)
            return iter(())
    monkeypatch.setattr(ConPtyBackend, "spawn", spawn)
    monkeypatch.setattr(native_bridge.sys, "stdin", Input())
    # Clear inherited provider environment for this inert Python CLI fixture.
    retained_env = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}}
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in retained_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CODEBUDDY_CONFIG_DIR", str(tmp_path / "profile"))
    try:
        native_bridge.run([sys.executable, str(fixture), str(marker)], str(tmp_path), "dedicated")
        results = json.loads(marker.read_text())
        assert results[0] == {"exit": 0, "stderr": ""}
        assert results[1]["exit"] == 2 and "missing_owner" in results[1]["stderr"]
        assert len(retained) == 1 and not retained[0].alive()
        assert not psutil.pid_exists(retained[0].pid)
        assert not retained[0].guard.handle_open
        assert "PAN_CBC_HOOK_TOKEN" not in capsys.readouterr().out
    finally:
        for backend in retained:
            backend.guard.close()
            backend.close()
