"""Pan-owned Job presentation metadata, including independently executed Jobs."""
from packages.core import background_jobs
from packages.jobs.final_runtime.coordinator import JsonPins, PinMetadataUnavailable


def read(root):
    return JsonPins(background_jobs._root(root)).read()


def set_pin(root, job_id, pinned):
    """Legacy adapter. Active owners must use service.final.set_pin instead."""
    return JsonPins(background_jobs._root(root)).set(job_id, pinned)


def snapshot(root):
    """Unknown is distinct from unpinned; display failure never fails a Job operation."""
    try:
        return read(root), "available"
    except (OSError, ValueError, UnicodeError):
        return None, "unavailable"


def presentation(job_id, value, status):
    return {"pinned": value.get(job_id, False) if value is not None else None,
            "pinMetadataStatus": status}
