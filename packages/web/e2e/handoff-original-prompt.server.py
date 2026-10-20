"""Owned HTTP/browser fixture: real routes and disk, no CLI workers or lifespan."""
import json
import os
from pathlib import Path
import sys

import uvicorn

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
RUNTIME = Path(os.environ["PAN_E2E_RUNTIME"]).resolve()
RUNTIME.mkdir(parents=True, exist_ok=True)
for key in list(os.environ):
    if key.startswith("PAN_") and key != "PAN_E2E_RUNTIME":
        del os.environ[key]
os.environ["PAN_BACKGROUND_JOBS_DIR"] = str(RUNTIME / "jobs")
os.environ["PAN_SCHEDULER_DIR"] = str(RUNTIME / "jobs")

from packages.core import config, session, workspace

config.CONFIG_FILE = RUNTIME / "config.json"
config.CONFIG_FILE.write_text(json.dumps({"plugin_manifests": [], "qq": {"enabled": False},
    "scheduler": {"enabled": False}}), encoding="utf-8")
session.SESSION_DIR = RUNTIME / "sessions"
workspace.WORKSPACE_DIR = RUNTIME / "workspaces"

from packages.web import server

server.DATA_DIR = RUNTIME / "data"
server.WORKDIRS_DIR = RUNTIME / "workdirs"
server.ATTACHMENTS_DIR = RUNTIME / "attachments"
for directory in (server.DATA_DIR, server.WORKDIRS_DIR, server.ATTACHMENTS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

fixtures = {}
for name in ("UI Alpha", "UI Bravo", *(f"HTTP {n}" for n in range(8))):
    s = session.create(name, adapter="codex", original_prompt="source rules", handoff_prompt="OLD BRIEF")
    fixtures[name] = s.id
(RUNTIME / "fixtures.json").write_text(json.dumps(fixtures), encoding="utf-8")

if __name__ == "__main__":
    # The OS allocates a free port; this launcher never touches existing listeners.
    uvicorn.run(server.app, host="127.0.0.1", port=0, lifespan="off")
