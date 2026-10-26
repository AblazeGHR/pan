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
from packages.jobs import chains
from packages.jobs.runtime_owner import legacy_writer
from .event_store import CapacityError, RETENTION_SECONDS
from .policy import concurrency, retry_policy


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

    def record_final_admission(self, event_id, trigger_id, receipt):
        """WP03 projection of an already durable canonical admission.

        This is not an execution queue. If the projection write fails, inbox
        delivery retries by the same Event/Trigger identity in FinalService.
        """
        key = event_id + ':' + trigger_id
        receipt = {key: copy.deepcopy(receipt[key]) for key in
                   ('operationId', 'requestKey', 'runId', 'admissionId') if key in receipt}
        def change(state):
            admissions = state.setdefault('finalAdmissions', {})
            old = admissions.get(key)
            if old is not None:
                if old != receipt:
                    raise Conflict('Canonical admission identity changed')
                return old
            admissions[key] = copy.deepcopy(receipt)
            return receipt
        return self.mutate(change)

    @legacy_writer(lambda self, *a, **kw: self.path.parent.parent)
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

    def put(self, raw, expected, authorized=False, creator=None, authority=None, *, managed=False):
        def change(state):
            item = copy.deepcopy(raw)
            allowed = {'id', 'name', 'description', 'eventTypes', 'eventFilter', 'condition', 'actionId', 'arguments', 'steps', 'targetJobId', 'ownerJobId', 'ownerLinkId', 'executionMode', 'maxConcurrency', 'retry', 'enabled', 'paused'}
            if set(item) - allowed:
                raise ValueError('Unknown Trigger fields')
            tid = item.setdefault('id', 'trg_' + uuid.uuid4().hex)
            if not isinstance(tid, str) or not tid or len(tid) > 100:
                raise ValueError('Invalid Trigger ID')
            old = state['triggers'].get(tid)
            if (item.get('ownerJobId') or (old and old.get('ownerJobId'))) and not managed:
                raise Conflict('Managed Trigger must be edited from its owning Job')
            if item.get('targetJobId') is not None and (not isinstance(item['targetJobId'],str) or not item['targetJobId'].startswith('job_')):
                raise ValueError('Invalid Job target')
            if item.get('targetJobId') and 'steps' in item:raise ValueError('Choose a Job target or a direct Action chain')
            # Older clients omit additive scheduler fields. Preserve an existing
            # explicit policy rather than silently reverting it on a name edit.
            if old:
                if old.get('targetJobId'):
                    item.setdefault('targetJobId', old['targetJobId'])
                    if 'steps' in item:
                        raise Conflict('Edit executable steps from the target Job')
                if old.get('managedJob'):
                    item['managedJob'] = copy.deepcopy(old['managedJob'])
                if 'eventFilter' in old:item.setdefault('eventFilter',copy.deepcopy(old['eventFilter']))
                item.setdefault('executionMode', old.get('executionMode', 'serial'))
                if item['executionMode'] == old.get('executionMode', 'serial'):
                    item.setdefault('maxConcurrency', concurrency(old))
                item.setdefault('retry', copy.deepcopy(old.get('retry', {})))
            if not old and len(state['triggers']) >= self.max_triggers:
                raise ValueError('Trigger capacity reached')
            if type(expected) is not int or expected != (old['revision'] if old else 0):
                raise Conflict('Trigger revision changed')
            if 'steps' in item:
                item['steps']=chains.normalize(item['steps'])
                item.setdefault('actionId',item['steps'][0]['actionId'])
                if item['actionId']!=item['steps'][0]['actionId']:raise ValueError('actionId must match first step')
            if not isinstance(item.get('name'), str) or not item['name'].strip() or not isinstance(item.get('actionId'), str):
                raise ValueError('Trigger name and actionId required')
            events = item.get('eventTypes')
            if not isinstance(events, list) or not events or any(not isinstance(e, str) for e in events):
                raise ValueError('eventTypes must be nonempty strings')
            filters=item.get('eventFilter',{})
            if not isinstance(filters,dict) or len(filters)>16 or any(
                not isinstance(k,str) or not k.startswith('payload.') or any(not part for part in k.split('.'))
                or not isinstance(v,(str,int,float,bool,type(None))) for k,v in filters.items()):
                raise ValueError('eventFilter requires at most16 payload paths with scalar equality values')
            item['maxConcurrency'] = concurrency(item)
            item.setdefault('executionMode', 'serial')
            if 'retry' in item and item['retry'] is None:
                raise ValueError('retry must be an object')
            item['retry'] = retry_policy(item.get('retry'))
            if not isinstance(item.get('arguments', {}), dict):
                raise ValueError('arguments must be an object')
            validate_argument_bindings(item.get('arguments', {}))
            enabled = item.setdefault('enabled', False)
            item.setdefault('paused', False)
            if type(item['paused']) is not bool:
                raise ValueError('paused must be boolean')
            if type(enabled) is not bool:
                raise ValueError('enabled must be boolean')
            if not authorized and (enabled or (old and old['enabled'])):
                raise PermissionError('User token required')
            if old and not authorized and (old.get('executionMode', 'serial') != item['executionMode'] or concurrency(old) != item['maxConcurrency']):
                raise PermissionError('User token required to change concurrency configuration')
            if old and item['maxConcurrency'] < sum(r['triggerId']==tid and r['status']=='running' for r in state['runs'].values()):
                raise Conflict('Running Run count exceeds new concurrency; wait before lowering limit')
            item.update(revision=expected + 1, createdBy=old['createdBy'] if old else (creator or {'type': 'agent'}), createdAt=old['createdAt'] if old else now(), updatedAt=now())
            item['authorization'] = copy.deepcopy(authority or {'type': 'user'})
            state['triggers'][tid] = item
            return item
        return self.mutate(change)

    def set_enabled(self, tid, enabled, expected, authority):
        with self.lock:
            old=self.read()['triggers'][tid]
            raw={k:v for k,v in old.items() if k not in {'revision','createdBy','createdAt','updatedAt','authorization','managedJob'}}
            raw['enabled']=enabled
            return self.put(raw,expected,True,authority=authority)

    def delete(self, tid, expected):
        def change(state):
            old = state['triggers'][tid]
            if old.get('ownerJobId'):raise Conflict('Managed Trigger must be deleted from its owning Job')
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
            if not current or current['revision'] != run['triggerRevision'] or (run['status'] == 'queued' and (not current['enabled'] or current.get('paused'))):
                run.update(status='cancelled', finishedAt=now(), error='Trigger changed during matching')
            run['revision'] = 1
            state['runs'][run['id']] = run
            return run
        return self.mutate(change)

    def update(self, rid, expected_revision=None, **fields):
        def change(state):
            run = state['runs'][rid]
            if expected_revision is not None and run['revision'] != expected_revision:
                raise Conflict('Run revision changed')
            state['runs'][rid].update(fields, revision=run['revision'] + 1)
            return state['runs'][rid]
        return self.mutate(change)

    def release_event(self, event_id):
        def change(state):
            for run in state['runs'].values():
                if run['event']['id'] == event_id and run.get('recordType') == 'triggerAdmission' and not run.get('admitted'):
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

    def bind_job(self, tid, expected, job_id, provenance):
        def change(state):
            trigger = state['triggers'][tid]
            if trigger['revision'] != expected:
                raise Conflict('Trigger changed during managed Job creation')
            if trigger.get('targetJobId') not in (None, job_id):
                raise Conflict('Trigger already has a different Job target')
            trigger['targetJobId'] = job_id
            trigger['managedJob'] = copy.deepcopy(provenance)
            trigger.pop('steps', None)
            # Linking does not change the executable revision observed by Event.
            return trigger
        return self.mutate(change)

    def import_subscription(self, job):
        tid = 'trigger:' + job['jobId']
        def change(state):
            old = state['triggers'].get(tid)
            if old:
                return old  # Explicit Trigger lifecycle is now authoritative.
            trigger = dict(id=tid, name=job['name'], enabled=True, paused=False,
                eventTypes=copy.deepcopy(job['config']['trigger']['eventTypes']),
                actionId=job['config']['actionId'], targetJobId=job['jobId'], arguments={},
                revision=1, createdBy=copy.deepcopy(job.get('authorization', {'type':'user'})),
                authorization=copy.deepcopy(job.get('authorization', {'type':'user'})),
                createdAt=now(), updatedAt=now(), managedJob={'source':'embedded-event', 'jobRevision':job['configRevision']})
            if len(state['triggers']) >= self.max_triggers:
                raise CapacityError('Trigger capacity reached')
            state['triggers'][tid] = trigger
            return trigger
        with self.lock:
            if tid in self.read()['triggers']:
                return self.read()['triggers'][tid]
            return self.mutate(change)

    def cancel(self, rid, expected):
        def change(state):
            run = state['runs'][rid]
            if run['revision'] != expected:
                raise Conflict('Run revision changed')
            if run.get('recordType') != 'triggerAdmission' or run['status'] != 'queued':
                raise Conflict('Control execution through the target Job; historical Runs are read only')
            run.update(status='cancelled', finishedAt=now(), revision=expected + 1)
            return run
        return self.mutate(change)

    def confirm_uncertain(self, rid, expected, max_pending=1024, authority=None):
        self.read()['runs'][rid]
        raise Conflict('Reconcile the original Job admission; Trigger cannot retry effects')

    def maintenance(self, retained_event_ids, *, clock=time.time, retention=RETENTION_SECONDS):
        def change(state):
            deleted = []
            for rid, run in list(state['runs'].items()):
                if run.get('recordType') != 'triggerAdmission' or run['status'] not in {'admitted', 'failed', 'cancelled', 'skipped'} or run['event']['id'] in retained_event_ids:
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
