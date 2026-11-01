"""Nonexecuting original-ID history and receipts for audited P9D imports.

History is never a Run/Attempt/admission. Unrecognized raw records cannot enter
the executor. All rows commit with activation, and retries only verify them.
"""
import base64
import copy
import hashlib
import os

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


def delete_preserved(service, record, request, *, actor):
    """Explicit data archival/removal, using the current owner's transaction.

    No old payload is parsed and no resource/unknown effect is settled. Shared
    registries are not removable files. Bytes remain in permanent local audit.
    """
    from pathlib import PurePosixPath
    from packages.jobs.final_runtime.coordinator import JsonPins
    if actor.get('type') != 'user':
        raise fc.ContractError('authorization_denied', 'Explicit user deletion required')
    service._permit(actor, 'job', 'delete')
    payload = {'recordId': record['id'], **{k: v for k, v in request.items() if k != 'requestKey'}}
    root = service.owner.root

    def plain(path):
        require(path.resolve().is_relative_to(root) and not any(p.is_symlink() or
                (hasattr(p, 'is_junction') and p.is_junction()) for p in (path, *path.parents)),
                'Preserved data path contains a link or escapes the owner profile')
        return path

    with service.transaction() as db:
        def mutate(operation_id):
            if (type(request['expectedRevision']) is not int
                    or request['expectedRevision'] != record['revision']
                    or request['expectedSha256'] != record['sha256']
                    or request['expectedSourceFile'] != record['sourceFile']):
                raise fc.ContractError('revision_conflict', 'Original disposition path/hash/revision changed')
            require(not set(record['reasonCodes']) & {'active_resource', 'uncertain_effect', 'pending_notice', 'unverified_authority'},
                    'Original resource or receipt requires reconciliation; source retained')
            relative = PurePosixPath(record['sourceFile'])
            if (len(relative.parts) != 3 or relative.parts[:2] not in
                    {('background_jobs', 'jobs'), ('scheduler', 'tasks')} or relative.suffix != '.json'):
                raise fc.ContractError('unsupported_operation', 'Shared registries require record-specific offline review')
            pin_path = plain(root / 'background_jobs' / 'pins.json')
            try:
                pins = JsonPins(pin_path.parent).read()
            except (OSError, ValueError) as exc:
                raise fc.ContractError('reference_conflict', 'Original pin policy cannot be verified') from exc
            require(not pins.get(record['originalId'], False), 'Original record is pinned')
            source = plain(root / relative)
            archive_relative = 'preserved-history/' + hashlib.sha256(record['id'].encode()).hexdigest() + '-' + record['sha256'] + '.bin'
            archive = plain(service.root / archive_relative)
            archive.parent.mkdir(parents=True, exist_ok=True)
            plain(archive)
            if source.exists():
                before = source.stat()
                require(before.st_nlink == 1 and source.is_file(), 'Original source is not an exclusive plain file')
                if before.st_size > 32 * 1024 * 1024:
                    raise fc.ContractError('capacity_exceeded', 'Preserved record exceeds the bounded archive limit')
                raw = source.read_bytes()
                require(hashlib.sha256(raw).hexdigest() == record['sha256'], 'Original source hash changed')
                if archive.exists():
                    require(archive.read_bytes() == raw, 'Preserved audit backup changed')
                else:
                    with archive.open('xb') as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                current = source.stat()
                require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                        (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns), 'Original source changed before removal')
                plain(source).unlink()
            else:
                # Crash after unlink but before the existing operation receipt
                # commits: reconcile this exact archive/hash, never an effect.
                require(archive.is_file() and hashlib.sha256(archive.read_bytes()).hexdigest() == record['sha256'],
                        'Original data missing without its verified audit backup')
            result = {'deleted': True, 'originalId': record['originalId'], 'sourceFile': record['sourceFile'],
                      'sourceSha256': record['sha256'], 'revision': record['revision'],
                      'archive': archive_relative, 'unknownSettlementAttempted': False,
                      'recordType': 'historicalEvidenceOnly', 'executable': False}
            service._save(db, 'migration_receipt', 'preserved-delete:' + record['id'],
                          {**result, 'operationId': operation_id})
            return result, [], False
        return service._operation(db, actor, 'retire', request['requestKey'], payload, 'job', mutate)


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


class Archive:
    """Strictly read-only view; never construct a service, store or owner.

    HTTP authorization belongs to the caller. SQLite opens mode=ro (never the
    CreatorStore connection, whose readonly flag can still initialize storage).
    """
    def __init__(self, root):
        from pathlib import Path
        self.root = Path(root).resolve()

    def documents(self):
        import sqlite3
        path = self.root / 'event-creators' / 'creators.sqlite3'
        if not path.exists():
            return []
        # WAL readers can create/update shared memory, even with mode=ro.
        # Refuse WAL explicitly; immutable=1 would silently lose committed rows.
        if path.with_name(path.name + '-wal').exists():
            raise ValueError('Historical ledger WAL cannot be read without shared-memory side effects')
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            db.execute('PRAGMA query_only=ON')
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='final_documents'").fetchone():
                return []
            return [(kind, fc.loads(raw)) for kind, raw in db.execute(
                "SELECT kind, document FROM final_documents WHERE kind IN "
                "('migration_history','migration_unrecognized','migration_receipt') ORDER BY kind,id")]
        finally:
            db.close()

    def source(self, record):
        raw = base64.b64decode(record['rawBytesBase64'], validate=True)
        require('sha256:' + hashlib.sha256(raw).hexdigest() == record['sourceSha256'],
                'Historical source bytes/hash mismatch')
        return fc.loads(raw.decode('utf-8-sig'))

    def log(self, record):
        ref = record.get('log')
        if not isinstance(ref, dict) or not ref.get('relativePath'):
            return None
        path = self.root.parent / ref['relativePath']
        require(path.resolve().is_relative_to(self.root.parent) and not any(
            p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
            for p in (path, *path.parents)), 'Archived log path escapes profile')
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'Archived log changed')
        return raw
