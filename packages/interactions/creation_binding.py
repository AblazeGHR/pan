"""Read-only proof of an explicitly selected canonical Job-producing Action."""
from packages.jobs import final_contracts as fc
from .contracts import InteractionError

OPERATIONS = frozenset({'job.create', 'template.instantiate'})


def template_plan(host, db, action, actor, *, authorize=True):
    from packages.actions.legacy_templates import resolve_action_template
    from packages.jobs.final_service import definition
    ref, template, binding = resolve_action_template(host, db, action)
    value = definition('template', template)
    activate = bool(binding and binding['raw']['config']['enabled'])
    if authorize:
        host._permit(actor, 'template', 'use')
        host._permit(actor, 'job', 'create')
    specs, triggers = value['composition']['creators'], value['composition']['triggers']
    if not specs and triggers:
        raise InteractionError('incomplete', 'Canonical source-free Template supports only a Job-only composition')
    host._references(db, 'template', value, enabled=activate)
    host._references(db, 'job', value['jobDefinition'], enabled=activate)
    for spec in specs:
        if spec['mode'] == 'create':
            if authorize:
                host._permit(actor, 'event_creator', 'create')
            source = host.creators.registry.validate_definition(spec['definition'])
        else:
            if authorize:
                host._permit(actor, 'event_creator', 'read')
            source = host.creators.get(spec['ref']['id'])
        if source is None or source['providerRef']['type'] != 'time' or source['providerRef']['id'] != 'scheduler':
            raise InteractionError('incomplete', 'Canonical Template composition requires original time Scheduler creators')
    if authorize:
        if triggers:
            host._permit(actor, 'trigger', 'create')
        if activate:
            host._permit(actor, 'job', 'lifecycle')
            host._permit(actor, 'job', 'execute', execution=True)
            if triggers:
                host._permit(actor, 'trigger', 'lifecycle', execution=True)
            if any(s['mode'] == 'create' for s in specs):
                host._permit(actor, 'event_creator', 'lifecycle', execution=True)
    return value['jobDefinition'], {'templateRef': ref, 'compositionDigest': fc.digest(value['composition']),
        'sourceMappingDigest': fc.digest(binding) if binding else fc.digest(None),
        'startRequested': activate, 'creatorCount': len(specs), 'triggerCount': len(triggers)}


def resolve(host, db, action, arguments, actor, *, restricted):
    from packages.actions.job_creation import resolve_definition
    from packages.jobs.chains import resolve as bindings
    from packages.actions.command_execution import argument_defaults
    nodes, targets = [], []
    dynamic = False

    def unresolved(path):
        nonlocal dynamic
        if restricted:
            raise InteractionError('incomplete', f'{path}: source managed scope cannot prove a future event/Step-derived target')
        dynamic = True  # Existing unrestricted authority; future Job admission owns resolution.

    def creation(current, args, depth=0):
        if depth > 8 or len(nodes) >= 256:
            raise InteractionError('capacity', 'Creation graph proof exceeds depth/node bound')
        operation = current['execution']['operation']
        # Every explicit creation node is an actual canonical Job creation
        # effect. Prove the same create grant up front; this does not create a
        # wrapper Job or reserve a second execution chain.
        host._permit(actor, 'job', 'create', execution=True)
        if operation == 'job.create':
            job = resolve_definition(host, db, current, args)
            info = {'startRequested': current['execution']['config'].get('start', False)}
        else:
            job, info = template_plan(host, db, current, actor, authorize=depth == 0)
        nodes.append({'operation': operation, 'definitionDigest': fc.digest(job), **info})
        for step in job['steps']:
            ref = step['actionRef']
            record = host._action_version_record(db, ref['id'], ref['revision'], digest=ref['digest'], for_ref=True)
            child = record['definition']
            op = child['execution']['operation']
            nodes.append({'actionRef': ref})
            if op in {'session-message', 'session-broadcast', 'assign'}:
                field = 'sessionIds' if op == 'session-broadcast' else 'sessionId'
                try:
                    value = bindings({field: step['arguments'][field]}, job.get('arguments', {}), {}, {})[field]
                except (ValueError, KeyError):
                    unresolved(f"steps.{step['stepId']}.arguments.{field}")
                    continue
                ids = value if field == 'sessionIds' else [value]
                if not isinstance(ids, list) or not ids or any(not isinstance(s, str) or not s for s in ids):
                    raise InteractionError('invalid', 'Invalid Session targets in explicit Job definition')
                targets.extend(ids)
                if len(set(targets)) > 256:
                    raise InteractionError('capacity', 'Creation target scope exceeds bound')
            elif op in OPERATIONS:
                try:
                    args = bindings(step['arguments'], job.get('arguments', {}), {}, {})
                except ValueError:
                    unresolved(f"steps.{step['stepId']}.arguments")
                    continue
                creation(child, argument_defaults(child['inputSchema'], args), depth + 1)
        return job, info

    job, info = creation(action, arguments)
    return list(dict.fromkeys(targets)), {'operation': action['execution']['operation'], 'name': job.get('name', ''),
        'definitionDigest': fc.digest(job), 'graphDigest': fc.digest(nodes), 'dynamicTargets': dynamic, **info}
