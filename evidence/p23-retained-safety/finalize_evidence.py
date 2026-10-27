"""Summarize actual JUnit artifacts and preserve raw failed experiments."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "bdd4c778d55daff58529635b2adc464c03a1393c"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


regression = ["tests/test_job_retained_safety_protocol.py", "tests/test_job_retained_safety_owned.py",
    "tests/test_job_final_independent_execution.py", "tests/test_independent_job_service.py",
    "tests/test_job_delete_safety.py", "tests/test_job_cleanup_failures.py", "tests/test_job_delete_main_combination.py"]
matrix = regression[:2] + ["tests/test_job_retained_safety_canonical.py"] + regression[2:] + ["tests/test_platform_process_contract.py"]
original = ["tests/test_job_delete_safety.py::test_scheduled_terminal_child_writeback_does_not_recreate_deleted_parent",
            "tests/test_job_delete_http_owned.py::test_real_owned_tree_delete_http"]
records = [
    ("original-red", original, 1),
    ("backend-first", ["tests/test_job_retained_safety_owned.py"], 0),
    ("canonical-first", ["tests/test_job_retained_safety_canonical.py"], 1),
    ("canonical-final", ["tests/test_job_retained_safety_canonical.py"], 1),
    ("regression-first", regression[:4] + ["tests/test_independent_job_platform.py"] + regression[4:], 4),
    ("regression-final", regression, 1),
    ("matrix-final", matrix, 1),
    ("delta-final", regression[:2] + ["tests/test_independent_job_service.py::test_cancel_with_wrong_runner_identity_does_not_touch_job"], 0),
    ("cleanup-final", ["tests/test_job_retained_safety_owned.py", "tests/test_job_retained_safety_protocol.py"], 0),
    ("acceptance-final", matrix, 1),
]
validation = []
nodes = {}
for label, selected, code in records:
    xml = OUT / (label + ".xml")
    suite = ET.parse(xml).getroot()
    cases = []
    for case in suite.iter("testcase"):
        status = "failed" if case.find("failure") is not None else "error" if case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
        parts = case.attrib.get("classname", "").split(".")
        boundary = next((index for index, part in enumerate(parts) if part[:1].isupper()), len(parts))
        node = "/".join(parts[:boundary]) + ".py::" + "::".join([*parts[boundary:], case.attrib["name"]])
        row = {"node": node, "status": status}
        if status == "skipped":
            row["reason"] = case.find("skipped").get("message", "")
        cases.append(row)
    command = ["python", "scripts/run_tests_isolated.py", "--", "python", "-m", "pytest", *selected,
               "-q", "--tb=short", "--junitxml=evidence/p23-retained-safety/" + label + ".xml"]
    validation.append({"label": label, "cwd": str(ROOT), "argv": command, "exit": code,
        "counts": dict(Counter(item["status"] for item in cases)), "collected": len(cases),
        "junit": xml.name, "log": label + ".log", "nodes": cases})
    if label in {"original-red", "acceptance-final"}:
        for item in cases:
            nodes[item["node"]] = {**item, "artifact": label}

protected = ["AGENTS.md", "CODEBUDDY.md", "packages/jobs/final_service.py", "packages/jobs/final_api.py",
    "packages/jobs/final_runtime/ledger.py", "packages/jobs/final_runtime/coordinator.py",
    "packages/core/background_jobs.py", "tests/conftest.py", "tests/test_job_delete_safety.py",
    "tests/test_job_delete_http_owned.py", "packages/scheduler/time_events.py", "packages/scheduler/store.py",
    "packages/scheduler/legacy_bridge.py", "docs/reports/R6_I_REPAIR_20261027.md",
    "evidence/r6i-repair/original-red-assessment.md"]
checks = []
for name in protected:
    baseline = git("rev-parse", BASE + ":" + name)
    current = git("hash-object", "--", name)
    checks.append({"path": name, "baselineBlob": baseline, "currentBlob": current, "unchanged": baseline == current})
assert all(row["unchanged"] for row in checks)
assert not git("diff", BASE, "--", "packages/web/src", "tests/support", "evidence/r6i-repair", "evidence/r6i-resume")
raw = [p for p in sorted(OUT.iterdir()) if p.suffix in {".xml", ".log"} or p.name.startswith("original-http-")]
archive_path = OUT / "raw-output.zip"
originals = {}
if archive_path.exists():
    with zipfile.ZipFile(archive_path) as archive:
        originals = {name: archive.read(name) for name in archive.namelist()}
added = False
for path in raw:
    if path.name not in originals:
        originals[path.name] = path.read_bytes()
        added = True
if added:
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in originals.items():
            archive.writestr(name, data)
hashes = {name: hashlib.sha256(data).hexdigest() for name, data in originals.items()}
result = {"baseline": BASE, "branch": git("branch", "--show-current"), "integrationTreePerTask": "bac726cf",
    "baselineIsUnacceptedP21Candidate": True, "commands": validation,
    "latestUniqueCounts": dict(Counter(row["status"] for row in nodes.values())), "latestUniqueNodes": list(nodes.values()),
    "protectedFiles": checks, "rawSha256": hashes,
    "additionalChecks": [{"argv": ["python", "scripts/run_tests_isolated.py", "--", "python", "-c",
        "import packages.web.server; print('server import OK')"], "exit": 0, "log": "import.log"},
        {"argv": ["git", "diff", "--cached", "--check"], "exit": 0}],
    "unverified": ["POSIX execution", "three-platform remote CI", "default pending adapter recovery",
        "public unknown refusal/confirmation flow", "full backend suite", "React/browser", "real data migration", "deployment", "human acceptance"]}
(OUT / "validation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps({"latestUniqueCounts": result["latestUniqueCounts"], "latestUniqueTotal": len(nodes),
                  "protectedUnchanged": len(checks)}, ensure_ascii=False))
