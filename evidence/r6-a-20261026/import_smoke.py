"""Import every tracked production package module under an IO audit guard.

Execute via scripts/run_tests_isolated.py. CLI/e2e/test scripts are compiled
and collected separately; importing a launch script is not a startup probe.
"""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
temporary = tempfile.TemporaryDirectory(prefix="pan-r6-import-")
owned = Path(temporary.name).resolve()
os.environ["TEMP"] = os.environ["TMP"] = str(owned)
active = True


def guard(event, args):
    if not active:
        return
    if event in {"subprocess.Popen", "os.system", "socket.connect", "socket.bind"}:
        raise RuntimeError("import attempted runtime IO: " + event)
    target = None
    if event == "open" and not isinstance(args[0], int):
        mode, flags = args[1:3]
        if (mode and any(c in mode for c in "wax+")) or (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            target = args[0]
    elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename"}:
        target = args[0]
    if target is not None and not Path(target).resolve().is_relative_to(owned):
        raise RuntimeError("import attempted write outside owned temporary root: " + event)


sys.addaudithook(guard)
results, excluded = [], []
for relative in paths:
    if not relative.startswith("packages/") or not relative.endswith(".py"):
        continue
    parts = Path(relative).parts
    if any(part in {"tests", "e2e"} for part in parts) or Path(relative).name.startswith("test_") or not all(p.isidentifier() for p in Path(relative).with_suffix("").parts):
        excluded.append(relative)
        continue
    module = relative.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
    try:
        if module == "packages.qq.plugin":
            # Like packages/qq/test_channels.py: register the plugin against a
            # test driver, without running it or connecting to a QQ gateway.
            import nonebot
            nonebot.init(_env_file=None, log_level="ERROR")
        importlib.import_module(module)
        results.append({"module": module, "status": "imported"})
    except BaseException as exc:
        results.append({"module": module, "status": "error", "type": type(exc).__name__, "message": str(exc)[:350]})
active = False
value = {"schema": "pan.r6-a.import-smoke/v1", "modules": results, "excludedLaunchOrTestFiles": excluded,
         "runtimeNetworkAndSubprocessForbidden": True, "writesRestrictedToTemporaryRoot": True,
         "passed": sum(row["status"] == "imported" for row in results),
         "failed": sum(row["status"] == "error" for row in results)}
(ROOT / "evidence/r6-a-20261026/imports.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"passed": value["passed"], "failed": value["failed"], "excluded": len(excluded)}))
for row in results:
    if row["status"] == "error":
        print(json.dumps(row, ensure_ascii=False))
temporary.cleanup()
raise SystemExit(bool(value["failed"]))
