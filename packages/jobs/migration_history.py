"""Nonexecuting original-ID history and receipts for audited P9D imports.

History is never a Run/Attempt/admission. Unrecognized raw records cannot enter
the executor. All rows commit with activation, and retries only verify them.
"""
import base64
import copy
import hashlib

from packages.jobs import final_contracts as fc


def require(ok, message):
    if not ok:
        raise fc.ContractError("reference_conflict", message)


def _raw(bundle, entry):
    backup = bundle["sourceBackups"][entry["sourceFile"]]
    raw = base64.b64decode(backup["bytes"], validate=True)
    require("sha256:" + hashlib.sha256(raw).hexdigest() == entry["originalDigest"] == backup["sha256"],
            "Historical source bytes/hash mismatch")
    return raw, backup


def rows_for(bundle):
    receipt = bundle["authorizationAudit"]
    rows = []
    context = bundle["authorizationContext"]
    for entry in bundle["entries"]:
        raw, backup = _raw(bundle, entry)
        source = fc.loads(raw.decode("utf-8-sig"))
        doc = {"migrationId": entry["migrationId"], "originalId": entry["id"], "canonicalIds": entry["targetIds"],
               "recordType": "historicalEvidenceOnly", "executable": False, "sourceFile": entry["sourceFile"],
               "sourceSha256": backup["sha256"], "rawBytesBase64": backup["bytes"], "source": source,
               "authorizationReceiptDigest": receipt["receiptDigest"],
               "log": context.get("historyLogs", {}).get(entry["migrationId"])}
        rows.append(("migration_history", entry["migrationId"], doc))
        key = source.get("terminalEventId")
        if key and source.get("notificationState") == "delivered":
            rows.append(("migration_receipt", key, {"originalKey": key, "originalId": entry["id"],
                "canonicalIds": entry["targetIds"], "state": "historical-delivered",
                "basis": "source snapshot reports delivered; adopted without re-delivery",
                "externalDeliveryVerified": False, "deliveryAttempted": False,
                "sourceSha256": backup["sha256"], "authorizationReceiptDigest": receipt["receiptDigest"]}))
    for entry in bundle["preserved"]:
        if not entry.get("migrationId"):
            continue  # Unclassified metadata stays in sourceBackups; not a Job.
        _, backup = _raw(bundle, entry)
        require(entry.get("recognition") == "unrecognized" and entry["targetIds"] == []
                and entry["executable"] is False, "Preserved record must stay unrecognized/nonexecutable")
        rows.append(("migration_unrecognized", entry["migrationId"], {
            "migrationId": entry["migrationId"], "originalId": entry["originalId"], "sourceFile": entry["sourceFile"],
            "sourceSha256": backup["sha256"], "rawBytesBase64": backup["bytes"], "recognition": "unrecognized",
            "targetIds": [], "executable": False, "reasonCodes": entry["reasonCodes"],
            "unknownSettlementAttempted": False, "authorizationReceiptDigest": receipt["receiptDigest"]}))
    require(len({(kind, key) for kind, key, _ in rows}) == len(rows), "Duplicate original history/receipt key")
    return rows


def install(service, db, bundle, *, verify_only=False):
    for kind, key, value in rows_for(bundle):
        existing = db.execute("SELECT document FROM final_documents WHERE kind=? AND id=?", (kind, key)).fetchone()
        if existing:
            require(fc.loads(existing[0]) == value, "Historical record/receipt drift")
        else:
            require(not verify_only, "Committed migration history/receipt missing")
            service._save(db, kind, key, value)


def read_original(service, original_id, *, actor, unrecognized=False):
    service._permit(actor, "job", "read")
    kind = "migration_unrecognized" if unrecognized else "migration_history"
    with service.transaction(readonly=True) as db:
        rows = [doc for doc in service._all(db, kind) if doc["originalId"] == original_id]
    require(len(rows) == 1, "Original history identity missing or ambiguous")
    return copy.deepcopy(rows[0])


def read_receipt(service, original_key, *, actor):
    service._permit(actor, "job", "read")
    with service.transaction(readonly=True) as db:
        return service._get(db, "migration_receipt", original_key)


def read_log(service, original_id, *, actor):
    record = read_original(service, original_id, actor=actor)
    ref = record.get("log")
    require(isinstance(ref, dict) and ref.get("relativePath"), "No archived log reference")
    root = service.root.parent
    path = root / ref["relativePath"]
    require(path.resolve().is_relative_to(root) and not any(p.is_symlink() or
            (hasattr(p, "is_junction") and p.is_junction()) for p in (path, *path.parents)),
            "Archived log path escapes profile")
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ref["sha256"], "Archived log changed")
    return raw
