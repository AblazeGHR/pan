"""Owned HTTP/fakeCLI closed loop: Worker error -> Event.system -> Action target."""
from __future__ import annotations

import json
from pathlib import Path

import psutil

from tests import test_real_queue_http_e2e as harness
from tests.test_automation_core import action
from tests.support.authorization import authenticate_user_requests


ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts" / "system-fields-registry"


def test_worker_error_persists_system_session_and_pauses_via_mapping(tmp_path, monkeypatch):
    assert harness.PORT not in {8767, 8768}
    monkeypatch.setattr(harness, "SERVER_LAUNCHER",
                        Path(__file__).parent / "support" / "automation_http_server.py")
    runtime = harness._PanRuntime(tmp_path / "owned-system-fields")
    authenticate_user_requests(runtime)
    runtime.data_root.mkdir(parents=True)
    (runtime.data_root / "config.json").write_text(
        json.dumps({"plugin_manifests": [], "scheduler": {"enabled": False}}), encoding="utf-8")
    runtime.start()
    evidence = {"port": harness.PORT, "dataRootKind": "owned-temp"}
    try:
        target = runtime.request("POST", "/api/sessions", json={
            "name": "system-fields-target", "adapter": "cbc",
            "model": "deepseek-v4-flash", "permissionMode": "bypassPermissions",
            "outputMode": "stream", "mcpServers": [],
        })
        assert "id" in target, target
        assert "error" not in harness._spawn(runtime, target["id"])

        pause = action()
        pause["execution"]["selector"] = {"mode": "event", "path": "system.sessionId"}
        pause["input_schema"] = {
            "type": "object",
            "properties": {"targetSessionId": {"type": "string"}},
            "required": ["targetSessionId"],
            "additionalProperties": False,
        }
        runtime.request("POST", "/api/actions/actions", json={"item": pause})

        item = {
            "id": "runtime_issue_pause",
            "name": "Pause runtime issue source",
            "eventTypes": ["runtime.issue.v1"],
            "actionId": "pause",
            "arguments": {"targetSessionId": {"from": "system", "path": "sessionId"}},
            "enabled": False,
        }
        runtime.request("POST", "/api/automation/triggers",
                        json={"item": item, "expectedRevision": 0})
        key = (runtime.data_root / "automation" / "user.key").read_text(encoding="utf-8")
        token = runtime.request("POST", "/api/automation/login", json={"key": key})["token"]
        runtime.request("POST", "/api/automation/triggers",
                        json={"item": {**item, "enabled": True}, "expectedRevision": 1},
                        headers={"Authorization": "Bearer " + token})

        view = runtime.request("GET", "/api/automation")
        definitions = {row["type"]: row for row in view["definitions"]}
        assert definitions["runtime.issue.v1"]["available"] is True

        harness._enqueue(runtime, target["id"], "RUNTIME_ISSUE_CAPACITY", "runtime-issue-capacity")

        def issue_row():
            page = runtime.request("GET", "/api/automation/events?after=0&limit=100")
            return next((row for row in page["events"]
                         if row["event"]["type"] == "runtime.issue.v1"), None)

        row = harness._wait_for(issue_row, timeout=30, label="durable runtime issue Event")
        event = row["event"]
        assert event["source"] == {"type": "core", "id": "runtime"}
        assert event["system"]["sessionId"] == target["id"]
        assert event["system"].get("workerId")
        assert event["payload"]["correlation"]["sessionId"] == target["id"]
        assert event["payload"]["classification"] == "cbc_rate_limited"
        assert event["payload"]["observation"]["code"] == "model_capacity_exceeded"
        assert event["payload"]["observation"]["status"] == 429

        def finished_run():
            listing = runtime.request("GET", "/api/automation")
            run = next((run for run in listing["runs"].values()
                        if run["event"]["id"] == event["id"]), None)
            return run if run and run["status"] not in {"queued", "running", "retry_wait"} else None

        run = harness._wait_for(finished_run, timeout=30, label="runtime issue Action Run")
        assert run["status"] == "succeeded", run
        assert run["arguments"] == {"targetSessionId": target["id"]}
        assert run["targetIds"] == [target["id"]]
        assert run["actionId"] == "pause"
        assert runtime.request("GET", f"/api/sessions/{target['id']}/queue")["queuePaused"] is True
        current = issue_row()
        assert current is not None
        evidence.update(
            event={"id": event["id"], "source": event["source"], "system": event["system"],
                   "classification": event["payload"]["classification"],
                   "observationCode": event["payload"]["observation"]["code"],
                   "observationStatus": event["payload"]["observation"]["status"],
                   "correlation": event["payload"]["correlation"]},
            run={"id": run["id"], "status": run["status"], "arguments": run["arguments"],
                 "targetIds": run["targetIds"], "attempts": run["attemptCount"]},
            queuePaused=True,
            eventDelivery={"status": current.get("status"), "deliveryCount": current.get("deliveryCount")},
            fakeCliRecords=len(runtime.fake_records()),
            realProviderUsed=False,
        )
    finally:
        if runtime.process and runtime.process.poll() is None:
            for process in psutil.Process(runtime.process.pid).children(recursive=True):
                runtime._owned_processes.append(
                    (process.pid, process.create_time(), " ".join(process.cmdline())))
        runtime.stop()
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "http-closed-loop.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8")
