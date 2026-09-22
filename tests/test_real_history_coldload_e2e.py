"""BE-3 process-level E2E: cold history/list reads must not freeze the loop.

This is deliberately not a TestClient or mocked test.  It starts a real
FastAPI/uvicorn Pan process on the isolated port 8767 with its own data root,
seeds a large real JSONL history fixture through the real Session store,
drives concurrent real HTTP cold reads (``GET /api/sessions`` and
``GET /api/sessions/{id}/history``) while a real dashboard WebSocket
(``/ws``) samples ping/pong heartbeat latency and a real agent WebSocket
(``/ws/agent``) observes worker stream events from a real (fake-provider)
worker.  Port 8768 is never touched.

Evidence (raw heartbeat samples, per-request latencies, process tree, paths) is
printed as ``REAL_HTTP_HISTORY_COLDLOAD_E2E <json>`` and also written to the
data root so the run can be audited after the fact.
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


ROOT = Path(__file__).resolve().parent.parent
SERVER_LAUNCHER = Path(__file__).resolve().parent / "support" / "isolated_http_server.py"
FAKE_CLI = Path(__file__).resolve().parent / "support" / "fake_stream_cli.py"
HEARTBEAT_PROBE = Path(__file__).resolve().parent / "support" / "ws_heartbeat_probe.py"
PORT = 8767
COMMIT = "591367a65f88e9d5270e8e99920ef5448d1d68c9"

LARGE_SESSION = "ses-e2e-coldload-large"
LARGE_ROWS = 60_000
BULK_SESSIONS = 24
BULK_ROWS = 2_500
WINDOW_SECONDS = 4.0
# Concurrent cold-read clients.  Overridable so the same E2E can separate
# "how long does one cold read block the loop" from "how does a concurrent
# cold-read storm affect the loop".
BURST_WORKERS = int(os.environ.get("BE3_E2E_BURST_WORKERS", "8"))
HEARTBEAT_INTERVAL = 0.02
MAX_STALL_SECONDS = 0.05


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
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}: {last!r}")


def _rows(count: int, *, start: int = 0, tag: str = "row") -> list[dict]:
    return [
        {
            "role": "assistant" if index % 2 else "user",
            "content": f"{tag}-{index:07d}",
        }
        for index in range(start, start + count)
    ]


def _seed_fixture(session_dir: Path) -> dict:
    """Write the fixture through the real Session store writers."""
    from packages.core import session as _sess

    session_dir.mkdir(parents=True, exist_ok=True)
    previous_dir = _sess.SESSION_DIR
    _sess.SESSION_DIR = session_dir
    _sess._cache.clear()
    _sess._all_loaded = False
    started = time.time()
    try:
        seeded = {}
        for index in range(BULK_SESSIONS):
            session_id = f"ses-e2e-bulk-{index:02d}"
            value = _sess.Session(
                id=session_id, name=session_id, adapter="cbc", model="test-model",
            )
            _sess.replace_history(value, _rows(BULK_ROWS, tag="bulk"))
            _sess._cache[value.id] = value
            _sess.save_full(value)
            seeded[session_id] = BULK_ROWS
        large = _sess.Session(
            id=LARGE_SESSION, name=LARGE_SESSION, adapter="cbc", model="test-model",
        )
        _sess.replace_history(large, _rows(LARGE_ROWS, tag="large"))
        _sess._cache[large.id] = large
        _sess.save_full(large)
        seeded[LARGE_SESSION] = LARGE_ROWS
    finally:
        _sess.SESSION_DIR = previous_dir
        _sess._cache.clear()
        _sess._all_loaded = False
    total_rows = sum(seeded.values())
    return {
        "sessions": seeded,
        "totalRows": total_rows,
        "bytes": sum(
            path.stat().st_size for path in session_dir.iterdir() if path.is_file()
        ),
        "seedSeconds": round(time.time() - started, 3),
    }


class _HeartbeatProbe:
    """Real dashboard WebSocket: measure ping→pong round trip and cadence."""

    def __init__(self, url: str, *, interval: float = HEARTBEAT_INTERVAL):
        self.url = url
        self.interval = interval
        self.samples: list[dict] = []
        self.ready = threading.Event()
        self.stop_requested = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="pan-e2e-heartbeat", daemon=True)
        self.error: Exception | None = None

    def start(self):
        self.thread.start()
        assert self.ready.wait(timeout=10), "heartbeat probe did not connect"

    def _run(self):
        try:
            with ws_connect(self.url, open_timeout=10, close_timeout=2) as ws:
                self.ready.set()
                last_pong = None
                while not self.stop_requested.is_set():
                    sent = time.perf_counter()
                    ws.send(json.dumps({"type": "ping"}))
                    while True:
                        raw = ws.recv(timeout=5)
                        if json.loads(raw).get("type") == "pong":
                            break
                    now = time.perf_counter()
                    self.samples.append({
                        "at": time.time(),
                        "rttMs": round((now - sent) * 1000.0, 3),
                        "gapMs": None if last_pong is None else round(
                            (now - last_pong) * 1000.0, 3),
                    })
                    last_pong = now
                    self.stop_requested.wait(self.interval)
        except Exception as exc:  # surfaced by stop()
            self.error = exc
        finally:
            self.ready.set()

    def stop(self):
        self.stop_requested.set()
        self.thread.join(timeout=8)
        assert not self.thread.is_alive(), "heartbeat probe did not stop"

    def window(self, start: float, end: float) -> list[dict]:
        return [s for s in self.samples if start <= s["at"] <= end]


class _AgentEventRecorder:
    """Real agent WebSocket: timestamp worker stream/result events."""

    EVENT_TYPES = ["worker.stream", "worker.result", "worker.status"]

    def __init__(self, url: str):
        self.url = url
        self.events: list[dict] = []
        self.ready = threading.Event()
        self.stop_requested = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="pan-e2e-agent-ws", daemon=True)
        self.error: Exception | None = None

    def start(self):
        self.thread.start()
        assert self.ready.wait(timeout=10), "agent WebSocket did not subscribe"

    def _run(self):
        try:
            with ws_connect(self.url, open_timeout=10, close_timeout=2) as ws:
                ws.send(json.dumps({
                    "type": "subscribe", "eventTypes": self.EVENT_TYPES,
                }))
                while not self.stop_requested.is_set():
                    try:
                        raw = ws.recv(timeout=0.2)
                    except TimeoutError:
                        continue
                    event = json.loads(raw)
                    if event.get("type") == "subscribed":
                        self.ready.set()
                    elif event.get("type") in self.EVENT_TYPES:
                        self.events.append({
                            "at": time.time(),
                            "type": event.get("type"),
                            "sessionId": event.get("sessionId"),
                            "eventType": (event.get("event") or {}).get("type")
                            if isinstance(event.get("event"), dict) else None,
                        })
        except Exception as exc:
            self.error = exc
        finally:
            self.ready.set()

    def stop(self):
        self.stop_requested.set()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "agent recorder did not stop"
        if self.error:
            raise self.error


class _OutOfProcessHeartbeatProbe:
    """Server-side-attributable heartbeat: its own process, its own GIL.

    The in-process probe below shares the pytest process with the burst
    clients, so its samples can include client-side scheduling noise.  This
    probe runs as a separate process and therefore only measures how fast the
    Pan event loop answers a dashboard ping.
    """

    def __init__(self, url: str, data_root: Path, *, name: str = "heartbeat"):
        self.url = url
        self.data_root = data_root
        self.name = name
        self.out = data_root / f"{name}-samples.json"
        self.ready = data_root / f"{name}-ready"
        self.release = data_root / f"{name}-release"
        self.process: subprocess.Popen | None = None

    def start(self, *, timeout: float = 120.0):
        for path in (self.out, self.ready, self.release):
            path.unlink(missing_ok=True)
        log = open(self.data_root / f"{self.name}.log", "a", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(HEARTBEAT_PROBE),
                 "--url", self.url, "--out", str(self.out),
                 "--ready", str(self.ready), "--release", str(self.release),
                 "--timeout", str(timeout)],
                cwd=str(ROOT),
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                stdout=log, stderr=subprocess.STDOUT,
            )
        finally:
            log.close()
        _wait_for(self.ready.exists, timeout=20, label=f"{self.name} probe ready")

    def stop(self) -> list[dict]:
        if self.process is None:
            return self.samples()
        self.release.write_text("stop", encoding="ascii")
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.process = None
        return self.samples()

    def samples(self) -> list[dict]:
        if not self.out.exists():
            return []
        return json.loads(self.out.read_text(encoding="utf-8"))

    @staticmethod
    def window(samples: list[dict], start: float, end: float) -> list[dict]:
        return [sample for sample in samples if start <= sample["at"] <= end]


def _latency_summary(samples: list[dict]) -> dict:
    rtts = [sample["rttMs"] for sample in samples]
    gaps = [sample["gapMs"] for sample in samples if sample["gapMs"] is not None]
    return {
        "samples": len(samples),
        "maxRttMs": max(rtts) if rtts else None,
        "p50RttMs": sorted(rtts)[len(rtts) // 2] if rtts else None,
        "p95RttMs": sorted(rtts)[int(len(rtts) * 0.95)] if rtts else None,
        "maxGapMs": max(gaps) if gaps else None,
    }


class _ColdLoadBurst:
    """Concurrent real HTTP cold reads, one thread per worker."""

    def __init__(self, base_url: str, *, workers: int = BURST_WORKERS):
        self.base_url = base_url
        self.workers = workers
        self.results: list[dict] = []
        self.stop_requested = threading.Event()
        self.threads: list[threading.Thread] = []
        self.error: Exception | None = None

    def start(self):
        for index in range(self.workers):
            thread = threading.Thread(
                target=self._run, args=(index,),
                name=f"pan-e2e-cold-{index}", daemon=True)
            self.threads.append(thread)
            thread.start()

    def _run(self, index: int):
        # Alternate the two dashboard cold-load endpoints.
        bulk_id = f"ses-e2e-bulk-{index % BULK_SESSIONS:02d}"
        try:
            with httpx.Client(
                base_url=self.base_url, timeout=30, trust_env=False, verify=False,
            ) as client:
                while not self.stop_requested.is_set():
                    if index % 2 == 0:
                        path = "/api/sessions"
                    else:
                        path = f"/api/sessions/{LARGE_SESSION}/history?before=0&limit=50"
                    started = time.perf_counter()
                    response = client.get(path)
                    elapsed = (time.perf_counter() - started) * 1000.0
                    self.results.append({
                        "at": time.time(),
                        "worker": index,
                        "path": path,
                        "status": response.status_code,
                        "ms": round(elapsed, 3),
                        "sessionId": None if index % 2 == 0 else LARGE_SESSION,
                        "probeSession": bulk_id,
                    })
        except Exception as exc:
            self.error = exc

    def stop(self):
        self.stop_requested.set()
        for thread in self.threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "cold-load burst thread did not stop"
        if self.error:
            raise self.error

    def window(self, start: float, end: float) -> list[dict]:
        return [r for r in self.results if start <= r["at"] <= end]


class _PanRuntime:
    """Own exactly the isolated Pan process(es) started by this test."""

    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.log_path = data_root / "fake-cli.jsonl"
        self.process: subprocess.Popen | None = None
        self.pan_pids: list[int] = []
        self.process_tree: list[dict] = []
        self._owned: list[tuple[int, float, str]] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{PORT}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{PORT}/ws"

    @property
    def agent_ws_url(self) -> str:
        return f"ws://127.0.0.1:{PORT}/ws/agent"

    @property
    def start_command(self) -> str:
        return (f"{sys.executable} {SERVER_LAUNCHER} --port {PORT} "
                f"--data-root {self.data_root}")

    def start(self):
        assert PORT != 8768, "the canonical 8768 service must never be used"
        assert _port_is_free(PORT), f"isolated port {PORT} is not free"
        self.data_root.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(ROOT) + (
            os.pathsep + existing_pythonpath if existing_pythonpath else "")
        env.update({
            "PAN_E2E_DATA_ROOT": str(self.data_root),
            "PAN_E2E_FAKE_CLI": str(FAKE_CLI),
            "PAN_E2E_FAKE_LOG": str(self.log_path),
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
                timeout=30,
                label="isolated FastAPI readiness",
            )
        except Exception:
            details = (self.data_root / "pan.log").read_text(
                encoding="utf-8", errors="replace")
            raise AssertionError(f"isolated server failed to start:\n{details[-4000:]}")
        finally:
            log.close()
        root_process = psutil.Process(self.process.pid)
        owned = [root_process, *root_process.children(recursive=True)]
        for process in owned:
            try:
                command_line = " ".join(process.cmdline())
                self._owned.append((process.pid, process.create_time(), command_line))
                self.process_tree.append({
                    "pid": process.pid,
                    "createTime": process.create_time(),
                    "commandLine": command_line,
                })
            except psutil.Error:
                continue
        self.pan_pids.extend(
            process.pid for process in owned if process.pid not in self.pan_pids)

    def _health_ok(self) -> bool:
        try:
            response = httpx.get(
                self.base_url + "/api/health", timeout=0.5,
                trust_env=False, verify=False)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def stop(self) -> dict:
        process = self.process
        self.process = None
        if process is None and not self._owned:
            return {"terminated": [], "portReleased": _port_is_free(PORT)}
        terminated: list[int] = []
        # Stop only the exact PIDs/create-times captured from this fixture's
        # process tree; never scan or kill a generic python/uvicorn process.
        for pid, create_time, command_line in reversed(self._owned):
            try:
                owned = psutil.Process(pid)
                if abs(owned.create_time() - create_time) > 0.01:
                    continue
                if str(SERVER_LAUNCHER) not in command_line and str(FAKE_CLI) not in command_line:
                    continue
                owned.terminate()
                terminated.append(pid)
            except psutil.Error:
                continue
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        lingering: list[int] = []
        for pid, create_time, command_line in reversed(self._owned):
            try:
                owned = psutil.Process(pid)
                if abs(owned.create_time() - create_time) <= 0.01 and owned.is_running():
                    if str(SERVER_LAUNCHER) in command_line or str(FAKE_CLI) in command_line:
                        owned.kill()
                        lingering.append(pid)
            except psutil.Error:
                continue
        self._owned.clear()
        released = _wait_for(
            lambda: _port_is_free(PORT), timeout=10, label="isolated port release")
        return {
            "terminated": terminated,
            "forceKilled": lingering,
            "portReleased": bool(released),
        }

    def request(self, method: str, path: str, **kwargs) -> dict:
        with httpx.Client(
            base_url=self.base_url, timeout=30, trust_env=False, verify=False,
        ) as client:
            response = client.request(method, path, **kwargs)
        assert response.status_code == 200, (
            f"{method} {path}: {response.status_code} {response.text[:400]}")
        return response.json()


@pytest.fixture
def coldload_runtime(tmp_path, monkeypatch):
    data_root = tmp_path / "persistent-data-root"
    fixture = _seed_fixture(data_root / "sessions")
    runtime = _PanRuntime(data_root)
    runtime.fixture = fixture
    runtime.start()
    # Server-attributable heartbeat (own process) plus an in-process probe and
    # agent stream recorder sharing the pytest process for comparison.
    probe = _OutOfProcessHeartbeatProbe(runtime.ws_url, data_root)
    heartbeat = _HeartbeatProbe(runtime.ws_url)
    agent = _AgentEventRecorder(runtime.agent_ws_url)
    probe.start()
    heartbeat.start()
    agent.start()
    try:
        yield runtime, heartbeat, agent, probe
    finally:
        probe.stop()
        heartbeat.stop()
        agent.stop()
        runtime.stop()


def _longest_over_target_run(samples: list[dict], target_ms: float) -> int:
    """Longest run of consecutive heartbeats whose gap exceeded ``target_ms``.

    A single slow heartbeat is scheduler noise (the probe itself paces at 20ms
    and this is a shared Windows host).  Consecutive slow heartbeats are what
    "a sustained stall" means: the loop stayed unresponsive across several
    probe intervals.
    """
    longest = current = 0
    for sample in samples:
        gap = sample["gapMs"]
        if gap is not None and gap > target_ms:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _burst_evidence(results: list[dict], in_process: list[dict],
                    server: list[dict], started: float, ended: float,
                    *, extra: dict | None = None) -> dict:
    """Summarize one measurement window with its raw heartbeat samples."""
    request_ms = [result["ms"] for result in results]
    server_gaps = [sample["gapMs"] for sample in server
                   if sample["gapMs"] is not None]
    record = {
        "startedAt": started,
        "endedAt": ended,
        "seconds": round(ended - started, 3),
        "requests": len(results),
        "listRequests": sum(1 for r in results if r["path"] == "/api/sessions"),
        "historyRequests": sum(1 for r in results if r["path"] != "/api/sessions"),
        "allStatus200": bool(results) and all(r["status"] == 200 for r in results),
        "p50RequestMs": sorted(request_ms)[len(request_ms) // 2] if request_ms else None,
        "maxRequestMs": max(request_ms) if request_ms else None,
        "server": _latency_summary(server),
        "inProcess": _latency_summary(in_process),
        "gapsOverTarget": [
            gap for gap in server_gaps if gap > MAX_STALL_SECONDS * 1000],
        "longestOverTargetRun": _longest_over_target_run(
            server, MAX_STALL_SECONDS * 1000),
        "rawServerHeartbeat": server,
    }
    if extra:
        record.update(extra)
    return record


def _write_evidence(runtime: "_PanRuntime", evidence: dict) -> Path:
    path = runtime.data_root / "be3-e2e-evidence.json"
    path.write_text(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8")
    evidence["evidencePath"] = str(path)
    return path


@pytest.mark.timeout(300)
def test_real_http_history_coldload_does_not_stall_dashboard_websocket(coldload_runtime):
    runtime, heartbeat, agent, probe = coldload_runtime
    fixture = runtime.fixture
    evidence: dict = {
        "task": "T-FRONTEND-COLDLOAD-BE3-20260921",
        "worktree": str(ROOT),
        "commit": COMMIT,
        "port": PORT,
        "dataRoot": str(runtime.data_root),
        "startCommand": runtime.start_command,
        "panPids": runtime.pan_pids,
        "processTree": runtime.process_tree,
        "fixture": fixture,
        "burstWorkers": BURST_WORKERS,
        "serverHeartbeatSamplesFile": str(probe.out),
        "maxStallTargetMs": MAX_STALL_SECONDS * 1000,
    }

    # ── baseline: no concurrent cold load ──
    baseline_start = time.time()
    time.sleep(1.5)
    baseline_end = time.time()
    baseline_samples = heartbeat.window(baseline_start, baseline_end)
    baseline_probe = _OutOfProcessHeartbeatProbe.window(
        probe.samples(), baseline_start, baseline_end)
    assert len(baseline_samples) >= 10, f"heartbeat too sparse: {baseline_samples}"
    assert len(baseline_probe) >= 10, f"probe too sparse: {baseline_probe}"
    evidence["baseline"] = {
        "server": _latency_summary(baseline_probe),
        "inProcess": _latency_summary(baseline_samples),
        "rawServer": baseline_probe,
    }

    # ── burst window 1: the first list read also loads the store index ──
    cold_burst = _ColdLoadBurst(runtime.base_url)
    cold_start = time.time()
    cold_burst.start()
    time.sleep(0.2)
    # A real worker produces a real stream while the cold reads are in flight.
    created = runtime.request("POST", "/api/sessions", json={
        "name": "e2e-stream-during-coldload", "adapter": "cbc",
        "model": "deepseek-v4-flash", "permissionMode": "bypassPermissions",
        "outputMode": "stream",
    })
    stream_session = created["id"]
    evidence["streamSessionId"] = stream_session
    runtime.request("POST", "/api/spawn", json={"sessionId": stream_session})
    stream_enqueued_at = time.time()
    runtime.request(
        "POST", f"/api/sessions/{stream_session}/queue",
        json={"text": "coldload-window-mark", "clientMessageId": "cm-coldload-e2e"},
    )
    _wait_for(
        lambda: any(
            event["sessionId"] == stream_session
            and event["type"] in {"worker.stream", "worker.result"}
            for event in agent.events),
        timeout=30,
        label="worker stream event during the cold-load window",
    )
    stream_event_at = time.time()
    time.sleep(max(0.0, WINDOW_SECONDS - (time.time() - cold_start)))
    cold_end = time.time()
    cold_burst.stop()

    # ── burst window 2: store index loaded, but every history read still
    # streams the companion JSONL from disk (shallow pages never hydrate) ──
    steady_burst = _ColdLoadBurst(runtime.base_url)
    steady_start = time.time()
    steady_burst.start()
    time.sleep(0.3)
    steady_enqueued_at = time.time()
    runtime.request(
        "POST", f"/api/sessions/{stream_session}/queue",
        json={"text": "steady-window-mark", "clientMessageId": "cm-steady-e2e"},
    )
    _wait_for(
        lambda: any(
            event["sessionId"] == stream_session
            and event["eventType"] == "assistant"
            and event["at"] >= steady_enqueued_at
            for event in agent.events),
        timeout=30,
        label="worker stream event during the steady-state window",
    )
    steady_event_at = time.time()
    time.sleep(max(0.0, WINDOW_SECONDS - (time.time() - steady_start)))
    steady_end = time.time()
    steady_burst.stop()

    server_samples = _OutOfProcessHeartbeatProbe.window(
        probe.stop(), cold_start, steady_end)
    evidence["windows"] = {
        "coldStartIndexLoad": _burst_evidence(
            cold_burst.window(cold_start, cold_end),
            heartbeat.window(cold_start, cold_end),
            _OutOfProcessHeartbeatProbe.window(server_samples, cold_start, cold_end),
            cold_start, cold_end,
            extra={
                "streamEnqueuedAt": stream_enqueued_at,
                "streamEventAt": stream_event_at,
                "streamEventDuringWindow": cold_start <= stream_event_at <= cold_end,
                "streamEvents": [
                    event for event in agent.events
                    if event["sessionId"] == stream_session
                ],
            }),
        "steadyStateColdReads": _burst_evidence(
            steady_burst.window(steady_start, steady_end),
            heartbeat.window(steady_start, steady_end),
            _OutOfProcessHeartbeatProbe.window(server_samples, steady_start, steady_end),
            steady_start, steady_end,
            extra={
                "streamEnqueuedAt": steady_enqueued_at,
                "streamEventAt": steady_event_at,
                "streamEventDuringWindow": steady_start <= steady_event_at <= steady_end,
            }),
    }
    evidence["saveDiagnostics"] = runtime.request(
        "GET", "/api/diagnostics/persistence")
    # Persist the latency evidence now so a later failure still leaves the raw
    # samples on disk for auditing.
    _write_evidence(runtime, evidence)

    cold_window = evidence["windows"]["coldStartIndexLoad"]
    steady_window = evidence["windows"]["steadyStateColdReads"]

    # ── response content + ordering ──
    listed = runtime.request("GET", "/api/sessions")
    listing = {item["id"]: item for item in listed["sessions"]}
    assert set(listing) >= set(fixture["sessions"]), (
        f"missing sessions: {set(fixture['sessions']) - set(listing)}")
    large_item = listing[LARGE_SESSION]
    assert large_item["historyTotal"] == LARGE_ROWS
    assert large_item["historyTruncated"] is True
    assert large_item["historyStart"] == LARGE_ROWS - 50
    assert [row["content"] for row in large_item["history"]] == [
        row["content"] for row in _rows(50, start=LARGE_ROWS - 50, tag="large")
    ]
    for index in range(BULK_SESSIONS):
        session_id = f"ses-e2e-bulk-{index:02d}"
        assert listing[session_id]["historyTotal"] == BULK_ROWS, session_id
        assert listing[session_id]["historyStart"] == BULK_ROWS - 50, session_id
        assert len(listing[session_id]["history"]) == 50, session_id

    for before, limit, expected_start in (
        (0, 50, LARGE_ROWS - 50),
        (30_000, 20, 29_980),
        (200, 200, 0),
    ):
        page = runtime.request(
            "GET",
            f"/api/sessions/{LARGE_SESSION}/history?before={before}&limit={limit}")
        assert page["total"] == LARGE_ROWS
        assert page["start"] == expected_start
        assert page["hasMore"] is (expected_start > 0)
        assert [row["content"] for row in page["history"]] == [
            row["content"] for row in _rows(
                min(limit, LARGE_ROWS - expected_start),
                start=expected_start, tag="large")
        ]
    evidence["contentChecks"] = {
        "sessionCount": len(listing),
        "largeHistoryTotal": large_item["historyTotal"],
        "largeTailFirst": large_item["history"][0]["content"],
        "largeTailLast": large_item["history"][-1]["content"],
    }

    # ── connection close + recovery ──
    heartbeat.stop()
    agent.stop()
    assert runtime.request("GET", "/api/health")["status"] == "ok"
    recovered = _HeartbeatProbe(runtime.ws_url)
    recovered.start()
    time.sleep(0.4)
    recovered.stop()
    assert len(recovered.samples) >= 3, "reconnected dashboard socket is not live"
    evidence["recovery"] = {
        "healthAfterClose": True,
        "reconnectedSamples": len(recovered.samples),
        "reconnectedMaxRttMs": max(s["rttMs"] for s in recovered.samples),
    }

    # Heartbeat cadence: no sustained >50ms pause while history/list reads ran.
    for label, window in (("coldStartIndexLoad", cold_window),
                          ("steadyStateColdReads", steady_window)):
        assert window["allStatus200"], f"{label}: a cold read failed"
        assert window["requests"] >= 6, (
            f"{label} did not exercise the cold path enough: {window['requests']}")
        assert window["listRequests"] >= 2 and window["historyRequests"] >= 2, (
            f"{label} did not cover both endpoints: {window}")
        assert window["server"]["samples"] >= 10, (
            f"{label} server heartbeat produced too few samples")
        assert window["server"]["p95RttMs"] < MAX_STALL_SECONDS * 1000, (
            f"{label}: p95 heartbeat RTT {window['server']['p95RttMs']}ms "
            f"shows a sustained stall (baseline p95 "
            f"{evidence['baseline']['server']['p95RttMs']}ms)")
        assert window["streamEventDuringWindow"], (
            f"{label}: no real worker stream event during the read storm")
    # The steady window is pure per-request cold history reads: nothing else is
    # loading, so the documented target ("no sustained >50ms stall") applies
    # without qualification.  p95 is the primary discriminator; the run length
    # is what makes a stall "sustained" rather than one late heartbeat.
    assert steady_window["longestOverTargetRun"] < 2, (
        "dashboard WebSocket heartbeat stalled for "
        f"{steady_window['longestOverTargetRun']} consecutive heartbeats beyond "
        f"{MAX_STALL_SECONDS * 1000:.0f}ms during steady-state cold reads: "
        f"{steady_window['gapsOverTarget'][:5]}ms (baseline max gap "
        f"{evidence['baseline']['server']['maxGapMs']}ms, "
        f"{BURST_WORKERS} concurrent clients)")
    assert (steady_window["server"]["maxGapMs"] or 0) < 100, (
        "one severe heartbeat gap during steady-state cold reads: "
        f"{steady_window['gapsOverTarget'][:5]}")
    assert (cold_window["server"]["maxGapMs"] or 0) < 250, (
        "one-time store index load froze the loop: "
        f"{cold_window['gapsOverTarget'][:5]}")

    # ── process cleanup ──
    cleanup = runtime.stop()
    evidence["cleanup"] = cleanup
    assert cleanup["portReleased"], "isolated port was not released"
    for entry in runtime.process_tree:
        try:
            process = psutil.Process(entry["pid"])
            running = (
                abs(process.create_time() - entry["createTime"]) <= 0.01
                and process.is_running()
            )
        except psutil.Error:
            running = False
        assert not running, f"owned process still running: {entry}"
    evidence["cleanup"]["ownedProcessesGone"] = True

    _write_evidence(runtime, evidence)
    print("REAL_HTTP_HISTORY_COLDLOAD_E2E "
          + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
