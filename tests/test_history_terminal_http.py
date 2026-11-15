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
    evidence = {"port": base.PORT, "ownedProfile": "<tmp>/owned-history-profile"}
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
        assert received == ["err-capacity-report", "later-success-body", "history-terminal-long",
                            "cbc-quota-result", "cbc-quota-echo", "history-terminal-long",
                            "same-429-done", "cbc-quota-echo", "cbc-unknown-error"]
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
        # CBC failed result: the durable terminal row must carry the native
        # reason/code, and the assistant prose must stay ordinary content.
        cbc_id = browser["cbcNativeReason"]["sessionId"]
        cbc_history = rt.request("GET", f"/api/sessions/{cbc_id}/history?limit=500",
                                 headers={"Authorization": "Bearer " + token})["history"]
        cbc_terminal = next(row for row in cbc_history if row.get("kind") == "terminal")
        cbc_error = cbc_terminal["terminal"]["error"]
        prose = "429 您的使用量已超出频率限制（普通说明，不是诊断）。"
        assert cbc_terminal["terminal"]["status"] == "error"
        assert cbc_error["code"] == 6004 and cbc_error["source"] == "cli.error"
        assert "429 您的使用量已超出频率限制" in cbc_error["message"]
        assert [row["content"] for row in cbc_history if row.get("role") == "assistant"] == [prose]
        assert prose not in cbc_error["message"]
        assert "未提供错误详情" not in json.dumps(cbc_terminal, ensure_ascii=False)
        # Echo shape: the assistant body is byte-identical to errors[0] and is
        # still stored/served verbatim; only the UI folds it into the card.
        echo_id = browser["cbcEchoFold"]["sessionId"]
        echo_history = rt.request("GET", f"/api/sessions/{echo_id}/history?limit=500",
                                  headers={"Authorization": "Bearer " + token})["history"]
        echo_terminals = [row for row in echo_history if row.get("kind") == "terminal"]
        echo_terminal = next(row for row in echo_terminals if row["terminal"]["status"] == "error")
        echo_body = [row for row in echo_history if row.get("role") == "assistant"]
        assert len(echo_body) >= 1
        assert echo_body[0]["content"] == echo_terminal["terminal"]["error"]["message"]
        assert echo_terminal["terminal"]["anchorMessageId"] == echo_body[0]["messageId"]
        assert echo_terminal["terminal"]["error"]["code"] == 6004
        assert any(row["terminal"]["status"] == "done" for row in echo_terminals)
        # Negative controls keep both bodies and both terminal facts.
        negative_id = browser["cbcEchoNegatives"]["sessionId"]
        negative_history = rt.request("GET", f"/api/sessions/{negative_id}/history?limit=500",
                                      headers={"Authorization": "Bearer " + token})["history"]
        negative_terminals = [row for row in negative_history if row.get("kind") == "terminal"]
        assert [row["terminal"]["status"] for row in negative_terminals] == ["done", "error", "error"]
        assert [row["content"] for row in negative_history if row.get("role") == "assistant"] == [
            echo_body[0]["content"], echo_body[0]["content"],
            "429 频率限制说明（本次没有结构化原因）。"]
        unknown_error = negative_terminals[2]["terminal"]["error"]
        assert unknown_error["code"] is None and unknown_error["message"] == ""
        assert negative_terminals[1]["terminal"]["error"]["code"] == 6004
        evidence["cbcEchoColdReopen"] = True
        evidence["cbcColdReopen"] = True
        evidence["coldReopen"] = True
    finally:
        def _scrub(command):
            text = str(command)
            for root in (str(rt.data_root), str(tmp_path)):
                text = text.replace(root, "<tmp>")
            return text
        # Evidence keeps the isolated shape only: no user/profile path or key.
        evidence["ownedProcesses"] = [
            [pid, created_at, _scrub(command)]
            for pid, created_at, command in rt._owned_processes
        ]
        rt.stop()
        evidence.update(portReleased=base._port_is_free(base.PORT))
        (out / "runtime.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    assert evidence["portReleased"]
