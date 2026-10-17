"""One revocable manual/illegal lock; report policy and protocol holds are independent."""


def source(item: dict) -> str | None:
    value = item.get("queueLockSource")
    if isinstance(value, str) and value in {"manual", "illegal"}:
        return value
    if item.get("queueLockManual") or item.get("locked") is True:
        return "manual"
    return None


def set_lock(item: dict, value: str | None, *, batch: str | None = None) -> None:
    if value not in {None, "manual", "illegal"} or (value == "illegal" and not batch):
        raise ValueError("Invalid message lock source or missing Illegal batch")
    for key in ("queueLockSource", "queueLockIllegalBatch", "queueLockManual", "locked"):
        item.pop(key, None)
    if value is not None:
        item["queueLockSource"] = value
        if value == "illegal":
            item["queueLockIllegalBatch"] = batch
        else:
            item["queueLockManual"] = True


def manual_change(item: dict, manual: bool | None, report: bool | None) -> None:
    if manual is not None:
        set_lock(item, "manual" if manual else None)
        if not manual:
            item.pop("queueLockAutoReport", None)  # Existing explicit Unlock API.
    if report is True:
        item["queueLockAutoReport"] = True
    elif report is False:
        item.pop("queueLockAutoReport", None)


def migrate(item: dict) -> None:
    """Normalize legacy revocable locks without changing report-policy ownership."""
    if "queueLockSource" not in item and source(item) == "manual":
        set_lock(item, "manual")
