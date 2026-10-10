"""Frozen-source regression, with durable Job notification and isolated stores."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
PYTHON_ONLY = "--python-only" in sys.argv
OUT = Path(__file__).resolve().parent / ("python-rerun" if PYTHON_ONLY else "evidence")
OUT.mkdir(parents=True, exist_ok=True)
ENV = {key: value for key, value in os.environ.items() if not key.upper().startswith("PAN_")}
ENV.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
PNPM = shutil.which("pnpm.cmd") or shutil.which("pnpm")
if not PNPM:
    raise RuntimeError("pnpm unavailable")


def anchors():
    args = ["packages", "tests", "docs/design/CODEX_NATIVE_CONTROL_20261010.md"]
    names = subprocess.check_output(["git", "diff", "--name-only", "1ad4fb1f", "--", *args], cwd=ROOT).decode("utf-8").splitlines()
    names += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "--", *args], cwd=ROOT).decode("utf-8").splitlines()
    return {name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in sorted(set(names)) if (ROOT / name).is_file()}


def run(label, argv, cwd):
    start = time.monotonic()
    print(f"START {label}", flush=True)
    with (OUT / f"{label}.txt").open("wb") as log:
        result = subprocess.run(argv, cwd=cwd, env=ENV, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    report = {"label": label, "returncode": result.returncode,
              "seconds": round(time.monotonic() - start, 3), "log": f"{label}.txt"}
    print(json.dumps(report), flush=True)
    return report


def python_suite():
    return [run("python-full", [sys.executable, str(ROOT / "scripts/run_tests_isolated.py"), "--",
                               sys.executable, "-m", "pytest", "tests", "-o", "addopts=", "-q",
                               "--timeout=90", f"--junitxml={OUT / 'python-full.xml'}"], ROOT)]


def frontend_suite():
    web = ROOT / "packages/web"
    return [
        run("frontend-lint", [PNPM, "exec", "eslint", "."], web),
        run("frontend-vitest", [PNPM, "exec", "vitest", "run", "--maxWorkers=2", "--minWorkers=1",
                               "--reporter=default", "--reporter=junit", f"--outputFile.junit={OUT / 'frontend-vitest.xml'}"], web),
        run("frontend-build", [PNPM, "build"], web),
        run("browser-native", ["node", "e2e/native-control.e2e.mjs"], web),
    ]


before = anchors()
if PYTHON_ONLY:
    reports = python_suite()
else:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        py = pool.submit(python_suite)
        fe = pool.submit(frontend_suite)
        reports = py.result() + fe.result()
after = anchors()
summary = {"base": "1ad4fb1f", "source_hash_convention": "sha256 LF-normalized worktree bytes",
           "source_anchors": before, "source_unchanged": before == after, "results": reports,
           "passed": before == after and all(row["returncode"] == 0 for row in reports)}
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"passed": summary["passed"], "source_unchanged": summary["source_unchanged"]}), flush=True)
raise SystemExit(0 if summary["passed"] else 1)
