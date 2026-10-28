"""CP-X1: real-host import, full repository collection and bounded contracts.

Every phase runs through run_tests_isolated.py. A failed collection remains a
failure even when the independent fast contracts pass; no ignore/deselect retry.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PHASES = ("import", "collect", "fast")

# Explicit nodes keep the gate reviewable. ProcessOwner's opposite-host classes
# retain their existing skipif reasons. Never change sys.platform to claim OS QA.
FAST_TESTS = (
    "tests/test_platform_layer.py",
    "tests/test_platform_api.py",
    "tests/test_portability_paths.py",
    "tests/test_platform_import_chain.py::test_shared_modules_have_no_unguarded_windows_imports",
    "tests/test_platform_import_chain.py::test_platform_layer_imports_only_stdlib",
    "tests/test_platform_process_contract.py",
    "tests/test_platform_process_posix_unit.py",
    "tests/test_job_posix_safety.py",
    "tests/test_job_runtime_owner.py",
    "tests/test_job_attempt_epoch.py",
    "tests/test_worker_platform_admission.py",
    "tests/test_plugin_ipc_endpoint.py",
    "tests/test_quota_native_admission.py",
    "tests/test_job_process_logs.py",
    "tests/test_job_final_executors.py",
    "tests/test_lifecycle_fast_contract.py",
    "tests/test_lifecycle_prelaunch_fence.py::test_prelaunch_original_error_idempotence_and_attempt_fence",
    "tests/test_lifecycle_prelaunch_fence.py::test_claim_or_launch_marker_forbids_prelaunch_rejection",
    "tests/test_job_final_independent_execution.py::test_fe02_concurrent_admission_never_calls_legacy_create_retry",
    "tests/test_job_final_independent_execution.py::test_fe02_stale_fence_never_settles_or_cancels",
    "tests/test_job_final_independent_execution.py::test_fe02_success_unknown_partial_cannot_be_replayed",
    "tests/test_job_final_independent_execution.py::test_fe02_retry_needs_cleanup_proof",
    "tests/test_job_final_independent_execution.py::test_fe02_ambiguous_launch_does_not_relaunch_or_change_other_intents",
    "tests/test_job_final_independent_execution.py::test_fe02_intent_and_terminal_outbox_transactions_are_atomic",
    "tests/test_job_final_independent_execution.py::test_fe02_real_detached_worker_keeps_identity_while_pan_is_absent",
    "tests/test_job_final_independent_execution.py::test_fe02_real_worker_survives_the_admitting_host_process_exit",
    "tests/test_job_final_independent_execution.py::test_fe02_explicit_reconciliation_queries_original_receipt_without_new_attempt",
    "tests/test_platform_fast_gate.py",
)

IMPORT_SERVER = """
import sys, tempfile
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='pan-fast-import-') as owned:
    from packages.core import config
    config.CONFIG_FILE = Path(owned) / 'config.json'
    import packages.web.server as server
    assert server.app is not None
    print('real server imported; sys.platform=' + sys.platform)
"""


def phase_command(phase: str, output: Path) -> list[str]:
    if phase == "import":
        child = [sys.executable, "-c", IMPORT_SERVER]
    elif phase == "collect":
        # Explicit full pytest.ini roots, including optional QQ/WeChat suites.
        # Do not use --continue-on-collection-errors or shrink to FAST_TESTS.
        child = [sys.executable, "-m", "pytest", "tests/", "packages/qq/",
                 "packages/wechat/", "--collect-only", "-q", "-ra",
                 f"--junitxml={output / 'collect.xml'}"]
    elif phase == "fast":
        child = [sys.executable, "-m", "pytest", *FAST_TESTS, "-q", "-ra",
                 "--durations=15", f"--junitxml={output / 'fast.xml'}"]
    else:
        raise ValueError(f"unknown phase: {phase}")
    return [sys.executable, str(ROOT / "scripts/run_tests_isolated.py"), "--", *child]


def run_gate(phases: tuple[str, ...], output: Path) -> int:
    from packages.jobs.window import command_window_options

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for phase in phases:
        command = phase_command(phase, output)
        started = time.monotonic()
        log = output / f"{phase}.log"
        with log.open("w", encoding="utf-8") as stream:
            completed = subprocess.run(command, cwd=ROOT, stdout=stream,
                                       stderr=subprocess.STDOUT,
                                       env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                       **command_window_options())
        results.append({"phase": phase, "exitCode": completed.returncode,
                        "seconds": round(time.monotonic() - started, 3),
                        "argv": command, "log": log.name})
        print(f"{phase}: exit={completed.returncode}; log={log}", flush=True)
    report = {"platform": sys.platform, "python": platform.python_version(),
              "results": results}
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return next((row["exitCode"] for row in results if row["exitCode"]), 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=(*PHASES, "all"), default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return run_gate(PHASES if args.phase == "all" else (args.phase,), args.output)


if __name__ == "__main__":
    # Direct script execution puts scripts/ on sys.path, not the repository.
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
