"""Run actual retired route probes from an unmodified bac726cf export."""
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile

from packages.jobs.window import command_window_options

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "bac726cf84c0510dff8618afb82e227e660f590c"
data = subprocess.check_output(["git", "archive", "--format=zip", BASE,
    "packages", "tests", "scripts", "pytest.ini"], cwd=ROOT, **command_window_options())
with TemporaryDirectory(prefix="pan-r6i-route-baseline-") as directory:
    target = Path(directory)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(target)
    command = [sys.executable, "scripts/run_tests_isolated.py", "--", sys.executable, "-m", "pytest",
        "tests/test_scheduler_host_retirement.py::test_production_scheduler_writes_cannot_reach_either_owner",
        "-q", "--tb=short", "--junitxml=" + str(OUT / "baseline-routes.xml")]
    with (OUT / "baseline-routes.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=target, stdout=log, stderr=subprocess.STDOUT,
                                **command_window_options())
record = {"baseSha": BASE, "command": command, "exit": result.returncode,
    "sourceWrites": ["POST /api/scheduler/event-sources", "PUT /api/scheduler/event-sources/{id}",
                     "DELETE /api/scheduler/event-sources/{id}"],
    "expected": {"anonymous": 401, "user": 410, "delegated": 410},
    "productionApp": True, "hostsAndStoresForbidden": True}
(OUT / "baseline-routes.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record))
raise SystemExit(result.returncode)
