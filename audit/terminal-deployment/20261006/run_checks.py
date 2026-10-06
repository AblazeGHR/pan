"""Broad terminal acceptance using the actual practical Python environment."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    names = sorted({str(p.relative_to(ROOT)) for pattern in ("test_terminal*.py", "test_rewind*.py", "test_premerge_legacy_data.py", "test_test_isolation.py")
                    for p in (ROOT / "tests").glob(pattern)})
    anchors = {name: subprocess.check_output(["git", "hash-object", name], cwd=ROOT, text=True).strip()
               for name in ("packages/core/terminal/observer.py", "packages/core/rewind/hybrid.py", "scripts/check_terminal_deployment.py", "tests/test_terminal_deployment.py", "tests/test_terminal_pan_lifecycle_ma.py", "audit/terminal/implementation/interrupt-ma/production_probe.py")}
    (HERE / "source_blobs.json").write_text(json.dumps(anchors, indent=2), encoding="utf-8")
    (HERE / "selection.json").write_text(json.dumps({"python": sys.executable, "test_files": names}, indent=2), encoding="utf-8")
    pnpm = shutil.which("pnpm.cmd")
    evidence = HERE / "round2"
    evidence.mkdir(exist_ok=True)
    checks = [("deployment", ROOT, [sys.executable, "scripts/check_terminal_deployment.py"])]
    checks += [(Path(name).stem, ROOT, [sys.executable, "-m", "pytest", name,
                   "-o", "addopts=", "-q", "--tb=short", "-ra", "--junitxml=" + str(evidence / (Path(name).stem + ".xml"))]) for name in names]
    checks += [
        ("frontend", ROOT / "packages/web", [pnpm, "exec", "vitest", "run", "--reporter=default", "--reporter=junit", "--outputFile=" + str(HERE / "frontend.xml")]),
        ("build", ROOT / "packages/web", [pnpm, "build"]),
    ]
    results = []
    for name, cwd, argv in checks:
        print("starting: " + name, flush=True)
        with (evidence / (name + ".txt")).open("w", encoding="utf-8") as log:
            rc = subprocess.run(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)).returncode
        results.append({"name": name, "exit_code": rc})
        print(name + ": exit " + str(rc), flush=True)
        (evidence / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return int(any(r["exit_code"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
