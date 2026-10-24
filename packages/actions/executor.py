"""Shared dynamic Action execution; immutable provider calls retain legacy mode."""
import asyncio
import copy
import jsonschema

from .store import validate_action
from .provider import execute
from packages.core.system_fields import system_target_name, system_value


async def execute_dynamic(action, arguments, event, progress, pause, query=None):
    async def before_effect():
        guard = getattr(progress, 'before_effect', None)
        if guard:
            await guard()
    action = await asyncio.to_thread(validate_action, action, automation=True)
    if not action['enabled'] or action['effect'] == 'high_risk':
        raise ValueError('Action disabled or requires approval policy')
    try:
        jsonschema.Draft202012Validator(action['input_schema']).validate(arguments)
    except jsonschema.ValidationError as exc:
        category = 'Additional properties' if exc.validator == 'additionalProperties' else str(exc.validator)
        raise ValueError('Action argument ' + category + ' validation failed') from None
    if action['execution']['kind'] == 'system_job':
        execute_system = getattr(progress, 'execute_system', None)
        if execute_system is None:
            raise PermissionError('Trusted Job host capability required')
        await before_effect()
        return await execute_system(action, arguments, event, progress)
    if action['execution']['kind'] == 'job_template':
        if arguments:
            raise ValueError('Fixed Job Template Action accepts no fields')
        instantiate = getattr(progress, 'instantiate_template', None)
        if instantiate is None:
            raise ValueError('Trusted Job Template capability unavailable')
        checkpoint = getattr(progress, 'checkpoint', {})
        if not checkpoint.get('id'):
            raise ValueError('Host execution identity required for idempotent Job creation')
        await before_effect()
        return await instantiate(action, checkpoint['id'])
    if action['execution']['kind'] != 'session_operation':
        return await execute(action, arguments, before_effect=before_effect)
    selector = action['execution']['selector']
    checkpoint = getattr(progress, 'checkpoint', {})
    query_info = checkpoint.get('selection')
    if 'targetIds' in checkpoint:
        ids = copy.deepcopy(checkpoint['targetIds'])
    elif selector['mode'] == 'explicit':
        ids = copy.deepcopy(selector['ids'])
    elif selector['mode'] == 'event':
        from packages.automation.engine import field
        path = selector['path']
        if path.startswith('system.'):
            ids = system_value(event, system_target_name(path))
        else:
            ids = field(event, path)
        if isinstance(ids, str):
            ids = [ids]
    elif selector['mode'] == 'query':
        from .session_query import resolve
        from packages.automation.events import now
        selection = await (query or resolve)(selector['query'])
        ids = selection['ids']
        query_info = dict(mode='query', matchedAt=now(), candidateCount=selection['candidateCount'], matched=len(ids))
    else:
        raise ValueError('Unknown Session selector mode')
    if not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError('Session selector must resolve to Session IDs')
    ids = list(dict.fromkeys(ids))
    confirmed = {r['sessionId']: copy.deepcopy(r) for r in checkpoint.get('targetResults', []) if r.get('ok') is True}
    results = [confirmed[sid] for sid in ids if sid in confirmed]
    await progress(targetIds=ids, targetResults=copy.deepcopy(results), **({'selection': query_info} if query_info is not None else {}))
    for sid in ids:
        if sid in confirmed:
            continue
        verify_interaction=getattr(progress,'verify_interaction',None)
        if verify_interaction:
            await verify_interaction()
        await before_effect()
        try:
            result = await pause(sid, {'paused': True})
        except Exception as exc:
            result = {'ok': False, 'error': str(exc)}
        confirmed[sid] = {'sessionId': sid, **result}
        results = [confirmed[i] for i in ids if i in confirmed]
        await progress(targetResults=copy.deepcopy(results))
    succeeded = sum(r.get('ok') is True for r in results)
    return dict(ok=succeeded == len(ids), matched=len(ids), succeeded=succeeded, failed=len(ids)-succeeded, targets=results)
