"""Product-owned Action definitions for compatibility executors.

These live in the ordinary Action registry. This module is a definition factory,
not a second registry or a permission source. Lifecycle can only be called by its
existing supervisor entry point, never delegated through an enabled user Job.
"""
import copy
import hashlib
import json
import time
import uuid
import asyncio

_waiters = {}

KINDS = ('background-process', 'session-message', 'session-broadcast', 'scheduled-task', 'main-lifecycle')


def definition(kind):
    if kind not in KINDS:
        raise ValueError('Unknown system executor')
    fields = {
        'background-process': {'sessionId': {'type': 'string', 'minLength': 1},
            'argv': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
            'cwd': {'type': 'string', 'minLength': 1}, 'hideWindow': {'type': 'boolean', 'default': True}},
        'session-message': {'sessionId': {'type': 'string', 'minLength': 1}, 'text': {'type': 'string', 'minLength': 1}},
        'session-broadcast': {'sessionIds': {'type': 'array', 'minItems': 1, 'maxItems': 256, 'items': {'type': 'string', 'minLength': 1}}, 'text': {'type': 'string', 'minLength': 1}},
        'scheduled-task': {'sessionId': {'type': ['string', 'null']}, 'text': {'type': 'string'},
            'action': {'oneOf': [
                {'type': 'object', 'additionalProperties': False, 'required': ['api'],
                 'properties': {'api': {'enum': ['assign', 'send_session', 'resume_legal_running']}}},
                {'type': 'object', 'additionalProperties': False, 'required': ['api', 'args'], 'properties': {
                    'api': {'const': 'shell'}, 'args': {'type': 'object', 'additionalProperties': False,
                    'required': ['command', 'cwd'], 'properties': {'command': {'type': 'string', 'minLength': 1},
                    'cwd': {'type': 'string', 'minLength': 1}, 'hideWindow': {'type': 'boolean'}}}}},
                {'type': 'object', 'additionalProperties': False, 'required': ['api', 'args'], 'properties': {
                    'api': {'const': 'send_qq'}, 'args': {'type': 'object', 'additionalProperties': False,
                    'required': ['targetType', 'targetId'], 'properties': {'targetType': {'enum': ['user', 'group']},
                    'targetId': {'type': 'string', 'minLength': 1}, 'botUin': {'type': 'string'}}}}},
            ]}},
        'main-lifecycle': {},
    }[kind]
    required = {'background-process': ['sessionId', 'argv', 'cwd'], 'session-message': ['sessionId', 'text'],
                'session-broadcast': ['sessionIds', 'text'], 'scheduled-task': ['sessionId', 'text', 'action'], 'main-lifecycle': []}[kind]
    return {'id': 'system_' + kind.replace('-', '_'), 'name': kind, 'description': 'Product executor: ' + kind,
            'system': True, 'enabled': True, 'idempotent': False,
            'monitoring': kind == 'background-process', 'recoverable': kind == 'background-process',
            'effect': 'high_risk' if kind == 'main-lifecycle' else 'write_local', 'output_format': 'json',
            'input_schema': {'type': 'object', 'properties': fields, 'required': required, 'additionalProperties': False},
            'execution': {'kind': 'system_job', 'operation': kind}}


def validate(action):
    expected = definition(action.get('execution', {}).get('operation'))
    if {**action, 'enabled': expected['enabled']} != expected:
        raise ValueError('System Action definitions are immutable product definitions')


def install(store):
    """Explicit startup installation. Conflicts fail closed; no overwrite of user data."""
    def change(state):
        for kind in KINDS:
            item = definition(kind)
            old = state['actions'].get(item['id'])
            if old is not None and old != item:
                raise ValueError('System Action ID conflict: ' + item['id'])
            if old is None:
                state['actions'][item['id']] = copy.deepcopy(item)
                state.setdefault('actionRevisions', {})[item['id']] = 1
    state = store.read(effective=False)
    if any(definition(kind)['id'] not in state['actions'] for kind in KINDS):
        store.mutate(change)


def arguments(job):
    kind = job['kind']
    if kind == 'background-process':
        return {'sessionId': job['targetSessionId'], 'argv': copy.deepcopy(job['argv']),
                'cwd': job['cwd'], 'hideWindow': job.get('hideWindow', True)}
    if kind == 'session-message':
        return {'sessionId': job['targetSessionId'], 'text': job['text']}
    if kind == 'session-broadcast':
        return {'sessionIds': list(dict.fromkeys(job['targetSessionIds'])), 'text': job['text']}
    if kind == 'scheduled-task':
        action = copy.deepcopy(job.get('action') or {'api': 'assign'})
        if action.get('api') not in ('assign', 'send_session', 'resume_legal_running', 'send_qq', 'shell'):
            action = {'api': 'assign'}  # Persisted legacy fallback only; public inputs remain strict.
        else:
            action = {key: action[key] for key in ('api', 'args') if key in action}
            if action['api'] in ('assign', 'send_session', 'resume_legal_running') and action.get('args') == {}:
                action.pop('args')  # Empty historical placeholder, not an undeclared input.
        return {'sessionId': job.get('targetSessionId'), 'text': job.get('text', ''),
                'action': action}
    return {}


def prepare(job, dispatch_key, *, prevalidated=False):
    """Mutate the caller's locked Job image before its atomic admission write.

    Compatibility execution uses the same Job record, not another Job/registry.
    The private snapshot is removed in the same atomic write as terminal audit.
    A failed write therefore cannot publish a false cleaned terminal state.
    """
    action = definition(job['kind'])
    params = arguments(job)
    if not prevalidated:
        import jsonschema
        jsonschema.Draft202012Validator(action['input_schema']).validate(params)
    prior = job.get('attemptSnapshot')
    if prior:
        raise ValueError('Previous system Action execution still running or cleaning; never replay an unknown receipt')
    previous=next((item for item in reversed(job.get('attempts',[])) if item.get('dispatchKey')==dispatch_key),None)
    rejected=previous and previous.get('status')=='failed' and (previous.get('receipt') or {}).get('rejectionCode')=='session_not_found'
    if previous and not rejected:
        raise ValueError('System Action occurrence already admitted; unknown is never replayed')
    if len(job.get('attempts', [])) >= 10000:
        raise ValueError('Job attempt audit capacity reached; archive before further execution')
    run_id = previous['runId'] if rejected else 'run_' + uuid.uuid4().hex[:24]
    attempt_id = 'attempt_' + uuid.uuid4().hex[:24]
    digest = hashlib.sha256(json.dumps(action, sort_keys=True).encode('utf-8')).hexdigest()
    snapshot = {'definition': action, 'arguments': copy.deepcopy(params), 'revision': 1, 'digest': digest,
                'dispatchKey': dispatch_key, 'runId': run_id, 'attemptId': attempt_id}
    if job['kind'] == 'main-lifecycle':
        snapshot['context'] = {key: copy.deepcopy(job[key]) for key in
            ('operation','options','requestId','root','port','oldPid','oldPidCreatedAt') if key in job}
        snapshot['contextDigest'] = hashlib.sha256(json.dumps(snapshot['context'],sort_keys=True).encode('utf-8')).hexdigest()
    job['attemptSnapshot'] = snapshot
    job['snapshotCleanup'] = 'active'
    job['runId'] = run_id
    job.setdefault('attempts', []).append({'id': attempt_id, 'runId': run_id, 'count': previous['count']+1 if rejected else 1,
        'actionId': action['id'], 'actionRevision': 1, 'digest': digest, 'dispatchKey': dispatch_key,
        'status': 'running', 'startedAt': time.time(), 'backoffSec': 0, 'receipt': None,
        'sideEffectUncertainty': False})
    return snapshot


def finish(job, status, receipt=None):
    snapshot = job.pop('attemptSnapshot', None)
    if not snapshot:
        return
    for attempt in job.get('attempts', []):
        if attempt['id'] == snapshot['attemptId']:
            attempt.update(status=status, finishedAt=time.time(), sideEffectUncertainty=status in ('unknown', 'uncertain'),
                           receipt=copy.deepcopy(receipt),snapshotCleanup='cleaned')
            if 'contextDigest' in snapshot:attempt['contextDigest']=snapshot['contextDigest']
            job['lastExecution'] = {**attempt, 'attemptId': attempt['id']}
            break
    job['snapshotCleanup'] = 'cleaned'


def sync(job, created=False):
    """Process/lifecycle writers already commit admission before their effects."""
    kind = job.get('kind')
    if created and (kind == 'background-process' and not job.get('scheduledParentJobId') and job.get('status') == 'starting'
                    or kind == 'main-lifecycle' and job.get('phase') == 'requested'):
        prepare(job, job['jobId'] + ':initial')
    if job.get('status') in ('completed', 'failed', 'cancelled', 'timed_out', 'unknown', 'uncertain'):
        finish(job, job['status'])


async def invoke(job, dispatch_key, effect, registry_root=None):
    """Run a compatibility effect against the admitted immutable arguments.

    Only trusted existing business entry points supply effect; no client callable
    or arbitrary API is accepted. The old executor retains all business gates.
    """
    from packages.core import background_jobs as jobs
    from packages.plugins.lifecycle import io
    def claim():
        with jobs._lock, jobs._job_lock(job['jobId'], registry_root):
            latest = jobs.get(job['jobId'], registry_root)
            if not latest or latest.get('status') in ('cancelled', 'unknown', 'uncertain'):
                raise ValueError('Job no longer eligible')
            snapshot = prepare(latest, dispatch_key)
            jobs._write_job(jobs._job_path(job['jobId'], registry_root), latest)
            frozen = copy.deepcopy(latest)
            params = snapshot['arguments']
            for field, source in (('text', 'text'), ('targetSessionId', 'sessionId'), ('targetSessionIds', 'sessionIds'), ('action', 'action')):
                if source in params:
                    frozen[field] = copy.deepcopy(params[source])
            return frozen
    frozen = await io(claim)
    try:
        result = await effect(frozen)
    except BaseException:
        await io(settle, job['jobId'], 'uncertain', None, registry_root)
        raise
    status = result.get('status', 'unknown') if isinstance(result, dict) else 'unknown'
    if status != 'running':
        outcome = 'uncertain' if status in ('unknown', 'uncertain', 'unconfirmed') else 'failed' if status in ('error', 'failed') else 'cancelled' if status == 'cancelled' else 'completed'
        # Keep only non-executable receipt identity, never delivery text/arguments.
        receipt = {key: result[key] for key in ('status', 'queueItemId', 'workerId', 'processJobId', 'dispatchKey') if key in result} if isinstance(result, dict) else None
        if receipt is not None and status=='error' and frozen.get('targetSessionId') and result.get('result')==f"Session {frozen['targetSessionId']} not found":
            receipt['rejectionCode']='session_not_found'
        await io(settle, job['jobId'], outcome, receipt, registry_root)
    return result


def settle(job_id, status, receipt, registry_root):
    from packages.core import background_jobs as jobs
    with jobs._lock, jobs._job_lock(job_id, registry_root):
        current = jobs.get(job_id, registry_root)
        if current:
            finish(current, status, receipt)
            if status in ('unknown','uncertain'):
                current.update(status='uncertain',enabled=False,lastStatus='uncertain',lastError='Unconfirmed effect; automatic replay blocked')
            jobs._write_job(jobs._job_path(job_id, registry_root), current)


def recovery_ready(action,params):
    """Admission probe only; business entry points still recheck their own gates."""
    if action.get('execution',{}).get('kind')!='system_job':return True
    if action['execution']['operation']!='background-process':return True
    from packages.core import session,worker,legal_offline
    target=session.get(params.get('sessionId'))
    if target is None:return False
    return not (getattr(target,'queue_paused',False) or getattr(target,'readonly_session',False)
                or legal_offline.is_illegal(target) or worker._native_handoff_blocked(target))


def notify(event):
    """In-process waiters observe the existing durable Job terminal event path."""
    job = event.get('job') or {}
    candidates = [(job.get('jobId'), job)]
    delivery = job.get('lastDelivery')
    if isinstance(delivery, dict) and delivery.get('processJobId'):
        candidates.append((delivery['processJobId'], {**delivery, 'status': delivery.get('processStatus')}))
    for jid, current in candidates:
        if current.get('status') not in ('completed', 'failed', 'cancelled', 'unknown', 'uncertain'):
            continue
        for loop, future in list(_waiters.get(jid, [])):
            def resolve(future=future, current=copy.deepcopy(current)):
                if not future.done(): future.set_result(current)
            loop.call_soon_threadsafe(resolve)


async def wait_process(child, root):
    from packages.core import background_jobs as jobs
    from packages.plugins.lifecycle import io
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    jid = child['jobId']
    item = (loop, future)
    _waiters.setdefault(jid, []).append(item)
    try:
        current = await io(jobs.get, jid, root)
        if current and current.get('status') in ('completed', 'failed', 'cancelled', 'unknown', 'uncertain'):
            return current
        return await future
    except asyncio.CancelledError:
        # Retain all original owned-process identity checks. No PID-only kill.
        await io(jobs.cancel, jid, root)
        raise
    finally:
        _waiters[jid].remove(item)
        if not _waiters[jid]: _waiters.pop(jid, None)


async def execute(action, params, context, progress):
    """The existing executors are capabilities, not separate user Job kinds."""
    from packages.core import background_jobs as jobs
    from packages.plugins.lifecycle import io
    kind = action['execution']['operation']
    validate(action)
    root = context['jobRoot']
    if kind == 'main-lifecycle':
        raise PermissionError('Main lifecycle requires its existing protected supervisor entry point')
    if kind == 'background-process':
        child = await io(jobs.start, params['sessionId'], params['argv'], params['cwd'],
                         hide_window=params.get('hideWindow', True), name=context['jobName'], registry_root=root)
        await progress(processJobId=child['jobId'])
        result = await wait_process(child, root)
    elif kind in ('session-message', 'session-broadcast'):
        checkpoint = getattr(progress, 'checkpoint', {})
        ids = checkpoint.get('targetIds', [params['sessionId']] if kind == 'session-message' else list(dict.fromkeys(params['sessionIds'])))
        await progress(targetIds=ids)
        results = copy.deepcopy(checkpoint.get('targetResults', []))
        for sid in ids:
            if any(row.get('sessionId') == sid for row in results):
                continue  # A known or uncertain receipt is never sent again.
            guard=getattr(progress,'before_effect',None)
            if guard:await guard()
            try:result = await jobs._worker.send_session(sid, params['text'], source='automation')
            except Exception as exc:result={'status':'unknown','error':str(exc)}
            results.append({'sessionId': sid, **result})
            await progress(targetResults=copy.deepcopy(results))
        unknown = any(r.get('status') in ('unknown', 'uncertain', 'unconfirmed') for r in results)
        return {'ok': not unknown and all(r.get('status') not in ('error', 'failed', 'cancelled') for r in results),
                'status': 'uncertain' if unknown else 'completed', 'targets': results,'matched':len(ids),
                'succeeded':sum(r.get('status') not in ('error','failed','cancelled','unknown','uncertain','unconfirmed') for r in results),
                'failed':sum(r.get('status') in ('error','failed','cancelled') for r in results)}
    else:
        legacy = {'jobId': context['jobId'], 'kind': 'scheduled-task', 'targetSessionId': params['sessionId'],
                  'text': params['text'], 'action': params['action'], 'name': context['jobName']}
        result = await jobs._execute_job_action(legacy, params['sessionId'], context['id'], registry_root=root, admitted_execution=context['id'])
        if result.get('processJobId') and result.get('status') == 'running':
            await progress(processJobId=result['processJobId'])
            result = await wait_process({'jobId': result['processJobId']}, root)
    status = result.get('status')
    if kind == 'background-process' and result.get('processTreeEnded') is True:
        # A signal is an exact JSON record from this owned execution log, never
        # inferred from success prose. Only a bounded tail is inspected.
        output = await io(jobs._tail_log_output, result.get('logPath'), 65536)
        for line in output.splitlines():
            try: value = json.loads(line)
            except (ValueError, TypeError): continue
            signal = value.get('panJobCompletion') if isinstance(value, dict) else None
            if isinstance(signal, dict) and set(signal) == {'type', 'completed'} and signal.get('completed') is True:
                result['panJobCompletion'] = signal
    return {**result, 'ok': status not in ('error', 'failed', 'cancelled', 'unknown', 'uncertain', 'unconfirmed')}
