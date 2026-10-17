"""One revocable message lock; protocol holds and edit leases are separate gates.

The two old booleans remain compatibility/provenance metadata. Once source is
present it is authoritative; manual and illegal can never stack. Auto-report
is a manual policy-owned lock, released by Resume reports unless explicitly
claimed by a user Lock. It is never an Illegal recovery entitlement.
"""


def source(item: dict) -> str | None:
    value = item.get("queueLockSource")
    if isinstance(value, str) and value in {"manual", "illegal"}:
        return value
    if item.get("queueLockManual") or item.get("queueLockAutoReport") or item.get("locked") is True:
        return "manual"
    return None


def set_lock(item: dict, value: str | None, *, batch: str | None = None,
             report_policy: bool = False) -> None:
    if value not in {None, "manual", "illegal"} or (value == "illegal" and not batch):
        raise ValueError("Invalid message lock source or missing Illegal batch")
    for key in ("queueLockSource", "queueLockIllegalBatch", "queueLockManual",
                "queueLockAutoReport", "locked"):
        item.pop(key, None)
    if value is not None:
        item["queueLockSource"] = value
        if value == "illegal":
            item["queueLockIllegalBatch"] = batch
        else:
            item["queueLockAutoReport" if report_policy else "queueLockManual"] = True


def manual_change(item: dict, manual: bool | None, report: bool | None) -> None:
    if manual is not None:
        set_lock(item, "manual" if manual else None)
    elif report is True:
        # Explicit manual locks win over the report policy.
        if source(item) != "manual" or not item.get("queueLockManual"):
            set_lock(item, "manual", report_policy=True)
    elif report is False and item.get("queueLockAutoReport") and source(item) == "manual":
        set_lock(item, None)


def migrate(item: dict) -> None:
    """Only normalize lock-bearing rows; never invent an Illegal history."""
    if "queueLockSource" not in item and source(item) == "manual":
        set_lock(item, "manual", report_policy=bool(
            item.get("queueLockAutoReport") and not item.get("queueLockManual")))
