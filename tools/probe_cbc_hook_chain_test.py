"""Explicit local integration probe; requires installed CBC and its Git Bash.

Run separately from the portable regression suite. No CLI bootstrap/provider.
"""
import json
import subprocess
import sys
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from packages.core.adapters.cbc import native_bridge, native_hook
from test_cbc_hook_channel import isolated_env

@pytest.mark.parametrize("prompt", ["continue", "继续", "修复 🧪 café"])
@pytest.mark.parametrize("mode", ["pan", "native"])
def test_installed_command_executor_utf8_receipt_crosses_real_http(monkeypatch, prompt, mode, record_property):
    """CBC JSON.stringify -> Git Bash -> locale-GBK Python -> owning bridge."""
    import shutil
    root = Path(__file__).resolve().parents[1]
    bundle = Path("D:/node_npm/node_global/node_modules/@tencent-ai/codebuddy-code/dist/codebuddy.js")
    shell = Path("E:/Git/bin/bash.exe")
    assert bundle.is_file() and shell.is_file(), "installed CBC and actual hook shell required"
    emitted = []
    bridge = native_bridge.NativeBridge(SimpleNamespace(write=lambda data: len(data)), emitted.append, "dedicated")
    monkeypatch.setattr(native_bridge.time, "sleep", lambda _: None)
    bridge.hook({"hook_event_name": "SessionStart", "session_id": "dedicated"})
    if mode == "pan":
        bridge.submit(prompt)
    else:
        bridge.control({"type": "control_mode", "mode": "native", "generation": 0})
        bridge.control({"type": "native_input", "text": prompt + "\r", "generation": 1})
    server = ThreadingHTTPServer(("127.0.0.1", 0), native_bridge.hook_handler(lambda: bridge, "fixture-capability"))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    env = isolated_env(f"http://127.0.0.1:{server.server_port}/hook", "fixture-capability")
    # Reproduce Windows Python's real pipe locale even on UTF-8 developer hosts.
    env.update(PYTHONIOENCODING="gbk", PYTHONUTF8="0")
    payload = {"command": subprocess.list2cmdline([sys.executable.replace("\\", "/"), Path(native_hook.__file__).as_posix()]),
               "shell": str(shell), "environment": env,
               "event": {"hook_event_name": "UserPromptSubmit", "session_id": "dedicated", "prompt": prompt,
                         "session": {"conversationRequestId": "fixture-run", "requestOptions": {"model": "fixture-only"}}}}
    try:
        result = subprocess.run([shutil.which("node"), str(root / "tools/probe_cbc_command_hook.mjs"), str(bundle)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"), capture_output=True, env=env, timeout=15)
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        receipt = json.loads(result.stdout)
        record_property("bundleSHA256", receipt["bundleSHA256"])
        record_property("loopback_port", server.server_port)
        record_property("hook_pid", receipt["hookPid"])
        record_property("hook_retired_exit", receipt["retiredExitCode"])
        record_property("owner_mode", mode)
        record_property("owner_generation", 1 if mode == "native" else 0)
        assert receipt["exitCode"] == 0 and receipt["allowed"] is True, receipt
        assert receipt["retiredExitCode"] == 0
        assert bridge.turn == "fixture-run" and bridge.pending_prompt is None
        assert not bridge.input_uncertain
        assert bridge.owner.snapshot() == {"mode": mode, "generation": 1 if mode == "native" else 0}
        native_users = [e["text"] for e in emitted if e["type"] == "pan.native_user"]
        assert native_users == ([prompt] if mode == "native" else [])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
