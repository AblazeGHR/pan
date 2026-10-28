"""Single-profile user identity and directed production automation binding."""
from .bridge import TriggerReferenceAdapter
from .store import UserPrincipal

LOCAL_RECIPIENT = 'user'


async def recover(store, adapter):
    """Reconcile persisted response intents; unknown is lookup-only in Store."""
    import asyncio
    def pending():
        import json
        with store.db() as db:
            return [row['id'] for row in db.execute('SELECT id,delivery FROM cards WHERE recipient=?', (LOCAL_RECIPIENT,))
                    if json.loads(row['delivery'])['state'] not in {'accepted','not_requested','rejected'}]
    for card_id in await asyncio.to_thread(pending):
        from .contracts import InteractionError
        try:
            await store.dispatch(UserPrincipal(LOCAL_RECIPIENT), card_id, adapter)
        except InteractionError:
            # Disabled/missing/stale references remain visible for explicit
            # reconciliation. One blocked Card must not prevent host startup.
            continue
    await store.recover_detached(adapter)


async def principal(app, request):
    service = getattr(app.state, 'automation', None)
    if service is None or not service.started or service.stopping:
        return None
    if await service.engine.io(service.auth.authorized, request.headers.get('authorization', '')):
        return UserPrincipal(LOCAL_RECIPIENT)
    return None


def bind(service):
    from .trusted import verify
    service.engine.interaction_verify=lambda event: verify(service,event)
    handle = service.bus.activate('core', 'pan')
    from .contracts import SCHEMA
    # Resolve local schema references using the already bounded Card validator;
    # the Bus accepts a closed inline Event payload definition, not external refs.
    from .contracts import validate
    def inline(value):
        if isinstance(value, list):
            return [inline(v) for v in value]
        if isinstance(value, dict):
            if '$ref' in value:
                return inline(SCHEMA['$defs'][value['$ref'].removeprefix('#/$defs/')])
            return {k: inline(v) for k, v in value.items()}
        return value
    service.bus.register(handle, 'pan.interaction.responded', inline(SCHEMA['$defs']['Event']['properties']['payload']),
                         max_payload_bytes=2_000_000)

    async def get_trigger(tid):
        from .trusted import canonical_host
        host = canonical_host(service)
        if host is not None:
            def read():
                with host.transaction(readonly=True) as db:
                    try:
                        trigger = host._get(db, 'trigger', tid)
                    except KeyError:
                        return None
                    return {**trigger, 'enabled': trigger['state'] == 'enabled'}
            return await service.engine.io(read)
        return (await service.engine.io(service.ledger.read))['triggers'].get(tid)

    async def authorize(event, trigger):
        validate('Event', event)
        from .trusted import canonical_host
        if canonical_host(service) is not None:
            return event['payload']['recipientId'] == LOCAL_RECIPIENT and event['type'] in trigger['eventTypes']
        return (event['payload']['recipientId'] == LOCAL_RECIPIENT
                and event['type'] in trigger['eventTypes']
                and service.engine.available(trigger))

    async def submit(event, tid, *, expected_revision):
        validate('Event', event)
        from .trusted import canonical_host
        host = canonical_host(service)
        if host is not None:
            import copy
            from packages.core import session
            # No await between final scope/proof verification and admission.
            # HTTP Session capability changes run on this same loop; managed
            # graph changes use _STORE_LOCK. Canonical definitions and profile
            # grants join their existing writer transaction/lock respectively.
            async with service.admission:
                if not service.started or service.stopping:
                    return {'status': 'rejected'}
                with session._STORE_LOCK, host.transaction() as db, service.auth.lock:
                    binding = verify(service, event)
                    if binding['triggerId'] != tid or binding['triggerRevision'] != expected_revision:
                        return {'status': 'rejected'}
                    trigger = host._get(db, 'trigger', tid)
                    if trigger.get('condition') is not None:
                        return {'status': 'rejected'}  # No asynchronous state observation inside this fence.
                    fact = {**copy.deepcopy(event), 'creatorType': 'core'}
                    result = host.admit_event(fact, trigger)
                    if result.get('runId') and result.get('admissionId') and result.get('status') in {'accepted', 'completed'}:
                        return {'status': 'accepted', 'receiptId': result['operationId']}
                    return {'status': 'rejected'} if result.get('status') in {'skipped', 'rejected'} or result.get('result', {}).get('skipped') else {'status': 'unknown'}
        prepared = service.bus.prepare(handle, event['type'], event['payload'], event['id'])
        # Preserve the persisted fact's timestamp; retries carry the same fact.
        prepared['occurredAt'] = event['occurredAt']
        async with service.admission:
            if not service.started or service.stopping:
                return {'status': 'rejected'}
            def admit():
                # Same writer as Trigger update/delete. No Action or event-loop
                # work runs under this bounded off-loop admission transaction.
                with service.ledger.lock:
                    from packages.actions.store import _lock as actions_lock
                    with actions_lock:
                        return admit_locked()
            def admit_locked():
                verify(service,event)
                trigger = service.ledger.read()['triggers'].get(tid)
                if (not trigger or not trigger['enabled'] or trigger['revision'] != expected_revision
                        or prepared['type'] not in trigger['eventTypes'] or not service.engine.available(trigger)):
                    return {'status': 'rejected'}
                old = service.events.lookup(event['id'])
                if old and [t['id'] for t in old['targets']] != [tid]:
                    raise ValueError('Directed Event identity conflict')
                if 'answers' in event['payload']['response']:
                    service.events.append(prepared, [trigger], max_payload_bytes=2_000_000)
                else:
                    service.events.append(prepared, [trigger])
                return {'status': 'accepted', 'receiptId': event['id']}
            result = await service.engine.io(admit)
            service.wake.set()
            return result

    async def lookup(eid):
        from .trusted import canonical_host
        host = canonical_host(service)
        if host is not None:
            def original():
                import json
                with host.transaction(readonly=True) as db:
                    rows = db.execute('SELECT triggerId,operationId,factDigest FROM final_event_operations WHERE eventId=?', (eid,)).fetchall()
                    if len(rows) != 1:
                        return None
                    row = rows[0]
                    operation = db.execute('SELECT document FROM final_operations WHERE id=?', (row['operationId'],)).fetchone()
                    if operation is None:
                        return None
                    operation = json.loads(operation[0])
                    result = operation['response']
                    rid = result.get('runId')
                    if not rid or not result.get('admissionId'):
                        return None
                    job, _ = host._find_run(db, rid)
                    from packages.jobs.final_runtime.ledger import aggregate
                    state = aggregate(job, host.root)
                    admission = state['admissions'].get(result['admissionId'])
                    if (not admission or admission['cause'].get('eventId') != eid
                            or admission['cause'].get('triggerId') != row['triggerId']):
                        return None
                    # Fact digest and target are checked by the original
                    # operation's immutable input, not the current definition.
                    from packages.jobs import final_contracts as fc
                    frozen_event = state.get('plans', {}).get(rid, {}).get('frozen', {}).get('event')
                    # A retired executable snapshot is not rebuilt. The
                    # immutable operation requestDigest still proves the
                    # original Event fact/Trigger admission identity.
                    if frozen_event is not None and (frozen_event.get('id') != eid or frozen_event.get('type') != 'pan.interaction.responded'
                            or frozen_event.get('payload', {}).get('triggerRef', {}).get('id') != row['triggerId']
                            or fc.digest(frozen_event) != row['factDigest']):
                        return None
                    payload = {'eventId': eid, 'triggerId': row['triggerId'], 'factDigest': row['factDigest']}
                    if (operation.get('requestDigest') != fc.request_digest('create', payload)
                            or result.get('requestKey') != 'event:' + fc.digest({'eventId': eid, 'triggerId': row['triggerId']})):
                        return None
                    return {'status': 'accepted', 'receiptId': row['operationId']}
            return await service.engine.io(original)
        row = await service.engine.io(service.events.lookup, eid)
        if row and row['event']['type'] == 'pan.interaction.responded':
            return {'status': 'accepted', 'receiptId': eid}
        return None

    adapter=TriggerReferenceAdapter(get_trigger=get_trigger, authorize=authorize, submit=submit, lookup=lookup)
    original_authorize=adapter.authorize
    async def bound_authorize(event):
        await service.engine.io(verify,service,event)
        return await original_authorize(event)
    adapter.authorize=bound_authorize
    return adapter
