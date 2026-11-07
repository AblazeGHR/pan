"""Explicit runtime defaults at the existing canonical Session effect fence.

Frozen Run input and Action releases remain unchanged. An existing input binding
can opt into current Job defaults only for trusted product target/text fields.
Each child has a persisted reservation before invocation. Accepted or ambiguous
children keep their original target and text; only proven unsent children can
select current defaults at a later fence.
"""
import copy

PRODUCTS = {
    "product_assign_runtime_v1": "assign", "product_session_message_runtime_v1": "session-message",
    "product_session_broadcast_runtime_v1": "session-broadcast",
    "product_channel_runtime_v1": "channel",
}


def provider_ref(action):
    from packages.jobs.final_contracts import digest
    channel = action['id'] == 'product_channel_runtime_v1'
    return {'id': 'pan.typed', 'releaseDigest': digest('pan.typed.qq-runtime.v1' if channel
        else 'pan.typed.session-runtime.v1'), 'generation': 2 if channel else 3}


def validate_references(job, actions):
    from packages.jobs.final_contracts import ContractError
    declaration = job.get('deliveryRuntime', {})
    if declaration.get('phase') != 'before-reservation':
        return
    for step, action in zip(job['steps'], actions):
        fields = {name for name, binding in step['arguments'].items()
                  if binding.get('from') == 'input' and binding.get('path') in declaration['inputFields']}
        if not fields:
            continue
        definition = action.get('definition', action)
        allowed = definition.get('runtimeBeforeAcceptance', {}).get('fields', [])
        if (definition.get('system') is not True or PRODUCTS.get(action['id']) != definition['execution']['operation']
                or not fields <= set(allowed)):
            raise ContractError('reference_conflict', 'Job runtime fields require the explicit immutable runtime Action release')
        if action['id'] != 'product_channel_runtime_v1' and 'text' in fields:
            # Current Session defaults are validated at the same fenced write
            # boundary as QQ defaults. This never changes frozen Run input or
            # resolves Event/output bindings before their execution boundary.
            from packages.jobs.chains import resolve
            binding = step['arguments']['text']
            try:
                text = resolve({'text': binding}, job.get('arguments', {}), {}, {})['text']
            except ValueError as exc:
                raise ContractError('invalid_schema', 'Runtime Session defaults must supply their declared text path') from exc
            if not isinstance(text, str) or not text.strip():
                raise ContractError('invalid_schema', 'Session text must contain a non-whitespace character')
        if action['id'] == 'product_channel_runtime_v1':
            # Validate current fixed defaults with the original QQ validator.
            # Event/output bindings remain execution-time inputs; they are
            # never guessed or mistaken for literal configuration.
            from packages.jobs.chains import resolve
            from packages.jobs.actions import normalize_scheduled_qq_action
            known = {k: b for k, b in step['arguments'].items() if b.get('from') in {'literal', 'input'}}
            try:
                defaults = resolve(known, job.get('arguments', {}), {}, {})
            except ValueError as exc:
                raise ContractError('invalid_schema', 'Runtime QQ defaults must supply their declared input paths') from exc
            if defaults.get('botUin', '') is None:
                defaults.pop('botUin')
            target = {k: defaults[k] for k in ('targetType', 'targetId', 'botUin') if k in defaults}
            if target:
                _, invalid = normalize_scheduled_qq_action({'api': 'send_qq', 'args': {
                    'targetType': 'user', 'targetId': '10000', **target}})
                if invalid:
                    raise ContractError('invalid_schema', invalid)
            if 'text' in defaults and (not isinstance(defaults['text'], str) or not defaults['text'].strip()):
                raise ContractError('invalid_schema', 'QQ text must contain a non-whitespace character')


def original_arguments(plan, target, fallback):
    """Read-only original query locator; never selects current runtime fields."""
    from packages.jobs.final_contracts import ContractError, digest
    attempt = target["attempt"]
    reserved = plan.get("runtimeDelivery", {}).get(attempt["stepId"])
    if not reserved or not reserved.get("effectKey"):
        return fallback
    if (reserved["effectKey"] != target["effectKey"] or reserved["attemptId"] != attempt["attemptId"]
            or any(reserved[field] != attempt[field] for field in ("ownerEpoch", "generation", "leaseId"))
            or reserved["argumentsDigest"] != digest(reserved["arguments"])):
        raise ContractError("revision_conflict", "Original runtime reservation query fence differs")
    args = copy.deepcopy(reserved["arguments"])
    if reserved.get('children'):
        expected = {k: reserved[k] for k in ('effectKey', 'attemptId', 'ownerEpoch', 'generation', 'leaseId')}
        if any(c.get('parentFence') != expected for c in reserved['children']):
            raise ContractError('revision_conflict', 'Original child reservation fence differs')
        args['_runtimeChildren'] = copy.deepcopy(reserved['children'])
    return args


def receipt_projection(reserved, receipt):
    projected = {key: copy.deepcopy(value) for key, value in reserved.items() if key != "arguments"}
    targets = receipt.get("receipt", {}).get("targets")
    projected["acceptance"] = ([{"targetId": row["effectKey"]["targetId"], "outcome": row["outcome"]} for row in targets]
        if isinstance(targets, list) else [{"targetId": target, "outcome": receipt.get("outcome", "unknown")}
                                          for target in reserved["effectiveTargets"]])
    if reserved.get('children'):
        projected['acceptance'] = [{'targetId': child.get('targetId'),
            'outcome': child.get('receipt', {}).get('outcome', 'unknown')} for child in reserved['children']]
        projected['effectiveTargets'] = [child.get('targetId') for child in reserved['children']]
    return projected


def effective(job, state, run_id, step, action, *, current=False):
    from packages.jobs.chains import resolve
    from packages.jobs.final_contracts import ContractError, digest
    from packages.core import session
    plan = state["plans"][run_id]
    frozen = plan["frozen"]
    declaration = frozen.get("deliveryRuntime")
    if not declaration or declaration["phase"] != "before-reservation":
        return None
    if not any(b.get('from') == 'input' and b.get('path') in declaration['inputFields'] for b in step['arguments'].values()):
        return None
    if (action.get("system") is not True or PRODUCTS.get(action["id"]) != action["execution"]["operation"]
            or action["providerRef"] != provider_ref(action)
            or not action.get("runtimeBeforeAcceptance")):
        raise ContractError("reference_conflict", "Runtime delivery requires a trusted immutable product Session Action")
    previous = plan.get("runtimeDelivery", {}).get(step["stepId"])
    # A previously reserved/attempted effect never consults new target/text.
    if previous and previous.get("effectKey") and not current:
        return copy.deepcopy(previous)
    paths = declaration["inputFields"]
    value = copy.deepcopy(frozen["input"])
    latest = resolve({path: {"from": "input", "path": path} for path in paths}, job["canonical"]["arguments"], {}, {})
    for path, current in latest.items():
        parent = value
        pieces = path.split('.')
        for piece in pieces[:-1]:
            parent = parent.setdefault(piece, {})
        parent[pieces[-1]] = copy.deepcopy(current)
    for name, binding in step["arguments"].items():
        if binding.get("from") == "input" and binding.get("path") in paths and name not in action["runtimeBeforeAcceptance"]["fields"]:
            raise ContractError("invalid_schema", "Only target/text are Session runtime delivery fields")
    arguments = resolve(step["arguments"], value, frozen["event"], plan.get("outputs", {}))
    channel = action['execution']['operation'] == 'channel'
    if channel and arguments.get('botUin', '') is None:
        arguments.pop('botUin')  # original sender's omitted/default bot contract
    targets = ([arguments.get('targetId')] if channel else
        list(dict.fromkeys(arguments.get("sessionIds") or [])) if "sessionIds" in arguments else [arguments.get("sessionId")])
    missing = not targets or (not channel and all(not target or session.get(target, load_history=False) is None for target in targets))
    return {"owner": "delivery-manager", "runtimeRevision": job["configRevision"],
        "runtimeOperationId": job.get("runtimeOperationId"), "phase": "pending" if missing else "reserved",
        "reservationScope": "per-target", "eventCause": copy.deepcopy(state["runs"][run_id]["cause"]),
        "effectiveTargets": copy.deepcopy(targets), "textDigest": digest(arguments.get("text")),
        "argumentsDigest": digest(arguments), "arguments": arguments, "unknownPolicy": "query-original"}


def bound_attempt(state, run_id):
    """Validate a live continuation, never resurrect a settled Attempt."""
    from packages.jobs.final_contracts import digest
    plan = state['plans'][run_id]
    aid = plan.get('pendingSessionAttempt')
    attempt = state['attempts'].get(aid)
    if not attempt or attempt['state'] != 'running' or state['runs'][run_id]['state'] not in {'running', 'paused_at_boundary'}:
        return None
    reserved = plan.get('runtimeDelivery', {}).get(attempt['stepId'])
    action = plan['frozen']['actions'].get(reserved.get('effectKey', {}).get('targetId')) if reserved else None
    if (not action or action.get('system') is not True or PRODUCTS.get(action['id']) != action['execution']['operation']
            or not action.get('runtimeBeforeAcceptance')
            or action.get('providerRef') != provider_ref(action)
            or digest(plan['frozen']) != state['runs'][run_id]['planDigest']
            or plan['frozen'].get('deliveryRuntime', {}).get('phase') != 'before-reservation'
            or reserved.get('argumentsDigest') != digest(reserved.get('arguments'))
            or any(reserved.get(k) != attempt[k] for k in ('attemptId', 'ownerEpoch', 'generation', 'leaseId'))
            or state['steps'][run_id][attempt['stepId']]['attemptCount'] != attempt['generation']
            or state['effectAdmissions'].get(digest(reserved['effectKey']), {}).get('attemptId') != aid
            or not isinstance(reserved.get('children'), list) or not reserved['children']):
        return None
    for index, child in enumerate(reserved['children']):
        if (child.get('slot') != index or child.get('status') not in
                {'never-attempted', 'known-no-attempt', 'sending', 'accepted', 'unknown', 'rejected'}
                or child.get('parentFence') != {k: reserved[k] for k in ('effectKey', 'attemptId', 'ownerEpoch', 'generation', 'leaseId')}):
            return None
        if child['status'] == 'known-no-attempt' and (child.get('phase') != 'before-enqueue' or child.get('attempted') is not False):
            return None
        if child['status'] == 'known-no-attempt' and action['execution']['operation'] == 'channel' and child.get('arguments'):
            from packages.actions.qq_channel_provider import reconnectable_no_send
            if not reconnectable_no_send(child.get('receipt', {}), attempt, reserved['effectKey'], child.get('arguments', {})):
                return None
        if child['status'] in {'sending', 'accepted', 'unknown', 'rejected'}:
            expected_key = {**reserved['effectKey'], 'targetId': child.get('targetId')} if action['execution']['operation'] == 'session-broadcast' else reserved['effectKey']
            if (child.get('operation') != action['execution']['operation'] or child.get('effectKey') != expected_key
                    or child.get('argumentsDigest') != digest(child.get('arguments'))
                    or child.get('arguments', {}).get('targetId' if action['execution']['operation'] == 'channel' else 'sessionId') != child.get('targetId')):
                return None
    return attempt


def unproven_channel_attempt(state, run_id):
    """Quarantine a live paused channel boundary; this never authorizes a call.

    The ordinary Run graph has no paused->uncertain transition. A damaged
    receipt cannot justify either replay or inventing such a transition.
    """
    from packages.jobs.final_contracts import digest
    plan = state['plans'][run_id]
    aid = plan.get('pendingSessionAttempt')
    attempt = state['attempts'].get(aid)
    if (not attempt or attempt['runId'] != run_id or attempt['state'] != 'running'
            or state['runs'][run_id]['state'] != 'paused_at_boundary'
            or digest(plan['frozen']) != state['runs'][run_id]['planDigest']
            or plan['frozen'].get('deliveryRuntime', {}).get('phase') != 'before-reservation'):
        return False
    step = next((s for s in plan['frozen']['steps'] if s['stepId'] == attempt['stepId']), None)
    action = plan['frozen']['actions'].get(step['actionRef']['id']) if step else None
    return bool(action and action.get('id') == 'product_channel_runtime_v1'
        and action.get('system') is True and action.get('providerRef') == provider_ref(action)
        and not bound_attempt(state, run_id))


class SessionDelivery:
    """A view of child reservations inside the existing canonical Run plan.

    No independent queue, receipt store, retry budget or dispatcher. The
    original typed fanout loop calls this fence before its original provider.
    """
    def __init__(self, service, job_id, run_id, attempt, key):
        self.service, self.job_id, self.run_id = service, job_id, run_id
        self.attempt, self.key = copy.deepcopy(attempt), copy.deepcopy(key)
        self.pending = False
        from packages.jobs.final_runtime.ledger import aggregate
        with service.transaction() as db:
            job = service._get(db, 'job', job_id)
            state = aggregate(job, service.root)
            plan = state['plans'][run_id]
            reserved = plan['runtimeDelivery'][attempt['stepId']]
            fence = {k: reserved[k] for k in ('effectKey', 'attemptId', 'ownerEpoch', 'generation', 'leaseId')}
            if 'children' not in reserved:
                reserved['children'] = [{'slot': i, 'status': 'never-attempted', 'parentFence': copy.deepcopy(fence)}
                    for i, _ in enumerate(reserved['effectiveTargets'])]
            plan['pendingSessionAttempt'] = attempt['attemptId']
            service._save(db, 'job', job_id, job)

    def check_identity(self, attempt, key):
        from packages.jobs.final_contracts import ContractError
        if dict(key) != self.key or any(attempt.get(k) != self.attempt[k]
                for k in ('attemptId', 'ownerEpoch', 'generation', 'leaseId')):
            raise ContractError('revision_conflict', 'Runtime child Attempt fence differs')

    def slots(self, fallback):
        from packages.jobs.final_runtime.ledger import aggregate
        with self.service.transaction(readonly=True) as db:
            job = self.service._get(db, 'job', self.job_id)
            reserved = aggregate(job, self.service.root)['plans'][self.run_id]['runtimeDelivery'][self.attempt['stepId']]
            return [fallback[min(i, len(fallback) - 1)] for i in range(len(reserved['children']))]

    def extend_slots(self, ids):
        # Explicit list append is selected at the next per-target fence; it
        # cannot create another slot for an already accepted target.
        from packages.jobs.final_runtime.ledger import aggregate
        with self.service.transaction() as db:
            job = self.service._get(db, 'job', self.job_id)
            state = aggregate(job, self.service.root)
            if not bound_attempt(state, self.run_id):
                return
            plan = state['plans'][self.run_id]
            step = next(s for s in plan['frozen']['steps'] if s['stepId'] == self.attempt['stepId'])
            action = plan['frozen']['actions'][step['actionRef']['id']]
            selected = effective(job, state, self.run_id, step, action, current=True)
            reserved = plan['runtimeDelivery'][step['stepId']]
            while len(ids) < len(selected['effectiveTargets']):
                i = len(ids)
                reserved['children'].append({'slot': i, 'status': 'never-attempted',
                    'parentFence': copy.deepcopy(reserved['children'][0]['parentFence'])})
                ids.append(selected['effectiveTargets'][i])
            self.service._save(db, 'job', self.job_id, job)

    async def deliver(self, slot, supplied_plan, invoke):
        from packages.core import session
        from packages.actions.typed_executor import EffectReceipt
        from packages.jobs.final_contracts import ContractError, digest
        from packages.jobs.final_runtime.ledger import aggregate
        with self.service.transaction() as db:
            job = self.service._get(db, 'job', self.job_id)
            state = aggregate(job, self.service.root)
            if not bound_attempt(state, self.run_id):
                raise ContractError('revision_conflict', 'Runtime continuation is not a live fenced Attempt')
            plan = state['plans'][self.run_id]
            reserved = plan['runtimeDelivery'][self.attempt['stepId']]
            child = reserved['children'][slot]
            if child['status'] in {'accepted', 'rejected'}:
                return copy.deepcopy(child['receipt'])
            if child['status'] in {'sending', 'unknown'}:
                # A lost callback is not evidence that no invocation happened.
                row = query_original_child(child)
                child.update(status='accepted' if row['outcome'] == 'succeeded' else 'unknown', receipt=row)
                self.service._save(db, 'job', self.job_id, job)
                return copy.deepcopy(row)
            step = next(s for s in plan['frozen']['steps'] if s['stepId'] == self.attempt['stepId'])
            action = plan['frozen']['actions'][step['actionRef']['id']]
            try:
                self.service._guard_effect(db, job, self.run_id, plan['frozen'], action)
                from packages.scheduler.json_handoff import manual_intent
                blocked = (job['canonical']['state'] != 'enabled' and not manual_intent(self.service, db, job, self.run_id) or plan.get('pauseRequested')
                    or self.run_id in state.get('cancellations', {}))
            except ContractError:
                blocked = True
            selected = effective(job, state, self.run_id, step, action, current=True)
            used = {c.get('targetId') for c in reserved['children'] if c['status'] in {'accepted', 'unknown', 'sending'}}
            candidates = [sid for sid in dict.fromkeys(selected['effectiveTargets']) if sid and sid not in used]
            sid = candidates[0] if candidates else None
            channel = action['execution']['operation'] == 'channel'
            if blocked or sid is None or (not channel and session.get(sid, load_history=False) is None):
                self.pending = True
                child.update(status='known-no-attempt', phase='before-enqueue', attempted=False)
                self.service._save(db, 'job', self.job_id, job)
                return EffectReceipt(self.key, 'unknown', {'code': 'runtime_delivery_pending', 'slot': slot}).as_dict()
            args = copy.deepcopy(selected['arguments'])
            if channel:
                from packages.jobs.actions import normalize_scheduled_qq_action
                from packages.jobs.final_contracts import _validate_instance
                _validate_instance(action['inputSchema'], args, path='runtime.arguments')
                _, invalid = normalize_scheduled_qq_action({'api': 'send_qq', 'args': {
                    k: args[k] for k in ('targetType', 'targetId', 'botUin') if k in args}})
                if invalid or not args['text'].strip():
                    raise ContractError('invalid_schema', invalid or 'QQ text cannot be blank')
            else:
                args.pop('sessionIds', None)
                args['sessionId'] = sid
                args = self.service._session_source(args, plan['frozen']['authority'])
            key = {**self.key, 'targetId': sid} if action['execution']['operation'] == 'session-broadcast' else self.key
            child.update(status='sending', operation=action['execution']['operation'], targetId=sid, arguments=args, argumentsDigest=digest(args),
                runtimeRevision=job['configRevision'], runtimeOperationId=job.get('runtimeOperationId'),
                reservationOwnerEpoch=self.service.owner.token[0] if self.service.owner.token else None,
                reservationOwnerDigest=digest(self.service.owner.token),
                effectKey=key, phase='before-provider-acceptance' if channel else 'before-queue-acceptance',
                textDigest=digest(args['text']))
            child.pop('attempted', None)  # old no-attempt fact cannot label a new possible invocation
            self.service._save(db, 'job', self.job_id, job)  # intent BEFORE invoke
        # Only this validated runtime reservation enables native pre-call
        # evidence. Ordinary v2/custom/non-runtime providers keep their old
        # Worker invocation and receipt semantics, including call order.
        from packages.actions.typed_executor import _runtime_session_delivery
        context = _runtime_session_delivery.set(True)
        try:
            selected_plan = {'arguments': args}
            if channel:
                selected_plan['action'] = {'api': 'send_qq', 'args': {k: args[k]
                    for k in ('targetType', 'targetId', 'botUin') if k in args}}
            row = await invoke(key, selected_plan)
        finally:
            _runtime_session_delivery.reset(context)
        with self.service.transaction() as db:
            job = self.service._get(db, 'job', self.job_id)
            state = aggregate(job, self.service.root)
            if not bound_attempt(state, self.run_id):
                raise ContractError('revision_conflict', 'Runtime callback lost its Attempt fence')
            current = state['plans'][self.run_id]['runtimeDelivery'][self.attempt['stepId']]['children'][slot]
            if current != child:
                raise ContractError('revision_conflict', 'Runtime child reservation changed during invoke')
            known_unsent = row.get('localNoAttempt') is True
            if channel:
                from packages.actions.qq_channel_provider import reconnectable_no_send
                known_unsent = reconnectable_no_send(row, self.attempt, key, args)
            if row.get('outcome') == 'rejected' and not known_unsent:
                row = {**row, 'outcome': 'unknown', 'status': 'unknown', 'ok': False}
            current.update(status='known-no-attempt' if known_unsent else
                'accepted' if row.get('outcome') == 'succeeded' else 'unknown', receipt=copy.deepcopy(row))
            current['attempted'] = not known_unsent
            if known_unsent:
                current.update(phase='before-enqueue', attempted=False)
                if channel:
                    # A per-fire pending fact in the original Run plan, not a
                    # second queue/ledger. Retain original source identity and
                    # original text/error even after later runtime edits.
                    current.setdefault('backlog', {
                        'dispatchKey': plan['frozen']['event'].get('payload', {}).get('dispatchKey')
                            or plan['frozen']['event'].get('eventId') or self.key['runId'],
                        'fireAt': plan['frozen']['event'].get('payload', {}).get('scheduledAt')
                            or plan['frozen']['event'].get('occurredAt'),
                        'text': args['text'], 'error': copy.deepcopy(row.get('receipt', {}).get('error'))})
                    current['lastError'] = copy.deepcopy(row.get('receipt', {}).get('error'))
            self.pending = self.pending or known_unsent
            self.service._save(db, 'job', self.job_id, job)
        return row


def query_original_child(child):
    """Read-only original local queue lookup; absence remains unknown."""
    from packages.core import worker
    from packages.actions.typed_executor import EffectReceipt
    from packages.jobs.final_contracts import digest
    args, key = child['arguments'], child['effectKey']
    if child.get('operation') == 'channel':
        # QQ has no original remote lookup contract. Never turn a missing
        # local receipt into permission to invoke the external channel again.
        return EffectReceipt(key, 'unknown', {'code': 'original_remote_receipt_unavailable'}).as_dict()
    if child.get('argumentsDigest') != digest(args):
        return EffectReceipt(key, 'unknown', {'code': 'runtime_binding_mismatch'}).as_dict()
    target = worker._get_session_shallow(child['targetId'])
    item = None
    if target is not None:
        if child['operation'] == 'assign':
            item = worker._find_queue_item_by_idempotency(target,
                task_id=args.get('taskId') or 'effect_' + digest(key)[:40])
        else:
            item = worker._find_queue_item_by_idempotency(target, client_message_id='effect_' + digest(key)[:40])
    if item is not None and item.get('text') == args['text']:
        return EffectReceipt(key, 'succeeded', {'status': 'accepted', 'sessionId': child['targetId'],
            'queueItemId': worker._queue_item_id(item)}, target_selection={'sessionIds': [child['targetId']]}).as_dict()
    return EffectReceipt(key, 'unknown', {'code': 'original_receipt_unavailable'},
        target_selection={'sessionIds': [child['targetId']]}).as_dict()
