"""Owned HTTP fixture for CP-X3. No lifespan, provider, or real profile.

The capability denial is injected policy evidence on Windows, not POSIX proof.
Auth, Session creation, native-control routes and capability projection are real.
"""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import uvicorn

root, port, origin = Path(sys.argv[1]).resolve(), int(sys.argv[2]), sys.argv[3]
assert port not in (8767, 8768)
root.mkdir(parents=True, exist_ok=True)
from packages.core import config, session, workspace
config.CONFIG_FILE = root / "config.json"
config.CONFIG_FILE.write_text(json.dumps({"plugin_manifests": []}), encoding="utf-8")
session.SESSION_DIR = root / "sessions"
workspace.WORKSPACE_DIR = root / "workspaces"
from packages.web import server, terminal_api
from packages.core import platform
from packages.automation.auth import UserAuth
server.DATA_DIR = root / "data"
server.WORKDIRS_DIR = root / "workdirs"
server.ATTACHMENTS_DIR = root / "attachments"
server._ACCESS_LOG_FILE = root / "access.log"
server.app.state.automation = SimpleNamespace(
    auth=UserAuth(root / "automation"), engine=SimpleNamespace(io=asyncio.to_thread))
auth = server.app.state.automation.auth
auth.path.parent.mkdir(parents=True, exist_ok=True)
auth.path.write_text(os.environ["CPX3_TEST_KEY"], encoding="utf-8")
runtime = terminal_api.build_runtime(env={"PAN_TERMINAL_ALLOWED_ORIGINS": origin}, platform="win32", host="127.0.0.1")
runtime.state = terminal_api.STATE_READY
runtime.service = object()  # gate only; no terminal service/backend starts
server.app.state.terminal_runtime = runtime
original = platform.CAPABILITIES["terminal.native"]


@server.app.post("/__cpx3/policy/{mode}")
async def policy(mode: str):
    assert mode in {"denied", "actual"}
    platform.CAPABILITIES["terminal.native"] = (
        replace(original, supported_on=frozenset()) if mode == "denied" else original)
    return {"ok": True}


@server.app.get("/__cpx3/identity")
async def identity():
    return {"pid": os.getpid(), "root": str(root), "port": port,
            "platform": platform.platform_name(), "fixture": "CP-X3"}


owned_server = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, lifespan="off", access_log=False))


@server.app.post("/__cpx3/stop")
async def stop():
    owned_server.should_exit = True
    return {"ok": True}


owned_server.run()
