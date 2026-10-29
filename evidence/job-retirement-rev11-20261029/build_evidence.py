from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "cce3a647d838301cfb3956615156bcf48f4d3553"
SOURCE = "6ce6ab8ac2a35c63d5ece20038dae27a81815690"
command = json.loads((OUT / "final-compat-command.json").read_text())
assert command["testedSourceSha"] == SOURCE and command["sourceStable"] and command["exit"] == 0
assert command["counts"] == {"passed": 142, "failure": 0, "error": 0, "skipped": 0}
changed = subprocess.check_output(["git", "diff", "--name-only", BASE, SOURCE], cwd=ROOT, text=True).splitlines()
assert changed == ["packages/jobs/final_service.py", "tests/test_retirement_rev11_compat.py"]
(OUT / "product.diff").write_bytes(subprocess.check_output(["git", "diff", "--unified=0", BASE, SOURCE, "--", "packages/jobs/final_service.py"], cwd=ROOT))
save = lambda name, value: (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
save("parity-matrix.json", {
    "schema": "pan.retirement.compatibility/revision11", "acceptedBaseline": BASE,
    "testedSourceSha": SOURCE, "changedFiles": changed,
    "command": {key: command[key] for key in ("exit", "counts", "elapsedSeconds")},
    "mapping": {
        "assign": {"oldFields": ["sessionId", "targetSessionId", "target", "text", "message", "source", "sourceSessionId", "taskId"],
                    "successor": "normalize_legacy_form_fields -> product_assign_v2 -> WorkerAssignProvider",
                    "status": "verified-real-owned-queue"},
        "shell": {"oldFields": ["command", "argv", "cwd", "hideWindow"],
                   "successor": "normalize_legacy_form_fields -> product_process_cleanup_v1 -> existing process executor",
                   "status": "verified-real-owned-process"},
        "template": {"status": "bounded-conflict", "reason": "canonical instantiate requires a Scheduler Creator; no-Creator default assign template returns capability_unsupported", "decision": "do not restore generic dispatcher or add a new protocol"}},
    "counts": command["counts"], "unverified": ["frontend form caller wiring", "remote CI/deployment", "real data migration"],
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
