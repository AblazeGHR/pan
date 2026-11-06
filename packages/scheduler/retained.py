"""Original-ID queries and explicit controls over existing canonical Jobs.

Reviewed provenance is an alias, never proof that a historical execution was
adopted. Old history stays separate from successor Runs and their real outbox.
This module owns no store, scheduler, lease or execution loop.
"""
import copy
import json

from packages.jobs import final_contracts as fc
from packages.jobs.final_runtime.ledger import aggregate


def resolve(service, db, task):
    # A committed JSON handoff supersedes evidence-only successor aliases.
    # Resolve its original owner/bytes/operation instead of registering a new
    # provenance row to make an active task appear settled.
    matches = db.execute("SELECT id FROM final_documents WHERE kind='json_scheduler_handoff' AND (id=? OR json_extract(document,'$.jobId')=?)", (task['id'], task['id'])).fetchall()
    if matches:
        from packages.scheduler.json_handoff import resolve as original
        _, binding = original(service, db, task['id'])
        return service._get(db, 'job', binding['jobId'])
    evidence = task['history']
    source = evidence['source']
    digest = evidence['sourceSha256'].removeprefix('sha256:')
    aliases = {value for value in (task['id'], source.get('taskId'), source.get('jobId'))
               if isinstance(value, str) and value}
    matches = []
    placeholders = ','.join('?' for _ in aliases)
    documents = db.execute("SELECT DISTINCT d.document FROM final_documents d, "
        "json_each(d.document, '$.runtimeMetadata.originals') p "
        "WHERE d.kind='job' AND p.key IN (" + placeholders + ")", tuple(aliases))
    for row in documents:
        job = json.loads(row[0])
        originals = job.get('runtimeMetadata', {}).get('originals', {})
        refs = [originals[key] for key in aliases if key in originals]
        if not refs:
            continue
        if any(ref.get('sourceDigest') != digest for ref in refs):
            raise fc.ContractError('reference_conflict', 'Reviewed original source digest changed')
        matches.append(job)
    if len(matches) != 1:
        raise fc.ContractError('reference_conflict', 'Original Scheduler has no unique reviewed canonical successor')
    # Unknown historical rows remain evidence-only, including aliases that a
    # caller registered separately. They cannot authorize settling old effects.
    if (source.get('kind') != 'scheduled-task' or source.get('enabled') is not False
            or source.get('status') not in {'completed', 'failed', 'cancelled', 'timed_out'}):
        raise fc.ContractError('reconciliation_required', 'Original Scheduler execution is unsettled')
    return matches[0]


def query(service, task, *, actor, limit=100, offset=0):
    service._permit(actor, 'job', 'read')
    with service.transaction(readonly=True) as db:
        job = resolve(service, db, task)
        state = aggregate(job, service.root)
        all_runs = sorted(state['runs'].values(), key=lambda run: (run['createdAt'], run['runId']), reverse=True)
        runs = [service._run_notification_projection(job, run) for run in all_runs[offset:offset + limit]]
        run_ids = {run['runId'] for run in runs}
        return {'jobId': job['jobId'], 'originalId': task['id'],
                'configRevision': job['configRevision'],
                'runs': copy.deepcopy(runs), 'total': len(all_runs),
                'notifications': [copy.deepcopy(row) for row in state['outbox'].values() if row['runId'] in run_ids],
                'effects': [copy.deepcopy(row) for row in state['effects'] if row['effectKey']['runId'] in run_ids],
                'cancellations': {key:copy.deepcopy(value) for key,value in state.get('cancellations', {}).items() if key in run_ids},
                'historicalExecutionAdopted': binding_exists(service, db, job['jobId'])}


def binding_exists(service, db, job_id):
    from packages.scheduler.json_handoff import binding_for_job
    binding = binding_for_job(service, db, job_id)
    return binding is not None and bool(binding['originalRunIds'])


async def cancel(service, task, body, *, actor):
    service._permit(actor, 'job', 'read')
    service._permit(actor, 'job', 'execute')
    fields = {'jobId', 'runId', 'expectedRevision', 'expectedStateRevision', 'requestKey'}
    if not isinstance(body, dict) or set(body) != fields:
        raise fc.ContractError('invalid_schema', 'Explicit original Run and revision envelope required')
    if any(not isinstance(body[name], str) or not 1 <= len(body[name]) <= 128
           for name in ('jobId', 'runId')):
        raise fc.ContractError('invalid_schema', 'Invalid Job or Run identity')
    fc.check_request_key(body['requestKey'])
    # Same FinalService receipt/owner transaction. Replay is resolved before
    # checking current state; a lost reply cannot create another cancellation.
    with service.transaction() as db:
        def mutate(operation_id):
            service._permit(actor, 'job', 'execute', execution=True)
            job = resolve(service, db, task)
            if job['jobId'] != body['jobId']:
                raise fc.ContractError('reference_conflict', 'Reviewed successor Job differs')
            fc.check_revision(job['configRevision'], body['expectedRevision'])
            owned, run = service._find_run(db, body['runId'])
            if owned['jobId'] != job['jobId']:
                raise fc.ContractError('reference_conflict', 'Run belongs to another Job')
            result = service.control_run(run['runId'], {
                'operation': 'cancel', 'expectedStateRevision': body['expectedStateRevision'],
                'requestKey': 'scheduler-run-cancel:' + operation_id}, actor=actor)
            return {'taskId': task['id'], 'jobId': job['jobId'], 'runId': run['runId'],
                    'canonicalOperationId': result['operationId']}, result['objectRefs'], True
        result = service._operation(db, actor, 'cancel', body['requestKey'],
            {'schedulerOriginalId': task['id'], **body}, 'job', mutate)
    if not result.get('replayed'):
        original = service.operation(result['result']['canonicalOperationId'], actor=actor)
        await service._cancel_postcommit(body['runId'], original, actor=actor)
    with service.transaction(readonly=True) as db:
        _, run = service._find_run(db, body['runId'])
        cancelled = run['state'] == 'cancelled'
    # The existing canonical dispatch/recovery pulse owns settlement/outbox.
    # Accepted means cancellation was requested, never proof it has completed.
    return {'ok': True, 'taskId': task['id'], 'status': result['status'],
            'cancelled': cancelled,
            'operation': result}
