"""Synthetic, disabled historical records; never create executable registrations."""
import json

from packages.core import background_jobs as jobs
from packages.core import worker


def history_record(identity, kind, **fields):
    return {"jobId": identity, "kind": kind, "name": "offline history",
            "status": "completed", "enabled": False, "paused": True,
            "createdAt": 1, "updatedAt": 1, "legacyCompat": True,
            "recordType": "historicalEvidenceOnly", "executable": False,
            "schedule": [], **fields}


def archive_job(root, identity, kind, **fields):
    value = history_record(identity, kind, **fields)
    directory = root / "jobs"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (identity + ".json")).write_text(json.dumps(value), encoding="utf-8")
    return value


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def forbid_legacy_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Historical access attempted execution")
    for name in ("start_message", "start_broadcast", "run_due_scheduled_tasks", "_spawn_background_runner"):
        if name in vars(jobs):
            monkeypatch.setattr(jobs, name, forbidden)
    for name in ("assign", "send_session", "wake_session"):
        monkeypatch.setattr(worker, name, forbidden)
