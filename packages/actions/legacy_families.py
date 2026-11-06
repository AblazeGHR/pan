"""Legacy union/schema adapters only; execution stays in existing primitives."""
import copy

from packages.jobs import final_contracts as fc
from .command_execution import product_definition
from .store import system_definition, validate_system_action

SYSTEM_OPERATIONS = {'session-message', 'session-broadcast', 'scheduled-task'}


def completion_contract(value, name):
    value['completionContracts'] = [{'id': name, 'schema': {'type': 'object'}, 'terminalProofSchema': {'type': 'object'}}]
    value['defaultCompletion'] = name
    return value


def template_successor(action, template_ref):
    from .store import validate_action
    action = validate_action(action)
    if action['execution']['kind'] != 'job_template' or not isinstance(template_ref, dict) or set(template_ref) != {'id', 'revision', 'digest'}:
        raise fc.ContractError('reference_conflict', 'Fixed legacy Template needs an explicit canonical TemplateRef')
    value = product_definition('template.instantiate')
    value.update(name=action['name'], description=action['description'], system=False,
        inputSchema=action['input_schema'], effectClass=action['effect'], requiredCapabilities=[],
        providerRef={'id': 'pan.templates', 'releaseDigest': fc.digest('pan.templates.instantiate.v1'), 'generation': 1},
        cancel={'supported': False}, retry={**value['retry'], 'idempotent': action['idempotent']})
    value['execution']['config'] = {'legacyTemplate': copy.deepcopy(action['execution']), 'templateRef': copy.deepcopy(template_ref)}
    return fc.validate_definition('action', completion_contract(value, 'job-created'))


def template_view(value, base, enabled):
    config = value['execution']['config']
    if set(config) != {'legacyTemplate', 'templateRef'}:
        raise fc.ContractError('reference_conflict', 'Fixed Template mapping required')
    raw = {**copy.deepcopy(base), 'name': value['name'], 'description': value['description'],
           'execution': config['legacyTemplate'], 'enabled': enabled, 'input_schema': value['inputSchema'],
           'effect': value['effectClass'], 'idempotent': value['retry']['idempotent']}
    if template_successor(raw, config['templateRef']) != value:
        raise fc.ContractError('reference_conflict', 'Canonical Template contracts differ from original fixed mapping')
    return raw


def system_successor(action):
    validate_system_action(action)
    old_operation = action['execution']['operation']
    if old_operation not in SYSTEM_OPERATIONS:
        raise fc.ContractError('reference_conflict', 'Monitoring/background and protected lifecycle require their existing Job/supervisor host')
    value = product_definition('legacy.' + old_operation)
    value.update(name=action['name'], description=action['description'], inputSchema=action['input_schema'],
                 requiredCapabilities=[], providerRef={'id': 'pan.typed', 'releaseDigest': fc.digest('pan.typed.legacy-union.v1'), 'generation': 1},
                 cancel={'supported': False})
    value['execution']['config'] = {}
    return fc.validate_definition('action', completion_contract(value, 'provider-receipt'))


def system_view(value, enabled):
    operation = value['execution']['operation'].removeprefix('legacy.')
    raw = system_definition(operation)
    if system_successor(raw) != value:
        raise fc.ContractError('reference_conflict', 'Immutable system successor contracts changed')
    raw['enabled'] = enabled
    return raw


def system_plan(operation, arguments):
    """Map the original scheduled-task union without interpreting shell text."""
    args = copy.deepcopy(arguments)
    if operation != 'legacy.scheduled-task':
        return {'operation': operation.removeprefix('legacy.'), 'arguments': args}
    action = args['action']
    api = action['api']
    if api == 'shell':
        return {'operation': 'process.shell', 'config': action['args'], 'arguments': {}}
    if api == 'send_qq':
        return {'operation': 'channel', 'action': action, 'text': args['text']}
    if api == 'resume_legal_running':
        from .session_resume import select
        return {'operation': 'resume-legal-running.v1', 'arguments': {'sessionIds': select()}}
    return {'operation': 'assign' if api == 'assign' else 'session-message',
            'arguments': {'sessionId': args['sessionId'], 'text': args['text']}}
