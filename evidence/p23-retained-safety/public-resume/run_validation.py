"""Record exact isolated delivery argv, exit codes and raw output durably."""
from pathlib import Path
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SAFETY = ["tests/test_job_retained_safety_" + name + ".py"
          for name in ("protocol", "owned", "canonical", "public")]
BACKEND = ["tests/" + name + ".py" for name in (
    "test_job_final_contracts", "test_job_final_runtime", "test_job_final_http_service",
    "test_job_final_transports", "test_job_final_independent_execution", "test_job_attempt_epoch",
    "test_job_final_admission", "test_job_final_executors", "test_job_final_clients",
    "test_independent_job_service")]
BACKEND += SAFETY + ["tests/" + name + ".py" for name in (
    "test_job_delete_safety", "test_job_delete_http_owned", "test_job_cleanup_failures",
    "test_job_delete_main_combination", "test_platform_process_contract", "test_job_path_flip")]


def run(label, interpreter, arguments):
    command = [interpreter, "scripts/run_tests_isolated.py", "--", interpreter, *arguments]
    start = time.time()
    with (OUT / (label + ".log")).open("wb") as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    record = {"label": label, "argv": command, "cwd": str(ROOT), "exit": result.returncode,
              "seconds": round(time.time() - start, 3)}
    (OUT / (label + "-command.json")).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record), flush=True)
    return result.returncode


if __name__ == "__main__":
    results = []
    for label, interpreter, files in (
        ("backend-delivery", sys.executable, BACKEND),
        ("venv-delivery", str(ROOT / ".validation/p23-venv/Scripts/python.exe"), SAFETY),
    ):
        junit = (OUT / (label + ".xml")).relative_to(ROOT).as_posix()
        results.append(run(label, interpreter, ["-m", "pytest", *files, "-q", "--tb=short", "--junitxml=" + junit]))
    results.append(run("import-delivery", sys.executable, ["-c", "import packages.web.server; print('import-ok')"]))
    assert results == [1, 0, 0], results  # Three preserved original legacy reds only.
