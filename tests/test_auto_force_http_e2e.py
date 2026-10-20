"""Real isolated HTTP/WS coverage for automatic Force send.

This starts an owned FastAPI/uvicorn process on a dynamic non-production port,
uses a deterministic stream CLI (and, for the Steer case, a deterministic
control-wire/receipt fixture), and talks through real HTTP and /ws/agent.
No real LLM, practical service, 8767/8768 listener, browser, or user data is
involved.  The fixture proves Pan's routing/identity layer; it is not a real
provider acceptance test.
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

from packages.jobs.window import command_window_options

ROOT = Path(__file__).resolve().parent.parent
SERVER_LAUNCHER = Path(__file__).resolve().parent / "support" / "isolated_http_server.py"
FAKE_CLI = Path(__file__).resolve().parent / "support" / "fake_stream_cli.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    return port


def _wait_for(predicate, *, timeout: float = 20.0, label: str = "condition"):
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
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {label}: {last!r}")


class _Events:
    EVENT_TYPES = [
        "queue.item_added", "queue.item_delivered", "queue.snapshot",
        "worker.spawned", "worker.restarted", "worker.status",
    ]

    def __init__(self, url: str):
        self.url = url
        self.events: list[dict] = []
        self.ready = threading.Event()
        self.stop_requested = threading.Event()
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, name="auto-force-ws", daemon=True)

    def start(self):
        self.thread.start()
        _wait_for(self.ready.is_set, label="WebSocket subscription")

    def _run(self):
        try:
            with ws_connect(self.url, open_timeout=10, close_timeout=2) as ws:
                ws.send(json.dumps({"type": "subscribe", "eventTypes": self.EVENT_TYPES}))
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
                        self.events.append(event)
        except (ConnectionClosed, OSError) as exc:
            if not self.stop_requested.is_set():
                self.error = exc
        except Exception as exc:
            self.error = exc
        finally:
            self.ready.set()

    def stop(self):
        self.stop_requested.set()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "WebSocket recorder did not stop"
        if self.error:
            raise self.error

    def find_added(self, session_id: str, text: str) -> dict | None:
        for event in self.events:
            if event.get("type") != "queue.item_added":
                continue
            if event.get("sessionId") != session_id:
                continue
            item = event.get("item") or {}
            if item.get("text") == text:
                return event
        return None

    def delivered_ids(self, session_id: str) -> list[str]:
        result = []
        for event in self.events:
            if event.get("type") == "queue.item_delivered" and event.get("sessionId") == session_id:
                result.extend(event.get("queueItemIds") or [])
        return result

    def count(self, event_type: str, session_id: str | None = None) -> int:
        return sum(
            1 for event in self.events
            if event.get("type") == event_type
            and (session_id is None or event.get("sessionId") == session_id)
        )


class _Runtime:
    def __init__(self, data_root: Path, *, steer: bool):
        self.data_root = data_root
        self.steer = steer
        self.port = _free_port()
        self.log_path = data_root / "fake-cli.jsonl"
        self.gates: dict[str, Path] = {}
        self.process: subprocess.Popen | None = None
        self.pids: list[int] = []
        self._owned: list[tuple[int, float, str]] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def gate(self, token: str) -> Path:
        path = self.data_root / f"release-{token}"
        self.gates[token] = path
        return path

    def start(self):
        self.data_root.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env.update({
            "PAN_E2E_DATA_ROOT": str(self.data_root),
            "PAN_E2E_FAKE_CLI": str(FAKE_CLI),
            "PAN_E2E_FAKE_LOG": str(self.log_path),
            "PAN_E2E_GATE_MAP": json.dumps({k: str(v) for k, v in self.gates.items()}),
            "PAN_TEST_DISABLE_QUEUE_WATCHDOG": "1",
            "PAN_AGENT_SESSION_ID": "",
        })
        if self.steer:
            env["PAN_E2E_FAKE_STEER"] = "1"
            env["PAN_E2E_STEER_MODE"] = "1"
        log = open(self.data_root / "pan.log", "a", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER_LAUNCHER), "--port", str(self.port),
             "--data-root", str(self.data_root)],
            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
            **command_window_options(),
        )
        self.pids.append(self.process.pid)
        try:
            _wait_for(self._health_ok, timeout=25, label="isolated FastAPI readiness")
        except Exception:
            details = (self.data_root / "pan.log").read_text(
                encoding="utf-8", errors="replace")
            raise AssertionError(f"isolated server failed to start:\n{details[-5000:]}")
        finally:
            log.close()
        root = psutil.Process(self.process.pid)
        for process in [root, *root.children(recursive=True)]:
            try:
                self._owned.append((process.pid, process.create_time(),
                                    " ".join(process.cmdline())))
            except psutil.Error:
                continue
        self.pids.extend(pid for pid, _, _ in self._owned)

    def _health_ok(self) -> bool:
        try:
            response = httpx.get(self.base_url + "/api/health", timeout=0.5,
                                 trust_env=False, verify=False)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def stop(self):
        # Workers are spawned after server readiness. Capture those descendants
        # before stopping their parent, with the same PID creation-time fence.
        if self.process is not None:
            root_identity = next((created for pid, created, _ in self._owned
                                  if pid == self.process.pid), None)
            try:
                root = psutil.Process(self.process.pid)
                if (root_identity is not None
                        and abs(root.create_time() - root_identity) <= 0.01):
                    known = {(pid, created) for pid, created, _ in self._owned}
                    for child in root.children(recursive=True):
                        identity = (child.pid, child.create_time())
                        if identity not in known:
                            self._owned.append((*identity, " ".join(child.cmdline())))
            except psutil.Error:
                pass
        for pid, created, command in reversed(self._owned):
            try:
                process = psutil.Process(pid)
                if abs(process.create_time() - created) > 0.01:
                    continue
                if not any(marker in command for marker in (
                        str(SERVER_LAUNCHER), str(FAKE_CLI), "background_runner")):
                    continue
                process.terminate()
            except psutil.Error:
                continue
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for pid, created, command in reversed(self._owned):
            try:
                process = psutil.Process(pid)
                if abs(process.create_time() - created) <= 0.01 and process.is_running():
                    if any(marker in command for marker in (
                            str(SERVER_LAUNCHER), str(FAKE_CLI), "background_runner")):
                        process.kill()
            except psutil.Error:
                continue
        self._owned.clear()

    def request(self, method: str, path: str, **kwargs) -> dict:
        with httpx.Client(base_url=self.base_url, timeout=20,
                          trust_env=False, verify=False) as client:
            response = client.request(method, path, **kwargs)
        assert response.status_code == 200, (
            f"{method} {path}: {response.status_code} {response.text}")
        return response.json()

    def records(self, kind: str | None = None) -> list[dict]:
        if not self.log_path.exists():
            return []
        result = []
        for line in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if kind is None or record.get("kind") == kind:
                result.append(record)
        return result


@pytest.fixture
def make_runtime(tmp_path):
    runtimes: list[_Runtime] = []

    def build(*, steer: bool):
        runtime = _Runtime(tmp_path / ("steer" if steer else "restart"), steer=steer)
        # Register gates before the CLI/server start so the env map is complete.
        runtime.gates = {
            "hold-auto": runtime.data_root / "release-hold-auto",
            "hold-report": runtime.data_root / "release-hold-report",
            "hold-restart": runtime.data_root / "release-hold-restart",
            "hold-job": runtime.data_root / "release-hold-job",
        }
        runtime.start()
        runtimes.append(runtime)
        return runtime

    yield build
    for runtime in reversed(runtimes):
        runtime.stop()


def _events(runtime: _Runtime) -> _Events:
    recorder = _Events(runtime.base_url.replace("http", "ws", 1) + "/ws/agent")
    recorder.start()
    return recorder


def _create(runtime: _Runtime, name: str) -> dict:
    return runtime.request("POST", "/api/sessions", json={
        "name": name,
        "adapter": "cbc",
        "model": "deepseek-v4-flash",
        "permissionMode": "bypassPermissions",
        "outputMode": "stream",
    })


def _spawn(runtime: _Runtime, session_id: str) -> dict:
    return runtime.request("POST", "/api/spawn", json={"sessionId": session_id})


def _send(runtime: _Runtime, session_id: str, text: str, **extra) -> dict:
    body = {"sessionId": session_id, "text": text, "source": "user", **extra}
    return runtime.request("POST", "/api/send", json=body)


def _claim(runtime: _Runtime, manager: str, child: str) -> dict:
    return runtime.request("POST", "/api/claim",
                           json={"managerId": manager, "sessionId": child})


def _enable(runtime: _Runtime, **flags: bool) -> None:
    values = {"system": False, "ma": False, "report": False, **flags}
    response = runtime.request("PUT", "/api/settings/ui",
                               json={"autoForceSend": values})
    assert response["autoForceSend"] == values


def _wait_received(runtime: _Runtime, text: str, *, timeout: float = 20.0) -> list[dict]:
    return _wait_for(
        lambda: [record for record in runtime.records("received")
                 if record.get("text") == text],
        timeout=timeout, label=f"fake CLI received {text!r}")


def _steer_texts(runtime: _Runtime) -> list[str]:
    return [record.get("text") or "" for record in runtime.records("steer")]


@pytest.mark.timeout(150)
def test_real_ma_auto_force_steers_original_item_without_restart(make_runtime):
    runtime = make_runtime(steer=True)
    recorder = _events(runtime)
    try:
        ma = _create(runtime, "ma-steer")
        target = _create(runtime, "target-steer")
        _claim(runtime, ma["id"], target["id"])
        _spawn(runtime, target["id"])
        _send(runtime, target["id"], "hold-auto")
        _wait_received(runtime, "hold-auto")
        # The fixture emits its run identity immediately after reading the
        # message; let Pan process that frame before the Steer hand-off.
        time.sleep(0.4)
        _enable(runtime, ma=True)

        response = _send(runtime, target["id"], "MA urgent", source="agent",
                         sourceSessionId=ma["id"])
        assert response["status"] == "queued", response
        added = _wait_for(lambda: recorder.find_added(target["id"], "MA urgent"),
                          label="MA queue item_added")
        _wait_for(lambda: "MA urgent" in " ".join(_steer_texts(runtime)),
                  label="fixture Steer control")
        _wait_for(lambda: added["queueItemId"] in recorder.delivered_ids(target["id"]),
                  label="original queue item delivered")
        history = runtime.request("GET", f"/api/sessions/{target['id']}/history")
        rows = history.get("history") or history.get("messages") or history.get("rows") or []
        assert any(added["queueItemId"] in (row.get("queueItemIds") or [])
                   for row in rows), history
        # No second provider turn and no restart: one init, and the Steer body
        # never appears as an ordinary received user message.
        assert len([r for r in runtime.records("init")
                    if r.get("cwd") == target["workdir"]]) == 1
        assert not [r for r in runtime.records("received")
                    if r.get("text") == "MA urgent"]
        assert recorder.count("worker.restarted", target["id"]) == 0
    finally:
        recorder.stop()


@pytest.mark.timeout(150)
def test_real_report_auto_force_steers_manager_report(make_runtime):
    runtime = make_runtime(steer=True)
    recorder = _events(runtime)
    try:
        manager = _create(runtime, "manager-report")
        child = _create(runtime, "child-report")
        _claim(runtime, manager["id"], child["id"])
        subscribe = runtime.request("POST", "/api/report-subscribe",
                                    json={"managerId": manager["id"],
                                          "sessionId": child["id"]})
        assert subscribe["subscribed"] is True
        _spawn(runtime, manager["id"])
        _send(runtime, manager["id"], "hold-report")
        _wait_received(runtime, "hold-report")
        time.sleep(0.4)
        _enable(runtime, report=True)

        _spawn(runtime, child["id"])
        _send(runtime, child["id"], "child work")
        _wait_received(runtime, "child work")
        _wait_for(lambda: any("child work" in text for text in _steer_texts(runtime)),
                  timeout=30, label="report Steer control")
        assert recorder.count("worker.restarted", manager["id"]) == 0
        assert len([r for r in runtime.records("init")
                    if r.get("cwd") == manager["workdir"]]) == 1
    finally:
        recorder.stop()


@pytest.mark.timeout(150)
def test_real_idle_ma_message_uses_normal_send_without_steer(make_runtime):
    runtime = make_runtime(steer=True)
    recorder = _events(runtime)
    try:
        ma = _create(runtime, "ma-idle")
        target = _create(runtime, "target-idle")
        _claim(runtime, ma["id"], target["id"])
        _spawn(runtime, target["id"])
        _enable(runtime, ma=True)

        response = _send(runtime, target["id"], "idle urgent", source="agent",
                         sourceSessionId=ma["id"])
        assert response["status"] == "queued", response
        _wait_received(runtime, "idle urgent")
        assert not _steer_texts(runtime)
        assert recorder.count("worker.restarted", target["id"]) == 0
    finally:
        recorder.stop()


@pytest.mark.timeout(180)
def test_real_nonsteer_restart_moves_original_item_to_head(make_runtime):
    runtime = make_runtime(steer=False)
    recorder = _events(runtime)
    try:
        ma = _create(runtime, "ma-restart")
        target = _create(runtime, "target-restart")
        _claim(runtime, ma["id"], target["id"])
        _spawn(runtime, target["id"])
        _send(runtime, target["id"], "hold-restart")
        _wait_received(runtime, "hold-restart")
        # A pre-existing queued message stays behind the newly forced item.
        _send(runtime, target["id"], "older queued")
        _enable(runtime, ma=True)

        response = _send(runtime, target["id"], "urgent restart", source="agent",
                         sourceSessionId=ma["id"])
        assert response["status"] == "queued", response
        added = _wait_for(lambda: recorder.find_added(target["id"], "urgent restart"),
                          label="urgent queue item_added")
        _wait_for(lambda: added["queueItemId"] in recorder.delivered_ids(target["id"]),
                  timeout=30, label="forced item delivered after restart")
        _wait_for(lambda: len([
            r for r in runtime.records("received")
            if r.get("text") == "urgent restart"]) == 1,
            timeout=30, label="one forced provider turn")
        _wait_received(runtime, "older queued", timeout=30)
        order = [r.get("text") for r in runtime.records("received")
                 if r.get("text") in {"urgent restart", "older queued"}]
        assert order == ["urgent restart", "older queued"], order
        assert recorder.count("worker.restarted", target["id"]) >= 1
        assert len([r for r in runtime.records("received")
                    if r.get("text") == "urgent restart"]) == 1
    finally:
        recorder.stop()


@pytest.mark.timeout(180)
def test_real_job_terminal_auto_force_steers_system_notice(make_runtime):
    runtime = make_runtime(steer=True)
    recorder = _events(runtime)
    try:
        manager = _create(runtime, "manager-job")
        _spawn(runtime, manager["id"])
        _send(runtime, manager["id"], "hold-job")
        _wait_received(runtime, "hold-job")
        time.sleep(0.4)
        _enable(runtime, system=True)

        job = runtime.request("POST", "/api/background-jobs", json={
            "targetSessionId": manager["id"],
            "argv": [sys.executable, "-c", "print('job-ok')"],
            "cwd": manager["workdir"],
            "label": "auto-force-job",
        })
        assert job.get("jobId"), job
        _wait_for(lambda: any("background_job_terminal" in text
                              for text in _steer_texts(runtime)),
                  timeout=60, label="job terminal Steer control")
        assert recorder.count("worker.restarted", manager["id"]) == 0
    finally:
        recorder.stop()
