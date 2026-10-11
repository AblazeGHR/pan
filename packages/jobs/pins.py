"""Pan-owned Job presentation metadata, including independently executed Jobs."""
from packages.core import background_jobs


def read(root):
    path = background_jobs._root(root) / "pins.json"
    if not path.exists():
        return {}
    import json
    # Fail visibly on unreadable/corrupt metadata instead of erasing existing pins.
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or any(type(v) is not bool for v in value.values()):
        raise ValueError("invalid Job pin metadata")
    return value


def set_pin(root, job_id, pinned):
    with background_jobs._lock, background_jobs._registry_lock("pins", root):
        value = read(root)
        if pinned:
            value[job_id] = True
        else:
            value.pop(job_id, None)
        background_jobs._atomic_write(background_jobs._root(root) / "pins.json", value)
    return pinned
