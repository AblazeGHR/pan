"""Verify two newly encountered, unrelated reds against the original base."""
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
with TemporaryDirectory(prefix="pan-r6i-extra-baseline-") as directory:
    target = Path(directory)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(target)
    command = [sys.executable, "scripts/run_tests_isolated.py", "--", sys.executable, "-m", "pytest",
        "tests/test_authorization_runtime.py::test_automatic_recovery_revocation_keeps_budget_and_never_starts_another_attempt",
        "tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http",
        "-q", "--tb=short", "--junitxml=" + str(OUT / "baseline-additional.xml")]
    with (OUT / "baseline-additional.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=target, stdout=log, stderr=subprocess.STDOUT,
                                **command_window_options())
(OUT / "baseline-additional-command.json").write_text(json.dumps({"baseSha": BASE, "command": command,
    "exit": result.returncode}, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"baseSha": BASE, "exit": result.returncode}))
raise SystemExit(result.returncode)
