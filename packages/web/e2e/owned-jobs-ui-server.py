"""Serve the production React bundle over the owned Jobs executor harness.

Test-only assembly: the harness owns the API and the executor; this launcher
only mounts the already-built static assets and the SPA fallback. It does not
change product routes or build anything.
"""
import importlib.util
import os
from pathlib import Path
import sys

import uvicorn
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[3]
ROOT_STR = str(ROOT)
if ROOT_STR not in sys.path:
    sys.path.insert(0, ROOT_STR)

owned = Path(os.environ["PAN_OWNED_ROOT"]).resolve()
if not (owned / ".jobs-owned-harness").exists():
    raise RuntimeError("owned harness root marker missing")
dist = ROOT / "packages/web/dist"
if not (dist / "index.html").is_file():
    raise RuntimeError("production React bundle missing; run pnpm build first")

spec = importlib.util.spec_from_file_location(
    "owned_jobs_harness", ROOT / "tests/support/jobs_owned_executor_server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
app = module.app_for(owned)

app.mount("/react/assets", StaticFiles(directory=dist / "assets"), name="react-assets")


@app.get("/api/job-maintenance/status")
def job_maintenance_status():
    """The owned harness holds the owner lease, so the shell is ready."""
    return {"mode": "normal", "ready": True}


@app.get("/")
def home():
    return RedirectResponse("/react/")


@app.get("/react/{path:path}")
def spa(path: str):
    candidate = (dist / path).resolve()
    if str(candidate).startswith(str(dist.resolve())) and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(dist / "index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PAN_PORT"]), log_level="info")
