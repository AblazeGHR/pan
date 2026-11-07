"""Source-bound Template projections over the existing migration mapper.

Only definitions/graphs are translated here. Ownership, operations, composition
and execution remain in the original canonical hosts.
"""
import copy
import json

from packages.jobs import final_contracts as fc
from packages.jobs.action_templates import Templates, check_action_graph
from scripts.job_final_migration.mapping import Mapper


def reject(message):
    raise fc.ContractError('reference_conflict', message)


def mapped_definition(source, state, bindings, policies, schedules):
    """Compare every source policy, edge and schedule; no stopped-owner claim.

    Mapper.config_job/schedule are pure existing conversion primitives. Their
    separate offline bundle authority attestation is deliberately not invoked:
    Group adoption owns the actual source fence and canonical transaction.
    """
    generation = source.get('referenceGeneration')
    valid_generation = (type(generation) is int and generation >= 0 or isinstance(generation, dict)
                        and all(isinstance(k, str) and type(v) is int and v >= 0 for k, v in generation.items()))
    if (set(source) != {'id', 'name', 'description', 'config', 'revision', 'referenceGeneration'}
            or not isinstance(source.get('id'), str) or not source['id'].startswith('jtpl_') or len(source['id']) > 64
            or type(source.get('revision')) is not int or source['revision'] < 1 or not valid_generation
            or not isinstance(source.get('name'), str) or not source['name'].strip() or not isinstance(source.get('description'), str)
            or not Templates.view(source, state)['usable']):
        reject('Template schema, reference generation or graph is incomplete')
    cfg = copy.deepcopy(source['config'])
    mapper = Mapper({'schemaVersion': 1, 'actions': bindings}, require_enabled=False)
    steps = cfg.get('steps') or [{'stepId': 'execute', 'actionId': cfg['actionId'], 'arguments': cfg['arguments']}]
    cfg['steps'] = [{**s, 'arguments': {key: value if isinstance(value, dict) and 'from' in value else
                         {'from': 'literal', 'value': value} for key, value in s['arguments'].items()}} for s in steps]
    for step in cfg['steps']:
        action = state['actions'][step['actionId']]
        check_action_graph(state, action)
        if step['actionId'] not in bindings:
            reject('Missing explicit Template ActionRef: ' + step['actionId'])
        completion = bindings[step['actionId']]['completions'].get(cfg['completion']['kind'])
        if cfg['completion']['kind'] == 'monitoring' and completion != {
                'contract': 'monitoring', 'parameters': {'signalType': cfg['completion']['signalType']}}:
            reject('Template monitoring signal differs from original completion')
    cfg['completion'] = {'kind': cfg['completion']['kind']}
    job = mapper.config_job(cfg, {'policies': policies})
    job['inputSchema'] = {'type': 'object'}
    job['arguments'] = copy.deepcopy(source['config']['arguments']) if 'steps' in source['config'] else {}
    composition = {'creators': [], 'triggers': []}
    original_schedules = source['config']['schedule']
    if not isinstance(schedules, list) or len(schedules) != len(original_schedules):
        reject('Every original schedule needs an explicit context, including disabled schedules')
    for index, (original, context) in enumerate(zip(original_schedules, schedules)):
        if not isinstance(context, dict) or set(context) - {'anchor', 'policy'} or 'policy' not in context:
            reject('Invalid reviewed schedule context')
        entry = copy.deepcopy(original)
        entry.setdefault('misfirePolicy', source['config']['misfirePolicy'])
        if 'anchor' in context:
            if entry.get('kind') != 'interval' or ('anchor' in entry and entry['anchor'] != context['anchor']):
                reject('Reviewed schedule anchor cannot replace an original anchor')
            entry['anchor'] = context['anchor']
        creator = mapper.schedule(entry, {'scheduler': context['policy'], 'name': source['name']})
        # The existing native TimeProvider publishes this versioned event;
        # legacy scheduler.tick is not a second runtime provider.
        creator['eventTypes'] = ['scheduler.time.v2']
        if original.get('enabled', True) is False:
            continue  # Kept verbatim in the source binding; it never produced events.
        key = 'clock_' + str(index)
        composition['creators'].append({'mode': 'create', 'key': key, 'definition': creator})
        composition['triggers'].append({'key': 'tick_' + str(index), 'creatorKeys': [key], 'targetJobKey': 'job',
            'definition': {'name': source['name'] + ' tick', 'eventTypes': ['scheduler.time.v2'],
                           'creatorSelector': {'mode': 'registeredType', 'providerType': 'time'}, 'targetJobId': 'job'}})
    # Event-template selectors/query belong on the Trigger, never on an input
    # binding. A source-free composition cannot yet bind an external creator.
    if source['config']['trigger']['kind'] == 'event':
        reject('Event Template needs its original external creator selector/query composition mapping')
    return fc.validate_definition('template', {'name': source['name'], 'description': source['description'],
                                             'jobDefinition': job, 'composition': composition})


def review(service, db, state, mappings):
    from .group_migration import command_definition
    from packages.jobs.final_service import definition, object_ref
    # Permission checks are performed by the adoption host using its trusted
    # actor; this pure graph projection never fabricates an authority claim.
    templates, counters = state.get('jobTemplates', {}), state.get('templateVersions', {})
    if (not isinstance(templates, dict) or len(templates) > 256 or not isinstance(counters, dict)
            or len(counters) > 10000 or any(not isinstance(k, str) or not k.startswith('jtpl_')
            or type(v) is not int or v < 1 for k, v in counters.items())):
        reject('Template registry/counter bounds or tombstone schema invalid')
    bindings = {}
    for aid, raw in state['actions'].items():
        entries = [m for m in mappings if m.get('version') == 'draft' and m.get('actionId') == aid
                   and m.get('definitionDigest') == fc.digest(raw)]
        refs = [m['actionRef'] for m in entries]
        if not refs or any(r != refs[0] for r in refs):
            reject('Template graph needs one explicit current Action identity: ' + aid)
        record = service._action_version_record(db, refs[0]['id'], refs[0]['revision'], digest=refs[0]['digest'])
        value = record['definition']
        if value != command_definition(raw, template_ref=value['execution']['config'].get('templateRef')):
            reject('Template graph Action definition mismatch: ' + aid)
        head = service._get(db, 'action', refs[0]['id'])
        if head['revision'] != refs[0]['revision']:
            reject('Template graph Action mapping is not the reviewed current version')
        bindings[aid] = {'object': head, 'ref': refs[0], 'completions': {'ordinary': {'contract': value['defaultCompletion']}}}
    items = []
    for tid, source in sorted(templates.items()):
        if source.get('id') != tid or counters.get(tid) != source.get('revision'):
            reject('Template revision/tombstone identity mismatch: ' + tid)
        entries = [m for m in mappings if m.get('kind') == 'template' and m.get('templateId') == tid
                   and m.get('revision') == source['revision'] and m.get('definitionDigest') == fc.digest(source)]
        if len(entries) != 1:
            reject('Missing explicit Template configuration/policy mapping: ' + tid)
        mapping = entries[0]
        local = copy.deepcopy(bindings)
        if source['config']['completion']['kind'] == 'monitoring':
            for binding in local.values():
                binding['completions']['monitoring'] = {'contract': 'monitoring',
                    'parameters': {'signalType': source['config']['completion']['signalType']}}
        expected = mapped_definition(source, state, local, mapping['policies'], mapping['schedules'])
        for spec in expected['composition']['creators']:
            service.creators.registry.validate_definition(spec['definition'])
        target = service._object(db, 'template', mapping['templateRef']['id'])
        if target['state'] != 'published' or object_ref('template', target) != mapping['templateRef'] or definition('template', target) != expected:
            reject('Canonical Template graph or policies differ from reviewed source: ' + tid)
        for row in db.execute("SELECT document FROM final_documents WHERE kind='legacy_template_binding'"):
            previous = json.loads(row[0])
            if (previous['legacyId'] == tid and previous['raw']['revision'] == source['revision']
                    and previous['templateRef'] == mapping['templateRef'] and previous['raw'] != source):
                reject('Fixed TemplateRef already maps a different original instantiation policy: ' + tid)
        service._references(db, 'template', expected)
        items.append({'legacyId': tid, 'raw': source, 'templateRef': mapping['templateRef'],
                      'template': target, 'mapping': mapping})
    return items


def fixed_binding(service, db, execution):
    """Resolve an explicit committed source/version mapping, never a name."""
    ref, original = execution['templateRef'], execution['legacyTemplate']
    rows = db.execute("SELECT document FROM final_documents WHERE kind='legacy_template_binding' "
        "AND json_extract(document,'$.templateRef.id')=? AND json_extract(document,'$.templateRef.revision')=? "
        "AND json_extract(document,'$.templateRef.digest')=?",
        (ref['id'], ref['revision'], ref['digest']))
    matches = [json.loads(row[0]) for row in rows]
    matches = [row for row in matches if row['raw']['id'] == original['templateId']
               and row['raw']['revision'] == original['templateRevision']]
    if not matches:
        return None
    if any(row['template'] != matches[0]['template'] or row['raw'] != matches[0]['raw'] for row in matches):
        reject('Conflicting original Template version mappings')
    return matches[0]


def instantiate_action(service, db, action, ref, actor, identity, *, source=None):
    """Existing composition inside the caller's original operation/Attempt tx."""
    from .legacy_families import template_view
    from packages.jobs.final_service import definition
    template_view(action, {'id': 'invocation', 'output_format': 'json'}, True)
    execution = action['execution']['config']
    template_ref = execution['templateRef']
    binding = fixed_binding(service, db, execution)
    if binding and binding['deleted']:
        reject('Original Template was deleted')
    current = service._get(db, 'template', template_ref['id'])
    if current['state'] == 'retired':
        reject('Template was explicitly retired')
    template = binding['template'] if binding else current
    if template['revision'] != template_ref['revision'] or fc.digest(definition('template', template)) != template_ref['digest']:
        reject('Fixed TemplateRef changed; explicit remapping required')
    created = service.instantiate_template(template_ref['id'], {
        'expectedRevision': template_ref['revision'], 'requestKey': fc.digest(['legacy-template', identity]),
        'activate': binding['raw']['config']['enabled'] if binding else False}, actor=actor,
        fixed_template=template if binding else None)
    original = execution['legacyTemplate']
    output = {'ok': True, 'jobId': created['result']['objectIds']['job'], 'created': True, 'jobCompleted': False,
              'templateId': original['templateId'], 'templateRevision': original['templateRevision']}
    return {'actionRef': ref, 'invocationId': identity, 'status': 'completed', 'phase': 'settled', 'output': output,
            'compositionOperationId': created['operationId'], **({'source': copy.deepcopy(source)} if source is not None else {})}, created['objectRefs']
