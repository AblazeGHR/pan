"""Disposable real HTTP/WS server; all Workers use the local fake CLI."""
import importlib.util
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location("summary_fixture", Path(__file__).with_name("server.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
os.environ["PAN_SESSION_ERROR_LOG_PATH"] = str(fixture.RUNTIME / "errors.jsonl")
os.environ["PAN_E2E_FAKE_CLI"] = str(Path(__file__).with_name("session-summary.fake.py"))
os.environ["PAN_E2E_SUMMARY_GATES"] = str(fixture.RUNTIME)
original_install = fixture._install_test_isolation
from packages.core.host_server import PanServer
original_server = PanServer


def seed():
    from packages.core import session
    fixture._write_fixture_files()
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    fixture.WORKDIR.mkdir(parents=True, exist_ok=True)
    for name in ["Summary A", "Summary B"]:
        target = session.create(name, adapter="cbc", workdir=str(fixture.WORKDIR),
                                notification_settings={"browser": False, "system": False})
        target.queue_paused = True
        session.save(target)


class OwnedServer(original_server):
    def run(self, *args, **kwargs):
        fixture.running_server = self
        self.config.timeout_graceful_shutdown = 2
        return super().run(*args, **kwargs)


def install(server):
    original_install(server)
    # Exercise the integrated production host while keeping its diagnostics
    # inside this owned fixture, never the checkout's default data directory.
    import json
    config = json.loads(fixture.fixture_config.CONFIG_FILE.read_text(encoding="utf-8"))
    config["logging"] = {"file": str(fixture.RUNTIME / "logs" / "pan.log")}
    fixture.fixture_config.CONFIG_FILE.write_text(json.dumps(config), encoding="utf-8")
    from packages.core.adapters.cbc.adapter import CbcAdapter
    from packages.core.adapters.cbc import adapter
    adapter.MCP_CONFIG_DIR = fixture.RUNTIME / "mcp-configs"
    CbcAdapter.supports_native_steer = False
    CbcAdapter.supports_steer_receipt = False

    @server.app.post("/__e2e/disconnect")
    async def disconnect():
        for connection in list(server.ws_clients):
            await connection.close(code=1012)
        return {"ok": True}

    @server.app.post("/__e2e/shutdown")
    async def shutdown():
        from packages.core import worker
        for owned in list(worker.workers.values()):
            await worker.kill_worker(owned.worker_id)
        fixture.running_server.should_exit = True
        return {"ok": True}


fixture._install_test_isolation = install
fixture._seed_sessions = seed
fixture.uvicorn.Server = OwnedServer
fixture.main()
