"""Fresh-process QQ comparison with exact baseline product modules, no edits."""
import importlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BASE = "4cf100f38051be48ab9e3b81155bb4ed0be00950"
for name in ("packages.jobs.independent_executor", "packages.jobs.independent_execution_runner", "packages.jobs.final_service"):
    module = importlib.import_module(name)
    source = subprocess.check_output(["git", "show", BASE+":"+name.replace(".", "/")+".py"], cwd=ROOT)
    exec(compile(source, module.__file__, "exec"), module.__dict__)

import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
