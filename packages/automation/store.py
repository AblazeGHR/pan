"""Atomic Trigger/Run ledger. Call through asyncio.to_thread, never on loop."""
import copy
import json
import threading
import time
from datetime import datetime
import uuid
from pathlib import Path

from packages.actions.store import atomic
from .events import now
from .event_store import CapacityError, RETENTION_SECONDS


class Conflict(ValueError):
    pass


class Ledger:
    def __init__(self, root, max_triggers=64, max_runs=10_000, max_bytes=64*1024*1024):
        self.path = Path(root) / 'registry.json'
        self.max_triggers = max_triggers
        self.max_runs, self.max_bytes = max_runs, max_bytes
        self.lock = threading.RLock()

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
            allowed = {'id', 'name', 'description', 'eventTypes', 'condition', 'actionId', 'arguments', 'executionMode', 'enabled'}
            if set(item) - allowed:
                raise ValueError('Unknown Trigger fields')
            tid = item.setdefault('id', 'trg_' + uuid.uuid4().hex)
            if not isinstance(tid, str) or not tid or len(tid) > 100:
                raise ValueError('Invalid Trigger ID')
            old = state['triggers'].get(tid)
            if not old and len(state['triggers']) >= self.max_triggers:
                raise ValueError('Trigger capacity reached')
            if type(expected) is not int or expected != (old['revision'] if old else 0):
                raise Conflict('Trigger revision changed')
            if not isinstance(item.get('name'), str) or not item['name'].strip() or not isinstance(item.get('actionId'), str):
                raise ValueError('Trigger name and actionId required')
            events = item.get('eventTypes')
            if not isinstance(events, list) or not events or any(not isinstance(e, str) for e in events):
                raise ValueError('eventTypes must be nonempty strings')
            if item.get('executionMode', 'serial') != 'serial' or not isinstance(item.get('arguments', {}), dict):
                raise ValueError('Only serial mode and argument object supported')
            enabled = item.setdefault('enabled', False)
            if type(enabled) is not bool:
                raise ValueError('enabled must be boolean')
            if not authorized and (enabled or (old and old['enabled'])):
                raise PermissionError('User token required')
            item.update(revision=expected + 1, createdBy=old['createdBy'] if old else (creator or {'type': 'agent'}), createdAt=old['createdAt'] if old else now(), updatedAt=now())
            state['triggers'][tid] = item
            if not enabled:
                for run in state['runs'].values():
                    if run['triggerId'] == tid and run['status'] == 'queued':
                        run.update(status='cancelled', finishedAt=now(), revision=run['revision'] + 1)
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
        self.mutate(change)

    def claim(self, rid):
        def change(state):
            run = state['runs'][rid]
            if run['status'] != 'queued' or not run.get('admitted', True):
                return None
            trigger = state['triggers'].get(run['triggerId'])
            if not trigger or not trigger['enabled']:
                run.update(status='cancelled', finishedAt=now(), revision=run['revision'] + 1)
                self.sync_attempt(run)
                return None
            run.update(status='running', startedAt=now(), revision=run['revision'] + 1)
            attempts = run.setdefault('attempts', [])
            # A dependency deferral is pre-effect waiting, not a new retry.
            if not attempts:
                attempts.append(dict(id='att_' + uuid.uuid4().hex, number=1, status='running', startedAt=run['startedAt']))
            elif attempts[-1]['status'] != 'queued':
                raise Conflict('Run already has an attempted effect; automatic retry refused')
            run['attemptCount'] = len(attempts)
            run['activeAttemptId'] = attempts[-1]['id']
            self.sync_attempt(run)
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
            if run['status'] != 'queued':
                raise Conflict('Only queued Runs can be cancelled; started effects require reconciliation')
            run.update(status='cancelled', finishedAt=now(), revision=expected + 1)
            self.sync_attempt(run)
            return run
        return self.mutate(change)

    def recover(self):
        def change(state):
            for run in state['runs'].values():
                if run['status'] == 'queued':
                    trigger = state['triggers'].get(run['triggerId'])
                    if not trigger or not trigger['enabled']:
                        run.update(status='cancelled', finishedAt=now())
                    run['admitted'] = True
                    run['revision'] += 1
                if run['status'] == 'running':
                    run.update(status='uncertain', error='Service interrupted; no automatic replay', finishedAt=now(), revision=run['revision'] + 1)
                    if not run.get('attempts'):
                        run['attempts'] = [dict(id='att_' + uuid.uuid4().hex, number=1, status='uncertain', legacy=True)]
                        run['attemptCount'] = 1
                        run['activeAttemptId'] = run['attempts'][0]['id']
                self.sync_attempt(run)
        self.mutate(change)


    @staticmethod
    def sync_attempt(run):
        if run.get('attempts'):
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
