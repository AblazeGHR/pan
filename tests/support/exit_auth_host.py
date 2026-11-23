"""Owned lifecycle fixture: production routes/auth/IPC/supervisor, no providers."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace


def main():
    root = Path(sys.argv[0]).resolve().parent
    config_data = json.loads((root / "config.json").read_text(encoding="utf-8"))
    port = config_data["port"]
    assert port not in (8767, 8768)
    for key in tuple(os.environ):
        if key.startswith("PAN_"):
            os.environ.pop(key)
    os.environ["PAN_PORT"] = str(port)
    os.environ["PAN_BACKGROUND_JOBS_DIR"] = str(root / "data/background_jobs")
    from packages.core import config, session, background_jobs, launcher
    config.CONFIG_FILE = root / "config.json"
    session.SESSION_DIR = root / "data/sessions"
    background_jobs.DEFAULT_ROOT = root / "data/background_jobs"
    background_jobs.PROJECT_ROOT = root
    from packages.web import server, machine_ipc
    from packages.automation.auth import UserAuth
    from packages.plugins.ipc import plugin_lifespan
    import psutil
    import uvicorn
    server._PROJECT_DIR = root
    server.DATA_DIR = root / "data"
    server._session_lifecycle_preferences = lambda config=None: {
        "exitStrategy": "preserve-running", "startupPreference": "ask"}
    app = server.app

    @asynccontextmanager
    async def owned_lifespan(app):
        async with plugin_lifespan(lambda: SimpleNamespace(plugins=[]), session.get, root / "data") as runtime:
            machine_ipc.bind(runtime, app, session.get)
            app.state.automation = SimpleNamespace(auth=UserAuth(root / "data/automation"))
            record = launcher._record(os.getpid(), root, "main", sys.argv,
                                      psutil.Process().create_time(), f"--pan-root-marker={root}")
            launcher.save_state(root, {"port": port, "bindHost": "127.0.0.1", "main": record})
            yield

    app.router.lifespan_context = owned_lifespan
    host = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                         access_log=False, timeout_graceful_shutdown=3))
    app.state.pan_uvicorn_server = host
    asyncio.run(host.serve())
