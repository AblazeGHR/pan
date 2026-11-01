"""Data-only serial Action chains shared by the Job and Trigger hosts.

Hosts own admission, persistence, authorization, cancellation and notification.
This module never creates a second Job, schedules time, or retries an effect.
"""
import asyncio
import copy
import re
import time
import uuid

import jsonschema

MAX_STEPS = 32
STEP_ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}\Z')
_BINDING_SCHEMA = {'oneOf': [
    {'not': {'type': 'object', 'required': ['from']}},
    {'type': 'object', 'additionalProperties': False, 'required': ['from', 'value'],
     'properties': {'from': {'const': 'literal'}, 'value': {}}},
    {'type': 'object', 'additionalProperties': False, 'required': ['from', 'path'],
     'properties': {'from': {'enum': ['input', 'event', 'system']}, 'path': {'type': 'string', 'maxLength': 512}}},
    {'type': 'object', 'additionalProperties': False, 'required': ['from', 'stepId', 'path'],
     'properties': {'from': {'const': 'step'}, 'stepId': {'type': 'string', 'pattern': r'^[A-Za-z][A-Za-z0-9_-]{0,63}$'},
                    'path': {'type': 'string', 'maxLength': 512}}},
]}
SCHEMA = {'version': 1, 'steps': {'type': 'array', 'minItems': 1, 'maxItems': MAX_STEPS,
    'items': {'type': 'object', 'additionalProperties': False, 'required': ['stepId', 'actionId'],
              'properties': {'stepId': {'type': 'string', 'pattern': r'^[A-Za-z][A-Za-z0-9_-]{0,63}$'},
                             'actionId': {'type': 'string', 'minLength': 1},
                             'arguments': {'type': 'object', 'maxProperties': 128, 'additionalProperties': _BINDING_SCHEMA}}}}}


class ChainPersistenceError(RuntimeError):
    pass


def normalize(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STEPS:
        raise ValueError('steps requires 1..32 ordered steps')
    steps = copy.deepcopy(raw)
    seen = set()
    for step in steps:
        if not isinstance(step, dict) or set(step) - {'stepId', 'actionId', 'arguments'}:
            raise ValueError('Unknown or invalid chain step fields')
        sid = step.get('stepId')
        if not isinstance(sid, str) or not STEP_ID.fullmatch(sid) or sid in seen:
            raise ValueError('stepId requires a unique stable ID of 1..64 letters, digits, underscores or hyphens')
        if not isinstance(step.get('actionId'), str) or not step['actionId']:
            raise ValueError('Step actionId required')
        mapping = step.setdefault('arguments', {})
        if not isinstance(mapping, dict) or len(mapping) > 128:
            raise ValueError('Step arguments requires an object of at most 128 fields')
        for binding in mapping.values():
            if not isinstance(binding, dict) or 'from' not in binding:
                continue  # Plain JSON literals are permitted in the new schema.
            source = binding.get('from')
            if source == 'literal' and set(binding) == {'from', 'value'}:
                continue
            keys = {'from', 'path', 'stepId'} if source == 'step' else {'from', 'path'}
            if source not in {'input', 'event', 'step', 'system'} or set(binding) != keys:
                raise ValueError('Invalid chain argument binding')
            path = binding.get('path')
            if not isinstance(path, str) or len(path) > 512 or (path and any(not p for p in path.split('.'))):
                raise ValueError('Chain path must be a dotted dictionary path (or empty for the root)')
            if source == 'step' and (not isinstance(binding['stepId'], str) or binding['stepId'] not in seen):
                raise ValueError('Step output must reference a preceding stepId')
            if source == 'system':
                from packages.core.system_fields import system_binding_name
                system_binding_name(path)
        seen.add(sid)
    return steps


def action_ids(config):
    return list(dict.fromkeys(s['actionId'] for s in config['steps'])) if 'steps' in config else [config['actionId']]


def validate_arguments(action, mapping):
    """Validate literals now; dynamically mapped values are validated pre-effect."""
    from .action_templates import filled_arguments
    schema = action['input_schema']
    properties = schema.get('properties', {})
    if set(mapping) - set(properties):
        raise ValueError('Step arguments contains undeclared Action fields')
    dynamic = False
    literals = {}
    for key, binding in mapping.items():
        if isinstance(binding, dict) and 'from' in binding:
            if binding['from'] != 'literal':
                dynamic = True
                continue
            value = binding['value']
        else:
            value = binding
        try:
            jsonschema.Draft202012Validator(properties[key]).validate(value)
        except jsonschema.ValidationError as exc:
            raise ValueError('Invalid literal argument: ' + key) from exc
        literals[key] = value
    if not dynamic:
        filled_arguments(action, literals)
    missing = set(schema.get('required', [])) - set(mapping) - {
        key for key, spec in properties.items() if isinstance(spec, dict) and 'default' in spec}
    if missing:
        raise ValueError('Missing required step arguments: ' + ', '.join(sorted(missing)))


def freeze(steps, state, input_value, event, *, completion=None, config_revision=None):
    """Freeze every definition and mapping in one source registry observation."""
    from packages.actions.store import validate_action
    from .action_templates import digest, check_action_graph, Templates, strict
    frozen = []
    for step in normalize(steps):
        aid = step['actionId']
        action = state['actions'].get(aid)
        if not action or not action.get('enabled') or state.get('actionLifecycle', {}).get(aid) is False:
            raise ValueError('Chain Action disabled or missing: ' + aid)
        action = validate_action(action)
        if action['effect'] == 'high_risk':
            raise ValueError('Chain Action requires approval policy: ' + aid)
        check_action_graph(state, action)
        validate_arguments(action, step['arguments'])
        entry = {**step, 'definition': action, 'actionRevision': state.get('actionRevisions', {}).get(aid, 0),
                 'generation': state.get('actionGenerations', {}).get(aid, 0), 'digest': digest(action)}
        if action['execution']['kind'] == 'job_template':
            template = state.get('jobTemplates', {}).get(action['execution']['templateId'])
            if not template or not Templates.view(template, state)['usable']:
                raise ValueError('Fixed Job Template unavailable')
            entry['fixedTemplate'] = copy.deepcopy(template)
            entry['fixedTemplate']['usable'] = True
        frozen.append(entry)
    snapshot = {'version': 1, 'steps': frozen, 'input': copy.deepcopy(input_value),
                'event': copy.deepcopy(event), 'completion': copy.deepcopy(completion or {'kind': 'ordinary'}),
                'configRevision': config_revision, 'registryRevision': state.get('revision', 0)}
    strict(snapshot, set(snapshot), 'chain snapshot')
    snapshot['digest'] = digest(snapshot)
    return snapshot


def records(snapshot, run_id):
    return [{'id': 'step_exec_' + uuid.uuid4().hex[:24], 'runId': run_id, 'stepId': s['stepId'],
             'actionId': s['actionId'], 'actionRevision': s['actionRevision'], 'digest': s['digest'],
             'ordinal': index, 'status': 'pending', 'attempts': [], 'checkpoint': {}}
            for index, s in enumerate(snapshot['steps'])]


def resolve(mapping, input_value, event, outputs):
    def path(value, dotted):
        for key in dotted.split('.') if dotted else []:
            if not isinstance(value, dict) or key not in value:
                raise ValueError('Missing chain mapping path: ' + dotted)
            value = value[key]
        return copy.deepcopy(value)
    result = {}
    for key, binding in mapping.items():
        if not isinstance(binding, dict) or 'from' not in binding:
            result[key] = copy.deepcopy(binding)
        elif binding['from'] == 'literal':
            result[key] = copy.deepcopy(binding['value'])
        elif binding['from'] == 'input':
            result[key] = path(input_value, binding['path'])
        elif binding['from'] == 'event':
            result[key] = path(event, binding['path'])
        elif binding['from'] == 'system':
            from packages.core.system_fields import system_binding_name, system_value
            result[key] = copy.deepcopy(system_value(event, system_binding_name(binding['path'])))
        else:
            if binding['stepId'] not in outputs:
                raise ValueError('Preceding step output unavailable: ' + binding['stepId'])
            result[key] = path(outputs[binding['stepId']], binding['path'])
    return result


def start_step(step):
    if step['status'] in {'completed', 'uncertain', 'unknown', 'cancelled', 'running'}:
        raise ValueError('Step cannot be replayed: ' + step['stepId'])
    if len(step['attempts']) >= 10:
        raise ValueError('Step attempt capacity reached')
    attempt = {'id': 'step_attempt_' + uuid.uuid4().hex[:24], 'status': 'running', 'startedAt': time.time()}
    step.update(status='running', startedAt=time.time())
    step.pop('error', None)
    step['attempts'].append(attempt)


def finish_step(step, status, **fields):
    step.update(copy.deepcopy(fields), status=status, finishedAt=time.time())
    if step['attempts'] and step['attempts'][-1]['status'] == 'running':
        step['attempts'][-1].update(status=status, finishedAt=step['finishedAt'], **copy.deepcopy(fields))


def interrupted(steps):
    """A pending boundary is resumable; a running effect has an unknown receipt."""
    active = False
    for step in steps:
        if step['status'] == 'running':
            finish_step(step, 'uncertain', error='Service interrupted; no automatic replay')
            active = True
    return not active and all(s['status'] in {'pending', 'completed'} for s in steps)


def public(value):
    """Never expose executable frozen snapshots through API/WS/audit views."""
    if isinstance(value, dict):
        private = set()
        if 'stepExecutions' in value:private.add('chainSnapshot')
        if 'canonicalPlan' in value:private.add('canonicalPlan')
        if 'jobId' in value and 'config' in value:private.update(('chainRecovery','runRecoveries'))
        if 'runs' in value and 'operations' in value:private.update(('plans','effectAdmissions'))
        return {k: public(v) for k, v in value.items() if k not in private}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


async def execute(snapshot, steps, *, invoke, save, guard, progress_factory, recovery=False):
    """Persist before each effect and after each terminal step; never retry here."""
    from .action_templates import filled_arguments, strict
    outputs = {s['stepId']: copy.deepcopy(s.get('output')) for s in steps if s['status'] == 'completed'}
    for definition, step in zip(snapshot['steps'], steps, strict=True):
        if step['status'] == 'completed':
            continue
        if step['status'] in {'uncertain', 'unknown', 'cancelled'}:
            return step['status'], None
        await guard(definition)
        try:
            args = filled_arguments(definition['definition'], resolve(
                definition['arguments'], snapshot['input'], snapshot['event'], outputs))
            # Mapping failure is a failed Step with no effect Attempt.
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            finish_step(step, 'failed', error=str(exc)[:1000])
            await save(steps, 'boundary')
            return 'failed', None
        start_step(step)
        await save(steps, 'step_running')
        progress = await progress_factory(definition, step)
        try:
            result = await invoke(definition, args, snapshot['event'], progress)
            if not isinstance(result, dict):
                raise ChainPersistenceError('Unconfirmed Action completion: expected an object')
            try:
                strict(result, set(result), 'chain step result')
            except (ValueError,TypeError) as exc:
                raise ChainPersistenceError('Action result cannot be durably audited') from exc
            status = result.get('status')
            completion = snapshot['completion']
            signal = (result.get('structuredContent', {}).get('panJobCompletion')
                      if isinstance(result.get('structuredContent'), dict) else result.get('panJobCompletion'))
            if status in {'unknown', 'uncertain', 'unconfirmed', 'running', 'starting'}:
                outcome = 'uncertain'
            elif status in {'failed', 'error', 'cancelled'} or not result.get('ok', True) or result.get('isError', False):
                outcome = 'cancelled' if status == 'cancelled' else 'failed'
            elif completion.get('kind') == 'monitoring' and not (
                    result.get('processTreeEnded') is True and isinstance(signal, dict)
                    and set(signal) == {'type', 'completed'} and signal['completed'] is True
                    and signal['type'] == completion.get('signalType')):
                outcome = 'restarting' if recovery else 'failed'
            else:
                outcome = 'completed'
            output = result.get('structuredContent', result)
            finish_step(step, outcome, result=result, output=output,
                        **({'error': str(result.get('error', 'Step completion contract not satisfied'))[:1000]} if outcome != 'completed' else {}))
            await save(steps, 'boundary')
        except asyncio.CancelledError:
            raise
        except Exception:
            # Hosts classify exceptions and durably settle the active Attempt;
            # a post-effect persistence exception must never advance the chain.
            raise
        if outcome != 'completed':
            return outcome, result
        outputs[step['stepId']] = copy.deepcopy(output)
    return 'completed', {'ok': True, 'outputs': outputs}
