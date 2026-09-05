"""Independent subprocess entrypoint for a durable background Job."""
from __future__ import annotations

import argparse
import subprocess
from packages.core import background_jobs as jobs


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
        with open(job["logPath"], "ab", buffering=0) as log:
            proc = subprocess.Popen(job["argv"], cwd=job["cwd"], stdout=log, stderr=subprocess.STDOUT)
            jobs.runner_update(args.job_id, status="running", pid=proc.pid, processCreatedAt=jobs._process_create_time(proc.pid))
            return_code = proc.wait()
        current = jobs.get(args.job_id)
        if not current or current.get("status") != "cancelled":
            status = "completed" if return_code == 0 else "failed"
            jobs.runner_update(args.job_id, status=status, exitCode=return_code)
        return return_code
    except Exception as exc:
        jobs.runner_update(args.job_id, status="failed", error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
