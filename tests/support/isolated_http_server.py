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
    if args.port not in {8767, 8765}:
        raise SystemExit("isolated E2E server accepts only port 8767 or 8765")

    data_root = args.data_root.resolve()
    data_root.mkdir(parents=True, exist_ok=True)

    from packages.core import session as session_store
    from packages.core.adapters.cbc import adapter as cbc_adapter
    import packages.web.server as server

    session_store.SESSION_DIR = data_root / "sessions"
    session_store._cache.clear()
    session_store._all_loaded = False
    cbc_adapter.MCP_CONFIG_DIR = data_root / "mcp-configs"

    server.DATA_DIR = data_root
    server.WORKDIRS_DIR = data_root / "workdirs"
    server.ATTACHMENTS_DIR = data_root / "attachments"
    server.app.add_middleware(_capture_delay_middleware)

    fake_cli = os.environ.get("PAN_E2E_FAKE_CLI")
    if fake_cli:
        from packages.core.adapters.cbc.adapter import CbcAdapter

        def resolve_fake_cli(_self):
            return [sys.executable, fake_cli]

        CbcAdapter._resolve_cbc_argv = resolve_fake_cli

    import uvicorn

    uvicorn.run(
        server.app,
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
