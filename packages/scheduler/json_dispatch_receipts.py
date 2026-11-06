"""Original dispatch/log reconciliation against existing execution identities.

Read only. A journal is a locator, never proof of execution. This module cannot
allocate a Run/Attempt/effect, submit a provider call, or settle an unknown.
"""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

from packages.jobs import final_contracts as fc


def require(value, message):
    if not value:
        raise fc.ContractError('reconciliation_required', message)


def journal(service, source_file):
    root = service.owner.root
    path = root / Path(source_file).parent.parent / 'runs.jsonl'
    require(path.resolve().is_relative_to(root) and all(not part.is_symlink()
            and not (hasattr(part, 'is_junction') and part.is_junction())
            for part in (path, *path.parents) if part.is_relative_to(root)),
            'Original dispatch journal is linked or outside profile')
    require(path.is_file(), 'Original dispatch journal missing; outcome remains unknown')
    raw = path.read_bytes()
    require(len(raw) <= 64 * 1024 * 1024, 'Original dispatch journal needs bounded review')
    rows = [fc.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()]
    require(len(rows) <= 10000 and all(isinstance(row, dict) for row in rows), 'Malformed or oversized original dispatch journal')
    return raw, rows


def process_receipt(service, job, run, effect, row, *, active=False):
    from packages.jobs.independent_executor import effect_id, fence
    key = effect['effectKey']
    path = service.root / 'executions' / 'executions.sqlite3'
    require(path.is_file() and all(not part.is_symlink() and not (hasattr(part, 'is_junction') and part.is_junction())
            for part in (path, path.parent)), 'Original process execution receipt store missing or linked')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        raw = db.execute('SELECT document FROM executions WHERE effect_id=?', (effect_id(key),)).fetchone()
    require(raw is not None, 'Original process receipt missing; do not recreate execution')
    doc = fc.loads(raw[0])
    attempt = job['finalRuntime']['attempts'][effect['attemptId']]
    require(doc['jobId'] == job['jobId'] and doc['effectKey'] == key and doc['effectId'] == effect_id(key)
            and fence(doc['attempt']) == fence(attempt) and fc.digest(doc['frozenPlan']) == doc['planDigest'],
            'Original process receipt/Attempt/plan fence mismatch')
    frozen = job['finalRuntime']['plans'][run['runId']]['frozen']
    step = next(step for step in frozen['steps'] if step['stepId'] == key['stepId'])
    from packages.jobs.chains import resolve
    arguments = resolve(step['arguments'], frozen['input'], frozen.get('event', {}),
                        job['finalRuntime']['plans'][run['runId']].get('outputs', {}))
    require(doc['frozenPlan'].get('operation') == 'process' and doc['frozenPlan'].get('arguments') == arguments
            and (doc['frozenPlan'].get('completion') == step['completion'] or
                 doc['frozenPlan'].get('completion') is None and step['completion'] == {'contract': 'process-exit'}),
            'Original process receipt executable differs from the original frozen Step')
    require(row.get('result', {}).get('processJobId') == doc['effectId'], 'Original process locator differs from its durable receipt')
    receipt = doc.get('result')
    if active:
        from packages.core.platform.execution_host import supervisor_alive
        require(receipt is None and doc['phase'] == 'running' and supervisor_alive(doc.get('supervisorIdentity'))
                and supervisor_alive(doc.get('evidence', {}).get('processIdentity')),
                'Original active supervisor identity/phase is unproven; preserve resource')
        require(job['finalRuntime']['plans'][run['runId']].get('pendingProcessAttempt') == attempt['attemptId'],
                'Original Run has no returned pending process receipt')
        return {'kind': 'active-process', 'effectId': doc['effectId'], 'attemptId': attempt['attemptId'],
                'effectKey': key, 'supervisorIdentity': doc['supervisorIdentity'], 'planDigest': doc['planDigest'],
                'effectiveArguments': arguments}
    require(receipt is not None and receipt.get('effectKey') == key and receipt.get('outcome') == effect['outcome'],
            'Original process result is unknown or differs; no replay')
    from packages.core.platform.execution_host import supervisor_exit_evidence
    exit_proof = supervisor_exit_evidence(doc.get('supervisorIdentity'))
    require(service._cleanup_complete({**doc, 'supervisorExitProof': exit_proof}),
            'Original process cleanup/supervisor exit unproven; resource remains blocked')
    return {'kind': 'process', 'effectId': doc['effectId'], 'receiptDigest': fc.digest(receipt),
            'attemptId': attempt['attemptId'], 'effectKey': key, 'effectiveArguments': arguments}


def queue_receipt(service, job, run, effect, row):
    key = effect['effectKey']
    plan = job['finalRuntime']['plans'][run['runId']]
    reserved = plan.get('runtimeDelivery', {}).get(key['stepId'])
    require(reserved and reserved.get('effectKey') == key, 'Original queue reservation missing')
    children = reserved.get('children', [])
    require(len(children) == 1, 'Original queue cohort is incomplete or multi-target')
    child = children[0]
    require(child.get('status') == 'accepted' and child.get('effectKey') == key,
            'Original queue result remains unknown; never resend')
    from packages.jobs.session_delivery_runtime import query_original_child
    receipt = query_original_child(child)
    queue_id = receipt.get('receipt', {}).get('queueItemId')
    require(receipt['outcome'] == 'succeeded' and queue_id is not None
            and row.get('result', {}).get('queueItemId') == queue_id,
            'Original queue receipt is absent or journal locator mismatches')
    return {'kind': 'queue', 'queueItemId': queue_id, 'effectKey': key,
            'attemptId': effect['attemptId'], 'receiptDigest': fc.digest(receipt)}


def review(service, db, source, source_file):
    """Return facts only when every counted dispatch has the original receipt."""
    raw, rows = journal(service, source_file)
    rows = [row for row in rows if row.get('task_id', row.get('taskId')) == source['taskId']]
    require(rows, 'No original task dispatch rows; outcome remains unknown')
    # Multiple log rows for one dispatch must agree in all identity/result facts.
    by_key = {}
    for row in rows:
        key = row.get('dispatch_key', row.get('dispatchKey'))
        require(isinstance(key, str) and key, 'Original dispatch key missing')
        require(key not in by_key or by_key[key] == row, 'Conflicting original dispatch rows; preserve and reconcile')
        by_key[key] = row
    require(len(by_key) == source['runCount'], 'Retained journal does not cover the original counter; unknown history is not success')
    try:
        job = service._get(db, 'job', source['jobId'])
    except KeyError as exc:
        raise fc.ContractError('reconciliation_required',
            'Original dispatch has no existing canonical Job/Run; journal cannot create an execution identity') from exc
    state = job.get('finalRuntime')
    require(isinstance(state, dict), 'Original Job has no existing canonical runtime; never manufacture one from logs')
    matches = []
    old_ids = set()
    for dispatch, row in sorted(by_key.items()):
        old_id = row.get('run_id', row.get('runId'))
        require(isinstance(old_id, str) and old_id and old_id not in old_ids,
                'Original journal Run identity missing or reused across dispatches')
        old_ids.add(old_id)
        active = row.get('status') == 'running'
        require(row.get('status') in {'completed', 'dispatched', 'error', 'running'}, 'Original dispatch is active/unknown; query the same resource')
        candidates = [rid for rid, plan in state['plans'].items() if plan.get('dispatchKey') == dispatch]
        require(len(candidates) == 1, 'No unique original frozen Run for dispatch; no new Run may impersonate it')
        run = state['runs'][candidates[0]]
        require(run['state'] in ({'running'} if active else {'completed', 'failed', 'cancelled'}),
                'Original Run is active/uncertain; no automatic settlement')
        require(run['jobId'] == source['jobId'] and fc.digest(state['plans'][run['runId']]['frozen']) == run['planDigest'],
                'Original Job/Run frozen plan differs')
        effects = [effect for effect in state['effects'] if effect['effectKey']['runId'] == run['runId']]
        if active:
            require(not effects, 'Active journal disagrees with terminal effect; review original result')
            effects = [effect for effect in state['effectAdmissions'].values() if effect['effectKey']['runId'] == run['runId']]
            require(len(effects) == 1, 'Original pending process effect admission missing or multi-step')
        else:
            require(len(effects) == 1 and effects[0]['outcome'] in {'succeeded', 'rejected'}, 'Original effect receipt is missing, unknown or multi-step')
        effect = effects[0]
        step = state['plans'][run['runId']]['frozen']['steps'][0]
        frozen = state['plans'][run['runId']]['frozen']
        action = frozen.get('stepActions', {}).get(step['stepId']) or frozen['actions'][step['actionRef']['id']]
        operation = action['execution']['operation']
        proof = (process_receipt(service, job, run, effect, row, active=active) if operation == 'process'
                 else queue_receipt(service, job, run, effect, row) if not active and operation in {'session-message', 'assign'} else None)
        require(proof is not None, 'Original provider has no trusted receipt lookup; preserve unknown')
        require(active or (row['status'] in {'completed', 'dispatched'}) == (effect['outcome'] == 'succeeded'),
                'Original log status disagrees with actual effect receipt')
        matches.append({'originalLogRunId': row.get('run_id', row.get('runId')), 'dispatchKey': dispatch,
                        'runId': run['runId'], 'admissionId': run['admissionId'], **proof})
    return {'journalSha256': hashlib.sha256(raw).hexdigest(), 'runtimeDigest': fc.digest(state),
            'jobId': job['jobId'], 'matches': matches}
