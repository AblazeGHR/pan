"""Offline handoff of original Trigger admission receipts, never execution.

Only an already admitted, same-Event/Trigger/Job/Run receipt can transfer. The
Run, frozen plan, Attempt, effect, cancellation and outbox remain in FinalService
verbatim. Missing original runtime records are a blocker, not a new admission.
"""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import object_ref
from packages.jobs.runtime_owner import OwnerFence, cooperative_profile


def require(value, message):
    if not value:
        raise fc.ContractError('reconciliation_required', message)


def source(root):
    root = Path(root).resolve()
    require(cooperative_profile(root), 'Unknown original writer; preserve Trigger ledger')
    path = root / 'automation' / 'registry.json'
    require(path.is_file() and not path.is_symlink(), 'Original Trigger ledger missing or linked')
    raw = path.read_bytes()
    state = fc.loads(raw.decode('utf-8-sig'))
    require(isinstance(state, dict) and type(state.get('revision')) is int
            and isinstance(state.get('runs'), dict), 'Malformed original Trigger ledger; preserve')
    return root, raw, state


def epoch(root):
    with sqlite3.connect((root / 'job-runtime-owner.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT epoch,state FROM owner WHERE singleton=1').fetchone()
        intent = db.execute("SELECT value FROM metadata WHERE key='trigger-receipt-handoff'").fetchone()
    return (row[0] if row else 0), (intent[0] if intent else None)


class TriggerReceiptHandoff:
    def __init__(self, service, source_root):
        self.service = service
        self.root = Path(source_root).resolve()

    def _entry(self, db, record):
        require(isinstance(record, dict) and type(record.get('revision')) is int and record['revision'] > 0
                and type(record.get('triggerRevision')) is int and record['triggerRevision'] > 0,
                'Original Trigger revisions are incomplete; preserve')
        require(record.get('recordType') == 'triggerAdmission',
                'Original effectful Trigger Run needs its frozen execution adapter; preserve')
        receipt = record.get('result')
        require(isinstance(receipt, dict) and receipt.get('runId') and receipt.get('admissionId'),
                'Missing original Job Run/admission receipt; never re-admit')
        job, run = self.service._find_run(db, receipt['runId'])
        state = job.get('finalRuntime', {})
        adm = state.get('admissions', {}).get(receipt['admissionId'])
        expected_cause = {'type': 'event', 'eventId': record['event']['id'], 'triggerId': record['triggerId']}
        require(adm is not None and run['admissionId'] == receipt['admissionId']
                and adm['jobId'] == job['jobId'] == record.get('targetJobId')
                and run['cause'] == adm['cause'] == expected_cause,
                'Original Event/Trigger/Job/Run/admission identity mismatch')
        require(run['runId'] in state.get('plans', {}) and run['runId'] in state.get('steps', {}),
                'Original frozen plan/steps missing')
        require(fc.digest(state['plans'][run['runId']]['frozen']) == run['planDigest'],
                'Original frozen plan digest mismatch')
        # Hash the entire runtime, including unknown effect/cancel/notice facts.
        # Apply refuses drift rather than reconciling a selected subset.
        return {'originalId': record['id'], 'revision': record['revision'],
                'triggerId': record['triggerId'], 'triggerRevision': record['triggerRevision'],
                'jobId': job['jobId'], 'configRevision': job['configRevision'],
                'runId': run['runId'], 'admissionId': receipt['admissionId'],
                'runStateRevision': run['stateRevision'],
                'runtimeDigest': fc.digest(state)}

    def preview(self, original_ids, *, actor):
        self.service._permit(actor, 'job', 'read')
        require(actor.get('type') == 'user', 'Explicit original owner review required')
        root, raw, state = source(self.root)
        require(isinstance(original_ids, list) and original_ids and len(set(original_ids)) == len(original_ids),
                'Explicit unique original Trigger admission IDs required')
        entries, blocked = [], []
        with self.service.transaction(readonly=True) as db:
            for rid in original_ids:
                try:
                    entries.append(self._entry(db, state['runs'][rid]))
                except (KeyError, fc.ContractError) as exc:
                    blocked.append({'originalId': rid, 'reason': str(exc), 'preserve': True})
        return {'protocol': 'trigger-receipt-handoff/v1', 'sourceRoot': str(root),
                'sourceDigest': hashlib.sha256(raw).hexdigest(), 'revision': state['revision'],
                'expectedEpoch': epoch(root)[0], 'entries': entries, 'blocked': blocked}

    def apply(self, plan, *, request_key, actor):
        fc.check_request_key(request_key)
        self.service._permit(actor, 'job', 'read')
        self.service._permit(actor, 'job', 'lifecycle', execution=True)
        require(actor.get('type') == 'user', 'Explicit original owner review required')
        require(plan.get('sourceRoot') == str(self.root) and plan.get('protocol') == 'trigger-receipt-handoff/v1'
                and plan.get('entries') and not plan.get('blocked'), 'Review all blockers before handoff')
        require(self.service.owner.token is not None, 'Active canonical owner required for handoff')
        with self.service.owner.writer(canonical=True):
            pass  # Reject a stale destination owner before freezing the source.
        root, raw, state = source(self.root)
        require(hashlib.sha256(raw).hexdigest() == plan['sourceDigest'] and state['revision'] == plan['revision'],
                'Original Trigger ledger changed; preserve and review again')
        digest = fc.digest(plan)
        current, intent = epoch(root)
        require(current == plan['expectedEpoch'] or intent == digest,
                'Original owner generation differs without this handoff intent')
        joined_owner = root == self.service.owner.root
        owner = self.service.owner if joined_owner else OwnerFence(root)
        if not joined_owner:
            owner.claim(current)  # Rejects live/abandoned legacy leases atomically.
        try:
            with owner.transaction() as owner_db:
                owner._check(owner_db, owner.token)
                prior = owner_db.execute("SELECT value FROM metadata WHERE key='trigger-receipt-handoff'").fetchone()
                require(prior is None or prior[0] == digest, 'Original Trigger handoff intent differs')
                owner_db.execute("INSERT OR IGNORE INTO metadata VALUES('trigger-receipt-handoff',?)", (digest,))
            # Source is durably frozen before canonical commit. If this process
            # fails, it stays frozen; only this reviewed intent may resume.
            require((root / 'automation' / 'registry.json').read_bytes() == raw,
                    'Original Trigger bytes changed before handoff')
            with self.service.transaction() as db:
                def transfer(operation_id):
                    self.service._permit(actor, 'job', 'lifecycle', execution=True)
                    for entry in plan['entries']:
                        require(self._entry(db, state['runs'][entry['originalId']]) == entry,
                                'Original runtime changed since preview')
                        key = fc.digest([str(root), entry['originalId']])
                        doc = {**entry, 'sourceRoot': str(root), 'sourceDigest': plan['sourceDigest'],
                               'planDigest': digest, 'operationId': operation_id,
                               'controlRevision': entry['revision']}
                        existing = db.execute("SELECT document FROM final_documents WHERE kind='trigger_receipt_handoff' AND id=?", (key,)).fetchone()
                        require(existing is None, 'Original Trigger receipt already handed off; query its operation')
                        self.service._save(db, 'trigger_receipt_handoff', key, doc)
                    return {'planDigest': digest, 'originalIds': [e['originalId'] for e in plan['entries']]}, [
                        object_ref('job', self.service._get(db, 'job', jid)['canonical'])
                        for jid in sorted({e['jobId'] for e in plan['entries']})], False
                return self.service._operation(db, actor, 'update', request_key,
                    {'triggerReceiptHandoff': plan}, 'job', transfer)
        finally:
            if not joined_owner:
                owner.release()  # closed still fences every legacy writer.

    def query(self, original_id, *, actor):
        self.service._permit(actor, 'job', 'read')
        root, raw, _ = source(self.root)
        key = fc.digest([str(root), original_id])
        with self.service.transaction(readonly=True) as db:
            binding = self.service._get(db, 'trigger_receipt_handoff', key)
            require(hashlib.sha256(raw).hexdigest() == binding['sourceDigest'], 'Original ledger bytes changed')
            _, intent = epoch(root)
            require(intent == binding['planDigest'], 'Original owner handoff intent changed')
            job, run = self.service._find_run(db, binding['runId'])
            require(job['jobId'] == binding['jobId'] and run['admissionId'] == binding['admissionId'],
                    'Original runtime identity changed')
            state = job['finalRuntime']
            return {'handoff': binding, 'run': copy.deepcopy(run),
                    'attempts': [copy.deepcopy(a) for a in state['attempts'].values() if a['runId'] == run['runId']],
                    'effects': [copy.deepcopy(e) for e in state['effects'] if e['effectKey']['runId'] == run['runId']],
                    'notifications': [copy.deepcopy(n) for n in state['outbox'].values() if n['runId'] == run['runId']],
                    'cancellation': copy.deepcopy(state.get('cancellations', {}).get(run['runId']))}

    async def cancel(self, original_id, expected_revision, *, actor):
        """Old expectedRevision -> original canonical Run CAS/receipt.

        The source admission remains admitted; accepted cancellation is not a
        claim that its Job effect has stopped. Unknown effects are not retried.
        """
        self.service._permit(actor, 'job', 'lifecycle')
        root, raw, state = source(self.root)
        binding_key = fc.digest([str(root), original_id])
        with self.service.transaction() as db:
            binding = self.service._get(db, 'trigger_receipt_handoff', binding_key)
            require(hashlib.sha256(raw).hexdigest() == binding['sourceDigest'], 'Original ledger bytes changed')
            require(epoch(root)[1] == binding['planDigest'], 'Original owner handoff intent changed')
            key = 'trigger-cancel:' + fc.digest([binding['operationId'], original_id, expected_revision])
            def request_cancel(operation_id):
                self.service._permit(actor, 'job', 'lifecycle', execution=True)
                job, run = self.service._find_run(db, binding['runId'])
                require(job['jobId'] == binding['jobId'] and run['admissionId'] == binding['admissionId'],
                        'Original Job Run identity changed')
                fc.check_revision(binding['controlRevision'], expected_revision)
                original = self.service.cancel_legacy_run(run['runId'], {
                    'expectedStateRevision': run['stateRevision'], 'requestKey': operation_id}, actor=actor)
                binding['controlRevision'] += 1
                self.service._save(db, 'trigger_receipt_handoff', binding_key, binding)
                return {'runId': run['runId'], 'canonicalOperationId': original['operationId']}, original['objectRefs'], True
            receipt = self.service._operation(db, actor, 'cancel', key,
                {'originalTriggerRunId': original_id, 'expectedRevision': expected_revision}, 'job', request_cancel)
        if not receipt.get('replayed'):
            # Read the nested receipt under the same lifecycle permission;
            # operation() would unnecessarily require the Job read grant.
            with self.service.transaction(readonly=True) as db:
                row = db.execute('SELECT document FROM final_operations WHERE id=?',
                                 (receipt['result']['canonicalOperationId'],)).fetchone()
                original = json.loads(row[0])['response']
            await self.service._cancel_postcommit(binding['runId'], original, actor=actor, capability='lifecycle')
        return {'run': {**copy.deepcopy(state['runs'][original_id]),
                        'revision': expected_revision + 1, 'cancellationRequested': True}, 'operation': receipt}


def project(service, source_root, records, *, actor):
    """Enrich retained history only when an actual handoff receipt exists.

    No enrollment, source writer or mutation is reachable from this read port.
    A broken committed binding is an error, never a silently absent successor.
    """
    port = TriggerReceiptHandoff(service, source_root)
    with service.transaction(readonly=True) as db:
        bindings = service._all(db, 'trigger_receipt_handoff')
    try:
        service._permit(actor, 'job', 'read')
    except fc.ContractError as exc:
        if exc.code == 'authorization_denied':
            return  # Preserve the original Trigger-only history grant.
        raise
    for binding in bindings:
        if binding['sourceRoot'] == str(port.root) and binding['originalId'] in records:
            records[binding['originalId']]['canonicalOriginal'] = port.query(binding['originalId'], actor=actor)
            records[binding['originalId']]['revision'] = binding['controlRevision']
