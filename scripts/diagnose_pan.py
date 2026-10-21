"""Read-only, external hang capture. Does not import/start/stop the Pan app."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

import psutil


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def process_identity(pid, root, created=None):
    """A port owner is not automatically a Pan process; never attach blindly."""
    try:
        p = psutil.Process(pid)
        observed = p.create_time()
        args = p.cmdline()
        entry = (root / "main.py").resolve()
        owns = any(Path(arg).is_absolute() and Path(arg).resolve() == entry for arg in args)
        if not owns or (created is not None and abs(observed - created) > 0.01):
            return None
        return {"pid": pid, "createdAt": observed, "argv": args, "name": p.name()}
    except (psutil.Error, OSError, ValueError):
        return None


def find_sampler(root):
    name = "py-spy.exe" if sys.platform == "win32" else "py-spy"
    for p in [Path(sys.executable).parent / name, root / ".venv/Scripts" / name,
              root / ".venv/bin" / name]:
        if p.is_file():
            return str(p)
    return shutil.which("py-spy")


def probe(base, endpoint, timeout=3.0, max_bytes=2 * 1024 * 1024):
    start = time.monotonic()
    result = {"path": endpoint, "startedAt": datetime.now().astimezone().isoformat()}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(base + endpoint, timeout=timeout) as response:
            result.update(status=response.status, requestId=response.headers.get("X-Pan-Request-Id"),
                          headersMs=round((time.monotonic() - start) * 1000, 3),
                          bodyComplete=False, bodyBytes=0)
            expected = response.headers.get("Content-Length")
            expected = int(expected) if expected is not None else None
            result["bodyExpectedBytes"] = expected
            chunks = []
            count = 0
            while count <= max_bytes:
                # A deadline also bounds a peer which trickles data forever.
                if time.monotonic() - start >= timeout:
                    raise TimeoutError("body deadline exceeded")
                chunk = response.read1(min(65536, max_bytes + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
                result["bodyBytes"] = count
            body = b"".join(chunks)
            result.update(bodyBytes=count, bodyComplete=(count <= max_bytes and
                          (expected is None or count == expected)), bodyLimitExceeded=count > max_bytes)
            if result["bodyComplete"] and endpoint.startswith("/api/diagnostics/"):
                result["diagnostics"] = json.loads(body)
    except urllib.error.HTTPError as exc:
        result.update(status=exc.code, errorType=type(exc).__name__)
    except Exception as exc:
        result.update(errorType=type(exc).__name__, error=str(exc)[:300])
    result["totalMs"] = round((time.monotonic() - start) * 1000, 3)
    return result


def capture_logs(root, output, full=False):
    records = []
    locations = [(root / relative, relative) for relative in
                 ("data/logs", "data/background_jobs/logs", "data/mcp_remote")]
    configured_log = None
    try:
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        configured_log = Path(config.get("logging", {}).get("file", "data/logs/pan.log"))
        if not configured_log.is_absolute():
            configured_log = root / configured_log
        if configured_log.parent.resolve() not in [p.resolve() for p, _ in locations]:
            locations.append((configured_log.parent, "configured-logs"))
    except (OSError, ValueError, TypeError):
        pass
    for directory, relative in locations:
        if not directory.is_dir():
            continue
        for p in directory.iterdir():
            if not p.is_file() or not (".log" in p.name or p.name == "session-errors.jsonl"
                    or p.name.startswith("runtime-diagnostics.jsonl")
                    or (configured_log is not None and p.name.startswith(configured_log.name))):
                continue
            try:
                stat = p.stat()
                offset = 0 if full else max(0, stat.st_size - 8 * 1024 * 1024)
                destination = output / relative / p.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                count = 0
                with p.open("rb") as source, destination.open("wb") as target:
                    source.seek(offset)
                    remaining = stat.st_size - offset
                    while remaining > 0:
                        block = source.read(min(1024 * 1024, remaining))
                        if not block:
                            break
                        target.write(block)
                        digest.update(block)
                        remaining -= len(block)
                        count += len(block)
                records.append({"source": str(p), "capturedFile": str(destination.relative_to(output)),
                                "sourceSize": stat.st_size, "offset": offset, "capturedBytes": count,
                                "sourceMtimeNs": stat.st_mtime_ns, "sha256": digest.hexdigest()})
            except OSError as exc:
                records.append({"source": str(p), "errorType": type(exc).__name__})
    write_json(output / "log-manifest.json", records)


def capture(root, output, port, *, session_id=None, full_logs=False, stack_samples=3, timeout=3.0):
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "capture.json", {"root": str(root), "port": port,
               "startedAt": datetime.now().astimezone().isoformat(), "readOnly": True})
    capture_logs(root, output, full_logs)
    try:
        listeners = [c for c in psutil.net_connections(kind="tcp")
                     if c.status == psutil.CONN_LISTEN and c.laddr.port == port]
    except psutil.Error as exc:
        listeners = []
        write_json(output / "listener-error.json", {"errorType": type(exc).__name__})
    write_json(output / "listeners.json", [c._asdict() for c in listeners])
    identities = [v for pid in {c.pid for c in listeners if c.pid}
                  if (v := process_identity(pid, root)) is not None]
    write_json(output / "verified-processes.json", identities)
    for p in (root / "data").glob("*pid*"):
        if p.is_file():
            shutil.copy2(p, output / p.name)
    state = root / "data/process.json"
    if state.is_file():
        shutil.copy2(state, output / "process.json")
    paths = ["/api/health", "/react/", "/api/sessions?summary=1",
             "/api/diagnostics/runtime", "/api/diagnostics/persistence"]
    if session_id:
        paths.append(f"/api/sessions/{session_id}/history?limit=1")
    base = f"http://127.0.0.1:{port}"
    with ThreadPoolExecutor(max_workers=len(paths)) as pool:
        probes = list(pool.map(lambda path: probe(base, path, timeout), paths))
    write_json(output / "http-probes.json", probes)
    sampler = find_sampler(root)
    samples = []
    for identity in identities:
        pid = identity["pid"]
        for index in range(stack_samples):
            if process_identity(pid, root, identity["createdAt"]) is None:
                samples.append({"pid": pid, "error": "process identity changed or exited"})
                break
            p = psutil.Process(pid)
            try:
                samples.append({"pid": pid, "time": time.time(), "cpuTimes": p.cpu_times()._asdict(),
                                "memory": p.memory_info()._asdict(), "threads": p.num_threads(),
                                "children": [{"pid": c.pid, "createdAt": c.create_time(), "name": c.name()}
                                             for c in p.children(recursive=True)]})
            except psutil.Error as exc:
                samples.append({"pid": pid, "errorType": type(exc).__name__})
            if sampler:
                try:
                    result = subprocess.run([sampler, "dump", "--pid", str(pid)],
                                            capture_output=True, timeout=8)
                    (output / f"stacks-{pid}-{index}.txt").write_bytes(result.stdout + b"\n" + result.stderr)
                    samples.append({"pid": pid, "stackSample": index, "exitCode": result.returncode})
                except (OSError, subprocess.TimeoutExpired) as exc:
                    samples.append({"pid": pid, "stackSample": index, "errorType": type(exc).__name__})
            if index + 1 < stack_samples:
                time.sleep(1)
    write_json(output / "resource-samples.json", {"pySpy": sampler or "unavailable; no install attempted",
               "systemMemory": psutil.virtual_memory()._asdict(), "samples": samples})
    for name, argv in [("git-head.txt", ["git", "rev-parse", "HEAD"]),
                       ("git-status.txt", ["git", "status", "--short"])]:
        try:
            r = subprocess.run(argv, cwd=root, capture_output=True, timeout=5)
            (output / name).write_bytes(r.stdout + r.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            (output / name).write_text(type(exc).__name__, encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--session-id")
    parser.add_argument("--full-logs", action="store_true")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / "main.py").is_file():
        parser.error("--root must identify a Pan checkout")
    if args.session_id and not __import__("re").fullmatch(r"ses_[A-Za-z0-9_-]+", args.session_id):
        parser.error("invalid Session ID")
    if not 0 < args.timeout <= 10:
        parser.error("timeout must be between 0 and 10 seconds")
    config_path = root / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    port = args.port or int(os.environ.get("PAN_PORT") or config.get("port", 8768))
    if not 1 <= port <= 65535:
        parser.error("port must be between 1 and 65535")
    output = args.output or root / "evidence/diagnostics" / (
        datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    result = capture(root, output.resolve(), port, session_id=args.session_id,
                     full_logs=args.full_logs, timeout=args.timeout)
    print(f"Evidence saved: {result}\nNo service was started, stopped, or restarted.")


if __name__ == "__main__":
    main()
