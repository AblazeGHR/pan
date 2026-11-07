"""Job-producing Actions over the canonical definition/admission transaction."""
import copy
import jsonschema
from packages.jobs import final_contracts as fc
from packages.jobs.final_runtime.ledger import aggregate, now, principal
from .command_execution import argument_defaults, product_definition as command_definition

OPERATION = "job.create"


def product_definition():
    value = command_definition("process.command")
    value.update(name="Create Job", inputSchema={"type": "object", "properties": {"jobDefinition": {"type": "object"}}, "required": ["jobDefinition"], "additionalProperties": False}, outputSchema={"type": "object"})
    value["execution"] = {"operation": OPERATION, "config": {"jobDefinition": {"$argument": "jobDefinition"}}}
    value["requiredCapabilities"] = []
    value["providerRef"] = {"id": "pan.jobs", "releaseDigest": fc.digest("pan.jobs.create.v1"), "generation": 1}
    value["cancel"] = {"supported": False}
    value["completionContracts"] = [{"id": "job-created", "schema": {"type": "object"}, "terminalProofSchema": {"type": "object"}}]
    value["defaultCompletion"] = "job-created"
    return value


def render(value, arguments):
    if isinstance(value, dict):
        if set(value) == {"$argument"}:
            current = arguments
            for part in value["$argument"].split("."):
                if not isinstance(current, dict) or part not in current:
                    raise fc.ContractError("invalid_schema", "Missing Job template argument")
                current = current[part]
            return copy.deepcopy(current)
        return {key: render(item, arguments) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, arguments) for item in value]
    return copy.deepcopy(value)


def create(service, db, action, ref, arguments, actor, identity, request_key, *, source=None):
    from packages.jobs.final_service import object_ref
    service._permit(actor, "job", "create", execution=True)
    cfg = action["execution"]["config"]
    if set(cfg) - {"jobDefinition", "start"} or "jobDefinition" not in cfg or type(cfg.get("start", False)) is not bool:
        raise fc.ContractError("invalid_schema", "job.create requires jobDefinition and optional boolean start")
    args = argument_defaults(action["inputSchema"], arguments)
    value = fc.validate_definition("job", render(cfg["jobDefinition"], args))
    service._references(db, "job", value)
    item = service._envelope("job", value, actor, state="disabled")
    provenance = {"actionRef": copy.deepcopy(ref), "operationId": identity, "requestKey": request_key,
        "argumentsDigest": fc.digest(arguments), "configDigest": fc.digest(value)}
    if source:
        provenance["source"] = copy.deepcopy(source)
    job = {"jobId": item["id"], "canonical": item, "config": copy.deepcopy(value),
           "configRevision": item["configRevision"], "provenance": provenance}
    aggregate(job, service.root)["profileId"] = service.profile_id
    service._save(db, "job", item["id"], job)
    db.execute("INSERT INTO final_authorities VALUES(?,?,?)", ("job", item["id"], fc.canonical_json(actor)))
    result = {"status": "completed", "phase": "settled", "actionRef": ref,
              "jobId": item["id"], "creation": {"status": "completed", "jobRef": object_ref("job", item)},
              "start": {"status": "not_requested"}, "provenance": provenance}
    # Creation has its own completed result even when start is refused. No effect
    # is dispatched here; an admitted Run belongs to the original Job driver.
    if cfg.get("start"):
        pending_changes = getattr(service._change_local, "pending", None)
        checkpoint = len(pending_changes) if pending_changes is not None else 0
        db.execute("SAVEPOINT action_job_start")
        try:
            service._permit(actor, "job", "lifecycle", execution=True)
            service._permit(actor, "job", "execute", execution=True)
            service._references(db, "job", value, object_id=item["id"], enabled=True)
            item.update(state="enabled", stateRevision=item["stateRevision"] + 1, updatedAt=now())
            job["canonical"] = item
            service._save(db, "job", item["id"], job)
            admission = service._admit(db, job, {"input": value["arguments"],
                "expectedRevision": item["configRevision"]}, actor, identity,
                cause={"type": "manual"})
            result["start"] = {"status": "accepted", **admission}
        except fc.ContractError as exc:
            db.execute("ROLLBACK TO action_job_start")
            if pending_changes is not None:
                del pending_changes[checkpoint:]
            result["start"] = {"status": "rejected", "code": exc.code}
        finally:
            db.execute("RELEASE action_job_start")
    return result


def invoke(service, action_id, body, *, actor, source=None):
    from .invocation import guard
    service._permit(actor, "action", "read")
    payload = {"actionRef": body["actionRef"], "arguments": body["arguments"]}
    if source is not None:
        payload["source"] = copy.deepcopy(source)
    with service.transaction() as db:
        def mutate(identity):
            ref = body["actionRef"]
            if ref["id"] != action_id:
                raise fc.ContractError("invalid_schema", "Action identity mismatch")
            action = guard(service, db, ref, actor)
            if not jsonschema.Draft202012Validator(action["inputSchema"]).is_valid(body["arguments"]):
                raise fc.ContractError("invalid_schema", "Action arguments rejected")
            if service.owner.token is None:
                raise fc.ContractError("revision_conflict", "Invocation requires canonical owner")
            result = create(service, db, action, ref, body["arguments"], actor, identity, body["requestKey"], source=source)
            return result, [ref], False
        return service._operation(db, actor, "invoke", body["requestKey"], payload, "action", mutate)


async def job_execute(service, job_id, run_id, attempt, effect_key, plan):
    from .invocation import guard
    with service.transaction() as db:
        job = service._get(db, "job", job_id)
        state = aggregate(job, service.root)
        frozen = state["plans"][run_id]["frozen"]
        from packages.jobs.final_service import frozen_action
        action = frozen_action(frozen, attempt["stepId"], effect_key["targetId"])
        service._guard_effect(db, job, run_id, frozen, action)
        if (state["attempts"][attempt["attemptId"]] != attempt or not service.owner.token
                or service.owner.token[0] != attempt["ownerEpoch"]):
            raise fc.ContractError("revision_conflict", "Original Attempt changed")
        ref = {key: action[key] for key in ("id", "revision", "digest")}
        key = "job-create:" + fc.digest(effect_key)
        def mutate(identity):
            result = create(service, db, action, ref, plan["arguments"], frozen["authority"], identity, key,
                source={"jobId": job_id, "runId": run_id, "effectKey": effect_key})
            return result, [ref], False
        receipt = service._operation(db, frozen["authority"], "invoke", key,
            {"effectKey": effect_key, "actionRef": ref, "arguments": plan["arguments"]}, "action", mutate)
        return {"outcome": "succeeded", "receipt": receipt["result"], "effectKey": effect_key}
