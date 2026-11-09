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

# Synthetic Sessions for the designed target selectors (owned root only).
from packages.core import session as session_store  # noqa: E402
session_store.SESSION_DIR = owned / "sessions"
session_store._cache.clear()
session_store._all_loaded = False
for name in ("Owned Alpha", "Owned Beta"):
    session_store.create(name, adapter="cbc", workdir=str(owned))

app.mount("/react/assets", StaticFiles(directory=dist / "assets"), name="react-assets")

# The product automation router owns the login form, cookie session and the
# dashboard read. Reuse it as-is instead of re-implementing authentication.
from types import SimpleNamespace  # noqa: E402
from packages.automation import api as automation_api  # noqa: E402
automation_api.install(SimpleNamespace(app=app))


# A long process that only writes its completion marker after the sleep. A
# cancelled Run must never contain that marker.
long_script = owned / "long_done.py"
long_script.write_text(
    "import os,time\n"
    "os.write(1,b'owned stdout\\n');os.write(2,b'owned stderr\\n')\n"
    "time.sleep(300)\n"
    "os.write(1,b'owned completion marker\\n')\n",
    encoding="utf-8")


# Shell bootstrap routes the full Pan UI calls; the owned harness only mounts
# the Jobs/automation surface, so these test-host equivalents read the same
# owned roots instead of inventing product state.
@app.get("/api/main/startup-recovery")
def startup_recovery():
    return {"recovered": True, "pending": []}


@app.get("/api/list")
def shell_list():
    return {"sessions": [_session_row(item) for item in session_store.list_all(load_history=False)]}


@app.get("/api/sessions")
def shell_sessions(summary: int = 0):
    return {"sessions": [_session_row(item) for item in session_store.list_all(load_history=False)]}


def _session_row(session):
    return {"id": session.id, "name": session.name, "adapter": getattr(session, "adapter", "cbc"),
            "workdir": getattr(session, "workdir", str(owned)), "messageCount": 0,
            "workerStatus": "idle", "createdAt": getattr(session, "created_at", None)}


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
