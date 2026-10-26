"""Explicit WP08 v2 bundle activation. No legacy source/store loader."""
import copy
import hashlib
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

from packages.jobs import final_contracts as fc
from packages.jobs.final_runtime.ledger import aggregate


def require(ok, message):
    if not ok:
        raise fc.ContractError("reference_conflict", message)


def restore_cursor(service, item, entry):
    """Adopt the reviewed next-fire fact, never recalculate from createdAt."""
    from packages.scheduler.providers import instant, timestamp

    cursor = entry.get("cursor")
    require(isinstance(cursor, dict) and set(cursor) == {"nextFireAt", "lastFireAt", "runCount", "maxRuns"},
            "Unsupported migration cursor")
    rules = item["config"]["rules"]
    require(item["providerRef"]["type"] == "time" and len(rules) == 1, "Ambiguous migration cursor rules")
    limit = item["config"].get("driveLimit", {})
    require((cursor["runCount"] is None or type(cursor["runCount"]) is int and cursor["runCount"] == 0)
            and cursor["maxRuns"] == limit.get("maxEvents")
            and item["producedCount"] == 0, "Migration counter domain mismatch")
    rule = rules[0]
    provider = service.creators.registry.get(item["providerRef"])
    fire = cursor["nextFireAt"]
    occurrence = None
    if fire is not None:
        try:
            if type(fire) in (int, float):
                point = Decimal(str(fire))
                require(point.is_finite(), "Non-finite migration cursor")
            else:
                wall = datetime.fromisoformat(fire.replace("Z", "+00:00"))
                if wall.tzinfo is None:
                    # Only cron retains a timezone in the canonical definition.
                    # An interval/once naive fact cannot safely infer host TZ.
                    require(bool(rule.get("timezone")), "Migration cursor lacks an explicit timezone")
                    zone = provider.zone(rule["timezone"])
                    first, second = wall.replace(tzinfo=zone, fold=0), wall.replace(tzinfo=zone, fold=1)
                    require(first.utcoffset() == second.utcoffset() and
                            first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == wall,
                            "Ambiguous/nonexistent migration cursor")
                    fire = first.isoformat()
                point = instant(fire)
            with localcontext() as context:
                context.prec = max(50, len(point.as_tuple().digits) + 20)
                before = point - Decimal(1).scaleb(min(point.as_tuple().exponent, -6))
            occurrence = provider.next_after(rule, timestamp(before))
            require(occurrence is not None and instant(occurrence["at"]) == point,
                    "Migration cursor is outside the canonical schedule grid")
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            raise fc.ContractError("reference_conflict", "Invalid migration time cursor") from exc
    else:
        require(entry["desiredState"] != "enabled", "Active migration schedule has no next-fire fact")
    item["cursor"]["rules"][rule["ruleId"]].update(
        nextFireAt=occurrence["at"] if occurrence else None, nextOccurrence=occurrence)
    item["migrationCursor"] = copy.deepcopy(cursor)


def activate_bundle(service, *, path, expected_hash, expected_plan_digest, active_ids):
    from packages.jobs.final_service import definition

    target = Path(path)
    require(target.is_absolute() and not target.is_symlink(), "Explicit absolute canonical bundle required")
    raw = target.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected_hash, "Canonical bundle hash mismatch")
    bundle = fc.loads(raw.decode("utf-8"))
    sealed = {k: v for k, v in bundle.items() if k != "bundleDigest"}
    require(fc.digest(sealed) == bundle.get("bundleDigest"), "Canonical bundle digest mismatch")
    require(bundle.get("toolSchema") == "pan.job-final-migration.bundle/v1" and
            bundle.get("mappingVersion") == "1" and bundle.get("state") == "committed-offline",
            "Unsupported canonical bundle")
    require(bundle.get("planDigest") == expected_plan_digest, "Canonical planDigest mismatch")
    source = Path(bundle["root"]).resolve()
    require(source == service.root.parent, "Canonical source/fence profile mismatch")
    require(not target.resolve().is_relative_to(source), "Canonical bundle must be outside source root")
    require(isinstance(active_ids, list) and len(active_ids) == len(set(active_ids)), "Explicit active IDs required")
    records = bundle["canonical"]
    by_id = {}
    for record in records:
        kind, obj = record["kind"], record["object"]
        require(kind in {"job", "trigger", "event_creator", "template"}, "Unexpected bundle kind")
        fc.validate_object(kind, obj)
        require(obj["profileId"] == service.profile_id, "Canonical bundle profile mismatch")
        require(obj["state"] == ("draft" if kind == "template" else "disabled"), "Bundle must contain disabled definitions")
        require(obj["id"] not in by_id, "Duplicate bundle object")
        by_id[obj["id"]] = record
    eligible = set()
    allocated = []
    entries_by_id = {}
    for entry in bundle["entries"]:
        require(entry.get("oldRecordReadOnly") is True and entry.get("status") == "verified-offline",
                "Offline read-only/finalization prerequisite missing")
        allocated.extend(entry["targetIds"])
        entries_by_id.update({object_id: entry for object_id in entry["targetIds"]})
        if entry["desiredState"] == "enabled":
            eligible.update(entry["targetIds"])
    require(len(allocated) == len(set(allocated)) and set(allocated) == set(by_id), "Incomplete bundle allocation graph")
    require(set(active_ids) <= eligible and all(by_id[i]["kind"] != "template" for i in active_ids),
            "Only explicitly active eligible definitions may enable")
    activation = {"bundleDigest": bundle["bundleDigest"], "planDigest": expected_plan_digest,
                  "activeIds": sorted(active_ids), "runtimeActivated": True, "runtimeFenceVerified": True}
    with service.transaction() as db:
        old = db.execute("SELECT document FROM final_documents WHERE kind='runtime_activation'").fetchone()
        if old:
            require(fc.loads(old[0]) == activation, "Activation conflicts with committed runtime graph")
            return activation
        # Entire graph, authorities, references and activation receipt share
        # one Creator DB transaction. Any failure rolls all imported rows back.
        for record in records:
            kind, obj = record["kind"], copy.deepcopy(record["object"])
            require(not db.execute("SELECT 1 FROM final_documents WHERE id=?", (obj["id"],)).fetchone()
                    and service.creators.get(obj["id"]) is None, "Target already exists")
            actor = copy.deepcopy(obj["createdBy"])
            actor["revision"] = service.auth.permissions_status()["revision"]
            service._permit(actor, kind, "create")
            if kind == "event_creator":
                item = service.creators.create(definition(kind, obj), request_key="activate:" + obj["id"],
                    creator_id=obj["id"], created_by=obj["createdBy"], owner=obj["owner"],
                    state="disabled", at=obj["createdAt"], migration=True)
                item.update(obj, ownerEpoch=service.owner.token[0])
                restore_cursor(service, item, entries_by_id[obj["id"]])
                service.creators._write(db, item)
            else:
                stored = obj
                if kind == "job":
                    stored = {"jobId": obj["id"], "canonical": obj, "config": definition(kind, obj),
                              "configRevision": obj["configRevision"]}
                    aggregate(stored, service.root)["profileId"] = service.profile_id
                service._save(db, kind, obj["id"], stored)
            db.execute("INSERT INTO final_authorities VALUES(?,?,?)", (kind, obj["id"], fc.canonical_json(actor)))
        for record in records:
            kind, obj = record["kind"], record["object"]
            service._references(db, kind, definition(kind, obj), object_id=obj["id"])
            if kind == "trigger" and obj["creatorSelector"]["mode"] == "ids":
                for cid in obj["creatorSelector"]["ids"]:
                    service.creators.bind_link(cid, obj["id"], obj["targetJobId"])
        # Mark every requested state before checking enabled dependencies.
        for object_id in active_ids:
            kind = by_id[object_id]["kind"]
            if kind == "event_creator":
                item = service.creators.get(object_id)
                item.update(state="enabled", stateRevision=item["stateRevision"] + 1)
                service.creators._write(db, item)
            else:
                stored = service._get(db, kind, object_id)
                item = stored["canonical"] if kind == "job" else stored
                item.update(state="enabled", stateRevision=item["stateRevision"] + 1)
                service._save(db, kind, object_id, stored)
        for object_id in active_ids:
            kind = by_id[object_id]["kind"]
            obj = service._object(db, kind, object_id)
            service._references(db, kind, definition(kind, obj), object_id=object_id, enabled=True)
            actor = fc.loads(
                db.execute("SELECT document FROM final_authorities WHERE kind=? AND id=?", (kind, object_id)).fetchone()[0])
            service._permit(actor, kind, "execute" if kind == "job" else "lifecycle", execution=True)
        service._save(db, "runtime_activation", bundle["operationId"], activation)
    return activation
