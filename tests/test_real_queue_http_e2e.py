"""Real-process regression coverage for the durable Pan queue.

This module is intentionally different from the TestClient and mocked worker
tests already in the repository.  It starts a separate FastAPI/uvicorn
process on 8767, uses a persistent temporary data root, talks to it through
HTTP and WebSocket, and uses a deterministic local stream provider.  No real
LLM, QQ bridge, Tunnel, or port 8768 is involved.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil
import pytest
from websockets.sync.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed


ROOT = Path(__file__).resolve().parent.parent
SERVER_LAUNCHER = Path(__file__).resolve().parent / "support" / "isolated_http_server.py"
FAKE_CLI = Path(__file__).resolve().parent / "support" / "fake_stream_cli.py"
PORT = 8767


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _wait_for(predicate, *, timeout: float = 15.0, label: str = "condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
            last = value
        except Exception as exc:  # startup/race polling only
            last = exc
        time.sleep(0.025)
    raise AssertionError(f"timed out waiting for {label}: {last!r}")


class _EventRecorder:
    """Consume the real /ws/agent stream and timestamp selected events."""

    EVENT_TYPES = [
        "queue.item_added",
        "queue.item_delivered",
        "queue.snapshot",
        "worker.spawned",
        "worker.restarted",
        "worker.status",
        "worker.result",
    ]

    def __init__(self, url: str):
        self.url = url
        self.events: list[dict] = []
        self.ready = threading.Event()
        self.stop_requested = threading.Event()
        self.thread = threading.Thread(target=self._run, name="pan-e2e-ws", daemon=True)
        self.error: Exception | None = None

    def start(self):
        self.thread.start()
        _wait_for(self.ready.is_set, label="WebSocket subscription")

    def _run(self):
        try:
            with ws_connect(self.url, open_timeout=10, close_timeout=2) as ws:
                ws.send(json.dumps({
                    "type": "subscribe",
                    "eventTypes": self.EVENT_TYPES,
                }))
                while not self.stop_requested.is_set():
                    try:
                        raw = ws.recv(timeout=0.2)
                    except TimeoutError:
                        continue
                    if raw is None:
                        break
                    event = json.loads(raw)
                    if event.get("type") == "subscribed":
                        self.ready.set()
                        continue
                    if event.get("type") in self.EVENT_TYPES:
                        self.events.append({
                            "at": time.time(),
                            "type": event.get("type"),
                            "sessionId": event.get("sessionId"),
                            "queueItemId": event.get("queueItemId"),
                            "queueItemIds": event.get("queueItemIds"),
                            "queueRevision": event.get("queueRevision"),
                            "sourceSessionId": event.get("sourceSessionId"),
                            "taskSeq": event.get("taskSeq"),
                        })
        except (ConnectionClosed, OSError) as exc:
            if not self.stop_requested.is_set():
                self.error = exc
        except Exception as exc:  # make listener failures visible to the test
            self.error = exc
        finally:
            self.ready.set()

    def stop(self):
        self.stop_requested.set()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "WebSocket recorder did not stop"
        if self.error:
            raise self.error


class _PanRuntime:
    """Own exactly the isolated Pan process(es) used by one test."""

    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.log_path = data_root / "fake-cli.jsonl"
        self.gates = {
            "hold-A": data_root / "release-A",
            "hold-recovery": data_root / "release-recovery",
            "child-report-1": data_root / "release-manager",
        }
        self.capture_marker = data_root / "stale-get-captured"
        self.process: subprocess.Popen | None = None
        self.pan_pids: list[int] = []
        self._owned_processes: list[tuple[int, float, str]] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{PORT}"

    def start(self):
        assert _port_is_free(PORT), f"isolated port {PORT} is not free"
        self.data_root.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(ROOT) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
        env.update({
            "PAN_E2E_DATA_ROOT": str(self.data_root),
            "PAN_E2E_FAKE_CLI": str(FAKE_CLI),
            "PAN_E2E_FAKE_LOG": str(self.log_path),
            "PAN_E2E_GATE_MAP": json.dumps({k: str(v) for k, v in self.gates.items()}),
            "PAN_E2E_CAPTURE_MARKER": str(self.capture_marker),
            # Avoid accidental provider/MCP work in this test process.
            "PAN_AGENT_SESSION_ID": "",
        })
        log = open(self.data_root / "pan.log", "a", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER_LAUNCHER), "--port", str(PORT),
             "--data-root", str(self.data_root)],
            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        self.pan_pids.append(self.process.pid)
        try:
            _wait_for(
                lambda: self.process.poll() is None and self._health_ok(),
                timeout=20,
                label="isolated FastAPI readiness",
            )
        except Exception:
            details = (self.data_root / "pan.log").read_text(encoding="utf-8", errors="replace")
            raise AssertionError(f"isolated server failed to start:\n{details[-4000:]}")
        finally:
            log.close()
        root_process = psutil.Process(self.process.pid)
        owned = [root_process, *root_process.children(recursive=True)]
        for process in owned:
            try:
                command_line = " ".join(process.cmdline())
                self._owned_processes.append((process.pid, process.create_time(), command_line))
            except psutil.Error:
                continue
        self.pan_pids.extend(process.pid for process in owned if process.pid not in self.pan_pids)

    def _health_ok(self) -> bool:
        try:
            response = httpx.get(
                self.base_url + "/api/health", timeout=0.5,
                trust_env=False, verify=False,
            )
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def stop(self):
        process = self.process
        self.process = None
        if process is None and not self._owned_processes:
            return
        # On Windows the venv launcher can leave uvicorn as a child after the
        # Popen handle exits.  Stop only the exact PIDs/create-times captured
        # from this fixture's process tree; never scan or kill a generic
        # python/uvicorn process.
        for pid, create_time, command_line in reversed(self._owned_processes):
            try:
                owned = psutil.Process(pid)
                if abs(owned.create_time() - create_time) > 0.01:
                    continue
                if str(SERVER_LAUNCHER) not in command_line and str(FAKE_CLI) not in command_line:
                    continue
                owned.terminate()
            except psutil.Error:
                continue
        if process is not None and process.poll() is None:
            process.terminate()  # only the Pan PID owned by this fixture
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()  # still only this fixture's Pan PID
                process.wait(timeout=5)
        for pid, create_time, command_line in reversed(self._owned_processes):
            try:
                owned = psutil.Process(pid)
                if abs(owned.create_time() - create_time) <= 0.01 and owned.is_running():
                    if str(SERVER_LAUNCHER) in command_line or str(FAKE_CLI) in command_line:
                        owned.kill()
            except psutil.Error:
                continue
        self._owned_processes.clear()
        _wait_for(lambda: _port_is_free(PORT), timeout=5, label="isolated port release")

    def request(self, method: str, path: str, **kwargs) -> dict:
        with httpx.Client(
            base_url=self.base_url, timeout=15, trust_env=False, verify=False,
        ) as client:
            response = client.request(method, path, **kwargs)
        assert response.status_code == 200, f"{method} {path}: {response.status_code} {response.text}"
        return response.json()

    def get_delayed_queue(self, session_id: str, delay: float) -> dict:
        return self.request(
            "GET", f"/api/sessions/{session_id}/queue",
            headers={"X-Pan-E2E-Delay": str(delay)},
        )

    def fake_records(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        records = []
        for line in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records


@pytest.fixture
def isolated_runtime(tmp_path):
    runtime = _PanRuntime(tmp_path / "persistent-data-root")
    runtime.start()
    recorder = _EventRecorder(runtime.base_url.replace("http", "ws", 1) + "/ws/agent")
    recorder.start()
    try:
        yield runtime, recorder
    finally:
        recorder.stop()
        runtime.stop()


def _create(runtime: _PanRuntime, name: str) -> dict:
    return runtime.request("POST", "/api/sessions", json={
        "name": name,
        "adapter": "cbc",
        "model": "deepseek-v4-flash",
        "permissionMode": "bypassPermissions",
        "outputMode": "stream",
    })


def _spawn(runtime: _PanRuntime, session_id: str) -> dict:
    return runtime.request("POST", "/api/spawn", json={"sessionId": session_id})


def _enqueue(runtime: _PanRuntime, session_id: str, text: str, client_id: str) -> dict:
    return runtime.request(
        "POST", f"/api/sessions/{session_id}/queue",
        json={"text": text, "clientMessageId": client_id},
    )


def _received(runtime: _PanRuntime, *, cwd: str | None = None, text=None) -> list[dict]:
    result = [record for record in runtime.fake_records() if record.get("kind") == "received"]
    if cwd is not None:
        result = [record for record in result if record.get("cwd") == cwd]
    if text is not None:
        if callable(text):
            result = [record for record in result if text(record.get("text", ""))]
        else:
            result = [record for record in result if record.get("text") == text]
    return result


def _event_ids(recorder: _EventRecorder, event_type: str, session_id: str, field: str) -> list:
    return [
        event.get(field)
        for event in recorder.events
        if event.get("type") == event_type and event.get("sessionId") == session_id
    ]


@pytest.mark.timeout(120)
def test_real_http_queue_fifo_races_sources_and_restart(isolated_runtime):
    """Reproduce the old-queue symptom and prove durable routing end to end."""
    runtime, recorder = isolated_runtime
    evidence = {
        "port": PORT,
        "dataRoot": str(runtime.data_root),
        "panPids": runtime.pan_pids,
        "sessions": {},
        "queueItemIds": {},
        "clientMessageIds": {},
        "taskIds": {},
    }

    fifo = _create(runtime, "real-fifo")
    fifo_id = fifo["id"]
    evidence["sessions"]["fifo"] = fifo_id
    fifo_workdir = fifo["workdir"]
    first_worker = _spawn(runtime, fifo_id)
    first_pid = None

    first = _enqueue(runtime, fifo_id, "hold-A", "cm-fifo-a")
    ids = {"A": first["item"]["queueItemId"]}
    evidence["queueItemIds"]["fifo-A"] = ids["A"]
    evidence["clientMessageIds"]["fifo"] = {
        "A": "cm-fifo-a", "B": "cm-fifo-b", "C": "cm-fifo-c",
        "D": "cm-fifo-d", "E": "cm-fifo-e",
    }
    _wait_for(
        lambda: _received(runtime, cwd=fifo_workdir, text="hold-A"),
        label="first FIFO item at fake provider",
    )
    records_before_restart = _received(runtime, cwd=fifo_workdir)
    first_pid = records_before_restart[0]["pid"]

    backlog = []
    for name, client_id in [("backlog-B", "cm-fifo-b"), ("backlog-C", "cm-fifo-c"),
                            ("backlog-D", "cm-fifo-d")]:
        response = _enqueue(runtime, fifo_id, name, client_id)
        backlog.append(response["item"]["queueItemId"])
    ids.update(dict(zip(("B", "C", "D"), backlog)))
    duplicate = _enqueue(runtime, fifo_id, "must-not-run-twice", "cm-fifo-b")
    assert duplicate["duplicate"] is True
    assert duplicate["item"]["queueItemId"] == ids["B"]

    before = runtime.request("GET", f"/api/sessions/{fifo_id}/queue")
    assert [item["text"] for item in before["items"]] == [
        "backlog-B", "backlog-C", "backlog-D",
    ]
    assert all(item["source"] == "user" for item in before["items"])
    assert all(item["meta"]["dispatchState"] == "queued" for item in before["items"])

    # Capture a real old HTTP response before a later enqueue, then hold its
    # network delivery.  This creates the response-order race deterministically.
    delayed_result: dict[str, dict] = {}
    delayed_thread = threading.Thread(
        target=lambda: delayed_result.setdefault(
            "response", runtime.get_delayed_queue(fifo_id, 0.7)),
        daemon=True,
    )
    delayed_thread.start()
    _wait_for(runtime.capture_marker.exists, label="captured stale GET /queue")
    later = _enqueue(runtime, fifo_id, "backlog-E", "cm-fifo-e")
    ids["E"] = later["item"]["queueItemId"]
    evidence["queueItemIds"]["fifo"] = ids
    newer = runtime.request("GET", f"/api/sessions/{fifo_id}/queue")
    assert [item["text"] for item in newer["items"]] == [
        "backlog-B", "backlog-C", "backlog-D", "backlog-E",
    ]

    # The first item has crossed the hand-off boundary and is blocked in the
    # provider.  Restart must consume only the queued backlog on the new
    # generation; hold-A must never be replayed.
    restarted = runtime.request("POST", f"/api/sessions/{fifo_id}/worker/restart")
    assert restarted["sessionId"] == fifo_id
    _wait_for(
        lambda: len(_received(runtime, cwd=fifo_workdir, text=lambda value: value in {
            "backlog-B", "backlog-C", "backlog-D", "backlog-E",
        })) == 4,
        timeout=20,
        label="FIFO backlog after worker restart",
    )
    assert len(_received(runtime, cwd=fifo_workdir, text="hold-A")) == 1
    restarted_pids = {record["pid"] for record in _received(runtime, cwd=fifo_workdir)}
    assert first_pid in restarted_pids
    assert len(restarted_pids) >= 2, "restart must create a new fake provider process"
    assert runtime.request("GET", f"/api/sessions/{fifo_id}/queue")["items"] == []

    delayed_thread.join(timeout=5)
    assert not delayed_thread.is_alive()
    stale = delayed_result["response"]
    assert [item["text"] for item in stale["items"]] == [
        "backlog-B", "backlog-C", "backlog-D",
    ]
    assert stale["queueRevision"] < newer["queueRevision"]
    assert "backlog-E" not in [item["text"] for item in stale["items"]]

    # Sent-to-CLI is a durable idempotency receipt, not a pending queue row,
    # and it survives a real Pan process restart.
    session_file = runtime.data_root / "sessions" / f"{fifo_id}.json"
    stored = json.loads(session_file.read_text(encoding="utf-8"))
    assert stored["queue_pending"] == []
    for key, queue_id in ids.items():
        assert stored["queue_delivery_ledger"][queue_id]["deliveryState"] == "sent_to_cli", key
    # The first WebSocket is intentionally tied to the first real process.
    # Close it before restarting the service; the earlier event evidence is
    # retained in ``recorder.events`` and later assertions use HTTP/persistence.
    recorder.stop()
    runtime.stop()
    runtime.start()
    receipt_after_restart = _enqueue(runtime, fifo_id, "late-duplicate", "cm-fifo-a")
    assert receipt_after_restart["duplicate"] is True
    assert receipt_after_restart["item"]["queueItemId"] == ids["A"]
    assert runtime.request("GET", f"/api/sessions/{fifo_id}/queue")["items"] == []

    delivered_events = [
        event for event in recorder.events
        if event.get("type") == "queue.item_delivered" and event.get("sessionId") == fifo_id
    ]
    delivered_ids = [queue_id for event in delivered_events for queue_id in (event.get("queueItemIds") or [])]
    assert delivered_ids[:5] == [ids[key] for key in ("A", "B", "C", "D", "E")]
    assert all(isinstance(event.get("queueRevision"), int) for event in delivered_events[:5])
    assert [event["queueRevision"] for event in delivered_events[:5]] == sorted(
        event["queueRevision"] for event in delivered_events[:5]
    )
    added_ids = _event_ids(recorder, "queue.item_added", fifo_id, "queueItemId")
    assert added_ids[:5] == [ids[key] for key in ("A", "B", "C", "D", "E")]

    history = runtime.request("GET", f"/api/sessions/{fifo_id}/history?limit=100")
    user_history = [entry for entry in history["history"] if entry.get("role") == "user"]
    assert [entry["content"] for entry in user_history[:5]] == [
        "hold-A", "backlog-B", "backlog-C", "backlog-D", "backlog-E",
    ]
    assert all(entry.get("source") == "user" for entry in user_history[:5])
    assert all("sourceSessionId" not in entry for entry in user_history[:5])

    # Manager reports and ordinary user tasks share a business FIFO, but their
    # source/session metadata must remain distinct.
    manager = _create(runtime, "real-manager")
    child_one = _create(runtime, "real-child-one")
    child_two = _create(runtime, "real-child-two")
    manager_id, child_one_id, child_two_id = manager["id"], child_one["id"], child_two["id"]
    evidence["sessions"].update({"manager": manager_id, "childOne": child_one_id, "childTwo": child_two_id})
    assert runtime.request("POST", "/api/report-subscribe", json={
        "managerId": manager_id, "sessionId": child_one_id,
    })["subscribed"] is True
    assert runtime.request("POST", "/api/report-subscribe", json={
        "managerId": manager_id, "sessionId": child_two_id,
    })["subscribed"] is True
    _spawn(runtime, manager_id)
    _spawn(runtime, child_one_id)
    _spawn(runtime, child_two_id)
    manager_workdir = manager["workdir"]
    report_one = runtime.request("POST", "/api/assign", json={
        "sessionId": child_one_id, "text": "child-report-1", "taskId": "manager-task-1",
    })
    evidence["taskIds"]["child-report-1"] = report_one["taskId"]
    _wait_for(
        lambda: _received(runtime, cwd=manager_workdir,
                          text=lambda value: "child-report-1" in value),
        label="manager receives first subscribed report",
    )
    report_two = runtime.request("POST", "/api/assign", json={
        "sessionId": child_two_id, "text": "child-report-2", "taskId": "manager-task-2",
    })
    evidence["taskIds"]["child-report-2"] = report_two["taskId"]

    def manager_has_second_report():
        queue = runtime.request("GET", f"/api/sessions/{manager_id}/queue")
        return queue if any(
            item["kind"] == "report" and item["meta"].get("sourceSessionId") == child_two_id
            for item in queue["items"]
        ) else None

    _wait_for(manager_has_second_report, label="second report in manager backlog")
    manager_user = _enqueue(runtime, manager_id, "manager-user-task", "cm-manager-user")
    evidence["clientMessageIds"]["manager-user"] = "cm-manager-user"
    evidence["queueItemIds"]["manager-user"] = manager_user["item"]["queueItemId"]
    manager_queue = runtime.request("GET", f"/api/sessions/{manager_id}/queue")
    assert [item["kind"] for item in manager_queue["items"]] == ["report", "task"]
    report_item, user_item = manager_queue["items"]
    evidence["queueItemIds"]["manager-report-2"] = report_item["queueItemId"]
    assert report_item["meta"]["sourceSessionId"] == child_two_id
    assert user_item["queueItemId"] == manager_user["item"]["queueItemId"]
    assert user_item["source"] == "user"
    assert "sourceSessionId" not in user_item["meta"]
    runtime.gates["child-report-1"].touch()
    _wait_for(
        lambda: len(_received(runtime, cwd=manager_workdir, text=lambda value: "child-report-2" in value)) >= 1,
        label="manager consumes second report",
    )
    _wait_for(
        lambda: len(_received(runtime, cwd=manager_workdir, text="manager-user-task")) >= 1,
        label="manager consumes normal user task after report",
    )
    assert runtime.request("GET", f"/api/sessions/{manager_id}/queue")["items"] == []
    manager_history = runtime.request("GET", f"/api/sessions/{manager_id}/history?limit=100")["history"]
    report_history = [entry for entry in manager_history if entry.get("source") == "report"]
    user_entries = [entry for entry in manager_history
                    if entry.get("role") == "user" and entry.get("content") == "manager-user-task"]
    assert any(child_one_id in entry.get("sourceSessionIds", []) for entry in report_history)
    assert any(child_two_id in entry.get("sourceSessionIds", []) for entry in report_history)
    assert len(user_entries) == 1
    assert "sourceSessionId" not in user_entries[0]
    assert runtime.request("GET", f"/api/sessions/{child_one_id}/queue")["items"] == []
    assert runtime.request("GET", f"/api/sessions/{child_two_id}/queue")["items"] == []

    # Two independent sessions may receive equal-looking text without sharing
    # queue items, receipts, or history.
    isolated_one = _create(runtime, "real-isolated-one")
    isolated_two = _create(runtime, "real-isolated-two")
    isolated_one_id, isolated_two_id = isolated_one["id"], isolated_two["id"]
    evidence["sessions"].update({"isolatedOne": isolated_one_id, "isolatedTwo": isolated_two_id})
    _spawn(runtime, isolated_one_id)
    _spawn(runtime, isolated_two_id)
    one_item = _enqueue(runtime, isolated_one_id, "same-old-marker-one", "cm-isolated-one")
    two_item = _enqueue(runtime, isolated_two_id, "same-old-marker-two", "cm-isolated-two")
    assert one_item["item"]["queueItemId"] != two_item["item"]["queueItemId"]
    _wait_for(lambda: _received(runtime, cwd=isolated_one["workdir"], text="same-old-marker-one"), label="isolated one delivery")
    _wait_for(lambda: _received(runtime, cwd=isolated_two["workdir"], text="same-old-marker-two"), label="isolated two delivery")
    one_history = runtime.request("GET", f"/api/sessions/{isolated_one_id}/history?limit=100")["history"]
    two_history = runtime.request("GET", f"/api/sessions/{isolated_two_id}/history?limit=100")["history"]
    assert "same-old-marker-two" not in [entry.get("content") for entry in one_history]
    assert "same-old-marker-one" not in [entry.get("content") for entry in two_history]

    # Restart recovery: hand-author the durable state a crashed worker leaves
    # behind, then let the real restarted FastAPI process migrate reserved to
    # queued and consume it.  This avoids a destructive kill while still
    # exercising the exact persisted recovery contract.
    recovery = _create(runtime, "real-recovery")
    recovery_id = recovery["id"]
    evidence["sessions"]["recovery"] = recovery_id
    _spawn(runtime, recovery_id)
    seed = _enqueue(runtime, recovery_id, "recovery-seed", "cm-recovery-seed")
    _wait_for(lambda: _received(runtime, cwd=recovery["workdir"], text="recovery-seed"), label="recovery seed delivery")
    runtime.stop()
    recovery_path = runtime.data_root / "sessions" / f"{recovery_id}.json"
    recovery_data = json.loads(recovery_path.read_text(encoding="utf-8"))
    recovery_qid = "q_recovery_reserved"
    recovery_item = {
        "type": "task", "kind": "task", "id": recovery_qid,
        "queueItemId": recovery_qid, "text": "hold-recovery", "source": "user",
        "seq": recovery_data["task_seq"] + 1, "taskId": "recovery-task",
        "clientMessageId": "cm-recovery", "deliveryState": "reserved",
        "dispatchState": "reserved", "reservedBy": "worker-before-crash",
        "reservedGeneration": 4, "reservedAt": time.time(), "revision": 1,
        "createdAt": time.time(), "position": 0,
    }
    recovery_data["task_seq"] = recovery_item["seq"]
    recovery_data["accepted_input_ids"].append("cm-recovery")
    recovery_data["queue_pending"] = [recovery_item]
    recovery_data["queue_delivery_ledger"][recovery_qid] = dict(recovery_item)
    recovery_data["queue_revision"] += 1
    recovery_path.write_text(json.dumps(recovery_data, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence["queueItemIds"]["recovery-reserved"] = recovery_qid
    evidence["clientMessageIds"]["recovery-reserved"] = "cm-recovery"
    evidence["taskIds"]["recovery-reserved"] = "recovery-task"
    runtime.start()
    migrated = runtime.request("GET", f"/api/sessions/{recovery_id}/queue")
    assert [item["queueItemId"] for item in migrated["items"]] == [recovery_qid]
    assert migrated["items"][0]["meta"]["dispatchState"] == "queued"
    migrated_data = json.loads(recovery_path.read_text(encoding="utf-8"))
    assert migrated_data["queue_pending"][0]["lastDeliveryState"] == "reserved"
    assert migrated_data["queue_pending"][0]["deliveryState"] == "queued"
    _spawn(runtime, recovery_id)
    _wait_for(lambda: _received(runtime, cwd=recovery["workdir"], text="hold-recovery"), label="recovered reserved item at provider")
    # The in-flight row is deliberately hidden from the pending API while the
    # provider hand-off is active; after release its durable receipt is sent.
    assert runtime.request("GET", f"/api/sessions/{recovery_id}/queue")["items"] == []
    runtime.gates["hold-recovery"].touch()
    _wait_for(lambda: runtime.request("GET", f"/api/sessions/{recovery_id}/queue")["items"] == [], label="recovered item completion")
    final_recovery_data = json.loads(recovery_path.read_text(encoding="utf-8"))
    assert final_recovery_data["queue_delivery_ledger"][recovery_qid]["deliveryState"] == "sent_to_cli"

    evidence["eventSummary"] = [
        event for event in recorder.events
        if event.get("type") in {"queue.item_added", "queue.item_delivered", "queue.snapshot"}
    ]
    evidence["fakeReceived"] = [
        {"pid": item["pid"], "cwd": item["cwd"], "text": item["text"], "at": item["at"]}
        for item in runtime.fake_records() if item.get("kind") == "received"
    ]
    print("REAL_HTTP_QUEUE_E2E " + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
