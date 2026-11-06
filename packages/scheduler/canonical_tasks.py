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
            entry_id = patch.get('entry_id')
            if len(source.get('schedule', [])) > 1 and entry_id is not None:
                require(isinstance(entry_id, str) and entry_id in {item['id'] for item in source['schedule']},
                        'Per-entry update requires an existing entry identity')
                expected_entry = patch.get('expected_entry_revision')
                current_entry_revision = binding.get('entryRevisions', {}).get(entry_id, 1)
                require(type(expected_entry) is int and expected_entry == current_entry_revision,
                        'Per-entry schedule revision changed; query the original entry')
                target = next(item for item in source['schedule'] if item['id'] == entry_id)
                if patch.get('delete_entry'):
                    require(len(source['schedule']) > 1, 'The final schedule entry requires task lifecycle delete')
                    source['schedule'] = [item for item in source['schedule'] if item['id'] != entry_id]
                else:
                    if patch.get('schedule') is not None:
                        replacement = copy.deepcopy(patch['schedule'])
                        replacement['id'] = entry_id
                        replacement['nextFireAt'] = target.get('nextFireAt')
                        replacement['enabled'] = target.get('enabled', True)
                        target.clear(); target.update(replacement)
                    if 'entry_enabled' in patch:
                        target['enabled'] = bool(patch['entry_enabled'])
                        if not target['enabled']:
                            target['nextFireAt'] = None
                        else:
                            target['nextFireAt'] = binding.get('entryCursors', {}).get(entry_id)
                            require(target['nextFireAt'] is not None,
                                    'Entry re-enable lacks a retained cursor; preserve and review')
                    if 'entry_paused' in patch:
                        binding.setdefault('entryPaused', {})[entry_id] = bool(patch['entry_paused'])
                        if patch['entry_paused']:
                            target['nextFireAt'] = None
                        else:
                            target['nextFireAt'] = binding.get('entryCursors', {}).get(entry_id)
                            require(target['nextFireAt'] is not None,
                                    'Entry resume lacks a retained cursor; preserve and review')
                    if 'entry_misfire_policy' in patch:
                        # Canonical time provider has one policy for the whole
                        # Creator. Do not silently change only one legacy rule.
                        require(all(item.get('misfirePolicy', source['misfirePolicy']) ==
                                    patch['entry_misfire_policy'] for item in source['schedule']),
                                'Per-entry policy differs from the canonical Creator policy; preserve')
                        target['misfirePolicy'] = patch['entry_misfire_policy']
                active = [item for item in source['schedule'] if item.get('enabled', True) and item.get('nextFireAt')]
                source['nextFireAt'] = min((item['nextFireAt'] for item in active), default=None)
                mapped_by = mapping(service, db, source, policies={key: job['config'][key]
                    for key in ('overlap', 'recovery', 'notification', 'retention')}, timezone=binding['timezone'])
                mapped = JsonTaskHandoff(service)._map(db, source, mapped_by, definition_only=True)
                service.write('event_creator', creator['id'], {'definition': mapped['creator'],
                    'expectedRevision': creator['revision'], 'requestKey': operation_id + ':entry'}, actor=actor)
                creator = service.creators.get(creator['id'])
                for item in source['schedule']:
                    cursor = creator['cursor']['rules'][item['id']]
                    paused = binding.get('entryPaused', {}).get(item['id'], False)
                    if paused or not item.get('enabled', True):
                        cursor.update(nextFireAt=None, nextOccurrence=None)
                    elif item.get('nextFireAt'):
                        cursor['nextFireAt'] = item['nextFireAt']
                service.creators._write(db, creator)
                binding.setdefault('entryRevisions', {})[entry_id] = current_entry_revision + 1
                retained = binding.setdefault('entryCursors', {})
                for item in source['schedule']:
                    if item.get('nextFireAt') is not None:
                        retained[item['id']] = item['nextFireAt']
                if patch.get('delete_entry'):
                    retained.pop(entry_id, None)
                binding['currentSource'] = source
                binding['revision'] += 1
                service._save(db, 'json_scheduler_handoff', tid, binding)
                return ({'taskId': tid, 'entryId': entry_id, 'entryRevision': current_entry_revision + 1},
                    [object_ref('job', service._object(db, 'job', job['jobId']))], False)
            if len(source.get('schedule', [])) > 1 and any(key in patch for key in
                    ('schedule', 'enabled', 'paused', 'misfire_policy', 'next_fire_at')):
                require(False, 'Multi-entry schedule edit requires per-entry revision/CAS; preserve all entry cursors')
            if source['action']['api'] == 'shell':
                # Use the already reviewed effective executable, never resolve
                # a retained relative path against today's process cwd.
                source['action']['args']['cwd'] = job['config']['steps'][0]['arguments']['cwd']['value']
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


def request_key(value):
    # A no-key old create denotes a new task; no guessed replay is introduced.
    return value if value is not None else 'scheduler-config:' + secrets.token_hex(16)
