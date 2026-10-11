"""Pan-owned Job presentation metadata, including independently executed Jobs."""
from packages.core import background_jobs


def read(root):
    path = background_jobs._root(root) / "pins.json"
    import json
    # Fail visibly on unreadable/corrupt metadata instead of erasing existing pins.
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict) or any(type(v) is not bool for v in value.values()):
        raise ValueError("invalid Job pin metadata")
    return value


def set_pin(root, job_id, pinned):
    with background_jobs._lock, background_jobs._registry_lock("pins", root):
        try:
            value = read(root)
        except (OSError, ValueError, UnicodeError) as exc:
            raise PinMetadataUnavailable("Job pin metadata unavailable; restore it before editing pins") from exc
        if pinned:
            value[job_id] = True
        else:
            value.pop(job_id, None)
        background_jobs._atomic_write(background_jobs._root(root) / "pins.json", value)
    return pinned


class PinMetadataUnavailable(ValueError):
    """Strict writes cannot proceed when existing metadata is unreadable."""


def snapshot(root):
    """Unknown is distinct from unpinned; display failure never fails a Job operation."""
    try:
        return read(root), "available"
    except (OSError, ValueError, UnicodeError):
        return None, "unavailable"


def presentation(job_id, value, status):
    return {"pinned": value.get(job_id, False) if value is not None else None,
            "pinMetadataStatus": status}
