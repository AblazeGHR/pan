"""Card-only trusted meaning. No general Job/Group reference policy."""
import copy
import hashlib

from packages.core import session
from packages.actions.store import _lock as actions_lock
from .contracts import InteractionError, canonical


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def scope(producer_id, owner_id, ids):
    producer = session.get(producer_id, load_history=False)
    if producer is None:
        raise InteractionError('incomplete', 'Source Session unavailable')
    restricted = bool(producer.pan_access.get('restrict_to_managed', False))
    allowed = {producer.id, *producer.managed}
    if restricted and (owner_id not in allowed or any(sid not in allowed for sid in ids)):
        raise InteractionError('forbidden', 'Source cannot authorize these Session targets')
    if any(session.get(sid, load_history=False) is None for sid in ids):
        raise InteractionError('incomplete', 'Target Session unavailable')
    return {'producerSessionId': producer_id, 'ownerSessionId': owner_id,
            'basis': 'self_or_managed' if restricted else 'existing_unrestricted', 'targetIds': ids}


def all_choices(definition):
    if definition.get('schemaVersion', 1) == 2:
        for question in definition['questions']:
            for choice in question.get('choices', []):
                if choice.get('triggerRef'):
                    yield question['id'], choice
    else:
        for choice in definition.get('choices', []):
            if choice.get('triggerRef'):
                yield None, choice


def resolve(service, request):
    """Read definitions under existing writers; persist result only with the Card."""
    choices = list(all_choices(request['card']))
    if not choices:
        return {}
    if service is None or not service.started or service.stopping:
        raise InteractionError('incomplete', 'Trigger host unavailable')
    result = {}
    with service.ledger.lock, actions_lock:
        triggers = service.ledger.read()['triggers']
        actions = service.engine.actions.read()['actions']
        for question_id, choice in choices:
            ref = choice['triggerRef']
            trigger = triggers.get(ref['id'])
            if not trigger or not trigger['enabled'] or 'pan.interaction.responded' not in trigger['eventTypes']:
                raise InteractionError('incomplete', 'Enabled interaction Trigger required')
            if 'revision' in ref and ref['revision'] != trigger['revision']:
                raise InteractionError('stale', 'Trigger revision changed')
            action = actions.get(trigger['actionId'])
            execution = (action or {}).get('execution', {})
            # Other capabilities cannot prove their full effects/Session scope
            # through this contract. Dynamic queries/event selectors fail closed.
            if (not action or not action['enabled'] or execution.get('kind') != 'session_operation'
                    or execution.get('selector', {}).get('mode') != 'explicit'):
                raise InteractionError('incomplete', 'Choice requires provable fixed Session targets')
            ids = list(dict.fromkeys(execution['selector']['ids']))
            authorization = scope(request.get('producerSessionId', request['ownerSessionId']), request['ownerSessionId'], ids)
            target = result.setdefault(question_id, {}) if question_id is not None else result
            target[choice['id']] = copy.deepcopy(dict(version=1, triggerId=trigger['id'],
                triggerRevision=trigger['revision'], triggerDigest=digest(trigger), actionId=action['id'],
                actionDigest=digest(action), action=action, arguments=trigger.get('arguments', {}),
                selector=execution['selector'], authorization=authorization,
                summary=f"{execution['operation']} · Action {action['id']} · Trigger {trigger['id']} r{trigger['revision']}",
                targetIds=ids))
    return result


def create(service, store, request, *, producer_id=None):
    # An existing identity is never rebound, including to a newer caller.
    from .contracts import identity
    with store.db() as db:
        exists = db.execute('SELECT 1 FROM cards WHERE id=?',
                            (identity('card_', [request['ownerSessionId'], request['requestKey']]),)).fetchone()
    if exists:
        return store.create(request, producer_id=producer_id)
    from .related_context import previous_user_message_id
    related = previous_user_message_id(request['ownerSessionId']) if producer_id else None
    if not any(all_choices(request['card'])):
        return store.create(request, producer_id=producer_id, related_message_id=related)
    if service is None:
        raise InteractionError('incomplete', 'Trigger host unavailable')
    with service.ledger.lock, actions_lock:
        return store.create(request, bindings=resolve(service, request), producer_id=producer_id, related_message_id=related)


def verify(service, event):
    binding = event['payload'].get('executionBinding')
    if not binding:
        raise InteractionError('incomplete', 'Legacy choice has no trusted execution binding')
    if service is None or not service.started or service.stopping:
        raise InteractionError('incomplete', 'Trigger host unavailable')
    with service.ledger.lock, actions_lock:
        trigger = service.ledger.read()['triggers'].get(binding['triggerId'])
        action = service.engine.actions.read()['actions'].get(binding['actionId'])
        if (not trigger or digest(trigger) != binding['triggerDigest'] or not action
                or digest(action) != binding['actionDigest'] or digest(binding['action']) != binding['actionDigest']):
            raise InteractionError('stale', 'Approved Trigger/Action definition changed')
        auth = binding['authorization']
        if scope(auth['producerSessionId'], auth['ownerSessionId'], binding['targetIds']) != auth:
            raise InteractionError('stale', 'Source authorization changed')
        if event['payload']['ownerSessionId'] != auth['ownerSessionId']:
            raise InteractionError('forbidden', 'Binding source mismatch')
    return binding
