"""Legacy task configuration over the existing canonical graph and receipts.

Creation is explicitly new canonical work, never evidence of source migration.
Updates retain the adopted source bytes and all frozen execution identities.
"""
import copy
from datetime import datetime
import secrets

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import definition, object_ref
from packages.scheduler import store
from packages.scheduler.json_handoff import JsonTaskHandoff, install_graph, resolve, require
from scripts.job_final_migration.runtime_fields import RUNTIME_PRODUCTS


def mapping(service, db, source, *, policies=None, timezone=None):
    key = {'assign': 'session.assign', 'send_session': 'session.send', 'send_qq': 'qq.send'}.get(source['action']['api'])
    aid = ({'shell': 'product_process_logs_v1', 'resume_legal_running': 'product_resume_legal_running_v1'}.get(source['action']['api'])
           or RUNTIME_PRODUCTS[key])
    action = service._get(db, 'action', aid)
    return {'actionRef': object_ref('action', action),
        # Old cron uses spec.timezone. Once/interval use host-local wall time;
        # choosing the form's cron timezone for those would change the cursor.
        'timezone': timezone or (source['schedule'][0].get('timezone') if source['schedule'][0]['kind'] == 'cron' else None)
            or datetime.now().astimezone().isoformat()[-6:],
        'policies': policies or {'overlap': {'mode': 'forbid', 'maxActive': 1, 'maxPending': 0},
            'recovery': {'automatic': False, 'maxAttempts': 1, 'backoffSeconds': ['1'], 'stableResetSeconds': '0'},
            'notification': {'on': [], 'recipients': []}, 'retention': {'policyRef': 'legacy-scheduler'}}}


def create(service, payload, *, actor, request_key):
    require(service.owner.token is not None and service.owner.lock.file is not None,
            'Canonical owner must already hold the profile; creation cannot imply legacy handoff')
    for kind, capability in [('job', 'create'), ('job', 'lifecycle'), ('event_creator', 'create'),
                             ('event_creator', 'lifecycle'), ('trigger', 'create'), ('trigger', 'lifecycle')]:
        service._permit(actor, kind, capability, execution=True)
    with service.transaction() as db:
        def mutate(operation_id):
            prepared = copy.deepcopy(payload)
            if not prepared.get('name', '').strip():
                taken = {j['canonical']['name'] for j in service._all(db, 'job')}
                number = 1
                while 'job-' + str(number) in taken:
                    number += 1
                prepared['name'] = 'job-' + str(number)
            source = store.prepare_task(prepared, root=service.owner.root / 'background_jobs')
            mapped_by = mapping(service, db, source)
            mapped = JsonTaskHandoff(service)._map(db, source, mapped_by, definition_only=True)
            ids = install_graph(service, db, source, mapped, actor, operation_id,
                {'origin': 'canonical-create', 'timezone': mapped_by['timezone'], 'receiptReview': None})
            return ids, [object_ref('job', service._object(db, 'job', ids['jobId']))], False
        return service._operation(db, actor, 'create', request_key,
            {'canonicalLegacyTaskCreate': payload}, 'job', mutate)


def update(service, task_id, patch, *, actor, request_key, expected_revision=None):
    service._permit(actor, 'job', 'updateConfig', execution=True)
    service._permit(actor, 'event_creator', 'updateConfig', execution=True)
    with service.transaction() as db:
        tid, binding = resolve(service, db, task_id)
        def mutate(operation_id):
            if expected_revision is not None:
                fc.check_revision(binding['revision'], expected_revision)
            require(not binding.get('deleted'), 'Original task was deleted')
            job = service._get(db, 'job', binding['jobId'])
            creator = service.creators.get(binding['creatorId'])
            require(not creator.get('pendingPublication'), 'Original publication must settle before clock configuration changes')
            source = copy.deepcopy(binding.get('currentSource', binding['source']))
            if source['action']['api'] == 'shell':
                # Use the already reviewed effective executable, never resolve
                # a retained relative path against today's process cwd.
                source['action']['args']['cwd'] = job['config']['steps'][0]['arguments']['cwd']['value']
            entry_id = patch.get('entry_id')
            require(entry_id is not None or not any(key in patch for key in (
                'expected_entry_revision', 'entry_enabled', 'entry_paused', 'entry_misfire_policy',
                'entry_grace_seconds', 'delete_entry')), 'Entry controls require an original entry identity')
            require('insert_index' not in patch or 'append_schedule' in patch,
                    'Insertion index requires explicit new schedule specs')
            if entry_id is not None or isinstance(patch.get('schedule'), list) or 'append_schedule' in patch or len(source['schedule']) > 1:
                return _update_entries(service, db, tid, binding, source, job, creator, patch,
                    actor, operation_id, expected_revision)
            # Start from the actual clock cursor, not the immutable source's
            # old timestamp. The old pure planner owns recomputation semantics.
            cursor = next(iter(creator['cursor']['rules'].values()))
            source['nextFireAt'] = cursor['nextFireAt']
            source['schedule'][0]['nextFireAt'] = cursor['nextFireAt']
            source.update(enabled=creator['state'] in {'enabled', 'paused'}, paused=binding['paused'])
            source.update(store.plan_update(source, patch, root=service.owner.root / 'background_jobs'))
            # The old helper projects a naive local aggregate timestamp while
            # the clock entry retains UTC. One entry has one cursor fact.
            source['nextFireAt'] = source['schedule'][0]['nextFireAt']
            mapped_by = mapping(service, db, source, policies={key: job['config'][key]
                for key in ('overlap', 'recovery', 'notification', 'retention')}, timezone=binding['timezone'])
            mapped = JsonTaskHandoff(service)._map(db, source, mapped_by, definition_only=True)
            # A definition edit uses the existing configuration CAS and runtime
            # audit. Previously frozen Runs/Attempts are untouched.
            service.write('job', job['jobId'], {'definition': mapped['job'],
                'expectedRevision': job['configRevision'], 'requestKey': operation_id + ':job'}, actor=actor)
            changed_clock = any(key in patch for key in ('schedule', 'enabled', 'misfire_policy'))
            if changed_clock:
                service._permit(actor, 'event_creator', 'lifecycle', execution=True)
                service.write('event_creator', creator['id'], {'definition': mapped['creator'],
                    'expectedRevision': creator['revision'], 'requestKey': operation_id + ':clock'}, actor=actor)
                creator = service.creators.get(creator['id'])
                cursor = creator['cursor']['rules'][source['schedule'][0]['id']]
                cursor.update(nextFireAt=mapped['occurrence']['at'] if mapped['occurrence'] else None,
                              nextOccurrence=mapped['occurrence'])
            desired = ('paused' if source['paused'] else 'enabled') if source['enabled'] else 'disabled'
            if desired != creator['state']:
                service._permit(actor, 'event_creator', 'lifecycle', execution=True)
                creator['stateRevision'] += 1
                creator['state'] = desired
            service.creators._write(db, creator)
            # Canonical Job enable is separate from the clock. Manual execution
            # remains possible when a schedule is disabled, as in old run-now.
            updated = service._get(db, 'job', job['jobId'])
            desired_job = 'enabled' if source['enabled'] else 'disabled'
            if desired_job != updated['canonical']['state']:
                service._permit(actor, 'job', 'lifecycle', execution=True)
                service.control('job', job['jobId'], {'operation': 'enable' if source['enabled'] else 'disable',
                    'expectedRevision': updated['configRevision'], 'expectedStateRevision': updated['canonical']['stateRevision'],
                    'requestKey': operation_id + ':state', 'parameters': {}}, actor=actor)
            binding.update(currentSource=source, revision=binding['revision'] + 1, paused=source['paused'])
            service._save(db, 'json_scheduler_handoff', tid, binding)
            return {'taskId': tid}, [object_ref('job', service._object(db, 'job', job['jobId']))], False
        return service._operation(db, actor, 'update', request_key,
            {'canonicalLegacyTaskUpdate': tid, 'patch': patch, 'expectedRevision': expected_revision}, 'job', mutate)


def _update_entries(service, db, tid, binding, source, job, creator, patch, actor, operation_id, expected_revision):
    """Translate old entry edits inside the existing owner-fenced operation."""
    from packages.jobs.schedule_calculations import _spec_to_entry, _epoch_to_iso
    require(type(expected_revision) is int or patch.get('entry_id') is not None,
            'Bulk edits require the original task revision')
    source.update(enabled=creator['state'] in {'enabled', 'paused'}, paused=binding['paused'])
    entries = source['schedule']
    for item in entries:
        live = creator['cursor']['rules'][item['id']]['nextFireAt']
        item['nextFireAt'] = live
        if live is not None:
            binding.setdefault('entryCursors', {})[item['id']] = live
    # Metadata uses the original pure planner; no first-entry clock side effect.
    metadata = {k: v for k, v in patch.items() if k in
        {'name', 'description', 'text', 'target_session_id', 'max_runs', 'paused'}}
    source.update(store.plan_update(source, metadata, root=service.owner.root / 'background_jobs'))
    if 'misfire_policy' in patch:
        require(patch['misfire_policy'] in {'fire_now', 'skip'}, 'Invalid misfire policy')
        source['misfirePolicy'] = patch['misfire_policy']
    if 'enabled' in patch:
        source['enabled'] = bool(patch['enabled'])
    entry_id = patch.get('entry_id')
    lifecycle = any(k in patch for k in ('entry_paused', 'entry_enabled', 'delete_entry',
        'schedule', 'append_schedule', 'enabled', 'paused'))
    if lifecycle:
        service._permit(actor, 'event_creator', 'lifecycle', execution=True)
    created_iso = _epoch_to_iso(source.get('createdAt'))
    def fresh(spec, defaults=None):
        spec = {**(defaults or {}), **copy.deepcopy(spec)}
        item = _spec_to_entry(spec, default_misfire=source['misfirePolicy'], created_iso=created_iso)
        if not item['enabled']:
            item['nextFireAt'] = None
        return item
    retire_last = False
    if entry_id is not None:
        require(isinstance(entry_id, str) and entry_id in {item['id'] for item in entries},
                'Per-entry update requires an existing entry identity')
        revision = binding.get('entryRevisions', {}).get(entry_id, 1)
        require(type(patch.get('expected_entry_revision')) is int and patch['expected_entry_revision'] == revision,
                'Per-entry schedule revision changed; query the original entry')
        target = next(item for item in entries if item['id'] == entry_id)
        require(not isinstance(patch.get('schedule'), list) and 'append_schedule' not in patch,
                'Entry replacement must identify exactly one schedule object')
        if patch.get('delete_entry'):
            require(not any(key in patch for key in ('schedule', 'entry_enabled', 'entry_paused',
                'entry_misfire_policy', 'entry_grace_seconds')),
                'Conflicting delete and entry replacement; no fields applied')
            retire_last = len(entries) == 1
            if not retire_last:
                entries[:] = [item for item in entries if item['id'] != entry_id]
        else:
            if patch.get('schedule') is not None:
                replacement = fresh(patch['schedule'], {k: target[k] for k in
                    ('enabled', 'misfirePolicy', 'graceSec') if k in target})
                replacement['id'] = entry_id
                target.clear(); target.update(replacement)
            if 'entry_enabled' in patch:
                target['enabled'] = bool(patch['entry_enabled'])
                if target['enabled']:
                    target['nextFireAt'] = binding.get('entryCursors', {}).get(entry_id) or fresh(target)['nextFireAt']
                else:
                    target['nextFireAt'] = None
            if 'entry_paused' in patch:
                binding.setdefault('entryPaused', {})[entry_id] = bool(patch['entry_paused'])
                if not patch['entry_paused']:
                    target['nextFireAt'] = binding.get('entryCursors', {}).get(entry_id)
                    require(target['nextFireAt'] is not None, 'Entry resume lacks a retained cursor; preserve and review')
            if 'entry_misfire_policy' in patch:
                target['misfirePolicy'] = patch['entry_misfire_policy']
            if 'entry_grace_seconds' in patch:
                target['graceSec'] = patch['entry_grace_seconds']
        binding.setdefault('entryRevisions', {})[entry_id] = revision + 1
    elif 'schedule' in patch:
        # Original replace_task_schedule allocates fresh entry IDs for bulk
        # replacement. It never claims these are adopted historical identities.
        specs = patch['schedule'] if isinstance(patch['schedule'], list) else [patch['schedule']]
        require(1 <= len(specs) <= fc.MAX_SCHEDULER_RULES, 'Bulk schedule must be non-empty and bounded')
        entries = source['schedule'] = [fresh(spec) for spec in specs]
    if 'append_schedule' in patch:
        specs = patch['append_schedule']
        require(isinstance(specs, list) and specs and len(entries) + len(specs) <= fc.MAX_SCHEDULER_RULES,
                'Inserted schedules must be non-empty and bounded')
        index = patch.get('insert_index', len(entries))
        require(type(index) is int and 0 <= index <= len(entries), 'Invalid schedule insertion index')
        entries[index:index] = [fresh(spec) for spec in specs]
    binding['multiEntry'] = binding.get('multiEntry', False) or len(entries) > 1
    known = set(binding.get('knownEntryIds', binding['originalEntryIds']))
    known.update(item['id'] for item in entries)
    binding['knownEntryIds'] = sorted(known)
    for item in entries:
        eid = item['id']
        binding.setdefault('entryRevisions', {}).setdefault(eid, 1)
        if item.get('nextFireAt') is not None:
            binding.setdefault('entryCursors', {})[eid] = item['nextFireAt']
        if binding.get('entryPaused', {}).get(eid, False) or not item.get('enabled', True):
            item['nextFireAt'] = None
    source['nextFireAt'] = min((item['nextFireAt'] for item in entries if item.get('nextFireAt')), default=None)
    mapped_by = mapping(service, db, source, policies={key: job['config'][key]
        for key in ('overlap', 'recovery', 'notification', 'retention')}, timezone=binding['timezone'])
    # Configuration edits keep the original pinned Action version.
    mapped_by['actionRef'] = copy.deepcopy(job['config']['steps'][0]['actionRef'])
    mapped = JsonTaskHandoff(service)._map(db, source, mapped_by, definition_only=True)
    if metadata or 'misfire_policy' in patch:
        service.write('job', job['jobId'], {'definition': mapped['job'],
            'expectedRevision': job['configRevision'], 'requestKey': operation_id + ':job'}, actor=actor)
    service.write('event_creator', creator['id'], {'definition': mapped['creator'],
        'expectedRevision': creator['revision'], 'requestKey': operation_id + ':entries'}, actor=actor)
    creator = service.creators.get(creator['id'])
    for item in entries:
        occurrence = mapped['occurrences'].get(item['id'])
        creator['cursor']['rules'][item['id']].update(
            nextFireAt=occurrence['at'] if occurrence else None, nextOccurrence=occurrence)
    desired = ('paused' if source['paused'] else 'enabled') if source['enabled'] else 'disabled'
    if creator['state'] != desired:
        creator.update(state=desired, stateRevision=creator['stateRevision'] + 1)
    service.creators._write(db, creator)
    if 'enabled' in patch:
        service._permit(actor, 'job', 'lifecycle', execution=True)
        current = service._get(db, 'job', job['jobId'])
        service.control('job', job['jobId'], {'operation': 'enable' if source['enabled'] else 'disable',
            'expectedRevision': current['configRevision'], 'expectedStateRevision': current['canonical']['stateRevision'],
            'requestKey': operation_id + ':state', 'parameters': {}}, actor=actor)
    binding.update(currentSource=source, revision=binding['revision'] + (0 if retire_last else 1), paused=source['paused'])
    service._save(db, 'json_scheduler_handoff', tid, binding)
    if retire_last:
        from packages.scheduler.json_handoff import control
        # Empty time Creators are invalid. Retire through the original task
        # primitive, preserving tombstone metadata and unsettled-Run guards.
        retired = control(service, tid, 'delete', actor=actor,
            request_key=operation_id + ':last-entry', expected_revision=binding['revision'])
        return retired['result'], retired['objectRefs'], False
    result = {'taskId': tid}
    if entry_id is not None:
        result.update(entryId=entry_id, entryRevision=binding['entryRevisions'][entry_id])
    return result, [object_ref('job', service._object(db, 'job', job['jobId']))], False


def request_key(value):
    # A no-key old create denotes a new task; no guessed replay is introduced.
    return value if value is not None else 'scheduler-config:' + secrets.token_hex(16)
