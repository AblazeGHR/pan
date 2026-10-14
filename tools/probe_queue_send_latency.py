"""Measure Send / Steer queue stages on an isolated real Pan instance.

Development probe (not a test): starts ``tests/support/isolated_http_server.py``
on a free ephemeral port with the deterministic fake stream provider, then
times the stages the user reports as slow:

    request -> durable ack -> queue.item_added (WS) -> fake CLI stdin
    -> queue.item_delivered (WS)

It also reads the server's own persistence diagnostics
(``GET /api/diagnostics/persistence``) so the disk stage is attributed from the
server side instead of being guessed from the client total.

Nothing here touches the practical services (8767/8768), the repository's real
``data`` directory, or a real provider: the launcher rewrites the data root and
the CLI argv before uvicorn starts.

Usage:
    E:/software/miniforge/python.exe tools/probe_queue_send_latency.py \
  --rounds 12 --label baseline
"""

from __future__ import annotations

import argparse
import hashlib
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
from websockets.sync.client import connect as ws_connect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from packages.jobs.window import command_window_options

ROOT = Path(__file__).resolve().parent.parent
SERVER_LAUNCHER = ROOT / "tests" / "support" / "isolated_http_server.py"
FAKE_CLI = ROOT / "tests" / "support" / "fake_stream_cli.py"


def _source_hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("packages/core/worker.py", "packages/mcp/server.py")}

WATCHED_EVENTS = [
    "queue.item_added",
    "queue.item_delivered",
    "queue.snapshot",
    "worker.status",
    "worker.result",
]


def _free_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in {8767, 8768, 8765}:
            return port


class _Recorder:
    """Timestamp selected /ws/agent events on a background thread."""

    def __init__(self, url: str):
        self.url = url
        self.events: list[dict] = []
        self.ready = threading.Event()
        self.stop_requested = threading.Event()
        self.thread = threading.Thread(target=self._run, name="probe-ws", daemon=True)
        self.error: Exception | None = None

    def start(self):
        self.thread.start()
        if not self.ready.wait(timeout=10):
            raise RuntimeError("WebSocket subscription timed out")
        if self.error:
            raise self.error

    def _run(self):
        try:
            with ws_connect(self.url, open_timeout=10, close_timeout=2) as ws:
                ws.send(json.dumps({"type": "subscribe", "eventTypes": WATCHED_EVENTS}))
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
                    if event.get("type") in WATCHED_EVENTS:
                        event["_at"] = time.time()
                        self.events.append(event)
        except Exception as exc:
            if not self.stop_requested.is_set():
                self.error = exc
        finally:
            self.ready.set()

    def stop(self):
        self.stop_requested.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("WebSocket recorder did not stop")
        if self.error:
            raise self.error

    def wait_for(self, predicate, timeout: float = 15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in list(self.events):
                if predicate(event):
                    return event
            if self.error:
                raise self.error
            time.sleep(0.005)
        return None


class _Runtime:
    def __init__(self, data_root: Path, port: int):
        self.data_root = data_root
        self.port = port
        self.log_path = data_root / "fake-cli.jsonl"
        self.process: subprocess.Popen | None = None
        self.fake_cli = FAKE_CLI
        self.extra_env: dict[str, str] = {}
        self.base_url = f"http://127.0.0.1:{port}"
        self.client = httpx.Client(base_url=self.base_url, timeout=30, trust_env=False)
        self.gates = {
            "hold-A": data_root / "release-A",
            "hold-recovery": data_root / "release-recovery",
            "child-report-1": data_root / "release-manager",
        }

    def start(self, seed=None):
        self.data_root.mkdir(parents=True, exist_ok=False)
        if seed is not None:
            seed(self.data_root)
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith("PAN_") and k.upper() != "PYTHONPATH"}
        env["PYTHONPATH"] = str(ROOT)
        env.update({
            "PAN_E2E_DATA_ROOT": str(self.data_root),
            "PAN_E2E_FAKE_CLI": str(self.fake_cli),
            "PAN_E2E_FAKE_LOG": str(self.log_path),
            "PAN_E2E_GATE_MAP": json.dumps({k: str(v) for k, v in self.gates.items()}),
            "PAN_AGENT_SESSION_ID": "",
        })
        env.update(self.extra_env)
        log = open(self.data_root / "pan.log", "a", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER_LAUNCHER), "--port", str(self.port),
             "--data-root", str(self.data_root)],
            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
            **command_window_options(),
        )
        log.close()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise SystemExit("isolated server exited during startup")
            try:
                if httpx.get(self.base_url + "/api/health", timeout=0.5,
                             trust_env=False).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        raise SystemExit("isolated server did not become ready")

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            # Capture identities while the owned parent still exists. Never
            # select processes by a broad name or a shared listening port.
            descendants = psutil.Process(self.process.pid).children(recursive=True)
            owned = [(p.pid, p.create_time()) for p in descendants]
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            for pid, created_at in reversed(owned):
                try:
                    child = psutil.Process(pid)
                    if child.create_time() == created_at:
                        child.kill()
                        child.wait(timeout=5)
                except psutil.NoSuchProcess:
                    pass
        self.client.close()

    def request(self, method: str, path: str, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    def records(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        out = []
        for line in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def received(self, text: str) -> list[dict]:
        return [r for r in self.records()
                if r.get("kind") == "received" and r.get("text") == text]


def _percentiles(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)

    def pick(q: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return round(ordered[index], 1)

    return {"n": len(ordered), "minMs": round(ordered[0], 1),
            "p50Ms": pick(0.5), "p90Ms": pick(0.9), "maxMs": round(ordered[-1], 1)}


def _measure_round(rt: _Runtime, recorder: _Recorder, sid: str, text: str,
                   client_id: str, *, enqueue_only: bool = False) -> dict:
    mark = len(recorder.events)
    start_wall = time.time()
    start = time.perf_counter()
    response = rt.request("POST", f"/api/sessions/{sid}/queue",
                          json={"text": text, "clientMessageId": client_id})
    acked = time.perf_counter()
    ack_wall = time.time()
    payload = response.json()
    item_id = payload["item"]["queueItemId"]
    diag = rt.request("GET", "/api/diagnostics/persistence",
                      params={"session_id": sid}).json()
    entry = diag
    row = {
        "text": text, "queueItemId": item_id,
        "httpAckMs": round((acked - start) * 1000, 1),
        "requestAt": start_wall, "ackAt": ack_wall, "eventMark": mark,
        "duplicate": bool(payload.get("duplicate")),
        "saveWaitMs": entry.get("lastWaitMs"),
        "saveFlushMs": entry.get("lastFlushMs"),
        "flushCount": entry.get("flushCount"),
    }
    if enqueue_only:
        return row
    _finish_round(rt, recorder, row)
    idle = recorder.wait_for(
        lambda e: e.get("type") == "worker.status" and e.get("sessionId") == sid
        and e.get("status") == "idle" and e["_at"] >= start_wall, timeout=15)
    if idle is None:
        raise RuntimeError(f"worker did not become idle after {text}")
    return row


def _finish_round(rt: _Runtime, recorder: _Recorder, row: dict) -> None:
    item_id, text = row["queueItemId"], row["text"]
    added = recorder.wait_for(
        lambda e: e.get("type") == "queue.item_added" and e.get("queueItemId") == item_id,
        timeout=10)
    delivered = recorder.wait_for(
        lambda e: e.get("type") == "queue.item_delivered"
        and item_id in (e.get("queueItemIds") or []), timeout=30)
    cli_deadline = time.monotonic() + 30
    while time.monotonic() < cli_deadline and not rt.received(text):
        time.sleep(0.005)
    cli = rt.received(text)
    if not added or not delivered or len(cli) != 1:
        raise RuntimeError(f"incomplete/duplicate handoff: {text}; added={bool(added)}, "
                           f"delivered={bool(delivered)}, cliCount={len(cli)}")
    row.update({
        "ackToWsAddedMs": round((added["_at"] - row["ackAt"]) * 1000, 1),
        "enqueueToCliMs": round((cli[0]["at"] - row["requestAt"]) * 1000, 1),
        "cliToDeliveredMs": round((delivered["_at"] - cli[0]["at"]) * 1000, 1)
        if (cli and delivered) else None,
        "totalMs": round((delivered["_at"] - row["requestAt"]) * 1000, 1),
        "eventsObserved": len(recorder.events) - row["eventMark"],
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--backlog", type=int, default=4)
    parser.add_argument("--label", default="run")
    parser.add_argument("--history-filler", type=int, default=0,
                        help="pre-enqueue N messages to grow persisted history")
    parser.add_argument("--data-root", default=None)
    args = parser.parse_args()

    data_root = Path(args.data_root) if args.data_root else (
        ROOT / "evidence" / f"queue-latency-probe-{args.label}")
    port = _free_port()
    rt = _Runtime(data_root, port)
    report: dict = {"label": args.label, "port": port, "dataRoot": str(data_root),
                    "sourceSha256": _source_hashes(), "phases": {}}
    recorder = _Recorder(f"ws://127.0.0.1:{port}/ws/agent")
    try:
        rt.start()
        recorder.start()
        created = rt.request("POST", "/api/sessions", json={
            "name": "real-fifo", "adapter": "cbc", "model": "hy3",
            "permissionMode": "bypassPermissions", "outputMode": "stream",
        }).json()
        sid = created["id"]
        rt.request("POST", "/api/spawn", json={"sessionId": sid})
        report["sessionId"] = sid
        report["workdir"] = created["workdir"]

        # Warm-up (worker start + first turn) is excluded from the numbers.
        _measure_round(rt, recorder, sid, "warmup", "cm-warmup")

        idle_rounds = []
        for index in range(args.rounds):
            idle_rounds.append(_measure_round(
                rt, recorder, sid, f"idle-{index}", f"cm-idle-{index}"))
        report["phases"]["idleWorker"] = idle_rounds

        # Backlog while a turn is held open by the fake provider: the worker is
        # busy, so the queue must accumulate and then drain on release.
        held = _measure_round(rt, recorder, sid, "hold-A", "cm-hold", enqueue_only=True)
        _finish_round(rt, recorder, held)
        held["note"] = "turn intentionally held open"
        backlog = []
        for index in range(args.backlog):
            backlog.append(_measure_round(
                rt, recorder, sid, f"backlog-{index}", f"cm-backlog-{index}",
                enqueue_only=True))
        drain_start = time.time()
        rt.gates["hold-A"].touch()
        drain_deadline = time.monotonic() + 30
        while time.monotonic() < drain_deadline:
            if all(rt.received(f"backlog-{i}") for i in range(args.backlog)):
                break
            time.sleep(0.005)
        drain_ms = round((time.time() - drain_start) * 1000, 1)
        for row in backlog:
            _finish_round(rt, recorder, row)
        report["phases"]["busyWorker"] = {
            "heldTurnMs": held["httpAckMs"],
            "enqueue": backlog,
            "releaseToLastCliMs": drain_ms,
            "releaseToFirstCliMs": round(
                (min((r["at"] for r in rt.received("backlog-0")), default=time.time())
                 - drain_start) * 1000, 1),
        }

        if args.history_filler:
            filler = []
            for index in range(args.history_filler):
                filler.append(_measure_round(
                    rt, recorder, sid, f"filler-{index}", f"cm-filler-{index}"))
            report["phases"]["historyFillerLast5"] = filler[-5:]
            report["phases"]["historyFillerAggregate"] = _percentiles(
                [row["httpAckMs"] for row in filler])
            large_rounds = [_measure_round(rt, recorder, sid, f"large-{i}", f"cm-large-{i}")
                            for i in range(args.rounds)]
            report["phases"]["largeHistoryIdle"] = large_rounds
            report["phases"]["largeHistoryAggregate"] = {
                key: _percentiles([row[key] for row in large_rounds])
                for key in ("httpAckMs", "enqueueToCliMs", "saveWaitMs", "saveFlushMs")}

        report["aggregate"] = {
            "httpAckMs": _percentiles([r["httpAckMs"] for r in idle_rounds]),
            "ackToWsAddedMs": _percentiles(
                [r["ackToWsAddedMs"] for r in idle_rounds if r["ackToWsAddedMs"] is not None]),
            "enqueueToCliMs": _percentiles(
                [r["enqueueToCliMs"] for r in idle_rounds if r["enqueueToCliMs"] is not None]),
            "cliToDeliveredMs": _percentiles(
                [r["cliToDeliveredMs"] for r in idle_rounds
                 if r["cliToDeliveredMs"] is not None]),
        }
        session_file = data_root / "sessions" / f"{sid}.json"
        if session_file.exists():
            stored = json.loads(session_file.read_text(encoding="utf-8"))
            history_path = session_file.with_suffix(".history.jsonl")
            report["persisted"] = {
                "bytes": session_file.stat().st_size,
                "historyRows": len(history_path.read_text(encoding="utf-8").splitlines())
                if history_path.exists() else len(stored.get("history") or []),
                "ledgerRows": len(stored.get("queue_delivery_ledger") or {}),
                "queuePending": len(stored.get("queue_pending") or []),
            }
        print("QUEUE_LATENCY_PROBE " + json.dumps(report["aggregate"], sort_keys=True))
        out = ROOT / "evidence" / f"queue-latency-probe-{args.label}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote " + str(out))
    finally:
        try:
            if recorder.thread.ident is not None:
                recorder.stop()
        finally:
            rt.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
