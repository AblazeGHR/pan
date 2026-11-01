"""Capture exact bounded runs and preservation against the accepted input."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "e2c12fad1021584cda8a9e2a00f0bde841e7b375"


def git(*args, **kwargs):
    return subprocess.check_output(["git", *args], cwd=ROOT, **kwargs).decode().strip()


runs = []
for label in ("new", "canonical", "retained", "retained-final", "safety"):
    command = json.loads((OUT / (label + ".json")).read_text(encoding="utf-8"))
    suite = ET.parse(OUT / (label + ".xml")).getroot().find("testsuite")
    counts = {key: int(suite.attrib[key]) for key in ("tests", "failures", "errors", "skipped")}
    nodes = [{"node": case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"],
              "result": "passed" if len(case) == 0 else case[0].tag}
             for case in suite.findall("testcase")]
    runs.append({"label": label, **command, **counts,
                 "passed": counts["tests"] - sum(counts[k] for k in ("failures", "errors", "skipped")),
                 "nodes": nodes})

prior = json.loads((ROOT / "evidence/p24-canonical-pins/validation.json").read_text(encoding="utf-8"))
paths = {row["path"] for row in prior["preserved"]}
paths.update(git("ls-files", "evidence/p24-canonical-pins").splitlines())
paths.update(["tests/test_job_retained_canonical_pins.py", "docs/reports/P24_CANONICAL_PINS_20261027.md"])
paths.discard("packages/jobs/final_runtime/coordinator.py")
paths = sorted(paths)
tree = {line.split("\t", 1)[1]: line.split()[2] for line in git("ls-tree", "-r", BASE).splitlines()}
blobs = git("hash-object", "--stdin-paths", input=("\n".join(paths) + "\n").encode()).splitlines()
preserved = [{"path": path, "gitBlob": blob, "unchanged": tree[path] == blob}
             for path, blob in zip(paths, blobs, strict=True)]
assert all(row["unchanged"] for row in preserved)
changed = git("diff", "--name-only", BASE, "HEAD").splitlines()
assert changed == ["packages/jobs/final_runtime/coordinator.py", "tests/test_job_retained_canonical_pin_resolution.py"]

old_nodes = prior["runs"][0]["nodes"]
current = {row["node"]: row["result"] for row in next(r for r in runs if r["label"] == "canonical")["nodes"]}
mapping = [{"oldNode": row["node"], "oldResult": row["result"],
            "successorNode": row["node"], "result": current[row["node"]],
            "assertionsUnchanged": True} for row in old_nodes]
artifacts = [{"path": str(path.relative_to(ROOT)).replace("\\", "/"),
              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
             for path in sorted(OUT.iterdir()) if path.is_file() and path.name != "validation.json"]
value = {"baselineSha": BASE, "sourceSha": git("rev-parse", "HEAD"),
         "branch": git("branch", "--show-current"), "sourcePaths": changed,
         "interfaceBaseline": prior["interfaceBaseline"], "integrationBaseline": prior["integrationBaseline"],
         "runs": runs, "originalCanonicalNodeMapping": mapping,
         "preserved": preserved, "artifacts": artifacts}
(OUT / "validation.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"runs": [{k: row[k] for k in ("label", "tests", "passed", "failures", "errors", "skipped", "exit")} for row in runs],
                  "preserved": len(preserved), "sourceSha": value["sourceSha"]}))
