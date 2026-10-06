"""Frozen-source rewind checks; invoked through run_tests_isolated.py."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WEB = ROOT / "packages/web"
PNPM = shutil.which("pnpm.cmd")
UV = shutil.which("uv")


def main():
    if not PNPM or not UV:
        raise RuntimeError("Required test tool unavailable")
    anchors = {}
    for name in ["packages/web/server.py", "packages/web/src/components/chat/MessageBubble.tsx", "packages/web/src/components/chat/MessageBubble.rewind.test.tsx", "tests/test_rewind_api.py"]:
        anchors[name] = subprocess.check_output(["git", "hash-object", name], cwd=ROOT, text=True).strip()
    (HERE / "source_blobs.json").write_text(json.dumps(anchors, indent=2), encoding="utf-8")
    checks = [
        ("python", ROOT, [UV, "run", "--no-project", "--python", "E:/software/miniforge/python.exe", "--with-requirements", "minimal-requirements.txt", "--with", "pytest", "--with", "pytest-timeout", "--with", "python-dotenv", "--", "python", "-m", "pytest", *[str(p.relative_to(ROOT)) for p in sorted((ROOT / "tests").glob("test_rewind*.py"))], "-o", "addopts=", "-q", "--junitxml=" + str(HERE / "python.xml")]),
        ("vitest", WEB, [PNPM, "exec", "vitest", "run", "--reporter=default", "--reporter=junit", "--outputFile=" + str(HERE / "vitest.xml")]),
        ("lint", WEB, [PNPM, "lint"]),
        ("build", WEB, [PNPM, "build"]),
    ]
    results = []
    for name, cwd, argv in checks:
        with (HERE / (name + ".txt")).open("w", encoding="utf-8") as log:
            rc = subprocess.run(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT).returncode
        results.append({"name": name, "exit_code": rc})
        print(name + ": exit " + str(rc), flush=True)
    (HERE / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return int(any(r["exit_code"] for r in results))


if __name__ == "__main__":
    sys.exit(main())
