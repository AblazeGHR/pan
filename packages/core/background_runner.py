"""Independent subprocess entrypoint for a durable background Job."""
from __future__ import annotations

import argparse
import subprocess
from packages.core import background_jobs as jobs
from packages.jobs.window import command_window_options
import os
import traceback
from contextlib import nullcontext
from packages.jobs.desktop import DesktopCommand


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    job = jobs.get(args.job_id)
    if not job:
        return 2
    if job.get("status") == "cancelled":
        return 0
    try:
        if os.name == "nt" and job.get("hideWindow", True):
            if not jobs.claim_hidden_runner(args.job_id):
                return 3
        with open(job["logPath"], "ab", buffering=0) as log:
            options = command_window_options(job.get("hideWindow", True))
            shell_command = job.get("shellCommand")
            hidden_desktop = os.name == "nt" and job.get("hideWindow", True)
            def spawn():
                if hidden_desktop:
                    return DesktopCommand(shell_command if isinstance(shell_command, str) else job["argv"],
                                          job["cwd"], log, shell=isinstance(shell_command, str))
                return subprocess.Popen(
                    shell_command if isinstance(shell_command, str) else job["argv"],
                    cwd=job["cwd"], shell=isinstance(shell_command, str),
                    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **options)
            proc = jobs.runner_spawn(args.job_id, spawn)
            with proc if hidden_desktop else nullcontext():
                return_code = proc.wait()
        current = jobs.get(args.job_id)
        if not current or current.get("status") != "cancelled":
            status = "completed" if return_code == 0 else "failed"
            jobs.runner_update(args.job_id, status=status, exitCode=return_code,
                               processTreeEnded=bool(hidden_desktop))
        return return_code
    except Exception:
        jobs.runner_update(args.job_id, status="failed", error=traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
