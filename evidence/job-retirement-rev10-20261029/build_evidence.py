from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "f04011d1691fe0aa2982c772b3d338866ed65b5d"
SOURCE = "8b2aa0cfcfd1209037545c52e2b1ac868667fe47"

def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)

def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

labels = ["final-retry", "final-qq-native", "final-canonical", "final-recovery"]
groups = {label: json.loads((OUT / f"{label}-command.json").read_text()) for label in labels}
assert all(group["testedSourceSha"] == SOURCE and group["sourceStable"] for group in groups.values())
changed = git("diff", "--name-only", BASE, SOURCE).decode().splitlines()
expected = [
    "packages/actions/qq_channel_provider.py", "packages/actions/typed_executor.py",
    "packages/jobs/final_service.py", "packages/qq/channels/onebot.py",
    "packages/qq/plugin.py", "packages/web/server.py",
    "tests/test_retirement_rev10_qq_retry.py",
]
assert changed == expected, changed
(OUT / "product.diff").write_bytes(git("diff", "--unified=0", BASE, SOURCE, "--", "packages"))
nodes = {}
for label, group in groups.items():
    for node in group["nodes"]:
        assert node["node"] not in nodes
        nodes[node["node"]] = {"status": node["status"], "command": label}
counts = {status: sum(row["status"] == status for row in nodes.values()) for status in ("passed", "failure", "error", "skipped")}
save("parity-matrix.json", {
    "schema": "pan.retirement.qq-parity/revision10", "acceptedBaseline": BASE,
    "testedSourceSha": SOURCE, "changedFiles": changed,
    "groups": {label: {key: group[key] for key in ("exit", "counts", "elapsedSeconds")} for label, group in groups.items()},
    "uniqueCounts": counts, "nodes": nodes,
    "oldRedMapping": {
        "r9-default-selected-no-send": {
            "old": "tests/test_retirement_rev9_qq_http.py::test_local_disconnected_no_send_manual_retry_retained_gap",
            "new": [node for node in nodes if "cold_retry_duplicate_control_cas_same_effect" in node],
            "status": "closed-with-trusted-pre-send-phase"},
        "r9-unknown-ambiguous": {
            "new": [node for node in nodes if "call_api_entered" in node or "accepted_without_ack" in node],
            "status": "preserved-unknown-reconciliation-required"}},
    "manualRetryOnly": True, "automaticChannelRetry": False,
    "publicationChanged": False, "newTransportOrLedger": False,
    "remainingGaps": ["default assign/shell wrappers", "MCP compatibility", "monitoring/stable-reset",
                       "unknown fallback/clear-target decisions", "real gateway/account delivery", "remote CI/deployment/real migration"]})
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
print(json.dumps({label: group["counts"] for label, group in groups.items()}))
