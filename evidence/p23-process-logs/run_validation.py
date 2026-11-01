"""Exact isolated commands and preservation of previous P23 safety artifacts."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SAFETY = ["tests/test_job_retained_safety_" + name + ".py" for name in ("protocol", "owned", "canonical", "public")]
LOGS = ["tests/test_job_process_logs.py"]
CONTRACTS = ["tests/" + name + ".py" for name in (
    "test_job_final_contracts", "test_job_final_runtime", "test_job_final_http_service", "test_job_final_transports",
    "test_job_final_independent_execution", "test_job_final_executors", "test_job_final_clients", "test_job_attempt_epoch",
    "test_independent_job_service", "test_job_path_flip", "test_platform_process_contract",
    "test_job_retained_canonical_pin_resolution", "test_job_retained_canonical_pins")]


def run(label, interpreter, arguments, axis=None):
    if any((OUT / (label + suffix)).exists() for suffix in (".log", ".xml", "-command.json")):
        raise FileExistsError("Preserve existing evidence; choose a new label before rerunning " + label)
    command = [str(interpreter), "scripts/run_tests_isolated.py", "--", str(interpreter), *arguments]
    previous = ROOT / "evidence/p23-retained-safety/public-resume" / ("native" if axis == "system" else "venv")
    snapshot = {p: p.read_bytes() for p in previous.glob("*.json")} if axis else {}
    started = time.time()
    print("Starting " + label, flush=True)
    try:
        with (OUT / (label + ".log")).open("wb") as log:
            child = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        if axis:
            target = OUT / ("safety-" + axis)
            target.mkdir(exist_ok=True)
            for path in previous.glob("*.json"):
                shutil.copyfile(path, target / path.name)
        record = {"argv": command, "cwd": str(ROOT), "exit": child.returncode,
                  "seconds": round(time.time() - started, 3), "sourceBase": "67d125de794132f72011012d8c6814110aafe8ef"}
        (OUT / (label + "-command.json")).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"label": label, "exit": child.returncode, "seconds": record["seconds"]}), flush=True)
        return child.returncode
    finally:
        for path, raw in snapshot.items():
            path.write_bytes(raw)


if __name__ == "__main__":
    results = []
    for label, interpreter, files, axis in (
        ("system-final", sys.executable, LOGS + SAFETY + CONTRACTS, "system"),
        ("venv-final", "D:/project/Pan-main/.venv/Scripts/python.exe", LOGS + SAFETY, "venv"),
    ):
        results.append(run(label, interpreter, ["-m", "pytest", *files, "-q", "--tb=short",
            "--junitxml=evidence/p23-process-logs/" + label + ".xml"], axis))
    results.append(run("import-final", sys.executable, ["-c", "import packages.web.server; print('import-ok')"]))
    assert results == [0, 0, 0], results
