"""Isolated browser harness: real terminal service, no full Pan/providers."""
import argparse
import asyncio
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from packages.core.terminal.service import TerminalService
from packages.web import terminal_api, terminal_ws

parser = argparse.ArgumentParser()
parser.add_argument("--root", required=True)
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()
repo = Path(__file__).resolve().parents[4]


@asynccontextmanager
async def lifespan(app):
    service = TerminalService(args.root, log_stderr=False)
    runtime = terminal_api.build_runtime(env={}, platform=sys.platform, host="127.0.0.1", port=args.port,
                                         service_factory=lambda: service, shutdown_budget=20)
    await terminal_api.start_runtime(app, runtime=runtime)
    async with terminal_ws.websocket_lifespan(app, runtime=runtime):
        try:
            yield
        finally:
            await terminal_api.stop_runtime(app, runtime=runtime)


app = FastAPI(lifespan=lifespan)
app.include_router(terminal_api.router)
app.include_router(terminal_ws.router)
@app.middleware("http")
async def observe_gate(request, call_next):
    if request.url.path == "/api/terminals" and request.method == "GET":
        print({key: request.headers.get(key) for key in ("origin", "host", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest")}, file=sys.stderr, flush=True)
    return await call_next(request)
app.mount("/react/assets", StaticFiles(directory=repo / "packages/web/dist/assets"), name="assets")


@app.get("/react/{path:path}")
def frontend(path: str):
    return FileResponse(repo / "packages/web/dist/index.html")


async def main():
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="error"))
    loop = asyncio.get_running_loop()
    def stop():
        sys.stdin.readline()
        loop.call_soon_threadsafe(setattr, server, "should_exit", True)
    threading.Thread(target=stop, daemon=True).start()
    await server.serve()


asyncio.run(main())
