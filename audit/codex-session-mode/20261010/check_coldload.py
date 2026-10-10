"""Serial candidate/base check of the exact performance failure, without edits."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "coldload-check"
OUT.mkdir(exist_ok=True)
BASE = Path("D:/project/Pan-main")
assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BASE).decode().strip() == "1ad4fb1fada6ecdd8af7e9e747ee78ce5568ccfc"
NODE = "tests/test_real_history_coldload_e2e.py::test_real_http_history_coldload_does_not_stall_dashboard_websocket"
relative = NODE.split("::")[0]
assert (ROOT / relative).read_bytes() == (BASE / relative).read_bytes()
env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_")}
env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
reports = []
for name, cwd in (("candidate", ROOT), ("baseline", BASE)):
    start = time.monotonic()
    with (OUT / f"{name}.txt").open("wb") as log:
        result = subprocess.run([sys.executable, str(cwd / "scripts/run_tests_isolated.py"), "--",
                                 sys.executable, "-m", "pytest", NODE, "-o", "addopts=", "-q",
                                 "--timeout=90", f"--junitxml={OUT / (name + '.xml')}"], cwd=cwd,
                                env=env, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    reports.append({"name": name, "returncode": result.returncode, "seconds": round(time.monotonic() - start, 3)})
    print(json.dumps(reports[-1]), flush=True)
(OUT / "summary.json").write_text(json.dumps({"node": NODE, "test_unchanged": True,
    "test_sha256": hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), "results": reports}, indent=2), encoding="utf-8")
raise SystemExit(0 if all(row["returncode"] == 0 for row in reports) else 1)
