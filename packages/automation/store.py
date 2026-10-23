"""Atomic Trigger/Run ledger. Call through asyncio.to_thread, never on loop."""
import copy
import json
import threading
import time
from datetime import datetime, timezone
import uuid
from pathlib import Path

from packages.actions.store import atomic
from packages.core.system_fields import validate_argument_bindings
from .events import now
from .event_store import CapacityError, RETENTION_SECONDS
from .policy import concurrency, retry_policy, backoff


class Conflict(ValueError):
    pass


class Ledger:
    def __init__(self, root, max_triggers=64, max_runs=10_000, max_bytes=64*1024*1024, clock=time.time):
        self.path = Path(root) / 'registry.json'
        self.max_triggers = max_triggers
        self.max_runs, self.max_bytes = max_runs, max_bytes
        self.lock = threading.RLock()
        self.clock = clock

    def read(self):
        with self.lock:
            return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'revision': 0, 'triggers': {}, 'runs': {}}

    def mutate(self, change):
        with self.lock:
            state = self.read()
            previous_size = len(json.dumps(state, ensure_ascii=False).encode('utf-8'))
            result = change(state)
            state['revision'] += 1
            size = len(json.dumps(state, ensure_ascii=False).encode('utf-8'))
            if size > self.max_bytes and size > previous_size:
                raise CapacityError('Run ledger byte capacity reached; write refused')
            atomic(self.path, state)
            return copy.deepcopy(result)

    def put(self, raw, expected, authorized=False, creator=None):
        def change(state):
            item = copy.deepcopy(raw)
            allowed = {'id', 'name', 'description', 'eventTypes', 'condition', 'actionId', 'arguments', 'executionMode', 'maxConcurrency', 'retry', 'enabled'}
            if set(item) - allowed:
                raise ValueError('Unknown Trigger fields')
            tid = item.setdefault('id', 'trg_' + uuid.uuid4().hex)
            if not isinstance(tid, str) or not tid or len(tid) > 100:
                raise ValueError('Invalid Trigger ID')
            old = state['triggers'].get(tid)
            # Older clients omit additive scheduler fields. Preserve an existing
            # explicit policy rather than silently reverting it on a name edit.
            if old:
                item.setdefault('executionMode', old.get('executionMode', 'serial'))
                if item['executionMode'] == old.get('executionMode', 'serial'):
                    item.setdefault('maxConcurrency', concurrency(old))
                item.setdefault('retry', copy.deepcopy(old.get('retry', {})))
            if not old and len(state['triggers']) >= self.max_triggers:
                raise ValueError('Trigger capacity reached')
            if type(expected) is not int or expected != (old['revision'] if old else 0):
                raise Conflict('Trigger revision changed')
            if not isinstance(item.get('name'), str) or not item['name'].strip() or not isinstance(item.get('actionId'), str):
                raise ValueError('Trigger name and actionId required')
            events = item.get('eventTypes')
            if not isinstance(events, list) or not events or any(not isinstance(e, str) for e in events):
                raise ValueError('eventTypes must be nonempty strings')
            item['maxConcurrency'] = concurrency(item)
            item.setdefault('executionMode', 'serial')
            if 'retry' in item and item['retry'] is None:
                raise ValueError('retry must be an object')
            item['retry'] = retry_policy(item.get('retry'))
            if not isinstance(item.get('arguments', {}), dict):
                raise ValueError('arguments must be an object')
            validate_argument_bindings(item.get('arguments', {}))
            enabled = item.setdefault('enabled', False)
            if type(enabled) is not bool:
                raise ValueError('enabled must be boolean')
            if not authorized and (enabled or (old and old['enabled'])):
                raise PermissionError('User token required')
            if old and not authorized and (old.get('executionMode', 'serial') != item['executionMode'] or concurrency(old) != item['maxConcurrency']):
                raise PermissionError('User token required to change concurrency configuration')
            if old and item['maxConcurrency'] < sum(r['triggerId']==tid and r['status']=='running' for r in state['runs'].values()):
                raise Conflict('Running Run count exceeds new concurrency; wait before lowering limit')
            item.update(revision=expected + 1, createdBy=old['createdBy'] if old else (creator or {'type': 'agent'}), createdAt=old['createdAt'] if old else now(), updatedAt=now())
            state['triggers'][tid] = item
            if not enabled:
                for run in state['runs'].values():
                    if run['triggerId'] == tid and run['status'] in {'queued', 'retry_wait'}:
                        run.update(status='cancelled', finishedAt=now(), revision=run['revision'] + 1)
                        run.pop('nextAttemptAt', None)
                        run.pop('retryAuthorized', None)
                        self.sync_attempt(run)
            return item
        return self.mutate(change)

    def delete(self, tid, expected):
        def change(state):
            old = state['triggers'][tid]
            if old['revision'] != expected:
                raise Conflict('Trigger revision changed')
            if old['enabled']:
                raise ValueError('Disable before deleting')
            del state['triggers'][tid]
        return self.mutate(change)

    def append(self, run):
        def change(state):
            # Same retained Event+Trigger is deduplicated, including failed runs.
            if any(r['event']['id'] == run['event']['id'] and r['triggerId'] == run['triggerId'] for r in state['runs'].values()):
                return None
            if len(state['runs']) >= self.max_runs:
                raise CapacityError('Run ledger count capacity reached; admission refused')
            current = state['triggers'].get(run['triggerId'])
            if not current or not current['enabled'] or current['revision'] != run['triggerRevision']:
                run.update(status='cancelled', finishedAt=now(), error='Trigger changed during matching')
            run['revision'] = 1
            run.setdefault('attempts', [])
            run.setdefault('attemptCount', 0)
            run.setdefault('retryPolicy', retry_policy(current.get('retry') if current else None))
            state['runs'][run['id']] = run
            return run
        return self.mutate(change)

    def update(self, rid, expected_revision=None, **fields):
        def change(state):
            run = state['runs'][rid]
            if expected_revision is not None and run['revision'] != expected_revision:
                raise Conflict('Run revision changed')
            state['runs'][rid].update(fields, revision=run['revision'] + 1)
            self.sync_attempt(run)
            return state['runs'][rid]
        return self.mutate(change)

    def release_event(self, event_id):
        def change(state):
            for run in state['runs'].values():
                if run['event']['id'] == event_id:
                    run['admitted'] = True
                    run['revision'] += 1
        # A retained fact with an unavailable dependency may have no Run yet.
        # Do not rewrite/fsync the entire ledger for that empty admission fence:
        # a page of such facts would delay unrelated ready Events. Keep the
        # existence check and actual admission under the same reentrant lock.
        with self.lock:
            if not any(run['event']['id'] == event_id for run in self.read()['runs'].values()):
                return
            self.mutate(change)

    def claim(self, rid):
        def change(state):
            run = state['runs'][rid]
            if run['status'] not in {'queued', 'retry_wait'} or not run.get('admitted', True):
                return None
            trigger = state['triggers'].get(run['triggerId'])
            if not trigger or not trigger['enabled']:
                run.update(status='cancelled', finishedAt=now(), revision=run['revision'] + 1)
                self.sync_attempt(run)
                return None
            if run['status'] == 'retry_wait':
                if not self.auto_retry_allowed(run, trigger):
                    run.update(status='failed', error='Retry policy no longer permits another attempt', revision=run['revision']+1)
                    run.pop('nextAttemptAt', None)
                    run.pop('retryAuthorized', None)
                    return None
                if datetime.fromisoformat(run['nextAttemptAt']).timestamp() > self.clock():
                    return None
            running = sum(r['triggerId'] == run['triggerId'] and r['status'] == 'running' for r in state['runs'].values())
            if running >= concurrency(trigger):
                return None
            started_at = now()
            attempts = run.setdefault('attempts', [])
            # A dependency deferral is pre-effect waiting, not a new retry.
            if not attempts:
                attempts.append(dict(id='att_' + uuid.uuid4().hex, number=1, status='running', startedAt=started_at))
            elif attempts[-1]['status'] != 'queued':
                limit = min(retry_policy(run.get('retryPolicy'))['maxAttempts'], retry_policy(trigger.get('retry'))['maxAttempts'])
                if not run.get('retryAuthorized') or len(attempts) >= limit:
                    run.update(status='cancelled', finishedAt=now(), revision=run['revision']+1, error='Another attempt was not authorized or attempt limit reached')
                    run.pop('retryAuthorized', None)
                    return None
                attempts.append(dict(id='att_' + uuid.uuid4().hex, number=len(attempts)+1, status='running', startedAt=started_at, reason=run['retryAuthorized']))
            run.update(status='running', startedAt=started_at, revision=run['revision'] + 1)
            for key in ('finishedAt', 'error', 'result', 'nextAttemptAt', 'retryAuthorized', 'waitingReason'):
                run.pop(key, None)
            run['attemptCount'] = len(attempts)
            run['activeAttemptId'] = attempts[-1]['id']
            self.sync_attempt(run)
            return run
        return self.mutate(change)

    @staticmethod
    def auto_retry_allowed(run, trigger):
        frozen = retry_policy(run.get('retryPolicy'))
        current = retry_policy(trigger.get('retry'))
        return (trigger['enabled'] and frozen['enabled'] and current['enabled']
                and run.get('retryEligible') is True
                and len(run.get('attempts', [])) < min(frozen['maxAttempts'], current['maxAttempts']))

    def finish(self, rid, status, *, retryable=True, **fields):
        def change(state):
            run = state['runs'][rid]
            if run['status'] != 'running':
                raise Conflict('Attempt no longer running')
            run.update(fields, status=status, finishedAt=now(), revision=run['revision']+1)
            self.sync_attempt(run)  # Finish history before changing logical Run to waiting.
            run.pop('action', None)
            trigger = state['triggers'].get(run['triggerId'])
            if status == 'failed' and retryable and trigger and self.auto_retry_allowed(run, trigger):
                run.update(status='retry_wait', retryAuthorized='automatic',
                           nextAttemptAt=datetime.fromtimestamp(self.clock()+backoff(len(run['attempts'])), timezone.utc).isoformat())
            return run
        return self.mutate(change)

    def prepare_action(self, rid, action, digest):
        def change(state):
            run = state['runs'][rid]
            if run['status'] != 'running':
                raise Conflict('Attempt no longer running')
            # Only the original start can establish retry eligibility. A newer
            # declaration never upgrades an already-started logical Run.
            if 'retryEligible' not in run:
                run['retryEligible'] = (len(run.get('attempts', [])) == 1
                                        and action.get('idempotent') is True
                                        and retry_policy(run.get('retryPolicy'))['enabled'])
            if run.get('actionDigest') and run['actionDigest'] != digest:
                raise Conflict('Action changed since first attempt; retry refused')
            run.update(action=copy.deepcopy(action), actionDigest=digest, revision=run['revision']+1)
            return run
        return self.mutate(change)

    def interrupt(self, rid, attempt_id, error):
        def change(state):
            run = state['runs'][rid]
            # Cancellation can arrive after a shielded final commit. Preserve
            # that durable outcome and never rewrite a different attempt.
            if run['status'] == 'running' and run.get('activeAttemptId') == attempt_id:
                run.update(status='uncertain', error=error, finishedAt=now(), revision=run['revision']+1)
                self.sync_attempt(run)
                run.pop('action', None)
            return run
        return self.mutate(change)

    def confirm_uncertain(self, rid, expected, max_pending=1024):
        def change(state):
            run = state['runs'][rid]
            if type(expected) is not int or run['revision'] != expected or run['status'] != 'uncertain':
                raise Conflict('Expected current uncertain Run revision')
            trigger = state['triggers'].get(run['triggerId'])
            if not trigger or not trigger['enabled']:
                raise Conflict('Enable current Trigger before confirming recovery')
            limit = min(retry_policy(run.get('retryPolicy'))['maxAttempts'], retry_policy(trigger.get('retry'))['maxAttempts'])
            if len(run.get('attempts', [])) >= limit:
                raise Conflict('Total attempt limit reached')
            if sum(r['status'] in {'queued','running','retry_wait'} for r in state['runs'].values()) >= max_pending:
                raise Conflict('Trigger backlog full')
            run.update(status='queued', admitted=True, retryAuthorized='user-confirmed', revision=run['revision']+1)
            run.pop('nextAttemptAt', None)
            return run
        return self.mutate(change)

    def defer_unavailable(self, rid, expected):
        """Return a pre-effect claim to waiting without resurrecting cancellation.

        The same transaction rechecks Run ownership/revision and current Trigger
        enablement. Disable before or after this transaction therefore converges
        to cancelled; a stale deferral never overwrites a newer Run transition.
        """
        def change(state):
            run = state['runs'][rid]
            if run['status'] != 'running' or run['revision'] != expected:
                return run
            trigger = state['triggers'].get(run['triggerId'])
            if trigger and trigger['enabled']:
                run.update(status='queued', waitingReason='Event dependency unavailable', revision=expected + 1)
            else:
                run.update(status='cancelled', finishedAt=now(), revision=expected + 1)
            self.sync_attempt(run)
            return run
        return self.mutate(change)

    def cancel(self, rid, expected):
        def change(state):
            run = state['runs'][rid]
            if type(expected) is not int or run['revision'] != expected:
                raise Conflict('Run revision changed')
            if run['status'] not in {'queued', 'retry_wait'}:
                raise Conflict('Only queued or retry-wait Runs can be cancelled; started effects require reconciliation')
            run.update(status='cancelled', finishedAt=now(), revision=expected + 1)
            run.pop('nextAttemptAt', None)
            run.pop('retryAuthorized', None)
            self.sync_attempt(run)
            return run
        return self.mutate(change)

    def recover(self):
        def change(state):
            for run in state['runs'].values():
                if run['status'] in {'queued', 'retry_wait'}:
                    trigger = state['triggers'].get(run['triggerId'])
                    if not trigger or not trigger['enabled']:
                        run.update(status='cancelled', finishedAt=now())
                        run.pop('nextAttemptAt', None)
                        run.pop('retryAuthorized', None)
                    run['admitted'] = True
                    run['revision'] += 1
                if run['status'] == 'running':
                    run.update(status='uncertain', error='Service interrupted; no automatic replay', finishedAt=now(), revision=run['revision'] + 1)
                    if not run.get('attempts'):
                        run['attempts'] = [dict(id='att_' + uuid.uuid4().hex, number=1, status='uncertain', legacy=True)]
                        run['attemptCount'] = 1
                        run['activeAttemptId'] = run['attempts'][0]['id']
                self.sync_attempt(run)
                if run['status'] not in {'queued', 'running'}:
                    run.pop('action', None)
        self.mutate(change)


    @staticmethod
    def sync_attempt(run):
        if run.get('attempts') and run['attempts'][-1]['status'] in {'queued', 'running'}:
            attempt = run['attempts'][-1]
            for key in ('status', 'startedAt', 'finishedAt', 'error', 'result', 'targetIds', 'targetResults', 'selection'):
                if key in run:
                    attempt[key] = copy.deepcopy(run[key])

    def maintenance(self, retained_event_ids, *, clock=time.time, retention=RETENTION_SECONDS):
        def change(state):
            deleted = []
            for rid, run in list(state['runs'].items()):
                if run['status'] not in {'succeeded', 'failed', 'cancelled', 'skipped'} or run['event']['id'] in retained_event_ids:
                    continue
                try:
                    finished_at = datetime.fromisoformat(run.get('finishedAt', ''))
                    if finished_at.tzinfo is None:
                        continue  # Do not guess a legacy record's timezone.
                    finished = finished_at.timestamp()
                except (ValueError, TypeError):
                    continue  # Unknown legacy timestamps are preserved, not guessed.
                if finished < clock() - retention:
                    del state['runs'][rid]
                    deleted.append(rid)
            return {'deletedRuns': len(deleted)}
        return self.mutate(change)

    def stats(self):
        state = self.read()
        size = len(json.dumps(state, ensure_ascii=False).encode('utf-8'))
        count = len(state['runs'])
        return dict(runs=count, bytes=size, maxRuns=self.max_runs, maxBytes=self.max_bytes,
                    warning=count >= self.max_runs*.8 or size >= self.max_bytes*.8,
                    full=count >= self.max_runs or size >= self.max_bytes)
