"""Fixed ActionRef proof and adapter to canonical direct invocation (no implicit wrapper Jobs)."""
import json
import jsonschema

from .contracts import InteractionError


def binding_capability(action, executor):
    """Definition-time explanation; actual arguments/authority are rechecked."""
    operation = action['execution']['operation']
    from packages.actions.session_invocation import OPERATIONS as SESSION_OPERATIONS
    if action.get('runtimeBeforeAcceptance'):
        return False, 'runtimeBeforeAcceptance requires Job deliveryRuntime input bindings, frozen Run and per-target Attempt/effect reservations'
    if operation in SESSION_OPERATIONS:
        if action['execution'].get('config'):
            return False, 'Session execution.config is not the fixed-input product contract'
        provider = executor.assign_provider if operation == 'assign' else executor.session_provider
        if not callable(getattr(provider, 'assign' if operation == 'assign' else 'send_session', None)) and not (
                operation != 'assign' and callable(getattr(provider, 'send', None))):
            return False, 'Original typed Session provider unavailable'
        return True, 'Fixed literal Session targets; original typed provider and operation receipt'
    if operation in {'process', 'process.command', 'process.shell', 'api.get'}:
        return True, 'Fixed command/API arguments; canonical operation receipt'
    if operation in {'job.create', 'template.instantiate'}:
        return True, 'Explicitly creates a complete Job via the original canonical creation/composition adapter'
    if operation == 'legacy.scheduled-task':
        return False, 'Legacy scheduled-task requires its original scheduler lifecycle contract'
    if operation in {'session.pause-everything', 'resume-legal-running.v1'}:
        return False, f'{operation} selects global/current Session state, without fixed Card target IDs'
    return False, f'{operation} has no complete fixed-target proof in its Card arguments contract'


def capabilities(service):
    from .trusted import canonical_host
    from packages.actions.invocation import guard
    from packages.jobs.final_service import object_ref
    host = canonical_host(service)
    if host is None:
        raise InteractionError('incomplete', 'Canonical Action host unavailable')
    actor = {'type': 'agent', 'id': 'agent', 'revision': service.auth.permissions_status()['revision']}
    rows = []
    try:
        actions = host.actions(actor=actor)['items']
    except Exception as exc:
        raise InteractionError('forbidden', 'Action read capability denied') from exc
    for action in actions:
        ref = object_ref('action', action)
        try:
            with host.transaction(readonly=True) as db:
                definition = guard(host, db, ref, actor)
                bindable, reason = binding_capability(definition, host.executor)
                if bindable and definition['execution']['operation'] in {'job.create', 'template.instantiate'}:
                    host._permit(actor, 'job', 'create', execution=True)
                    if definition['execution']['operation'] == 'template.instantiate':
                        from .creation_binding import template_plan
                        template_plan(host, db, definition, actor)
        except Exception as exc:
            bindable, reason = False, str(exc)
        rows.append({'actionRef': ref, 'name': action['name'], 'operation': action['execution']['operation'],
                     'bindable': bindable, 'reason': reason, 'inputSchema': action['inputSchema']})
    return {'actions': rows, 'argumentsPolicy': 'Fixed literal arguments; creation rechecks source scope, targets and current grants'}


def resolve(service, request, choice, *, actor=None):
    from .trusted import canonical_host, scope
    from packages.actions.invocation import guard
    host = canonical_host(service)
    if host is None:
        raise InteractionError('incomplete', 'Canonical Action host unavailable')
    actor = actor or {'type': 'agent', 'id': 'agent', 'revision': service.auth.permissions_status()['revision']}
    try:
        with host.transaction(readonly=True) as db:
            host._permit(actor, 'action', 'read')
            action = guard(host, db, choice['actionRef'], actor)
            args = choice.get('arguments', {})
            if not jsonschema.Draft202012Validator(action['inputSchema']).is_valid(args):
                raise InteractionError('invalid', 'Fixed Action arguments rejected')
            operation = action['execution']['operation']
            bindable, reason = binding_capability(action, host.executor)
            if not bindable:
                raise InteractionError('incomplete', reason)
            from packages.actions.session_invocation import OPERATIONS as SESSION_OPERATIONS, prepare as prepare_session
            creation = None
            if operation in {'job.create', 'template.instantiate'}:
                from .creation_binding import resolve as resolve_creation
                source = scope(request.get('producerSessionId', request['ownerSessionId']), request['ownerSessionId'], [])
                ids, creation = resolve_creation(host, db, action, args, actor, restricted=source['basis'] == 'self_or_managed')
            elif operation in SESSION_OPERATIONS:
                try:
                    prepare_session(host, action, args, actor)
                except Exception as exc:
                    raise InteractionError('incomplete', str(exc)) from exc
                ids = list(dict.fromkeys(args['sessionIds'] if operation == 'session-broadcast' else [args['sessionId']]))
            elif operation in {'process', 'process.command', 'process.shell', 'api.get'}:
                ids = []
            else:
                raise InteractionError('incomplete', 'Fixed Card binding requires literal command/API or Session targets; this Action selects Job/global/runtime state')
            auth = scope(request.get('producerSessionId', request['ownerSessionId']), request['ownerSessionId'], ids)
            return {'version': 3, 'actionRef': choice['actionRef'], 'arguments': args, 'actor': actor,
                    'authorization': auth, 'targetIds': ids,
                    'summary': (f"显式创建完整 Job：{creation['name']} · {'请求启动/启用，以原回执为准' if creation['startRequested'] else '创建后保持禁用'}" if creation else f"Action {choice['actionRef']['id']} r{choice['actionRef']['revision']} · {operation}"),
                    **({'creation': creation} if creation else {})}
    except InteractionError:
        raise
    except Exception as exc:
        # Preserve canonical validation/authorization distinctions for card
        # creation; do not turn an invalid graph into a generic forbidden.
        from packages.jobs import final_contracts as fc
        if isinstance(exc, fc.ContractError):
            code = 'incomplete' if exc.code in {'provider_unavailable', 'unsupported_operation', 'reference_conflict'} else 'invalid'
            raise InteractionError(code, str(exc)) from exc
        raise InteractionError('forbidden', 'Canonical Action authorization unavailable') from exc


def verify(service, event):
    b = event['payload']['executionBinding']
    auth = b['authorization']
    if event['payload']['ownerSessionId'] != auth['ownerSessionId'] or event['payload'].get('actionRef', b['actionRef']) != b['actionRef']:
        raise InteractionError('forbidden', 'Action binding source/reference mismatch')
    current = resolve(service, auth, {'actionRef': b['actionRef'], 'arguments': b['arguments']}, actor=b['actor'])
    if current != b:
        raise InteractionError('stale', 'Fixed Action authority changed')
    return b


class Adapter:
    def __init__(self, service, trigger):
        self.service, self.trigger = service, trigger

    @property
    def submit(self):
        return self.trigger.submit

    @submit.setter
    def submit(self, value):
        self.trigger.submit = value

    async def authorize(self, event):
        if not event['payload'].get('actionRef'):
            return await self.trigger.authorize(event)
        verify(self.service, event)
        return True

    async def accept(self, event):
        if not event['payload'].get('actionRef'):
            return await self.trigger.accept(event)
        from .trusted import canonical_host
        b = verify(self.service, event)
        host = canonical_host(self.service)
        # Canonical invoke persists its operation before effects and rechecks
        # the frozen version/grants at each effect boundary. No Card lock or
        # shared admission mutex is held across command execution.
        result = await host.invoke_action(b['actionRef']['id'], {
            'actionRef': b['actionRef'], 'arguments': b['arguments'],
            'requestKey': 'interaction:' + event['id']}, actor=b['actor'], source=self.source(event), effect_guard=lambda: verify(self.service, event))
        return {'status': 'accepted', 'receiptId': result['operationId']}

    @staticmethod
    def source(event):
        from .trusted import digest
        return {'interactionEventId': event['id'], 'cardId': event['payload']['cardId'],
                'bindingDigest': digest(event['payload']['executionBinding'])}

    async def lookup_event(self, event):
        if not event['payload'].get('actionRef'):
            return await self.trigger.lookup(event['id'])
        from .trusted import canonical_host
        from packages.jobs import final_contracts as fc
        from packages.jobs.final_runtime.ledger import principal
        host = canonical_host(self.service)
        if host is not None:
            b = event['payload']['executionBinding']
            payload = {'actionRef': b['actionRef'], 'arguments': b['arguments'], 'source': self.source(event)}
            with host.transaction(readonly=True) as db:
                rows = db.execute("SELECT document FROM final_operations WHERE scope=? AND requestKey=?",
                    (host._scope(b['actor'], 'invoke'), 'interaction:' + event['id'])).fetchall()
                if len(rows) == 1:
                    doc = json.loads(rows[0][0])
                    if (doc['principal'] == principal(b['actor']) and doc['requestDigest'] == fc.request_digest('invoke', payload)
                            and doc['response']['result'].get('actionRef') == b['actionRef']):
                        return {'status': 'accepted', 'receiptId': doc['response']['operationId']}
        return None

    async def lookup(self, eid):
        return await self.trigger.lookup(eid)
