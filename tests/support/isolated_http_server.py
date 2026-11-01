"""Test-only FastAPI launcher with an isolated persistent data root.

The production entry points intentionally use the checkout's normal ``data``
directory.  This launcher patches the imported module paths before uvicorn
starts so a real server process can be used without touching that directory
or any other Pan service.  It only accepts the explicitly allocated E2E ports.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from tests.support.startup_trace import arm, StartupTrace

arm()


def _capture_delay_middleware(app):
    marker = os.environ.get("PAN_E2E_CAPTURE_MARKER")

    class Middleware:
        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http" or scope.get("method") != "GET":
                await app(scope, receive, send)
                return
            path = scope.get("path", "")
            if not path.endswith("/queue"):
                await app(scope, receive, send)
                return
            headers = dict(scope.get("headers") or [])
            raw_delay = headers.get(b"x-pan-e2e-delay")
            if raw_delay is None:
                await app(scope, receive, send)
                return
            try:
                delay = max(0.0, min(float(raw_delay.decode("ascii")), 10.0))
            except (UnicodeDecodeError, ValueError):
                await app(scope, receive, send)
                return

            captured = []

            async def capture(message):
                captured.append(dict(message))

            # Let the actual FastAPI route produce its response now, then hold
            # the already-captured bytes in this request.  Other HTTP requests
            # continue through the same real server while this task sleeps.
            await app(scope, receive, capture)
            if marker:
                Path(marker).write_text(str(time.time()), encoding="ascii")
            await asyncio.sleep(delay)
            for message in captured:
                await send(message)

    return Middleware()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    allowed_ephemeral_port = (
        1024 <= args.port <= 65535 and args.port not in {8767, 8768})
    if args.port not in {8767, 8765} and not allowed_ephemeral_port:
        raise SystemExit(
            "isolated E2E server accepts only ports 8765/8767 or an ephemeral port")

    data_root = args.data_root.resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    # Keep the append-only sample log inside this test's owned data root.
    os.environ["PAN_SESSION_ERROR_LOG_PATH"] = str(
        data_root / "logs" / "session-errors.jsonl")

    from packages.core import session as session_store
    from packages.core import config
    from packages.core.adapters.cbc import adapter as cbc_adapter
    config.CONFIG_FILE = data_root / "config.json"
    session_store.SESSION_DIR = data_root / "sessions"
    session_store._cache.clear()
    session_store._all_loaded = False
    cbc_adapter.MCP_CONFIG_DIR = data_root / "mcp-configs"

    import packages.web.server as server
    from packages.core import background_jobs
    if os.environ.get("PAN_TEST_DISABLE_QUEUE_WATCHDOG") == "1":
        from packages.core import worker
        worker._GLOBAL_WATCHDOG_TICK_SEC = 3600
    from packages.core.platform import data_paths as scheduler
    from packages.core.terminal import registry as terminals
    background_jobs.DEFAULT_ROOT = data_root / "background_jobs"
    scheduler.DEFAULT_ROOT = data_root / "background_jobs"
    terminals.DEFAULT_REGISTRY_ROOT = data_root / "terminals"

    server.DATA_DIR = data_root
    server.WORKDIRS_DIR = data_root / "workdirs"
    server.ATTACHMENTS_DIR = data_root / "attachments"
    server._startup_recovery_record_path = lambda: (
        data_root / "startup_recovery" / f"{server._STARTUP_RECOVERY_GENERATION}.json")
    server.app.add_middleware(_capture_delay_middleware)
    server.app.add_middleware(StartupTrace)

    fake_cli = os.environ.get("PAN_E2E_FAKE_CLI")
    if fake_cli:
        from packages.core.adapters.cbc.adapter import CbcAdapter

        def resolve_fake_cli(_self):
            return [sys.executable, fake_cli]

        CbcAdapter._resolve_cbc_argv = resolve_fake_cli
        # fake_stream_cli implements ordinary stream stdin only by default: it
        # emits no confirmed run or native admission receipts. Exercise the
        # capability-absent restart path explicitly, rather than inheriting
        # real CBC's native Steer contract and weakening its unconfirmed-run
        # safety gate. PAN_E2E_FAKE_STEER opts into a deterministic
        # control-wire/receipt fixture that proves the hand-off layer only.
        if os.environ.get("PAN_E2E_FAKE_STEER") == "1":
            CbcAdapter.supports_native_steer = True
            CbcAdapter.supports_steer_receipt = True
        else:
            CbcAdapter.supports_native_steer = False
            CbcAdapter.supports_steer_receipt = False

    if os.environ.get("PAN_E2E_LATENCY_CONTROLS") == "1" and fake_cli:
        # This capability belongs only to the deterministic probe CLI. It
        # exercises Pan's receipt path without a real native provider.
        from packages.core import worker
        CbcAdapter.supports_native_steer = True
        CbcAdapter.supports_steer_receipt = True
        CbcAdapter.encode_control_message = lambda self, value: json.dumps(value).encode()
        worker._QUEUE_EDIT_LOCK_TTL_SECONDS = 0.15
        spawn = worker._spawn_process

        class ProbeStdin:
            def __init__(self, stdin):
                self.stdin = stdin
                self.blocked = False

            def write(self, data):
                self.blocked = json.loads(data).get("text") == "blocked-control"
                if self.blocked:
                    (data_root / "steer-write").touch()
                return self.stdin.write(data)

            async def drain(self):
                while self.blocked and not (data_root / "release-drain").exists():
                    await asyncio.sleep(0.005)
                await self.stdin.drain()

            def __getattr__(self, name):
                return getattr(self.stdin, name)

        async def probe_spawn(*pos, **kw):
            proc = await spawn(*pos, **kw)
            if not isinstance(proc, str) and proc.stdin is not None:
                proc.stdin = ProbeStdin(proc.stdin)
            return proc

        worker._spawn_process = probe_spawn

    import uvicorn
    if os.environ.get("PAN_E2E_TAKEOVER_PROBE") == "1":
        from takeover_probe import install
        install(server, data_root)

    from packages.core.host_server import PanServer
    host = PanServer(uvicorn.Config(server.app, host="127.0.0.1", port=args.port,
                                    log_level="warning", access_log=False))
    server.app.state.pan_uvicorn_server = host
    @server.app.post('/__test/stop-owned-runtime')
    async def stop_owned_runtime():
        # Ordinary fixture shutdown joins lifespan-owned Actions/plugins.
        # Explicit process.kill crash tests remain a separate path.
        asyncio.get_running_loop().call_later(.05, setattr, host, 'should_exit', True)
        return {'ok': True}
    if os.environ.get("PAN_TEST_HOST_EXIT_PROBE") == "1":
        import signal
        @server.app.post("/__test/host-signal/{kind}")
        async def host_signal(kind: str):
            selected = {"SIGINT": signal.SIGINT, "SIGTERM": signal.SIGTERM}[kind]
            asyncio.get_running_loop().call_later(.05, signal.raise_signal, selected)
            return {"ok": True}
        # Real API/Job/worker shutdown, with only the detached production
        # supervisor replaced by stopping this owned temporary test server.
        server._main_exit_status = lambda: {"available": True, "pending": server._main_exit_pending, "port": args.port}
        server._main_restart_status = lambda: {"pending": False}
        server._main_restart_registry_root = lambda: data_root / "background_jobs"
        server._launch_main_exit_supervisor = lambda request_id: setattr(host, "should_exit", True)
    host.run()



if __name__ == "__main__":
    main()
