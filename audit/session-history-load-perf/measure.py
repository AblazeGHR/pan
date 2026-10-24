"""Server-side + browser load-perf matrix for the Session list/history path.

Owned instances only: the dataset lives in a temporary data root, the server is
``tests/support/isolated_http_server.py`` on an ephemeral loopback port, and the
browser is the Playwright production Chromium. Ports 8767/8768 are refused.

Usage (from the repo root):
  python audit/session-history-load-perf/measure.py --label baseline --out audit/session-history-load-perf/baseline.json

``--label`` only tags the evidence. Each run repeats every scenario
``--repeat`` times (default 3) and reports the median.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "tests" / "support" / "isolated_http_server.py"
FAKE_CLI = ROOT / "tests" / "support" / "fake_stream_cli.py"
PYTHON = sys.executable
PROTECTED = {8767, 8768}
STANDARD_SESSION = "ses-perf-00"
LARGE_SESSION = "ses-perf-large"


def free_port() -> int:
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port in PROTECTED or port < 1024:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("no isolated port available")


def listening_pids(port: int) -> list[int]:
    return sorted({
        conn.pid for conn in psutil.net_connections(kind="tcp")
        if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port
        and conn.pid is not None
    })


class OwnedServer:
    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.port = free_port()
        self.process: subprocess.Popen | None = None
        self.base_url = f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "OwnedServer":
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "PAN_E2E_DATA_ROOT": str(self.data_root),
            "PAN_E2E_FAKE_CLI": str(FAKE_CLI),
            "PAN_E2E_FAKE_LOG": str(self.data_root / "fake-cli.log"),
            "PAN_TEST_DISABLE_QUEUE_WATCHDOG": "1",
            "PAN_AGENT_SESSION_ID": "",
            "PAN_SESSION_ERROR_LOG_PATH": str(self.data_root / "logs" / "session-errors.jsonl"),
        }
        self.process = subprocess.Popen(
            [PYTHON, str(LAUNCHER), "--port", str(self.port), "--data-root", str(self.data_root)],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"server exited: {self.process.stderr.read()[-4000:]}")
            try:
                # Deliberately not /api/sessions: that would warm the store.
                response = httpx.get(self.base_url + "/api/settings/ui", timeout=2.0,
                                     trust_env=False)
                if response.status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        raise RuntimeError("server did not become ready")

    def __exit__(self, *exc_info) -> None:
        assert self.process is not None
        root = psutil.Process(self.process.pid)
        children = root.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.Error:
                pass
        root.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            for child in children:
                try:
                    child.kill()
                except psutil.Error:
                    pass
            root.kill()
            self.process.wait(timeout=15)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and listening_pids(self.port):
            time.sleep(0.05)
        if listening_pids(self.port):
            raise RuntimeError(f"port {self.port} still has listeners after shutdown")

    def timed_get(self, path: str) -> dict:
        started = time.perf_counter()
        response = httpx.get(self.base_url + path, timeout=120.0, trust_env=False)
        elapsed = (time.perf_counter() - started) * 1000.0
        return {
            "path": path,
            "status": response.status_code,
            "ms": round(elapsed, 2),
            "wireBytes": int(response.headers.get("content-length") or 0),
            "decodedBytes": len(response.content),
            "encoding": response.headers.get("content-encoding"),
            "cacheControl": response.headers.get("cache-control"),
        }

    def settle(self, *, budget: float = 30.0, target_ms: float = 120.0) -> float:
        """Wait out post-start background work so reads are measured steady-state.

        Startup keeps a one-off ~1.3 s stall (reproducible: a single slow
        response about 1.3-1.6 s after the first request, then p95 < 50 ms).
        Measuring during that window reports start-up noise as load latency, so
        this waits past it and then requires two consecutive fast reads.
        """
        started = time.monotonic()
        fast = 0
        while time.monotonic() - started < budget:
            probe = self.timed_get("/api/sessions?summary=1")
            fast = fast + 1 if probe["ms"] < target_ms else 0
            if fast >= 2 and time.monotonic() - started > 2.0:
                return round(time.monotonic() - started, 2)
            time.sleep(0.05)
        return round(time.monotonic() - started, 2)


def server_matrix(data_root: Path, repeat: int) -> dict:
    """First read of a Session's history vs a repeat read (warm tail page)."""
    runs = []
    for index in range(repeat):
        with OwnedServer(data_root) as server:
            settled_after = server.settle()
            sample = {
                "run": index,
                "settleSeconds": settled_after,
                "list": server.timed_get("/api/sessions?summary=1"),
                "listRepeat": server.timed_get("/api/sessions?summary=1"),
                "historyStandardFirst": server.timed_get(
                    f"/api/sessions/{STANDARD_SESSION}/history?before=0&limit=50"),
                "historyStandardRepeat": server.timed_get(
                    f"/api/sessions/{STANDARD_SESSION}/history?before=0&limit=50"),
                "historyLargeFirst": server.timed_get(
                    f"/api/sessions/{LARGE_SESSION}/history?before=0&limit=50"),
                "historyLargeRepeat": server.timed_get(
                    f"/api/sessions/{LARGE_SESSION}/history?before=0&limit=50"),
                "historyLargeOlder": server.timed_get(
                    f"/api/sessions/{LARGE_SESSION}/history?before=20000&limit=50"),
                "legacyList": server.timed_get("/api/sessions"),
            }
            runs.append(sample)
    keys = [key for key in runs[0] if key not in {"run", "settleSeconds"}]
    medians = {}
    for key in keys:
        medians[key] = {
            "ms": round(statistics.median([run[key]["ms"] for run in runs]), 2),
            "wireBytes": int(statistics.median([run[key]["wireBytes"] for run in runs])),
            "encoding": runs[0][key]["encoding"],
        }
    return {"runs": runs, "medians": medians}


def browser_matrix(data_root: Path, out: Path, repeat: int, diag: bool) -> dict:
    out = out.resolve()
    with OwnedServer(data_root) as server:
        # Steady-state server: the browser numbers below are about browser work
        # and the wire, not about the process's first seconds.
        server.settle()
        command = [
            "node", str(ROOT / "packages" / "web" / "e2e" / "session-load-perf.mjs"),
            server.base_url, str(out), f"--runs={repeat}",
        ]
        if diag:
            command.append("--diag")
        completed = subprocess.run(command, cwd=str(ROOT / "packages" / "web"),
                                   capture_output=True, text=True, encoding="utf-8",
                                   timeout=3600)
        if completed.returncode != 0:
            raise RuntimeError(f"browser run failed: {completed.stderr[-4000:]}")
        return json.loads(out.read_text(encoding="utf-8"))


def medians_for_browser(payload: dict) -> dict:
    summary = {}
    for name, samples in payload["scenarios"].items():
        entry = {}
        for field in ("listMs", "historyMs", "largeHistoryMs", "switchBackMs"):
            values = [s[field] for s in samples if s.get(field) is not None]
            entry[field] = round(statistics.median(values), 1) if values else None
        for field in ("fcpMs", "lcpMs", "loadMs"):
            values = [s["paints"].get(field) for s in samples if s["paints"].get(field) is not None]
            entry[field] = round(statistics.median(values), 1) if values else None
        for phase in ("nav", "history", "switchBack"):
            scoped = [s["phases"][phase] for s in samples if s["phases"].get(phase)]
            entry[f"{phase}Requests"] = int(statistics.median(
                [p["count"] for p in scoped])) if scoped else None
            entry[f"{phase}Bytes"] = int(statistics.median(
                [p["bytes"] for p in scoped])) if scoped else None
        summary[name] = entry
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--skip-server", action="store_true")
    parser.add_argument("--diag", action="store_true",
                        help="record the client call stack that issued each /api/sessions read")
    args = parser.parse_args()

    work_root = Path(tempfile.mkdtemp(prefix=f"pan-perf-{args.label}-"))
    data_root = work_root / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    evidence: dict = {
        "label": args.label,
        "repeat": args.repeat,
        "sourceSha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True).strip(),
        "dirty": subprocess.check_output(
            ["git", "status", "--short", "--untracked-files=no"], cwd=str(ROOT),
            text=True).strip().splitlines(),
        "dataRoot": str(data_root),
    }
    try:
        seeded = subprocess.run(
            [PYTHON, str(ROOT / "audit" / "session-history-load-perf" / "seed.py"),
             "--data-root", str(data_root)],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", check=True)
        evidence["dataset"] = json.loads(seeded.stdout.strip().splitlines()[-1])

        if not args.skip_server:
            evidence["server"] = server_matrix(data_root, args.repeat)
        if not args.skip_browser:
            raw_path = args.out.with_suffix(".browser.json")
            raw = browser_matrix(data_root, raw_path, args.repeat, args.diag)
            evidence["browser"] = {"medians": medians_for_browser(raw), "raw": str(raw_path.name)}
    finally:
        evidence["cleanup"] = {"workRoot": str(work_root)}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({key: evidence.get(key) for key in ("label", "dataset", "server")},
                         indent=2, ensure_ascii=False)[:6000])
        if evidence.get("browser"):
            print(json.dumps(evidence["browser"]["medians"], indent=2, ensure_ascii=False))
        shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    main()
