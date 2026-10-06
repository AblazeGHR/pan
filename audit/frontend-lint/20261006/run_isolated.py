"""Run frontend checks with clean Pan environment and UTF-8 result evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[2]
WEB = ROOT / "packages" / "web"
ENV = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_")}
FILES = [
    "audit-cbc-strict.mjs", "audit-lib.mjs", "codex-background-history-race.e2e.mjs",
    "codex-multi-item-order.e2e.mjs", "editor-browser-runner.mjs",
    "historySearchAcceptance.mjs", "session-message-recovery.e2e.mjs", "t053-phase1.e2e.mjs",
]
mode = sys.argv[1]
if mode == "install":
    commands = [["pnpm.cmd", "install", "--frozen-lockfile"]]
elif mode == "targeted":
    commands = [["pnpm.cmd", "exec", "eslint", *[f"e2e/{f}" for f in FILES],
                 "--format", "json", "--output-file", str(EVIDENCE / "lint-targeted.json")]]
    commands += [["node", "--check", f"e2e/{f}"] for f in FILES]
elif mode == "lint":
    commands = [["pnpm.cmd", "exec", "eslint", ".", "--format", "json",
                 "--output-file", str(EVIDENCE / "lint-final.json")]]
elif mode == "test":
    commands = [["pnpm.cmd", "exec", "vitest", "run", "--reporter=default",
                 "--reporter=junit", f"--outputFile={EVIDENCE / 'vitest-final.xml'}"]]
elif mode == "build":
    commands = [["pnpm.cmd", "build"]]
else:
    raise ValueError(mode)
results = []
for argv in commands:
    completed = subprocess.run(argv, cwd=WEB, env=ENV, check=False)
    results.append({"argv": argv, "exitCode": completed.returncode})
    if completed.returncode:
        break
summary = {"mode": mode, "root": str(ROOT), "cwd": str(WEB), "commands": results}
if mode in ("lint", "targeted") and results[0]["exitCode"] == 0:
    lint = json.loads((EVIDENCE / ("lint-final.json" if mode == "lint" else "lint-targeted.json")).read_text(encoding="utf-8"))
    summary.update(errors=sum(f["errorCount"] for f in lint), warnings=sum(f["warningCount"] for f in lint))
if mode == "test" and results[0]["exitCode"] == 0:
    xml = ET.parse(EVIDENCE / "vitest-final.xml").getroot()
    summary["junit"] = dict(xml.attrib)
    cases = list(xml.iter("testcase"))
    summary["testcases"] = len(cases)
    summary["failures"] = len(list(xml.iter("failure")))
    summary["errors"] = len(list(xml.iter("error")))
    summary["skipped"] = len(list(xml.iter("skipped")))
    if not cases or summary["failures"] or summary["errors"] or summary["skipped"]:
        raise RuntimeError("JUnit did not confirm all tests passed")
(EVIDENCE / f"{mode}-result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
sys.exit(next((r["exitCode"] for r in results if r["exitCode"]), 0))
