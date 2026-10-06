"""Read-only Terminal/Rewind prerequisite check in the deployed interpreter.

Run with the interpreter selected by Pan's launcher, not a temporary uv venv.
This checks dependencies, not provider credentials or browser functionality.
"""
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT):
    results = {}
    for name in ("pyte", "psutil", "fastapi", "uvicorn", "websockets", "httpx", "mcp", "jwt"):
        try:
            importlib.import_module(name)
            results[name] = True
        except ImportError:
            results[name] = False
    node = shutil.which("node")
    results["node"] = node is not None
    results["cbc_command"] = shutil.which("cbc") is not None
    sidecar = root / "packages/core/terminal/emulator_sidecar"
    results["sidecar"] = False
    if node and sidecar.is_dir():
        try:
            proc = subprocess.run([node, "--input-type=module", "-e",
                "import '@xterm/headless'; import '@xterm/addon-serialize';"],
                cwd=sidecar, capture_output=True, timeout=10)
            lock = json.loads((sidecar / "package-lock.json").read_text(encoding="utf-8"))
            versions = all(json.loads((sidecar / path / "package.json").read_text(encoding="utf-8"))["version"] == lock["packages"][path]["version"]
                           for path in ("node_modules/@xterm/headless", "node_modules/@xterm/addon-serialize"))
            results["sidecar"] = proc.returncode == 0 and versions
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            pass
    return {"python": sys.executable, "windows_terminal_supported": sys.platform == "win32",
            "checks": results, "ready": sys.platform == "win32" and all(results.values())}


if __name__ == "__main__":
    report = check()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ready"] else 1)
