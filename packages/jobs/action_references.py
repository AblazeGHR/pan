"""Bounded explicit Job reference changes, preview and apply in one store."""
import copy
from packages.jobs import final_contracts as fc
from packages.jobs.final_runtime.ledger import now
from packages.actions.invocation import check_capabilities

MAX_JOBS = 64


def preview(service, body, *, actor, db=None):
    if db is None:
        with service.transaction(readonly=True) as connection:
            return preview(service, body, actor=actor, db=connection)
    from .final_service import definition
    if set(body) != {"jobs", "triggers"} or not isinstance(body["jobs"], list) or not 1 <= len(body["jobs"]) <= MAX_JOBS or not isinstance(body["triggers"], list) or len(body["triggers"]) > 256:
        raise fc.ContractError("invalid_schema", "Explicit bounded jobs/triggers lists required")
    service._permit(actor, "job", "read")
    service._permit(actor, "job", "updateConfig", execution=True)
    service._permit(actor, "action", "read")
    service._permit(actor, "trigger", "read")
    seen, rows, subscribers, blockers = set(), [], [], []
    selected_triggers = {}
    for spec in body["triggers"]:
        if not isinstance(spec, dict) or set(spec) != {"triggerId", "expectedRevision"} or spec["triggerId"] in selected_triggers:
            raise fc.ContractError("invalid_schema", "Invalid or repeated Trigger selection")
        selected_triggers[spec["triggerId"]] = spec["expectedRevision"]
    for spec in body["jobs"]:
        if not isinstance(spec, dict) or set(spec) != {"jobId", "expectedRevision", "steps"} or spec["jobId"] in seen:
            raise fc.ContractError("invalid_schema", "Invalid or repeated Job selection")
        seen.add(spec["jobId"])
        job = service._object(db, "job", spec["jobId"])
        fc.check_revision(job["configRevision"], spec["expectedRevision"])
        if job["state"] == "retired":
            raise fc.ContractError("reference_conflict", "Job retired")
        value = definition("job", job)
        if not isinstance(spec["steps"], list) or not spec["steps"] or len(spec["steps"]) > 32:
            raise fc.ContractError("invalid_schema", "Explicit Step selection required")
        steps = {s["stepId"]: s for s in value["steps"]}
        changed, differences = set(), []
        for change in spec["steps"]:
            if not isinstance(change, dict) or set(change) != {"stepId", "actionRef"} or change["stepId"] not in steps or change["stepId"] in changed:
                raise fc.ContractError("invalid_schema", "Invalid or repeated Step")
            changed.add(change["stepId"])
            step = steps[change["stepId"]]
            old = service._action_version_record(db, **_ref_args(step["actionRef"]), for_ref=True)
            new_ref = change["actionRef"]
            # Full definition validation below validates the exact Ref shape.
            step["actionRef"] = copy.deepcopy(new_ref)
            fc.validate_definition("job", value)
            new = service._action_version_record(db, **_ref_args(new_ref), for_ref=True)
            before, after = old["definition"], new["definition"]
            check_capabilities(after)
            for field in ("cancel", "reconcile"):
                if before[field]["supported"] and before[field] != after[field]:
                    blockers.append({"jobId": job["id"], "stepId": step["stepId"], "reason": field + "_contract"})
            selected_contract = step["completion"]["contract"]
            old_completion = next(c for c in before["completionContracts"] if c["id"] == selected_contract)
            new_completion = next((c for c in after["completionContracts"] if c["id"] == selected_contract), None)
            if old_completion != new_completion:
                blockers.append({"jobId": job["id"], "stepId": step["stepId"], "reason": "completion_contract"})
            if before["retry"] != after["retry"] or before["effectClass"] != after["effectClass"]:
                blockers.append({"jobId": job["id"], "stepId": step["stepId"], "reason": "effect_or_retry_contract"})
            # Literal mappings are fully validated by _references. Dynamic
            # consumers require compatible schemas; do not guess subsumption.
            if (any(b["from"] != "literal" for b in step["arguments"].values()) and before["inputSchema"] != after["inputSchema"]
                    or before["outputSchema"] != after["outputSchema"]):
                blockers.append({"jobId": job["id"], "stepId": step["stepId"], "reason": "dynamic_schema_changed"})
            differences.append({"stepId": step["stepId"], "before": {k: old[k] for k in ("id", "revision", "digest")},
                "after": new_ref, "contracts": {k: {"before": before[k], "after": after[k]} for k in
                    ("inputSchema", "outputSchema", "cancel", "reconcile", "retry", "requiredCapabilities", "completionContracts", "effectClass")}})
        service._references(db, "job", value, object_id=job["id"], enabled=True)
        rows.append({"jobId": job["id"], "expectedRevision": job["configRevision"], "definition": value, "changes": differences})
        for trigger in service._all(db, "trigger"):
            target = trigger.get("target", {})
            if trigger.get("targetJobId", target.get("jobId")) != job["id"] or trigger["state"] == "retired":
                continue
            pinned = target.get("type") == "job"
            subscribers.append({"triggerId": trigger["id"], "revision": trigger["revision"], "jobId": job["id"], "pinned": pinned})
            if pinned:
                if trigger["id"] not in selected_triggers:
                    blockers.append({"triggerId": trigger["id"], "reason": "subscriber_not_selected"})
                else:
                    service._permit(actor, "trigger", "updateConfig", execution=True)
                    fc.check_revision(trigger["revision"], selected_triggers[trigger["id"]])
    pinned_ids = {r["triggerId"] for r in subscribers if r["pinned"]}
    if set(selected_triggers) - pinned_ids:
        raise fc.ContractError("reference_conflict", "Trigger selection outside affected pinned subscribers")
    result = {"jobs": rows, "subscribers": subscribers, "blockers": blockers,
        "authorizationRevision": service.auth.permissions_status()["revision"], "existingRuns": "frozen", "scope": body}
    return {**result, "previewDigest": fc.digest(result)}


def _ref_args(ref):
    return {"action_id": ref["id"], "revision": ref["revision"], "digest": ref["digest"]}


def apply(service, body, *, actor):
    if set(body) != {"scope", "previewDigest", "requestKey"}:
        raise fc.ContractError("invalid_schema", "Apply requires scope, previewDigest and requestKey")
    service._permit(actor, "job", "updateConfig", execution=True)
    with service.transaction() as db:
        def mutate(identity):
            from .final_service import object_ref
            reviewed = preview(service, body["scope"], actor=actor, db=db)
            if reviewed["previewDigest"] != body["previewDigest"]:
                raise fc.ContractError("revision_conflict", "Reference preview changed")
            if reviewed["blockers"]:
                raise fc.ContractError("reference_conflict", "Reference update has blockers")
            refs = []
            for row in reviewed["jobs"]:
                job = service._get(db, "job", row["jobId"])
                item = job["canonical"]
                item.update(copy.deepcopy(row["definition"]))
                item.update(configRevision=item["configRevision"] + 1, updatedAt=now())
                fc.validate_object("job", item)
                job.update(config=copy.deepcopy(row["definition"]), configRevision=item["configRevision"])
                service._save(db, "job", item["id"], job)
                refs.append(object_ref("job", item))
                for subscriber in reviewed["subscribers"]:
                    if subscriber["jobId"] == item["id"] and subscriber["pinned"]:
                        trigger = service._get(db, "trigger", subscriber["triggerId"])
                        trigger["target"]["configRevision"] = item["configRevision"]
                        trigger.update(revision=trigger["revision"] + 1, updatedAt=now())
                        fc.validate_object("trigger", trigger)
                        service._save(db, "trigger", trigger["id"], trigger)
            return {"jobs": refs, "subscribers": reviewed["subscribers"], "existingRuns": "frozen"}, refs, False
        return service._operation(db, actor, "update", body["requestKey"], {"referenceUpdate": body}, "job", mutate)


def references(service, action_id, *, actor, cursor=None, limit=100):
    service._permit(actor, "action", "read")
    service._permit(actor, "job", "read")
    with service.transaction(readonly=True) as db:
        service._get(db, "action", action_id)
        rows = []
        for stored in service._all(db, "job"):
            job = stored["canonical"]
            steps = [{"stepId": s["stepId"], "actionRef": copy.deepcopy(s["actionRef"])}
                     for s in job["steps"] if s["actionRef"]["id"] == action_id]
            if steps:
                rows.append({"jobId": job["id"], "name": job["name"], "configRevision": job["configRevision"],
                             "state": job["state"], "steps": steps})
        return service._page(rows, "action-job-references:" + action_id, cursor, limit, actor)
