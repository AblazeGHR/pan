"""Canonical typed executor port and non-executing historical audit helpers.

Legacy definitions, snapshots and settlement helpers remain for retained hosts.
R6-A removed the legacy dispatchers; protected lifecycle uses its own port.
"""
import copy
import hashlib
import json
import time
import uuid
import asyncio

_waiters = {}

KINDS = ('background-process', 'session-message', 'session-broadcast', 'scheduled-task', 'main-lifecycle')


async def execute_typed(*, attempt, effect_key, frozen_plan, retry=False, executor=None, **executor_options):
    """Execute only the caller's canonical Attempt; retain the host's receipts.

    Hosts should reuse an executor (or inject a durable receipt_store) across
    reconnects so unknown/partial receipts cannot be resent.
    """
    from packages.actions.typed_executor import TypedExecutor
    if executor is not None and executor_options:
        raise ValueError("executor and executor_options are mutually exclusive")
    operation = frozen_plan.get("operation") if isinstance(frozen_plan, dict) else None
    durable_host = (executor_options.get("independent_executor")
                    if operation in {"process", "background-process", "command"}
                    else executor_options.get("lifecycle_executor")
                    if operation in {"service.restart", "service.exit"} else None)
    if executor is None and executor_options.get("receipt_store") is None and durable_host is None:
        raise ValueError("a reusable executor or receipt host is required")
    return await (executor if executor is not None else TypedExecutor(**executor_options)).execute(
        attempt=attempt, effect_key=effect_key, frozen_plan=frozen_plan, retry=retry
    )


# Historical definitions remain readable until R6; their authority belongs to
# the Action registry and has no dependency on this retired execution module.
from packages.actions.store import system_definition as definition, validate_system_action as validate


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


def settle(job_id, status, receipt, registry_root):
    from packages.core import background_jobs as jobs
    with jobs._lock, jobs._job_lock(job_id, registry_root):
        current = jobs.get(job_id, registry_root)
        if current:
            finish(current, status, receipt)
            if status in ('unknown','uncertain'):
                current.update(status='uncertain',enabled=False,lastStatus='uncertain',lastError='Unconfirmed effect; automatic replay blocked')
            jobs._write_job(jobs._job_path(job_id, registry_root), current)


def recovery_ready(action, params):
    """Historical system_job definitions are audit-only; never re-admit them."""
    return action.get('execution', {}).get('kind') != 'system_job'


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


# Removed R6-A leaves are absent; stale Python callers receive an explicit refusal.
_RETIRED_ENTRYPOINTS = frozenset({
    'invoke',
    'execute',
})


def __getattr__(name):
    from packages.jobs.retired import missing
    return missing(name, _RETIRED_ENTRYPOINTS)
