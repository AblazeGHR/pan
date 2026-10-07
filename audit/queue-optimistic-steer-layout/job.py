"""Run an authorized command through Pan's isolated durable Job Registry."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core import background_jobs as jobs, session

RUNTIME = ROOT / "audit/queue-optimistic-steer-layout/runtime"
session.SESSION_DIR = RUNTIME / "sessions"
os.environ["PAN_BACKGROUND_JOBS_DIR"] = str(RUNTIME / "jobs")
os.environ["PAN_PYTHON"] = sys.executable
os.environ["PAN_PORT"] = "18765"
if sys.argv[1] == "start":
    target = session.create("isolated-validation", adapter="codex")
    result = jobs.start(target.id, sys.argv[2:], str(ROOT), registry_root=RUNTIME / "jobs")
elif sys.argv[1] == "cancel":
    result = jobs.cancel(sys.argv[2])
else:
    result = jobs.get(sys.argv[2], registry_root=RUNTIME / "jobs")
print(json.dumps(result, ensure_ascii=False, indent=2))
