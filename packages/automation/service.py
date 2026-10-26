"""Explicit automation lifetime, durable event admission and bounded dispatch."""
import asyncio
import threading
from pathlib import Path

from packages.actions.store import Store
from packages.core.runtime_issues import runtime_issue_event_schema
from packages.core.system_fields import canonical_event
from .auth import UserAuth
from .engine import Engine
from .events import Bus, now
from .event_store import EventStore, fact_identity
from .store import Ledger


class Service:
    def __init__(self, root, worker, pause):
        self.events = EventStore(root)
        self.bus = Bus(durable=self.accept)
        self.ledger = Ledger(root)
        self.auth = UserAuth(root)
        self.worker = worker
        self.pause = pause
        self.core = self.bus.activate('core', 'workers')
        self.bus.register(self.core, 'workers.all_idle', {'type': 'object', 'properties': {'runningCount': {'const': 0}}, 'required': ['runningCount'], 'additionalProperties': False})
        self.issue_runtime = self.bus.activate('core', 'runtime')
        self.bus.register(self.issue_runtime, 'runtime.issue.v1', runtime_issue_event_schema())
        from packages.scheduler.time_events import EVENT_TYPE, PAYLOAD_SCHEMA
        self.scheduler_handle=self.bus.activate('core','scheduler')
        self.bus.register(self.scheduler_handle,EVENT_TYPE,PAYLOAD_SCHEMA)
        async def state_reader(key):
            if key == 'workers.runningCount':
                return sum(w.status == 'running' for w in worker.workers.values())
            raise ValueError('Unknown State field: ' + str(key))
        self.engine = Engine(self.ledger, self.bus, Store(), state_reader=state_reader)
        self.engine.authorization = self.auth.permission_allowed
        self.previous = sum(w.status == 'running' for w in worker.workers.values())
        self.ingress = asyncio.Queue(128)
        self.wake = asyncio.Event()
        self.admission = asyncio.Lock()
        self.started = False
        self.stopping = False
        self.loop = None
        self._runtime_issue_lock = threading.Lock()
        self._runtime_issue_reserved = 0
        self.dispatcher = None
        self.ingester = None
        self.job_runtime = None
        self.engine_started = False
        from .quota_policy import Runtime as QuotaPolicy
        # Configuration/evaluation stay available without starting the retired
        # query scheduler. Effects require a separately bound host adapter.
        self.quota_policy = QuotaPolicy(root, Path(root).parent / 'background_jobs')

    def canonical(self):
        final = getattr(self, 'final_jobs', None)
        return final is not None and final.owner.token is not None

    def diagnostic(self, phase, error, event_id=None):
        self.bus.diagnostics.append(dict(phase=phase, error=str(error), eventId=event_id, at=now()))

    def observe(self):
        count = sum(w.status == 'running' for w in self.worker.workers.values())
        previous, self.previous = self.previous, count
        if previous > 0 and count == 0:
            try:
                self.ingress.put_nowait(self.bus.prepare(self.core, 'workers.all_idle', {'runningCount': 0}))
            except Exception as exc:
                self.diagnostic('core-ingress', exc)

    def observe_runtime_issue(self, mapping):
        """Enqueue a mapped RuntimeIssue Event through trusted core admission.

        The quota observer is intentionally separate; this path only adds the
        durable Event/Trigger fact. Same-loop calls enter the existing 128-slot
        ingress directly. Cross-thread calls reserve one bounded callback slot
        before scheduling; the reservation is released in every completion and
        refusal path so a blocked loop cannot accumulate unbounded callbacks.
        """
        event = mapping.get('event') if isinstance(mapping, dict) else None
        if not isinstance(event, dict) or event.get('type') != 'runtime.issue.v1':
            return
        if self.stopping or not self.started:
            self.diagnostic('runtime-issue-ingress', 'Runtime closed or not started', event.get('id'))
            return
        loop = self.loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._enqueue_runtime_issue(event)
            return
        if loop is None or loop.is_closed():
            self.diagnostic('runtime-issue-ingress', 'Runtime loop unavailable', event.get('id'))
            return
        if not self._reserve_runtime_issue():
            self.diagnostic('runtime-issue-ingress', 'Runtime issue cross-thread backlog full', event.get('id'))
            return
        try:
            loop.call_soon_threadsafe(self._enqueue_runtime_issue, event, True)
        except Exception as exc:
            self._release_runtime_issue()
            self.diagnostic('runtime-issue-ingress', 'Runtime loop closed: ' + str(exc), event.get('id'))

    def _reserve_runtime_issue(self):
        limit = self.ingress.maxsize or 128
        with self._runtime_issue_lock:
            if self._runtime_issue_reserved >= limit:
                return False
            self._runtime_issue_reserved += 1
            return True

    def _release_runtime_issue(self):
        with self._runtime_issue_lock:
            if self._runtime_issue_reserved > 0:
                self._runtime_issue_reserved -= 1

    def _reset_runtime_issue_reservations(self):
        with self._runtime_issue_lock:
            self._runtime_issue_reserved = 0

    def _enqueue_runtime_issue(self, event, reserved=False):
        try:
            if self.stopping or not self.started:
                self.diagnostic('runtime-issue-ingress', 'Runtime closed before admission', event.get('id'))
                return
            try:
                # Bus validation and the core owner identity are authoritative;
                # the mapping helper cannot bypass payload schema or publish as
                # a plugin.
                prepared = self.bus.prepare(
                    self.issue_runtime, event['type'], event.get('payload'),
                    event.get('id'), system=event.get('system'))
                self.ingress.put_nowait(prepared)
            except asyncio.QueueFull:
                self.diagnostic('runtime-issue-ingress', 'Runtime issue backlog full', event.get('id'))
            except Exception as exc:
                self.diagnostic('runtime-issue-ingress', exc, event.get('id'))
        finally:
            if reserved:
                self._release_runtime_issue()

    async def accept(self, event, handle=None):
        # At most one admission snapshot/commit at a time; no Action is awaited.
        async with self.admission:
            if not self.started or self.stopping:
                raise ValueError('Durable event admission is closed')
            if not self.canonical():
                await self.engine.subscriptions()
            state = await self.engine.io(self.ledger.read)
            if handle is not None and handle not in self.bus.handles:
                raise ValueError('Event publisher revoked before durable admission')
            # Canonical system fields are part of Event identity. Normalize
            # before retained-Run dedupe so aliases never create a second fact.
            event = canonical_event(event)
            retained = [r['event'] for r in state['runs'].values() if r['event']['id'] == event['id']]
            if retained:
                if any(fact_identity(old) != fact_identity(event) for old in retained):
                    raise ValueError('Event ID already belongs to a different fact in retained Run history')
                # The inbox may have expired while Run dedup is still retained.
                # Preserve its original envelope; do not reset any Run/Attempt.
                event = retained[0]
            # Snapshot every subscription, including non-executing admissions.
            targets = [{**t,'_admissionStatus':'disabled' if not t['enabled'] or t.get('paused') else
                       'unavailable' if not self.engine.available(t) else 'eligible'}
                       for t in ([] if self.canonical() else state['triggers'].values()) if event['type'] in t['eventTypes']]
            final_jobs = getattr(self, 'final_jobs', None)
            if final_jobs is not None:
                targets.extend(await self.engine.io(final_jobs.targets, event))
            for target in targets:
                if target.get('ownerJobId') and any(op.get('jobId')==target['ownerJobId'] and op['status'] not in {'complete','cancelled'} for op in state.get('linkOperations',{}).values()):target['_admissionStatus']='disabled'
            try:
                receipt = await self.engine.io(self.events.append, event, targets)
                if retained:
                    receipt['duplicate'] = True
            except Exception as exc:
                self.diagnostic('event-admission', exc, event['id'])
                raise
            self.wake.set()
            receipt['persisted']=True
            return receipt

    async def accept_time(self,event):
        prepared=self.bus.prepare(self.scheduler_handle,event['type'],event['payload'],event['id'])
        prepared['occurredAt']=event['occurredAt']
        return await self.accept(prepared,self.scheduler_handle)

    async def deliver(self,row,errors_batch=None):
        """Each persisted subscription settles independently; retry only gaps."""
        import copy
        event=row['event']; eid=event['id']
        receipts=await self.engine.io(self.events.subscription_receipts,eid)
        errors=[]
        if not row['targets'] and not self.canonical():
            await self.engine.match(copy.deepcopy(event),targets=[],strict=True)
        for target in row['targets']:
            key=('job:'+target['jobId']) if target.get('_job') else 'trigger:'+target['id']
            if receipts.get(key,{}).get('status') not in (None,'error'):continue
            try:
                admission=target.get('_admissionStatus','eligible')
                if admission!='eligible':
                    await self.engine.io(self.events.subscription,eid,key,admission,revision=target.get('revision'))
                    continue
                if target.get('_final'):
                    final_jobs = getattr(self, 'final_jobs', None)
                    if final_jobs is None:
                        raise ValueError('Canonical admission service unavailable')
                    result = await final_jobs.admission.admit(copy.deepcopy(event), target)
                    await self.engine.io(self.events.subscription,eid,key,'admitted',
                        admissionResult=result.get('status'),runId=result.get('runId'),revision=target['revision'])
                    continue
                if self.canonical():
                    # Retained old subscriptions remain evidence, never work.
                    await self.engine.io(self.events.subscription,eid,key,'unavailable',revision=target.get('revision'))
                    continue
                if target.get('_job'):
                    # Retained pre-cutover inbox rows enter the same Trigger path.
                    await self.engine.subscriptions()
                    state = await self.engine.io(self.ledger.read)
                    target = state['triggers']['trigger:' + target['jobId']]
                if not self.engine.available(target):raise ValueError('Event dependency unavailable')
                await self.engine.match(copy.deepcopy(event),targets=[copy.deepcopy(target)],strict=True)
                state=await self.engine.io(self.ledger.read)
                run=next((r for r in state['runs'].values() if r['event']['id']==eid and r['triggerId']==target['id']),None)
                if not run:raise ValueError('Subscription has no durable admission result')
                await self.engine.io(self.events.subscription,eid,key,'admitted',runId=run['id'],admissionResult=run['status'],revision=target['revision'])
            except Exception as exc:
                errors.append(str(exc))
                if errors_batch is not None:errors_batch.append((eid,key,str(exc)))
                else:
                    try:await self.engine.io(self.events.subscription,eid,key,'error',error=str(exc)[:1000])
                    except Exception as write_error:self.diagnostic('subscription-receipt',write_error,eid)
        if errors:raise ValueError('; '.join(errors))

    async def event_page(self,after,limit,include_execution=False):
        page=await self.engine.io(self.events.page,after,limit)
        if not include_execution:return page
        state=await self.engine.io(self.ledger.read)
        for row in page['events']:
            for receipt in row.get('subscriptions',{}).values():
                run=state['runs'].get(receipt.get('runId'))
                if run:receipt['execution']={k:run[k] for k in ('status','result','error') if k in run}
        return page

    async def ingest_core(self):
        while True:
            event = await self.ingress.get()
            try:
                await self.accept(event)
            except Exception as exc:
                self.diagnostic('core-persist', exc, event['id'])
            finally:
                self.ingress.task_done()

    async def maintenance(self):
        # One local storage maintenance window; no provider/Action waits. Order
        # is admission -> matching, never the reverse. Prevent readmission from
        # using a retained pair which this stale retainedIds snapshot will prune.
        async with self.admission, self.engine.matching:
            removed = await self.engine.io(self.events.maintenance)
            runs = {} if self.canonical() else await self.engine.io(self.ledger.maintenance, removed['retainedIds'])
        return {'deletedEvents': removed['deletedEvents'], **runs}

    async def dispatch(self):
        loop = asyncio.get_running_loop()
        maintenance_at = loop.time() + 60
        failures = 0
        cursor = 0
        cycle_retry = False
        while not self.stopping:
            self.wake.clear()
            retry = False
            try:
                if loop.time() >= maintenance_at:
                    await self.maintenance()
                    maintenance_at = loop.time() + 60
                batch = await self.engine.io(self.events.pending, 64, cursor)
                delivery_errors = []
                subscription_errors=[]
                try:
                    for row in batch:
                        cursor = row['seq']
                        if self.stopping:
                            return
                        try:
                            await self.deliver(row,subscription_errors)
                            # Notifications remain best effort; durable inbox delivery
                            # guarantees Trigger matching, not plugin delivery receipts.
                            self.bus._fanout(row['event'])
                            await self.engine.io(self.events.delivery, row['event']['id'], done=True)
                        except Exception as exc:
                            retry = True
                            self.diagnostic('event-delivery', exc, row['event']['id'])
                            # Failed matching has no delivered receipt. Persist its
                            # diagnostic/count with this bounded page so unavailable
                            # old dependencies do not each impose a separate fsync
                            # before independent events on the next page can run.
                            delivery_errors.append((row['event']['id'], str(exc)))
                finally:
                    if subscription_errors:
                        await self.engine.io(self.events.subscription_errors,subscription_errors)
                    if delivery_errors:
                        await self.engine.io(self.events.delivery_errors, delivery_errors)
                cycle_retry = cycle_retry or retry
                if len(batch) == 64:
                    continue
                cursor = 0
                retry = cycle_retry
                cycle_retry = False
            except Exception as exc:
                retry = True
                self.diagnostic('event-store', exc)
            failures = min(failures + 1, 5) if retry else 0
            # Retry only Event-to-Run persistence, never a failed/uncertain Action.
            # A wake or plugin-ready interrupts this bounded failure backoff.
            delay = min(2**failures, 30) if retry else max(.1, maintenance_at-loop.time())
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def start(self):
        if self.stopping or self.started:
            return
        if not self.canonical():
            from .engine import DependencyUnavailable
            raise DependencyUnavailable('Canonical Job owner required; legacy Automation host is retired')
        # The composition root supplies the owner. Automation owns the Event
        # inbox and ingress tasks only, never a Job/time/chain execution loop.
        await self.engine.io(self.auth.key)
        await self.engine.io(self.events.initialize)
        self.loop = asyncio.get_running_loop()
        self.started = True
        self.dispatcher = asyncio.create_task(self.dispatch(), name='automation-event-dispatch')
        self.ingester = asyncio.create_task(self.ingest_core(), name='automation-core-ingress')
        self.worker.set_running_observer(self.observe)

    async def close(self):
        if self.started:
            self.worker.set_running_observer(None)
        self.stopping = True
        await self.quota_policy.close()
        self.wake.set()
        # Settle accepted writes before returning; pending records survive close.
        tasks = [t for t in (self.ingester, self.dispatcher) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.ingester = self.dispatcher = None
        async with self.admission:
            self.started = False
        await self.bus.close()
        if self.engine_started:
            await self.engine.close()
        else:
            self.engine.closed = True
        self.loop = None
        self._reset_runtime_issue_reservations()

    async def replay(self, ids):
        async with self.engine.matching:
            state = await self.engine.io(self.ledger.read)
            if isinstance(ids, list) and any(r['event']['id'] in ids and r['status'] == 'uncertain' for r in state['runs'].values()):
                raise ValueError('Uncertain Run requires reconciliation; Event replay refused')
            result = await self.engine.io(self.events.replay, ids)
        self.wake.set()
        return result

    async def plugin_enabled(self, plugin_id):
        return self.bus.activate('plugin', plugin_id)

    async def plugin_ready(self):
        self.wake.set()
        await self.engine.kick()

    async def plugin_disabled(self, handle):
        await self.bus.deactivate(handle)
