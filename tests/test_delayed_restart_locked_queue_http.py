"""Real HTTP/process regression: queue locks must not gate Worker retirement."""
import json
import os
import subprocess
import socket
import sys
import time

import psutil
import pytest
import test_real_queue_http_e2e as real_queue
from packages.jobs.window import command_window_options

from test_real_queue_http_e2e import (
    _PanRuntime, _EventRecorder, _create, _enqueue, _received, _wait_for, _spawn,
)


def _read_session_metadata(path):
    # The live server atomically replaces this evidence file. Windows can
    # briefly reject opening the target during replacement. Retry only that
    # transient open, never the scenario, JSON decoding, or any assertion.
    for attempt in range(6):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except PermissionError as exc:
            if (os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32, 33}
                    or attempt == 5):
                raise
            time.sleep(.01 * (attempt + 1))
    return json.loads(text)


@pytest.fixture(autouse=True)
def owned_dynamic_port(monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    monkeypatch.setattr(real_queue, "PORT", port)


def _stop_owned(runtime, recorder, session_id):
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
        if session_id:
            runtime.request("POST", f"/api/sessions/{session_id}/worker/kill")
    finally:
        try:
            recorder.stop()
        finally:
            runtime.stop()
    for pid, created in identities:
        assert not psutil.pid_exists(pid) or psutil.Process(pid).create_time() != created
    print("CLEANUP", runtime.base_url, "owned server stopped; port released")


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
        durable = _read_session_metadata(runtime.data_root / "sessions" / f"{sid}.json")
        assert durable["queue_delivery_ledger"][item["id"]]["deliveryState"] == "sent_to_cli"
        print("REAL_DELAYED_RESTART " + json.dumps({
            "entry": entry, "base": runtime.base_url, "dataRoot": str(runtime.data_root),
            "oldPid": old.pid, "oldCreatedAt": identity, "newPid": delivered[0]["pid"],
            "accepted": intent, "completed": finished, "lockedItem": item,
            "events": recorder.events,
        }))
    finally:
        _stop_owned(runtime, recorder, locals().get("sid"))


@pytest.mark.parametrize("order", ["plugin-first", "manual-first", "idle", "offline"])
def test_plugin_configuration_and_delayed_restart_share_lifecycle(tmp_path, order):
    runtime = _PanRuntime(tmp_path / "runtime")
    runtime.start()
    recorder = _EventRecorder(runtime.base_url.replace("http", "ws", 1) + "/ws/agent")
    recorder.start()
    try:
        s = _create(runtime, "real-fifo-plugin-" + order)
        sid = s["id"]
        endpoint = f"/api/sessions/{sid}/worker/delayed-restart"
        active = order in {"plugin-first", "manual-first"}
        runtime.request("PUT", "/api/settings/ui", json={"autoReport": {"ma": True, "user": True}})
        if active:
            manager = _create(runtime, "paused-report-manager")
            runtime.request("PATCH", f"/api/sessions/{manager['id']}/queue/paused", json={"paused": True})
            assert runtime.request("POST", "/api/report-subscribe", json={
                "managerId": manager["id"], "sessionId": sid,
            })["subscribed"]
            _enqueue(runtime, sid, "hold-A", "active")
            first = _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="hold-A"))
            process = psutil.Process(first[0]["pid"])
            created = process.create_time()
        elif order == "idle":
            _spawn(runtime, sid)
        locked = runtime.request("POST", f"/api/sessions/{sid}/queue", json={
            "text": "plugin-next", "clientMessageId": "locked", "locked": True,
        })["item"]["queueItemId"]
        prior = runtime.request("POST", endpoint)["delayedRestart"] if order == "manual-first" else None
        bindings = [{"plugin_id": "qq", "enabled": True, "mode": "pan_native"}]
        changed = runtime.request("PATCH", f"/api/sessions/{sid}", json={"pluginBindings": bindings})
        assert not changed.get("error"), changed
        if order == "offline":
            assert changed["pluginApplyStatus"] == "next_start"
            assert runtime.request("GET", endpoint)["delayedRestart"] is None
            accepted = runtime.request("POST", endpoint)["delayedRestart"]
            assert accepted["action"] == "start"
        else:
            accepted = changed["delayedRestart"]
            assert changed["pluginRestart"] == accepted
        if active:
            assert changed["pluginApplyStatus"] == "pending"
            assert accepted["status"] == "pending"
            browser_script = os.environ.get("PAN_TEST_DELAYED_BROWSER_SCRIPT")
            if browser_script and order == "plugin-first":
                subprocess.run(["node", browser_script, runtime.base_url, sid, "plugin-pending"],
                               check=True, timeout=60, stdout=sys.stdout, stderr=sys.stderr,
                               **command_window_options())
            if prior:
                assert accepted == prior
            assert runtime.request("POST", endpoint)["delayedRestart"] == accepted
            # A second physical tool-list update joins the same runtime intent.
            bindings[0]["mode"] = "standalone"
            updated = runtime.request("PATCH", f"/api/sessions/{sid}", json={"pluginBindings": bindings})
            assert updated["delayedRestart"] == accepted
            assert process.is_running() and process.create_time() == created
            assert len(_received(runtime, cwd=s["workdir"])) == 1
            disk = _read_session_metadata(runtime.data_root / "sessions" / f"{sid}.json")
            assert disk["adapter_config"]["plugin_bindings"] == bindings
            runtime.gates["hold-A"].touch()
        completed = _wait_for(lambda: (
            state if (state := runtime.request("GET", endpoint)["delayedRestart"])["status"] == "completed"
            else None), label="plugin/delayed restart completion")
        queue = runtime.request("GET", f"/api/sessions/{sid}/queue")
        row = next(item for item in queue["items"] if item["id"] == locked)
        assert row["meta"]["locked"]
        same = runtime.request("PATCH", f"/api/sessions/{sid}", json={"pluginBindings": bindings})
        assert same["pluginApplyStatus"] == "applied" and not same.get("requireRestart")
        assert runtime.request("GET", endpoint)["delayedRestart"] == completed
        assert not _received(runtime, cwd=s["workdir"], text="plugin-next")
        if active:
            manager_disk = _read_session_metadata(runtime.data_root / "sessions" / f"{manager['id']}.json")
            assert len(manager_disk["queue_pending"]) == 1
            assert manager_disk["queue_pending"][0]["result"] == "hold-A"
        runtime.request("PATCH", f"/api/sessions/{sid}/queue/{locked}/lock", json={"locked": False})
        received = _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="plugin-next"))
        assert len(received) == 1
        if active:
            assert received[0]["pid"] != process.pid
        events = [event for event in recorder.events if event["sessionId"] == sid]
        assert sum(event["type"] == "worker.spawned" for event in events) == (1 if order == "offline" else 2)
        if active:
            result = next(i for i, event in enumerate(events) if event["type"] == "worker.result")
            assert result < max(i for i, event in enumerate(events) if event["type"] == "worker.spawned")
        print("PLUGIN_DELAYED_COMBINATION", json.dumps({"order": order, "base": runtime.base_url,
              "accepted": accepted, "completed": completed, "bindings": bindings}))
    finally:
        _stop_owned(runtime, recorder, locals().get("sid"))
