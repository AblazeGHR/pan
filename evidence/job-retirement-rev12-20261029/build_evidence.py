from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "4a86a3ce275159b203ef883f4072931ce7125bee"
SOURCE = "f7116a898469bfd6334ad0d781338b86a1c7bfdf"
command = json.loads((OUT / "final-mcp-command.json").read_text())
assert command["testedSourceSha"] == SOURCE and command["sourceStable"] and command["exit"] == 0
assert command["counts"] == {"passed": 16, "failure": 0, "error": 0, "skipped": 0}
changed = subprocess.check_output(["git", "diff", "--name-only", BASE, SOURCE], cwd=ROOT, text=True).splitlines()
assert changed == ["packages/mcp/server.py", "tests/test_retirement_rev12_mcp.py"]
(OUT / "product.diff").write_bytes(subprocess.check_output(["git", "diff", "--unified=0", BASE, SOURCE, "--", "packages/mcp/server.py"], cwd=ROOT))
save = lambda name, value: (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
save("parity-matrix.json", {
    "schema": "pan.retirement.mcp-compatibility/revision12", "acceptedBaseline": BASE,
    "testedSourceSha": SOURCE, "changedFiles": changed,
    "command": {key: command[key] for key in ("exit", "counts", "elapsedSeconds")},
    "oldToCanonical": {
        "canonical-already-mapped": {"tools": ["job_*", "trigger_*", "event_creator_*", "job_template_*", "job_run_*", "job_operation_*"], "status": "verified-existing-canonical-tools"},
        "removed-background-start-retry": {"status": "capability_unsupported", "reason": "old Runner owner/retry identity cannot be represented without restoring legacy lifecycle or inventing a protocol"},
        "removed-message-job-create-update": {"status": "capability_unsupported", "reason": "old backlog/schedule envelope lacks Creator/Trigger/source admission and immutable CAS mapping"},
        "removed-scheduler-writes": {"status": "capability_unsupported", "reason": "old cursor mutation lacks canonical Creator/Trigger/Job references and Event identity"},
        "retained-read-cancel": {"tools": ["agent_background_get", "agent_background_list", "agent_background_cancel", "agent_message_job_get", "agent_message_job_list", "agent_message_job_cancel", "scheduler_list", "scheduler_get", "scheduler_runs"], "status": "verified-discoverable"}},
    "counts": command["counts"], "noTransportFallback": True,
    "remaining": ["frontend form caller wiring", "no-Creator Template decision", "monitoring/stable-reset", "unknown fallback/clear-target", "real migration/deployment"],
})
save("environment.json", {"interpreter": "D:/project/Pan-main/.venv/Scripts/python.exe", "version": subprocess.check_output(["D:/project/Pan-main/.venv/Scripts/python.exe", "--version"], text=True).strip(), "installedThisRevision": False})
files = [path for path in OUT.iterdir() if path.is_file() and path.name not in {"raw-artifacts.zip", "artifact-hashes.json", "raw-archive.json"}]
hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
save("artifact-hashes.json", hashes)
with zipfile.ZipFile(OUT / "raw-artifacts.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, path.name)
with zipfile.ZipFile(OUT / "raw-artifacts.zip") as archive:
    assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name, digest in hashes.items())
save("raw-archive.json", {"sha256": hashlib.sha256((OUT / "raw-artifacts.zip").read_bytes()).hexdigest(), "entries": len(hashes), "verified": True})
print(command["counts"])
