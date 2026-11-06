"""Explicit cooperative JSON task handoff into the existing canonical stores.

No source writer, dispatch loop or replacement runtime is hosted here. Existing
dispatch identities without a complete executable canonical checkpoint are
preserved/blocked; a new admission is never used to repair missing history.
"""
import base64
import copy
import hashlib
import sqlite3
from pathlib import Path

from packages.jobs import final_contracts as fc
from packages.jobs.final_runtime.ledger import aggregate, principal
from packages.jobs.final_service import definition, object_ref
from packages.jobs.runtime_owner import cooperative_profile
from packages.scheduler.providers import legacy_schedule_plan
from scripts.job_final_migration.inventory import SCHEDULED_FIELDS, SCHEDULE_ENTRY_FIELDS
from scripts.job_final_migration.runtime_fields import RUNTIME_PRODUCTS, runtime_definition


def require(ok, message):
    if not ok:
        raise fc.ContractError('reconciliation_required', message)


def read_source(service, relative):
    root = service.owner.root
    require(cooperative_profile(root), 'Unknown original writer; preserve JSON tasks')
    path = root / relative
    require(not Path(relative).is_absolute() and path.resolve().is_relative_to(root)
            and all(not p.is_symlink() and not (hasattr(p, 'is_junction') and p.is_junction())
                    for p in (path, *path.parents) if p.is_relative_to(root)), 'Original task path is linked or escapes profile')
    raw = path.read_bytes()
    value = fc.loads(raw.decode('utf-8-sig'))
    require(isinstance(value, dict), 'Malformed original task; preserve bytes')
    return raw, value


def cohort(service, mappings):
    """A profile claim fences all old writers, so review all live siblings.

    Never freeze an unselected active JSON task/resource as a side effect of
    adopting one easy row. Historical journals participate in the preview CAS.
    """
    selected = {item['sourceFile'] for item in mappings}
    require(len(selected) == len(mappings), 'Duplicate original source path')
    root = service.owner.root
    hashes = {}
    for directory in {Path(path).parent for path in selected}:
        for file in sorted((root / directory).glob('*.json')):
            relative = file.relative_to(root).as_posix()
            raw, value = read_source(service, relative)
            hashes[relative] = hashlib.sha256(raw).hexdigest()
            if relative not in selected:
                require(not value.get('enabled') and value.get('status') not in
                        {'running', 'unknown', 'uncertain', 'cleaning', 'restarting', 'scheduled'}
                        and not value.get('undeliveredFires') and not value.get('activeExecutions'),
                        'Unselected active original profile record must be reviewed: ' + relative)
        journal = root / directory.parent / 'runs.jsonl'
        if journal.exists():
            require(not journal.is_symlink(), 'Original history journal is linked')
            hashes[journal.relative_to(root).as_posix()] = hashlib.sha256(journal.read_bytes()).hexdigest()
    return hashes


def _checkpoint(service, source):
    runtime = source.get('finalRuntime')
    touched = (source.get('runCount', 0) != 0 or source.get('dispatchKey') or source.get('runId')
               or source.get('runStartedAt') or source.get('lastFireAt'))
    if runtime is None:
        require(not touched, 'Original dispatch has no complete Run/Attempt/effect checkpoint; preserve, never re-admit')
        return None
    require(isinstance(runtime, dict) and runtime.get('profileId') == service.profile_id,
            'Original runtime profile identity differs; do not rewrite effect/operation scope')
    for key in ('admissions', 'runs', 'plans', 'steps', 'attempts', 'effects', 'effectAdmissions', 'operations', 'operationIndex', 'outbox'):
        require(key in runtime, 'Original runtime checkpoint is incomplete: ' + key)
    for rid, run in runtime['runs'].items():
        fc.validate_record('run', run)
        require(run['runId'] == rid and run['jobId'] == source['jobId'], 'Original Run/Job identity differs')
        require(run['state'] not in {'running', 'settling'}, 'Active original resource requires complete process/Worker handoff proof')
        plan = runtime['plans'].get(rid, {}).get('frozen', {})
        require(fc.digest(plan) == run['planDigest'] and plan.get('steps') and plan.get('authority'),
                'Original frozen execution plan lacks canonical identity; legacy-plan adapter remains required')
        require(all('actionRef' in step for step in plan['steps']), 'Original frozen steps have no fixed ActionRefs')
        require(run['admissionId'] in runtime['admissions'] and rid in runtime['steps'], 'Original admission/steps missing')
    if source.get('runId'):
        require(source['runId'] in runtime['runs'], 'Original dispatch Run missing')
    if source.get('dispatchKey'):
        require(any(plan.get('dispatchKey') == source['dispatchKey']
                    or plan.get('frozen', {}).get('event', {}).get('dispatchKey') == source['dispatchKey']
                    for plan in runtime['plans'].values()), 'Original dispatch key has no exact frozen Run mapping')
    for aid, attempt in runtime['attempts'].items():
        fc.validate_record('attempt', attempt)
        require(aid == attempt['attemptId'] and attempt['runId'] in runtime['runs'], 'Original Attempt identity differs')
        require(attempt['stepId'] in runtime['steps'][attempt['runId']], 'Original Attempt Step missing')
    for effect in runtime['effects']:
        fc.validate_record('effect', effect)
    for admission in runtime['admissions'].values():
        fc.validate_record('admission', admission)
    for steps in runtime['steps'].values():
        for step in steps.values():
            fc.validate_record('step', step)
    for effect in [*runtime['effects'], *runtime['effectAdmissions'].values()]:
        require(effect['attemptId'] in runtime['attempts']
                and effect['effectKey']['runId'] == runtime['attempts'][effect['attemptId']]['runId'],
                'Original effect/Attempt identity differs')
    require(source['runCount'] >= len({effect['effectKey']['runId'] for effect in runtime['effects']}),
            'Original returned dispatch count disagrees with effect receipts')
    return copy.deepcopy(runtime)


class JsonTaskHandoff:
    def __init__(self, service):
        self.service = service

    def _map(self, db, source, mapping):
        allowed = SCHEDULED_FIELDS | {'finalRuntime', 'dispatchKey', 'revision', 'generation'}
        require(not set(source) - allowed and source.get('kind') == 'scheduled-task',
                'Unknown JSON task fields; preserve source: ' + ','.join(sorted(set(source) - allowed)))
        require(all(isinstance(source.get(k), str) and source[k] for k in ('jobId', 'taskId', 'name')),
                'Original task identity is incomplete')
        require(type(source.get('enabled')) is bool and type(source.get('paused')) is bool
                and type(source.get('runCount')) is int and source['runCount'] >= 0, 'Original lifecycle/count schema is incomplete')
        require(all(key not in source or type(source[key]) is int and source[key] >= 1
                    for key in ('revision', 'generation', 'configRevision')), 'Original revision/generation is invalid')
        require(source.get('status') not in {'unknown', 'running', 'cleaning', 'restarting'},
                'Original dispatch/resource outcome unresolved; preserve source')
        require(source.get('status') != 'uncertain' or source.get('finalRuntime') is not None,
                'Original uncertain dispatch lacks its complete checkpoint; preserve source')
        require(not source.get('undeliveredFires') and not source.get('pendingRuns') and not source.get('activeExecutions'),
                'Original pending dispatch/notice requires original occurrence handoff')
        runtime = _checkpoint(self.service, source)
        entries = source.get('schedule')
        require(isinstance(entries, list) and len(entries) == 1 and isinstance(entries[0], dict),
                'Multiple-entry cursor/count handoff is not yet connected; preserve all entries')
        entry = entries[0]
        require(not set(entry) - SCHEDULE_ENTRY_FIELDS and entry.get('enabled', True), 'Unknown/disabled entry mapping requires review')
        require(entry.get('nextFireAt') == source.get('nextFireAt'), 'Original task and entry cursors disagree')
        action = source.get('action', {})
        key = {'send_session': 'session.send', 'assign': 'session.assign', 'send_qq': 'qq.send'}.get(action.get('api'))
        require(key is not None and not set(action) - {'api', 'args'}, 'Original Action family needs its reviewed pinned mapping')
        ref = mapping['actionRef']
        pinned = self.service._action_version_record(db, ref['id'], ref['revision'], digest=ref['digest'], for_ref=True)
        require(ref['id'] == RUNTIME_PRODUCTS[key], 'Original runtime arguments require the existing fixed runtime product release')
        args = copy.deepcopy(action.get('args', {}))
        args['text'] = source['text']
        if key != 'qq.send':
            args['sessionId'] = source['targetSessionId']
        action_head = self.service._get(db, 'action', ref['id'])
        require(object_ref('action', action_head) == ref, 'Reviewed runtime product pin differs')
        bindings, extra = runtime_definition(key, args, {'object': action_head, 'ref': ref})
        policies = mapping['policies']
        require(set(policies) == {'overlap', 'recovery', 'notification', 'retention'}, 'All original policies need explicit review')
        job = fc.validate_definition('job', {'name': source['name'], 'description': source.get('description', ''),
            'steps': [{'stepId': 'execute', 'actionRef': ref, 'arguments': bindings,
                       'completion': {'contract': pinned['definition']['defaultCompletion']}}], **policies, **extra})
        require(job['overlap']['mode'] == 'forbid' and job['overlap']['maxActive'] == 1,
                'Original scheduled-task has one effect writer; parallel mapping is not equivalent')
        require(not job['recovery']['automatic'], 'Original scheduled-task cannot gain automatic effect retries during handoff')
        self.service._references(db, 'job', job, enabled=True)
        if runtime is not None:
            for rid, run in runtime['runs'].items():
                require(run['configRevision'] == source.get('configRevision', 1), 'Original Run config revision differs')
                frozen = runtime['plans'][rid]['frozen']
                require(frozen['steps'] == job['steps'], 'Original frozen steps differ from reviewed task mapping')
                for step in frozen['steps']:
                    ref = step['actionRef']
                    action = frozen.get('stepActions', {}).get(step['stepId']) or frozen.get('actions', {}).get(ref['id'])
                    require(isinstance(action, dict) and action.get('id') == ref['id']
                            and action.get('revision') == ref['revision'] and action.get('digest') == ref['digest']
                            and fc.digest(action.get('definition')) == ref['digest'], 'Original frozen Action identity differs')
                    self.service._action_version_record(db, ref['id'], ref['revision'], digest=ref['digest'])
        clock_source = {'id': source['taskId'], 'name': source['name'], 'owner': 'scheduler-json', 'revision': 1,
            'enabled': source['enabled'], 'schedule': {k: v for k, v in entry.items()
                if k in {'kind', 'at', 'intervalSec', 'interval_sec', 'anchor', 'cron', 'timezone'}},
            'misfirePolicy': entry.get('misfirePolicy', source['misfirePolicy']), 'graceSec': entry.get('graceSec', 0),
            'nextFireAt': entry['nextFireAt'], 'pending': None, 'publishCursor': None}
        creator, occurrence = legacy_schedule_plan(clock_source, timezone_name=mapping['timezone'], rule_id=entry['id'])
        creator['eventTypes'] = ['scheduler.time.v2']
        maximum = source.get('maxRuns')
        if maximum is not None:
            require(type(maximum) is int and maximum > source['runCount'], 'Original max-run limit is exhausted or invalid')
        # Old runCount increments after the provider returns, including manual
        # runs. Creator.producedCount counts Events before Job execution. Never
        # substitute one for the other; the existing effect receipts own count.
        self.service.creators.registry.validate_definition(creator)
        return {'job': job, 'creator': creator, 'occurrence': occurrence, 'runtime': runtime}

    def preview(self, mappings, *, actor):
        require(actor.get('type') == 'user', 'Explicit owner review required')
        self.service._permit(actor, 'job', 'read')
        entries, blocked = [], []
        siblings = {}
        try:
            siblings = cohort(self.service, mappings)
        except (fc.ContractError, OSError, ValueError, KeyError, TypeError) as exc:
            blocked.append({'sourceFile': None, 'preserve': True, 'reason': str(exc)})
        with self.service.transaction(readonly=True) as db:
            for mapping in mappings:
                try:
                    raw, source = read_source(self.service, mapping['sourceFile'])
                    mapped = self._map(db, source, mapping)
                    entries.append({'mapping': copy.deepcopy(mapping), 'sha256': hashlib.sha256(raw).hexdigest(),
                                    'source': source, 'mapped': mapped})
                except (fc.ContractError, KeyError, ValueError, OSError, TypeError, AttributeError) as exc:
                    blocked.append({'sourceFile': mapping.get('sourceFile'), 'preserve': True, 'reason': str(exc)})
        require(len({e['source']['jobId'] for e in entries}) == len(entries)
                and len({e['source']['taskId'] for e in entries}) == len(entries), 'Duplicate original identity')
        status = self.service.owner.status()
        return {'protocol': 'json-task-handoff/v1', 'profileRoot': str(self.service.owner.root),
                'expectedEpoch': status['epoch'] if status else 0, 'cohortHashes': siblings, 'entries': entries, 'blocked': blocked}

    def apply(self, plan, request_key, *, actor):
        fc.check_request_key(request_key)
        require(actor.get('type') == 'user' and plan.get('protocol') == 'json-task-handoff/v1'
                and plan.get('profileRoot') == str(self.service.owner.root) and plan.get('entries') and not plan.get('blocked'),
                'Review all original blockers before apply')
        require(type(plan.get('expectedEpoch')) is int and plan['expectedEpoch'] >= 0,
                'Original owner epoch must be an explicit integer')
        for kind, capability in [('job', 'create'), ('job', 'lifecycle'), ('trigger', 'create'),
                                  ('trigger', 'lifecycle'), ('event_creator', 'create'), ('event_creator', 'lifecycle')]:
            self.service._permit(actor, kind, capability, execution=True)
        require(cooperative_profile(self.service.owner.root), 'Unknown original writer; preserve')
        owner = self.service.owner
        intent_key = 'json-task-handoff:' + fc.digest([e['mapping']['sourceFile'] for e in plan['entries']])
        digest = fc.digest(plan)
        with owner.transaction() as db:
            prior = db.execute('SELECT value FROM metadata WHERE key=?', (intent_key,)).fetchone()
            status = db.execute('SELECT epoch FROM owner WHERE singleton=1').fetchone()
            current = status[0] if status else 0
            require(current == plan['expectedEpoch'] or prior is not None and prior[0] == digest,
                    'Original owner generation changed without this intent')
        if owner.token is None:
            owner.claim(current)  # live/abandoned leases block here, never cleared.
        with owner.transaction() as db:
            owner._check(db, owner.token)
            require(prior is None or prior[0] == digest, 'Original handoff intent differs')
            db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)', (intent_key, digest))
        with self.service.transaction() as db:
            def transfer(operation_id):
                require(cohort(self.service, [entry['mapping'] for entry in plan['entries']]) == plan['cohortHashes'],
                        'Original profile records/history changed after preview; preserve and review')
                ids = []
                for entry in plan['entries']:
                    raw, source = read_source(self.service, entry['mapping']['sourceFile'])
                    require(hashlib.sha256(raw).hexdigest() == entry['sha256'] and source == entry['source'], 'Original bytes changed; no retry')
                    require(self._map(db, source, entry['mapping']) == entry['mapped'], 'Reviewed fixed references changed')
                    jid, tid = source['jobId'], source['taskId']
                    require(not db.execute("SELECT 1 FROM final_documents WHERE kind='job' AND id=?", (jid,)).fetchone(),
                            'Original Job identity already exists; reconcile its original operation')
                    mapped = entry['mapped']
                    obj = self.service._envelope('job', mapped['job'], actor, object_id=jid,
                        state='enabled' if source['enabled'] else 'disabled')
                    obj['configRevision'] = source.get('configRevision', 1)
                    fc.validate_object('job', obj)
                    job = {'jobId': jid, 'canonical': obj, 'config': definition('job', obj), 'configRevision': obj['configRevision']}
                    if mapped['runtime'] is not None:
                        job['finalRuntime'] = copy.deepcopy(mapped['runtime'])
                    else:
                        aggregate(job, self.service.root)['profileId'] = self.service.profile_id
                    self.service._save(db, 'job', jid, job)
                    cid = 'creator_' + fc.digest(['json-task', tid])[:24]
                    creator = self.service.creators.create(mapped['creator'], request_key=operation_id + ':' + tid,
                        creator_id=cid, state='disabled', created_by=principal(actor), owner=principal(actor))
                    cursor = creator['cursor']['rules'][source['schedule'][0]['id']]
                    cursor.update(nextFireAt=mapped['occurrence']['at'] if mapped['occurrence'] else None,
                                  nextOccurrence=mapped['occurrence'])
                    creator['state'] = ('paused' if source['paused'] else 'enabled') if source['enabled'] else 'disabled'
                    self.service.creators._write(db, creator)
                    trigger_id = 'trg_' + fc.digest(['json-task', tid])[:24]
                    trigger = self.service._envelope('trigger', {'name': source['name'] + ' tick',
                        'creatorSelector': {'mode': 'ids', 'ids': [cid]}, 'eventTypes': ['scheduler.time.v2'],
                        'targetJobId': jid}, actor, object_id=trigger_id, state='enabled')
                    self.service._save(db, 'trigger', trigger_id, trigger)
                    self.service.creators.bind_link(cid, trigger_id, jid)
                    for kind, oid in [('job', jid), ('trigger', trigger_id), ('event_creator', cid)]:
                        db.execute('INSERT INTO final_authorities VALUES(?,?,?)', (kind, oid, fc.canonical_json(actor)))
                    self.service._save(db, 'json_scheduler_handoff', tid, {'sourceFile': entry['mapping']['sourceFile'],
                        'sourceSha256': entry['sha256'], 'rawBytesBase64': base64.b64encode(raw).decode(),
                        'source': source, 'jobId': jid, 'creatorId': cid, 'triggerId': trigger_id,
                        'operationId': operation_id, 'planDigest': digest, 'intentKey': intent_key,
                        'revision': source.get('revision', 1), 'generation': source.get('generation', 1),
                        'timezone': entry['mapping']['timezone'],
                        'paused': source['paused'],
                        'originalRunIds': list(job['finalRuntime']['runs']),
                        'originalReturnedRunIds': sorted({effect['effectKey']['runId'] for effect in job['finalRuntime']['effects']})})
                    ids.append({'taskId': tid, 'jobId': jid, 'creatorId': cid, 'triggerId': trigger_id})
                return {'tasks': ids}, [object_ref('job', self.service._object(db, 'job', i['jobId'])) for i in ids], False
            result = self.service._operation(db, actor, 'create', request_key, {'jsonTaskHandoff': plan}, 'job', transfer)
        owner.set_state('active')
        return result


def binding_for_job(service, db, job_id):
    rows = db.execute("SELECT document FROM final_documents WHERE kind='json_scheduler_handoff' AND json_extract(document,'$.jobId')=?", (job_id,)).fetchall()
    require(len(rows) <= 1, 'Ambiguous original task identity')
    return fc.loads(rows[0][0]) if rows else None


def resolve(service, db, task_id):
    rows = db.execute("SELECT id,document FROM final_documents WHERE kind='json_scheduler_handoff' AND (id=? OR json_extract(document,'$.jobId')=?)", (task_id, task_id)).fetchall()
    require(len(rows) == 1, 'Original JSON task has no unique handoff receipt')
    identity, raw = rows[0]
    binding = fc.loads(raw)
    source_raw, _ = read_source(service, binding['sourceFile'])
    require(hashlib.sha256(source_raw).hexdigest() == binding['sourceSha256'], 'Original JSON task bytes changed after handoff')
    # A read projection must not acquire the owner write transaction while it
    # holds the Creator read lock: canonical writers acquire those in reverse
    # order. Read the committed immutable intent without reserving a writer.
    with sqlite3.connect(service.owner.path.as_uri() + '?mode=ro', uri=True) as owner_db:
        row = owner_db.execute('SELECT value FROM metadata WHERE key=?', (binding['intentKey'],)).fetchone()
        require(row is not None and row[0] == binding['planDigest'], 'Original writer handoff intent differs')
    require(db.execute('SELECT 1 FROM final_operations WHERE id=?', (binding['operationId'],)).fetchone(), 'Original handoff operation missing')
    return identity, binding


def project(service, task_id, *, actor, capability='read'):
    service._permit(actor, 'job', capability)
    with service.transaction(readonly=True) as db:
        tid, binding = resolve(service, db, task_id)
        job = service._get(db, 'job', binding['jobId'])
        creator = service.creators.get(binding['creatorId'])
        require(creator is not None, 'Original task clock missing')
        from packages.scheduler.history import project as historical
        task = historical(binding['source'], tid)
        cursor = next(iter(creator['cursor']['rules'].values()))
        task.update(enabled=creator['state'] in {'enabled', 'paused'},
                    paused=creator['state'] == 'paused' if creator['state'] != 'disabled' else binding['paused'],
                    nextFireAt=cursor['nextFireAt'], runCount=run_count(job, binding),
                    revision=binding['revision'], generation=binding['generation'],
                    canonicalJobId=binding['jobId'], canonicalCreatorId=creator['id'], executable=not binding.get('deleted', False))
        runs = sorted(job['finalRuntime']['runs'].values(), key=lambda r: (r['createdAt'], r['runId']), reverse=True)
        return {'task': task, 'deleted': binding.get('deleted', False),
                'runs': copy.deepcopy(runs), 'historicalExecutionAdopted': bool(binding['originalRunIds'])}


def legacy_runs(service, task_id, *, actor):
    service._permit(actor, 'job', 'read')
    with service.transaction(readonly=True) as db:
        tid, binding = resolve(service, db, task_id)
        job = service._get(db, 'job', binding['jobId'])
        state = job['finalRuntime']
        result = []
        for rid, run in state['runs'].items():
            plan = state['plans'][rid]
            event = plan['frozen'].get('event', {})
            result.append({'runId': rid, 'taskId': tid,
                'fireAt': event.get('payload', {}).get('scheduledAt', run['createdAt']),
                'actualAt': run.get('startedAt'), 'dispatchKey': plan.get('dispatchKey'),
                'status': run['state'], 'sessionId': binding['source']['targetSessionId'],
                'error': run.get('error'), 'result': {'runId': rid, 'state': run['state']}})
        return sorted(result, key=lambda row: (row['fireAt'], row['runId']), reverse=True)


def control(service, task_id, operation, *, actor, request_key=None, expected_revision=None):
    """Task-wide lifecycle aliases over the same clock/Job and receipts.

    Pause advances the existing clock under its original pausePolicy; it does
    not pause an already dispatched Run. Manual intent leaves the clock cursor
    unchanged. No legacy JSON write is performed.
    """
    require(operation in {'pause', 'resume', 'run-now', 'delete'}, 'Unknown lifecycle alias')
    capability = 'execute' if operation == 'run-now' else 'delete' if operation == 'delete' else 'lifecycle'
    service._permit(actor, 'job', capability, execution=True)
    with service.transaction() as db:
        tid, binding = resolve(service, db, task_id)
        if request_key is None:
            if operation == 'run-now':
                # Original send/assign API used taskId + wall-second as the
                # manual dedup key. Keep that scope; explicit keys are additive.
                from datetime import datetime
                request_key = tid + ':' + str(int(datetime.now().timestamp()))
            else:
                request_key = 'scheduler:' + fc.digest([binding['operationId'], operation, binding['revision']])
        fc.check_request_key(request_key)
        def mutate(operation_id):
            if expected_revision is not None:
                fc.check_revision(binding['revision'], expected_revision)
            require(not binding.get('deleted'), 'Original task is deleted')
            job = service._get(db, 'job', binding['jobId'])
            creator = service.creators.get(binding['creatorId'])
            require(creator is not None, 'Original clock missing')
            result = {'taskId': tid}
            pending = False
            if operation in {'pause', 'resume'}:
                binding['paused'] = operation == 'pause'
                # Legacy pause affects future drives; in-flight work retains
                # its original Run. A disabled clock keeps its disabled state.
                if creator['state'] in {'enabled', 'paused'}:
                    desired = 'paused' if operation == 'pause' else 'enabled'
                    if creator['state'] != desired:
                        service.creators.set_state(creator['id'], desired,
                            expected_state_revision=creator['stateRevision'])
            elif operation == 'delete':
                runtime = job['finalRuntime']
                require(not any(r['state'] in {'queued', 'running', 'retry_wait', 'paused_at_boundary', 'cancelling', 'settling', 'uncertain'}
                                for r in runtime['runs'].values()) and not creator.get('pendingPublication'),
                        'Original execution/publication unsettled; preserve task and query original identity')
                trigger = service._get(db, 'trigger', binding['triggerId'])
                trigger.update(state='retired', stateRevision=trigger['stateRevision'] + 1)
                service._save(db, 'trigger', trigger['id'], trigger)
                creator.update(state='disabled', stateRevision=creator['stateRevision'] + 1)
                service.creators._write(db, creator)
                job['canonical'].update(state='retired', stateRevision=job['canonical']['stateRevision'] + 1)
                service._save(db, 'job', job['jobId'], job)
                binding['deleted'] = True
                result['deleted'] = True
            else:
                guard_effect(service, db, job, '')
                require(job['canonical']['state'] in {'enabled', 'disabled'}, 'Original task is retired or controlled by another lifecycle')
                prior_state = job['canonical']['state']
                # Original manual dispatch was independent of schedule enable.
                # This transaction authorizes just this Run; it does not enable
                # the clock or grant future automatic admissions.
                job['canonical']['state'] = 'enabled'
                value = {'expectedRevision': job['configRevision'], 'input': job['config']['arguments']}
                admitted = service._admit(db, job, value, actor, operation_id)
                job = service._get(db, 'job', job['jobId'])
                job['canonical']['state'] = prior_state
                if 'runId' in admitted:
                    plan = job['finalRuntime']['plans'][admitted['runId']]
                    plan['dispatchKey'] = request_key
                    plan['schedulerManualIntent'] = {'handoffOperationId': binding['operationId'], 'operationId': operation_id}
                service._save(db, 'job', job['jobId'], job)
                result.update(admitted, dispatchKey=request_key)
                pending = not admitted.get('skipped', False)
            binding['revision'] += 1
            service._save(db, 'json_scheduler_handoff', tid, binding)
            return result, [object_ref('job', job['canonical'])], pending
        return service._operation(db, actor, 'create' if operation == 'run-now' else 'retire' if operation == 'delete' else operation,
            request_key, {'jsonSchedulerTask': tid, 'operation': operation, 'expectedRevision': expected_revision}, 'job', mutate)


def returned_runs(job, binding):
    old = set(binding['originalReturnedRunIds'])
    return {effect['effectKey']['runId'] for effect in job['finalRuntime']['effects']
            if effect['effectKey']['runId'] not in old}


def freeze_dispatch(service, db, job, run_id, event):
    if not event:
        return
    binding = binding_for_job(service, db, job['jobId'])
    if not binding:
        return
    require(event.get('creatorId') == binding['creatorId'], 'Original clock identity differs')
    scheduled = event.get('payload', {}).get('scheduledAt')
    from datetime import datetime
    point = datetime.fromisoformat(scheduled.replace('Z', '+00:00'))
    entry_id = binding['source']['schedule'][0]['id']
    job['finalRuntime']['plans'][run_id]['dispatchKey'] = f"{binding['source']['taskId']}:{entry_id}:{int(point.timestamp())}"


def run_count(job, binding):
    return binding['source']['runCount'] + len(returned_runs(job, binding))


def guard_effect(service, db, job, run_id):
    binding = binding_for_job(service, db, job['jobId'])
    if binding:
        maximum = binding['source'].get('maxRuns')
        if maximum is not None and run_id not in returned_runs(job, binding) and run_count(job, binding) >= maximum:
            raise fc.ContractError('capacity_exceeded', 'Original task maxRuns exhausted; no further effect')


def manual_intent(service, db, job, run_id):
    """Prove one original manual dispatch, never automatic start authority."""
    binding = binding_for_job(service, db, job['jobId'])
    if not binding or job['canonical']['state'] != 'disabled':
        return False
    marker = job['finalRuntime']['plans'][run_id].get('schedulerManualIntent')
    if not marker or marker.get('handoffOperationId') != binding['operationId']:
        return False
    receipt = db.execute('SELECT document FROM final_operations WHERE id=?', (marker.get('operationId'),)).fetchone()
    require(receipt is not None and fc.loads(receipt[0])['response'].get('runId') == run_id,
            'Original manual dispatch operation is unproven; query only')
    return True


def guard_clock(service, item):
    with service.transaction() as db:
        rows = db.execute("SELECT document FROM final_documents WHERE kind='json_scheduler_handoff' AND json_extract(document,'$.creatorId')=?", (item['id'],)).fetchall()
        if not rows:
            return
        require(len(rows) == 1, 'Ambiguous original clock identity')
        binding = fc.loads(rows[0][0])
        job = service._get(db, 'job', binding['jobId'])
        maximum = binding['source'].get('maxRuns')
        if maximum is not None and run_count(job, binding) >= maximum and not item.get('pendingPublication'):
            current = service.creators._read_db(db, item['id'])
            current.update(state='disabled', stateRevision=current['stateRevision'] + 1)
            service.creators._write(db, current)
        else:
            return
    raise fc.ContractError('capacity_exceeded', 'Original task maxRuns exhausted; clock disabled')
