"""Owned Sessions for the real WS indicator regression; no provider is started."""
import importlib.util
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location("live_fixture", Path(__file__).with_name("server.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
# Match the product's default unified registry while keeping every path owned.
os.environ["PAN_SCHEDULER_DIR"] = os.environ["PAN_BACKGROUND_JOBS_DIR"]


def seed():
    from packages.core import session as sessions
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    fixture.WORKDIR.mkdir(parents=True, exist_ok=True)
    for name in ["Live Selected", "Live Background B", "Live Background C"]:
        session = sessions.create(name, adapter="codex", workdir=str(fixture.WORKDIR),
                                  notification_settings={"browser": False, "system": False})
        # Real terminal notices may enqueue, but cannot wake a provider process.
        session.queue_paused = True
        sessions.save(session)


original_install = fixture._install_test_isolation
original_server = fixture.uvicorn.Server


class OwnedServer(original_server):
    def run(self, *args, **kwargs):
        fixture.running_server = self
        return super().run(*args, **kwargs)


def install(server):
    original_install(server)

    @server.app.get("/__e2e/worker-count")
    async def worker_count():
        from packages.core import worker
        return {"count": len(worker.workers)}

    @server.app.post("/__e2e/shutdown")
    async def shutdown():
        fixture.running_server.should_exit = True
        return {"ok": True}


fixture.uvicorn.Server = OwnedServer
fixture._install_test_isolation = install
fixture._seed_sessions = seed
fixture.main()
