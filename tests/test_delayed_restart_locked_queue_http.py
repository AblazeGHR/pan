"""Real HTTP/process regression: queue locks must not gate Worker retirement."""
import json
import os
import subprocess
import socket
import sys

import psutil
import pytest
import test_real_queue_http_e2e as real_queue
from packages.jobs.window import command_window_options

from test_real_queue_http_e2e import (
    _PanRuntime, _EventRecorder, _create, _enqueue, _received, _wait_for,
)


@pytest.fixture(autouse=True)
def owned_dynamic_port(monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    monkeypatch.setattr(real_queue, "PORT", port)


@pytest.mark.parametrize("entry", ["settings", "topbar", "reconnect"]
                         if os.environ.get("PAN_TEST_DELAYED_BROWSER_SCRIPT") else ["http"])
def test_locked_queue_delayed_restart(tmp_path, entry):
    browser_script = os.environ.get("PAN_TEST_DELAYED_BROWSER_SCRIPT")
    runtime = _PanRuntime(tmp_path / "runtime")
    runtime.start()
    recorder = _EventRecorder(runtime.base_url.replace("http", "ws", 1) + "/ws/agent")
    recorder.EVENT_TYPES = [*recorder.EVENT_TYPES, "worker.delayed_restart"]
    recorder.start()
    try:
        s = _create(runtime, "real-fifo-" + entry)
        sid = s["id"]
        _enqueue(runtime, sid, "hold-A", "first")
        first = _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="hold-A"))
        old = psutil.Process(first[0]["pid"])
        identity = old.create_time()
        locked = runtime.request("POST", f"/api/sessions/{sid}/queue", json={
            "text": "locked-next", "clientMessageId": "locked", "locked": True,
        })
        assert not locked.get("error"), locked
        endpoint = f"/api/sessions/{sid}/worker/delayed-restart"
        if browser_script:
            subprocess.run(["node", browser_script, runtime.base_url, sid, entry,
                            str(runtime.gates["hold-A"])],
                           check=True, timeout=60, stdout=sys.stdout, stderr=sys.stderr,
                           **command_window_options())
            intent = runtime.request("GET", endpoint)["delayedRestart"]
        else:
            intent = runtime.request("POST", endpoint)["delayedRestart"]
        assert intent["status"] == ("completed" if entry == "reconnect" and browser_script else "pending")
        if intent["status"] == "pending":
            assert runtime.request("POST", endpoint)["delayedRestart"] == intent
            assert old.is_running() and old.create_time() == identity
        expected_received = 2 if entry == "reconnect" and browser_script else 1
        assert len(_received(runtime, cwd=s["workdir"])) == expected_received
        runtime.gates["hold-A"].touch()
        finished = _wait_for(lambda: (
            state if (state := runtime.request("GET", endpoint)["delayedRestart"])
            ["status"] == "completed" else None), label="restart with locked queue")
        assert finished["replacementGeneration"] > intent["generation"]
        queue = runtime.request("GET", f"/api/sessions/{sid}/queue")
        assert "locked-next" in json.dumps(queue), queue
        assert len(_received(runtime, cwd=s["workdir"])) == expected_received
        # Unlock via the public route. The retained item must run only on the replacement.
        items = queue.get("items", queue.get("queue", []))
        item = next(row for row in items if row.get("text") == "locked-next")
        assert item["meta"]["locked"] is True
        runtime.request("PATCH", f"/api/sessions/{sid}/queue/{item['id']}/lock", json={"locked": False})
        delivered = _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="locked-next"))
        assert len(delivered) == 1 and delivered[0]["pid"] != old.pid
        assert runtime.request("GET", endpoint)["delayedRestart"] == finished
        events = [e for e in recorder.events if e["sessionId"] == sid]
        spawns = [i for i, e in enumerate(events) if e["type"] == "worker.spawned"]
        assert len(spawns) == 2, "one initial spawn and exactly one replacement"
        result_index = next(i for i, e in enumerate(events) if e["type"] == "worker.result")
        assert result_index < spawns[1], "terminal publication precedes replacement"
        durable = json.loads((runtime.data_root / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))
        assert durable["queue_delivery_ledger"][item["id"]]["deliveryState"] == "sent_to_cli"
        print("REAL_DELAYED_RESTART " + json.dumps({
            "entry": entry, "base": runtime.base_url, "dataRoot": str(runtime.data_root),
            "oldPid": old.pid, "oldCreatedAt": identity, "newPid": delivered[0]["pid"],
            "accepted": intent, "completed": finished, "lockedItem": item,
            "events": recorder.events,
        }))
    finally:
        runtime.gates["hold-A"].touch()
        identities = []
        for record in runtime.fake_records():
            if record.get("kind") != "init":
                continue
            try:
                process = psutil.Process(record["pid"])
                if str(runtime.data_root) in process.cwd():
                    identities.append((process.pid, process.create_time()))
            except psutil.Error:
                pass
        try:
            if 'sid' in locals():
                runtime.request("POST", f"/api/sessions/{sid}/worker/kill")
        finally:
            try:
                recorder.stop()
            finally:
                runtime.stop()
        for pid, created in identities:
            assert not psutil.pid_exists(pid) or psutil.Process(pid).create_time() != created
        print("CLEANUP", runtime.base_url, "owned server stopped; port released")
