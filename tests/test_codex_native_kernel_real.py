"""Optional live CLI check with private home and a synthetic local provider."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ConPTY proof")
def test_real_wrapper_mode_round_trip_does_not_exit_kernel(tmp_path):
    from packages.core.terminal.backend import ConPtyBackend
    from packages.core.adapters.codex.adapter import _resolve_codex_node, _resolve_codex_js
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("native_probe_helper", root / "audit/terminal/codex/probe_codex_remote_tui.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    home = tmp_path / "codex-home"
    home.mkdir()
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    helper.seed_placeholder_credentials(str(home), [])
    provider = helper.Blackhole()
    backend = None
    try:
        options = ["-c", 'model_provider="probe_bh"', "-c", 'model="probe-model"', "-c",
            f'model_providers.probe_bh={{name="probe_bh",base_url="{provider.base_url}",wire_api="responses"}}']
        env = dict(os.environ, CODEX_HOME=str(home))
        argv = [sys.executable, "-u", str(root / "packages/core/adapters/codex/wrapper.py"),
            "--app-server", "--native-control", "--node-path", _resolve_codex_node(),
            "--codex-path", _resolve_codex_js(), "--codex-extra-args", json.dumps(options)]
        backend = ConPtyBackend.spawn(argv, cwd=str(cwd), env=env, rows=40, cols=240)
        identity = backend.identity
        buffered = b""
        events = []
        def wait_for(predicate, seconds=15):
            nonlocal buffered
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                buffered += backend.read(65536, timeout=.1)
                while b"\n" in buffered:
                    line, buffered = buffered.split(b"\n", 1)
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(event, dict):
                        events.append(event)
                found = next((event for event in events if predicate(event)), None)
                if found is not None:
                    return found
                assert not backend.wait_dead(0), "Wrapper exited before expected event"
            raise AssertionError(f"Expected native wrapper event missing; types={[e.get('type') for e in events]}")
        init = wait_for(lambda e: e.get("type") == "thread.started")
        assert init["thread_id"]
        backend.write(b'{"text":"LOCAL SYNTHETIC PENDING TURN"}\r')
        wait_for(lambda e: e.get("type") == "codex.thread_status" and e.get("native_status", {}).get("type") == "active")
        for request, mode, generation in [("n1", "native", 0), ("p1", "pan", 1), ("n2", "native", 2), ("p2", "pan", 3)]:
            backend.write((json.dumps({"type": "control_mode", "requestId": request, "mode": mode, "generation": generation}) + "\r").encode())
            receipt = wait_for(lambda e: e.get("type") == "pan.control_mode" and e.get("requestId") == request)
            assert receipt["ok"] is True and receipt["mode"] == mode
            assert receipt["generation"] == generation + 1
            assert backend.identity == identity and not backend.wait_dead(0)
        assert not any(e.get("type") == "result" for e in events)
    finally:
        if backend is not None:
            backend.guard.terminate_tree(backend.pid, timeout=3)
            assert backend.wait_dead(3)
            assert backend.close()["closed"] is True
        provider.close()
