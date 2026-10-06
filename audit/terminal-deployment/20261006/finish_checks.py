"""Preserve the final differential check without rewriting earlier evidence."""
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    evidence = HERE / "round3"
    evidence.mkdir(exist_ok=True)
    argv = [sys.executable, "-m", "pytest", "tests/test_terminal_driver.py",
            "tests/test_terminal_deployment.py", "tests/test_terminal_pan_lifecycle_ma.py",
            "tests/test_rewind_terminal_core.py", "-o", "addopts=", "-q", "--tb=short",
            "--junitxml=" + str(evidence / "targeted.xml")]
    with (evidence / "targeted.txt").open("w", encoding="utf-8") as log:
        rc = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP).returncode
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for path in (HERE / "round2").glob("*.xml"):
        for suite in ET.parse(path).getroot().iter("testsuite"):
            for key in totals:
                totals[key] += int(suite.get(key, "0"))
    sources = ["packages/core/terminal/observer.py", "packages/core/rewind/hybrid.py",
               "scripts/check_terminal_deployment.py", "scripts/setup.bat",
               "tests/test_terminal_deployment.py", "tests/test_terminal_pan_lifecycle_ma.py",
               "tests/test_terminal_api.py"]
    anchors = {name: subprocess.check_output(["git", "hash-object", name], cwd=ROOT,
                                             text=True).strip() for name in sources}
    report = {"python": sys.executable, "targeted_exit_code": rc,
              "earlier_round2_totals_before_fixes": totals, "source_blobs": anchors}
    (evidence / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
