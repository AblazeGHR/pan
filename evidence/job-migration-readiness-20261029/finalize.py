"""Collect only this task's synthetic evidence; never writes source/profile data."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
DRILL = ROOT / ".validation/job-migration-readiness/verified-c89866f9"
BASE = "d350efaf87e6ae60a5a60f037a4d736b52a2243b"
SOURCE = "c89866f9db23d3833994511353c51cd18fe3cf6c"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


assert git("rev-parse", "HEAD") == SOURCE
assert not git("diff", BASE, "--", "packages", "tests/conftest.py")
allowed_tests = {"tests/test_job_migration_readiness.py", "tests/support/job_migration_readiness_rehearsal.py"}
paths = git("diff", "--name-only", BASE, SOURCE).splitlines()
assert all(p.startswith("scripts/job_final_migration/") or p in allowed_tests for p in paths)
for name in ("summary.json", "dispositions.json", "source-before.json", "source-after.json", "crash-recovery.json"):
    shutil.copyfile(DRILL / name, OUT / name)
commands = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DRILL.glob("*.command.json"))]
write("cli-commands.json", commands)
reports = {}
for path in sorted(OUT.glob("*.xml")):
    suites = ET.parse(path).getroot().iter("testsuite")
    reports[path.name] = {key: sum(int(s.attrib.get(key, 0)) for s in ET.parse(path).getroot().iter("testsuite"))
                          for key in ("tests", "failures", "errors", "skipped")}
write("test-counts.json", reports)
assert reports["verified-regression.xml"] == {"tests": 208, "failures": 0, "errors": 0, "skipped": 0}
write("source-guard.json", {
    "baseline": BASE, "verifiedSource": SOURCE, "changedPaths": paths,
    "packagesDiffEmpty": True, "oldTestsAndGlobalFixtureDiffEmpty": True,
    "scriptTree": git("rev-parse", SOURCE + ":scripts/job_final_migration"),
    "ownedFileSha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths},
    "protectedRefs": {r: git("rev-parse", r) for r in ("main", "practical", "xplat/minimal-ci-20261029")},
    "testCommand": ["D:/project/Pan-main/.venv/Scripts/python.exe", "scripts/run_tests_isolated.py", "--",
                    "D:/project/Pan-main/.venv/Scripts/python.exe", "-m", "pytest",
                    "tests/test_job_migration_readiness.py", "tests/test_job_final_migration.py",
                    "tests/test_job_final_migration_apply.py", "tests/test_job_final_migration_review_packet.py",
                    "tests/test_job_final_contracts.py", "-q", "-o", "addopts=",
                    "--junitxml=evidence/job-migration-readiness-20261029/verified-regression.xml"],
    "testExit": 0, "rehearsalExit": 0, "runtimeImportedOrActivated": False,
    "localPlatform": "Windows / designated venv Python 3.14.5", "remoteCi": False,
    "originalR14ShellRed": "tests/test_retirement_rev14_templates.py::test_production_shell_form_real_process_exit_and_original_lookup",
    "originalR14Evidence": "evidence/job-retirement-rev14-20261029/product.xml",
    "preservedOriginalTreeObservations": {
        tree: subprocess.check_output(["git", "-C", tree, "status", "--short"], text=True)
        for tree in ("D:/project/pan-worktrees/xplat-worker-capability-20261027",
                     "D:/project/pan-worktrees/xplat-minimal-release-20261029")},
})
write("artifact-hashes.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "artifact-hashes.json"})
