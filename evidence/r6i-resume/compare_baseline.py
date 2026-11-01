"""Export the reviewed base to a temporary directory; never reset this tree."""
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile
import argparse

from packages.jobs.window import command_window_options

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "bac726cf84c0510dff8618afb82e227e660f590c"
parser = argparse.ArgumentParser()
parser.add_argument("--safety", action="store_true")
args = parser.parse_args()
data = subprocess.check_output(["git", "archive", "--format=zip", BASE,
    "packages", "tests", "scripts", "pytest.ini"], cwd=ROOT, **command_window_options())
with TemporaryDirectory(prefix="pan-r6i-baseline-") as directory:
    target = Path(directory)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(target)
    commands = [
        [sys.executable, "scripts/run_tests_isolated.py", "--", sys.executable, "-m", "pytest",
         "tests/test_jobs_api.py", "tests/test_scheduler_legacy_bridge.py", "-q", "--tb=short",
         "--junitxml=" + str(OUT / "baseline-red.xml")],
        [sys.executable, "scripts/run_tests_isolated.py", "--", sys.executable,
         str(OUT / "owner_epoch_probe.py"), str(OUT / "baseline-epoch.json")],
    ]
    labels = ("baseline-red", "baseline-epoch")
    if args.safety:
        commands = [[sys.executable, "scripts/run_tests_isolated.py", "--", sys.executable, "-m", "pytest",
            "tests/test_job_delete_safety.py::test_scheduled_terminal_child_writeback_does_not_recreate_deleted_parent",
            "tests/test_job_delete_http_owned.py", "-q", "--tb=short", "--junitxml=" + str(OUT / "baseline-safety.xml")]]
        labels = ("baseline-safety",)
    results = []
    for label, command in zip(labels, commands):
        with (OUT / (label + ".log")).open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=target, stdout=log, stderr=subprocess.STDOUT, **command_window_options())
        results.append({"label":label, "command":command, "exit":result.returncode})
(OUT / ("baseline-safety-commands.json" if args.safety else "baseline-commands.json")).write_text(json.dumps({"baseSha":BASE,"results":results}, indent=2)+"\n", encoding="utf-8")
print(json.dumps({"baseSha":BASE,"results":results}))
