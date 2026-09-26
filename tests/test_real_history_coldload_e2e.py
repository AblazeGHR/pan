"""BE-3 process-level E2E: cold history/list reads must not freeze the loop.

This is deliberately not a TestClient or mocked test.  It starts a real
FastAPI/uvicorn Pan process on an ephemeral isolated loopback port with its own data root,
seeds a large real JSONL history fixture through the real Session store,
drives concurrent real HTTP cold reads (``GET /api/sessions`` and
``GET /api/sessions/{id}/history``) while a real dashboard WebSocket
(``/ws``) samples ping/pong heartbeat latency and a real agent WebSocket
(``/ws/agent``) observes worker stream events from a real (fake-provider)
worker.  Protected ports 8767 and 8768 are never touched.

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
PROTECTED_PORTS = frozenset({8767, 8768})

LARGE_SESSION = "ses-e2e-coldload-large"
LARGE_ROWS = 60_000
BULK_SESSIONS = 24
BULK_ROWS = 2_500
WINDOW_SECONDS = 4.0
BURST_WORKERS = 8
HEARTBEAT_INTERVAL = 0.02
MAX_STALL_SECONDS = 0.05


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _allocate_isolated_port() -> int:
    """Choose an ephemeral loopback port, never the protected Pan ports."""
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if (port not in PROTECTED_PORTS and _port_is_free(port)
                and not _listening_pids(port)):
            return port
    raise RuntimeError("could not allocate an empty isolated E2E port")


def _listening_pids(port: int) -> list[int]:
    return sorted({
        connection.pid
        for connection in psutil.net_connections(kind="tcp")
        if connection.status == psutil.CONN_LISTEN
        and connection.laddr
        and connection.laddr.port == port
        and connection.pid is not None
    })


def _repository_snapshot() -> dict:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short", "--untracked-files=no"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return {"commit": head, "trackedStatus": status}


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
        self.started = False
        self.thread = threading.Thread(
            target=self._run, name="pan-e2e-heartbeat", daemon=True)
        self.error: Exception | None = None

    def start(self):
        self.thread.start()
        self.started = True
        assert self.ready.wait(timeout=10), "heartbeat probe did not connect"

    def _run(self):
        try:
            with ws_connect(self.url, open_timeout=10, close_timeout=2) as ws:
                self.ready.set()
                last_pong = None
                next_send = None
                while not self.stop_requested.is_set():
                    sent = time.perf_counter()
                    probe_lateness_ms = (
                        max(0.0, (sent - next_send) * 1000.0)
                        if next_send is not None else 0.0
                    )
                    ws.send(json.dumps({"type": "ping"}))
                    while True:
                        raw = ws.recv(timeout=5)
                        if json.loads(raw).get("type") == "pong":
                            break
                    now = time.perf_counter()
                    gap_ms = None if last_pong is None else (now - last_pong) * 1000.0
                    server_gap_ms = (
                        None if gap_ms is None
                        else max(0.0, gap_ms - probe_lateness_ms)
                    )
                    next_send = now + self.interval
                    self.samples.append({
                        "at": time.time(),
                        "rttMs": round((now - sent) * 1000.0, 3),
                        "gapMs": None if gap_ms is None else round(gap_ms, 3),
                        "probeIntervalMs": round(self.interval * 1000.0, 3),
                        "probeLatenessMs": round(probe_lateness_ms, 3),
                        "serverGapMs": (
                            None if server_gap_ms is None
                            else round(server_gap_ms, 3)),
                    })
                    last_pong = now
                    self.stop_requested.wait(
                        max(0.0, next_send - time.perf_counter()))
        except Exception as exc:  # surfaced by stop()
            self.error = exc
        finally:
            self.ready.set()

    def stop(self):
        if not self.started:
            return
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
        self.started = False
        self.thread = threading.Thread(
            target=self._run, name="pan-e2e-agent-ws", daemon=True)
        self.error: Exception | None = None

    def start(self):
        self.thread.start()
        self.started = True
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
        if not self.started:
            return
        self.stop_requested.set()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "agent recorder did not stop"
        if self.error:
            raise self.error


class _OutOfProcessHeartbeatProbe:
    """Independent-process heartbeat with explicit client-side timing.

    The in-process probe below shares the pytest process with the burst
    clients, so its samples can include client-side scheduling noise.  This
    probe runs as a separate process and records both ping→pong RTT and delay
    before sending each scheduled ping. Raw inter-pong gaps remain available
    so server response delay and probe-side timing can be compared.
    """

    def __init__(self, url: str, data_root: Path, *, name: str = "heartbeat"):
        self.url = url
        self.data_root = data_root
        self.name = name
        self.out = data_root / f"{name}-samples.json"
        self.ready = data_root / f"{name}-ready"
        self.release = data_root / f"{name}-release"
        self.process: subprocess.Popen | None = None
        self.process_identity: dict | None = None
        self.diagnostics: dict | None = None

    def start(self, *, timeout: float = 120.0):
        for path in (self.out, self.ready, self.release):
            path.unlink(missing_ok=True)
        command = [sys.executable, str(HEARTBEAT_PROBE),
                   "--url", self.url, "--out", str(self.out),
                   "--ready", str(self.ready), "--release", str(self.release),
                   "--interval", str(HEARTBEAT_INTERVAL),
                   "--timeout", str(timeout)]
        self.process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        process = psutil.Process(self.process.pid)
        self.process_identity = {
            "pid": self.process.pid,
            "createTime": process.create_time(),
            "commandLine": " ".join(process.cmdline()),
        }
        try:
            _wait_for(
                lambda: self.ready.exists() or self.process.poll() is not None,
                timeout=20,
                label=f"{self.name} probe ready or exit",
            )
            if not self.ready.exists():
                raise RuntimeError(
                    f"probe exited before becoming ready (returncode={self.process.returncode})")
        except Exception as exc:
            diagnostic = self.stop()
            raise AssertionError(
                f"{self.name} probe failed to start: {exc}; "
                f"subprocess={diagnostic}") from exc

    def stop(self) -> dict:
        if self.diagnostics is not None:
            return {**self.diagnostics, "samples": self.samples()}
        process = self.process
        if process is None:
            self.diagnostics = {
                **(self.process_identity or {}),
                "returnCode": None,
                "stdout": "",
                "stderr": "",
                "samplesPath": str(self.out),
                "sampleCount": len(self.samples()),
                "terminatedByHarness": False,
            }
            return {**self.diagnostics, "samples": self.samples()}

        self.release.write_text("stop", encoding="ascii")
        terminated_by_harness = False
        try:
            stdout, stderr = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            terminated_by_harness = True
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
        self.process = None
        samples = self.samples()
        self.diagnostics = {
            **(self.process_identity or {"pid": process.pid}),
            "returnCode": process.returncode,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "samplesPath": str(self.out),
            "sampleCount": len(samples),
            "terminatedByHarness": terminated_by_harness,
        }
        return {**self.diagnostics, "samples": samples}

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
    server_gaps = [
        sample.get("serverGapMs", sample.get("gapMs"))
        for sample in samples
    ]
    server_gaps = [gap for gap in server_gaps if gap is not None]
    probe_lateness = [sample.get("probeLatenessMs", 0.0) for sample in samples]
    return {
        "samples": len(samples),
        "maxRttMs": max(rtts) if rtts else None,
        "p50RttMs": sorted(rtts)[len(rtts) // 2] if rtts else None,
        "p95RttMs": sorted(rtts)[int(len(rtts) * 0.95)] if rtts else None,
        "maxGapMs": max(gaps) if gaps else None,
        "maxServerGapMs": max(server_gaps) if server_gaps else None,
        "maxProbeLatenessMs": max(probe_lateness) if probe_lateness else None,
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
        self.port = _allocate_isolated_port()
        self.log_path = data_root / "fake-cli.jsonl"
        self.stdout_path = data_root / "pan.stdout.log"
        self.stderr_path = data_root / "pan.stderr.log"
        self.process: subprocess.Popen | None = None
        self.pan_pids: list[int] = []
        self.process_tree: list[dict] = []
        self._owned: list[tuple[int, float, str]] = []
        self.listener_pids: list[int] = []
        self.process_identity: dict | None = None
        self.diagnostics: dict | None = None
        self.evidence: dict | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    @property
    def agent_ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws/agent"

    @property
    def start_command(self) -> str:
        return (f"{sys.executable} {SERVER_LAUNCHER} --port {self.port} "
                f"--data-root {self.data_root}")

    def start(self):
        assert self.port not in PROTECTED_PORTS, (
            f"protected Pan port selected for isolated E2E: {self.port}")
        assert _port_is_free(self.port), (
            f"isolated port {self.port} is not free")
        assert not _listening_pids(self.port), (
            f"isolated port {self.port} already has a listener")
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
        command = [sys.executable, str(SERVER_LAUNCHER), "--port",
                   str(self.port), "--data-root", str(self.data_root)]
        with self.stdout_path.open("w", encoding="utf-8") as stdout, \
                self.stderr_path.open("w", encoding="utf-8") as stderr:
            self.process = subprocess.Popen(
                command, cwd=str(ROOT), env=env,
                stdout=stdout, stderr=stderr,
            )
        self.pan_pids.append(self.process.pid)
        self.process_identity = {
            "pid": self.process.pid,
            "createTime": psutil.Process(self.process.pid).create_time(),
            "commandLine": " ".join(command),
            "stdoutPath": str(self.stdout_path),
            "stderrPath": str(self.stderr_path),
        }
        try:
            _wait_for(
                lambda: self.process.poll() is None and self._health_ok(),
                timeout=30,
                label="isolated FastAPI readiness",
            )
        except Exception:
            stdout = self.stdout_path.read_text(
                encoding="utf-8", errors="replace")
            stderr = self.stderr_path.read_text(
                encoding="utf-8", errors="replace")
            raise AssertionError(
                "isolated server failed to start: "
                f"pid={self.process.pid} returncode={self.process.poll()} "
                f"stdout={stdout[-4000:]} stderr={stderr[-4000:]}")
        self._capture_process_tree()
        assert self.listener_pids, (
            f"no listener PID found for isolated port {self.port}")
        assert set(self.listener_pids).intersection(self.pan_pids), (
            f"port {self.port} listener {self.listener_pids} is not owned by "
            f"the fixture process tree {self.pan_pids}")

    def _capture_process_tree(self) -> None:
        """Track this fixture's current descendants by PID and creation time."""
        if self.process is None:
            return
        try:
            root_process = psutil.Process(self.process.pid)
            owned = [root_process, *root_process.children(recursive=True)]
        except psutil.Error:
            return
        snapshots = {
            (entry["pid"], entry["createTime"]): entry
            for entry in self.process_tree
        }
        for process in owned:
            try:
                command_line = " ".join(process.cmdline())
                create_time = process.create_time()
                listens_on_port = any(
                    connection.status == psutil.CONN_LISTEN
                    and connection.laddr
                    and connection.laddr.port == self.port
                    for connection in process.net_connections(kind="tcp")
                )
                entry = {
                    "pid": process.pid,
                    "parentPid": process.ppid(),
                    "createTime": create_time,
                    "commandLine": command_line,
                    "ownsIsolatedPort": listens_on_port,
                }
                snapshots[(process.pid, create_time)] = entry
                if (str(SERVER_LAUNCHER) in command_line
                        or str(FAKE_CLI) in command_line):
                    if not any(pid == process.pid and created == create_time
                               for pid, created, _ in self._owned):
                        self._owned.append((process.pid, create_time, command_line))
            except psutil.Error:
                continue
        self.process_tree = sorted(
            snapshots.values(), key=lambda entry: (entry["pid"], entry["createTime"]))
        self.pan_pids = sorted({entry["pid"] for entry in self.process_tree})
        self.listener_pids = _listening_pids(self.port)

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
        if process is not None:
            self._capture_process_tree()
        if process is None and not self._owned:
            if self.diagnostics is None:
                self.diagnostics = {
                    **(self.process_identity or {}),
                    "processTree": self.process_tree,
                    "port": self.port,
                    "portOwnerPids": self.listener_pids,
                    "remainingListenerPids": _listening_pids(self.port),
                    "returnCode": None,
                    "stdout": "",
                    "stderr": "",
                    "terminated": [],
                    "forceKilled": [],
                    "portReleased": _port_is_free(self.port),
                }
            remaining = _listening_pids(self.port)
            self.diagnostics["remainingListenerPids"] = remaining
            self.diagnostics["portReleased"] = (
                _port_is_free(self.port) and not remaining)
            self._persist_diagnostics()
            return self.diagnostics
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
                try:
                    return_code = owned.wait(timeout=2)
                except psutil.TimeoutExpired:
                    return_code = None
                terminated.append({
                    "pid": pid,
                    "returnCode": return_code,
                    "createTime": create_time,
                    "commandLine": command_line,
                })
            except psutil.Error:
                continue
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        process_objects: list[psutil.Process] = []
        for pid, create_time, command_line in reversed(self._owned):
            try:
                owned = psutil.Process(pid)
                if (abs(owned.create_time() - create_time) <= 0.01
                        and owned.is_running()
                        and (str(SERVER_LAUNCHER) in command_line
                             or str(FAKE_CLI) in command_line)):
                    process_objects.append(owned)
            except psutil.Error:
                continue
        _, still_alive = psutil.wait_procs(process_objects, timeout=5)
        lingering: list[dict] = []
        force_killed: list[psutil.Process] = []
        for owned in still_alive:
            try:
                identity = next(
                    item for item in self._owned if item[0] == owned.pid)
                pid, create_time, command_line = identity
                if abs(owned.create_time() - create_time) > 0.01:
                    continue
                if str(SERVER_LAUNCHER) not in command_line and str(FAKE_CLI) not in command_line:
                    continue
                owned.kill()
                force_killed.append(owned)
                lingering.append({
                    "pid": pid,
                    "returnCode": None,
                    "createTime": create_time,
                    "commandLine": command_line,
                })
            except (psutil.Error, StopIteration):
                continue
        gone_after_kill, _ = psutil.wait_procs(
            force_killed, timeout=5)
        force_exit_codes = {owned.pid: owned.returncode for owned in gone_after_kill}
        for entry in lingering:
            entry["returnCode"] = force_exit_codes.get(entry["pid"])
        self._owned.clear()
        return_code = process.poll() if process is not None else None
        self.process = None
        owned_processes_gone = True
        for entry in self.process_tree:
            try:
                owned_process = psutil.Process(entry["pid"])
                if (abs(owned_process.create_time() - entry["createTime"]) <= 0.01
                        and owned_process.is_running()):
                    owned_processes_gone = False
                    break
            except psutil.Error:
                continue
        released = _wait_for(
            lambda: _port_is_free(self.port), timeout=10,
            label=f"isolated port {self.port} release")
        remaining_listener_pids = _listening_pids(self.port)
        self.diagnostics = {
            **(self.process_identity or {}),
            "processTree": self.process_tree,
            "port": self.port,
            "portOwnerPids": self.listener_pids,
            "remainingListenerPids": remaining_listener_pids,
            "returnCode": return_code,
            "stdout": self.stdout_path.read_text(
                encoding="utf-8", errors="replace"),
            "stderr": self.stderr_path.read_text(
                encoding="utf-8", errors="replace"),
            "terminated": terminated,
            "forceKilled": lingering,
            "portReleased": bool(released and not remaining_listener_pids),
            "ownedProcessesGone": owned_processes_gone,
        }
        self._persist_diagnostics()
        return self.diagnostics

    def _persist_diagnostics(self) -> None:
        if self.evidence is None or self.diagnostics is None:
            return
        self.evidence.setdefault("subprocesses", {})["pan"] = self.diagnostics
        self.evidence["cleanup"] = {
            "terminated": self.diagnostics.get("terminated", []),
            "forceKilled": self.diagnostics.get("forceKilled", []),
            "portReleased": self.diagnostics.get("portReleased", False),
            "ownedProcessesGone": self.diagnostics.get(
                "ownedProcessesGone", False),
            "remainingListenerPids": self.diagnostics.get(
                "remainingListenerPids", []),
        }
        _write_evidence(self, self.evidence)

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
    # Independent-process heartbeat plus an in-process probe and agent stream
    # recorder sharing the pytest process for comparison.
    probe = _OutOfProcessHeartbeatProbe(runtime.ws_url, data_root)
    heartbeat = _HeartbeatProbe(runtime.ws_url)
    agent = _AgentEventRecorder(runtime.agent_ws_url)
    try:
        runtime.start()
        probe.start()
        heartbeat.start()
        agent.start()
        yield runtime, heartbeat, agent, probe
    finally:
        probe_result = None
        try:
            probe_result = probe.stop()
        finally:
            try:
                heartbeat.stop()
            finally:
                try:
                    agent.stop()
                finally:
                    cleanup = runtime.stop()
                    if runtime.evidence is not None:
                        runtime.evidence.setdefault("subprocesses", {})[
                            "heartbeatProbe"] = {
                                key: value for key, value in (probe_result or {}).items()
                                if key != "samples"
                            }
                        runtime.evidence["cleanup"] = cleanup
                        _write_evidence(runtime, runtime.evidence)
                        print(
                            "REAL_HTTP_HISTORY_COLDLOAD_E2E "
                            + json.dumps(
                                runtime.evidence, ensure_ascii=False,
                                sort_keys=True),
                            flush=True,
                        )


def _longest_over_target_run(samples: list[dict], target_ms: float) -> int:
    """Longest run of consecutive heartbeats whose gap exceeded ``target_ms``.

    ``serverGapMs`` removes only measured delay in scheduling the probe's next
    ping. ``gapMs`` remains available as the raw inter-pong wall interval.
    Consecutive server-attributed intervals beyond the target are what a
    sustained event-loop stall means.
    """
    longest = current = 0
    for sample in samples:
        gap = sample.get("serverGapMs", sample.get("gapMs"))
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
    server_gaps = [sample.get("serverGapMs", sample.get("gapMs"))
                   for sample in server]
    server_gaps = [gap for gap in server_gaps if gap is not None]
    raw_server_gaps = [sample["gapMs"] for sample in server
                       if sample.get("gapMs") is not None]
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
        "rawInProcessHeartbeat": in_process,
        "gapsOverTarget": [
            gap for gap in server_gaps if gap > MAX_STALL_SECONDS * 1000],
        "rawProbeGapsOverTarget": [
            gap for gap in raw_server_gaps if gap > MAX_STALL_SECONDS * 1000],
        "longestOverTargetRun": _longest_over_target_run(
            server, MAX_STALL_SECONDS * 1000),
        "rawServerHeartbeat": server,
    }
    if extra:
        record.update(extra)
    return record


def _write_evidence(runtime: "_PanRuntime", evidence: dict) -> Path:
    path = runtime.data_root / "be3-e2e-evidence.json"
    evidence["evidencePath"] = str(path)
    path.write_text(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8")
    return path


def test_heartbeat_measurement_separates_probe_lateness_from_server_response():
    # This is the 3.12 CI shape: the raw inter-pong interval was 109.063ms,
    # while the response itself took 11.104ms and the independent in-process
    # probe remained responsive. Keep the raw gap, but attribute its measured
    # pre-send scheduling delay to the probe process.
    sample = {
        "rttMs": 11.104,
        "gapMs": 109.063,
        "probeLatenessMs": 78.0,
        "serverGapMs": 31.063,
    }
    summary = _latency_summary([sample])
    assert summary["maxGapMs"] == 109.063
    assert summary["maxProbeLatenessMs"] == 78.0
    assert summary["maxServerGapMs"] == 31.063
    assert _longest_over_target_run([sample], 50.0) == 0


@pytest.mark.timeout(300)
def test_real_http_history_coldload_does_not_stall_dashboard_websocket(coldload_runtime):
    runtime, heartbeat, agent, probe = coldload_runtime
    fixture = runtime.fixture
    repository = _repository_snapshot()
    evidence: dict = {
        "task": "T-FRONTEND-COLDLOAD-BE3-20260921",
        "worktree": str(ROOT),
        "commit": repository["commit"],
        "trackedStatus": repository["trackedStatus"],
        "port": runtime.port,
        "protectedPortsAvoided": sorted(PROTECTED_PORTS),
        "dataRoot": str(runtime.data_root),
        "startCommand": runtime.start_command,
        "panPids": runtime.pan_pids,
        "processTree": runtime.process_tree,
        "fixture": fixture,
        "burstWorkers": BURST_WORKERS,
        "serverHeartbeatSamplesFile": str(probe.out),
        "maxStallTargetMs": MAX_STALL_SECONDS * 1000,
        "heartbeatIntervalMs": HEARTBEAT_INTERVAL * 1000,
    }
    runtime.evidence = evidence

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

    probe_result = probe.stop()
    evidence.setdefault("subprocesses", {})["heartbeatProbe"] = {
        key: value for key, value in probe_result.items() if key != "samples"
    }
    server_samples = _OutOfProcessHeartbeatProbe.window(
        probe_result["samples"], cold_start, steady_end)
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

    assert probe_result["returnCode"] == 0, (
        "out-of-process heartbeat probe did not exit cleanly: "
        f"pid={probe_result.get('pid')} returnCode={probe_result['returnCode']} "
        f"stdout={probe_result['stdout']!r} stderr={probe_result['stderr']!r}")

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
        assert all(
            sample.get("probeIntervalMs") == HEARTBEAT_INTERVAL * 1000
            for sample in window["rawServerHeartbeat"]
        ), f"{label} changed the 20ms out-of-process heartbeat interval"
        assert window["server"]["p95RttMs"] < MAX_STALL_SECONDS * 1000, (
            f"{label}: p95 heartbeat RTT {window['server']['p95RttMs']}ms "
            f"shows a sustained stall (baseline p95 "
            f"{evidence['baseline']['server']['p95RttMs']}ms)")
        assert window["streamEventDuringWindow"], (
            f"{label}: no real worker stream event during the read storm")
    # Raw probe gaps include time when the probe process was not scheduled to
    # send its next 20ms heartbeat. serverGapMs subtracts only that measured
    # client-side lateness; service thresholds remain unchanged and raw
    # intervals stay in the evidence.
    assert steady_window["longestOverTargetRun"] < 2, (
        "dashboard WebSocket heartbeat response stalled for "
        f"{steady_window['longestOverTargetRun']} consecutive heartbeats beyond "
        f"{MAX_STALL_SECONDS * 1000:.0f}ms during steady-state cold reads: "
        f"serverGaps={steady_window['gapsOverTarget'][:5]}ms; "
        f"rawProbeGaps={steady_window['rawProbeGapsOverTarget'][:5]}ms; "
        f"probeLatenessMax={steady_window['server']['maxProbeLatenessMs']}ms "
        f"(baseline max server gap "
        f"{evidence['baseline']['server']['maxServerGapMs']}ms, "
        f"{BURST_WORKERS} concurrent clients)")
    worst_steady_server_sample = max(
        steady_window["rawServerHeartbeat"],
        key=lambda sample: sample.get("serverGapMs", sample["gapMs"]) or 0,
        default=None,
    )
    in_process_samples_near_worst_gap = [
        sample for sample in steady_window["rawInProcessHeartbeat"]
        if worst_steady_server_sample is not None
        and abs(sample["at"] - worst_steady_server_sample["at"]) < 0.15
    ]
    assert (steady_window["server"]["maxServerGapMs"] or 0) < 100, (
        "one severe heartbeat gap during steady-state cold reads: "
        f"serverGaps={steady_window['gapsOverTarget'][:5]}; "
        f"rawProbeGaps={steady_window['rawProbeGapsOverTarget'][:5]}; "
        f"serverSample={worst_steady_server_sample}; "
        f"inProcessAtSameTime={in_process_samples_near_worst_gap}; "
        f"inProcessSummary={steady_window['inProcess']}; "
        f"requestMaxMs={steady_window['maxRequestMs']} requests={steady_window['requests']}")
    assert (cold_window["server"]["maxServerGapMs"] or 0) < 250, (
        "one-time store index load froze the loop: "
        f"{cold_window['gapsOverTarget'][:5]}")

    # ── process cleanup ──
    cleanup = runtime.stop()
    evidence["cleanup"] = cleanup
    assert cleanup["portReleased"], "isolated port was not released"
    assert cleanup["ownedProcessesGone"], "an owned server process is still running"
    assert not cleanup["remainingListenerPids"], (
        f"isolated port {runtime.port} still has a listener")
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
