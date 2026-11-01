"""Run each original red node unchanged; never replace them with successors."""
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from packages.jobs.window import command_window_options

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
nodes = []
for label in ("baseline-red", "baseline-safety"):
    for test in ET.parse(ROOT / "evidence/r6i-resume" / (label + ".xml")).iter("testcase"):
        if test.find("failure") is not None:
            nodes.append(test.attrib["classname"].replace(".", "/") + ".py::" + test.attrib["name"])
assert len(nodes) == len(set(nodes)) == 41
command = [sys.executable, "scripts/run_tests_isolated.py", "--", sys.executable, "-m", "pytest",
           *nodes, "-q", "--tb=short", "--junitxml=" + str(OUT / "original-red-current.xml")]
with (OUT / "original-red-current.log").open("w", encoding="utf-8") as log:
    result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                            **command_window_options())
(OUT / "original-red-command.json").write_text(json.dumps({"command": command, "exit": result.returncode}, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"nodes": len(nodes), "exit": result.returncode}))
raise SystemExit(result.returncode)
