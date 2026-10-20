"""Real isolated Chromium check for Behavior → Message auto force send.

The React production build runs against a real owned FastAPI process on a
dynamic non-production port. No practical service, 8767/8768 listener, real
provider, or user data is used. The script verifies real PUT persistence,
a real page reload, and the in-browser save-failure alert.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.test_auto_force_http_e2e import _Runtime

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "packages" / "web"
SCRIPT = WEB / "e2e" / "auto-force-settings.e2e.mjs"


@pytest.mark.timeout(180)
def test_auto_force_settings_ui_real_chromium(tmp_path):
    runtime = _Runtime(tmp_path / "ui-data", steer=True)
    runtime.gates = {
        "hold-ui": runtime.data_root / "release-hold-ui",
    }
    runtime.start()
    try:
        def create(name: str) -> dict:
            return runtime.request("POST", "/api/sessions", json={
                "name": name,
                "adapter": "cbc",
                "model": "deepseek-v4-flash",
                "permissionMode": "bypassPermissions",
                "outputMode": "stream",
            })

        ma = create("ui-ma")
        target = create("ui-target")
        runtime.request("POST", "/api/claim", json={
            "managerId": ma["id"], "sessionId": target["id"]})
        runtime.request("POST", "/api/spawn", json={"sessionId": target["id"]})
        runtime.request("POST", "/api/send", json={
            "sessionId": target["id"], "text": "hold-ui", "source": "user"})
        deadline = __import__("time").monotonic() + 20
        while __import__("time").monotonic() < deadline:
            if any(record.get("kind") == "received"
                   and record.get("text") == "hold-ui"
                   for record in runtime.records()):
                break
            __import__("time").sleep(0.05)
        else:
            raise AssertionError("fixture CLI did not receive hold-ui")
        __import__("time").sleep(0.4)
        artifacts = tmp_path / "artifacts"
        env = dict(os.environ)
        env["PAN_E2E_BASE_URL"] = runtime.base_url
        env["PAN_E2E_ARTIFACTS"] = str(artifacts)
        env["PAN_E2E_MA_ID"] = ma["id"]
        env["PAN_E2E_TARGET_ID"] = target["id"]
        env["PAN_AGENT_SESSION_ID"] = ""
        completed = subprocess.run(
            ["node", str(SCRIPT)],
            cwd=str(WEB), env=env, capture_output=True, text=True, timeout=120,
        )
        assert completed.returncode == 0, (
            f"Chromium settings E2E failed\nstdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}")
        assert (artifacts / "result.json").is_file()
        assert (artifacts / "auto-force-settings-saved.png").is_file()
        assert (artifacts / "auto-force-settings-save-failure.png").is_file()
        assert (artifacts / "auto-force-ui-triggered.png").is_file()
        assert any("UI triggered MA" in (record.get("text") or "")
                   for record in runtime.records("steer")), runtime.records()
    finally:
        runtime.stop()
