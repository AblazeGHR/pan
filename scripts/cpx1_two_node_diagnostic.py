"""Run the two original CI nodes with opt-in instrumentation in a fresh output."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="new, nonexistent diagnostic directory")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env["CPX1_DIAGNOSTIC_DIR"] = str(output)
    nodes = [
        "tests/test_terminal_broadcast.py::test_stream_persists_before_result_then_idle_and_does_not_wait_for_enrich",
        "tests/test_cp_x1_terminal_enrichment_diagnostic.py::test_terminal_finishes_while_enrichment_is_held",
        "tests/test_locker_order_scroll_live.py::test_locker_order_scroll_live",
        "tests/test_cpx1_locker_read_offload.py",
    ]
    command = [sys.executable, str(ROOT / "scripts/run_tests_isolated.py"), "--", sys.executable,
               "-m", "pytest", "-p", "tests.support.cpx1_two_node_diagnostic", "-q", "-ra", *nodes]
    result = subprocess.run(command, cwd=ROOT, env=env)
    (output / "result.json").write_text(json.dumps({"exitCode": result.returncode, "nodes": nodes,
                                                    "python": sys.version.split()[0]}, indent=2), encoding="utf-8")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
