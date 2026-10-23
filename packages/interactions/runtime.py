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
    service.bus.register(handle, 'pan.interaction.responded', inline(SCHEMA['$defs']['Event']['properties']['payload']))

    async def get_trigger(tid):
        return (await service.engine.io(service.ledger.read))['triggers'].get(tid)

    async def authorize(event, trigger):
        validate('Event', event)
        return (event['payload']['recipientId'] == LOCAL_RECIPIENT
                and event['type'] in trigger['eventTypes']
                and service.engine.available(trigger))

    async def submit(event, tid, *, expected_revision):
        validate('Event', event)
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
                service.events.append(prepared, [trigger])
                return {'status': 'accepted', 'receiptId': event['id']}
            result = await service.engine.io(admit)
            service.wake.set()
            return result

    async def lookup(eid):
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
