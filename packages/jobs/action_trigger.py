"""Direct Trigger delivery using the original Event admission and operations."""
import asyncio
import copy
import json
import jsonschema
from packages.jobs import final_contracts as fc
from packages.actions import invocation
from packages.actions.command_execution import prepare, argument_defaults, OPERATIONS


def admit(service, db, trigger, event, actor, key, fact_digest):
    service._permit(actor, "trigger", "lifecycle", execution=True)
    ref = trigger["target"]["actionRef"]
    action = invocation.guard(service, db, ref, actor)
    from packages.jobs.chains import resolve
    try:
        arguments = resolve(trigger["arguments"], {}, event, {})
    except ValueError as exc:
        raise fc.ContractError("invalid_schema", "Trigger input mapping cannot be resolved") from exc
    if not jsonschema.Draft202012Validator(action["inputSchema"]).is_valid(arguments):
        raise fc.ContractError("invalid_schema", "Trigger Action mapping rejected")
    operation = action["execution"]["operation"]
    if operation not in {*OPERATIONS, "process", "job.create"}:
        raise fc.ContractError("unsupported_operation", "Action has no shared direct adapter")
    if operation != "job.create":
        prepare("process.command" if operation == "process" else operation, action["execution"]["config"],
                argument_defaults(action["inputSchema"], arguments))
    limits = trigger.get("concurrency", {"maxActive": 1, "maxPending": 64})
    pending = db.execute("""SELECT count(*) FROM final_operations
        WHERE json_extract(document, '$.delivery.triggerId')=?
        AND json_extract(document, '$.response.result.phase') IN ('admitted','calling')""", (trigger["id"],)).fetchone()[0]
    if pending >= limits["maxActive"] + limits["maxPending"]:
        return {"status": "deferred", "reason": "action_delivery_capacity"}
    def mutate(identity):
        return {"actionRef": ref, "invocationId": identity, "phase": "admitted", "status": "pending",
                "eventId": event.get("eventId", event.get("id")), "triggerId": trigger["id"],
                "triggerRevision": trigger["revision"]}, [ref], True
    response = service._operation(db, actor, "invoke", key,
        {"eventId": event.get("eventId", event.get("id")), "triggerId": trigger["id"], "factDigest": fact_digest}, "action", mutate)
    identity = response["operationId"]
    doc = json.loads(db.execute("SELECT document FROM final_operations WHERE id=?", (identity,)).fetchone()[0])
    doc["delivery"] = {"triggerId": trigger["id"], "triggerRevision": trigger["revision"],
        "actionRef": copy.deepcopy(ref), "definition": copy.deepcopy(action), "arguments": arguments,
        "authority": copy.deepcopy(actor), "authorizationRevision": service.auth.permissions_status()["revision"],
        "event": copy.deepcopy(event)}
    db.execute("UPDATE final_operations SET document=? WHERE id=?", (fc.canonical_json(doc), identity))
    return response


async def deliver(service):
    with service.transaction(readonly=True) as db:
        identities = [row[0] for row in db.execute("""SELECT id FROM final_operations
            WHERE json_extract(document, '$.delivery.triggerId') IS NOT NULL
            AND json_extract(document, '$.response.result.phase')='admitted'
            ORDER BY rowid LIMIT 256""")]
    # Bounded delivery on the existing tick, with per-Trigger durable claims.
    await asyncio.gather(*(_deliver_one(service, identity) for identity in identities[:256]))


async def _deliver_one(service, identity):
    with service.transaction() as db:
        doc = json.loads(db.execute("SELECT document FROM final_operations WHERE id=?", (identity,)).fetchone()[0])
        if doc["response"]["result"].get("phase") != "admitted":
            return
        frozen = doc["delivery"]
        trigger = service._get(db, "trigger", frozen["triggerId"])
        if trigger["state"] != "enabled":
            return
        limits = trigger.get("concurrency", {"maxActive": 1, "maxPending": 64})
        calling = db.execute("""SELECT count(*) FROM final_operations
            WHERE json_extract(document, '$.delivery.triggerId')=?
            AND json_extract(document, '$.response.result.phase')='calling'""", (trigger["id"],)).fetchone()[0]
        if calling >= limits["maxActive"]:
            return
        actor, ref = frozen["authority"], frozen["actionRef"]
        try:
            service._permit(actor, "trigger", "lifecycle", execution=True)
            invocation.guard(service, db, ref, actor)
            if service.owner.token is None:
                raise fc.ContractError("revision_conflict", "Trigger delivery requires canonical owner")
            action = frozen["definition"]
            operation = action["execution"]["operation"]
            if operation == "job.create":
                from packages.actions.job_creation import create
                result = create(service, db, action, ref, frozen["arguments"], actor, identity,
                    doc["response"]["requestKey"], source={"eventId": frozen["event"].get("eventId", frozen["event"].get("id")),
                    "triggerId": frozen["triggerId"], "triggerRevision": frozen["triggerRevision"]})
                doc["response"]["result"].update(result)
                doc["response"]["status"] = "completed"
            else:
                commands = prepare("process.command" if operation == "process" else operation,
                    action["execution"]["config"], argument_defaults(action["inputSchema"], frozen["arguments"]))
                doc["response"]["result"].update(phase="calling", status="uncertain")
                doc["response"]["status"] = "unknown"
        except (fc.ContractError, ValueError) as exc:
            doc["response"]["result"].update(phase="settled", status="failed", code=getattr(exc, "code", "invalid_schema"))
            doc["response"]["status"] = "rejected"
        db.execute("UPDATE final_operations SET document=? WHERE id=?", (fc.canonical_json(doc), identity))
        epoch = service.owner.token
    if doc["response"]["result"]["phase"] == "calling":
        try:
            await invocation.execute_prepared(service, identity, ref, actor, commands, epoch, operation, trigger_id=frozen["triggerId"])
        except fc.ContractError:
            # Calling remains uncertain if the owner/grant changed at the
            # effect boundary. Query evidence, never re-enter the effect.
            pass
