"""Isolated HTTP fixture using the actual Jobs router and atomic registry."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from fastapi import FastAPI
from packages.jobs import api, pan_bridge
from packages.core import background_jobs
from packages.scheduler import store
pan_bridge.configured_client = lambda: None
app = FastAPI()
app.include_router(api.router)
@app.get("/api/sessions")
def sessions():
    return {"sessions": []}
@app.put("/test/write-failure/{enabled}")
def failure(enabled: int):
    background_jobs._atomic_write = fail if enabled else original
    return {"ok": True}
def fail(*args):
    raise OSError("isolated disk failure")
original = background_jobs._atomic_write
root = store.data_root()
for index, (kind, status) in enumerate([("session-message", "running"), ("session-broadcast", "completed"), ("scheduled-task", "scheduled"), ("background-process", "running"), ("main-lifecycle", "completed")]):
    job_id = f"job_pin_{index}"
    if background_jobs.get(job_id, registry_root=root) is None:
        background_jobs._create({"jobId": job_id, "kind": kind, "status": status,
            "name": f"Pin fixture {index}", "description": "isolated", "createdAt": 1,
            "updatedAt": index + 1, "paused": False, "runCount": 0}, registry_root=root)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
