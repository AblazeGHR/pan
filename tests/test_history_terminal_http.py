"""Authenticated owned HTTP/WS/Chromium acceptance of canonical terminals."""
import json
import os
from pathlib import Path
import subprocess

from tests import test_real_queue_http_e2e as base
from packages.jobs.window import command_window_options


def test_history_terminal_production_browser(tmp_path, monkeypatch):
    assert base.PORT not in {8767, 8768}, "Use scripts/run_tests_isolated.py"
    monkeypatch.setattr(base, "FAKE_CLI", Path(__file__).parent / "support/history_terminal_fake_cli.py")
    monkeypatch.setattr(base, "SERVER_LAUNCHER", Path(__file__).parent / "support/automation_http_server.py")
    rt = base._PanRuntime(tmp_path / "owned-history-profile")
    rt.data_root.mkdir()
    (rt.data_root / "config.json").write_text(json.dumps({"plugin_manifests": [],
        "scheduler": {"enabled": False}, "memory": {"enabled": False}}), encoding="utf-8")
    out = Path("artifacts/history-terminal/http").resolve()
    out.mkdir(parents=True, exist_ok=True)
    evidence = {"port": base.PORT, "ownedProfile": str(rt.data_root)}
    try:
        rt.start()
        env = {**os.environ, "PAN_TEST_HISTORY_KEY": (
            rt.data_root / "automation/user.key").read_text(encoding="utf-8")}
        result = subprocess.run(["node", "packages/web/e2e/history-terminal-live.mjs", rt.base_url, str(out)],
            cwd=base.ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=150,
            **command_window_options())
        (out / "browser.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        evidence["browserExit"] = result.returncode
        assert result.returncode == 0, result.stdout + result.stderr
        received = [row["text"] for row in rt.fake_records() if row.get("kind") == "received"]
        assert received == ["err-capacity-report", "later-success-body", "history-terminal-long"]
        evidence["providerTextOnly"] = True
        rt.stop()
        # Reopen the actual JSONL, with no live Worker or runtime marker.
        rt.start()
        key = (rt.data_root / "automation/user.key").read_text(encoding="utf-8")
        token = rt.request("POST", "/api/automation/login", json={"key": key})["token"]
        browser = json.loads((out / "browser.json").read_text(encoding="utf-8"))
        history = rt.request("GET", f"/api/sessions/{browser['sessionId']}/history?limit=500",
                             headers={"Authorization": "Bearer " + token})["history"]
        assert [row["messageId"] for row in history if row.get("kind") == "terminal"] == browser["terminalIds"]
        evidence["coldReopen"] = True
    finally:
        evidence["ownedProcesses"] = list(rt._owned_processes)
        rt.stop()
        evidence.update(portReleased=base._port_is_free(base.PORT))
        (out / "runtime.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    assert evidence["portReleased"]
