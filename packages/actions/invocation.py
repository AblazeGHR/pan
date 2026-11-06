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


def guard(service, db, ref, actor, source=None):
    service._permit(actor, "action", "invoke", execution=True)
    legacy_snapshot = False
    if source and isinstance(source.get('groupRelease'), str):
        row = db.execute("SELECT document FROM final_documents WHERE kind='action_group_release' AND id=?",
                         (source['groupRelease'],)).fetchone()
        release = json.loads(row[0]) if row else {}
        legacy_snapshot = (release.get('compatibility', {}).get('protocol') == 'legacy-flat/v1'
                           and any(t['actionRef'] == ref for t in release.get('tools', [])))
        if legacy_snapshot:
            alias = next(t['alias'] for t in release['tools'] if t['actionRef'] == ref)
            owner = db.execute("SELECT document FROM final_documents WHERE kind IN ('action_group_handoff','legacy_group_binding') AND id=?",
                               (release['groupId'],)).fetchone()
            source_root = json.loads(owner[0])['sourceRoot'] if owner else None
            legacy_snapshot = bool(db.execute(
                "SELECT 1 FROM final_documents WHERE kind='legacy_action_retirement' "
                "AND json_extract(document,'$.actionId')=? AND json_extract(document,'$.legacyId')=? "
                "AND json_extract(document,'$.sourceRoot')=?", (ref['id'], alias, source_root)).fetchone())
    # Legacy current-definition deletion/disable never rewrote an already built
    # snapshot. Only a trusted bound immutable release can retain that contract;
    # explicit retirement/disable of the exact version and invoke revocation
    # still reject. Public direct Action calls retain the normal head gate.
    action = service._action_version_record(db, ref["id"], ref["revision"], digest=ref["digest"], for_ref=not legacy_snapshot)
    if action["state"] != "published" or not action["enabled"]:
        raise fc.ContractError("authorization_denied", "Action version is disabled or retired")
    definition = action["definition"]
    if definition.get("system"):
        service._permit(actor, "action", "manageSystemActions", execution=True)
    if definition["effectClass"] == "high_risk":
        raise fc.ContractError("authorization_denied", "Protected effects require their existing supervisor")
    check_capabilities(definition)
    return definition


async def invoke(service, action_id, body, *, actor, source=None):
    ref = body["actionRef"]
    fc.check_request_key(body["requestKey"])
    if (not isinstance(ref, dict) or set(ref) != {"id", "revision", "digest"} or ref["id"] != action_id
            or type(ref["revision"]) is not int or ref["revision"] < 1
            or not isinstance(ref["digest"], str) or len(ref["digest"]) != 64):
        raise fc.ContractError("invalid_schema", "A fixed ActionRef is required")
    payload = {"actionRef": ref, "arguments": body["arguments"]}
    if source is not None:
        payload["source"] = copy.deepcopy(source)
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
        action = guard(service, db, ref, actor, source)
        if service.owner.token is None:
            raise fc.ContractError("revision_conflict", "Invocation requires the canonical owner")
        if not jsonschema.Draft202012Validator(action["inputSchema"]).is_valid(body["arguments"]):
            raise fc.ContractError("invalid_schema", "Action input schema rejected arguments")
        operation = action["execution"]["operation"]
        if operation == "job.create":
            from .job_creation import invoke as create_job
            return create_job(service, action_id, body, actor=actor, source=source)
        if operation == 'template.instantiate':
            from .legacy_families import template_view
            template_view(action, {'id': action_id, 'output_format': 'json'}, True)
            template_ref = action['execution']['config']['templateRef']
            from packages.jobs.final_service import definition
            template = service._get(db, 'template', template_ref['id'])
            if (template['revision'] != template_ref['revision']
                    or fc.digest(definition('template', template)) != template_ref['digest']):
                raise fc.ContractError('reference_conflict', 'Fixed TemplateRef changed; explicit remapping required')
            def instantiate(identity):
                # Existing composition and Action operation join the same owner
                # transaction. There is no second creation engine or ledger.
                created = service.instantiate_template(template_ref['id'], {
                    'expectedRevision': template_ref['revision'], 'requestKey': fc.digest(['legacy-template', identity])}, actor=actor)
                old = action['execution']['config']['legacyTemplate']
                output = {'ok': True, 'jobId': created['result']['objectIds']['job'], 'created': True,
                          'jobCompleted': False, 'templateId': old['templateId'], 'templateRevision': old['templateRevision']}
                return {'actionRef': ref, 'invocationId': identity, 'status': 'completed', 'phase': 'settled',
                        'output': output, 'compositionOperationId': created['operationId'],
                        **({'source': copy.deepcopy(source)} if source is not None else {})}, created['objectRefs'], False
            return service._operation(db, actor, 'invoke', body['requestKey'], payload, 'action', instantiate)
        if operation == "process":
            operation = "process.command"  # Original Job adapter remains IndependentExecutor.
        from .legacy_families import SYSTEM_OPERATIONS, system_plan
        system_adapter = operation in {'legacy.' + op for op in SYSTEM_OPERATIONS}
        if operation not in OPERATIONS and operation not in {'api.get', 'session.pause-everything'} and not system_adapter:
            raise fc.ContractError("unsupported_operation", "This Action has no direct command adapter")
        if system_adapter:
            from .legacy_families import system_view
            system_view(action, True)  # Only exact product schemas gain this adapter.
            service._permit(actor, 'job', 'execute', execution=True)
            prepared = system_plan(operation, argument_defaults(action['inputSchema'], body['arguments']))
            if prepared['operation'] in OPERATIONS:
                prepared['commands'] = prepare(prepared['operation'], prepared['config'], prepared['arguments'])
        elif operation in {'api.get', 'session.pause-everything'}:
            from .group_migration import legacy_action_view
            prepared = legacy_action_view(action, {'id': action_id, 'output_format': 'json'}, True)
            if operation == 'session.pause-everything':
                service._permit(actor, 'job', 'execute', execution=True)
                if not callable(service.session_pause):
                    raise fc.ContractError('provider_unavailable', 'Original Session pause host unavailable')
        else:
            try:
                prepared = prepare(operation, action["execution"]["config"],
                    argument_defaults(action["inputSchema"], body["arguments"]))
            except ValueError as exc:
                raise fc.ContractError("invalid_schema", str(exc)) from exc
        response = service._operation(db, actor, "invoke", body["requestKey"], payload, "action",
            lambda identity: ({"actionRef": ref, "invocationId": identity,
                "status": "uncertain", "phase": "prepared", "commands": [],
                **({'providerPlan': copy.deepcopy(prepared)} if system_adapter else {}),
                **({'source': copy.deepcopy(source)} if source is not None else {})}, [ref], True))
        identity = response["operationId"]
        epoch = service.owner.token

    return await execute_prepared(service, identity, ref, actor, prepared, epoch, operation,
                                  source=source, arguments=body['arguments'])


async def execute_prepared(service, identity, ref, actor, commands, epoch, operation, *, trigger_id=None, source=None, arguments=None):
    @contextmanager
    def boundary():
        with service.transaction() as db:
            if service.owner.token != epoch:
                raise fc.ContractError("revision_conflict", "Invocation owner changed")
            guard(service, db, ref, actor, source)
            if trigger_id is not None:
                service._permit(actor, "trigger", "lifecycle", execution=True)
                if service._get(db, "trigger", trigger_id)["state"] != "enabled":
                    raise fc.ContractError("authorization_denied", "Trigger disabled before effect")
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
    if operation.startswith('legacy.'):
        # Map the old payload once, then use the same process/provider primitive
        # as canonical Jobs. The direct operation is the only durable identity.
        @contextmanager
        def system_boundary():
            with boundary():
                service._permit(actor, 'job', 'execute', execution=True)
                yield
        context = CommandContext({'operationId': identity}, system_boundary,
                                 lambda rows: persist({'providerReceipts': rows}))
        if commands['operation'] in OPERATIONS:
            result = await service.executor.execute_commands(prepared_commands=commands['commands'],
                frozen_plan=commands, context=CommandContext(context.identity, system_boundary,
                    lambda rows: persist({'commands': rows})))
            persist({**result['receipt'], 'outcome': result['outcome'], 'phase': 'settled'}, terminal=True)
        else:
            result = await service.executor.execute_provider(context=context, frozen_plan=commands)
            status = {'succeeded': 'completed', 'rejected': 'failed', 'partial': 'partial', 'unknown': 'uncertain'}[result['outcome']]
            persist({'status': status, 'phase': 'settled', 'outcome': result['outcome'],
                     'providerReceipt': result, 'output': result['receipt']}, terminal=True)
        return service.operation(identity, actor=actor)
    if operation in {'api.get', 'session.pause-everything'}:
        from .provider import execute
        async def before_effect():
            with boundary():
                if operation == 'session.pause-everything':
                    service._permit(actor, 'job', 'execute', execution=True)
        try:
            if operation == 'api.get':
                output = await execute(commands, arguments, before_effect=before_effect)
            else:
                from .executor import execute_dynamic
                async def progress(**fields):
                    persist({'checkpoint': {**progress.checkpoint, **fields}})
                    progress.checkpoint.update(copy.deepcopy(fields))
                progress.checkpoint = {'id': identity}
                progress.before_effect = before_effect
                output = await execute_dynamic(commands, arguments, {}, progress, service.session_pause)
        except Exception as exc:
            # Preserve the original provider error text. The admitted identity
            # remains query-only, including a lost HTTP response or owner loss.
            persist({'status': 'uncertain', 'phase': 'settled', 'error': str(exc) or type(exc).__name__}, terminal=True)
        else:
            persist({'status': 'completed' if output.get('ok', True) else 'failed',
                     'phase': 'settled', 'output': output}, terminal=True)
        return service.operation(identity, actor=actor)
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
            from packages.jobs.final_service import frozen_action
            action = frozen_action(frozen, attempt["stepId"], effect_key["targetId"])
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
