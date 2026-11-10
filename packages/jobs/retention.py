"""Global Run-history retention on the existing canonical pulse and ledger.

Definitions and execution identities never expire. The small expiredRun records
are replay fences, not another execution or receipt ledger.
"""
import copy
from datetime import datetime, timezone
import json
import math
import os
import re
import time

from packages.core import config
from . import final_contracts as fc
from .final_runtime.ledger import aggregate
from .final_runtime.coordinator import effective_pin

RULES = tuple(config.JOB_RETENTION_CONFIG_KEYS)
DAY = 86400


def fail(code, message):
    raise fc.ContractError(code, message)


def user(actor):
    # A single-Job delegated permission never grants global deletion policy.
    if actor.get('type') != 'user':
        fail('authorization_denied', 'Global retention requires local User authority')


def settings(service, *, actor):
    user(actor)
    with service.transaction(readonly=True) as db:
        return {**_settings(service, db), 'requestScope': request_scope(service, actor)}


def request_scope(service, actor):
    return fc.digest(service._scope(actor, 'update'))


def _settings(service, db):
    try:
        return copy.deepcopy(service._get(db, 'retention_settings', 'global'))
    except KeyError:
        rules, validity = config.job_retention_settings()
        return {'revision': 0, 'rules': rules, 'configValidity': validity,
                'cleanupScope': 'run_history', 'lastRuns': {k: None for k in RULES}}


def update(service, value, *, actor):
    user(actor)
    if not isinstance(value, dict) or set(value) != {'rules', 'expectedRevision', 'expectedRequestScope', 'requestKey'}:
        fail('invalid_schema', 'rules, expectedRevision, expectedRequestScope and requestKey are required')
    scope = value['expectedRequestScope']
    if not isinstance(scope, str) or re.fullmatch('[0-9a-f]{64}', scope) is None:
        fail('invalid_schema', 'expectedRequestScope must be a 64-character lowercase hex string')
    if scope != request_scope(service, actor):
        fail('request_scope_conflict', 'Authenticated request scope changed; retain the original unknown request')
    if type(value['expectedRevision']) is not int or value['expectedRevision'] < 0:
        fail('invalid_schema', 'Invalid settings revision')
    patches = value['rules']
    if not isinstance(patches, dict) or not patches or set(patches) - set(RULES):
        fail('invalid_schema', 'Provide known retention rules')
    for rule, patch in patches.items():
        if not isinstance(patch, dict) or not patch or set(patch) - {'enabled', 'days'}:
            fail('invalid_schema', 'A rule patch requires enabled and/or days')
        _, valid = config.parse_retention_settings(patch)
        if not valid:
            fail('invalid_schema', 'Invalid retention enabled/days')
    with service.transaction() as db:
        def mutate(_):
            current = _settings(service, db)
            fc.check_revision(current['revision'], value['expectedRevision'])
            for rule, patch in patches.items():
                current['rules'][rule].update(patch)
                current['configValidity'][rule] = True
            current['revision'] += 1
            service._save(db, 'retention_settings', 'global', current)
            return {'settings': {**current, 'requestScope': request_scope(service, actor)}}, [], False
        return service._operation(db, actor, 'update', value['requestKey'],
                                  {'globalRetention': value}, 'job', mutate)


def stamp(value):
    if type(value) in (int, float):
        point = float(value)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Retention requires an original timezone')
        point = parsed.astimezone(timezone.utc).timestamp()
    else:
        raise ValueError('Retention requires an original timestamp')
    if not math.isfinite(point):
        raise ValueError('Retention requires a finite timestamp')
    return point


def run_rule(state, run):
    status = run['state']
    if status == 'completed':
        return 'completed'
    if status == 'cancelled':
        return 'cancelled'
    if status != 'failed':
        return None
    attempts = {a['attemptId']: a for a in state['attempts'].values() if a['runId'] == run['runId']}
    # Only a receipt from an original Attempt may classify a failed Run as a
    # timeout; text messages, Job projections and configured deadlines cannot.
    for effect in state['effects']:
        if effect['attemptId'] not in attempts:
            continue
        receipt = effect.get('receipt', {})
        payload = receipt.get('receipt', {})
        if (effect.get('outcome') != receipt.get('outcome')
                or effect.get('effectKey', {}).get('runId') != run['runId']
                or receipt.get('effectKey') != effect['effectKey']):
            continue
        if (receipt.get('outcome') == 'rejected' and payload.get('code') == 'timeout'
                and receipt.get('cleanupProof', {}).get('confirmed') is True):
            return 'timed_out'
        if receipt.get('outcome') == 'rejected' and any(
                c.get('code') == 'timeout' and c.get('cleanupProof', {}).get('confirmed') is True
                for c in payload.get('commands', [])):
            return 'timed_out'
    return 'failed'


def protected(job, state, rid):
    run = state['runs'].get(rid) or state.get('expiredRuns', {}).get(rid, {}).get('run')
    if (not run or run.get('jobId') != job['jobId']
            or run.get('profileId') != job.get('canonical', {}).get('profileId')):
        return True
    try:
        if effective_pin(job):
            return True
    except (ValueError, TypeError):
        return True
    if job.get('cleanupPending') or job.get('unknownOwnership') or job.get('activeExecutions'):
        return True
    attempts = {a['attemptId']: a for a in state['attempts'].values() if a['runId'] == rid}
    if any(a['state'] not in {'succeeded', 'failed', 'cancelled'} for a in attempts.values()):
        return True
    if any(r['state'] not in {'completed', 'failed', 'cancelled'}
           for r in state['runs'].values()):
        return True
    if any(r.get('rerunOf') == rid for r in [*state['runs'].values(),
           *(t['run'] for t in state.get('expiredRuns', {}).values())] if r['runId'] != rid):
        return True
    if any(e['attemptId'] in attempts for e in state['effectAdmissions'].values()):
        return True
    if any(e['attemptId'] in attempts and e['outcome'] in {'unknown', 'partial'} for e in state['effects']):
        return True
    if any(e['attemptId'] in attempts and (e['effectKey'].get('runId') != rid
           or e.get('receipt', {}).get('effectKey') != e['effectKey']
           or e.get('receipt', {}).get('outcome') != e['outcome']) for e in state['effects']):
        return True
    if any(e['attemptId'] in attempts and 'cleanupProof' in e.get('receipt', {})
           and e['receipt']['cleanupProof'].get('confirmed') is not True for e in state['effects']):
        return True
    if any(row['runId'] == rid and row['status'] not in {'acked', 'suppressed'} for row in state['outbox'].values()):
        return True
    for source in (state.get('cancellations', {}), job.get('runtimeMetadata', {}).get('cancellations', {})):
        if any(row.get('status') != 'confirmed' for row in source.values()):
            return True
    plan = state['plans'].get(rid, {})
    if any(plan.get(k) for k in ('autoRecovery', 'pendingProcessAttempt', 'pendingSessionAttempt', 'runtimeHold')):
        return True
    return False


def log_records(state, rid):
    return [{'attempt': copy.deepcopy(a), 'rows': copy.deepcopy([
        e for e in state['effects'] if e['attemptId'] == a['attemptId']]),
        **({'logExpired': copy.deepcopy(state['expiredLogs'][a['attemptId']])}
           if a['attemptId'] in state.get('expiredLogs', {}) else {})}
        for a in state['attempts'].values() if a['runId'] == rid]


def minimal_receipt(receipt):
    """Retain disposition/proof, discard output and user/provider payloads."""
    return {k: copy.deepcopy(v) for k, v in receipt.items()
            if k in {'effectKey', 'outcome', 'cleanupProof', 'code', 'logRef'}}


def expire_history(service, job, state, rid, point):
    run = state['runs'][rid]
    records = log_records(state, rid)
    for record in records:
        for effect in record['rows']:
            receipt = effect.get('receipt', {})
            payload = receipt.get('receipt', {})
            effect['receipt'] = minimal_receipt(receipt)
            effect['receipt']['receipt'] = minimal_receipt(payload)
    # These facts are deliberately independent of the retained definition.
    # Keeping them in the aggregate preserves event admission and runLimit.
    state.setdefault('expiredRuns', {})[rid] = {
        'run': copy.deepcopy(run), 'expiredAt': point, 'historyDigest': history_digest(state, rid), 'logRecords': records,
        'originalIds': [identity for row in state['outbox'].values() if row['runId'] == rid
                        for identity in (row['outboxId'], row.get('legacyId')) if identity]}
    cancellation = state.get('cancellations', {}).pop(rid, None)
    if cancellation:
        state['expiredRuns'][rid]['cancellation'] = {k: copy.deepcopy(v) for k, v in cancellation.items()
            if k in {'operationId', 'revision', 'status', 'targets'}}
    state['runs'].pop(rid)
    state['plans'].pop(rid, None)
    state['steps'].pop(rid, None)
    aids = {a['attempt']['attemptId'] for a in records}
    state['attempts'] = {k: v for k, v in state['attempts'].items() if k not in aids}
    state['effects'] = [e for e in state['effects'] if e['attemptId'] not in aids]
    reconciliations = state.get('receiptReconciliations', {})
    for aid in aids:
        reconciliations.pop(aid, None)
        state.get('expiredLogs', {}).pop(aid, None)
    state['outbox'] = {k: v for k, v in state['outbox'].items() if v['runId'] != rid}
    for key in ('lastExecution',):
        if job.get(key, {}).get('runId') == rid:
            job[key] = {'runId': rid, 'status': run['state'], 'historyExpired': True}
    # Existing operation rows retain request fingerprints, principal/scope,
    # result identities and original status. No old request can create a Run.


def expired(state, rid):
    return state.get('expiredRuns', {}).get(rid)


def history_digest(state, rid):
    return fc.digest({'run': state['runs'][rid], 'plan': state['plans'].get(rid),
                      'steps': state['steps'].get(rid), 'records': log_records(state, rid)})


def canonical_identity(service, job):
    item = job.get('canonical', {})
    return (isinstance(item, dict) and item.get('schemaVersion') == 2
            and item.get('id') == job.get('jobId') and item.get('profileId') == service.profile_id
            and job.get('finalRuntime', {}).get('profileId', service.profile_id) == service.profile_id)


def scan(service, *, at=None):
    """One bounded daily pass on the existing pulse, serialized with settings.

    The owner transaction covers eligibility and reduction. Each per-rule gate
    is committed with its effects, so cold hosts do not repeat successful work.
    """
    point = time.time() if at is None else stamp(at)
    recover_history_intents(service)
    results = {}
    with service.transaction() as db:
        current = _settings(service, db)
        for rule in RULES:
            policy = current['rules'][rule]
            if not current['configValidity'][rule] or not policy['enabled'] or policy['days'] is None:
                continue
            previous = current['lastRuns'][rule]
            if previous and point - previous['at'] < DAY:
                continue
            result = {'at': point, 'expired': 0, 'protected': 0, 'errors': []}
            cutoff = point - policy['days'] * DAY
            for kind in ('job', 'runtime_archive'):
                for job in service._all(db, kind):
                    if not canonical_identity(service, job):
                        result['protected'] += 1
                        continue
                    state = aggregate(job, service.root)
                    before = copy.deepcopy(job)
                    for rid, run in list(state['runs'].items()):
                        if rule == 'logs' or run_rule(state, run) != rule:
                            continue
                        try:
                            if stamp(run.get('finishedAt')) > cutoff:
                                continue
                            if protected(job, state, rid) or not external_safe(service, job, state, rid):
                                result['protected'] += 1
                                continue
                            if not reserve_history(service, job, state, rid):
                                result['protected'] += 1
                                continue
                            expire_history(service, job, state, rid, point)
                            result['expired'] += 1
                        except (ValueError, TypeError, KeyError, OverflowError):
                            result['protected'] += 1
                    if rule == 'logs':
                        _logs(service, db, job, state, cutoff, point, result)
                    if job != before:
                        service._save(db, kind, job['jobId'], job)
            current['lastRuns'][rule] = result
            results[rule] = result
        if results:
            service._save(db, 'retention_settings', 'global', current)
    return results


def external_safe(service, job, state, rid):
    from .independent_executor import ExecutionStore, effect_id
    if not (service.root / 'executions' / 'executions.sqlite3').exists():
        return True
    store = ExecutionStore(service.root / 'executions')
    for record in log_records(state, rid):
        for row in record['rows']:
            try:
                doc = store.get(effect_id(row['effectKey']))
            except KeyError:
                continue
            from .independent_executor import fence
            if doc.get('jobId') == job['jobId']:
                acknowledge_original(store, doc, record['attempt'], row)
            if (doc['jobId'] != job['jobId'] or doc['effectKey'] != row['effectKey']
                    or fence(doc['attempt']) != fence(record['attempt'])
                    or not settled_document(service, doc) or pending_notice(store, doc)):
                return False
            # A previous generation is still a live audit/log holder. Until
            # its own explicit reduction is supported, retain the whole Run.
            if doc.get('history'):
                return False
    return True


def pending_notice(store, doc):
    with store.connect() as db:
        return any(json.loads(row[0]).get('effectId') == doc['effectId']
                   for row in db.execute('SELECT payload FROM execution_outbox WHERE acked=0'))


def acknowledge_original(store, doc, attempt, effect):
    """Reuse the original ack only for a receipt already durably canonical.

    A host can settle by original lookup before consuming its notification.
    An equal, fenced terminal receipt is the same admission, not a new effect.
    Changed/stale/unknown notices remain unacknowledged and block cleanup.
    """
    from .independent_executor import fence
    receipt = effect.get('receipt')
    if (receipt != doc.get('result') or not isinstance(receipt, dict)
            or receipt.get('outcome') not in {'succeeded', 'rejected'}
            or receipt.get('effectKey') != doc['effectKey']
            or fence(doc['attempt']) != fence(attempt)):
        return
    for event in store.events()['events']:
        if (event.get('effectId') == doc['effectId'] and event.get('jobId') == doc['jobId']
                and fence(event.get('attempt', {})) == fence(attempt) and event.get('result') == receipt):
            store.ack(event['eventId'])


def settled_document(service, doc):
    # Same identity verifier used by lookup_execution; no cleanup/cancel call.
    from packages.core.platform.execution_host import supervisor_exit_evidence
    doc['supervisorExitProof'] = supervisor_exit_evidence(doc.get('supervisorIdentity', {}))
    return service._cleanup_complete(doc)


def reserve_history(service, job, state, rid):
    """Fence new independent Attempts before committing canonical expiry.

    The intent lives on each ORIGINAL execution document, like logPurge. The
    external writer transaction rechecks every identity after eligibility; a
    concurrent admitted Attempt causes the entire reservation to roll back.
    """
    from .independent_executor import ExecutionStore, effect_id, fence
    if not (service.root / 'executions' / 'executions.sqlite3').exists():
        return True
    store = ExecutionStore(service.root / 'executions')
    intent = {'runId': rid, 'historyDigest': history_digest(state, rid)}
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for record in log_records(state, rid):
            for effect in record['rows']:
                try:
                    doc = store._load(db, effect_id(effect['effectKey']))
                except KeyError:
                    continue
                if (doc['jobId'] != job['jobId'] or doc['effectKey'] != effect['effectKey']
                        or fence(doc['attempt']) != fence(record['attempt'])
                        or doc.get('history') or doc.get('historyExpired')
                        or doc.get('historyPurge', intent) != intent
                        or doc.get('result') != effect.get('receipt')
                        or not settled_document(service, doc) or pending_notice(store, doc)):
                    db.rollback()
                    return False
                doc['historyPurge'] = intent
                store._save(db, doc)
        db.commit()
    return True


def recover_history_intents(service):
    """Release an aborted intent only against its still-live original facts.

    A committed tombstone keeps the fence until payload reduction completes.
    Unknown/missing/changed identities keep the intent and cannot be replayed.
    This also handles host exit between external reservation and canonical
    commit, without another ledger or a compensating execution.
    """
    from .independent_executor import ExecutionStore, fence
    if not (service.root / 'executions' / 'executions.sqlite3').exists():
        return
    store = ExecutionStore(service.root / 'executions')
    with service.transaction() as canonical:
        jobs = {j['jobId']: j for kind in ('runtime_archive', 'job') for j in service._all(canonical, kind)}
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            docs = [json.loads(row[0]) for row in db.execute('SELECT document FROM executions')]
            for doc in docs:
                intent = doc.get('historyPurge')
                if not intent or doc.get('historyExpired'):
                    continue
                job = jobs.get(doc['jobId'])
                if job is None or not canonical_identity(service, job):
                    continue
                state = aggregate(job, service.root)
                rid = intent['runId']
                if rid not in state['runs'] or history_digest(state, rid) != intent['historyDigest']:
                    continue
                records = log_records(state, rid)
                if any(fence(record['attempt']) == fence(doc['attempt'])
                       and any(row['effectKey'] == doc['effectKey'] and row.get('receipt') == doc.get('result')
                               for row in record['rows']) for record in records):
                    doc.pop('historyPurge')
                    store._save(db, doc)
            db.commit()


def reduce_executions(service):
    """Resume payload reduction after the canonical expiry commit, even cold.

    Same execution rows keep planDigest, Attempt/lease and effect receipts;
    matching admission still returns the original row and never spawns again.
    """
    from .independent_executor import ExecutionStore, effect_id, fence
    if not (service.root / 'executions' / 'executions.sqlite3').exists():
        return
    with service.transaction() as db:
        store = ExecutionStore(service.root / 'executions')
        for kind in ('job', 'runtime_archive'):
            for job in service._all(db, kind):
                if not canonical_identity(service, job):
                    continue
                for rid, tomb in job.get('finalRuntime', {}).get('expiredRuns', {}).items():
                    for record in tomb['logRecords']:
                        for effect in record['rows']:
                            try:
                                with store.connect() as external:
                                    external.execute('BEGIN IMMEDIATE')
                                    doc = store._load(external, effect_id(effect['effectKey']))
                                    if (doc.get('historyExpired') or not settled_document(service, doc)
                                            or doc.get('jobId') != job['jobId'] or doc.get('effectKey') != effect['effectKey']
                                            or fence(doc['attempt']) != fence(record['attempt'])
                                            or doc.get('historyPurge') != {'runId': rid, 'historyDigest': tomb['historyDigest']}
                                            or doc.get('history') or pending_notice(store, doc)):
                                        continue
                                    doc['historyExpired'] = {'runId': rid, 'at': tomb['expiredAt'], 'historyDigest': tomb['historyDigest']}
                                    doc.pop('historyPurge', None)
                                    doc.pop('frozenPlan', None)
                                    result = doc.get('result', {})
                                    payload = result.get('receipt', {})
                                    doc['result'] = minimal_receipt(result)
                                    doc['result']['receipt'] = minimal_receipt(payload)
                                    store._save(external, doc)
                                    # Acknowledged notices are payload copies;
                                    # identity/ack rows remain for deduplication.
                                    for event_id, raw in external.execute('SELECT event_id,payload FROM execution_outbox WHERE acked=1').fetchall():
                                        notice = json.loads(raw)
                                        if notice.get('effectId') == doc['effectId']:
                                            external.execute('UPDATE execution_outbox SET payload=? WHERE event_id=?',
                                                (fc.canonical_json({'effectId': doc['effectId'], 'historyExpired': True}), event_id))
                                    external.commit()
                            except KeyError:
                                continue


def pulse(service):
    # Existing pulse only; no new task, scheduler, host or receipt store.
    point = time.monotonic()
    if point < getattr(service, '_retention_next_check', 0):
        return
    scan(service)
    reduce_executions(service)
    service._retention_next_check = point + 60


def install(app):
    from fastapi import APIRouter, Request
    from .final_api import ContractRoute, actor, body
    from packages.plugins.lifecycle import io
    router = APIRouter(prefix='/api/job-maintenance', route_class=ContractRoute)

    @router.get('/retention-settings')
    async def read(request: Request):
        who = await actor(request)
        user(who)
        service = getattr(request.app.state, 'job_final_service', None)
        if service is None:
            fail('provider_unavailable', 'Canonical retention service unavailable')
        return await io(settings, service, actor=who)

    @router.put('/retention-settings')
    async def write(request: Request):
        who = await actor(request)
        user(who)
        service = getattr(request.app.state, 'job_final_service', None)
        if service is None:
            fail('provider_unavailable', 'Canonical retention service unavailable')
        result = await io(update, service, await body(request), actor=who)
        service._retention_next_check = 0
        return result

    app.include_router(router)


def _logs(service, db, job, state, cutoff, point, result):
    from .independent_executor import ExecutionStore, effect_id, fence
    from .process_logs import purge_log, reference, open_log
    path = service.root / 'executions' / 'executions.sqlite3'
    if not path.is_file():
        return
    store = ExecutionStore(service.root / 'executions')
    runs = [(rid, run, log_records(state, rid)) for rid, run in state['runs'].items()]
    runs += [(rid, tomb['run'], tomb['logRecords']) for rid, tomb in state.get('expiredRuns', {}).items()]
    for rid, run, records in runs:
        try:
            if run['state'] not in {'completed', 'failed', 'cancelled'} or stamp(run.get('finishedAt')) > cutoff or protected(job, state, rid):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        for record in records:
            for effect in record['rows']:
                identity = effect_id(effect['effectKey'])
                try:
                    original = store.get(identity)
                    if original.get('jobId') == job['jobId']:
                        acknowledge_original(store, original, record['attempt'], effect)
                    with store.connect() as external:
                        external.execute('BEGIN IMMEDIATE')
                        doc = store._load(external, identity)
                        if (doc['jobId'] != job['jobId'] or doc['effectKey'] != effect['effectKey']
                                or fence(doc['attempt']) != fence(record['attempt'])
                                or doc.get('logRef') != reference(doc) or not settled_document(service, doc)
                                or doc.get('history') or pending_notice(store, doc)):
                            result['protected'] += 1
                            continue
                        ref = doc['logRef']
                        if doc.get('logExpired'):
                            mark_log_expired(state, record['attempt']['attemptId'], doc['logExpired'])
                            continue
                        if _other_log_holder(service, db, ref, job['jobId'], rid, record['attempt']['attemptId']):
                            result['protected'] += 1
                            continue
                        if not doc.get('logPurge'):
                            with open_log(store.root, doc) as stream:
                                if os.fstat(stream.fileno()).st_mtime > cutoff:
                                    result['protected'] += 1
                                    continue
                        # Commit intent in the ORIGINAL execution document
                        # before unlink. Interrupted passes resume this same ref.
                        doc.setdefault('logPurge', {'at': point, 'logRef': ref})
                        store._save(external, doc)
                        external.commit()
                    purge_log(store.root, doc, cutoff=cutoff)
                    with store.connect() as external:
                        external.execute('BEGIN IMMEDIATE')
                        latest = store._load(external, identity)
                        if latest.get('logPurge') != doc['logPurge'] or fence(latest['attempt']) != fence(doc['attempt']):
                            fail('reference_conflict', 'Log purge identity changed')
                        latest['logExpired'] = {'at': point, 'logRef': ref}
                        store._save(external, latest)
                        external.commit()
                    mark_log_expired(state, record['attempt']['attemptId'], latest['logExpired'])
                    result['expired'] += 1
                except KeyError:
                    continue  # Non-process providers have no execution/log.
                except (OSError, ValueError) as exc:
                    result['errors'].append({'runId': rid, 'attemptId': record['attempt']['attemptId'], 'code': type(exc).__name__})


def mark_log_expired(state, aid, marker):
    # Keep generated Effect records schema-valid for later normal controls.
    if aid in state['attempts']:
        state.setdefault('expiredLogs', {})[aid] = copy.deepcopy(marker)
    for tomb in state.get('expiredRuns', {}).values():
        for record in tomb['logRecords']:
            if record['attempt']['attemptId'] == aid:
                record['logExpired'] = copy.deepcopy(marker)


def _other_log_holder(service, db, ref, job_id, rid, aid):
    for kind in ('job', 'runtime_archive'):
        for job in service._all(db, kind):
            state = aggregate(job, service.root)
            records = [r for run in state['runs'] for r in log_records(state, run)]
            records += [r for tomb in state.get('expiredRuns', {}).values() for r in tomb['logRecords']]
            for record in records:
                if (job['jobId'], record['attempt']['runId'], record['attempt']['attemptId']) == (job_id, rid, aid):
                    continue
                if any(e.get('receipt', {}).get('receipt', {}).get('logRef') == ref for e in record['rows']):
                    return True
    return False
