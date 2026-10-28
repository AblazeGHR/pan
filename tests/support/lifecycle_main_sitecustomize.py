"""Test-owned bootstrap used only by a byte-for-byte copy of real main.py."""
import os
from pathlib import Path
import sys

root = Path(os.environ.get("PAN_E2E_DATA_ROOT", ".")).resolve()
if Path(sys.argv[0]).resolve() == root / "main.py":
    import asyncio
    import json
    from packages.core import config, session as session_store, background_jobs
    from packages.core import logging_setup
    from packages.core.adapters.cbc import adapter as cbc_adapter
    from packages.core.platform import data_paths as scheduler
    from packages.core.terminal import registry as terminals

    config.CONFIG_FILE = root / "config.json"
    session_store.SESSION_DIR = root / "sessions"
    session_store._cache.clear()
    session_store._all_loaded = False
    cbc_adapter.MCP_CONFIG_DIR = root / "mcp-configs"
    background_jobs.DEFAULT_ROOT = root / "background_jobs"
    scheduler.DEFAULT_ROOT = root / "background_jobs"
    terminals.DEFAULT_REGISTRY_ROOT = root / "terminals"
    logging_setup._REPO_ROOT = root
    os.environ["PAN_SESSION_ERROR_LOG_PATH"] = str(root / "logs" / "session-errors.jsonl")

    import packages.web.server as server
    server._PROJECT_DIR = root
    server.DATA_DIR = root
    server.WORKDIRS_DIR = root / "workdirs"
    server.ATTACHMENTS_DIR = root / "attachments"
    server._startup_recovery_record_path = lambda: root / "startup_recovery" / f"{server._STARTUP_RECOVERY_GENERATION}.json"

    fake_cli = os.environ.get("PAN_E2E_FAKE_CLI")
    if fake_cli:
        from packages.core.adapters.cbc.adapter import CbcAdapter
        CbcAdapter._resolve_cbc_argv = lambda self: [sys.executable, fake_cli]
        CbcAdapter.supports_native_steer = False
        CbcAdapter.supports_steer_receipt = False

    class CaptureShutdown:
        def __init__(self, app):
            self.app = app
        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http" or scope.get("path") != "/api/internal/main/shutdown":
                return await self.app(scope, receive, send)
            event = {"statusCode": None, "response": None}
            async def capture(message):
                if message["type"] == "http.response.start":
                    event["statusCode"] = message["status"]
                if message["type"] == "http.response.body":
                    try:
                        event["response"] = json.loads(message.get("body", b""))
                    except (ValueError, UnicodeError):
                        pass
                    host = getattr(server.app.state, "pan_uvicorn_server", None)
                    event["shouldExit"] = getattr(host, "should_exit", None)
                    (root / "cpx1-internal.json").write_text(json.dumps(event), encoding="utf-8")
                await send(message)
            await self.app(scope, receive, capture)
    server.app.add_middleware(CaptureShutdown)

    @server.app.get("/__test/cpx1-should-exit")
    async def should_exit():
        host = getattr(server.app.state, "pan_uvicorn_server", None)
        return {"shouldExit": getattr(host, "should_exit", None)}

    @server.app.post("/__test/stop-owned-runtime")
    async def stop_owned():
        host = getattr(server.app.state, "pan_uvicorn_server", None)
        if host is None:
            return {"ok": False}
        asyncio.get_running_loop().call_later(.05, setattr, host, "should_exit", True)
        return {"ok": True}
