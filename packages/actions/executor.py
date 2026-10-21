"""Shared dynamic Action execution; immutable provider calls retain legacy mode."""
import asyncio
import copy
import jsonschema

from .store import validate_action
from .provider import execute


async def execute_dynamic(action, arguments, event, progress, pause):
    action = await asyncio.to_thread(validate_action, action, automation=True)
    if not action['enabled'] or action['effect'] == 'high_risk':
        raise ValueError('Action disabled or requires approval policy')
    jsonschema.Draft202012Validator(action['input_schema']).validate(arguments)
    if action['execution']['kind'] != 'session_operation':
        return await execute(action, arguments)
    selector = action['execution']['selector']
    if selector['mode'] == 'explicit':
        ids = copy.deepcopy(selector['ids'])
    elif selector['mode'] == 'event':
        from packages.automation.engine import field
        ids = field(event, selector['path'])
        if isinstance(ids, str):
            ids = [ids]
    else:
        raise ValueError('query selector is reserved, not implemented')
    if not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError('Session selector must resolve to Session IDs')
    ids = list(dict.fromkeys(ids))
    results = []
    await progress(targetIds=ids, targetResults=results)
    for sid in ids:
        try:
            result = await pause(sid, {'paused': True})
        except Exception as exc:
            result = {'ok': False, 'error': str(exc)}
        results.append({'sessionId': sid, **result})
        await progress(targetResults=copy.deepcopy(results))
    succeeded = sum(r.get('ok') is True for r in results)
    return dict(ok=succeeded == len(ids), matched=len(ids), succeeded=succeeded, failed=len(ids)-succeeded, targets=results)
