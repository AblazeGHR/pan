"""Operation/Attempt adapters over the shared command primitive."""
import copy
from contextlib import contextmanager
import json

import jsonschema

from packages.jobs import final_contracts as fc
from packages.jobs.final_runtime.ledger import principal
from .command_execution import CommandContext, OPERATIONS, argument_defaults, prepare


def check_capabilities(definition):
    from packages.core.platform import supports, UnknownCapability
    for name in definition["requiredCapabilities"]:
        try:
            available = supports(name)
        except UnknownCapability:
            available = False
        if not available:
            raise fc.ContractError("capability_unsupported", "Action capability unavailable: " + name)


def guard(service, db, ref, actor):
    service._permit(actor, "action", "invoke", execution=True)
    action = service._action_version_record(db, ref["id"], ref["revision"], digest=ref["digest"], for_ref=True)
    if action["state"] != "published" or not action["enabled"]:
        raise fc.ContractError("authorization_denied", "Action version is disabled or retired")
    definition = action["definition"]
    if definition.get("system"):
        service._permit(actor, "action", "manageSystemActions", execution=True)
    if definition["effectClass"] == "high_risk":
        raise fc.ContractError("authorization_denied", "Protected effects require their existing supervisor")
    check_capabilities(definition)
    return definition


async def invoke(service, action_id, body, *, actor):
    ref = body["actionRef"]
    fc.check_request_key(body["requestKey"])
    if (not isinstance(ref, dict) or set(ref) != {"id", "revision", "digest"} or ref["id"] != action_id
            or type(ref["revision"]) is not int or ref["revision"] < 1
            or not isinstance(ref["digest"], str) or len(ref["digest"]) != 64):
        raise fc.ContractError("invalid_schema", "A fixed ActionRef is required")
    payload = {"actionRef": ref, "arguments": body["arguments"]}
    service._permit(actor, "action", "read")
    scope = service._scope(actor, "invoke")

    def existing(db):
        row = db.execute("SELECT document FROM final_operations WHERE scope=? AND requestKey=?",
                         (scope, body["requestKey"])).fetchone()
        if row:
            prior = json.loads(row[0])
            fc.idempotency_outcome({"scope": scope, "requestDigest": prior["requestDigest"]},
                scope=scope, request_digest=fc.request_digest("invoke", payload))
            return {**prior["response"], "replayed": True}

    # A cold/read-only host and a revoked invocation grant can still query
    # the original identity with read permission. No writer claim is needed.
    with service.transaction(readonly=True) as db:
        previous = existing(db)
        if previous:
            return previous
    with service.transaction() as db:
        previous = existing(db)  # Concurrent admissions serialize here.
        if previous:
            return previous
        action = guard(service, db, ref, actor)
        if service.owner.token is None:
            raise fc.ContractError("revision_conflict", "Invocation requires the canonical owner")
        if not jsonschema.Draft202012Validator(action["inputSchema"]).is_valid(body["arguments"]):
            raise fc.ContractError("invalid_schema", "Action input schema rejected arguments")
        operation = action["execution"]["operation"]
        if operation == "process":
            operation = "process.command"  # Original Job adapter remains IndependentExecutor.
        if operation not in OPERATIONS:
            raise fc.ContractError("unsupported_operation", "This Action has no direct command adapter")
        try:
            commands = prepare(operation, action["execution"]["config"],
                argument_defaults(action["inputSchema"], body["arguments"]))
        except ValueError as exc:
            raise fc.ContractError("invalid_schema", str(exc)) from exc
        response = service._operation(db, actor, "invoke", body["requestKey"], payload, "action",
            lambda identity: ({"actionRef": ref, "invocationId": identity,
                "status": "uncertain", "phase": "prepared", "commands": []}, [ref], True))
        identity = response["operationId"]
        epoch = service.owner.token

    @contextmanager
    def boundary():
        with service.transaction() as db:
            if service.owner.token != epoch:
                raise fc.ContractError("revision_conflict", "Invocation owner changed")
            guard(service, db, ref, actor)
            yield

    def persist(result, *, terminal=False):
        with service.transaction() as db:
            if service.owner.token != epoch:
                raise fc.ContractError("revision_conflict", "Invocation owner changed")
            row = db.execute("SELECT document FROM final_operations WHERE id=?", (identity,)).fetchone()
            doc = json.loads(row[0])
            if doc["principal"] != principal(actor):
                raise fc.ContractError("revision_conflict", "Invocation principal changed")
            doc["response"]["result"].update(copy.deepcopy(result))
            if terminal:
                doc["response"]["status"] = "unknown" if result["status"] == "uncertain" else "completed"
            db.execute("UPDATE final_operations SET document=? WHERE id=?", (fc.canonical_json(doc), identity))

    # Prepared is already durable. Any crash from here on is query-only, even
    # when no process happened to start. No automatic direct recovery.
    persist({"phase": "calling"})
    result = await service.executor.execute_commands(prepared_commands=commands,
        frozen_plan={"operation": operation}, context=CommandContext({"operationId": identity}, boundary,
        lambda rows: persist({"commands": rows})))
    persist({**result["receipt"], "outcome": result["outcome"], "phase": "settled"}, terminal=True)
    return service.operation(identity, actor=actor)


async def job_execute(service, job_id, run_id, attempt, effect_key, plan):
    commands = prepare(plan["operation"], plan, plan["arguments"])

    @contextmanager
    def boundary():
        from packages.jobs.final_runtime.ledger import aggregate
        with service.transaction() as db:
            job = service._get(db, "job", job_id)
            state = aggregate(job, service.root)
            current = state["attempts"][attempt["attemptId"]]
            if (current != attempt or state["runs"][run_id]["state"] != "running"
                    or not service.owner.token or service.owner.token[0] != attempt["ownerEpoch"]):
                raise fc.ContractError("revision_conflict", "Original Attempt is no longer executable")
            frozen = state["plans"][run_id]["frozen"]
            action = frozen["actions"][effect_key["targetId"]]
            service._guard_effect(db, job, run_id, frozen, action)
            check_capabilities(action)
            yield

    def checkpoint(rows):
        from packages.jobs.final_runtime.ledger import aggregate
        with service.transaction() as db:
            job = service._get(db, "job", job_id)
            state = aggregate(job, service.root)
            if (state["attempts"][attempt["attemptId"]] != attempt or not service.owner.token
                    or service.owner.token[0] != attempt["ownerEpoch"]):
                raise fc.ContractError("revision_conflict", "Original Attempt fence lost before checkpoint")
            state["plans"][run_id].setdefault("commandReceipts", {})[attempt["attemptId"]] = rows
            service._save(db, "job", job_id, job)

    result = await service.executor.execute_commands(prepared_commands=commands, frozen_plan=plan,
        context=CommandContext({"attempt": attempt, "effectKey": effect_key}, boundary, checkpoint))
    return {**result, "effectKey": copy.deepcopy(effect_key)}
