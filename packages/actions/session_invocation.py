"""Fixed Session inputs for the existing identity-neutral provider port."""
import copy

from packages.jobs import final_contracts as fc

OPERATIONS = frozenset({'session-message', 'session-broadcast', 'assign'})


def prepare(service, action, arguments, actor):
    """Reuse canonical source policy; never construct a Job/Run/Attempt."""
    if action.get('runtimeBeforeAcceptance'):
        raise fc.ContractError('unsupported_operation',
            'runtimeBeforeAcceptance requires Job deliveryRuntime input bindings, frozen Run and per-target Attempt/effect reservations')
    if action['execution'].get('config'):
        raise fc.ContractError('unsupported_operation', 'Direct Session input requires the original empty execution.config')
    operation = action['execution']['operation']
    service._permit(actor, 'job', 'execute', execution=True)
    args = service._session_source(copy.deepcopy(arguments), actor)
    target_field = 'sessionIds' if operation == 'session-broadcast' else 'sessionId'
    ids = args[target_field] if target_field == 'sessionIds' else [args[target_field]]
    if not isinstance(ids, list) or not 1 <= len(ids) <= 256 or any(not isinstance(s, str) or not s for s in ids):
        raise fc.ContractError('invalid_schema', 'Fixed Session targets must contain 1..256 IDs')
    from packages.core import session
    if any(session.get(sid, load_history=False) is None for sid in ids):
        raise fc.ContractError('invalid_schema', 'Fixed Session target does not exist')
    if not isinstance(args.get('text'), str) or not args['text'].strip():
        raise fc.ContractError('invalid_schema', 'Session text must not be blank')
    provider = service.executor.assign_provider if operation == 'assign' else service.executor.session_provider
    method = 'assign' if operation == 'assign' else 'send_session'
    if not callable(getattr(provider, method, None)) and not (method == 'send_session' and callable(getattr(provider, 'send', None))):
        raise fc.ContractError('provider_unavailable', 'Original typed Session provider unavailable')
    return {'operation': operation, 'arguments': args}
