"""Pure application data-root policy shared across platforms and read facades.

Resolving a path never creates directories, imports a registry or starts a host.
"""
import os
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "background_jobs"


def job_root(default=None):
    """Preserve PAN_SCHEDULER_DIR > PAN_BACKGROUND_JOBS_DIR > default."""
    return Path(os.environ.get("PAN_SCHEDULER_DIR")
                or os.environ.get("PAN_BACKGROUND_JOBS_DIR")
                or (DEFAULT_ROOT if default is None else default))
