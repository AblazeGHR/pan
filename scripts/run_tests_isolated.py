"""Run repository tests without inheriting a deployed Pan instance's settings.

The outer Job Runner keeps its own registry and notification configuration.
Only the test child receives this environment. Do not reuse it to start Pan.
Usage: python scripts/run_tests_isolated.py -- uv run ... python -m pytest ...
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import subprocess


def test_environment(parent: dict[str, str], repo: Path) -> dict[str, str]:
    """Allow OS/toolchain settings, not inherited application state or imports."""
    child = {key: value for key, value in parent.items()
             if not key.upper().startswith("PAN_") and key.upper() != "PYTHONPATH"}
    child["PYTHONPATH"] = str(repo.resolve())
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        child["PAN_TEST_HTTP_PORT"] = str(reservation.getsockname()[1])
    return child


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide the test command after --")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / "tests" / "conftest.py").is_file():
        parser.error("repository test isolation fixture is missing")
    env = test_environment(dict(os.environ), repo)
    print("Isolated test child: inherited PAN_* removed; cwd=" + str(repo), flush=True)
    return subprocess.run(command, cwd=repo, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
