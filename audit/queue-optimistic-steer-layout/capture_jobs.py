"""Snapshot terminal durable Job facts and logs without contacting a service."""
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from packages.core import background_jobs as jobs

area = Path(__file__).resolve().parent
registry = area / "runtime/jobs"
os.environ["PAN_BACKGROUND_JOBS_DIR"] = str(registry)
ids = ["job_e83990d56acaed0553628f39", "job_5a767c1cb2ba0329f1eaee66",
       "job_e0f8be294a8bc1b96ad35c09", "job_5190b8c0037f05b8143d5c7e",
       "job_653a31ee640021647a4ce3a5"]
facts = [jobs.get(identity, registry_root=registry) for identity in ids]
for fact in facts:
    if fact.get("status") != "completed" or fact.get("exitCode") != 0:
        raise RuntimeError(f"Job not successful: {fact}")
area.joinpath("job-facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
area.joinpath("quality.log").write_bytes(Path(facts[-1]["logPath"]).read_bytes())
print(json.dumps([{key: f.get(key) for key in ("jobId", "status", "exitCode")} for f in facts]))
