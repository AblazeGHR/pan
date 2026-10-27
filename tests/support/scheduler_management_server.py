"""Owned production-bundle/canonical HTTP fixture; no deployed profile/provider."""
import argparse
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace


def app_for(root, *, seed_time_template_target=False, time_test_host_zone=None):
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse
    from packages.core import config, session, background_jobs
    from packages.core.platform import data_paths
    config.CONFIG_FILE = root / "config.json"
    config.CONFIG_FILE.write_text("{}", encoding="utf-8")
    session.SESSION_DIR = root / "sessions"
    if seed_time_template_target:
        session.save(session.Session(id="owned-session", name="Owned time Template target"))
    data_paths.DEFAULT_ROOT = background_jobs.DEFAULT_ROOT = root / "background_jobs"
    os.environ["PAN_BACKGROUND_JOBS_DIR"] = os.environ["PAN_SCHEDULER_DIR"] = str(root / "background_jobs")
    from packages.automation.service import Service
    from packages.automation.api import install as install_automation
    from packages.jobs.final_api import CanonicalUserAuth, install
    from packages.jobs.final_service import bind
    from packages.jobs.final_runtime.executor import TypedExecutor
    from tests.support.canonical_host import RecordingProvider
    from packages.jobs.action_api import router as action_router
    from packages.jobs.api import router as job_router
    from packages.scheduler.api import router as scheduler_router
    app = FastAPI()
    automation = Service(root / "automation", SimpleNamespace(workers={}), None)
    automation.auth = CanonicalUserAuth(root / "automation")
    automation.auth.key()
    final = bind(app, automation, root / "canonical")
    if time_test_host_zone:
        # Owned fixture only: exercise DST without changing the OS timezone.
        final.creators.registry.providers[("time", "scheduler")].host_zone = lambda: time_test_host_zone
    # UI lifecycle tests are independent of wall-clock due fires. Time/DST and
    # effect recovery run in their dedicated real coordinator/provider tests.
    final.creators.clock = lambda: "2026-10-27T00:00:00Z"
    provider = RecordingProvider()
    final.executor = TypedExecutor(session_provider=provider)
    app.state.automation = automation
    install(app, action_router, job_router)
    install_automation(SimpleNamespace(app=app))
    app.include_router(scheduler_router)
    @asynccontextmanager
    async def lifespan(app):
        await final.start()
        try:
            yield
        finally:
            await final.close()
    app.router.lifespan_context = lifespan
    @app.get("/test/ready")
    def ready():
        return {"ready":True,"root":str(root),"providerCalls":len(provider.calls)}
    @app.get("/api/sessions")
    def sessions():
        return {"sessions":[]}
    @app.get("/api/config")
    def settings():
        return config.load_config()
    @app.get("/api/platform")
    def platform():
        return {"platform":"windows","capabilities":{}}
    @app.get("/api/{path:path}")
    def shell_reads(path: str):
        # Unrelated shell discovery only; native routes above always win.
        return {"sessions":[],"workspaces":[],"panels":[],"adapters":[],"jobs":[],"items":[],"nextCursor":None}
    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
    dist = Path(__file__).resolve().parents[2] / "packages" / "web" / "dist"
    @app.get("/react/{path:path}")
    def react(path: str):
        candidate = (dist / path).resolve()
        if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")
    return app


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--seed-time-template-target", action="store_true")
    parser.add_argument("--time-test-host-zone")
    args = parser.parse_args()
    if args.port in (8767,8768) or not args.root.is_absolute():
        raise SystemExit("Owned absolute root and non-protected port required")
    args.root.mkdir(parents=True, exist_ok=True)
    uvicorn.run(app_for(args.root, seed_time_template_target=args.seed_time_template_target,
                       time_test_host_zone=args.time_test_host_zone), host="127.0.0.1", port=args.port, log_level="warning")
